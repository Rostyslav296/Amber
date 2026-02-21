(async function () {
  // Wait for AgentF bridge
  if (window.AgentF && AgentF.ready) {
    await AgentF.ready();
  }

  // Show runtime version in the status bar
  const status = document.getElementById("status");
  try {
    const v = await AgentF.version();
    status.textContent = "AgentF v" + v;
  } catch {
    status.textContent = "Offline";
  }

  // Example: increment a visit counter via storage
  const root = document.getElementById("root");
  const visits = parseInt((await AgentF.storageGet("visits")) || "0", 10) + 1;
  await AgentF.storageSet("visits", String(visits));

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div>Welcome back! Visits: <span class="mono">${visits}</span></div>
    <button class="btn" id="btnEcho">Echo "hello"</button>
    <pre class="mono" id="log" style="margin-top:8px"></pre>
  `;
  root.appendChild(card);

  document.getElementById("btnEcho").addEventListener("click", async () => {
    const out = await AgentF.echo("hello");
    document.getElementById("log").textContent = out;
  });
})();
