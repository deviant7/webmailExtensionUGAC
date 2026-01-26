// background.js
// MV3-safe background service worker
// Gemini Proxy + Google Calendar Handler
// ⚠️ TEMPORARY: hard-coded API keys (internal/demo use only)

const GEMINI_API_KEY = "apikey";

/* -------------------- Auth Locking State -------------------- */
let isAuthPending = false; // The Gatekeeper lock
let cachedToken = null;    // Temporary storage for the hour-long session

/* -------------------- Message Router -------------------- */

chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  // NEW: Content Script trigger to bypass gesture restriction
  if (req.type === "OPEN_SIDEBAR" && sender.tab) {
    chrome.sidePanel.open({ tabId: sender.tab.id }).catch(e => console.error(e));
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

// Auto-enable side panel options when entering the tab
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (tab.url && tab.url.includes("webmail.iitb.ac.in")) {
    await chrome.sidePanel.setOptions({
      tabId,
      path: 'popup.html',
      enabled: true
    });
  }
});

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error(error));

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
        res = await fetch("http://127.0.0.1:8000/api/gemini-proxy/", {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "Accept": "application/json"
          },
          body: JSON.stringify(payload)
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
      sendResponse({ ok: false, error: "Network error. Check if Django is running or your internet connection." });
    }
  });
}
/* -------------------- Google Calendar Logic -------------------- */

// UPDATED: Now supports locking mechanism to prevent multiple login popups
async function handleCalendarFlow(eventData, cardId, eventId = null, forceSelect = false) {
  // 1. Check local cache or storage
  const data = await chrome.storage.local.get(['sessionToken']);
  let token = cachedToken || data.sessionToken;

  if (token && !forceSelect) {
    executeCalendarInsert(token, eventData, cardId, eventId);
    return;
  }

  // 2. QUEUEING LOGIC: If a request is already fetching a token, wait for it
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

  const clientId = "451389975470-e42fi26fo0gbde0d9ppafei19ctk63nb.apps.googleusercontent.com";
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
        chrome.runtime.sendMessage({ 
          type: "CALENDAR_RESULT", 
          status: "error", 
          message: "Google login failed.",
          cardId: cardId 
        });
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