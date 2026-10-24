import { useEffect, useState } from "react";
import "./App.css";

const API = "/api/v1";

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  const contentType = response.headers.get("content-type") || "";

  return contentType.includes("application/json")
    ? response.json()
    : response.blob();
}

function formatTime(seconds = 0) {
  const value = Number(seconds) || 0;
  const minutes = Math.floor(value / 60);
  const remaining = Math.floor(value % 60);
  const milliseconds = Math.floor((value % 1) * 100);

  return `${String(minutes).padStart(2, "0")}:${String(
    remaining
  ).padStart(2, "0")}.${String(milliseconds).padStart(2, "0")}`;
}

function getTimeline(data) {
  return data?.timeline || data;
}

export default function App() {
  const [file, setFile] = useState(null);
  const [videoId, setVideoId] = useState("");
  const [timelineId, setTimelineId] = useState("");
  const [timeline, setTimeline] = useState(null);

  const [instruction, setInstruction] = useState("");
  const [cutStart, setCutStart] = useState("10");
  const [cutEnd, setCutEnd] = useState("15");
  const [speed, setSpeed] = useState("1.5");

  const [captionText, setCaptionText] = useState("My caption");
  const [captionStart, setCaptionStart] = useState("2");
  const [captionEnd, setCaptionEnd] = useState("5");

  const [renders, setRenders] = useState([]);
  const [previewUrl, setPreviewUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Ready to edit");
  const [error, setError] = useState("");

  useEffect(() => {
    loadRenders();

    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
    // Load the initial render history only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadRenders() {
    try {
      const data = await request("/renders");
      setRenders(Array.isArray(data) ? data : data.renders || []);
    } catch {
      // The render list can load once the backend is running.
    }
  }

  async function runAction(action, successMessage) {
    setBusy(true);
    setError("");

    try {
      const result = await action();
      if (successMessage) setStatus(successMessage);
      return result;
    } catch (err) {
      setError(err.message || "Something went wrong.");
      setStatus("Action failed");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function uploadVideo() {
    if (!file) {
      setError("Please choose a video file first.");
      return;
    }

    const result = await runAction(async () => {
      const formData = new FormData();
      formData.append("file", file);

      const uploaded = await request("/videos/upload", {
        method: "POST",
        body: formData,
      });

      const id = uploaded.video_id || uploaded.id;

      if (!id) {
        throw new Error("Upload succeeded, but no video ID was returned.");
      }

      setVideoId(id);

      const timelineResult = await request(
        `/timeline/from-video/${encodeURIComponent(id)}`,
        { method: "POST" }
      );

      const newTimelineId =
        timelineResult.timeline_id ||
        timelineResult.id ||
        `video-${id}`;

      setTimelineId(newTimelineId);
      setTimeline(getTimeline(timelineResult));

      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(file));

      await loadRenders();
      return true;
    }, "Video uploaded successfully");

    if (result) {
      setInstruction("");
    }
  }

  async function createDemo() {
    await runAction(async () => {
      const data = await request("/timeline/demo", {
        method: "POST",
      });

      const id = data.timeline_id || data.id || "demo";

      setTimelineId(id);
      setTimeline(getTimeline(data));
      setVideoId("");
      setFile(null);

      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl("");

      return data;
    }, "Demo timeline created");
  }

  async function refreshTimeline() {
    if (!timelineId) return;

    const data = await request(
      `/timeline/${encodeURIComponent(timelineId)}`
    );

    setTimeline(getTimeline(data));
  }

  async function applyCut() {
    if (!timelineId) {
      setError("Upload a video or create a demo timeline first.");
      return;
    }

    const start = Number(cutStart);
    const end = Number(cutEnd);

    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
      setError("Enter a valid start time and an end time greater than it.");
      return;
    }

    await runAction(async () => {
      await request(`/timeline/${encodeURIComponent(timelineId)}/cut`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_s: start, end_s: end }),
      });

      await refreshTimeline();
    }, "Cut applied");
  }

  async function applySpeed() {
    if (!timelineId) {
      setError("Upload a video or create a demo timeline first.");
      return;
    }

    const start = Number(cutStart);
    const end = Number(cutEnd);
    const speedValue = Number(speed);

    if (
      start < 0 ||
      end <= start ||
      !Number.isFinite(speedValue) ||
      speedValue <= 0 ||
      speedValue > 16
    ) {
      setError("Check the time range and speed. Speed must be above 0 and at most 16.");
      return;
    }

    await runAction(async () => {
      await request(`/timeline/${encodeURIComponent(timelineId)}/speed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_s: start,
          end_s: end,
          speed: speedValue,
        }),
      });

      await refreshTimeline();
    }, "Playback speed updated");
  }

  async function applyInstruction() {
    if (!timelineId) {
      setError("Upload a video or create a demo timeline first.");
      return;
    }

    if (!instruction.trim()) {
      setError("Type an editing instruction first.");
      return;
    }

    await runAction(async () => {
      const data = await request("/timeline/instructions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction: instruction.trim(),
          timeline_id: timelineId,
        }),
      });

      setTimeline(getTimeline(data));
      setStatus(
        `${data.status || "Completed"}: ${data.message || "Instruction processed"}`
      );
    });
  }

  async function addCaption() {
    if (!timelineId) {
      setError("Upload a video or create a demo timeline first.");
      return;
    }

    const start = Number(captionStart);
    const end = Number(captionEnd);

    if (!captionText.trim() || start < 0 || end <= start) {
      setError("Enter caption text and a valid time range.");
      return;
    }

    await runAction(async () => {
      await request(
        `/timeline/${encodeURIComponent(timelineId)}/captions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            caption_id: `caption_${Date.now()}`,
            text: captionText.trim(),
            start_s: start,
            end_s: end,
            color: "#FFFFFF",
          }),
        }
      );

      await refreshTimeline();
    }, "Caption added");
  }

  async function changeHistory(direction) {
    if (!timelineId) {
      setError("Create a timeline first.");
      return;
    }

    await runAction(async () => {
      await request(
        `/timeline/${direction}?timeline_id=${encodeURIComponent(timelineId)}`,
        { method: "POST" }
      );

      await refreshTimeline();
    }, direction === "undo" ? "Last edit undone" : "Edit restored");
  }

  async function renderVideo() {
    if (!timelineId) {
      setError("Create a timeline first.");
      return;
    }

    await runAction(async () => {
      await request(`/timeline/${encodeURIComponent(timelineId)}/render`, {
        method: "POST",
      });

      await loadRenders();
    }, "Render request completed");
  }

  async function downloadRender(renderId) {
    await runAction(async () => {
      const blob = await request(
        `/renders/${encodeURIComponent(renderId)}`
      );

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${renderId}.mp4`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }, "Download started");
  }

  const segments = timeline?.segments || [];
  const captions = timeline?.captions || [];
  const duration =
    timeline?.duration_s ??
    timeline?.duration_seconds ??
    timeline?.source_duration_s ??
    0;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div>
            <h1>ProofCut</h1>
            <p>Precise editing. Verifiable output.</p>
          </div>
        </div>

        <div className="topbar-right">
          <span className="status-dot" />
          <span>{busy ? "Processing" : "Studio ready"}</span>
          <button className="secondary-button" onClick={createDemo} disabled={busy}>
            Try demo
          </button>
        </div>
      </header>

      <section className="welcome">
        <div>
          <p className="eyebrow">VIDEO WORKSPACE / 01</p>
          <h2>Make every edit <span>intentional.</span></h2>
          <p className="welcome-copy">
            Upload a video, describe the edit, review your timeline, and render
            the result.
          </p>
        </div>
        <div className="project-chip">
          <span>PROJECT</span>
          <strong>{timelineId ? timelineId.slice(0, 18) : "Untitled session"}</strong>
        </div>
      </section>

      {error && (
        <div className="alert error-alert">
          <span>{error}</span>
          <button onClick={() => setError("")}>Dismiss</button>
        </div>
      )}

      <div className="alert status-alert">
        <span className="status-dot" />
        <span>{status}</span>
        {busy && <span className="loading-label">Please wait…</span>}
      </div>

      <section className="workspace">
        <div className="panel preview-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">01 / SOURCE</p>
              <h3>Video preview</h3>
            </div>
            <span className="mini-label">{file ? "LOCAL FILE" : "NO SOURCE"}</span>
          </div>

          <div className="video-stage">
            {previewUrl ? (
              <video src={previewUrl} controls />
            ) : (
              <div className="empty-preview">
                <div className="play-icon">▶</div>
                <h4>Your footage goes here</h4>
                <p>Choose a video to start editing, or try the demo timeline.</p>
              </div>
            )}
          </div>

          <label className="upload-zone">
            <input
              type="file"
              accept="video/mp4,video/quicktime,.mp4,.mov"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
            <span className="upload-icon">↑</span>
            <span className="upload-text">
              <strong>{file ? file.name : "Choose a video file"}</strong>
              <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : "MP4 or MOV · up to 500 MB"}</small>
            </span>
          </label>

          <button
            className="primary-button full-button"
            onClick={uploadVideo}
            disabled={busy || !file}
          >
            {busy ? "Working…" : "Upload and create timeline"} <span>↗</span>
          </button>

          {videoId && (
            <p className="metadata-line">
              Video ID: <code>{videoId}</code>
            </p>
          )}
        </div>

        <div className="panel edit-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">02 / INSTRUCTIONS</p>
              <h3>Editing assistant</h3>
            </div>
            <span className="ai-tag">RULE-BASED</span>
          </div>

          <p className="section-description">
            Describe a supported edit in plain English. You can start with a
            cut instruction.
          </p>

          <label className="field-label" htmlFor="instruction">
            YOUR INSTRUCTION
          </label>
          <textarea
            id="instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="e.g. Remove from 10 to 15 seconds"
            rows={3}
          />

          <div className="example-row">
            <button
              className="example-chip"
              onClick={() => setInstruction("remove from 10 to 15 seconds")}
            >
              Remove 10–15s
            </button>
            <button
              className="example-chip"
              onClick={() => setInstruction("remove the first 5 seconds")}
            >
              Remove first 5s
            </button>
          </div>

          <button
            className="primary-button full-button"
            onClick={applyInstruction}
            disabled={busy || !timelineId}
          >
            Apply instruction <span>↗</span>
          </button>

          <div className="subsection">
            <h4>Manual cut</h4>
            <p className="section-description">
              Remove a section from the source video.
            </p>
            <div className="two-fields">
              <div>
                <label className="field-label" htmlFor="cutStart">START (SEC)</label>
                <input
                  id="cutStart"
                  type="number"
                  min="0"
                  value={cutStart}
                  onChange={(event) => setCutStart(event.target.value)}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="cutEnd">END (SEC)</label>
                <input
                  id="cutEnd"
                  type="number"
                  min="0"
                  value={cutEnd}
                  onChange={(event) => setCutEnd(event.target.value)}
                />
              </div>
            </div>
            <button
              className="secondary-button full-button"
              onClick={applyCut}
              disabled={busy || !timelineId}
            >
              Apply cut
            </button>
          </div>

          <div className="subsection">
            <h4>Change playback speed</h4>
            <div className="inline-control">
              <label className="field-label" htmlFor="speed">SPEED</label>
              <select
                id="speed"
                value={speed}
                onChange={(event) => setSpeed(event.target.value)}
              >
                <option value="0.5">0.5× Slow</option>
                <option value="0.75">0.75×</option>
                <option value="1">1× Normal</option>
                <option value="1.25">1.25×</option>
                <option value="1.5">1.5×</option>
                <option value="2">2× Fast</option>
              </select>
              <button
                className="secondary-button"
                onClick={applySpeed}
                disabled={busy || !timelineId}
              >
                Apply
              </button>
            </div>
            <p className="hint">Speed uses the start and end times above.</p>
          </div>

          <div className="subsection">
            <h4>Add a caption</h4>
            <label className="field-label" htmlFor="captionText">CAPTION TEXT</label>
            <input
              id="captionText"
              value={captionText}
              onChange={(event) => setCaptionText(event.target.value)}
              placeholder="Type your caption"
            />
            <div className="two-fields">
              <div>
                <label className="field-label" htmlFor="captionStart">START (SEC)</label>
                <input
                  id="captionStart"
                  type="number"
                  min="0"
                  value={captionStart}
                  onChange={(event) => setCaptionStart(event.target.value)}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="captionEnd">END (SEC)</label>
                <input
                  id="captionEnd"
                  type="number"
                  min="0"
                  value={captionEnd}
                  onChange={(event) => setCaptionEnd(event.target.value)}
                />
              </div>
            </div>
            <button
              className="secondary-button full-button"
              onClick={addCaption}
              disabled={busy || !timelineId}
            >
              + Add caption
            </button>
          </div>
        </div>
      </section>

      <section className="panel timeline-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">03 / ARRANGEMENT</p>
            <h3>Timeline overview</h3>
          </div>
          <div className="timeline-actions">
            <button
              className="secondary-button"
              onClick={() => changeHistory("undo")}
              disabled={busy || !timelineId}
            >
              ↶ Undo
            </button>
            <button
              className="secondary-button"
              onClick={() => changeHistory("redo")}
              disabled={busy || !timelineId}
            >
              ↷ Redo
            </button>
            <button
              className="secondary-button"
              onClick={refreshTimeline}
              disabled={busy || !timelineId}
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="timeline-summary">
          <div>
            <span>EDITED DURATION</span>
            <strong>{formatTime(duration)}</strong>
          </div>
          <div>
            <span>VIDEO SEGMENTS</span>
            <strong>{segments.length}</strong>
          </div>
          <div>
            <span>CAPTIONS</span>
            <strong>{captions.length}</strong>
          </div>
        </div>

        {!timeline ? (
          <div className="empty-timeline">
            Your timeline will appear here after you upload a video or create a demo.
          </div>
        ) : (
          <>
            <div className="track">
              <div className="track-label">VIDEO</div>
              <div className="track-content">
                {segments.length ? (
                  segments.map((segment, index) => (
                    <div className="segment" key={`${segment.source_start_s}-${index}`}>
                      <span>CLIP {String(index + 1).padStart(2, "0")}</span>
                      <strong>
                        {formatTime(segment.source_start_s)} –{" "}
                        {formatTime(segment.source_end_s)}
                      </strong>
                      <small>{segment.speed || 1}× speed</small>
                    </div>
                  ))
                ) : (
                  <span className="muted">No segments available</span>
                )}
              </div>
            </div>

            <div className="track">
              <div className="track-label">CAPTIONS</div>
              <div className="track-content caption-track">
                {captions.length ? (
                  captions.map((caption, index) => (
                    <div className="caption-block" key={caption.id || index}>
                      <strong>{caption.text}</strong>
                      <small>
                        {formatTime(caption.anchor_start_s ?? caption.start_s)} –{" "}
                        {formatTime(caption.anchor_end_s ?? caption.end_s)}
                      </small>
                    </div>
                  ))
                ) : (
                  <span className="muted">No captions added</span>
                )}
              </div>
            </div>
          </>
        )}

        <p className="hint">
          Segment times refer to the original source. The preview above shows the
          original uploaded file; the rendered output applies your edits.
        </p>
      </section>

      <section className="panel render-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">04 / EXPORT</p>
            <h3>Render and output history</h3>
          </div>
          <span className="mini-label">{renders.length} SAVED</span>
        </div>

        <div className="render-banner">
          <div>
            <h4>Ready to create your edited video?</h4>
            <p>Render the current timeline and save the result.</p>
          </div>
          <button
            className="primary-button"
            onClick={renderVideo}
            disabled={busy || !timelineId}
          >
            {busy ? "Processing…" : "Render video"} <span>↗</span>
          </button>
        </div>

        <div className="render-list">
          {renders.length === 0 ? (
            <p className="empty-renders">
              No renders yet. Your completed exports will appear here.
            </p>
          ) : (
            renders.map((render) => (
              <div className="render-item" key={render.render_id}>
                <div className="render-file-icon">MP4</div>
                <div className="render-details">
                  <strong>{render.filename || render.render_id}</strong>
                  <small>
                    {render.status || "saved"}
                    {render.size_bytes
                      ? ` · ${(render.size_bytes / 1024 / 1024).toFixed(2)} MB`
                      : ""}
                  </small>
                </div>
                <button
                  className="secondary-button"
                  onClick={() => downloadRender(render.render_id)}
                  disabled={busy}
                >
                  ↓ Download
                </button>
              </div>
            ))
          )}
        </div>
      </section>

      <footer className="footer">
        <span>PROOFCUT · VIDEO EDITING WORKSPACE</span>
        <span>Built for the IncuBrix Software Engineer Assessment</span>
      </footer>
    </main>
  );
}