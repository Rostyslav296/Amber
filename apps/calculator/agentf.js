// Copy of apps/_template/lib/agentf.js
(function () {
  const listeners = [];
  let readyPromiseResolve;
  let AgentFReady = new Promise(r => (readyPromiseResolve = r));
  window.addEventListener("agentf-ready", () => { ensureBridge(); });
  const poll = setInterval(() => { if (window.AgentF) { clearInterval(poll); ensureBridge(); } }, 50);
  function ensureBridge() {
    if (!window.AgentF) return;
    if (!window.AgentF._wrapped) {
      const base = window.AgentF;
      async function call(fn, ...args) { return await base[fn](...args); }
      const API = {
        __proto__: base,
        ready() { return AgentFReady; },
        version() { return call("version"); },
        echo(s) { return call("echo", String(s)); },
        log(s) { try { base.log(String(s)); } catch{} },
        storageGet(key) { return call("storage_get", key); },
        storageSet(key, val) { return call("storage_set", key, String(val)); },
        onEvent(cb) { if (typeof cb === "function") listeners.push(cb); if (base.onEvent) base.onEvent(cb); },
        _wrapped: true
      };
      window.AgentF = API;
    }
    if (readyPromiseResolve) { readyPromiseResolve(); readyPromiseResolve = null; }
    try { window.AgentF.log("AgentF bridge ready"); } catch {}
  }
})();
