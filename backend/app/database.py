
import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = PROJECT_ROOT / "storage"
DATABASE_PATH = STORAGE_DIR / "proofcut.db"


def get_connection() -> sqlite3.Connection:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS timelines (
                timeline_id TEXT PRIMARY KEY,
                video_id TEXT,
                timeline_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id)
                    REFERENCES videos(video_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS renders (
                render_id TEXT PRIMARY KEY,
                timeline_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def save_video(
    video_id: str,
    filename: str,
    file_path: str,
    metadata: dict[str, Any],
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO videos (video_id, filename, file_path, metadata_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                filename = excluded.filename,
                file_path = excluded.file_path,
                metadata_json = excluded.metadata_json
            """,
            (video_id, filename, file_path, json.dumps(metadata)),
        )


def load_videos() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM videos ORDER BY created_at"
        ).fetchall()

    return [
        {
            "video_id": row["video_id"],
            "filename": row["filename"],
            "file_path": row["file_path"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def save_timeline(
    timeline_id: str,
    timeline: dict[str, Any],
    video_id: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO timelines (timeline_id, video_id, timeline_json)
            VALUES (?, ?, ?)
            ON CONFLICT(timeline_id) DO UPDATE SET
                video_id = excluded.video_id,
                timeline_json = excluded.timeline_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (timeline_id, video_id, json.dumps(timeline)),
        )


def load_timelines() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT timeline_id, video_id, timeline_json FROM timelines"
        ).fetchall()

    return [
        {
            "timeline_id": row["timeline_id"],
            "video_id": row["video_id"],
            "timeline": json.loads(row["timeline_json"]),
        }
        for row in rows
    ]


def save_render(
    render_id: str,
    timeline_id: str,
    filename: str,
    file_path: str,
    status: str = "completed",
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO renders
                (render_id, timeline_id, filename, file_path, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(render_id) DO UPDATE SET
                timeline_id = excluded.timeline_id,
                filename = excluded.filename,
                file_path = excluded.file_path,
                status = excluded.status
            """,
            (render_id, timeline_id, filename, file_path, status),
        )


def load_renders() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM renders ORDER BY created_at"
        ).fetchall()

    return [dict(row) for row in rows]
