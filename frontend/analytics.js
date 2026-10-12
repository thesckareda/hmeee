/**
 * Analytics page — TailAdmin-inspired layout + Chart.js
 * https://github.com/TailAdmin/tailadmin-free-tailwind-dashboard-template
 */
(function analyticsModule() {
  const charts = { daily: null, hourly: null, outcomes: null };

  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  function destroyCharts() {
    Object.keys(charts).forEach((k) => {
      if (charts[k]) {
        charts[k].destroy();
        charts[k] = null;
      }
    });
  }

  function formatDateLabel(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
  }

  function buildDailySeries(daily, days = 14) {
    const map = new Map((daily || []).map((x) => [x.date, Number(x.calls) || 0]));
    const result = [];
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    for (let offset = days - 1; offset >= 0; offset -= 1) {
      const d = new Date(today);
      d.setDate(d.getDate() - offset);
      const key = [
        d.getFullYear(),
        String(d.getMonth() + 1).padStart(2, "0"),
        String(d.getDate()).padStart(2, "0"),
      ].join("-");
      result.push({ date: key, calls: map.get(key) || 0 });
    }
    return result;
  }

  function renderKpis(kpis) {
    const items = [
      {
        id: "ana-connect",
        label: "Connect Rate",
        value: `${kpis.connect_rate || 0}%`,
        badge: kpis.connect_rate >= 50 ? "up" : "down",
        badgeText: `${kpis.connect_rate || 0}%`,
        icon: "phone",
      },
      {
        id: "ana-duration",
        label: "Avg Duration",
        value: `${kpis.avg_duration || 0}s`,
        badge: "neutral",
        badgeText: "per call",
        icon: "clock",
      },
      {
        id: "ana-booked",
        label: "Booked Calls",
        value: String(kpis.booked_calls || 0),
        badge: kpis.booking_rate >= 10 ? "up" : "neutral",
        badgeText: `${kpis.booking_rate || 0}% rate`,
        icon: "calendar",
      },
      {
        id: "ana-total",
        label: "Total Calls",
        value: String(kpis.total_calls || 0),
        badge: "neutral",
        badgeText: "all time",
        icon: "chart",
      },
    ];

    const grid = document.getElementById("ta-metrics");
    if (!grid) return;

    grid.innerHTML = items.map((item) => `
      <article class="ta-metric-card card">
        <div class="ta-metric-icon" aria-hidden="true">${metricIcon(item.icon)}</div>
        <div class="ta-metric-body">
          <span class="ta-metric-label">${item.label}</span>
          <strong class="ta-metric-value" id="${item.id}">${item.value}</strong>
        </div>
        <span class="ta-metric-badge ta-badge-${item.badge}">${item.badgeText}</span>
      </article>
    `).join("");
  }

  function metricIcon(type) {
    const icons = {
      phone: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/></svg>',
      clock: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
      calendar: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
      chart: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 3v18h18"/><path d="M7 16l4-6 4 3 5-8"/></svg>',
    };
    return icons[type] || icons.chart;
  }

  function chartDefaults() {
    const text = cssVar("--color-text-muted", "rgba(255,255,255,0.55)");
    const grid = cssVar("--color-border", "rgba(255,255,255,0.08)");
    const green = cssVar("--speaksy-green", "#63dcb4");
    return {
      text,
      grid,
      green,
      font: cssVar("--font-sans", "Geist, -apple-system, sans-serif"),
    };
  }

  /* Same 6-stop ramp as .btn-speaksy's --sbtn-magic-surface gradient. */
  const SPEAKSY_GRADIENT_STOPS = [
    [0, "#0e2410"],
    [0.22, "#163d18"],
    [0.45, "#245c28"],
    [0.65, "#397e39"],
    [0.82, "#4c9349"],
    [1, "#5aa652"],
  ];

  function speaksyBarGradient(ctx2d, y0, y1) {
    const gradient = ctx2d.createLinearGradient(0, y0, 0, y1);
    SPEAKSY_GRADIENT_STOPS.forEach(([stop, color]) => gradient.addColorStop(stop, color));
    return gradient;
  }

  function hexShade(hex, percent) {
    const f = parseInt(hex.slice(1), 16);
    const t = percent < 0 ? 0 : 255;
    const p = Math.abs(percent);
    const R = f >> 16, G = (f >> 8) & 0x00ff, B = f & 0x0000ff;
    return `#${(
      0x1000000 +
      (Math.round((t - R) * p) + R) * 0x10000 +
      (Math.round((t - G) * p) + G) * 0x100 +
      (Math.round((t - B) * p) + B)
    ).toString(16).slice(1)}`;
  }

  function speaksySliceGradient(ctx2d, chartArea, baseColor) {
    const gradient = ctx2d.createLinearGradient(chartArea.left, chartArea.top, chartArea.right, chartArea.bottom);
    gradient.addColorStop(0, hexShade(baseColor, -0.65));
    gradient.addColorStop(1, hexShade(baseColor, 0.35));
    return gradient;
  }

  function renderDailyChart(daily) {
    const canvas = document.getElementById("ana-daily-chart");
    if (!canvas || typeof Chart === "undefined") return;

    const theme = chartDefaults();
    const labels = daily.map((x) => formatDateLabel(x.date));
    const data = daily.map((x) => x.calls || 0);

    const maxVal = Math.max(...data, 0);
    const totalCalls = data.reduce((sum, v) => sum + v, 0);
    const tooltipBg = cssVar("--color-surface-2", "#1a1f2e");
    const tooltipText = cssVar("--color-text", "#f5f5f5");

    const barFill = (ctx) => {
      const val = data[ctx.dataIndex] || 0;
      if (!val) return `${theme.green}18`;
      const { chart } = ctx;
      if (!chart.chartArea) return `${theme.green}cc`;
      return speaksyBarGradient(chart.ctx, chart.chartArea.top, chart.chartArea.bottom);
    };

    charts.daily = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Calls",
          data,
          backgroundColor: barFill,
          hoverBackgroundColor: barFill,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 28,
          barPercentage: 0.72,
          categoryPercentage: 0.82,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            backgroundColor: tooltipBg,
            titleColor: tooltipText,
            bodyColor: tooltipText,
            titleFont: { family: theme.font, weight: "600" },
            bodyFont: { family: theme.font },
            padding: 12,
            cornerRadius: 8,
            callbacks: {
              title: (items) => {
                const idx = items[0]?.dataIndex ?? 0;
                return daily[idx]?.date ? formatDateLabel(daily[idx].date) : items[0]?.label || "";
              },
              label: (ctx) => {
                const count = Number(ctx.raw) || 0;
                const pct = totalCalls ? Math.round((count / totalCalls) * 100) : 0;
                return `${count} call${count === 1 ? "" : "s"} · ${pct}% of period`;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: theme.text,
              font: { family: theme.font, size: 10 },
              maxRotation: 0,
              autoSkip: false,
            },
          },
          y: {
            beginAtZero: true,
            suggestedMax: maxVal > 0 ? undefined : 5,
            grid: { color: theme.grid },
            ticks: {
              color: theme.text,
              font: { family: theme.font, size: 11 },
              precision: 0,
              stepSize: maxVal > 10 ? undefined : 1,
            },
          },
        },
      },
    });
  }

  function renderHourlyChart(hourly) {
    const canvas = document.getElementById("ana-hourly-chart");
    if (!canvas || typeof Chart === "undefined") return;

    const theme = chartDefaults();
    const labels = hourly.map((x) => `${String(x.hour).padStart(2, "0")}:00`);
    const data = hourly.map((x) => x.calls || 0);
    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.offsetHeight || 220);
    gradient.addColorStop(0, `${theme.green}55`);
    gradient.addColorStop(1, `${theme.green}00`);

    charts.hourly = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Calls by hour",
          data,
          borderColor: theme.green,
          backgroundColor: gradient,
          fill: true,
          tension: 0.4,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: theme.green,
          pointHoverBorderColor: "#fff",
          pointHoverBorderWidth: 2,
          borderWidth: 2.5,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: cssVar("--color-surface-2", "#1a1f2e"),
            titleFont: { family: theme.font, weight: "600" },
            bodyFont: { family: theme.font },
            padding: 12,
            cornerRadius: 10,
            callbacks: {
              title: (items) => `${items[0]?.label || ""} IST`,
              label: (ctx) => `${ctx.raw || 0} calls`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: theme.text,
              font: { family: theme.font, size: 10 },
              maxTicksLimit: 12,
            },
          },
          y: {
            beginAtZero: true,
            grid: { color: theme.grid, drawBorder: false },
            border: { display: false },
            ticks: {
              color: theme.text,
              font: { family: theme.font, size: 11 },
              precision: 0,
              maxTicksLimit: 5,
            },
          },
        },
      },
    });
  }

  function outcomePalette() {
    /* Explicit hues — booked/completed must not share speaksy-green / --color-primary */
    return {
      booked: cssVar("--ana-outcome-booked", "#30d158"),
      completed: cssVar("--ana-outcome-completed", "#0a84ff"),
      cancelled: cssVar("--ana-outcome-cancelled", "#ff9f0a"),
      unknown: cssVar("--ana-outcome-unknown", "#8e8e93"),
    };
  }

  function renderOutcomesChart(outcomes) {
    const canvas = document.getElementById("ana-outcomes-chart");
    if (!canvas || typeof Chart === "undefined") return;

    const theme = chartDefaults();
    const labels = ["Booked", "Completed", "Cancelled", "Unknown"];
    const keys = ["booked", "completed", "cancelled", "unknown"];
    const data = keys.map((k) => outcomes[k] || 0);
    const palette = outcomePalette();
    const colors = keys.map((k) => palette[k]);

    const sliceFill = (ctx) => {
      const { chart, dataIndex } = ctx;
      if (!chart.chartArea) return `${colors[dataIndex]}dd`;
      return speaksySliceGradient(chart.ctx, chart.chartArea, colors[dataIndex]);
    };

    charts.outcomes = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: sliceFill,
          borderWidth: 0,
          hoverOffset: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: theme.text,
              font: { family: theme.font, size: 12 },
              padding: 16,
              usePointStyle: true,
            },
          },
          tooltip: {
            backgroundColor: cssVar("--color-surface-2", "#1a1f2e"),
            titleFont: { family: theme.font },
            bodyFont: { family: theme.font },
            padding: 12,
            cornerRadius: 8,
          },
        },
      },
    });

    const total = data.reduce((s, v) => s + v, 0);
    const summary = document.getElementById("ana-outcomes-summary");
    if (summary) {
      summary.innerHTML = keys.map((k, i) => `
        <div class="ta-outcome-stat">
          <span class="ta-outcome-label">
            <span class="ta-outcome-swatch" style="background:${palette[k]}"></span>
            ${labels[i]}
          </span>
          <strong class="ta-outcome-value">${data[i]}</strong>
          <span class="ta-outcome-pct">${total ? Math.round((data[i] / total) * 100) : 0}%</span>
        </div>
      `).join("");
    }
  }

  function render(data) {
    const ana = data || window.state?.analytics || {
      kpis: {},
      outcomes: {},
      daily_series: [],
      hourly_series: [],
    };

    destroyCharts();
    renderKpis(ana.kpis || {});

    const daily = buildDailySeries(ana.daily_series || []);
    const hourly = ana.hourly_series || [];
    const outcomes = ana.outcomes || {};
    const dailyTotal = daily.reduce((sum, row) => sum + (row.calls || 0), 0);

    const dailyEmpty = document.getElementById("ana-daily-empty");
    const hourlyEmpty = document.getElementById("ana-hourly-empty");
    if (dailyEmpty) {
      dailyEmpty.hidden = dailyTotal > 0;
      dailyEmpty.textContent = dailyTotal > 0
        ? ""
        : "No calls in the last 14 days — bars will fill in as calls come in.";
    }
    if (hourlyEmpty) hourlyEmpty.hidden = (hourly.reduce((s, p) => s + (p.calls || 0), 0) > 0);

    renderDailyChart(daily);
    if (hourly.length) renderHourlyChart(hourly);
    renderOutcomesChart(outcomes);
  }

  window.Analytics = { render, destroy: destroyCharts };
})();