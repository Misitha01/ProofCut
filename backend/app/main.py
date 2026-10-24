import re
import hashlib
import json

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.app.timeline.engine import Timeline, TimelineHistory
from backend.app.video import router as video_router, VIDEO_RECORDS
from backend.app.render import router as render_router
from backend.app import database


app = FastAPI(
    title="ProofCut API",
    version="0.6.0",
    description=(
        "ProofCut video editing API with deterministic timeline operations, "
        "source-anchored captions, audio-edit metadata, history, persistence "
        "and video rendering."
    ),
)

app.include_router(video_router)
app.include_router(render_router)

histories = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class Instruction(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    timeline_id: str = "demo"


class CutRequest(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


class SpeedRequest(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    speed: float = Field(gt=0, le=16)


class KeepRequest(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


class ReorderRequest(BaseModel):
    ranges: list[list[float]] = Field(min_length=1)


class CaptionCreate(BaseModel):
    caption_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=500)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    color: str = "#FFFFFF"


class CaptionUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, gt=0)
    color: str | None = None


class AudioEditRequest(BaseModel):
    edit_id: str = Field(min_length=1, max_length=100)
    action: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    word: str = ""


class MusicRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=1000)
    volume: float = Field(default=0.15, ge=0, le=1)
    ducking: bool = True


class RestoreRequest(BaseModel):
    state_index: int = Field(ge=0)


# ============================================================
# TIMELINE HASH
# ============================================================

def calculate_timeline_hash(timeline_dict: dict) -> str:
    """Calculate a stable SHA-256 hash from canonical timeline JSON."""
    canonical = json.dumps(
        timeline_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def timeline_response(timeline_id: str, timeline: Timeline) -> dict:
    snapshot = timeline.to_dict()
    return {
        "timeline_id": timeline_id,
        "timeline": snapshot,
        "timeline_hash": calculate_timeline_hash(snapshot),
    }


# ============================================================
# PERSISTENCE
# ============================================================

def persist_timeline(timeline_id: str) -> None:
    history = histories.get(timeline_id)

    if history is None:
        raise ValueError("Cannot persist a missing timeline")

    video_id = None

    if timeline_id.startswith("video-"):
        candidate = timeline_id[len("video-"):]
        if candidate in VIDEO_RECORDS:
            video_id = candidate

    database.save_timeline(
        timeline_id=timeline_id,
        timeline=history.current.to_dict(),
        video_id=video_id,
    )


def restore_history(snapshot: dict) -> TimelineHistory:
    """Restore the saved current timeline, including reordered segments."""
    if not isinstance(snapshot, dict):
        raise ValueError("Saved timeline must be a JSON object")

    source_duration = snapshot.get("source_duration_s")

    if source_duration is None:
        source_duration = snapshot.get(
            "source_duration",
            snapshot.get("duration_seconds", snapshot.get("duration")),
        )

    if source_duration is None:
        raise ValueError("Saved timeline has no source duration")

    timeline = Timeline(float(source_duration))

    saved_segments = snapshot.get("segments")

    if not isinstance(saved_segments, list) or not saved_segments:
        raise ValueError("Saved timeline has no segments")

    timeline.segments = [
        {
            "source_start_s": float(
                segment.get("source_start_s", segment.get("start", 0))
            ),
            "source_end_s": float(
                segment.get("source_end_s", segment.get("end", 0))
            ),
            "speed": float(segment.get("speed", 1.0)),
        }
        for segment in saved_segments
    ]
    timeline._validate_segments()

    for index, caption in enumerate(snapshot.get("captions", [])):
        start = caption.get(
            "anchor_start_s", caption.get("start_s")
        )
        end = caption.get(
            "anchor_end_s", caption.get("end_s")
        )

        if start is None or end is None:
            continue

        timeline.add_caption(
            caption.get("id", f"restored_caption_{index + 1}"),
            caption.get("text", ""),
            float(start),
            float(end),
            caption.get("color", "#FFFFFF"),
        )

    for index, edit in enumerate(snapshot.get("audio_edits", [])):
        timeline.add_audio_edit(
            edit.get("id", f"restored_audio_edit_{index + 1}"),
            edit.get("action", "mute"),
            float(edit["anchor_start_s"]),
            float(edit["anchor_end_s"]),
            edit.get("word", ""),
        )

    music = snapshot.get("background_music")
    if music:
        timeline.set_background_music(
            music["file_path"],
            music.get("volume", 0.15),
            music.get("ducking", True),
        )

    # The persisted snapshot is the current state after a server restart.
    # Older in-memory history states are not stored by the current database.
    history = TimelineHistory(timeline)
    return history


def restore_timelines() -> None:
    for saved in database.load_timelines():
        timeline_id = saved["timeline_id"]
        video_id = saved.get("video_id")

        if video_id and video_id not in VIDEO_RECORDS:
            continue

        try:
            histories[timeline_id] = restore_history(saved["timeline"])
        except (ValueError, KeyError, TypeError) as error:
            print(f"Could not restore timeline {timeline_id}: {error}")


database.initialize_database()
restore_timelines()


# ============================================================
# COMMON HELPERS
# ============================================================

def get_history(timeline_id: str) -> TimelineHistory:
    history = histories.get(timeline_id)

    if history is None:
        raise HTTPException(
            status_code=404,
            detail="Timeline not found. Create it first.",
        )

    return history


def apply_edit(history, operation, *args):
    try:
        operation(*args)
        return history.current
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def save_and_respond(timeline_id: str, timeline: Timeline) -> dict:
    try:
        persist_timeline(timeline_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "status": "APPLIED",
        **timeline_response(timeline_id, timeline),
    }


# ============================================================
# NATURAL-LANGUAGE INSTRUCTIONS
# ============================================================

def interpret_edit_instruction(instruction: str, duration: float) -> dict:
    """Deterministically classify supported, unclear, and rejected requests."""
    text = re.sub(r"\s+", " ", instruction.strip().lower())

    if not text:
        return {
            "status": "NEEDS_CLARIFICATION",
            "message": "Please describe the edit you want.",
        }

    # The original video must never be permanently deleted.
    destructive_phrases = (
        "permanently delete",
        "delete permanently",
        "delete the entire original",
        "delete the original video",
        "destroy the original",
        "erase the original video",
    )

    if any(phrase in text for phrase in destructive_phrases):
        return {
            "status": "REJECTED",
            "message": (
                "Permanently deleting the original video is not supported. "
                "The original source must remain untouched."
            ),
        }

    first_match = re.fullmatch(
        r"(?:cut|remove|delete)\s+(?:the\s+)?first\s+"
        r"(\d+(?:\.\d+)?)\s+seconds?",
        text,
    )

    if first_match:
        end = float(first_match.group(1))

        if end <= 0 or end >= duration:
            return {
                "status": "REJECTED",
                "message": (
                    "The duration must be greater than zero and less "
                    f"than {duration:g} seconds."
                ),
            }

        return {
            "status": "APPLIED",
            "action": "cut",
            "start_s": 0.0,
            "end_s": end,
            "message": f"Remove the first {end:g} seconds.",
        }

    range_match = re.fullmatch(
        r"(?:cut|remove|delete)\s+"
        r"(?:(?:the\s+)?section\s+)?"
        r"(?:from\s+)?"
        r"(\d+(?:\.\d+)?)\s*(?:s|seconds?)?\s*"
        r"(?:to|-)\s*"
        r"(\d+(?:\.\d+)?)\s*(?:s|seconds?)?",
        text,
    )

    if range_match:
        start = float(range_match.group(1))
        end = float(range_match.group(2))

        if start < 0 or end <= start or end > duration:
            return {
                "status": "REJECTED",
                "message": (
                    "Choose a valid source-time range with the start "
                    "before the end and the end within the source video."
                ),
            }

        return {
            "status": "APPLIED",
            "action": "cut",
            "start_s": start,
            "end_s": end,
            "message": (
                f"Remove the source section from {start:g} "
                f"to {end:g} seconds."
            ),
        }

    return {
        "status": "NEEDS_CLARIFICATION",
        "message": (
            "I could not safely interpret that instruction. "
            "Please specify an edit and its time range. For example, "
            "'cut the first 10 seconds' or 'remove from 10 to 15 seconds'."
        ),
    }


def process_instruction(request: Instruction) -> dict:
    """Process one instruction and return its decision and timeline summaries."""
    history = get_history(request.timeline_id)
    current = history.current

    before = current.to_dict()
    before_hash = calculate_timeline_hash(before)
    before_summary = {
        "duration_s": before.get("duration_s"),
        "segment_count": len(before.get("segments", [])),
        "caption_count": len(before.get("captions", [])),
        "audio_edit_count": len(before.get("audio_edits", [])),
    }

    decision = interpret_edit_instruction(
        request.instruction,
        current.source_duration_s,
    )

    # Clarification and rejection must not change the timeline.
    if decision["status"] != "APPLIED":
        return {
            "timeline_id": request.timeline_id,
            "status": decision["status"],
            "message": decision["message"],
            "operations": [],
            "before_summary": before_summary,
            "after_summary": before_summary,
            "timeline": before,
            "timeline_hash": before_hash,
        }

    try:
        if decision["action"] == "cut":
            updated = history.apply_cut(
                decision["start_s"],
                decision["end_s"],
            )
        else:
            # Never report success for an operation that is not implemented.
            return {
                "timeline_id": request.timeline_id,
                "status": "REJECTED",
                "message": "This operation is not supported yet.",
                "operations": [],
                "before_summary": before_summary,
                "after_summary": before_summary,
                "timeline": before,
                "timeline_hash": before_hash,
            }

        persist_timeline(request.timeline_id)

        after = updated.to_dict()
        after_summary = {
            "duration_s": after.get("duration_s"),
            "segment_count": len(after.get("segments", [])),
            "caption_count": len(after.get("captions", [])),
            "audio_edit_count": len(after.get("audio_edits", [])),
        }

        return {
            "timeline_id": request.timeline_id,
            "status": "APPLIED",
            "action": decision["action"],
            "message": decision["message"],
            "operations": [
                {
                    "type": "cut",
                    "start_s": decision["start_s"],
                    "end_s": decision["end_s"],
                    "time_basis": "original_source",
                }
            ],
            "before_summary": before_summary,
            "after_summary": after_summary,
            "edit_range_source_s": [
                decision["start_s"],
                decision["end_s"],
            ],
            **timeline_response(request.timeline_id, updated),
        }

    except ValueError as error:
        unchanged = history.current.to_dict()
        return {
            "timeline_id": request.timeline_id,
            "status": "REJECTED",
            "message": str(error),
            "operations": [],
            "before_summary": before_summary,
            "after_summary": before_summary,
            "timeline": unchanged,
            "timeline_hash": calculate_timeline_hash(unchanged),
        }



def process_instruction(request: Instruction) -> dict:
    history = get_history(request.timeline_id)
    current = history.current

    decision = interpret_edit_instruction(
        request.instruction,
        current.source_duration_s,
    )

    if decision["status"] != "APPLIED":
        return {
            "timeline_id": request.timeline_id,
            **decision,
            "timeline": current.to_dict(),
            "timeline_hash": current.timeline_hash(),
        }

    try:
        if decision["action"] == "cut":
            updated = history.apply_cut(
                decision["start_s"],
                decision["end_s"],
            )
        else:
            raise ValueError("Unsupported operation")

        persist_timeline(request.timeline_id)

        return {
            "status": "APPLIED",
            "action": decision["action"],
            "message": decision["message"],
            "edit_range_source_s": [
                decision["start_s"],
                decision["end_s"],
            ],
            **timeline_response(request.timeline_id, updated),
        }

    except ValueError as error:
        return {
            "timeline_id": request.timeline_id,
            "status": "REJECTED",
            "message": str(error),
            "timeline": history.current.to_dict(),
            "timeline_hash": history.current.timeline_hash(),
        }


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "service": "ProofCut",
        "version": app.version,
        "persistence": "sqlite",
    }


@app.get("/api/v1/ready")
def ready():
    try:
        database.load_timelines()
        return {"status": "ready"}
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Persistence is unavailable",
        ) from error


# ============================================================
# TIMELINE CREATION AND RETRIEVAL
# ============================================================

@app.post("/api/v1/timeline/demo")
def create_demo():
    timeline = Timeline(120)
    timeline.add_caption("caption_1", "Welcome!", 42, 44)

    histories["demo"] = TimelineHistory(timeline)
    persist_timeline("demo")

    return {
        "status": "created",
        "timeline_id": "demo",
        **timeline_response("demo", timeline),
    }


@app.post("/api/v1/timeline/from-video/{video_id}")
def create_timeline_from_video(video_id: str):
    record = VIDEO_RECORDS.get(video_id)

    if record is None:
        raise HTTPException(status_code=404, detail="Video not found")

    metadata = record.get("metadata", {})
    format_info = metadata.get("format", {})

    try:
        duration = float(format_info["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail="Video duration is unavailable",
        ) from error

    if duration <= 0:
        raise HTTPException(
            status_code=422,
            detail="Video duration must be greater than zero",
        )

    timeline_id = f"video-{video_id}"
    timeline = Timeline(duration)
    histories[timeline_id] = TimelineHistory(timeline)
    persist_timeline(timeline_id)

    return {
        "status": "created",
        "video_id": video_id,
        "duration_seconds": duration,
        **timeline_response(timeline_id, timeline),
    }


@app.get("/api/v1/timeline/{timeline_id}")
def get_timeline(timeline_id: str):
    return timeline_response(timeline_id, get_history(timeline_id).current)


# ============================================================
# CUT
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/cut")
def cut_timeline(timeline_id: str, request: CutRequest):
    history = get_history(timeline_id)
    updated = apply_edit(
        history, history.apply_cut, request.start_s, request.end_s
    )
    return save_and_respond(timeline_id, updated)


# ============================================================
# KEEP ONLY A RANGE
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/keep")
def keep_timeline_range(timeline_id: str, request: KeepRequest):
    history = get_history(timeline_id)
    updated = apply_edit(
        history, history.apply_keep, request.start_s, request.end_s
    )
    return save_and_respond(timeline_id, updated)


# ============================================================
# REORDER SECTIONS
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/reorder")
def reorder_timeline(timeline_id: str, request: ReorderRequest):
    history = get_history(timeline_id)
    updated = apply_edit(history, history.apply_reorder, request.ranges)
    return save_and_respond(timeline_id, updated)


# ============================================================
# PLAYBACK SPEED
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/speed")
def change_timeline_speed(timeline_id: str, request: SpeedRequest):
    history = get_history(timeline_id)
    updated = apply_edit(
        history,
        history.apply_speed,
        request.start_s,
        request.end_s,
        request.speed,
    )
    return save_and_respond(timeline_id, updated)


# ============================================================
# CAPTIONS
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/captions")
def add_timeline_caption(timeline_id: str, request: CaptionCreate):
    history = get_history(timeline_id)
    updated = apply_edit(
        history,
        history.add_caption,
        request.caption_id,
        request.text,
        request.start_s,
        request.end_s,
        request.color,
    )
    return save_and_respond(timeline_id, updated)


@app.patch("/api/v1/timeline/{timeline_id}/captions/{caption_id}")
def update_timeline_caption(
    timeline_id: str,
    caption_id: str,
    request: CaptionUpdate,
):
    history = get_history(timeline_id)
    updated = apply_edit(
        history,
        history.update_caption,
        caption_id,
        request.text,
        request.start_s,
        request.end_s,
        request.color,
    )
    return save_and_respond(timeline_id, updated)


@app.delete("/api/v1/timeline/{timeline_id}/captions/{caption_id}")
def delete_timeline_caption(timeline_id: str, caption_id: str):
    history = get_history(timeline_id)
    updated = apply_edit(history, history.delete_caption, caption_id)
    return save_and_respond(timeline_id, updated)


# ============================================================
# MUTE / BLEEP METADATA
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/audio-edits")
def add_audio_edit(timeline_id: str, request: AudioEditRequest):
    if request.action not in {"mute", "bleep"}:
        raise HTTPException(
            status_code=422,
            detail="Action must be 'mute' or 'bleep'",
        )

    history = get_history(timeline_id)
    updated = apply_edit(
        history,
        history.add_audio_edit,
        request.edit_id,
        request.action,
        request.start_s,
        request.end_s,
        request.word,
    )
    return save_and_respond(timeline_id, updated)


@app.delete("/api/v1/timeline/{timeline_id}/audio-edits/{edit_id}")
def delete_audio_edit(timeline_id: str, edit_id: str):
    history = get_history(timeline_id)
    updated = history.current

    try:
        updated.remove_audio_edit(edit_id)
        history._commit(updated)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return save_and_respond(timeline_id, history.current)


# ============================================================
# BACKGROUND MUSIC SETTINGS
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/music")
def set_timeline_music(timeline_id: str, request: MusicRequest):
    history = get_history(timeline_id)
    updated = apply_edit(
        history,
        history.set_background_music,
        request.file_path,
        request.volume,
        request.ducking,
    )
    return save_and_respond(timeline_id, updated)


@app.delete("/api/v1/timeline/{timeline_id}/music")
def remove_timeline_music(timeline_id: str):
    history = get_history(timeline_id)
    updated = history.current
    updated.remove_background_music()
    history._commit(updated)
    return save_and_respond(timeline_id, history.current)


# ============================================================
# NATURAL-LANGUAGE INSTRUCTIONS
# ============================================================

@app.post("/api/v1/timeline/instructions")
def edit_timeline(request: Instruction):
    return process_instruction(request)


@app.post("/api/v1/instructions")
def edit_timeline_alias(request: Instruction):
    return process_instruction(request)


# ============================================================
# UNDO / REDO
# ============================================================

@app.post("/api/v1/timeline/undo")
def undo(timeline_id: str = "demo"):
    history = get_history(timeline_id)

    try:
        updated = history.undo()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return save_and_respond(timeline_id, updated)


@app.post("/api/v1/timeline/redo")
def redo(timeline_id: str = "demo"):
    history = get_history(timeline_id)

    try:
        updated = history.redo()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return save_and_respond(timeline_id, updated)


# ============================================================
# RESTORE AN EARLIER IN-MEMORY HISTORY STATE
# ============================================================

@app.post("/api/v1/timeline/{timeline_id}/restore")
def restore_timeline_state(
    timeline_id: str,
    request: RestoreRequest,
):
    history = get_history(timeline_id)

    try:
        updated = history.restore_state(request.state_index)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return save_and_respond(timeline_id, updated)
