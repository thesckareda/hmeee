/**
 * Speaksy Booking Calendar — UI inspired by Thunderbird Appointment.
 */
(function calendarModule() {
  const IST = "Asia/Kolkata";
  const BOOKING_DURATION_MIN = 30;

  const calState = {
    view: "agenda",
    filter: "all",
    cursor: new Date(),
    selectedDate: "",
    selectedBookingId: null,
    initialized: false,
  };

  function bookings() {
    return window.state?.bookings || [];
  }

  function parseDt(value) {
    return typeof window.parseAppointmentDate === "function"
      ? window.parseAppointmentDate(value)
      : (value ? new Date(value) : null);
  }

  function dateKey(value) {
    if (typeof window.appointmentDateKeyIST === "function") {
      return window.appointmentDateKeyIST(value);
    }
    const d = parseDt(value);
    return d ? d.toISOString().slice(0, 10) : "";
  }

  function todayKey() {
    if (typeof window.formatDateKeyIST === "function") {
      return window.formatDateKeyIST(new Date());
    }
    return new Date().toISOString().slice(0, 10);
  }

  function formatTimeRange(startValue) {
    const start = parseDt(startValue);
    if (!start) return "Time unavailable";
    const end = new Date(start.getTime() + BOOKING_DURATION_MIN * 60 * 1000);
    const fmt = (d) =>
      new Intl.DateTimeFormat("en-IN", {
        timeZone: IST,
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      }).format(d);
    return `${fmt(start)} – ${fmt(end)}`;
  }

  function formatDayLabel(dateKeyStr) {
    const [y, m, d] = dateKeyStr.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: IST,
      weekday: "long",
      month: "short",
      day: "numeric",
    }).format(dt);
  }

  function formatMonthYear(cursor) {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: IST,
      month: "long",
      year: "numeric",
    }).format(cursor);
  }

  function esc(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function nameOf(b) {
    return window.displayCallerName?.(b.caller_name, b.phone_number) || b.caller_name || "Unknown";
  }

  function phoneOf(b) {
    return window.displayPhone?.(b.phone_number) || b.phone_number || "—";
  }

  function filteredBookings() {
    const now = Date.now();
    const tk = todayKey();
    return bookings()
      .filter((b) => {
        const d = parseDt(b.appointment_time);
        if (!d) return calState.filter === "all";
        const ts = d.getTime();
        if (calState.filter === "upcoming") return ts >= now - 30 * 60 * 1000;
        if (calState.filter === "past") return ts < now;
        if (calState.filter === "today") return dateKey(b.appointment_time) === tk;
        return true;
      })
      .sort((a, b) => {
        const ta = parseDt(a.appointment_time)?.getTime() || 0;
        const tb = parseDt(b.appointment_time)?.getTime() || 0;
        return ta - tb;
      });
  }

  function bookingsInMonth(cursor) {
    const y = cursor.getFullYear();
    const m = cursor.getMonth();
    return filteredBookings().filter((b) => {
      const d = parseDt(b.appointment_time);
      return d && d.getFullYear() === y && d.getMonth() === m;
    });
  }

  function groupByDay(list) {
    const groups = {};
    list.forEach((b) => {
      const k = dateKey(b.appointment_time);
      if (!k) return;
      groups[k] = groups[k] || [];
      groups[k].push(b);
    });
    return Object.keys(groups)
      .sort()
      .map((day) => ({
        day,
        label: formatDayLabel(day),
        items: groups[day].sort(
          (a, b) =>
            (parseDt(a.appointment_time)?.getTime() || 0) -
            (parseDt(b.appointment_time)?.getTime() || 0),
        ),
      }));
  }

  function computeMetrics() {
    const all = bookings();
    const now = Date.now();
    const tk = todayKey();
    const weekEnd = now + 7 * 24 * 60 * 60 * 1000;
    let today = 0;
    let week = 0;
    let upcoming = 0;
    all.forEach((b) => {
      const d = parseDt(b.appointment_time);
      if (!d) return;
      const ts = d.getTime();
      if (dateKey(b.appointment_time) === tk) today += 1;
      if (ts >= now && ts <= weekEnd) week += 1;
      if (ts >= now - 30 * 60 * 1000) upcoming += 1;
    });
    return { today, week, upcoming, total: all.length };
  }

  function bookingStatus(b) {
    const d = parseDt(b.appointment_time);
    if (!d) return { label: "Unknown", cls: "cal-badge-muted" };
    const ts = d.getTime();
    const now = Date.now();
    if (dateKey(b.appointment_time) === todayKey()) return { label: "Today", cls: "cal-badge-today" };
    if (ts < now) return { label: "Completed", cls: "cal-badge-past" };
    return { label: "Upcoming", cls: "cal-badge-upcoming" };
  }

  function renderMetrics() {
    const m = computeMetrics();
    const el = document.getElementById("cal-metrics");
    if (!el) return;
    el.innerHTML = `
      <div class="metric-pill"><span class="metric-label">Today</span><span class="metric-value">${m.today}</span></div>
      <div class="metric-pill"><span class="metric-label">This week</span><span class="metric-value">${m.week}</span></div>
      <div class="metric-pill"><span class="metric-label">Upcoming</span><span class="metric-value">${m.upcoming}</span></div>
      <div class="metric-pill"><span class="metric-label">All bookings</span><span class="metric-value">${m.total}</span></div>
    `;
  }

  function renderBookingCard(b, { compact = false } = {}) {
    const status = bookingStatus(b);
    const selected = calState.selectedBookingId === String(b.id);
    const summary = esc((b.summary || "").slice(0, compact ? 0 : 120));
    return `
      <button type="button" class="cal-booking-card${selected ? " selected" : ""}" data-booking-id="${esc(b.id)}">
        <div class="cal-booking-main">
          <strong class="cal-booking-name">${esc(nameOf(b))}</strong>
          <span class="cal-booking-phone">${esc(phoneOf(b))}</span>
        </div>
        <div class="cal-booking-time">${esc(formatTimeRange(b.appointment_time))}</div>
        <div class="cal-booking-badge-col">
          <span class="cal-badge ${status.cls}">${status.label}</span>
        </div>
        ${summary ? `<p class="cal-booking-snippet">${summary}</p>` : ""}
      </button>
    `;
  }

  function renderEmpty(message, hint) {
    return `
      <div class="cal-empty">
        <div class="cal-empty-icon" aria-hidden="true">📅</div>
        <h3>${message}</h3>
        <p>${hint}</p>
      </div>
    `;
  }

  function renderMonthNav() {
    return `
      <div class="cal-month-nav">
        <button type="button" class="btn btn-ghost btn-sm cal-nav-btn" data-cal-nav="prev" aria-label="Previous month">‹</button>
        <h2 class="cal-month-title">${formatMonthYear(calState.cursor)}</h2>
        <button type="button" class="btn btn-ghost btn-sm cal-nav-btn" data-cal-nav="next" aria-label="Next month">›</button>
        <button type="button" class="btn btn-ghost btn-sm cal-today-btn" data-cal-nav="today">Today</button>
      </div>
    `;
  }

  function renderAgenda() {
    const list = bookingsInMonth(calState.cursor);
    const groups = groupByDay(list);
    if (!list.length) {
      return `${renderMonthNav()}<div class="cal-panel">${renderEmpty("No bookings this month", "Voice agent bookings appear here when callers schedule site visits or appointments.")}</div>`;
    }
    return `
      ${renderMonthNav()}
      <div class="cal-panel">
        <div class="cal-divider"></div>
        ${groups
          .map(
            (g) => `
          <section class="cal-day-group">
            <h3 class="cal-day-heading">${g.label}</h3>
            <div class="cal-day-list">
              ${g.items.map((b) => renderBookingCard(b)).join("")}
            </div>
          </section>
        `,
          )
          .join("")}
      </div>
    `;
  }

  const WEEK_CENTER_OFFSET = 3; // today sits on the middle column (4th of 7)

  function normalizeCalendarDate(d) {
    const copy = new Date(d);
    copy.setHours(12, 0, 0, 0);
    return copy;
  }

  /** Rolling 7-day strip with the anchor day in the center column. */
  function startOfCenteredWeek(centerDate) {
    const center = normalizeCalendarDate(centerDate);
    const start = new Date(center);
    start.setDate(center.getDate() - WEEK_CENTER_OFFSET);
    return start;
  }

  function weekDaysAround(centerDate) {
    const weekStart = startOfCenteredWeek(centerDate);
    const days = [];
    for (let i = 0; i < 7; i += 1) {
      const d = new Date(weekStart);
      d.setDate(weekStart.getDate() + i);
      days.push(d);
    }
    return days;
  }

  function renderWeek() {
    const center = normalizeCalendarDate(calState.cursor);
    const days = weekDaysAround(center);
    const all = filteredBookings();
    const byDay = {};
    days.forEach((d) => {
      const k = typeof window.formatDateKeyIST === "function" ? window.formatDateKeyIST(d) : "";
      byDay[k] = all.filter((b) => dateKey(b.appointment_time) === k);
    });

    const centerKey =
      typeof window.formatDateKeyIST === "function" ? window.formatDateKeyIST(center) : todayKey();
    const weekLabel = formatDayLabel(centerKey);
    const rangeLabel = `${formatDayLabel(
      typeof window.formatDateKeyIST === "function" ? window.formatDateKeyIST(days[0]) : "",
    )} – ${formatDayLabel(
      typeof window.formatDateKeyIST === "function" ? window.formatDateKeyIST(days[6]) : "",
    )}`;

    return `
      <div class="cal-month-nav cal-week-nav">
        <button type="button" class="btn btn-ghost btn-sm cal-nav-btn" data-cal-nav="prev-week" aria-label="Previous week">‹</button>
        <div class="cal-week-title-wrap">
          <h2 class="cal-month-title">${weekLabel}</h2>
          <p class="cal-week-range muted">${rangeLabel}</p>
        </div>
        <button type="button" class="btn btn-ghost btn-sm cal-nav-btn" data-cal-nav="next-week" aria-label="Next week">›</button>
        <button type="button" class="btn btn-ghost btn-sm cal-today-btn" data-cal-nav="today">Today</button>
      </div>
      <div class="cal-week-grid">
        ${days
          .map((d, idx) => {
            const k = typeof window.formatDateKeyIST === "function" ? window.formatDateKeyIST(d) : "";
            const isToday = k === todayKey();
            const isCenter = idx === WEEK_CENTER_OFFSET;
            const items = byDay[k] || [];
            return `
              <div class="cal-week-col${isToday ? " is-today" : ""}${isCenter ? " is-center" : ""}">
                <div class="cal-week-col-head">
                  <span class="cal-week-dow">${new Intl.DateTimeFormat("en-IN", { weekday: "short", timeZone: IST }).format(d)}</span>
                  <span class="cal-week-date">${d.getDate()}</span>
                </div>
                <div class="cal-week-col-body">
                  ${items.length
                    ? items.map((b) => `
                      <button type="button" class="cal-week-event" data-booking-id="${esc(b.id)}">
                        <span class="cal-week-event-time">${esc(formatTimeRange(b.appointment_time).split("–")[0].trim())}</span>
                        <span class="cal-week-event-name">${esc(nameOf(b))}</span>
                      </button>
                    `).join("")
                    : `<span class="cal-week-empty">—</span>`}
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function renderMonthGrid() {
    const y = calState.cursor.getFullYear();
    const m = calState.cursor.getMonth();
    const first = new Date(y, m, 1);
    const last = new Date(y, m + 1, 0);
    const monthBookings = bookingsInMonth(calState.cursor);
    const byDate = {};
    monthBookings.forEach((b) => {
      const k = dateKey(b.appointment_time);
      byDate[k] = (byDate[k] || 0) + 1;
    });

    const cells = [];
    for (let i = 0; i < first.getDay(); i++) cells.push(`<div class="cal-mini-cell cal-mini-pad"></div>`);
    for (let day = 1; day <= last.getDate(); day++) {
      const ds = `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      const count = byDate[ds] || 0;
      const isToday = ds === todayKey();
      const selected = ds === calState.selectedDate;
      cells.push(`
        <button type="button" class="cal-mini-cell${count ? " has-bookings" : ""}${isToday ? " is-today" : ""}${selected ? " selected" : ""}" data-cal-date="${ds}">
          <span class="cal-mini-day">${day}</span>
          ${count ? `<span class="cal-mini-count">${count}</span>` : ""}
        </button>
      `);
    }

    const selected = calState.selectedDate;
    const dayItems = selected
      ? monthBookings.filter((b) => dateKey(b.appointment_time) === selected)
      : [];

    return `
      ${renderMonthNav()}
      <div class="cal-month-layout">
        <div class="cal-panel cal-mini-wrap">
          <div class="cal-mini-dow">
            <span>Sun</span><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span>
          </div>
          <div class="cal-mini-grid">${cells.join("")}</div>
        </div>
        <div class="cal-panel cal-month-side">
          <h3 class="cal-day-heading">${selected ? formatDayLabel(selected) : "Select a date"}</h3>
          ${selected && dayItems.length
            ? `<div class="cal-day-list">${dayItems.map((b) => renderBookingCard(b, { compact: true })).join("")}</div>`
            : `<p class="muted cal-month-side-empty">${selected ? "No bookings on this date." : "Click a highlighted date to see appointments."}</p>`}
        </div>
      </div>
    `;
  }

  function renderDetail() {
    const panel = document.getElementById("cal-detail");
    if (!panel) return;
    const b = bookings().find((x) => String(x.id) === String(calState.selectedBookingId));
    if (!b) {
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }
    const status = bookingStatus(b);
    const recording = (b.recording_url || "").trim();
    const recordingSrc = b.id && window.CallLogs?.streamUrl
      ? window.CallLogs.streamUrl(b.id)
      : recording;
    panel.hidden = false;
    panel.innerHTML = `
      <div class="cal-detail-head">
        <h3>Appointment details</h3>
        <button type="button" class="btn btn-ghost btn-sm" id="cal-detail-close" aria-label="Close">✕</button>
      </div>
      <div class="cal-detail-body">
        <div class="cal-detail-block">
          <span class="cal-detail-label">When</span>
          <strong>${window.safeAppointmentDate?.(b.appointment_time) || formatTimeRange(b.appointment_time)}</strong>
          <span class="muted">${formatTimeRange(b.appointment_time)}</span>
        </div>
        <div class="cal-detail-divider"></div>
        <div class="cal-detail-block">
          <span class="cal-detail-label">Lead</span>
          <strong>${esc(nameOf(b))}</strong>
          <span>${esc(phoneOf(b))}</span>
        </div>
        <div class="cal-detail-divider"></div>
        <div class="cal-detail-block">
          <span class="cal-detail-label">Status</span>
          <span class="cal-badge ${status.cls}">${status.label}</span>
        </div>
        ${b.summary ? `
          <div class="cal-detail-divider"></div>
          <div class="cal-detail-block">
            <span class="cal-detail-label">Call summary</span>
            <p class="cal-detail-summary">${esc(String(b.summary).slice(0, 500))}</p>
          </div>
        ` : ""}
        ${recording ? `
          <div class="cal-detail-divider"></div>
          <div class="cal-detail-block">
            <span class="cal-detail-label">Recording</span>
            <audio class="cal-detail-audio" controls preload="none" src="${recordingSrc}"></audio>
          </div>
        ` : ""}
        <div class="cal-detail-actions">
          <button type="button" class="btn btn-primary btn-sm" data-cal-action="contact" data-phone="${b.phone_number || ""}">View contact</button>
        </div>
      </div>
    `;
    panel.querySelector("#cal-detail-close")?.addEventListener("click", () => {
      calState.selectedBookingId = null;
      renderDetail();
      document.querySelectorAll(".cal-booking-card.selected, .cal-week-event.selected").forEach((el) => {
        el.classList.remove("selected");
      });
    });
    panel.querySelector("[data-cal-action=contact]")?.addEventListener("click", () => {
      if (b.phone_number && window.setPage) {
        window.setPage("crm");
        window.renderCRMDetails?.(b.phone_number);
      }
    });
  }

  function renderMain() {
    const main = document.getElementById("cal-main");
    if (!main) return;
    if (calState.view === "week") main.innerHTML = renderWeek();
    else if (calState.view === "month") main.innerHTML = renderMonthGrid();
    else main.innerHTML = renderAgenda();
    bindMainEvents();
    renderDetail();
  }

  function bindMainEvents() {
    const main = document.getElementById("cal-main");
    if (!main) return;

    main.querySelectorAll("[data-cal-nav]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nav = btn.dataset.calNav;
        if (nav === "prev") {
          calState.cursor = new Date(calState.cursor.getFullYear(), calState.cursor.getMonth() - 1, 1);
        } else if (nav === "next") {
          calState.cursor = new Date(calState.cursor.getFullYear(), calState.cursor.getMonth() + 1, 1);
        } else if (nav === "prev-week") {
          const d = new Date(calState.cursor);
          d.setDate(d.getDate() - 7);
          calState.cursor = d;
        } else if (nav === "next-week") {
          const d = new Date(calState.cursor);
          d.setDate(d.getDate() + 7);
          calState.cursor = d;
        } else if (nav === "today") {
          calState.cursor = new Date();
          calState.selectedDate = todayKey();
        }
        renderMain();
      });
    });

    main.querySelectorAll("[data-cal-date]").forEach((cell) => {
      cell.addEventListener("click", () => {
        calState.selectedDate = cell.dataset.calDate || "";
        renderMain();
      });
    });

    main.querySelectorAll("[data-booking-id]").forEach((el) => {
      el.addEventListener("click", () => {
        calState.selectedBookingId = el.dataset.bookingId || null;
        main.querySelectorAll(".cal-booking-card.selected, .cal-week-event.selected").forEach((n) => {
          n.classList.remove("selected");
        });
        el.classList.add("selected");
        renderDetail();
      });
    });
  }

  function syncToolbar() {
    document.querySelectorAll("[data-cal-view]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.calView === calState.view);
    });
    document.querySelectorAll("[data-cal-filter]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.calFilter === calState.filter);
    });
  }

  function setupToolbar() {
    if (calState.initialized) return;
    calState.initialized = true;

    document.querySelectorAll("[data-cal-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nextView = btn.dataset.calView || "agenda";
        if (nextView === "week" && calState.view !== "week") {
          calState.cursor = new Date();
        }
        calState.view = nextView;
        syncToolbar();
        renderMain();
      });
    });

    document.querySelectorAll("[data-cal-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        calState.filter = btn.dataset.calFilter || "all";
        syncToolbar();
        renderMetrics();
        renderMain();
      });
    });
  }

  function render() {
    if (!calState.selectedDate) calState.selectedDate = todayKey();
    renderMetrics();
    syncToolbar();
    renderMain();
  }

  window.Calendar = { render, setupToolbar };
})();