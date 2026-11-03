from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Optional

from ticketfinder.config import Settings, interactive_setup_env
from ticketfinder.daemon import (
    get_daemon_status,
    run_check_for_job,
    run_daemon_worker_loop,
    start_daemon_background,
    stop_daemon,
)
from ticketfinder.diff import compute_diff_report
from ticketfinder.geo import geocode_location
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob
from ticketfinder.nlp import parse_natural_language_query
from ticketfinder.notifiers.base import dispatch_notifications
from ticketfinder.storage import Storage


def _friendly_date(iso_date: str) -> str:
    if iso_date.upper() == "ANY":
        return "Any Available Date (Whenever Bookings Open)"
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%a, %d %b %Y")
    except Exception:
        return iso_date


def print_parsed_summary(
    job: WatchJob, settings: Settings, all_dates: Optional[list[str]] = None
) -> None:
    dates_to_show = all_dates or [job.target_date]
    date_labels = " OR ".join(
        _friendly_date(d) if d.upper() == "ANY" else f"{_friendly_date(d)} ({d})"
        for d in dates_to_show
    )
    coord_str = (
        f" [Coords: {job.area_lat:.4f}, {job.area_lon:.4f}]"
        if job.area_lat is not None and job.area_lon is not None
        else ""
    )
    area_line = (
        f"{job.area}, {job.city} (within {job.radius_km:g} km){coord_str}"
        if job.area
        else f"{job.city} (City-wide)"
    )
    import re as _re
    strict_only = bool(
        job.cinemas and _re.search(r"\b(restrict|restricted|only)\b", job.raw_query or "", _re.IGNORECASE)
    )
    if job.cinemas:
        if strict_only:
            cinema_line = "ONLY: " + ", ".join(job.cinemas) + (
                f" (within {job.radius_km:g} km of {job.area})" if job.area else ""
            )
        else:
            cinema_line = ", ".join(job.cinemas) + (
                f" (+ any cinema within {job.radius_km:g} km of {job.area})" if job.area else ""
            )
    else:
        cinema_line = (
            f"Any cinema within {job.radius_km:g} km of {job.area}" if job.area else "All cinemas in city"
        )
    gmail_status = (
        f"✅ Configured ({settings.gmail_recipient})"
        if settings.gmail_configured
        else "⚠️  Not configured yet (will preview in terminal)"
    )
    wa_status = (
        f"✅ Configured ({settings.whatsapp_provider}: {settings.whatsapp_phone})"
        if settings.whatsapp_configured
        else (
            "🛑 Disabled"
            if settings.whatsapp_provider in ("disabled", "none", "off", "false", "")
            else "⚠️  Not configured yet (will preview in terminal)"
        )
    )

    print("\n🎬 Parsed Ticket Watch Settings")
    print("══════════════════════════════════════════════════════════════════════")
    print(f"  • Movie            : {job.movie}")
    print(f"  • Target Date(s)   : {date_labels}")
    print(f"  • City & Locality  : {area_line}")
    print(f"  • Target Cinemas   : {cinema_line}")
    print(f"  • Gmail Channel    : {gmail_status}")
    print(f"  • WhatsApp Channel : {wa_status}")
    if settings.telegram_configured:
        tg_label = f"✅ Configured (Chat ID: {settings.telegram_chat_id or 'auto-discover on first send'})"
        print(f"  • Telegram Channel : {tg_label}")
    print("══════════════════════════════════════════════════════════════════════")


def run_simulated_diff_test(
    movie: str = "Coolie",
    target_date: str = "2026-11-06",
    city: str = "Chennai",
    area: str = "Velachery",
    radius_km: float = 12.0,
    delay_seconds: float = 1.5,
    dry_run: bool = False,
) -> None:
    """
    Runs a 2-step simulated test (without any cron job) demonstrating:
      - Step 1: First wave of cinemas opening (Update #1)
      - Step 2: Second wave of cinemas opening + new showtimes added + seat deltas on previous cinemas (Update #2)
    """
    settings = Settings.from_env()
    job = WatchJob(
        id=1,
        movie=movie,
        target_date=target_date,
        city=city,
        area=area,
        area_lat=12.9755,
        area_lon=80.2207,
        radius_km=radius_km,
        cinemas=["Sathyam", "Palazzo"],
        alert_count=0,
    )

    print("\n🧪 Running 2-Step Notification & Diff Simulation (No Cron / No Background Job)...")

    # Wave 1: 2 initial cinemas open at 2:00 PM
    wave1_venues = [
        CinemaVenue(
            name="Cinepolis: BSR Mall, OMR",
            platform="TicketNew",
            booking_url="https://ticketnew.com/movies/chennai",
            distance_km=5.1,
            matched_by="nearby_locality",
            first_seen_at="2:00 PM",
            showtimes=[
                ShowtimeSlot(
                    time="11:00 AM",
                    screen_format="Dolby 7.1",
                    status="AVAILABLE",
                    available_seats=115,
                    booked_seats=65,
                    total_seats=180,
                )
            ],
        ),
        CinemaVenue(
            name="AGS Cinemas, Navalur",
            platform="BookMyShow",
            booking_url="https://in.bookmyshow.com/explore/movies-chennai",
            distance_km=11.4,
            matched_by="nearby_locality",
            first_seen_at="2:00 PM",
            showtimes=[
                ShowtimeSlot(
                    time="10:30 AM",
                    screen_format="Dolby Atmos",
                    status="AVAILABLE",
                    available_seats=160,
                    booked_seats=40,
                    total_seats=200,
                ),
                ShowtimeSlot(
                    time="02:30 PM",
                    screen_format="Dolby Atmos",
                    status="ALMOST_FULL",
                    available_seats=25,
                    booked_seats=175,
                    total_seats=200,
                ),
            ],
        ),
    ]

    print("\n─── [STEP 1/2] Simulating Wave 1 (Initial Cinemas Open) ───")
    report1 = compute_diff_report(
        job=job,
        current_venues=wave1_venues,
        previous_snapshots={},
        is_initial_check=False,
    )
    dispatch_notifications(
        report1, settings=settings, is_daemon_running=False, print_preview=True, dry_run=dry_run
    )

    if delay_seconds > 0:
        time.sleep(delay_seconds)

    # Wave 2: PVR Grand Mall & SPI Sathyam open + Cinepolis adds evening shows + earlier shows fill up
    job.alert_count = 1
    prev_snapshots = {v.normalized_key: v for v in wave1_venues}

    wave2_venues = [
        CinemaVenue(
            name="PVR: Grand Mall, Velachery",
            platform="TicketNew",
            booking_url="https://ticketnew.com/movies/chennai",
            extra_platforms=[
                ("BookMyShow", "https://in.bookmyshow.com/explore/movies-chennai")
            ],
            distance_km=2.1,
            matched_by="nearby_locality",
            showtimes=[
                ShowtimeSlot(
                    time="10:15 AM",
                    screen_format="Dolby Atmos",
                    status="AVAILABLE",
                    available_seats=184,
                    booked_seats=66,
                    total_seats=250,
                ),
                ShowtimeSlot(
                    time="01:45 PM",
                    screen_format="Dolby Atmos",
                    status="FAST_FILLING",
                    available_seats=52,
                    booked_seats=198,
                    total_seats=250,
                ),
                ShowtimeSlot(
                    time="06:30 PM",
                    screen_format="IMAX 2D",
                    status="ALMOST_FULL",
                    available_seats=18,
                    booked_seats=302,
                    total_seats=320,
                ),
                ShowtimeSlot(
                    time="10:15 PM",
                    screen_format="Dolby Atmos",
                    status="AVAILABLE",
                    available_seats=210,
                    booked_seats=40,
                    total_seats=250,
                ),
            ],
        ),
        CinemaVenue(
            name="SPI Sathyam, Royapettah",
            platform="BookMyShow",
            booking_url="https://in.bookmyshow.com/explore/movies-chennai",
            distance_km=9.8,
            matched_by="both",
            showtimes=[
                ShowtimeSlot(
                    time="09:00 AM",
                    screen_format="Dolby Atmos",
                    status="AVAILABLE",
                    category_info="Prime: ₹190 Available, Elite: Available",
                ),
                ShowtimeSlot(
                    time="07:45 PM",
                    screen_format="Dolby Atmos",
                    status="FAST_FILLING",
                    category_info="Prime: Fast Filling, Elite: Sold Out",
                ),
            ],
        ),
        CinemaVenue(
            name="Cinepolis: BSR Mall, OMR",
            platform="TicketNew",
            booking_url="https://ticketnew.com/movies/chennai",
            distance_km=5.1,
            matched_by="nearby_locality",
            first_seen_at="2:00 PM",
            showtimes=[
                ShowtimeSlot(
                    time="11:00 AM",
                    screen_format="Dolby 7.1",
                    status="FAST_FILLING",
                    available_seats=42,
                    booked_seats=138,
                    total_seats=180,
                ),
                ShowtimeSlot(
                    time="07:00 PM",
                    screen_format="Dolby 7.1",
                    status="AVAILABLE",
                    available_seats=160,
                    booked_seats=20,
                    total_seats=180,
                ),
                ShowtimeSlot(
                    time="10:30 PM",
                    screen_format="Dolby 7.1",
                    status="AVAILABLE",
                    available_seats=175,
                    booked_seats=5,
                    total_seats=180,
                ),
            ],
        ),
        CinemaVenue(
            name="AGS Cinemas, Navalur",
            platform="BookMyShow",
            booking_url="https://in.bookmyshow.com/explore/movies-chennai",
            distance_km=11.4,
            matched_by="nearby_locality",
            first_seen_at="2:00 PM",
            showtimes=[
                ShowtimeSlot(
                    time="10:30 AM",
                    screen_format="Dolby Atmos",
                    status="AVAILABLE",
                    available_seats=120,
                    booked_seats=80,
                    total_seats=200,
                ),
                ShowtimeSlot(
                    time="02:30 PM",
                    screen_format="Dolby Atmos",
                    status="SOLD_OUT",
                    available_seats=0,
                    booked_seats=200,
                    total_seats=200,
                ),
            ],
        ),
    ]

    print("\n─── [STEP 2/2] Simulating Wave 2 (New Cinemas + New Showtimes + Seat Deltas) ───")
    report2 = compute_diff_report(
        job=job,
        current_venues=wave2_venues,
        previous_snapshots=prev_snapshots,
        is_initial_check=False,
    )
    dispatch_notifications(
        report2, settings=settings, is_daemon_running=False, print_preview=True, dry_run=dry_run
    )
    print("✅ Simulation complete! Zero background jobs or cron tasks were created.\n")


def cmd_status(storage: Storage) -> None:
    running, pid = get_daemon_status()
    print("\n🤖 TicketFinder Agent Status")
    print("══════════════════════════════════════════════════════════════════════")
    if running:
        print(f"  • Background Daemon : 🟢 RUNNING (PID {pid}, sleep-protected via caffeinate)")
    else:
        print("  • Background Daemon : ⚪ STOPPED (no background polling active)")

    jobs = storage.list_jobs()
    if not jobs:
        print("  • Watchlist         : No watch jobs saved yet.")
        print("══════════════════════════════════════════════════════════════════════\n")
        return

    print(f"  • Watch Jobs        : {len(jobs)} total\n")
    for j in jobs:
        badge = "🟢 ACTIVE" if j.status == "ACTIVE" else "🛑 STOPPED"
        snapshots = storage.get_venue_snapshots(j.id) if j.id else {}
        print(
            f"  [ID #{j.id}] {badge} | {j.movie} on {_friendly_date(j.target_date)} ({j.target_date})"
        )
        print(f"          Target : {j.target_summary}")
        print(
            f"          Checks : {j.check_count} | Alerts Sent: {j.alert_count} | "
            f"Open Cinemas Tracked: {len(snapshots)}"
        )
        if snapshots:
            names = ", ".join(v.name for v in list(snapshots.values())[:4])
            print(f"          Opened : {names}")
        print()
    print("══════════════════════════════════════════════════════════════════════\n")


def handle_natural_language_flow(
    query: str,
    run_now_only: bool = False,
    start_daemon_flag: bool = False,
    auto_confirm: bool = False,
    dry_run: bool = False,
    initial_delay_mins: float = 5.0,
    interval_mins: float = 30.0,
) -> None:
    settings = Settings.from_env()
    storage = Storage()
    parsed = parse_natural_language_query(query, settings=settings)

    if parsed.intent == "STOP":
        if parsed.movie:
            count = storage.stop_jobs_by_movie(parsed.movie)
            print(f"🛑 Stopped {count} active watch job(s) matching '{parsed.movie}'.")
            active_left = storage.list_jobs(status="ACTIVE")
            if not active_left:
                _, msg = stop_daemon()
                print(f"🛑 {msg}")
        else:
            count = storage.stop_all_jobs()
            _, msg = stop_daemon()
            print(f"🛑 Stopped all {count} active watch job(s). {msg}")
        return

    area_lat: Optional[float] = None
    area_lon: Optional[float] = None
    if parsed.area:
        coords = geocode_location(parsed.area, parsed.city, storage=storage)
        if coords:
            area_lat, area_lon = coords

    target_dates = parsed.target_dates or [parsed.target_date]
    jobs = [
        WatchJob(
            raw_query=query,
            movie=parsed.movie,
            target_date=d,
            city=parsed.city,
            area=parsed.area,
            area_lat=area_lat,
            area_lon=area_lon,
            radius_km=parsed.radius_km,
            cinemas=parsed.cinemas,
        )
        for d in target_dates
    ]

    print_parsed_summary(jobs[0], settings, all_dates=target_dates)

    # Direct --now or --cli-only mode: test once right now without any cron/daemon
    if run_now_only or dry_run:
        if initial_delay_mins > 0 and not dry_run:
            print(
                f"\n⏳ Waiting {initial_delay_mins:g} minute(s) ({initial_delay_mins * 60:g}s) before running check — "
                f"lock your Mac screen now (Control + Command + Q)!"
            )
            sys.stdout.flush()
            time.sleep(initial_delay_mins * 60.0)
        reports = []
        mode_label = "CLI-only dry-run" if dry_run else "live one-shot"
        for job in jobs:
            job = storage.add_job(job)
            print(
                f"\n🔍 Running {mode_label} check across all ticketing sites for '{job.movie}' on {job.target_date} (Job #{job.id})..."
            )
            rep = run_check_for_job(
                job,
                storage=storage,
                settings=settings,
                force_notify=True,
                is_initial_check=False,
                is_daemon_running=False,
                print_preview=False,
                dry_run=dry_run,
                send_notification=False,
            )
            reports.append(rep)
            storage.stop_job(job.id)  # type: ignore[arg-type]
        if reports:
            dispatch_notifications(
                reports,
                settings=settings,
                is_daemon_running=False,
                print_preview=True,
                dry_run=dry_run,
            )
        print("✅ Check complete! (No cron or background daemon started.)")
        return

    # Direct --daemon mode with --yes
    if start_daemon_flag and auto_confirm:
        storage.stop_jobs_by_movie(parsed.movie)
        saved_ids: list[int] = []
        for job in jobs:
            saved = storage.add_job(job)
            if saved.id:
                saved_ids.append(saved.id)
        started, pid, log_path = start_daemon_background(
            initial_delay_minutes=initial_delay_mins,
            interval_minutes=interval_mins,
            heartbeat_minutes=60.0,
        )
        print(
            f"\n✅ Saved Watch Job(s) {saved_ids} and started background watcher (PID {pid}).\n"
            f"   • First consolidated notification in : {initial_delay_mins:g} minutes\n"
            f"   • Subsequent checks run every        : {interval_mins:g} minutes\n"
            f"   • Batch job heartbeat alert          : Every 60 minutes (1 hour)\n"
            f"   • Log file: {log_path}\n"
            f"   • To stop anytime: ticketfinder stop"
        )
        return

    # Interactive prompt
    if not sys.stdin.isatty():
        # Non-interactive default: run once immediately
        job = storage.add_job(job)
        run_check_for_job(
            job,
            storage=storage,
            settings=settings,
            force_notify=True,
            is_initial_check=False,
            is_daemon_running=False,
            print_preview=True,
        )
        storage.stop_job(job.id)  # type: ignore[arg-type]
        return

    print("\nHow would you like to proceed?")
    print("  [1] Test once RIGHT NOW (live check + Gmail/WhatsApp notification, NO cron/background job)")
    print(
        f"  [2] Start Background Bot (first notification in {initial_delay_mins:g} mins, then checks every {interval_mins:g} mins)"
    )
    print("  [3] Configure Gmail / WhatsApp (.env) credentials first")
    print("  [4] Cancel")
    choice = input("Select [1/2/3/4] (default: 1): ").strip() or "1"

    if choice == "3":
        settings = interactive_setup_env()
        print_parsed_summary(job, settings)
        choice = (
            input("Now select [1] Test once RIGHT NOW or [2] Start Background Bot (default: 1): ").strip()
            or "1"
        )

    if choice == "1":
        job = storage.add_job(job)
        print(f"\n🔍 Checking all ticketing platforms right now for '{job.movie}'...")
        run_check_for_job(
            job,
            storage=storage,
            settings=settings,
            force_notify=True,
            is_initial_check=False,
            is_daemon_running=False,
            print_preview=True,
        )
        storage.stop_job(job.id)  # type: ignore[arg-type]
        print("✅ One-shot test finished! Zero background processes left running.")
    elif choice == "2":
        job = storage.add_job(job)
        started, pid, log_path = start_daemon_background(
            initial_delay_minutes=initial_delay_mins,
            interval_minutes=interval_mins,
        )
        status_word = "Started" if started else "Attached to running"
        print(
            f"\n✅ Saved Watch Job #{job.id}! {status_word} Mac background bot (PID {pid}).\n"
            f"   • First check & notification in : {initial_delay_mins:g} minutes\n"
            f"   • Recurring check interval      : Every {interval_mins:g} minutes\n"
            f"   • Sleep protection              : Active (caffeinate -i)\n"
            f"   • Daemon log                    : {log_path}\n"
            f"   • To stop anytime               : ticketfinder stop"
        )
    else:
        print("Cancelled.")


def cmd_tips() -> None:
    print(
        """
🎬 TicketFinder Bot — Quick Command Tipsheet
══════════════════════════════════════════════════════════════════════════════

1️⃣  INSTANT CHECK (No Background Job)
    • Check & send alert to Telegram + Gmail right now:
      ./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --now

    • Check in terminal ONLY (no Telegram / Gmail sent):
      ./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --cli-only

    • Check after 1-minute delay (to test Locked Screen delivery):
      ./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --now --initial-delay 1

2️⃣  START BACKGROUND WATCHER (5-Min Initial + 30-Min Diffs + 1-Hr Heartbeat)
    • Single date:
      ./ticketfinder watch movie tickets for ramayana tamil version on 7th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes

    • Multiple dates (sends 1 consolidated alert for both dates):
      ./ticketfinder watch movie tickets for ramayana tamil version on 7th november or 9th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes

    • Custom intervals (e.g. first alert in 2 mins, check every 15 mins):
      ./ticketfinder watch movie tickets for coolie tamil version on 14th november near velachery within 12km --daemon --yes --initial-delay 2 --interval 15

3️⃣  MONITOR & MANAGE RUNNING WATCHER
    • View daemon status & active jobs:
      ./ticketfinder status

    • Trigger an immediate check (or heartbeat preview) for active jobs:
      ./ticketfinder check
      ./ticketfinder check --heartbeat --cli-only

    • View live background daemon logs:
      tail -n 50 .ticketfinder/daemon.log

4️⃣  STOP WATCHING
    • Stop a specific movie (stops daemon if no other movies are active):
      ./ticketfinder stop --movie "Ramayana"

    • Stop a specific Job ID:
      ./ticketfinder stop --id 39

    • Stop ALL active jobs & shut down background daemon:
      ./ticketfinder stop

5️⃣  SIMULATE & TEST NOTIFICATIONS
    • Simulate Wave 1 -> Wave 2 cinema openings & seat diffs:
      ./ticketfinder test-notify
══════════════════════════════════════════════════════════════════════════════
"""
    )


def main(argv: Optional[list[str]] = None) -> int:
    args_list = list(argv if argv is not None else sys.argv[1:])
    storage = Storage()

    # Handle explicit subcommands cleanly
    if args_list and args_list[0] in (
        "status",
        "list",
        "stop",
        "check",
        "test-notify",
        "setup",
        "tips",
        "cheatsheet",
        "help",
        "_daemon-worker",
    ):
        sub = args_list[0]
        rest = args_list[1:]

        if sub in ("tips", "cheatsheet", "help"):
            cmd_tips()
            return 0

        if sub in ("status", "list"):
            cmd_status(storage)
            return 0

        if sub == "setup":
            interactive_setup_env()
            return 0

        if sub == "test-notify":
            parser = argparse.ArgumentParser(prog="ticketfinder test-notify")
            parser.add_argument("--movie", default="Coolie")
            parser.add_argument("--date", default="2026-11-06")
            parser.add_argument("--city", default="Chennai")
            parser.add_argument("--area", default="Velachery")
            parser.add_argument("--radius", type=float, default=12.0)
            parser.add_argument("--delay", type=float, default=1.0)
            p_args = parser.parse_args(rest)
            run_simulated_diff_test(
                movie=p_args.movie,
                target_date=p_args.date,
                city=p_args.city,
                area=p_args.area,
                radius_km=p_args.radius,
                delay_seconds=p_args.delay,
            )
            return 0

        if sub == "stop":
            parser = argparse.ArgumentParser(prog="ticketfinder stop")
            parser.add_argument("--id", type=int, help="Stop a specific watch job ID")
            parser.add_argument("--movie", type=str, help="Stop watch jobs matching movie name")
            p_args = parser.parse_args(rest)

            if p_args.id is not None:
                ok = storage.stop_job(p_args.id)
                print(
                    f"🛑 Stopped Watch Job #{p_args.id}."
                    if ok
                    else f"⚠️  Job #{p_args.id} was not found or already stopped."
                )
                if not storage.list_jobs(status="ACTIVE"):
                    _, msg = stop_daemon()
                    print(f"🛑 {msg}")
            elif p_args.movie:
                count = storage.stop_jobs_by_movie(p_args.movie)
                print(f"🛑 Stopped {count} job(s) matching '{p_args.movie}'.")
                if not storage.list_jobs(status="ACTIVE"):
                    _, msg = stop_daemon()
                    print(f"🛑 {msg}")
            else:
                count = storage.stop_all_jobs()
                _, msg = stop_daemon()
                print(f"🛑 Stopped {count} active watch job(s). {msg}")
            return 0

        if sub == "check":
            parser = argparse.ArgumentParser(prog="ticketfinder check")
            parser.add_argument("--id", type=int, help="Specific job ID to check now")
            parser.add_argument("--cli-only", "--dry-run", dest="dry_run", action="store_true")
            parser.add_argument("--heartbeat", action="store_true", help="Format as 1-hour heartbeat alert")
            p_args = parser.parse_args(rest)
            settings = Settings.from_env()
            if p_args.id:
                job = storage.get_job(p_args.id)
                jobs = [job] if job else []
            else:
                jobs = storage.list_jobs(status="ACTIVE")
                if not jobs:
                    jobs = storage.list_jobs()[:1]
            if not jobs:
                print("⚠️  No watch jobs found. Pass a natural language query to check a movie!")
                return 1
            grouped: dict[str, list] = {}
            for j in sorted(jobs, key=lambda x: x.target_date):
                print(f"\n🔍 Running immediate check for Job #{j.id}: {j.movie} ({j.target_date})...")
                rep = run_check_for_job(
                    j,
                    storage=storage,
                    settings=settings,
                    force_notify=True,
                    is_initial_check=False,
                    is_daemon_running=False,
                    print_preview=False,
                    dry_run=p_args.dry_run,
                    send_notification=False,
                )
                grouped.setdefault(j.movie.strip().lower(), []).append(rep)
            for _, reps in grouped.items():
                dispatch_notifications(
                    reps,
                    settings=settings,
                    is_daemon_running=True,
                    print_preview=True,
                    dry_run=p_args.dry_run,
                    is_heartbeat=p_args.heartbeat,
                )
            return 0

        if sub == "_daemon-worker":
            parser = argparse.ArgumentParser()
            parser.add_argument("--initial-delay", type=float, default=5.0)
            parser.add_argument("--interval", type=float, default=30.0)
            parser.add_argument("--heartbeat", type=float, default=60.0)
            p_args = parser.parse_args(rest)
            run_daemon_worker_loop(
                initial_delay_minutes=p_args.initial_delay,
                interval_minutes=p_args.interval,
                heartbeat_minutes=p_args.heartbeat,
            )
            return 0

    if args_list and args_list[0] in ("watch", "find", "query"):
        args_list = args_list[1:]

    # Natural language CLI entrypoint
    parser = argparse.ArgumentParser(
        prog="ticketfinder",
        description=(
            "Natural-language Movie Ticket Watcher Agent.\n"
            "Example:\n"
            '  ticketfinder "Watch for Coolie on Friday Nov 6 in Chennai near Velachery within 12km and Sathyam" --now\n'
            "  ticketfinder test-notify\n"
            "  ticketfinder status\n"
            "  ticketfinder stop"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Natural language query describing movie, date, city, locality/radius, and cinemas",
    )
    parser.add_argument(
        "--now",
        "--once",
        "--test",
        dest="run_now",
        action="store_true",
        help="Run a single live check & notification right now without any cron/background daemon",
    )
    parser.add_argument(
        "--daemon",
        "--background",
        dest="start_daemon",
        action="store_true",
        help="Start the background watcher (first check in 5 mins, then every 30 mins)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        dest="auto_confirm",
        action="store_true",
        help="Confirm settings automatically without interactive prompt",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run the 2-step simulated notification & diff test (no cron job)",
    )
    parser.add_argument(
        "--cli-only",
        "--dry-run",
        "--no-email",
        dest="dry_run",
        action="store_true",
        help="Run the check and print full output in CLI without sending any Email or WhatsApp",
    )
    parser.add_argument(
        "--initial-delay",
        type=float,
        default=None,
        help="Minutes before the first notification (default: 0 for --now, 5 for --daemon)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Minutes between recurring checks in background mode (default: 30)",
    )

    parsed_args = parser.parse_args(args_list)

    if parsed_args.simulate:
        run_simulated_diff_test(dry_run=parsed_args.dry_run)
        return 0

    query_str = " ".join(parsed_args.query).strip()
    if not query_str:
        if sys.stdin.isatty():
            print("🎬 TicketFinder Natural Language Setup")
            print('   (e.g. "Watch for Coolie on Friday Nov 6 in Chennai near Velachery within 12km and Sathyam")')
            query_str = input("\n💬 Enter your request: ").strip()
        if not query_str:
            parser.print_help()
            return 0

    resolved_initial_delay = (
        parsed_args.initial_delay
        if parsed_args.initial_delay is not None
        else (0.0 if (parsed_args.run_now or parsed_args.dry_run) else 5.0)
    )
    handle_natural_language_flow(
        query=query_str,
        run_now_only=parsed_args.run_now,
        start_daemon_flag=parsed_args.start_daemon,
        auto_confirm=parsed_args.auto_confirm,
        dry_run=parsed_args.dry_run,
        initial_delay_mins=resolved_initial_delay,
        interval_mins=parsed_args.interval,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
