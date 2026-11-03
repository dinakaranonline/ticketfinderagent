from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from ticketfinder.config import Settings, find_env_file
from ticketfinder.models import DiffReport
from ticketfinder.notifiers.templates import render_multi_whatsapp_message


def _get_bot_username(bot_token: str) -> Optional[str]:
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TicketFinderAgent/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            username = (data.get("result") or {}).get("username")
            if username:
                return str(username)
    except Exception:
        pass
    return None


def _discover_chat_id(bot_token: str) -> Optional[str]:
    """
    Auto-discovers the user's Telegram chat_id from the latest message sent to the bot
    via getUpdates, and persists it into config.env if found.
    """
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TicketFinderAgent/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            results = data.get("result") or []
            for item in reversed(results):
                msg = item.get("message") or item.get("channel_post") or {}
                chat = msg.get("chat") or {}
                cid = chat.get("id")
                if cid is not None:
                    chat_id_str = str(cid)
                    _persist_chat_id_to_env(chat_id_str)
                    return chat_id_str
    except Exception:
        pass
    return None


def _persist_chat_id_to_env(chat_id: str) -> None:
    try:
        env_path = find_env_file()
        if not env_path.exists():
            return
        text = env_path.read_text(encoding="utf-8")
        if "TELEGRAM_CHAT_ID=" in text:
            lines = []
            for line in text.splitlines():
                if line.strip().startswith("TELEGRAM_CHAT_ID="):
                    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                else:
                    lines.append(line)
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _split_telegram_chunks(text: str, max_len: int = 3900) -> list[str]:
    """Splits long messages on paragraph boundaries so each chunk fits Telegram's 4096-char limit."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(block) <= max_len:
                current = block
            else:
                for i in range(0, len(block), max_len):
                    chunks.append(block[i : i + max_len])
                current = ""
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]


def send_telegram_notification(
    report: DiffReport | list[DiffReport],
    settings: Settings,
    is_daemon_running: bool = False,
    is_heartbeat: bool = False,
) -> tuple[bool, str]:
    """
    Sends a formatted notification via the official Telegram Bot API (HTTPS).
    Works 100% in the background even when the Mac screen is locked.
    """
    token = settings.telegram_bot_token.strip()
    if not token:
        return False, "Telegram not configured in config.env (TELEGRAM_BOT_TOKEN)"

    chat_id = settings.telegram_chat_id.strip()
    if not chat_id:
        discovered = _discover_chat_id(token)
        if not discovered:
            bot_user = _get_bot_username(token)
            link_hint = f" (https://t.me/{bot_user})" if bot_user else ""
            return (
                False,
                f"TELEGRAM_CHAT_ID not found yet — open your bot{link_hint} in Telegram and tap Start / send 'hi' once!",
            )
        chat_id = discovered
        settings.telegram_chat_id = chat_id

    reports_list = [report] if isinstance(report, DiffReport) else list(report)
    message = render_multi_whatsapp_message(
        reports_list, is_daemon_running=is_daemon_running, is_heartbeat=is_heartbeat
    )

    chunks = _split_telegram_chunks(message, max_len=3900)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        for idx, chunk in enumerate(chunks, 1):
            prefix = f"[Part {idx}/{len(chunks)}]\n" if len(chunks) > 1 else ""
            payload = urllib.parse.urlencode(
                {
                    "chat_id": chat_id,
                    "text": prefix + chunk,
                    "disable_web_page_preview": "true",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "TicketFinderAgent/0.1",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if not (200 <= resp.status < 300):
                    return False, f"Telegram API HTTP {resp.status}"
        part_note = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
        return True, f"Sent Telegram alert to chat {chat_id}{part_note}"
    except Exception as exc:
        return False, f"Telegram API error: {exc}"
