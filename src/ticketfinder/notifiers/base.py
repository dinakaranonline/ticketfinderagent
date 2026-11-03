from __future__ import annotations

import subprocess
from typing import Optional

from ticketfinder.config import Settings
from ticketfinder.models import DiffReport
from ticketfinder.notifiers.gmail import send_gmail_notification
from ticketfinder.notifiers.telegram import send_telegram_notification
from ticketfinder.notifiers.templates import (
    render_email_content,
    render_multi_email_content,
    render_multi_whatsapp_message,
    render_whatsapp_message,
)
from ticketfinder.notifiers.whatsapp import send_whatsapp_notification


def send_macos_desktop_banner(
    reports: DiffReport | list[DiffReport], is_heartbeat: bool = False
) -> None:
    """Fires a native macOS Notification Center banner as a local bonus alert."""
    r_list = [reports] if isinstance(reports, DiffReport) else list(reports)
    if not r_list:
        return
    job = r_list[0].job
    dates_str = ", ".join(r.job.target_date for r in r_list)
    any_open = any(r.any_tickets_open for r in r_list)
    if any_open:
        title = f"🎟️ Tickets Open: {job.movie}"
        new_c = sum(len(r.newly_opened_cinemas) for r in r_list)
        subtitle = f"{new_c} new cinema(s) on {dates_str} in {job.city}"
    elif is_heartbeat:
        title = f"💓 Heartbeat: {job.movie}"
        subtitle = f"Batch job active — checked {dates_str}"
    else:
        title = f"🤖 Watching: {job.movie}"
        subtitle = f"No tickets open yet for {dates_str}"

    script = (
        f'display notification "{subtitle.replace(chr(34), "")}" '
        f'with title "{title.replace(chr(34), "")}" sound name "Glass"'
    )
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass


def dispatch_notifications(
    report: DiffReport | list[DiffReport],
    settings: Optional[Settings] = None,
    is_daemon_running: bool = False,
    print_preview: bool = True,
    dry_run: bool = False,
    is_heartbeat: bool = False,
) -> dict[str, tuple[bool, str]]:
    """
    Dispatches a single consolidated notification across Gmail SMTP and WhatsApp
    for one or more target dates of the same movie.
    If dry_run=True, prints the CLI preview without sending any emails or WhatsApp messages.
    """
    reports_list = [report] if isinstance(report, DiffReport) else list(report)
    if not reports_list:
        return {}

    cfg = settings or Settings.from_env()
    subject, email_text, _ = render_multi_email_content(
        reports_list, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )
    wa_text = render_multi_whatsapp_message(
        reports_list, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )

    if print_preview:
        print("\n" + email_text)
        print("\n📱 Telegram / Mobile Message Preview:")
        print("──────────────────────────────────────────────────────────────────────")
        print(wa_text)
        print("──────────────────────────────────────────────────────────────────────")

    if dry_run:
        if print_preview:
            print("\n📡 Notification Delivery Status: [CLI-ONLY / DRY-RUN — No notifications sent]\n")
        return {
            "gmail": (False, "Skipped (--cli-only / --dry-run)"),
            "telegram": (False, "Skipped (--cli-only / --dry-run)"),
            "whatsapp": (False, "Skipped (--cli-only / --dry-run)"),
        }

    send_macos_desktop_banner(reports_list, is_heartbeat=is_heartbeat)

    results: dict[str, tuple[bool, str]] = {}
    gmail_ok, gmail_msg = send_gmail_notification(
        reports_list, cfg, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )
    results["gmail"] = (gmail_ok, gmail_msg)

    wa_ok = False
    wa_msg = "Disabled in config.env"
    if cfg.whatsapp_configured:
        wa_ok, wa_msg = send_whatsapp_notification(
            reports_list, cfg, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
        )
    results["whatsapp"] = (wa_ok, wa_msg)

    tg_ok = False
    tg_msg = ""
    if cfg.telegram_configured:
        tg_ok, tg_msg = send_telegram_notification(
            reports_list, cfg, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
        )
        results["telegram"] = (tg_ok, tg_msg)

    if print_preview:
        print("\n📡 Notification Delivery Status:")
        print(f"  • Gmail    : {'✅' if gmail_ok else '⚠️ '} {gmail_msg}")
        if cfg.telegram_configured:
            print(f"  • Telegram : {'✅' if tg_ok else '⚠️ '} {tg_msg}")
        if cfg.whatsapp_configured:
            print(f"  • WhatsApp : {'✅' if wa_ok else '⚠️ '} {wa_msg}")
        else:
            print("  • WhatsApp : 🛑 Disabled")
        print()

    return results
