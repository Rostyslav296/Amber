// Lightweight helper around the AgentF WebChannel bridge.
// Guarantees AgentF is ready before use and provides tiny utilities.
(function () {
  const listeners = [];
  let readyPromiseResolve;
  let AgentFReady = new Promise(r => (readyPromiseResolve = r));

  // If agentf.py injected bootstrap already fired, we should get an event.
  window.addEventListener("agentf-ready", () => {
    ensureBridge();
  });

  // Fallback: poll for window.AgentF (in case event arrived early).
  const poll = setInterval(() => {
    if (window.AgentF) {
      clearInterval(poll);
      ensureBridge();
    }
  }, 50);

  function ensureBridge() {
    if (!window.AgentF) return;
    // Expose tiny helpers on global AgentF if not present
    if (!window.AgentF._wrapped) {
      const base = window.AgentF;

      // Promise-returning wrapper for possibly immediate-return slots
      async function call(fn, ...args) {
        try {
          return await base[fn](...args);
        } catch (e) {
          console.error("[AgentF] call failed", fn, e);
          throw e;
        }
      }

      const API = {
        __proto__: base,
        ready() { return AgentFReady; },
        version() { return call("version"); },
        echo(s) { return call("echo", String(s)); },
        log(s) { try { base.log(String(s)); } catch (e) { /* ignore */ } },

        // storage helpers
        async storageGet(key) { return await call("storage_get", key); },
        async storageSet(key, val) { await call("storage_set", key, String(val)); },

        // event subscription from host to JS
        onEvent(cb) {
          if (typeof cb === "function") listeners.push(cb);
          if (base.onEvent) base.onEvent(cb); // bootstrap may provide connect
        },

        _wrapped: true
      };

      window.AgentF = API;
    }
    // Mark ready once
    if (readyPromiseResolve) {
      readyPromiseResolve();
      readyPromiseResolve = null;
    }
    try { window.AgentF.log("AgentF bridge ready"); } catch {}
  }

})();
