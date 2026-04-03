import email
import imaplib
import json
import re
import ssl
from datetime import date, timedelta
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# Import check - but NO execution at module level
GEMINI_AVAILABLE = False
GEMINI_NEW_API = False
DEFAULT_PROXY_MODEL = "gemini-2.5-flash"
SUPPORTED_PROXY_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
}

# Silent import check
try:
    import google.generativeai as genai

    GEMINI_AVAILABLE = True
except ImportError:
    try:
        import google.genai as genai_new

        GEMINI_AVAILABLE = True
        GEMINI_NEW_API = True
    except ImportError:
        pass


class HTMLTextExtractor(HTMLParser):
    """Converts HTML email bodies into readable text without extra dependencies."""

    BLOCK_TAGS = {
        "br",
        "div",
        "p",
        "li",
        "tr",
        "table",
        "section",
        "article",
        "header",
        "footer",
    }
    SKIP_TAGS = {"script", "style", "head", "title", "meta", "noscript"}

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self):
        return "".join(self._parts)


def decode_mime_header(value):
    if not value:
        return ""

    decoded = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(encoding or "utf-8", errors="ignore"))
            except LookupError:
                decoded.append(part.decode("utf-8", errors="ignore"))
        else:
            decoded.append(part)
    return "".join(decoded).strip()


def decode_part_payload(part):
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="ignore")
        except LookupError:
            return payload.decode("utf-8", errors="ignore")

    raw_payload = part.get_payload()
    if isinstance(raw_payload, str):
        return raw_payload
    return ""


def clean_extracted_text(text):
    if not text:
        return ""

    text = unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def html_to_text(html):
    if not html:
        return ""

    parser = HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return clean_extracted_text(parser.get_text())


def build_snippet(text, limit=320):
    cleaned = clean_extracted_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 3, 0)].rstrip() + "..."


def clamp(value, low, high):
    return max(low, min(high, value))


def extract_best_body(message):
    plain_candidates = []
    html_candidates = []

    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_type = part.get_content_type()
        disposition = (part.get("Content-Disposition") or "").lower()

        if part.get_filename() or "attachment" in disposition:
            continue

        payload = decode_part_payload(part)
        if not payload:
            continue

        if content_type == "text/plain":
            plain_candidates.append(clean_extracted_text(payload))
        elif content_type == "text/html":
            html_candidates.append(html_to_text(payload))

    plain_candidates = [candidate for candidate in plain_candidates if candidate]
    if plain_candidates:
        return max(plain_candidates, key=len)

    html_candidates = [candidate for candidate in html_candidates if candidate]
    if html_candidates:
        return max(html_candidates, key=len)

    return ""


def classify_sender(sender_email):
    domain = sender_email.split("@")[-1].lower() if sender_email else ""
    return "internal" if domain.endswith("iitb.ac.in") else "external"


def format_received_label(raw_value):
    if not raw_value:
        return "Today"

    try:
        dt = parsedate_to_datetime(raw_value)
        return dt.strftime("%d %b %H:%M")
    except Exception:
        return raw_value[:32]


def build_email_record(message, is_unread):
    raw_from = decode_mime_header(message.get("From", ""))
    sender_name, sender_email = parseaddr(raw_from)
    sender_name = sender_name.strip() or sender_email or "Unknown sender"
    sender_email = sender_email.strip()

    subject = decode_mime_header(message.get("Subject", "")) or "(No subject)"
    body = extract_best_body(message)
    fallback_text = f"Subject: {subject}. From: {sender_name}."
    snippet_source = body or fallback_text

    return {
        "subject": subject,
        "sender": sender_name,
        "sender_email": sender_email,
        "status": "unread" if is_unread else "read",
        "source": classify_sender(sender_email),
        "received_label": format_received_label(message.get("Date", "")),
        "snippet": build_snippet(snippet_source, 320),
        "body_excerpt": build_snippet(snippet_source, 900),
    }


def build_model_input(email_records, char_limit=26000):
    if not email_records:
        return ""

    per_email_limit = clamp((char_limit // len(email_records)) - 120, 180, 900)
    sections = []

    for index, record in enumerate(email_records, start=1):
        sections.append(
            "\n".join(
                [
                    f"EMAIL {index}",
                    f"Status: {record['status'].upper()}",
                    f"Source: {record['source'].upper()}",
                    f"From: {record['sender']}" + (
                        f" <{record['sender_email']}>" if record["sender_email"] else ""
                    ),
                    f"Subject: {record['subject']}",
                    f"Received: {record['received_label']}",
                    f"Content: {build_snippet(record['body_excerpt'] or record['snippet'], per_email_limit)}",
                ]
            )
        )

    return "\n\n---\n\n".join(sections)


def generate_text(prompt):
    if not GEMINI_AVAILABLE:
        return ""

    if not GEMINI_NEW_API:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        return getattr(response, "text", "") or ""

    client = genai_new.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text if hasattr(response, "text") else str(response)


def summarize_email_group(email_records, category_name, summary_date):
    if not email_records:
        return f"No {category_name.lower()} emails on {summary_date.isoformat()}."

    prompt = f"""
You are creating a structured daily inbox digest for an IIT Bombay student.

Summarize the following {category_name.lower()} emails from {summary_date.isoformat()}.

Output rules:
- Start with the exact heading: {category_name} EMAILS
- Group related emails under bold headings using Markdown syntax like **Heading**
- Under each heading, add 2-4 bullet points
- Explicitly mention deadlines, required actions, and meeting times
- If an important email is from outside IIT Bombay, mention "(External)" in the heading or bullet
- Ignore boilerplate and signatures
- Maximum 220 words

Emails:
{build_model_input(email_records, char_limit=26000)}
"""

    summary = generate_text(prompt).strip()
    if summary:
        return summary

    return f"DEBUG: {len(email_records)} {category_name.lower()} emails found for {summary_date.isoformat()}."


def extract_json_object(text):
    if not text:
        return None

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def build_stats(email_records, mailbox_total):
    unread_count = sum(1 for record in email_records if record["status"] == "unread")
    read_count = len(email_records) - unread_count
    internal_records = [record for record in email_records if record["source"] == "internal"]
    external_records = [record for record in email_records if record["source"] == "external"]

    return {
        "mailbox_total": mailbox_total,
        "processed_total": len(email_records),
        "total": len(email_records),
        "unread": unread_count,
        "read": read_count,
        "internal_total": len(internal_records),
        "external_total": len(external_records),
        "internal_unread": sum(1 for record in internal_records if record["status"] == "unread"),
        "external_unread": sum(1 for record in external_records if record["status"] == "unread"),
    }


def parse_requested_summary_date(request):
    raw_value = (request.GET.get("date") or "").strip()
    if not raw_value:
        return date.today(), None

    try:
        return date.fromisoformat(raw_value), None
    except ValueError:
        return None, "Invalid date format. Use YYYY-MM-DD."


def get_safe_fetch_query():
    """Return a non-mutating IMAP fetch query so unread messages stay unread."""
    return "(BODY.PEEK[])"


def resolve_proxy_request(data):
    if not isinstance(data, dict):
        return DEFAULT_PROXY_MODEL, {}

    model = data.get("model")
    if model not in SUPPORTED_PROXY_MODELS:
        model = DEFAULT_PROXY_MODEL

    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    return model, payload


def build_secure_imap_ssl_context(compatibility_mode=False):
    """Use verified TLS for IMAP, with an optional legacy-compatible fallback."""
    context = ssl.create_default_context()

    if compatibility_mode:
        # Some legacy IMAP servers fail modern handshakes unless the OpenSSL
        # security level and minimum TLS version are relaxed. Certificate
        # verification stays enabled.
        try:
            context.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:
            pass

        tls_version = getattr(ssl, "TLSVersion", None)
        if tls_version is not None and hasattr(tls_version, "TLSv1"):
            try:
                context.minimum_version = tls_version.TLSv1
            except ValueError:
                pass

    return context


def should_retry_imap_with_compatibility(exc):
    message = str(exc).lower()
    retry_markers = (
        "handshake failure",
        "no shared cipher",
        "wrong version number",
        "version too low",
        "tlsv1 alert protocol version",
    )
    return any(marker in message for marker in retry_markers)


def connect_to_iitb_imap():
    strict_context = build_secure_imap_ssl_context()

    try:
        return imaplib.IMAP4_SSL(
            host="imap.iitb.ac.in",
            port=993,
            ssl_context=strict_context,
        )
    except ssl.SSLCertVerificationError:
        raise
    except ssl.SSLError as exc:
        if not should_retry_imap_with_compatibility(exc):
            raise

        compatibility_context = build_secure_imap_ssl_context(compatibility_mode=True)
        return imaplib.IMAP4_SSL(
            host="imap.iitb.ac.in",
            port=993,
            ssl_context=compatibility_context,
        )


def get_client_ip(request):
    forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "unknown").strip() or "unknown"


def is_allowed_extension_request(request):
    allowed_ids = getattr(settings, "ALLOWED_EXTENSION_IDS", []) or []
    if not allowed_ids:
        return True

    header_extension_id = (request.headers.get("X-Extension-Id") or "").strip()
    origin = (request.headers.get("Origin") or "").strip()
    allowed_origins = {f"chrome-extension://{extension_id}" for extension_id in allowed_ids}
    return header_extension_id in allowed_ids or origin in allowed_origins


def is_gemini_proxy_rate_limited(request):
    limit = max(int(getattr(settings, "GEMINI_PROXY_MAX_REQUESTS_PER_MINUTE", 60)), 1)
    window_seconds = max(
        int(getattr(settings, "GEMINI_PROXY_RATE_LIMIT_WINDOW_SECONDS", 60)),
        1,
    )
    client_ip = get_client_ip(request)
    cache_key = f"gemini-proxy-rate:{client_ip}"

    if cache.add(cache_key, 1, timeout=window_seconds):
        return False

    try:
        current_count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        return False

    return current_count > limit


def sanitize_proxy_payload(payload):
    if not isinstance(payload, dict):
        return None, "Invalid payload", 400

    contents = payload.get("contents")
    if not isinstance(contents, list) or not contents:
        return None, "Payload must include contents", 400

    max_total_chars = max(int(getattr(settings, "GEMINI_PROXY_MAX_TEXT_CHARS", 25000)), 1)
    remaining_chars = max_total_chars
    sanitized_contents = []

    for content in contents[:6]:
        if not isinstance(content, dict):
            continue

        role = str(content.get("role") or "user").strip() or "user"
        raw_parts = content.get("parts")
        if not isinstance(raw_parts, list):
            continue

        sanitized_parts = []
        for part in raw_parts[:8]:
            if not isinstance(part, dict):
                continue

            text = part.get("text")
            if not isinstance(text, str):
                continue

            cleaned_text = text.strip()
            if not cleaned_text:
                continue

            if remaining_chars <= 0:
                break

            if len(cleaned_text) > remaining_chars:
                cleaned_text = cleaned_text[:remaining_chars]

            sanitized_parts.append({"text": cleaned_text})
            remaining_chars -= len(cleaned_text)

        if sanitized_parts:
            sanitized_contents.append({"role": role, "parts": sanitized_parts})

        if remaining_chars <= 0:
            break

    if not sanitized_contents:
        return None, "Payload does not contain supported text parts", 400

    sanitized_payload = {"contents": sanitized_contents}

    system_instruction = payload.get("systemInstruction")
    if isinstance(system_instruction, dict):
        sanitized_instruction, _, _ = sanitize_proxy_payload({"contents": [system_instruction]})
        if sanitized_instruction and sanitized_instruction.get("contents"):
            sanitized_payload["systemInstruction"] = sanitized_instruction["contents"][0]

    requested_generation_config = payload.get("generationConfig")
    requested_max_tokens = None
    if isinstance(requested_generation_config, dict):
        candidate_tokens = requested_generation_config.get("maxOutputTokens")
        if isinstance(candidate_tokens, int):
            requested_max_tokens = candidate_tokens

    max_output_tokens = max(int(getattr(settings, "GEMINI_PROXY_MAX_OUTPUT_TOKENS", 1536)), 256)
    sanitized_payload["generationConfig"] = {
        "maxOutputTokens": min(requested_max_tokens or max_output_tokens, max_output_tokens)
    }

    return sanitized_payload, None, 200


def fallback_overview(stats, summary_date):
    return (
        f"{stats['total']} inbox emails arrived on {summary_date.isoformat()}: {stats['unread']} unread and "
        f"{stats['read']} read. {stats['external_total']} came from outside IIT Bombay."
    )


def fallback_action_items(email_records):
    keywords = (
        "deadline",
        "due",
        "submit",
        "meeting",
        "interview",
        "register",
        "payment",
        "exam",
        "quiz",
        "assignment",
    )
    actions = []
    for record in email_records:
        haystack = f"{record['subject']} {record['snippet']}".lower()
        if any(keyword in haystack for keyword in keywords):
            actions.append(
                {
                    "title": record["subject"],
                    "detail": record["snippet"],
                    "priority": "high" if record["status"] == "unread" else "medium",
                    "status": record["status"],
                    "source": record["source"],
                }
            )
        if len(actions) >= 5:
            break
    return actions


def generate_digest_insights(email_records, stats, summary_date):
    if not email_records:
        return "", []

    prompt = f"""
You are generating an actionable inbox digest for an IIT Bombay student.

Return JSON only in this format:
{{
  "overview": "1-2 concise sentences",
  "action_items": [
    {{
      "title": "short action title",
      "detail": "what to do and any deadline/time if available",
      "priority": "high | medium | low",
      "status": "unread | read",
      "source": "internal | external"
    }}
  ]
}}

Rules:
- Include only concrete actions, deadlines, submissions, meetings, interviews, payments, registrations, or approvals
- Sort action_items by urgency
- Maximum 5 action_items
- Mention if an action comes from an external sender when relevant
- If no action exists, return an empty array

Email stats for {summary_date.isoformat()}:
- Total emails: {stats['total']}
- Unread emails: {stats['unread']}
- External emails: {stats['external_total']}

Emails:
{build_model_input(email_records, char_limit=22000)}
"""

    parsed = extract_json_object(generate_text(prompt))
    if not parsed:
        return fallback_overview(stats, summary_date), fallback_action_items(email_records)

    overview = parsed.get("overview") or fallback_overview(stats, summary_date)
    action_items = parsed.get("action_items")
    if not isinstance(action_items, list):
        action_items = fallback_action_items(email_records)

    normalized_items = []
    for item in action_items[:5]:
        if not isinstance(item, dict):
            continue
        normalized_items.append(
            {
                "title": str(item.get("title", "")).strip() or "Action item",
                "detail": str(item.get("detail", "")).strip(),
                "priority": str(item.get("priority", "medium")).strip().lower() or "medium",
                "status": str(item.get("status", "unread")).strip().lower() or "unread",
                "source": str(item.get("source", "internal")).strip().lower() or "internal",
            }
        )

    return overview, normalized_items


@csrf_exempt
def gemini_proxy(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        max_body_bytes = max(
            int(getattr(settings, "GEMINI_PROXY_MAX_REQUEST_BODY_BYTES", 50000)),
            1024,
        )
        if len(request.body or b"") > max_body_bytes:
            return JsonResponse({"error": "Request body too large"}, status=413)

        if not request.body:
            return JsonResponse({"error": "Empty request body"}, status=400)

        if not is_allowed_extension_request(request):
            return JsonResponse({"error": "Unauthorized extension client"}, status=403)

        if is_gemini_proxy_rate_limited(request):
            return JsonResponse({"error": "Rate limit exceeded. Please try again shortly."}, status=429)

        data = json.loads(request.body)
        model, payload = resolve_proxy_request(data)
        sanitized_payload, payload_error, payload_status = sanitize_proxy_payload(payload)
        if payload_error:
            return JsonResponse({"error": payload_error}, status=payload_status)

        api_key = getattr(settings, "GEMINI_API_KEY", None)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        response = requests.post(url, json=sanitized_payload, timeout=60)

        if response.status_code != 200:
            return JsonResponse(
                {
                    "error": "Google API Error",
                    "details": response.text,
                },
                status=response.status_code,
            )

        gemini_response = response.json()

        text_content = ""
        if "candidates" in gemini_response and gemini_response["candidates"]:
            candidate = gemini_response["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                for part in candidate["content"]["parts"]:
                    if "text" in part:
                        text_content += part["text"]

        return JsonResponse(
            {
                "ok": True,
                "text": text_content,
                "summary": text_content,
                "raw": gemini_response,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": f"Invalid JSON: {request.body.decode()}"}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


def daily_summary_api(request):
    """Fetches a selected inbox date, including external and HTML-only mail, and returns a richer digest."""
    requested_date, date_error = parse_requested_summary_date(request)
    if date_error:
        return JsonResponse(
            {"status": "error", "message": date_error},
            status=400,
        )

    print(f"[API] daily_summary_api called for {requested_date.isoformat()}")

    mail = None
    try:
        user = request.headers.get("X-LDAP-User")
        password = request.headers.get("X-LDAP-Pass")

        if not user or not password:
            return JsonResponse(
                {"status": "error", "message": "LDAP credentials missing"},
                status=401,
            )

        mail = connect_to_iitb_imap()

        mail.login(user, password)
        mail.select("inbox", readonly=True)

        imap_start = requested_date.strftime("%d-%b-%Y")
        imap_end = (requested_date + timedelta(days=1)).strftime("%d-%b-%Y")

        all_status, all_messages = mail.search(None, f'(SINCE "{imap_start}" BEFORE "{imap_end}")')
        unread_status, unread_messages = mail.search(
            None,
            f'(SINCE "{imap_start}" BEFORE "{imap_end}" UNSEEN)',
        )

        if all_status != "OK":
            return JsonResponse(
                {"status": "error", "message": "Failed to fetch inbox messages"},
                status=500,
            )

        all_ids = list(reversed(all_messages[0].split()))
        unread_ids = set(unread_messages[0].split()) if unread_status == "OK" else set()

        if not all_ids:
            return JsonResponse(
                {
                    "status": "success",
                    "generated_for": requested_date.isoformat(),
                    "overview": f"No inbox emails arrived on {requested_date.isoformat()}.",
                    "stats": build_stats([], 0),
                    "action_items": [],
                    "external_highlights": [],
                    "recent_emails": [],
                    "unread_summary": f"No unread emails on {requested_date.isoformat()}.",
                    "read_summary": f"No read emails on {requested_date.isoformat()}.",
                }
            )

        email_records = []
        for email_id in all_ids:
            fetch_status, msg_data = mail.fetch(email_id, get_safe_fetch_query())
            if fetch_status != "OK" or not msg_data:
                continue

            raw_message = next(
                (item[1] for item in msg_data if isinstance(item, tuple) and len(item) > 1),
                None,
            )
            if not raw_message:
                continue

            message = email.message_from_bytes(raw_message)
            email_records.append(build_email_record(message, email_id in unread_ids))

        stats = build_stats(email_records, len(all_ids))
        unread_records = [record for record in email_records if record["status"] == "unread"]
        read_records = [record for record in email_records if record["status"] == "read"]

        overview, action_items = generate_digest_insights(email_records, stats, requested_date)
        unread_summary = summarize_email_group(unread_records, "UNREAD", requested_date)
        read_summary = summarize_email_group(read_records, "READ", requested_date)

        external_highlights = sorted(
            [record for record in email_records if record["source"] == "external"],
            key=lambda record: (record["status"] != "unread",),
        )[:5]

        recent_emails = email_records

        return JsonResponse(
            {
                "status": "success",
                "generated_for": requested_date.isoformat(),
                "overview": overview,
                "stats": stats,
                "action_items": action_items,
                "external_highlights": external_highlights,
                "recent_emails": recent_emails,
                "unread_summary": unread_summary,
                "read_summary": read_summary,
            }
        )

    except ssl.SSLCertVerificationError as exc:
        return JsonResponse(
            {
                "status": "error",
                "message": (
                    "Secure connection to IITB IMAP failed certificate verification. "
                    f"Details: {exc}"
                ),
            },
            status=502,
        )
    except ssl.SSLError as exc:
        return JsonResponse(
            {
                "status": "error",
                "message": f"Secure IMAP connection failed: {exc}",
            },
            status=502,
        )
    except Exception as exc:
        return JsonResponse(
            {
                "status": "error",
                "message": str(exc),
            },
            status=500,
        )
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass
