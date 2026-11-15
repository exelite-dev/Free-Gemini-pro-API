// OmniBridge Gemini Auto-Sync - Background Service Worker

async function getCookies() {
  const psid = await chrome.cookies.get({ url: "https://gemini.google.com", name: "__Secure-1PSID" });
  const psidts = await chrome.cookies.get({ url: "https://gemini.google.com", name: "__Secure-1PSIDTS" });
  return {
    secure_1psid: psid ? psid.value : null,
    secure_1psidts: psidts ? psidts.value : null
  };
}

async function syncCookiesToServer() {
  const { serverUrl, apiKey } = await chrome.storage.local.get({
    serverUrl: "https://free-gemini-pro-api-me28.onrender.com",
    apiKey: "sk-omni--dhAN9jUNjBaYZTGQviPMEq9CcNastH2IEgUdzdktII"
  });

  if (!serverUrl || !apiKey) {
    console.log("[OmniBridge Sync] Server URL or API Key missing.");
    return false;
  }

  const cookies = await getCookies();
  if (!cookies.secure_1psid || !cookies.secure_1psidts) {
    console.log("[OmniBridge Sync] Gemini cookies not found in browser yet.");
    return false;
  }

  try {
    const endpoint = `${serverUrl.replace(/\/+$/, '')}/admin/api/sync-gemini`;
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        secure_1psid: cookies.secure_1psid,
        secure_1psidts: cookies.secure_1psidts,
        label: "Chrome Browser Auto-Sync"
      })
    });

    if (resp.ok) {
      const data = await resp.json();
      console.log("[OmniBridge Sync] Successfully synced cookies to server!", data);
      await chrome.storage.local.set({ lastSync: Date.now(), syncStatus: "success" });
      chrome.action.setBadgeText({ text: "OK" });
      chrome.action.setBadgeBackgroundColor({ color: "#10b981" });
      return true;
    } else {
      console.warn("[OmniBridge Sync] Server responded with error status:", resp.status);
      await chrome.storage.local.set({ syncStatus: "error", lastError: `HTTP ${resp.status}` });
      chrome.action.setBadgeText({ text: "ERR" });
      chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
      return false;
    }
  } catch (err) {
    console.error("[OmniBridge Sync] Network error syncing cookies:", err);
    await chrome.storage.local.set({ syncStatus: "error", lastError: String(err) });
    return false;
  }
}

// 1. Listen for cookie changes on Google Gemini
chrome.cookies.onChanged.addListener((changeInfo) => {
  if (
    changeInfo.cookie.domain.includes("google.com") &&
    (changeInfo.cookie.name === "__Secure-1PSIDTS" || changeInfo.cookie.name === "__Secure-1PSID") &&
    !changeInfo.removed
  ) {
    console.log("[OmniBridge Sync] Detected Gemini cookie refresh in browser! Syncing...");
    syncCookiesToServer();
  }
});

// 2. Periodic sync check every 20 minutes
chrome.alarms.create("omnibridge_periodic_sync", { periodInMinutes: 20 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "omnibridge_periodic_sync") {
    syncCookiesToServer();
  }
});

// 3. Sync on startup
chrome.runtime.onInstalled.addListener(() => {
  syncCookiesToServer();
});
chrome.runtime.onStartup.addListener(() => {
  syncCookiesToServer();
});

// Listen for messages from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "manual_sync") {
    syncCookiesToServer().then(success => sendResponse({ success }));
    return true;
  }
});
