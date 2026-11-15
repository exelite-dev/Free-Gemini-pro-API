document.addEventListener("DOMContentLoaded", async () => {
  const serverInput = document.getElementById("server-url");
  const apiKeyInput = document.getElementById("api-key");
  const syncBtn = document.getElementById("sync-btn");
  const badge = document.getElementById("sync-badge");
  const timeSpan = document.getElementById("last-sync-time");

  // Load saved settings
  const data = await chrome.storage.local.get({
    serverUrl: "https://free-gemini-pro-api-me28.onrender.com",
    apiKey: "sk-omni--dhAN9jUNjBaYZTGQviPMEq9CcNastH2IEgUdzdktII",
    lastSync: null,
    syncStatus: "pending"
  });

  serverInput.value = data.serverUrl;
  apiKeyInput.value = data.apiKey;

  function updateStatus(status, time) {
    if (status === "success") {
      badge.textContent = "فعال و متصل ✅";
      badge.className = "badge badge-green";
    } else if (status === "error") {
      badge.textContent = "خطا در اتصال ❌";
      badge.className = "badge badge-red";
    } else {
      badge.textContent = "در انتظار ارسال ⏳";
      badge.className = "badge badge-yellow";
    }

    if (time) {
      const d = new Date(time);
      timeSpan.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } else {
      timeSpan.textContent = "هنوز انجام نشده";
    }
  }

  updateStatus(data.syncStatus, data.lastSync);

  // Save changes on input
  serverInput.addEventListener("change", () => {
    chrome.storage.local.set({ serverUrl: serverInput.value.trim() });
  });
  apiKeyInput.addEventListener("change", () => {
    chrome.storage.local.set({ apiKey: apiKeyInput.value.trim() });
  });

  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = "⏳ در حال استخراج و ارسال...";

    await chrome.storage.local.set({
      serverUrl: serverInput.value.trim(),
      apiKey: apiKeyInput.value.trim()
    });

    chrome.runtime.sendMessage({ action: "manual_sync" }, (res) => {
      syncBtn.disabled = false;
      syncBtn.textContent = "🔄 ارسال و سینک فوری کوکی‌ها";
      if (res && res.success) {
        updateStatus("success", Date.now());
      } else {
        updateStatus("error", Date.now());
      }
    });
  });
});
