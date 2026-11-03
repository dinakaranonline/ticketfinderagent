from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def get_app_dir() -> Path:
    """Returns project .ticketfinder directory (or TICKETFINDER_HOME if set) for DB, PID, and logs."""
    custom = os.environ.get("TICKETFINDER_HOME")
    if custom:
        path = Path(custom)
    else:
        pkg_root = Path(__file__).resolve().parents[2]
        path = pkg_root / ".ticketfinder"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_env_file() -> Path:
    """Finds config.env or .env in current working directory, project root, or app dir."""
    pkg_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path.cwd() / "config.env",
        pkg_root / "config.env",
        Path.cwd() / ".env",
        pkg_root / ".env",
        get_app_dir() / ".env",
    ]
    for c in candidates:
        if c.exists():
            return c
    return pkg_root / "config.env"


def load_dotenv_file(env_path: Optional[Path] = None) -> dict[str, str]:
    """Lightweight .env loader using Python standard library."""
    path = env_path or find_env_file()
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        loaded[key] = val
        if key not in os.environ:
            os.environ[key] = val
    return loaded


@dataclass
class Settings:
    gmail_sender: str = ""
    gmail_app_password: str = ""
    gmail_recipient: str = ""

    whatsapp_provider: str = "callmebot"  # callmebot, twilio, meta
    whatsapp_phone: str = ""
    callmebot_api_key: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    meta_phone_number_id: str = ""
    meta_access_token: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    gemini_api_key: str = ""
    default_city: str = "Chennai"
    scraper_api_key: str = ""

    @property
    def gmail_configured(self) -> bool:
        return bool(
            self.gmail_sender
            and self.gmail_app_password
            and self.gmail_sender != "your.email@gmail.com"
            and "xxxx" not in self.gmail_app_password
        )

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def whatsapp_configured(self) -> bool:
        prov = self.whatsapp_provider.lower()
        if prov in ("disabled", "none", "off", "false", ""):
            return False
        if not self.whatsapp_phone or self.whatsapp_phone == "+919876543210":
            return False
        if prov in ("desktop", "mac", "local"):
            return True
        if prov == "callmebot":
            return bool(self.callmebot_api_key) or Path("/Applications/WhatsApp.app").exists()
        if prov == "twilio":
            return bool(self.twilio_account_sid and self.twilio_auth_token)
        if prov == "meta":
            return bool(self.meta_phone_number_id and self.meta_access_token)
        return False

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> Settings:
        loaded = load_dotenv_file(env_path)

        def _val(key: str, default: str = "") -> str:
            if key in loaded:
                return loaded[key].strip()
            return os.environ.get(key, default).strip()

        sender = _val("GMAIL_SENDER")
        recipient = _val("GMAIL_RECIPIENT") or sender
        return cls(
            gmail_sender=sender,
            gmail_app_password=_val("GMAIL_APP_PASSWORD"),
            gmail_recipient=recipient,
            whatsapp_provider=_val("WHATSAPP_PROVIDER", "callmebot").lower(),
            whatsapp_phone=_val("WHATSAPP_PHONE"),
            callmebot_api_key=_val("CALLMEBOT_API_KEY"),
            twilio_account_sid=_val("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_val("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=_val("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
            meta_phone_number_id=_val("META_PHONE_NUMBER_ID"),
            meta_access_token=_val("META_ACCESS_TOKEN"),
            telegram_bot_token=_val("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_val("TELEGRAM_CHAT_ID"),
            gemini_api_key=_val("GEMINI_API_KEY"),
            default_city=_val("DEFAULT_CITY", "Chennai"),
            scraper_api_key=_val("SCRAPER_API_KEY"),
        )


def interactive_setup_env(env_path: Optional[Path] = None) -> Settings:
    """Interactive CLI wizard to configure Gmail & WhatsApp in .env if missing."""
    target = env_path or find_env_file()
    settings = Settings.from_env(target)
    print("\n⚙️  Notification Setup (.env)")
    print("──────────────────────────────────────────────────────────────")

    gmail_sender = input(f"  Gmail Address [{settings.gmail_sender or 'skip'}]: ").strip()
    if gmail_sender and gmail_sender.lower() != "skip":
        settings.gmail_sender = gmail_sender
        app_pw = input("  Gmail 16-char App Password: ").strip()
        if app_pw:
            settings.gmail_app_password = app_pw
        recip = input(f"  Recipient Email [{settings.gmail_sender}]: ").strip()
        settings.gmail_recipient = recip or settings.gmail_sender

    wa_phone = input(
        f"  WhatsApp Number with country code (e.g. +919876543210) [{settings.whatsapp_phone or 'skip'}]: "
    ).strip()
    if wa_phone and wa_phone.lower() != "skip":
        settings.whatsapp_phone = wa_phone
        prov = (
            input(
                f"  WhatsApp Provider (callmebot / twilio / meta) [{settings.whatsapp_provider}]: "
            )
            .strip()
            .lower()
            or settings.whatsapp_provider
        )
        settings.whatsapp_provider = prov
        if prov == "callmebot":
            key = input("  CallMeBot API Key: ").strip()
            if key:
                settings.callmebot_api_key = key
        elif prov == "twilio":
            sid = input("  Twilio Account SID: ").strip()
            tok = input("  Twilio Auth Token: ").strip()
            if sid:
                settings.twilio_account_sid = sid
            if tok:
                settings.twilio_auth_token = tok
        elif prov == "meta":
            pid = input("  Meta Phone Number ID: ").strip()
            tok = input("  Meta Access Token: ").strip()
            if pid:
                settings.meta_phone_number_id = pid
            if tok:
                settings.meta_access_token = tok

    lines = [
        f"GMAIL_SENDER={settings.gmail_sender}",
        f"GMAIL_APP_PASSWORD={settings.gmail_app_password}",
        f"GMAIL_RECIPIENT={settings.gmail_recipient}",
        f"WHATSAPP_PROVIDER={settings.whatsapp_provider}",
        f"WHATSAPP_PHONE={settings.whatsapp_phone}",
        f"CALLMEBOT_API_KEY={settings.callmebot_api_key}",
        f"TWILIO_ACCOUNT_SID={settings.twilio_account_sid}",
        f"TWILIO_AUTH_TOKEN={settings.twilio_auth_token}",
        f"TWILIO_WHATSAPP_FROM={settings.twilio_whatsapp_from}",
        f"META_PHONE_NUMBER_ID={settings.meta_phone_number_id}",
        f"META_ACCESS_TOKEN={settings.meta_access_token}",
        f"GEMINI_API_KEY={settings.gemini_api_key}",
        f"DEFAULT_CITY={settings.default_city}",
        f"SCRAPER_API_KEY={settings.scraper_api_key}",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_dotenv_file(target)
    print(f"  ✅ Saved notification config to {target}\n")
    return settings
