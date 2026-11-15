// OmniBridge Auto-Sync - Background Service Worker (Manifest V3)

let syncDebounceTimer = null;

// ── Cookie Extraction ────────────────────────────────────────────────────────

async function getGeminiCookies() {
  const urls = ["https://gemini.google.com", "https://google.com"];
  let psid = null;
  let psidts = null;

  for (const url of urls) {
    if (!psid) {
      const c = await chrome.cookies.get({ url, name: "__Secure-1PSID" });
      if (c && c.value) psid = c.value;
    }
    if (!psidts) {
      const c = await chrome.cookies.get({ url, name: "__Secure-1PSIDTS" });
      if (c && c.value) psidts = c.value;
    }
  }

  return {
    secure_1psid: psid,
    secure_1psidts: psidts
  };
}

async function getChatGptToken() {
  const c = await chrome.cookies.get({
    url: "https://chatgpt.com",
    name: "__Secure-next-auth.session-token"
  });
  return c && c.value ? c.value : null;
}

// ── Server Sync Execution ───────────────────────────────────────────────────

async function syncAllToServer() {
  const { serverUrl, apiKey, autoSyncEnabled } = await chrome.storage.local.get({
    serverUrl: "https://free-gemini-pro-api-me28.onrender.com",
    apiKey: "sk-omni--dhAN9jUNjBaYZTGQviPMEq9CcNastH2IEgUdzdktII",
    autoSyncEnabled: true
  });

  if (!autoSyncEnabled) {
    console.log("[OmniBridge Auto-Sync] Sync is disabled in settings.");
    return { success: false, reason: "disabled" };
  }

  if (!serverUrl || !apiKey) {
    console.warn("[OmniBridge Auto-Sync] Server URL or API Key missing.");
    return { success: false, reason: "missing_config" };
  }

  const base = serverUrl.replace(/\/+$/, "");
  const results = { gemini: null, chatgpt: null };

  // 1. Sync Gemini
  const gemini = await getGeminiCookies();
  if (gemini.secure_1psid && gemini.secure_1psidts) {
    try {
      const resp = await fetch(`${base}/admin/api/sync-gemini`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          secure_1psid: gemini.secure_1psid,
          secure_1psidts: gemini.secure_1psidts,
          label: "Chrome Extension Auto-Sync"
        })
      });

      if (resp.ok) {
        results.gemini = true;
      } else {
        console.warn("[OmniBridge Sync] Gemini sync returned HTTP", resp.status);
        results.gemini = false;
      }
    } catch (e) {
      console.error("[OmniBridge Sync] Gemini network error:", e);
      results.gemini = false;
    }
  }

  // 2. Sync ChatGPT (optional if user has logged in)
  const chatGptToken = await getChatGptToken();
  if (chatGptToken) {
    try {
      const resp = await fetch(`${base}/admin/api/sync-chatgpt`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          session_token: chatGptToken,
          label: "Chrome Extension Auto-Sync"
        })
      });
      if (resp.ok) {
        results.chatgpt = true;
      }
    } catch (e) {
      console.error("[OmniBridge Sync] ChatGPT network error:", e);
    }
  }

  // 3. Update Status
  const isAnySuccess = results.gemini === true || results.chatgpt === true;
  if (isAnySuccess) {
    await chrome.storage.local.set({
      lastSyncTime: Date.now(),
      syncStatus: "success",
      geminiDetected: !!(gemini.secure_1psid && gemini.secure_1psidts),
      chatgptDetected: !!chatGptToken
    });
    chrome.action.setBadgeText({ text: "OK" });
    chrome.action.setBadgeBackgroundColor({ color: "#10b981" });
    return { success: true, results };
  } else {
    await chrome.storage.local.set({
      syncStatus: "error",
      geminiDetected: !!(gemini.secure_1psid && gemini.secure_1psidts),
      chatgptDetected: !!chatGptToken
    });
    chrome.action.setBadgeText({ text: "ERR" });
    chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
    return { success: false, results };
  }
}

// ── Debounced Event Listener ────────────────────────────────────────────────

function triggerDebouncedSync() {
  if (syncDebounceTimer) clearTimeout(syncDebounceTimer);
  syncDebounceTimer = setTimeout(() => {
    syncAllToServer();
  }, 1500);
}

// Watch for Google Gemini & ChatGPT cookies
chrome.cookies.onChanged.addListener((changeInfo) => {
  if (changeInfo.removed) return;
  const name = changeInfo.cookie.name;
  const domain = changeInfo.cookie.domain;

  if (
    (domain.includes("google.com") && (name === "__Secure-1PSIDTS" || name === "__Secure-1PSID")) ||
    (domain.includes("chatgpt.com") && name === "__Secure-next-auth.session-token")
  ) {
    console.log(`[OmniBridge Sync] Cookie changed: ${name} on ${domain}. Debouncing sync...`);
    triggerDebouncedSync();
  }
});

// Periodic keep-alive sync every 15 minutes
chrome.alarms.create("omnibridge_periodic_alarm", { periodInMinutes: 15 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "omnibridge_periodic_alarm") {
    syncAllToServer();
  }
});

// Lifecycle Triggers
chrome.runtime.onInstalled.addListener(() => {
  syncAllToServer();
});
chrome.runtime.onStartup.addListener(() => {
  syncAllToServer();
});

// Messaging from Popup UI
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "manual_sync") {
    syncAllToServer().then((res) => sendResponse(res));
    return true;
  }
  if (msg.action === "check_cookies") {
    Promise.all([getGeminiCookies(), getChatGptToken()]).then(([gem, cgp]) => {
      sendResponse({
        gemini: {
          hasPsid: !!gem.secure_1psid,
          hasPsidts: !!gem.secure_1psidts,
          psidPreview: gem.secure_1psid ? gem.secure_1psid.slice(0, 10) + "..." : null,
          psidtsPreview: gem.secure_1psidts ? gem.secure_1psidts.slice(0, 10) + "..." : null,
        },
        chatgpt: {
          hasToken: !!cgp,
          tokenPreview: cgp ? cgp.slice(0, 10) + "..." : null,
        }
      });
    });
    return true;
  }
});
