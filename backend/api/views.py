import imaplib
import ssl
import email
from datetime import date
from django.http import JsonResponse
from django.conf import settings
import json
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

# Import check - but NO execution at module level
GEMINI_AVAILABLE = False
GEMINI_NEW_API = False

# Silent import check
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
    # Don't print here - it runs on import!
except ImportError:
    try:
        import google.genai as genai_new
        GEMINI_AVAILABLE = True
        GEMINI_NEW_API = True
    except ImportError:
        pass  # Silent fail

@csrf_exempt
def gemini_proxy(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)
    
    try:
        if not request.body:
            return JsonResponse({"error": "Empty request body"}, status=400)

        data = json.loads(request.body)
        
        api_key = getattr(settings, "GEMINI_API_KEY", None)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        
        response = requests.post(url, json=data)
        
        if response.status_code != 200:
            return JsonResponse({
                "error": "Google API Error",
                "details": response.text 
            }, status=response.status_code)

        gemini_response = response.json()
        
        # TRANSFORM THE RESPONSE to match what your extension expects
        # Extract the text from Gemini's response
        text_content = ""
        if 'candidates' in gemini_response and gemini_response['candidates']:
            candidate = gemini_response['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content']:
                for part in candidate['content']['parts']:
                    if 'text' in part:
                        text_content += part['text']
        
        # Return in the format your extension expects
        return JsonResponse({
            "ok": True,  
            "text": text_content,  
            "summary": text_content,
            "raw": gemini_response  
        })

    except json.JSONDecodeError:
        return JsonResponse({"error": f"Invalid JSON: {request.body.decode()}"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    
def daily_summary_api(request):
    """Fetches ALL today's emails and generates separate summaries for unread and read emails."""
    print(f"[API] daily_summary_api called at {date.today()}")

    try:
        USER = request.headers.get('X-LDAP-User')
        PASS = request.headers.get('X-LDAP-Pass')

        if not USER or not PASS:
            return JsonResponse(
                {"status": "error", "message": "LDAP credentials missing"},
                status=401
            )

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('DEFAULT')

        mail = imaplib.IMAP4_SSL(
            host='imap.iitb.ac.in',
            port=993,
            ssl_context=context
        )

        mail.login(USER, PASS)
        mail.select('inbox')

        today = date.today().strftime("%d-%b-%Y")

        def fetch_emails(query):
            bodies = []
            status, messages = mail.search(None, query)

            if status != 'OK':
                return bodies

            email_ids = messages[0].split()
            print(f"[IMAP] Query '{query}' → {len(email_ids)} emails")

            for email_id in email_ids:
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status != 'OK':
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                body = None
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True)
                            if body:
                                break
                else:
                    body = msg.get_payload(decode=True)

                if body:
                    try:
                        decoded_body = body.decode('utf-8', errors='ignore')
                        cleaned_body = decoded_body.replace('\r', '').replace('\n', ' ').strip()
                        bodies.append(cleaned_body[:3000])
                    except:
                        pass

            return bodies

        unread_bodies = fetch_emails(f'(SINCE "{today}" UNSEEN)')
        read_bodies = fetch_emails(f'(SINCE "{today}" SEEN)')

        mail.logout()

        if not unread_bodies and not read_bodies:
            return JsonResponse({
                "status": "success",
                "unread_summary": "No unread emails today.",
                "read_summary": "No read emails today."
            })


        def summarize_emails(email_list, category_name):
            if not email_list:
                return f"No {category_name.lower()} emails today."

            combined_text = "\n\n---\n\n".join(email_list)

            # 🔥 Safety token cap (not mail cap)
            if len(combined_text) > 20000:
                combined_text = combined_text[:20000] + "\n\n[Truncated]"

            prompt = prompt = f"""
You are generating a structured daily email brief for an IIT Bombay student.

Organize the summary clearly using this format:

SECTION TITLE:
Use exactly this header:
{category_name} EMAILS

For each important topic or event:
- Write a short descriptive heading in bold (Markdown style: **Heading**)
- Under that heading, add 2–5 bullet points explaining:
    • What it is
    • Key details
    • Deadlines (if any)
    • Required action (if any)

Rules:
- Do NOT make everything one long bullet list.
- Group related emails under one bold heading.
- Be concise but clear.
- Maximum 300 words total.

Emails:
{combined_text}
"""


            if not GEMINI_AVAILABLE:
                return f"DEBUG: {len(email_list)} {category_name} emails found."

            if not GEMINI_NEW_API:
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = model.generate_content(prompt)
                return response.text
            else:
                client = genai_new.Client(api_key=settings.GEMINI_API_KEY)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                return response.text if hasattr(response, 'text') else str(response)

        unread_summary = summarize_emails(unread_bodies, "UNREAD")
        read_summary = summarize_emails(read_bodies, "READ")

        return JsonResponse({
            "status": "success",
            "unread_summary": unread_summary,
            "read_summary": read_summary
        })

    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)
