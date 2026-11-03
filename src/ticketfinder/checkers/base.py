from __future__ import annotations

import os
import subprocess
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ticketfinder.models import CinemaVenue, WatchJob

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
}

MACOS_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


def fetch_page_content(
    url: str,
    timeout: float = 12.0,
    use_browser_fallback: bool = True,
) -> str:
    """
    Fetches HTML or JSON content from a ticketing URL.
    1. If SCRAPER_API_KEY is set, routes via ScraperAPI (country_code=in).
    2. Tries fast HTTP fetch via urllib / curl.
    3. Falls back to headless Chrome on macOS if blocked by JS challenge.
    """
    scraper_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    target_url = url
    if scraper_key:
        params = urllib.parse.urlencode(
            {"api_key": scraper_key, "url": url, "country_code": "in"}
        )
        target_url = f"https://api.scraperapi.com/?{params}"

    # Tier 1: Direct HTTP request
    try:
        req = urllib.request.Request(target_url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            if content and "cf-browser-verification" not in content and len(content) > 400:
                return content
    except Exception:
        pass

    # Tier 2: macOS curl (handles HTTP/2 & TLS fingerprint cleanly)
    try:
        proc = subprocess.run(
            [
                "/usr/bin/curl",
                "-sL",
                "--max-time",
                str(int(timeout)),
                "-A",
                DEFAULT_HEADERS["User-Agent"],
                "-H",
                "Accept-Language: en-IN,en;q=0.9",
                target_url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        if proc.returncode == 0 and len(proc.stdout) > 400 and "Just a moment..." not in proc.stdout:
            return proc.stdout
    except Exception:
        pass

    # Tier 3: Headless Chrome/Edge on macOS if enabled
    if use_browser_fallback:
        for browser_bin in MACOS_BROWSERS:
            if Path(browser_bin).exists():
                try:
                    proc = subprocess.run(
                        [
                            browser_bin,
                            "--headless=new",
                            "--disable-gpu",
                            "--no-sandbox",
                            "--disable-blink-features=AutomationControlled",
                            f"--user-agent={DEFAULT_HEADERS['User-Agent']}",
                            "--virtual-time-budget=3500",
                            "--dump-dom",
                            url,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=timeout + 5,
                    )
                    if proc.returncode == 0 and len(proc.stdout) > 400:
                        return proc.stdout
                except Exception:
                    continue

    return ""


class PlatformChecker(ABC):
    """Abstract base class for a ticketing platform checker."""

    platform_name: str = "Unknown"

    @abstractmethod
    def check(self, job: WatchJob) -> list[CinemaVenue]:
        """Checks open venues and showtimes for the given WatchJob."""
        raise NotImplementedError


def check_all_platforms(
    job: WatchJob,
    checkers: Optional[list[PlatformChecker]] = None,
) -> tuple[list[CinemaVenue], list[str]]:
    """
    Runs all platform checkers concurrently and returns:
      (discovered_venues, platform_names_checked)
    """
    if checkers is None:
        from ticketfinder.checkers.bookmyshow import BookMyShowChecker
        from ticketfinder.checkers.district import DistrictChecker
        from ticketfinder.checkers.ticketnew import TicketNewChecker
        from ticketfinder.checkers.web_discovery import WebDiscoveryChecker

        checkers = [
            BookMyShowChecker(),
            DistrictChecker(),
            TicketNewChecker(),
            WebDiscoveryChecker(),
        ]

    all_venues: list[CinemaVenue] = []
    sites_checked: list[str] = [c.platform_name for c in checkers]

    with ThreadPoolExecutor(max_workers=len(checkers)) as pool:
        future_map = {pool.submit(c.check, job): c.platform_name for c in checkers}
        for fut in as_completed(future_map):
            try:
                venues = fut.result()
                if venues:
                    all_venues.extend(venues)
            except Exception:
                continue

    return all_venues, sites_checked
