# Mail IITB

Mail IITB is a Chrome extension for IIT Bombay webmail that adds an AI-assisted side panel to the inbox experience. It helps users summarize emails, draft replies, compose new emails, detect calendar events from email content, sync events to Google Calendar, and generate a date-based inbox summary using IITB mail credentials.

## What It Does

- Summarizes the currently open email in brief or detailed mode
- Extracts event information from email content and helps add it to Google Calendar
- Drafts AI replies based on the selected email and optional user instructions
- Generates a new email draft from a short user prompt
- Produces a Daily Summary for a selected date by reading that day's IITB inbox mail
- Supports direct Gemini usage with a user-provided API key or a shared backend Gemini proxy

## Project Structure

- `extension/`
  Chrome extension source code including the side panel UI, content script, background service worker, styles, and assets
- `backend/`
  Django backend used for Daily Summary and the shared Gemini proxy

## Main Components

### Extension

- `extension/manifest.json`
  Declares the MV3 extension, permissions, side panel, OAuth scopes, and content script registration
- `extension/content.js`
  Extracts mail content from IITB webmail and injects drafted reply or compose text back into the webmail UI
- `extension/background.js`
  Handles Gemini requests, backend proxy calls, Google Calendar OAuth, and calendar event creation or update
- `extension/popup.html`
  Defines the side panel layout
- `extension/popup.js`
  Implements the main UI behavior and feature orchestration
- `extension/popup.css`
  Styles the extension interface

### Backend

- `backend/api/views.py`
  Contains the Gemini proxy and Daily Summary logic
- `backend/api/urls.py`
  Exposes backend endpoints used by the extension
- `backend/mail_backend/settings.py`
  Holds Django configuration and production environment-driven limits for the Gemini proxy

## Core Features

### Email Summary

The extension reads the currently opened email from IITB webmail and generates an AI summary inside the side panel.

### Calendar Extraction

The extension can inspect an email for event details and help the user create a Google Calendar event. Users can also create events manually inside the extension.

### AI Reply

The extension drafts a reply using the current email context and optional user instructions.

### AI Compose

The extension generates a fresh email draft from an intent or prompt.

### Daily Summary

The extension can generate a summary for a selected date by connecting to IITB mail through IMAP in read-only mode, fetching inbox emails for that day, classifying them, and summarizing read and unread groups separately.

## Privacy And Safety

This project handles sensitive information and should be used with care.

### Data The Extension May Access

- Email bodies from IITB webmail when the user asks the extension to summarize, reply, compose from context, or detect events
- IITB mail credentials entered for the Daily Summary feature
- A user-provided Gemini API key, if the user chooses to add one
- Temporary Google Calendar access tokens obtained through Google OAuth

### Local Storage

The extension stores some values in `chrome.storage.local` on the user's device:

- IITB mail username and token for Daily Summary
- User-provided Gemini API key
- Temporary Google Calendar session token
- Extension preferences such as selected model

These values remain on the device until the user clears them, the extension removes them, or the extension is uninstalled.

### AI Processing

When a user triggers an AI feature, relevant email content or prompt text may be sent to one of the following:

- Google Gemini directly, if the user has configured a personal Gemini API key
- The project backend Gemini proxy, which forwards the request to Gemini using a shared backend API key

Daily Summary also uses AI summarization on backend-processed inbox content for the selected date.

### Backend Handling

The backend:

- Accepts Daily Summary requests with IITB mail credentials sent from the extension
- Connects to IITB IMAP over verified TLS with a compatibility fallback for legacy server handshakes
- Reads inbox messages in read-only mode so unread status is not intentionally changed
- Does not use the Django database as a product datastore for email content

### Deletion And Revocation

Users can reduce retained local data by:

- Clearing saved IITB mail credentials from the extension
- Clearing a saved Gemini API key from the extension
- Switching Google accounts or waiting for the cached calendar token to expire
- Uninstalling the extension

If Google Calendar access was previously granted, users can also revoke the app from their Google account permissions page.

## Security Notes

- The shared Gemini proxy is protected by extension-origin checks, payload limits, and rate limiting, but it is not equivalent to full user authentication
- Sensitive values should be provided through environment variables in production
- The backend should be deployed with production-safe Cloud Run settings and monitored for abuse
- Institutional approval may be appropriate before broadly distributing a tool that accepts IITB mail access credentials from users

## Running The Backend Locally

From the `backend/` directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GEMINI_API_KEY="your_key"
python manage.py runserver
```

## Packaging The Extension

Load `extension/` as an unpacked Chrome extension during development. For release, package the same extension code that corresponds to the pinned manifest key so the production extension ID remains stable.

## Environment Variables

Important backend environment variables include:

- `SECRET_KEY`
- `DJANGO_DEBUG`
- `GEMINI_API_KEY`
- `ALLOWED_EXTENSION_IDS`
- `GEMINI_PROXY_MAX_REQUESTS_PER_MINUTE`
- `GEMINI_PROXY_RATE_LIMIT_WINDOW_SECONDS`
- `GEMINI_PROXY_MAX_REQUEST_BODY_BYTES`
- `GEMINI_PROXY_MAX_TEXT_CHARS`
- `GEMINI_PROXY_MAX_OUTPUT_TOKENS`

## Limitations

- Daily Summary depends on IITB IMAP availability and server TLS compatibility
- AI output quality depends on the selected Gemini model and the structure of the source email
- The shared Gemini proxy has abuse protections, but it is still a shared backend resource
- Changes in IITB webmail DOM structure may require extension updates

## Intended Use

This project is meant to improve productivity inside IITB webmail while keeping users informed about what data is accessed, where it is processed, and what is stored locally.
