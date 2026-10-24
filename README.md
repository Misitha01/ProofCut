# ProofCut

ProofCut is a video-editing API and application that represents edits as a timeline and renders edited video to a separate output file.

## Requirements
- Python and pip
- Node.js and npm
- FFmpeg and FFprobe

## Run the backend
Activate your Python virtual environment and run:

    uvicorn backend.app.main:app --reload --port 8000

API documentation: http://127.0.0.1:8000/docs

## Run the frontend

    cd frontend
    npm install
    npm run dev

## Testing
Run `python -m pytest -q` from the project root.

## Limitations
Refer to REPORT.md for features requiring further verification.
