from datetime import date
from email.message import EmailMessage

from django.http import QueryDict
from django.test import SimpleTestCase

from .views import (
    build_email_record,
    build_model_input,
    extract_best_body,
    extract_json_object,
    fallback_overview,
    get_safe_fetch_query,
    parse_requested_summary_date,
    resolve_proxy_request,
)


class DailySummaryHelpersTests(SimpleTestCase):
    def test_extract_best_body_prefers_plain_text(self):
        message = EmailMessage()
        message["Subject"] = "Assignment reminder"
        message["From"] = "Course Team <course@iitb.ac.in>"
        message.set_content("Submit the assignment by 5 PM tomorrow.")
        message.add_alternative(
            "<html><body><p>HTML version</p></body></html>",
            subtype="html",
        )
        message.add_attachment(
            b"ignore attachment",
            maintype="application",
            subtype="octet-stream",
            filename="notes.bin",
        )

        self.assertEqual(
            extract_best_body(message),
            "Submit the assignment by 5 PM tomorrow.",
        )

    def test_build_email_record_falls_back_to_html_and_marks_external(self):
        message = EmailMessage()
        message["Subject"] = "Interview Invitation"
        message["From"] = "Recruiter <jobs@example.com>"
        message["Date"] = "Thu, 02 Apr 2026 09:15:00 +0530"
        message.set_content(
            "<html><body><p>Your interview is scheduled for Friday.</p></body></html>",
            subtype="html",
        )

        record = build_email_record(message, is_unread=True)

        self.assertEqual(record["source"], "external")
        self.assertEqual(record["status"], "unread")
        self.assertIn("Your interview is scheduled for Friday.", record["snippet"])

    def test_build_model_input_keeps_each_email_visible(self):
        records = [
            {
                "subject": "Subject A",
                "sender": "Sender A",
                "sender_email": "a@iitb.ac.in",
                "status": "unread",
                "source": "internal",
                "received_label": "02 Apr 09:00",
                "snippet": "Alpha",
                "body_excerpt": "Alpha details",
            },
            {
                "subject": "Subject B",
                "sender": "Sender B",
                "sender_email": "b@example.com",
                "status": "read",
                "source": "external",
                "received_label": "02 Apr 10:00",
                "snippet": "Beta",
                "body_excerpt": "Beta details",
            },
        ]

        corpus = build_model_input(records, char_limit=1200)

        self.assertIn("Subject: Subject A", corpus)
        self.assertIn("Subject: Subject B", corpus)
        self.assertIn("Source: EXTERNAL", corpus)

    def test_extract_json_object_handles_markdown_wrapped_json(self):
        payload = """
        ```json
        {
          "overview": "2 emails today",
          "action_items": []
        }
        ```
        """

        parsed = extract_json_object(payload)

        self.assertEqual(parsed["overview"], "2 emails today")
        self.assertEqual(parsed["action_items"], [])

    def test_parse_requested_summary_date_accepts_iso_date(self):
        request = type("Request", (), {"GET": QueryDict("date=2026-04-01")})()

        parsed_date, error = parse_requested_summary_date(request)

        self.assertEqual(parsed_date, date(2026, 4, 1))
        self.assertIsNone(error)

    def test_parse_requested_summary_date_rejects_invalid_date(self):
        request = type("Request", (), {"GET": QueryDict("date=04-01-2026")})()

        parsed_date, error = parse_requested_summary_date(request)

        self.assertIsNone(parsed_date)
        self.assertEqual(error, "Invalid date format. Use YYYY-MM-DD.")

    def test_fallback_overview_includes_selected_date(self):
        overview = fallback_overview(
            {"total": 3, "unread": 1, "read": 2, "external_total": 1},
            date(2026, 4, 1),
        )

        self.assertIn("2026-04-01", overview)

    def test_get_safe_fetch_query_uses_body_peek(self):
        self.assertEqual(get_safe_fetch_query(), "(BODY.PEEK[])")

    def test_resolve_proxy_request_uses_supported_model(self):
        model, payload = resolve_proxy_request(
            {
                "model": "gemini-2.5-pro",
                "payload": {"contents": [{"parts": [{"text": "Hello"}]}]},
            }
        )

        self.assertEqual(model, "gemini-2.5-pro")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "Hello")

    def test_resolve_proxy_request_falls_back_for_unknown_model(self):
        model, payload = resolve_proxy_request(
            {
                "model": "unknown-model",
                "payload": {"contents": [{"parts": [{"text": "Hello"}]}]},
            }
        )

        self.assertEqual(model, "gemini-2.5-flash")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "Hello")
