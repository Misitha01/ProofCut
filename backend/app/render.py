
from pathlib import Path
from uuid import uuid4
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import math
import re
import subprocess

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse

from backend.app.video import VIDEO_PATHS, VIDEO_RECORDS
from backend.app import database

router = APIRouter(prefix="/api/v1", tags=["Rendering"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RENDER_DIR = PROJECT_ROOT / "storage" / "renders"
RENDER_DIR.mkdir(parents=True, exist_ok=True)

RENDER_RECORDS = {}
RENDER_JOBS = {}
IDEMPOTENCY_RECORDS = {}
JOB_LOCK = Lock()
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2)

MAX_SEGMENTS = 100
MAX_RENDER_SECONDS = 1800


def restore_render_records():
    """Restore render metadata for output files that still exist."""
    RENDER_RECORDS.clear()

    for item in database.load_renders():
        output_path = Path(item["file_path"])
        if not output_path.is_file():
            continue

        RENDER_RECORDS[item["render_id"]] = {
            "render_id": item["render_id"],
            "timeline_id": item["timeline_id"],
            "filename": item["filename"],
            "file_path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "status": item["status"],
        }


database.initialize_database()
restore_render_records()


def _number(value, name):
    """Convert a value to a finite float."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc

    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")

    return result


def _atempo_filters(speed):
    """Build FFmpeg audio speed filters for any positive speed."""
    remaining = speed
    filters = []

    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0

    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5

    if not math.isclose(remaining, 1.0, rel_tol=1e-9):
        filters.append(f"atempo={remaining:.12g}")

    return filters


def _filter_path(path):
    """Escape a file path for FFmpeg filter arguments."""
    value = Path(path).resolve().as_posix()
    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    return f"'{value}'"


def _validate_segments(segments, source_duration):
    """Validate source ranges while preserving output order."""
    if not isinstance(segments, list) or not segments:
        raise ValueError("The timeline has no renderable segments")

    if len(segments) > MAX_SEGMENTS:
        raise ValueError(
            f"A timeline may contain at most {MAX_SEGMENTS} segments"
        )

    validated = []

    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Invalid timeline segment")

        start = _number(segment.get("source_start_s"), "Segment start")
        end = _number(segment.get("source_end_s"), "Segment end")
        speed = _number(segment.get("speed"), "Segment speed")

        if not 0 <= start < end <= source_duration:
            raise ValueError(
                "Segment boundaries are outside the source video"
            )

        if speed <= 0:
            raise ValueError("Segment speed must be positive")

        validated.append({
            "source_start_s": start,
            "source_end_s": end,
            "speed": speed,
        })

    source_order = sorted(
        validated,
        key=lambda segment: segment["source_start_s"],
    )

    for previous, current in zip(source_order, source_order[1:]):
        if current["source_start_s"] < previous["source_end_s"]:
            raise ValueError(
                "Timeline segments must not overlap in source time"
            )

    duration = sum(
        (segment["source_end_s"] - segment["source_start_s"])
        / segment["speed"]
        for segment in validated
    )

    if duration <= 0:
        raise ValueError("The rendered duration must be positive")

    return validated, duration


def _has_audio(video_id):
    """Check whether the uploaded video has an audio stream."""
    record = VIDEO_RECORDS.get(video_id, {})
    streams = record.get("metadata", {}).get("streams", [])

    return any(
        stream.get("codec_type") == "audio"
        for stream in streams
    )


def _build_filter_graph(
    segments,
    has_audio,
    captions,
    temp_dir,
    audio_edits=None,
):
    """Build FFmpeg filters for segments, captions, and audio edits."""
    filters = []
    concat_inputs = []
    audio_edits = audio_edits or []

    for index, segment in enumerate(segments):
        start = segment["source_start_s"]
        end = segment["source_end_s"]
        speed = segment["speed"]

        filters.append(
            f"[{index}:v:0]"
            f"trim=start={start:.12g}:end={end:.12g},"
            f"setpts=(PTS-STARTPTS)/{speed:.12g},"
            f"setsar=1[v{index}]"
        )

        if not has_audio:
            concat_inputs.append(f"[v{index}]")
            continue

        audio_filters = _atempo_filters(speed)
        audio_chain = ",".join(audio_filters) if audio_filters else "anull"

        filters.append(
            f"[{index}:a:0]"
            f"atrim=start={start:.12g}:end={end:.12g},"
            f"asetpts=PTS-STARTPTS,"
            f"{audio_chain}[abase{index}]"
        )

        current_audio = f"abase{index}"
        output_duration = (end - start) / speed

        for edit_index, edit in enumerate(audio_edits):
            if not isinstance(edit, dict):
                continue

            action = edit.get("action")
            if action not in {"mute", "bleep"}:
                continue

            try:
                edit_start = _number(
                    edit.get("anchor_start_s"), "Audio edit start"
                )
                edit_end = _number(
                    edit.get("anchor_end_s"), "Audio edit end"
                )
            except ValueError:
                continue

            if edit_start < 0 or edit_end <= edit_start:
                continue

            overlap_start = max(start, edit_start)
            overlap_end = min(end, edit_end)

            if overlap_start >= overlap_end:
                continue

            local_start = (overlap_start - start) / speed
            local_end = (overlap_end - start) / speed

            if action == "mute":
                next_audio = f"amute{index}_{edit_index}"
                filters.append(
                    f"[{current_audio}]volume=0:"
                    f"enable='between(t,{local_start:.12g},"
                    f"{local_end:.12g})'[{next_audio}]"
                )
                current_audio = next_audio

            else:
                muted_audio = f"ableepmute{index}_{edit_index}"
                filters.append(
                    f"[{current_audio}]volume=0:"
                    f"enable='between(t,{local_start:.12g},"
                    f"{local_end:.12g})'[{muted_audio}]"
                )

                beep_audio = f"beepsrc{index}_{edit_index}"
                filters.append(
                    f"aevalsrc=0.25*sin(2*PI*1000*t):"
                    f"s=48000:d={output_duration:.12g},"
                    f"aformat=sample_rates=48000:"
                    f"channel_layouts=stereo,"
                    f"volume=1:"
                    f"enable='between(t,{local_start:.12g},"
                    f"{local_end:.12g})'[{beep_audio}]"
                )

                mixed_audio = f"ableep{index}_{edit_index}"
                filters.append(
                    f"[{muted_audio}][{beep_audio}]"
                    f"amix=inputs=2:duration=first:normalize=0"
                    f"[{mixed_audio}]"
                )
                current_audio = mixed_audio

        concat_inputs.extend([f"[v{index}]", f"[{current_audio}]"])

    if has_audio:
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(segments)}:v=1:a=1[vcat][acat]"
        )
    else:
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(segments)}:v=1:a=0[vcat]"
        )

    video_label = "vcat"
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font_option = (
        f":fontfile={_filter_path(font_path)}"
        if font_path.is_file()
        else ""
    )

    for caption_index, caption in enumerate(captions or []):
        if not isinstance(caption, dict):
            continue

        text = caption.get("text")
        color = caption.get("color", "#FFFFFF")

        if not isinstance(text, str) or not text.strip():
            continue

        if not isinstance(color, str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", color
        ):
            color = "#FFFFFF"

        ranges = caption.get("output_ranges_s")
        if ranges is None:
            single_range = caption.get("output_range_s")
            ranges = [single_range] if single_range else []

        for range_index, time_range in enumerate(ranges):
            if (
                not isinstance(time_range, (list, tuple))
                or len(time_range) != 2
            ):
                continue

            try:
                caption_start = _number(time_range[0], "Caption start")
                caption_end = _number(time_range[1], "Caption end")
            except ValueError:
                continue

            if caption_start < 0 or caption_end <= caption_start:
                continue

            text_path = Path(temp_dir) / (
                f"caption_{caption_index}_{range_index}.txt"
            )
            text_path.write_text(text, encoding="utf-8")

            next_label = f"vcap{caption_index}_{range_index}"
            filters.append(
                f"[{video_label}]drawtext="
                f"textfile={_filter_path(text_path)}"
                f"{font_option}:fontsize=32:fontcolor={color}:"
                f"x=(w-text_w)/2:y=h-text_h-40:"
                f"box=1:boxcolor=black@0.45:boxborderw=10:"
                f"enable='between(t,{caption_start:.12g},"
                f"{caption_end:.12g})'[{next_label}]"
            )
            video_label = next_label

    filters.append(f"[{video_label}]null[vout]")

    if has_audio:
        filters.append("[acat]anull[aout]")

    return ";".join(filters)


def _perform_render(timeline_id: str):
    """Perform FFmpeg rendering in a worker thread."""
    from backend.app.main import histories

    history = histories.get(timeline_id)
    if history is None:
        raise HTTPException(
            status_code=404,
            detail="Timeline not found. Create the timeline first.",
        )

    if not timeline_id.startswith("video-"):
        raise HTTPException(
            status_code=422,
            detail="Rendering requires a timeline created from an uploaded video.",
        )

    video_id = timeline_id[len("video-"):]
    source_path = VIDEO_PATHS.get(video_id)

    if source_path is None or not source_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Source video not found. Upload it again.",
        )

    timeline = history.current.to_dict()

    try:
        source_duration = _number(
            timeline.get("source_duration_s"), "Source duration"
        )
        segments, expected_duration = _validate_segments(
            timeline.get("segments"), source_duration
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if expected_duration > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="The rendered video cannot exceed 24 hours.",
        )

    has_audio = _has_audio(video_id)
    captions = timeline.get("captions", [])
    audio_edits = timeline.get("audio_edits", [])

    render_id = str(uuid4())
    output_path = RENDER_DIR / f"{render_id}.mp4"

    try:
        with TemporaryDirectory(
            prefix=f"proofcut_{render_id}_",
            dir=RENDER_DIR,
        ) as temp_dir:
            filter_graph = _build_filter_graph(
                segments, has_audio, captions, temp_dir, audio_edits
            )

            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

            for _ in segments:
                command.extend(["-i", str(source_path)])

            command.extend(["-filter_complex", filter_graph, "-map", "[vout]"])

            if has_audio:
                command.extend(["-map", "[aout]"])

            command.extend([
                "-c:v", "libx264", "-preset", "medium", "-crf", "23"
            ])

            if has_audio:
                command.extend(["-c:a", "aac"])

            command.extend(["-movflags", "+faststart", str(output_path)])

            subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=MAX_RENDER_SECONDS,
                check=True,
            )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg did not create the output video.",
            )

        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        try:
            output_duration = float(probe.stdout.strip())
        except (ValueError, TypeError) as error:
            raise ValueError(
                "FFprobe returned an invalid output duration."
            ) from error

        if not math.isfinite(output_duration) or output_duration <= 0:
            raise ValueError("The rendered video has an invalid duration.")

        record = {
            "render_id": render_id,
            "timeline_id": timeline_id,
            "filename": output_path.name,
            "file_path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "status": "completed",
        }

        database.save_render(
            render_id=render_id,
            timeline_id=timeline_id,
            filename=output_path.name,
            file_path=str(output_path),
            status="completed",
        )
        RENDER_RECORDS[render_id] = record

        return {
            "render_id": render_id,
            "timeline_id": timeline_id,
            "filename": output_path.name,
            "size_bytes": record["size_bytes"],
            "duration_seconds": output_duration,
            "status": "completed",
            "download_url": f"/api/v1/renders/{render_id}",
        }

    except HTTPException:
        output_path.unlink(missing_ok=True)
        raise

    except FileNotFoundError as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail=f"FFmpeg, FFprobe, or a required file is unavailable: {str(error)[:300]}",
        ) from error

    except subprocess.TimeoutExpired as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=504,
            detail="Rendering or output validation timed out.",
        ) from error

    except subprocess.CalledProcessError as error:
        output_path.unlink(missing_ok=True)
        message = (error.stderr or "Unknown FFmpeg error")[-1500:]
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg rendering failed: {message}",
        ) from error

    except Exception as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Rendering failed: {str(error)[:500]}",
        ) from error


def _run_render_job(job_id: str, timeline_id: str):
    """Execute a render and record its final status."""
    with JOB_LOCK:
        RENDER_JOBS[job_id]["status"] = "running"

    try:
        result = _perform_render(timeline_id)
        with JOB_LOCK:
            RENDER_JOBS[job_id].update(status="completed", result=result)

    except HTTPException as exc:
        with JOB_LOCK:
            RENDER_JOBS[job_id].update(
                status="failed",
                error={
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                },
            )

    except Exception as exc:
        with JOB_LOCK:
            RENDER_JOBS[job_id].update(
                status="failed",
                error={"status_code": 500, "detail": str(exc)[:500]},
            )


@router.post("/timeline/{timeline_id}/render", status_code=202)
def render_timeline(
    timeline_id: str,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    """Start a render job; repeated keyed requests reuse the same job."""
    job_key = (
        f"render:{timeline_id}:{idempotency_key}"
        if idempotency_key
        else None
    )

    with JOB_LOCK:
        if job_key and job_key in IDEMPOTENCY_RECORDS:
            existing_job_id = IDEMPOTENCY_RECORDS[job_key]
            existing_job = RENDER_JOBS.get(existing_job_id)

            if existing_job is not None:
                return JSONResponse(
                    status_code=202,
                    content={
                        **existing_job,
                        "status_url": f"/api/v1/jobs/{existing_job_id}",
                    },
                )

        job_id = str(uuid4())
        RENDER_JOBS[job_id] = {
            "job_id": job_id,
            "timeline_id": timeline_id,
            "status": "queued",
        }

        if job_key:
            IDEMPOTENCY_RECORDS[job_key] = job_id

    try:
        JOB_EXECUTOR.submit(_run_render_job, job_id, timeline_id)
    except Exception as exc:
        with JOB_LOCK:
            RENDER_JOBS[job_id].update(
                status="failed",
                error={"status_code": 500, "detail": str(exc)[:500]},
            )

    with JOB_LOCK:
        job = dict(RENDER_JOBS[job_id])

    return JSONResponse(
        status_code=202,
        content={
            **job,
            "status_url": f"/api/v1/jobs/{job_id}",
        },
    )


@router.get("/jobs/{job_id}")
def get_render_job(job_id: str):
    """Get the current status and result of a render job."""
    with JOB_LOCK:
        job = RENDER_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job)


@router.get("/renders")
def list_renders():
    """List render records currently available on disk."""
    available = [
        record for record in RENDER_RECORDS.values()
        if Path(record["file_path"]).is_file()
    ]
    return {"renders": available, "count": len(available)}


@router.get("/renders/{render_id}")
def download_render(render_id: str):
    """Download a completed rendered video."""
    record = RENDER_RECORDS.get(render_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Render not found. Render the video first.",
        )

    output_path = Path(
        record.get("file_path", RENDER_DIR / record["filename"])
    )

    if not output_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Rendered video file is missing.",
        )

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=output_path.name,
        content_disposition_type="attachment",
    )
