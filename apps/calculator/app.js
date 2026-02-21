(() => {
  const q = (sel, el = document) => el.querySelector(sel);
  const qa = (sel, el = document) => Array.from(el.querySelectorAll(sel));

  const display = q('#display');
  const historyList = q('#historyList');
  const statusEl = q('#status');

  // ---- Calculator state machine ----
  const state = {
    a: null,      // first operand (number)
    b: null,      // second operand (number)
    op: null,     // "+", "−", "×", "÷", "%"
    enteringB: false,
    overwrite: true, // next digit overwrites display
    lastEq: null, // last {a, b, op} for repeated "="
  };

  const fmt = (n) => {
    if (!isFinite(n)) return '∞';
    // Keep up to 10 fractional digits, trim zeros
    let s = Number(n).toFixed(10);
    s = s.replace(/\.?0+$/, '');
    return s;
  };

  const readDisplay = () => display.textContent.trim();
  const setDisplay = (v) => { display.textContent = String(v); };

  const clearAll = () => {
    state.a = state.b = null;
    state.op = null;
    state.enteringB = false;
    state.overwrite = true;
    state.lastEq = null;
    setDisplay('0');
  };

  const backspace = () => {
    if (state.overwrite) return;
    const s = readDisplay();
    if (s.length <= 1 || (s.length === 2 && s.startsWith('-'))) {
      setDisplay('0'); state.overwrite = true; return;
    }
    setDisplay(s.slice(0, -1));
  };

  const inputDigit = (d) => {
    if (state.overwrite) {
      setDisplay(d === '.' ? '0.' : d);
      state.overwrite = false;
      return;
    }
    const s = readDisplay();
    if (d === '.' && s.includes('.')) return;
    if (s === '0' && d !== '.') {
      setDisplay(d);
    } else {
      setDisplay(s + d);
    }
  };

  const commitNumber = () => Number(readDisplay());

  const applyOp = (a, op, b) => {
    switch (op) {
      case '+': return a + b;
      case '−': return a - b;
      case '×': return a * b;
      case '÷': return b === 0 ? NaN : a / b;
      default:  return b;
    }
  };

  const pushHist = (line) => {
    const old = historyList.textContent.trim();
    historyList.textContent = old ? (old + '\n' + line) : line;
    historyList.scrollTop = historyList.scrollHeight;
  };

  const setStatus = (s) => { if (statusEl) statusEl.textContent = s; };

  const chooseOp = (op) => {
    if (state.op && !state.enteringB && !state.overwrite) {
      // Replace pending op if user taps operators repeatedly
      state.op = op; setStatus(`Op: ${op}`); return;
    }
    if (state.a === null) {
      state.a = commitNumber();
    } else if (state.enteringB && !state.overwrite) {
      // chain operation: a op b -> result, then set new op
      state.b = commitNumber();
      const r = applyOp(state.a, state.op, state.b);
      pushHist(`${fmt(state.a)} ${state.op} ${fmt(state.b)} = ${fmt(r)}`);
      state.a = r;
      setDisplay(fmt(r));
    }
    state.op = op;
    state.enteringB = true;
    state.overwrite = true;
    setStatus(`Op: ${op}`);
  };

  const doPercent = () => {
    // Common calculator behavior: when entering B, treat % as (a * b / 100)
    if (state.enteringB && state.a !== null) {
      const b = commitNumber();
      const p = state.a * (b / 100);
      setDisplay(fmt(p));
      state.overwrite = true;
      setStatus('Percent');
    } else {
      // Otherwise percentage of the current display
      const v = commitNumber();
      setDisplay(fmt(v / 100));
      state.overwrite = true;
      setStatus('Percent');
    }
  };

  const equals = () => {
    let a = state.a, op = state.op, b;

    if (a === null || !op) {
      // Repeat last equation if present
      if (state.lastEq) {
        a = Number(readDisplay());
        op = state.lastEq.op;
        b = state.lastEq.b;
      } else {
        return;
      }
    } else {
      b = commitNumber();
      state.lastEq = { a, b, op };
    }

    const r = applyOp(a, op, b);
    pushHist(`${fmt(a)} ${op} ${fmt(b)} = ${fmt(r)}`);
    setDisplay(fmt(r));
    state.a = r;
    state.enteringB = false;
    state.overwrite = true;
    setStatus('Done');
  };

  // ---- Wiring buttons ----
  const onKey = (k) => {
    if (k === 'AC') return clearAll();
    if (k === '⌫') return backspace();
    if (k === '=') return equals();
    if (k === '%') return doPercent();

    if ('0123456789.'.includes(k)) return inputDigit(k);
    if ('+−×÷'.includes(k)) return chooseOp(k);
  };

  qa('[data-k]').forEach(btn => {
    btn.addEventListener('click', () => onKey(btn.getAttribute('data-k')));
  });

  // ---- Keyboard support ----
  const keyMap = {
    'Enter': '=', '=': '=',
    '+': '+', '-': '−', '*': '×', 'x': '×', '/': '÷',
    '%': '%', '.': '.', ',': '.',
    'Backspace': '⌫', 'Escape': 'AC',
  };
  window.addEventListener('keydown', (e) => {
    const k = e.key;
    if (k >= '0' && k <= '9') { onKey(k); e.preventDefault(); return; }
    if (keyMap[k]) { onKey(keyMap[k]); e.preventDefault(); return; }
  });

  // ---- Status & environment integration ----
  setStatus('Ready');
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) setStatus('Ready');
  });

  // Optional: detect AgentF bridge (Qt) and reflect it in status
  if (window.qt || window.AgentFBridge) setStatus('Connected');

  // Make basic helpers available for automation sanity checks (read-only)
  window.__calc = {
    exists: (sel) => !!document.querySelector(sel),
    getText: (sel) => (document.querySelector(sel)?.textContent ?? ''),
  };
})();
