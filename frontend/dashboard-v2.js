/**
 * Dashboard v2 shell — UX hooks only. Page logic stays in app.js / dashboard.js.
 */
(function () {
  "use strict";

  function wireTopbarSearch() {
    const sidebarSearch = document.getElementById("global-search");
    const topSearch = document.getElementById("v2-global-search");
    if (!sidebarSearch || !topSearch) return;

    const sync = (from, to) => {
      from.addEventListener("input", () => {
        if (to.value !== from.value) {
          to.value = from.value;
          to.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
      from.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          to.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        }
      });
    };

    sync(sidebarSearch, topSearch);
    sync(topSearch, sidebarSearch);
    topSearch.value = sidebarSearch.value;
  }

  function wireDeployCta() {
    document.getElementById("v2-deploy-btn")?.addEventListener("click", () => {
      if (typeof window.setPage === "function") {
        window.setPage("assistants");
        const desc = document.getElementById("assistant-description");
        desc?.focus();
        desc?.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }

  function wireActiveCallsPill() {
    const pill = document.getElementById("active-calls-pill");
    if (!pill) return;
    const base = pill.textContent?.trim() || "0 active";
    pill.setAttribute("aria-label", `Active calls: ${base}`);
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!document.body.classList.contains("vui-v2")) return;
    wireTopbarSearch();
    wireDeployCta();
    wireActiveCallsPill();
  });
})();