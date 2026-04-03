// background.js
// MV3 service worker for the shared Gemini proxy, Google Calendar, and side panel state.
// ⚠️ TEMPORARY: hard-coded API keys (internal/demo use only)

const BACKEND_BASE = "https://webmailextensionugac-260151192882.asia-south1.run.app";
const sidePanelStateByTab = new Map();

async function buildBackendUrl(path) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${BACKEND_BASE}${normalizedPath}`;
}

function getSidePanelState(tabId) {
  return sidePanelStateByTab.get(tabId) || { autoOpened: false, opened: false, manuallyClosed: false };
}

function setSidePanelState(tabId, nextState) {
  sidePanelStateByTab.set(tabId, nextState);
}

function clearSidePanelState(tabId) {
  sidePanelStateByTab.delete(tabId);
}

function isWebmailUrl(url = "") {
  return typeof url === "string" && url.includes("webmail.iitb.ac.in");
}

function applySidePanelOptions(tabId, enabled) {
  return chrome.sidePanel.setOptions({
    tabId,
    path: "popup.html",
    enabled,
  });
}

function disableDefaultSidePanel() {
  return chrome.sidePanel.setOptions({
    enabled: false,
  });
}

function markTabState(tabId, updates) {
  const current = getSidePanelState(tabId);
  setSidePanelState(tabId, {
    ...current,
    ...updates,
  });
}

async function syncTabSidePanel(tabId, url = "") {
  const state = getSidePanelState(tabId);
  await applySidePanelOptions(tabId, isWebmailUrl(url) && state.opened === true);
}

disableDefaultSidePanel().catch((error) => console.error(error));

chrome.runtime.onInstalled.addListener(() => {
  disableDefaultSidePanel().catch((error) => console.error(error));
});

chrome.runtime.onStartup.addListener(() => {
  disableDefaultSidePanel().catch((error) => console.error(error));
});

/* -------------------- Auth Locking State -------------------- */
let isAuthPending = false;
let cachedToken = null;

/* -------------------- Message Router -------------------- */

chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  if (req.type === "OPEN_SIDEBAR" && sender.tab) {
    const state = getSidePanelState(sender.tab.id);
    if (state.autoOpened || state.manuallyClosed) {
      return;
    }

    markTabState(sender.tab.id, {
      autoOpened: true,
      opened: true,
      manuallyClosed: false,
    });

    // Keep the open() call directly in this message handler so Chrome still
    // treats it as a user-gesture-driven action from the originating click.
    applySidePanelOptions(sender.tab.id, true).catch((e) => console.error(e));
    chrome.sidePanel.open({ tabId: sender.tab.id })
      .catch((e) => console.error(e));
    return;
  }

  if (req.type === "GEMINI_SUMMARY") {
    handleGeminiProxy(req, sendResponse);
    return true; // ⛔ REQUIRED for async response
  }

  if (req.type === "ADD_CALENDAR_EVENT") {
    handleCalendarFlow(req.eventData, req.cardId);
  }

  if (req.type === "UPDATE_CALENDAR_EVENT") {
    handleCalendarFlow(req.eventData, req.cardId, req.eventId);
  }

  // ADDED FROM TEAMMATE: Handle account switching
  if (req.type === "SWITCH_GOOGLE_ACCOUNT") {
    cachedToken = null; // Clear local cache
    chrome.storage.local.remove('sessionToken', () => {
      handleCalendarFlow({}, null, null, true); 
    });
  }

  // NOTE: Web Speech API is handled locally in popup.js 
  // No background proxy required for native browser speech.
});

chrome.action.onClicked.addListener((tab) => {
  if (!tab?.id || !isWebmailUrl(tab.url)) {
    return;
  }

  markTabState(tab.id, {
    opened: true,
    manuallyClosed: false,
  });

  applySidePanelOptions(tab.id, true).catch((e) => console.error(e));
  chrome.sidePanel.open({ tabId: tab.id }).catch((e) => console.error(e));
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (!tab.url) {
    return;
  }

  await syncTabSidePanel(tabId, tab.url);
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    await syncTabSidePanel(tabId, tab.url);
  } catch (error) {
    console.error(error);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  clearSidePanelState(tabId);
});

if (chrome.sidePanel.onOpened) {
  chrome.sidePanel.onOpened.addListener((info) => {
    if (Number.isInteger(info.tabId)) {
      markTabState(info.tabId, { opened: true });
    }
  });
}

if (chrome.sidePanel.onClosed) {
  chrome.sidePanel.onClosed.addListener((info) => {
    if (Number.isInteger(info.tabId)) {
      markTabState(info.tabId, {
        opened: false,
        manuallyClosed: true,
      });
      applySidePanelOptions(info.tabId, false).catch((error) => console.error(error));
    }
  });
}

/* -------------------- Gemini Logic -------------------- */

async function handleGeminiProxy(req, sendResponse) {
  // 1. Check local storage for custom credentials first
  chrome.storage.local.get(['customApiKey', 'selectedModel'], async (data) => {
    const hasCustomKey = !!data.customApiKey;
    
    // 2. Build the standard Gemini payload
    const payload = req.payload || {
      contents: [{ parts: [{ text: req.prompt }] }]
    };

    try {
      let res;
      
      if (hasCustomKey) {
        /* --- PATH A: Direct to Google (User's Private Key) --- */
        console.log("[DEBUG] Using Custom User API Key");
        const model = req.model || data.selectedModel || 'gemini-2.5-flash';
        const endpoint = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${data.customApiKey}`;
        
        res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
      } else {
        /* --- PATH B: Route through Django (Backend Key) --- */
        console.log("[DEBUG] Routing to Django Backend Proxy");
        const backendProxyUrl = await buildBackendUrl('/api/gemini-proxy/');
        res = await fetch(backendProxyUrl, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Extension-Id": chrome.runtime.id,
            "X-Client-Feature": "iitb-mail-extension"
          },
          body: JSON.stringify({
            model: req.model || data.selectedModel || 'gemini-2.5-flash',
            payload
          })
        });
      }

      // 3. Handle Response
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        const errMsg = errJson.error?.message || `Error ${res.status}`;
        sendResponse({ ok: false, error: hasCustomKey ? `Custom Key Error: ${errMsg}` : `Backend Error: ${errMsg}` });
        return;
      }

      const json = await res.json();

      // 4. Normalize extraction (handle raw Google response OR Django's summary field)
      const text = json.summary || 
                   json.candidates?.[0]?.content?.parts?.map(p => p.text).join("\n") || 
                   "No response generated.";

      sendResponse({ ok: true, text });

    } catch (err) {
      console.error("[DEBUG] Proxy Error:", err);
      sendResponse({ ok: false, error: "Network error. Please check your connection and try again." });
    }
  });
}
/* -------------------- Google Calendar Logic -------------------- */

async function handleCalendarFlow(eventData, cardId, eventId = null, forceSelect = false) {
  // 1. Check local cache or storage
  const data = await chrome.storage.local.get(['sessionToken']);
  let token = cachedToken || data.sessionToken;

  if (token && !forceSelect) {
    executeCalendarInsert(token, eventData, cardId, eventId);
    return;
  }

  // If auth is already in progress, wait for the existing flow to finish.
  if (isAuthPending) {
    console.log("Auth already in progress. Queueing request for:", cardId);
    const checkInterval = setInterval(() => {
        if (cachedToken || !isAuthPending) {
            clearInterval(checkInterval);
            if (cachedToken) executeCalendarInsert(cachedToken, eventData, cardId, eventId);
        }
    }, 500);
    return;
  }

  const clientId = "204923188074-lev69m2gnnock5k9btjvqmle7pono50r.apps.googleusercontent.com";
  const redirectUri = `https://${chrome.runtime.id}.chromiumapp.org/`;
  const scopes = encodeURIComponent("https://www.googleapis.com/auth/calendar.events");

  const authUrl =
    `https://accounts.google.com/o/oauth2/v2/auth` +
    `?client_id=${clientId}` +
    `&response_type=token` +
    `&redirect_uri=${redirectUri}` +
    `&scope=${scopes}&prompt=select_account`;

  isAuthPending = true; // Lock the gate

  chrome.identity.launchWebAuthFlow(
    { url: authUrl, interactive: true },
    async (redirectUrl) => {
      isAuthPending = false; // Release the gate

      if (chrome.runtime.lastError || !redirectUrl) {
        if (forceSelect && !(eventData && eventData.title)) {
          chrome.runtime.sendMessage({
            type: "GOOGLE_ACCOUNT_SWITCH_RESULT",
            status: "error",
            message: "Google account switch failed."
          });
        } else {
          chrome.runtime.sendMessage({ 
            type: "CALENDAR_RESULT", 
            status: "error", 
            message: "Google login failed.",
            cardId: cardId 
          });
        }
        return;
      }

      const params = new URLSearchParams(new URL(redirectUrl).hash.substring(1));
      const newToken = params.get("access_token");
      if (newToken) {
        cachedToken = newToken;
        await chrome.storage.local.set({ sessionToken: newToken });
        
        // 1-Hour Logic: Clear token after 60 minutes
        setTimeout(() => {
            cachedToken = null;
            chrome.storage.local.remove('sessionToken');
        }, 3600000);

        // Only attempt insert if we have event data (not a pure account switch)
        if (eventData && eventData.title) {
          executeCalendarInsert(newToken, eventData, cardId, eventId);
        } else if (forceSelect) {
          chrome.runtime.sendMessage({
            type: "GOOGLE_ACCOUNT_SWITCH_RESULT",
            status: "success",
            message: "Google account switched successfully."
          });
        }
      }
    }
  );
}

async function executeCalendarInsert(token, data, cardId, eventId = null) {
  const startTimeVal = data.startTime || data.time;
  
  const event = {
    summary: data.title,
    description: data.description || "Added via IITB Mail Assistant",
  };

  // LOGIC: Check if this is a Timed Event or an All-Day Event
  if (startTimeVal && startTimeVal.trim() !== "") {
    // TIMED EVENT
    const startDateTime = `${data.date}T${startTimeVal}:00`;
    let endDateTime;

    if (data.endTime && /^\d{2}:\d{2}$/.test(data.endTime)) {
      endDateTime = `${data.date}T${data.endTime}:00`;
    } else {
      const [hours, minutes] = startTimeVal.split(':').map(Number);
      let endHours = hours + 1;
      let endDate = data.date;
      
      if (endHours >= 24) {
          endHours = 0;
      }
      const pad = (n) => n.toString().padStart(2, '0');
      endDateTime = `${endDate}T${pad(endHours)}:${pad(minutes)}:00`;
    }

    event.start = { dateTime: startDateTime, timeZone: "Asia/Kolkata" };
    event.end = { dateTime: endDateTime, timeZone: "Asia/Kolkata" };
  } else {
    // ALL-DAY EVENT (No time provided)
    event.start = { date: data.date };
    event.end = { date: data.date };
  }

  const url = eventId 
    ? `https://www.googleapis.com/calendar/v3/calendars/primary/events/${eventId}`
    : "https://www.googleapis.com/calendar/v3/calendars/primary/events";
  
  const method = eventId ? "PATCH" : "POST";

  try {
    const res = await fetch(url, {
        method: method,
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(event)
      }
    );

    if (res.status === 401) {
      cachedToken = null;
      await chrome.storage.local.remove('sessionToken');
      handleCalendarFlow(data, cardId, eventId);
      return;
    }

    if (res.ok) {
      const savedEvent = await res.json();
      chrome.runtime.sendMessage({ 
        type: "CALENDAR_RESULT", 
        status: "success", 
        cardId: cardId,
        eventId: savedEvent.id 
      });

      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: eventId ? "Event Updated" : "Event Scheduled",
        message: startTimeVal ? `Scheduled for ${startTimeVal} (IST)` : `Scheduled as All Day Event`,
        priority: 2
      });
    } else {
      const errorInfo = await res.json();
      chrome.runtime.sendMessage({ type: "CALENDAR_RESULT", status: "error", message: errorInfo.error?.message, cardId: cardId });
    }
  } catch (err) {
    chrome.runtime.sendMessage({ type: "CALENDAR_RESULT", status: "error", message: "Network error.", cardId: cardId });
  }

}
