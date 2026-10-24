
from pathlib import Path
from uuid import uuid4
import json
import subprocess

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from backend.app import database

router = APIRouter(
    prefix="/api/v1/videos",
    tags=["Videos"],
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = PROJECT_ROOT / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".mp4", ".mov"}
MAX_FILE_SIZE = 500 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

# In-memory caches, restored from SQLite on startup.
VIDEO_RECORDS = {}
VIDEO_PATHS = {}


def restore_videos() -> None:
    """Restore uploaded-video metadata from SQLite."""
    VIDEO_RECORDS.clear()
    VIDEO_PATHS.clear()

    for item in database.load_videos():
        video_id = item["video_id"]
        saved_metadata = item.get("metadata", {})

        # Also tolerate older records that contain raw FFprobe metadata.
        record = {
            "original_filename": item["filename"],
            "size_bytes": saved_metadata.get("size_bytes", 0),
            "metadata": saved_metadata.get("metadata", saved_metadata),
            "status": saved_metadata.get("status", "uploaded"),
        }

        VIDEO_RECORDS[video_id] = record
        VIDEO_PATHS[video_id] = Path(item["file_path"])


database.initialize_database()
restore_videos()


def remove_video(video_id: str, video_path: Path) -> None:
    """Remove a failed upload and its cached records."""
    VIDEO_RECORDS.pop(video_id, None)
    VIDEO_PATHS.pop(video_id, None)
    video_path.unlink(missing_ok=True)


@router.post("/upload", status_code=201)
async def upload_video(file: UploadFile = File(...)):
    """Upload a video, inspect it, and persist its metadata."""
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail="Only MP4 and MOV videos are supported.",
        )

    video_id = str(uuid4())
    video_path = UPLOAD_DIR / f"{video_id}{extension}"
    size = 0

    try:
        with video_path.open("wb") as output:
            while True:
                chunk = await file.read(CHUNK_SIZE)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 500 MB upload limit.",
                    )

                output.write(chunk)

        if size == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded video is empty.",
            )

        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries",
                (
                    "format=duration,format_name,size:"
                    "stream=codec_type,codec_name,width,height,"
                    "r_frame_rate"
                ),
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        metadata = json.loads(result.stdout)
        streams = metadata.get("streams", [])

        if not any(
            stream.get("codec_type") == "video"
            for stream in streams
        ):
            raise HTTPException(
                status_code=400,
                detail="The uploaded file contains no video stream.",
            )

        record = {
            "original_filename": Path(filename).name,
            "size_bytes": size,
            "metadata": metadata,
            "status": "uploaded",
        }

        # Persist before reporting success.
        database.save_video(
            video_id=video_id,
            filename=record["original_filename"],
            file_path=str(video_path),
            metadata={
                "size_bytes": size,
                "metadata": metadata,
                "status": "uploaded",
            },
        )

        VIDEO_RECORDS[video_id] = record
        VIDEO_PATHS[video_id] = video_path

        return {"video_id": video_id, **record}

    except HTTPException:
        remove_video(video_id, video_path)
        raise

    except FileNotFoundError:
        remove_video(video_id, video_path)
        raise HTTPException(
            status_code=503,
            detail=(
                "FFprobe is unavailable. Install FFmpeg and "
                "add its bin directory to Windows PATH."
            ),
        )

    except subprocess.TimeoutExpired:
        remove_video(video_id, video_path)
        raise HTTPException(
            status_code=504,
            detail="Video metadata inspection timed out.",
        )

    except subprocess.CalledProcessError:
        remove_video(video_id, video_path)
        raise HTTPException(
            status_code=400,
            detail="FFprobe could not read this video file.",
        )

    except json.JSONDecodeError:
        remove_video(video_id, video_path)
        raise HTTPException(
            status_code=500,
            detail="FFprobe returned invalid metadata.",
        )

    except Exception as error:
        remove_video(video_id, video_path)
        raise HTTPException(
            status_code=500,
            detail=f"Video upload or metadata inspection failed: {error}",
        ) from error

    finally:
        await file.close()


@router.get("/")
def list_videos():
    """List uploaded videos, including records restored from SQLite."""
    return {
        "videos": [
            {"video_id": video_id, **record}
            for video_id, record in VIDEO_RECORDS.items()
        ],
        "count": len(VIDEO_RECORDS),
    }


@router.get("/{video_id}")
def get_video(video_id: str):
    """Retrieve metadata for one uploaded video."""
    record = VIDEO_RECORDS.get(video_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Video not found.",
        )

    return {"video_id": video_id, **record}


@router.get("/{video_id}/file")
def get_video_file(video_id: str):
    """Return the uploaded video for browser playback."""
    video_path = VIDEO_PATHS.get(video_id)

    if video_path is None or not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="The uploaded video file is missing from storage.",
        )

    media_type = (
        "video/mp4"
        if video_path.suffix.lower() == ".mp4"
        else "video/quicktime"
    )

    return FileResponse(
        path=video_path,
        media_type=media_type,
        filename=video_path.name,
        content_disposition_type="inline",
    )
