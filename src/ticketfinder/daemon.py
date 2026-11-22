from __future__ import annotations

import fcntl
import os
import random
import signal
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ticketfinder.checkers.base import PlatformChecker, check_all_platforms
from ticketfinder.config import Settings, get_app_dir
from ticketfinder.diff import compute_diff_report
from ticketfinder.matcher import filter_and_rank_venues
from ticketfinder.models import DiffReport, WatchJob
from ticketfinder.notifiers.base import dispatch_notifications
from ticketfinder.storage import Storage


def get_pid_file() -> Path:
    return get_app_dir() / "daemon.pid"


def get_lock_file() -> Path:
    return get_app_dir() / "daemon.lock"


def _release_pid_file(pid: int) -> None:
    """Removes the PID file only if it still points at this process (never another daemon's)."""
    pid_file = get_pid_file()
    try:
        if pid_file.read_text(encoding="utf-8").strip() == str(pid):
            pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def get_log_file() -> Path:
    return get_app_dir() / "daemon.log"


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # EPERM means the process exists on the system
        return True
    except OSError:
        return False


def get_daemon_status() -> tuple[bool, Optional[int]]:
    """Returns (is_running, pid)."""
    pid_file = get_pid_file()
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        if is_pid_alive(pid):
            return True, pid
    except Exception:
        pass
    pid_file.unlink(missing_ok=True)
    return False, None


def stop_daemon() -> tuple[bool, str]:
    """Stops the background watcher daemon and releases the caffeinate sleep lock."""
    running, pid = get_daemon_status()
    if not running or pid is None:
        return False, "Background daemon is not currently running."
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not is_pid_alive(pid):
                break
            time.sleep(0.1)
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    get_pid_file().unlink(missing_ok=True)
    return True, f"Stopped background watcher daemon (PID {pid}) and released sleep lock."


def run_check_for_job(
    job: WatchJob,
    storage: Optional[Storage] = None,
    settings: Optional[Settings] = None,
    force_notify: bool = False,
    is_initial_check: bool = False,
    is_daemon_running: bool = False,
    print_preview: bool = True,
    dry_run: bool = False,
    checkers: Optional[list[PlatformChecker]] = None,
    send_notification: bool = True,
) -> DiffReport:
    """
    Executes a single multi-platform check for a WatchJob, computes the New vs. Previous
    diff + seat occupancy, updates SQLite snapshots, and sends notifications if triggered.
    """
    store = storage or Storage()
    cfg = settings or Settings.from_env()

    raw_venues, sites_checked = check_all_platforms(job, checkers=checkers)
    matched_venues = filter_and_rank_venues(raw_venues, job, storage=store)

    previous_snapshots = store.get_venue_snapshots(job.id) if job.id else {}
    report = compute_diff_report(
        job=job,
        current_venues=matched_venues,
        previous_snapshots=previous_snapshots,
        is_initial_check=is_initial_check,
        sites_checked=sites_checked,
    )

    should_notify = force_notify or is_initial_check or report.has_new_openings

    if job.id and matched_venues and not dry_run:
        store.save_venue_snapshots(job.id, matched_venues)

    if send_notification and should_notify:
        dispatch_notifications(
            report,
            settings=cfg,
            is_daemon_running=is_daemon_running,
            print_preview=print_preview,
            dry_run=dry_run,
        )

    if job.id:
        updated_job = store.record_check(
            job.id, alerted=(should_notify and report.any_tickets_open)
        )
        if updated_job:
            report.job = updated_job

    return report


def start_daemon_background(
    initial_delay_minutes: float = 5.0,
    interval_minutes: float = 30.0,
    heartbeat_minutes: float = 240.0,
) -> tuple[bool, int, Path]:
    """
    Spawns the background watcher daemon wrapped in `/usr/bin/caffeinate -i -s` on macOS
    so it continues running even when the Mac screen is locked.
    """
    running, existing_pid = get_daemon_status()
    if running and existing_pid is not None:
        stop_daemon()

    log_path = get_log_file()
    cmd: list[str] = []
    if Path("/usr/bin/caffeinate").exists():
        cmd.extend(["/usr/bin/caffeinate", "-i", "-s"])

    cmd.extend(
        [
            sys.executable,
            "-m",
            "ticketfinder.cli",
            "_daemon-worker",
            "--initial-delay",
            str(initial_delay_minutes),
            "--interval",
            str(interval_minutes),
            "--heartbeat",
            str(heartbeat_minutes),
        ]
    )

    env = os.environ.copy()
    src_dir = str(Path(__file__).resolve().parents[1])
    existing_py_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_py_path}" if existing_py_path else src_dir

    with open(log_path, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

    get_pid_file().write_text(str(proc.pid), encoding="utf-8")
    return True, proc.pid, log_path


def run_daemon_worker_loop(
    initial_delay_minutes: float = 5.0,
    interval_minutes: float = 30.0,
    heartbeat_minutes: float = 240.0,
) -> None:
    """
    Background loop:
      1. Waits initial_delay_minutes (default 5 mins) and sends the first consolidated status & seat notification.
      2. Loops every interval_minutes (default 30 mins):
         - Sends immediate consolidated diff alerts if any watched date opens new cinemas/showtimes.
         - Sends a 4-hour Heartbeat Alert every heartbeat_minutes (default 240 mins) confirming the batch job is alive.
    """
    pid = os.getpid()
    # Single-instance guard: a second worker would send every alert/heartbeat twice.
    lock_fh = open(get_lock_file(), "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            f"[{datetime.now().isoformat()}] Another TicketFinder daemon already holds the lock. "
            f"Worker PID {pid} exiting to avoid duplicate notifications."
        )
        sys.stdout.flush()
        return
    lock_fh.write(str(pid))
    lock_fh.flush()

    store = Storage()
    cfg = Settings.from_env()
    get_pid_file().write_text(str(pid), encoding="utf-8")

    def _handle_sigterm(signum: int, frame: object) -> None:
        print(f"[{datetime.now().isoformat()}] Daemon (PID {pid}) received stop signal. Exiting.")
        _release_pid_file(pid)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    print(
        f"[{datetime.now().isoformat()}] Started TicketFinder Daemon (PID {pid}). "
        f"First check in {initial_delay_minutes:g} mins, then every {interval_minutes:g} mins "
        f"(4-hour heartbeat every {heartbeat_minutes:g} mins)."
    )
    sys.stdout.flush()

    # Wait initial 5-minute setup delay
    if initial_delay_minutes > 0:
        time.sleep(initial_delay_minutes * 60.0)

    last_notified_mono: dict[tuple[str, ...], float] = {}
    heartbeat_threshold_sec = max(60.0, (heartbeat_minutes * 60.0) - 90.0)

    while True:
        cfg = Settings.from_env()
        active_jobs = store.list_jobs(status="ACTIVE")
        today_iso = date.today().isoformat()

        # Auto-expire past-date jobs
        valid_jobs: list[WatchJob] = []
        for j in active_jobs:
            if j.target_date != "ANY" and j.target_date < today_iso and j.id:
                store.stop_job(j.id)
                print(
                    f"[{datetime.now().isoformat()}] Auto-expired past job #{j.id} ({j.movie} on {j.target_date})"
                )
            else:
                valid_jobs.append(j)

        if not valid_jobs:
            print(
                f"[{datetime.now().isoformat()}] No active watch jobs remaining. Daemon shutting down."
            )
            _release_pid_file(pid)
            break

        # Group active jobs by movie + city + locality + cinemas so multiple dates send 1 consolidated notification
        grouped_jobs: dict[tuple[str, ...], list[WatchJob]] = {}
        for job in sorted(valid_jobs, key=lambda x: x.target_date):
            g_key = (
                job.movie.strip().lower(),
                job.city.strip().lower(),
                (job.area or "").strip().lower(),
                ",".join(c.lower() for c in job.cinemas),
            )
            grouped_jobs.setdefault(g_key, []).append(job)

        for g_key, group_list in grouped_jobs.items():
            group_reports: list[DiffReport] = []
            any_initial = False
            for job in group_list:
                initial_for_job = job.check_count == 0
                if initial_for_job:
                    any_initial = True
                print(
                    f"[{datetime.now().isoformat()}] Checking job #{job.id}: {job.movie} ({job.target_date}) "
                    f"[initial={initial_for_job}]"
                )
                try:
                    rep = run_check_for_job(
                        job,
                        storage=store,
                        settings=cfg,
                        force_notify=initial_for_job,
                        is_initial_check=initial_for_job,
                        is_daemon_running=True,
                        print_preview=False,
                        send_notification=False,
                    )
                    group_reports.append(rep)
                except Exception as exc:
                    print(f"[{datetime.now().isoformat()}] Error checking job #{job.id}: {exc}")
                sys.stdout.flush()

            if not group_reports:
                continue

            now_mono = time.monotonic()
            prev_notif_mono = last_notified_mono.get(g_key)
            any_new_openings = any(r.has_new_openings for r in group_reports)
            heartbeat_due = (
                prev_notif_mono is None
                or (now_mono - prev_notif_mono) >= heartbeat_threshold_sec
            )

            if any_initial or any_new_openings or heartbeat_due:
                is_hb = bool(heartbeat_due and not any_initial and not any_new_openings)
                dates_summary = ", ".join(r.job.target_date for r in group_reports)
                reason = (
                    "initial 5-min check"
                    if any_initial
                    else ("new ticket openings" if any_new_openings else "4-hour heartbeat")
                )
                print(
                    f"[{datetime.now().isoformat()}] Sending consolidated notification for "
                    f"'{group_reports[0].job.movie}' ({dates_summary}) [reason={reason}]"
                )
                try:
                    dispatch_notifications(
                        group_reports,
                        settings=cfg,
                        is_daemon_running=True,
                        print_preview=True,
                        is_heartbeat=is_hb,
                    )
                    last_notified_mono[g_key] = time.monotonic()
                except Exception as exc:
                    print(f"[{datetime.now().isoformat()}] Error sending notification: {exc}")
                sys.stdout.flush()

        jitter_sec = random.uniform(5.0, 20.0)
        sleep_sec = max(10.0, (interval_minutes * 60.0) + jitter_sec)
        print(
            f"[{datetime.now().isoformat()}] Sleeping {sleep_sec / 60.0:.1f} minutes until next check..."
        )
        sys.stdout.flush()
        time.sleep(sleep_sec)
