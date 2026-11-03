from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ticketfinder.config import Settings
from ticketfinder.models import DiffReport
from ticketfinder.notifiers.templates import render_email_content, render_multi_email_content


def send_gmail_notification(
    report: DiffReport | list[DiffReport],
    settings: Settings,
    is_daemon_running: bool = False,
    is_heartbeat: bool = False,
) -> tuple[bool, str]:
    """
    Sends a multipart (plain-text + HTML) email notification via Gmail SMTP.
    Supports both a single DiffReport and a multi-date list[DiffReport].
    """
    if not settings.gmail_configured:
        return False, "Gmail not configured in .env (GMAIL_SENDER / GMAIL_APP_PASSWORD)"

    reports_list = [report] if isinstance(report, DiffReport) else list(report)
    subject, text_body, html_body = render_multi_email_content(
        reports_list, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"TicketFinder Agent <{settings.gmail_sender}>"
    msg["To"] = settings.gmail_recipient or settings.gmail_sender
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    clean_password = settings.gmail_app_password.replace(" ", "")
    context = ssl.create_default_context()

    last_err: Exception | None = None
    # Try Port 465 (Direct SSL/TLS) first, then Port 587 (STARTTLS)
    for mode in ("ssl_465", "starttls_587"):
        try:
            if mode == "ssl_465":
                with smtplib.SMTP_SSL(
                    "smtp.gmail.com", 465, context=context, timeout=12
                ) as server:
                    server.login(settings.gmail_sender, clean_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=12) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(settings.gmail_sender, clean_password)
                    server.send_message(msg)
            return True, f"Sent Gmail to {msg['To']}"
        except smtplib.SMTPAuthenticationError as auth_err:
            err_text = str(auth_err)
            if "534" in err_text or "535" in err_text or "BadCredentials" in err_text or "Application-specific" in err_text:
                return (
                    False,
                    "Gmail rejected regular account password (requires a 16-letter Google App Password from https://myaccount.google.com/apppasswords )",
                )
            return False, f"Gmail authentication error: {auth_err}"
        except Exception as exc:
            last_err = exc
            continue

    return False, f"Gmail SMTP error: {last_err}"
