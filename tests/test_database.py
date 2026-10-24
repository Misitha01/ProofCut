
import json
import sqlite3

import backend.app.database as database


def use_test_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "test_proofcut.db",
    )
    database.initialize_database()


def test_database_initializes(tmp_path, monkeypatch):
    use_test_database(tmp_path, monkeypatch)

    with database.get_connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"videos", "timelines", "renders"}.issubset(tables)


def test_video_survives_database_reopen(tmp_path, monkeypatch):
    use_test_database(tmp_path, monkeypatch)

    metadata = {"format": {"duration": "120.5"}}

    database.save_video(
        "video-1",
        "sample.mp4",
        "storage/uploads/sample.mp4",
        metadata,
    )

    # Load using a new database connection.
    videos = database.load_videos()

    assert len(videos) == 1
    assert videos[0]["video_id"] == "video-1"
    assert videos[0]["filename"] == "sample.mp4"
    assert videos[0]["metadata"] == metadata


def test_timeline_is_saved_and_loaded(tmp_path, monkeypatch):
    use_test_database(tmp_path, monkeypatch)

    timeline = {
        "duration": 120,
        "segments": [{"start": 0, "end": 120, "speed": 1}],
        "captions": [],
    }

    database.save_timeline("timeline-1", timeline)

    saved = database.load_timelines()

    assert len(saved) == 1
    assert saved[0]["timeline_id"] == "timeline-1"
    assert saved[0]["timeline"] == timeline


def test_render_is_saved_and_loaded(tmp_path, monkeypatch):
    use_test_database(tmp_path, monkeypatch)

    database.save_render(
        "render-1",
        "timeline-1",
        "render-1.mp4",
        "storage/renders/render-1.mp4",
    )

    renders = database.load_renders()

    assert len(renders) == 1
    assert renders[0]["render_id"] == "render-1"
    assert renders[0]["status"] == "completed"


def test_saving_existing_video_updates_record(tmp_path, monkeypatch):
    use_test_database(tmp_path, monkeypatch)

    database.save_video("video-1", "old.mp4", "old.mp4", {})
    database.save_video("video-1", "new.mp4", "new.mp4", {})

    videos = database.load_videos()

    assert len(videos) == 1
    assert videos[0]["filename"] == "new.mp4"
