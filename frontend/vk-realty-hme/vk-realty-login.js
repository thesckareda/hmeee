(function () {
  const AUTH_TOKEN_KEY = "speaksy-auth-token";
  const base = window.clientBasePath ? window.clientBasePath() : "/vk-realty-hme";
  const DASHBOARD = `${base}/dashboard`;
  const loginForm = document.getElementById("login-form");

  if (!loginForm) return;

  function setAuthToken(token) {
    if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
  }

  function initReveal() {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const targets = document.querySelectorAll(".vk-reveal");
    if (reduced) {
      targets.forEach((el) => el.classList.add("is-visible"));
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            obs.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    targets.forEach((el) => obs.observe(el));
  }

  async function verifyExistingSession() {
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (!token) return false;
    try {
      const res = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = document.getElementById("login-error");
    const submitBtn = document.getElementById("login-submit");
    errorEl.textContent = "";
    submitBtn.disabled = true;
    const prev = submitBtn.textContent;
    submitBtn.textContent = "Signing in…";
    const fd = new FormData(e.target);
    const username = (fd.get("username") || fd.get("email") || "").toString().trim();
    const orgCode = (fd.get("org_code") || "").toString().trim();
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          username: username || undefined,
          email: fd.get("email") || undefined,
          password: fd.get("password"),
          org_code: orgCode || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Invalid credentials");
      }
      const data = await res.json();
      setAuthToken(data.access_token);
      window.location.replace(DASHBOARD);
    } catch (err) {
      errorEl.textContent = err.message || "Login failed";
      submitBtn.disabled = false;
      submitBtn.textContent = prev;
    }
  });

  if (window.location.hash === "#login") {
    const loginEl = document.getElementById("login");
    if (loginEl) {
      requestAnimationFrame(() => loginEl.scrollIntoView({ behavior: "smooth" }));
    }
  }

  initReveal();

  verifyExistingSession().then((ok) => {
    if (ok) window.location.replace(DASHBOARD);
  });
})();