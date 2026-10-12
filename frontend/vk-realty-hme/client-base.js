/** Shared client dashboard path prefix (injected via meta or env default). */
(function () {
  function readBase() {
    const meta = document.querySelector('meta[name="client-base"]');
    const fromMeta = meta && meta.getAttribute("content");
    if (fromMeta && fromMeta.startsWith("/")) return fromMeta.replace(/\/$/, "");
    return "/vk-realty-hme";
  }
  window.clientBasePath = readBase;
})();