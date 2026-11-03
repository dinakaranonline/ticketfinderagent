from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ticketfinder.config import get_app_dir
from ticketfinder.models import CinemaVenue, WatchJob


class Storage:
    """SQLite state store for watch jobs, venue snapshots, and geocoding cache."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (get_app_dir() / "watchlist.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watch_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_query TEXT NOT NULL DEFAULT '',
                    movie TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    city TEXT NOT NULL,
                    area TEXT,
                    area_lat REAL,
                    area_lon REAL,
                    radius_km REAL NOT NULL DEFAULT 15.0,
                    cinemas_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    check_count INTEGER NOT NULL DEFAULT 0,
                    alert_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_checked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS venue_snapshots (
                    job_id INTEGER NOT NULL,
                    venue_key TEXT NOT NULL,
                    venue_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, venue_key),
                    FOREIGN KEY (job_id) REFERENCES watch_jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS geo_cache (
                    query_key TEXT PRIMARY KEY,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def add_job(self, job: WatchJob) -> WatchJob:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job.created_at = job.created_at or now
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO watch_jobs (
                    raw_query, movie, target_date, city, area, area_lat, area_lon,
                    radius_km, cinemas_json, status, check_count, alert_count,
                    created_at, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.raw_query,
                    job.movie,
                    job.target_date,
                    job.city,
                    job.area,
                    job.area_lat,
                    job.area_lon,
                    job.radius_km,
                    json.dumps(job.cinemas),
                    job.status,
                    job.check_count,
                    job.alert_count,
                    job.created_at,
                    job.last_checked_at,
                ),
            )
            job.id = int(cursor.lastrowid)
        return job

    def _row_to_job(self, row: sqlite3.Row) -> WatchJob:
        return WatchJob(
            id=row["id"],
            raw_query=row["raw_query"],
            movie=row["movie"],
            target_date=row["target_date"],
            city=row["city"],
            area=row["area"],
            area_lat=row["area_lat"],
            area_lon=row["area_lon"],
            radius_km=float(row["radius_km"]),
            cinemas=json.loads(row["cinemas_json"] or "[]"),
            status=row["status"],
            check_count=int(row["check_count"]),
            alert_count=int(row["alert_count"]),
            created_at=row["created_at"],
            last_checked_at=row["last_checked_at"],
        )

    def get_job(self, job_id: int) -> Optional[WatchJob]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watch_jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs(self, status: Optional[str] = None) -> list[WatchJob]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM watch_jobs WHERE status = ? ORDER BY id DESC",
                    (status.upper(),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM watch_jobs ORDER BY id DESC").fetchall()
            return [self._row_to_job(r) for r in rows]

    def stop_job(self, job_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE watch_jobs SET status = 'STOPPED' WHERE id = ? AND status = 'ACTIVE'",
                (job_id,),
            )
            return cur.rowcount > 0

    def stop_jobs_by_movie(self, movie_query: str) -> int:
        pattern = f"%{movie_query.strip()}%"
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE watch_jobs SET status = 'STOPPED' WHERE status = 'ACTIVE' AND movie LIKE ?",
                (pattern,),
            )
            return cur.rowcount

    def stop_all_jobs(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE watch_jobs SET status = 'STOPPED' WHERE status = 'ACTIVE'"
            )
            return cur.rowcount

    def delete_job(self, job_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM venue_snapshots WHERE job_id = ?", (job_id,))
            cur = conn.execute("DELETE FROM watch_jobs WHERE id = ?", (job_id,))
            return cur.rowcount > 0

    def record_check(self, job_id: int, alerted: bool = False) -> Optional[WatchJob]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            if alerted:
                conn.execute(
                    """
                    UPDATE watch_jobs
                    SET check_count = check_count + 1,
                        alert_count = alert_count + 1,
                        last_checked_at = ?
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE watch_jobs
                    SET check_count = check_count + 1,
                        last_checked_at = ?
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
        return self.get_job(job_id)

    def get_venue_snapshots(self, job_id: int) -> dict[str, CinemaVenue]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT venue_key, venue_json, first_seen_at FROM venue_snapshots WHERE job_id = ?",
                (job_id,),
            ).fetchall()
            result: dict[str, CinemaVenue] = {}
            for row in rows:
                data = json.loads(row["venue_json"])
                venue = CinemaVenue.from_dict(data)
                venue.first_seen_at = row["first_seen_at"]
                result[row["venue_key"]] = venue
            return result

    def save_venue_snapshots(self, job_id: int, venues: list[CinemaVenue]) -> None:
        now = datetime.now().strftime("%I:%M %p").lstrip("0")
        iso_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = self.get_venue_snapshots(job_id)
        with self._connect() as conn:
            for venue in venues:
                key = venue.normalized_key
                prev = existing.get(key)
                first_seen = (prev.first_seen_at if prev else None) or venue.first_seen_at or now
                venue.first_seen_at = first_seen
                conn.execute(
                    """
                    INSERT INTO venue_snapshots (job_id, venue_key, venue_json, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(job_id, venue_key) DO UPDATE SET
                        venue_json = excluded.venue_json,
                        updated_at = excluded.updated_at
                    """,
                    (job_id, key, json.dumps(venue.to_dict()), first_seen, iso_now),
                )

    def get_cached_coords(self, query: str) -> Optional[tuple[float, float]]:
        key = query.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT latitude, longitude FROM geo_cache WHERE query_key = ?", (key,)
            ).fetchone()
            if row:
                return float(row["latitude"]), float(row["longitude"])
            return None

    def save_cached_coords(self, query: str, lat: float, lon: float) -> None:
        key = query.strip().lower()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO geo_cache (query_key, latitude, longitude, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    updated_at = excluded.updated_at
                """,
                (key, lat, lon, now),
            )
