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
    """Connects to IMAP, fetches today's emails, and summarizes with Gemini 2.5 Flash."""
    print(f"[API] daily_summary_api called at {date.today()}")
    
    try:
        # 1. IMAP Setup - USERNAME WITHOUT DOMAIN
        USER = request.headers.get('X-LDAP-User')
        PASS = request.headers.get('X-LDAP-Pass')
    
        if not USER or not PASS:
            return JsonResponse({"status": "error", "message": "LDAP credentials missing from request headers"}, status=401)
        print(f"[IMAP] Connecting as user: {USER}")
        
        # SSL context that works with IITB
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('DEFAULT')
        
        # Connect
        mail = imaplib.IMAP4_SSL(
            host='imap.iitb.ac.in',
            port=993,
            ssl_context=context
        )
        
        mail.login(USER, PASS)
        mail.select('inbox')
        
        # Search for today's emails
        today = date.today().strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{today}")')
        
        bodies = []
        if status == 'OK':
            email_ids = messages[0].split()
            print(f"[IMAP] Found {len(email_ids)} emails today")
            
            for i, email_id in enumerate(email_ids[:5]):  # Limit to 5
                print(f"[IMAP] Fetching email {i+1}/{min(5, len(email_ids))}")
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status == 'OK':
                    msg = email.message_from_bytes(msg_data[0][1])
                    
                    # Extract plain text body
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
                            # Clean up the body
                            cleaned_body = decoded_body.replace('\r', '').replace('\n', ' ').strip()
                            bodies.append(cleaned_body[:3000])  # Limit to 3000 chars
                        except:
                            pass
        
        mail.logout()
        
        print(f"[IMAP] Extracted {len(bodies)} email bodies")
        
        if not bodies:
            return JsonResponse({
                "status": "success", 
                "summary": "No emails found for today."
            })
        
        # 2. Gemini Summarization - USING gemini-2.5-flash
        if not GEMINI_AVAILABLE:
            print("[Gemini] Gemini not available, using fallback")
            # Fallback mock response
            return JsonResponse({
                "status": "success",
                "summary": f"DEBUG: Found {len(bodies)} emails. Gemini not configured.\n\nFirst email preview:\n{bodies[0][:500]}..."
            })
        
        combined_text = "\n\n---\n\n".join(bodies)
        
        # Truncate if too long (Gemini has token limits)
        if len(combined_text) > 15000:
            combined_text = combined_text[:15000] + "\n\n[Content truncated due to length]"
        
        prompt = f"""You are summarizing emails for an IIT Bombay student. 
        Create a concise daily brief from these emails (max 250 words).
        
        Focus on:
        - Important announcements
        - Deadlines and due dates  
        - Meetings or events
        - Action items requiring attention
        - Academic or administrative updates
        
        Emails from today:
        {combined_text}
        
        Provide the summary in clear, bullet-point format if appropriate."""
        
        try:
            # Try old API first (simpler)
            if not GEMINI_NEW_API:
                print("[Gemini] Using old API (google.generativeai)")
                # Old API
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel('gemini-2.5-flash')  # Your chosen model
                response = model.generate_content(prompt)
                summary = response.text
            else:
                print("[Gemini] Using new API (google.genai)")
                # New API
                client = genai_new.Client(api_key=settings.GEMINI_API_KEY)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                
                # Extract text
                if hasattr(response, 'text'):
                    summary = response.text
                elif hasattr(response, 'candidates') and response.candidates:
                    summary = ""
                    for candidate in response.candidates:
                        if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                            for part in candidate.content.parts:
                                if hasattr(part, 'text'):
                                    summary += part.text + "\n"
                    summary = summary.strip()
                else:
                    summary = str(response)
            
            print(f"[Gemini] Summary generated: {len(summary)} chars")
            
            return JsonResponse({
                "status": "success", 
                "summary": summary
            })
            
        except Exception as gemini_error:
            print(f"[Gemini] Error: {gemini_error}")
            # Fallback to simple concatenation
            simple_summary = f"Daily Email Summary ({len(bodies)} emails)\n\n"
            for i, body in enumerate(bodies[:3]):
                simple_summary += f"Email {i+1}: {body[:200]}...\n\n"
            
            return JsonResponse({
                "status": "success",
                "summary": simple_summary
            })
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Full error: {error_trace}")
        
        # Provide a more user-friendly error
        error_msg = str(e)
        if "authentication failed" in error_msg.lower():
            error_msg = "IMAP authentication failed. Check username/password."
        elif "ssl" in error_msg.lower():
            error_msg = "SSL connection error. Server may be unreachable."
        
        return JsonResponse({
            "status": "error", 
            "message": f"Server error: {error_msg}"
        }, status=500)
    
