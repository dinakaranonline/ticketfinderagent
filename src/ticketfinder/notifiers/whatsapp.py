from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request

from ticketfinder.config import Settings
from ticketfinder.models import DiffReport
from ticketfinder.notifiers.templates import (
    render_multi_whatsapp_message,
    render_whatsapp_message,
)


def send_whatsapp_notification(
    report: DiffReport | list[DiffReport],
    settings: Settings,
    is_daemon_running: bool = False,
    is_heartbeat: bool = False,
) -> tuple[bool, str]:
    """
    Sends a formatted WhatsApp notification via the configured provider
    (CallMeBot, macOS WhatsApp.app, Twilio WhatsApp API, or Meta WhatsApp Cloud API).
    Supports both a single DiffReport and a multi-date list[DiffReport].
    """
    if not settings.whatsapp_configured:
        return (
            False,
            f"WhatsApp ({settings.whatsapp_provider}) not configured in .env",
        )

    reports_list = [report] if isinstance(report, DiffReport) else list(report)
    message = render_multi_whatsapp_message(
        reports_list, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )
    provider = settings.whatsapp_provider.lower()
    phone = settings.whatsapp_phone.strip()

    try:
        import re as _re
        import subprocess as _sp
        import time as _time
        from pathlib import Path as _Path

        digits = _re.sub(r"[^0-9]", "", phone)
        if len(digits) == 10:
            digits = f"91{digits}"

        if provider in ("desktop", "mac", "local") or (
            provider == "callmebot"
            and not settings.callmebot_api_key.strip()
            and _Path("/Applications/WhatsApp.app").exists()
        ):
            encoded_text = urllib.parse.quote(message, safe="")
            deep_link = f"whatsapp://send?phone={digits}&text={encoded_text}"
            # 1. Bring WhatsApp.app to the foreground first so the URL handler focuses the chat input
            _sp.run(["/usr/bin/open", "-a", "WhatsApp"], check=False)
            _time.sleep(1.2)
            # 2. Trigger the whatsapp://send deep link to populate the chat composer
            _sp.run(["/usr/bin/open", deep_link], check=False)
            _time.sleep(2.5)
            # 3. Press Return (key code 36) in WhatsApp to send the pre-filled message
            apple_script = (
                'tell application "WhatsApp" to activate\n'
                'delay 0.5\n'
                'tell application "System Events"\n'
                '    tell process "WhatsApp"\n'
                '        set frontmost to true\n'
                '        delay 0.3\n'
                '        key code 36\n'
                '    end tell\n'
                'end tell'
            )
            res = _sp.run(
                ["/usr/bin/osascript", "-e", apple_script],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return True, f"Sent WhatsApp via macOS WhatsApp.app to +{digits}"
            err_detail = (res.stderr or "").strip()
            return (
                True,
                f"Opened WhatsApp.app chat for +{digits} with message pre-filled ({err_detail or 'press Enter to send'})",
            )

        if provider == "callmebot":
            clean_phone = f"+{digits}" if digits else phone.replace("whatsapp:", "").strip()
            params = urllib.parse.urlencode(
                {
                    "phone": clean_phone,
                    "text": message,
                    "apikey": settings.callmebot_api_key.strip(),
                }
            )
            url = f"https://api.callmebot.com/whatsapp.php?{params}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "TicketFinderAgent/0.1"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                if "APIKey is invalid" in body or "Error" in body:
                    return False, f"CallMeBot rejected request: {body.strip()[:120]}"
                if 200 <= resp.status < 300:
                    return True, f"Sent WhatsApp via CallMeBot to {clean_phone}"
                return False, f"CallMeBot HTTP {resp.status}"

        elif provider == "twilio":
            to_phone = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"
            from_phone = settings.twilio_whatsapp_from
            if not from_phone.startswith("whatsapp:"):
                from_phone = f"whatsapp:{from_phone}"

            url = (
                f"https://api.twilio.com/2010-04-01/Accounts/"
                f"{settings.twilio_account_sid}/Messages.json"
            )
            data = urllib.parse.urlencode(
                {"From": from_phone, "To": to_phone, "Body": message}
            ).encode("utf-8")
            creds = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode(
                "utf-8"
            )
            auth_header = "Basic " + base64.b64encode(creds).decode("ascii")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 300:
                    return True, f"Sent WhatsApp via Twilio to {to_phone}"
                return False, f"Twilio HTTP {resp.status}"

        elif provider == "meta":
            clean_phone = phone.replace("whatsapp:", "").replace("+", "").strip()
            url = (
                f"https://graph.facebook.com/v19.0/"
                f"{settings.meta_phone_number_id}/messages"
            )
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "text",
                "text": {"preview_url": True, "body": message},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {settings.meta_access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 300:
                    return True, f"Sent WhatsApp via Meta Cloud API to {clean_phone}"
                return False, f"Meta API HTTP {resp.status}"

        return False, f"Unknown WhatsApp provider: {provider}"
    except Exception as exc:
        return False, f"WhatsApp ({provider}) error: {exc}"
