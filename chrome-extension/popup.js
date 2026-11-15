// OmniBridge Auto-Sync - Popup Controller

document.addEventListener("DOMContentLoaded", async () => {
  const serverInput = document.getElementById("server-url");
  const apiKeyInput = document.getElementById("api-key");
  const syncBtn = document.getElementById("sync-btn");
  const badge = document.getElementById("sync-badge");
  const timeSpan = document.getElementById("last-sync-time");
  const toast = document.getElementById("toast-msg");

  const dotPsid = document.getElementById("dot-psid");
  const valPsid = document.getElementById("val-psid");
  const dotPsidts = document.getElementById("dot-psidts");
  const valPsidts = document.getElementById("val-psidts");
  const openGeminiLink = document.getElementById("link-open-gemini");

  // Open Gemini link handler
  if (openGeminiLink) {
    openGeminiLink.addEventListener("click", () => {
      chrome.tabs.create({ url: "https://gemini.google.com/app" });
    });
  }

  // Load saved settings
  const settings = await chrome.storage.local.get({
    serverUrl: "https://free-gemini-pro-api-me28.onrender.com",
    apiKey: "sk-omni--dhAN9jUNjBaYZTGQviPMEq9CcNastH2IEgUdzdktII",
    lastSyncTime: null,
    syncStatus: "pending"
  });

  serverInput.value = settings.serverUrl;
  apiKeyInput.value = settings.apiKey;

  function showToast(text, isError = false) {
    toast.style.display = "block";
    toast.textContent = text;
    toast.style.background = isError ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.2)";
    toast.style.color = isError ? "#f87171" : "#34d399";
    toast.style.border = isError ? "1px solid rgba(239, 68, 68, 0.3)" : "1px solid rgba(16, 185, 129, 0.3)";
    setTimeout(() => {
      toast.style.display = "none";
    }, 4000);
  }

  function updateStatus(status, time) {
    if (status === "success") {
      badge.textContent = "متصل و فعال ✅";
      badge.className = "badge badge-green";
    } else if (status === "error") {
      badge.textContent = "خطا در اتصال ❌";
      badge.className = "badge badge-red";
    } else {
      badge.textContent = "در انتظار بررسی ⏳";
      badge.className = "badge badge-yellow";
    }

    if (time) {
      const d = new Date(time);
      timeSpan.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } else {
      timeSpan.textContent = "هنوز انجام نشده";
    }
  }

  updateStatus(settings.syncStatus, settings.lastSyncTime);

  // Check live cookie detection
  chrome.runtime.sendMessage({ action: "check_cookies" }, (res) => {
    if (res && res.gemini) {
      if (res.gemini.hasPsid) {
        dotPsid.className = "dot dot-green";
        valPsid.textContent = res.gemini.psidPreview || "یافت شد ✅";
        valPsid.style.color = "#34d399";
      } else {
        dotPsid.className = "dot dot-red";
        valPsid.textContent = "یافت نشد ❌";
        valPsid.style.color = "#ef4444";
      }

      if (res.gemini.hasPsidts) {
        dotPsidts.className = "dot dot-green";
        valPsidts.textContent = res.gemini.psidtsPreview || "یافت شد ✅";
        valPsidts.style.color = "#34d399";
      } else {
        dotPsidts.className = "dot dot-red";
        valPsidts.textContent = "یافت نشد ❌";
        valPsidts.style.color = "#ef4444";
      }
    }
  });

  // Auto-save fields
  serverInput.addEventListener("input", () => {
    chrome.storage.local.set({ serverUrl: serverInput.value.trim() });
  });
  apiKeyInput.addEventListener("input", () => {
    chrome.storage.local.set({ apiKey: apiKeyInput.value.trim() });
  });

  // Manual Sync trigger
  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncBtn.innerHTML = "<span>⏳</span><span>در حال استخراج و ارسال...</span>";

    await chrome.storage.local.set({
      serverUrl: serverInput.value.trim(),
      apiKey: apiKeyInput.value.trim()
    });

    chrome.runtime.sendMessage({ action: "manual_sync" }, (res) => {
      syncBtn.disabled = false;
      syncBtn.innerHTML = "<span>🔄</span><span>همگام‌سازی فوری کوکی‌ها</span>";

      if (res && res.success) {
        updateStatus("success", Date.now());
        showToast("✅ کوکی‌های جدید با موفقیت به سرور Render ارسال شدند!");
      } else {
        updateStatus("error", Date.now());
        showToast("❌ ارسال ناموفق بود! لطفاً آدرس سرور یا لاگین جمنای را بررسی کنید.", true);
      }
    });
  });
});
