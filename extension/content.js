// content.js — robust extraction of opened mail body (SPA-safe)

/* -------------------- Helpers -------------------- */

function cleanMailBody(text) {
  if (!text) return "";
  return text
    .replace(/=+/g, "")
    .replace(/If you reply to mails received.*?\n/gi, "")
    .replace(/please take care.*?\n/gi, "")
    .replace(/NOT\*\*/gi, "")
    .replace(/To unsubscribe.*?\n/gi, "")
    .replace(/Your subscribed address.*?\n/gi, "")
    .replace(/automated message.*?\n/gi, "")
    .replace(/do not reply.*?\n/gi, "")
    .replace(/PLEASE READ BELOW.*?\n/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/* -------------------- Core Extraction & Storage Check -------------------- */

// Check on every load if there's a pending injection (from Version 2/Automation)
chrome.storage.local.get(['pendingCompose'], (result) => {
  if (result.pendingCompose) {
    const { subject, body, timestamp } = result.pendingCompose;
    // Only inject if the request is fresh (less than 30 seconds old)
    if (Date.now() - timestamp < 30000) {
      handleNewComposeInjection(subject, body);
    }
    // Clear it so it doesn't double-inject
    chrome.storage.local.remove('pendingCompose');
  }
});

function handleNewComposeInjection(subject, body) {
  let attempts = 0;
  const timer = setInterval(() => {
    attempts++;
    
    // Updated Selectors for Roundcube (covers both Compose and Reply views)
    const subjectField = document.getElementById('compose-subject') || 
                         document.querySelector('input[name="_subject"]');
    
    // Check for the TinyMCE Iframe (HTML mode) - Roundcube uses ID 'composebody_ifr'
    const bodyIframe = document.getElementById('composebody_ifr') || 
                       document.getElementById('_message_ifr'); 
                       
    // Check for the Standard Textarea (Plain text mode) - Roundcube uses ID 'composebody' or name '_message'
    const bodyTextarea = document.getElementById('composebody') || 
                         document.querySelector('textarea[name="_message"]');

    // Logic: Subject is only filled if it exists and a value was provided (avoids clearing reply subjects)
    if (subjectField && subject) {
      subjectField.value = subject;
    }

    if (bodyIframe || bodyTextarea) {
      // 2. Fill Body
      if (bodyIframe) {
        try {
          const doc = bodyIframe.contentDocument || bodyIframe.contentWindow.document;
          // Ensure the iframe body exists before writing
          if (doc && doc.body) {
             // Convert newlines to <br> because this is an HTML editor
             const htmlBody = (body || "").replace(/\n/g, '<br>');
             
             // If it's a reply (subject is null), we prepend to keep the thread below
             if (subject === null) {
                doc.body.innerHTML = htmlBody + "<br><br>" + doc.body.innerHTML;
             } else {
                doc.body.innerHTML = htmlBody;
             }
             
             console.log("IITB Assistant: Fields injected into Iframe.");
             clearInterval(timer);
          }
        } catch (e) { 
          console.error("Iframe access blocked", e); 
        }
      } else if (bodyTextarea) {
        if (subject === null) {
            bodyTextarea.value = (body || "") + "\n\n" + bodyTextarea.value;
        } else {
            bodyTextarea.value = body || "";
        }
        console.log("IITB Assistant: Fields injected into Textarea.");
        clearInterval(timer);
      }
    }

    if (attempts > 60) { // Increased to 30 seconds to survive slow URL loads
      clearInterval(timer);
      console.warn("IITB Assistant: Timeout waiting for compose fields.");
    }
  }, 500);
}

async function injectNewCompose(subject, body) {
  // Logic: Differentiate between Reply and New Compose
  const isReply = (subject === null);

  if (isReply) {
    // 1. Find the REAL reply button in the toolbar
    const replyBtn = 
        document.querySelector('a.button.reply[title="Reply to sender"]') || 
        document.querySelector('a.button.reply') ||
        document.getElementById('rcmbtn124') ||
        document.querySelector('.toolbar a.reply');

    if (replyBtn) {
        chrome.storage.local.set({
            pendingCompose: { subject: null, body: body, timestamp: Date.now() }
        }, () => {
            replyBtn.click();
        });
    } else {
        // If no button found, maybe the reply window is already open?
        handleNewComposeInjection(null, body);
    }
  } else {
    // 1. Locate the "Compose" button and click it
    const composeBtn = document.getElementById('rcmbtn107') || 
                        document.querySelector('a.compose[title="Create a new message"]') ||
                        document.querySelector('a.compose');
    
    if (composeBtn) {
      // Save to storage first to ensure it survives the navigation
      chrome.storage.local.set({
        pendingCompose: { subject, body, timestamp: Date.now() }
      }, () => {
        composeBtn.click();
      });
    } else {
      // If no button is found, check if we are already in the compose view
      console.log("IITB Assistant: No compose button found, checking if fields already exist...");
      handleNewComposeInjection(subject, body);
    }
  }
}

function tryExtractMailBody() {
  let body = "";

  const iframes = Array.from(document.querySelectorAll("iframe"));
  for (const f of iframes) {
    try {
      const doc = f.contentDocument;
      if (!doc || !doc.body) continue;

      const text = doc.body.innerText?.trim() || "";
      if (text.length > body.length) body = text;
    } catch {}
  }

  if (!body) {
    const bodyEl = document.querySelector(
      "#message-content, .message-body, .preview-pane, .mail-body, article"
    );
    if (bodyEl) {
      const clone = bodyEl.cloneNode(true);
      clone.querySelectorAll("script, style, iframe").forEach(n => n.remove());
      body = clone.innerText.trim();
    }
  }

  return body ? cleanMailBody(body) : null;
}

/* -------------------- Wait Logic -------------------- */

function waitForMailBody(maxAttempts = 10, interval = 300) {
  return new Promise(resolve => {
    let attempts = 0;

    const timer = setInterval(() => {
      const body = tryExtractMailBody();
      attempts++;

      if (body) {
        clearInterval(timer);
        resolve(body);
      } else if (attempts >= maxAttempts) {
        clearInterval(timer);
        resolve(null);
      }
    }, interval);
  });
}

function findReplyButton(root = document) {
  // STRICT: only "Reply to sender"
  return root.querySelector(
    'a.reply[title="Reply to sender"], a.reply#rcmbtn124'
  );
}

function findReplyEditor(root = document) {
  return (
    root.querySelector('textarea#composebody') ||
    root.querySelector('textarea[name="_message"]') ||
    root.querySelector('textarea[name="body"]') ||
    root.querySelector('textarea')
  );
}

function fillReplyEditor(replyText) {
  // Manual trigger for FILL_REPLY message type
  const replyBtn = 
    document.querySelector('a.button.reply[title="Reply to sender"]') || 
    document.querySelector('a.button.reply') ||
    document.getElementById('rcmbtn124') ||
    document.querySelector('.toolbar a.reply');

  if (!replyBtn) {
    console.error("IITB Assistant: Reply button not found.");
    return;
  }

  replyBtn.click();

  let attempts = 0;
  const timer = setInterval(() => {
    attempts++;
    const editor = document.querySelector('textarea#composebody') || 
                   document.querySelector('textarea[name="_message"]');

    if (editor) {
      editor.value = replyText + "\n\n" + editor.value;
      clearInterval(timer);
    }
    if (attempts >= 20) clearInterval(timer);
  }, 500);
}

/* -------------------- Message Handler -------------------- */

chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {

  /* ---------- mail extraction ---------- */
  if (req?.type === "GET_OPENED_MAIL") {
    waitForMailBody().then(body => {
      if (!body) {
        sendResponse({ ok: false, error: "No opened mail detected" });
        return;
      }

      sendResponse({
        ok: true,
        mail: { body }
      });
    });

    return true; // Keep channel open for async
  }

  /* ---------- fill reply ---------- */
  if (req?.type === "FILL_REPLY") {
    fillReplyEditor(req.replyText);
    sendResponse({ ok: true });
  }

  /* ---------- Inject New Compose (From automation flow) ---------- */
  if (req?.type === "COMPOSE_NEW_MAIL") {
    // This handler now serves both New Compose and Reply injections
    injectNewCompose(req.subject, req.body);
    sendResponse({ ok: true });
  }

  return true;
});

/* ---------- Redundant Logic Preservation (Teammate Version) ---------- */

function teammate_tryExtractMailBody() {
  let body = "";
  const iframes = Array.from(document.querySelectorAll("iframe"));
  for (const f of iframes) {
    try {
      const doc = f.contentDocument;
      if (!doc || !doc.body) continue;
      const text = doc.body.innerText?.trim() || "";
      if (text.length > body.length) body = text;
    } catch {}
  }
  return body;
}

/* -------------------- One-Time Session Trigger -------------------- */

let hasOpenedOnce = false;

document.addEventListener('click', (e) => {
    if (hasOpenedOnce) return;

    const isMailItem = e.target.closest('tr[id^="rcmrow"]') || 
                        e.target.closest('.message-list-item') ||
                        e.target.closest('.messagelist tr');

    if (isMailItem) {
        console.log("IITB Assistant: First mail detected. Opening Side Panel...");
        hasOpenedOnce = true; 
        chrome.runtime.sendMessage({ type: "OPEN_SIDEBAR" });
    }
});