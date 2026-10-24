
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.timeline.engine import Timeline, TimelineHistory


OUTPUT = ROOT / "test_pack"
SESSIONS = OUTPUT / "sessions"


def close(actual, expected, tolerance=0.001):
    assert abs(actual - expected) <= tolerance, (
        f"Expected {expected}, got {actual}"
    )


def initial():
    history = TimelineHistory(Timeline(120))
    return history, {"duration_s": 120}


def session_01():
    h, _ = initial()
    close(h.current.duration_s, 120)
    return h, "Initial timeline is 120 seconds"


def session_02():
    h, _ = initial()
    h.apply_cut(10, 20)
    close(h.current.duration_s, 110)
    return h, "Cut 10-20 seconds"


def session_03():
    h, _ = initial()
    h.apply_keep(20, 80)
    close(h.current.duration_s, 60)
    return h, "Keep only source seconds 20-80"


def session_04():
    h, _ = initial()
    h.apply_reorder([[30, 40], [0, 10]])
    close(h.current.duration_s, 20)
    assert h.current.segments[0]["source_start_s"] == 30
    return h, "Reorder two source sections"


def session_05():
    h, _ = initial()
    h.apply_speed(0, 60, 2)
    close(h.current.duration_s, 90)
    return h, "Double playback speed for first 60 seconds"


def session_06():
    h, _ = initial()
    h.add_caption("c1", "Hello", 42, 44)
    h.apply_cut(10, 20)
    caption = h.current.to_dict()["captions"][0]
    assert caption["output_range_s"] == [32.0, 34.0]
    return h, "Caption remains source-anchored after a cut"


def session_07():
    h, _ = initial()
    h.add_caption("c1", "Important", 15, 25)
    h.apply_cut(10, 20)
    caption = h.current.to_dict()["captions"][0]
    assert caption["output_ranges_s"] == [[10.0, 15.0]]
    return h, "Caption overlapping a cut keeps its visible part"


def session_08():
    h, _ = initial()
    h.add_audio_edit("a1", "mute", 42, 44)
    h.apply_cut(10, 20)
    edit = h.current.to_dict()["audio_edits"][0]
    assert edit["output_ranges_s"] == [[32.0, 34.0]]
    return h, "Mute edit remains anchored after a cut"


def session_09():
    h, _ = initial()
    h.add_audio_edit("a1", "bleep", 50, 52, word="example")
    h.apply_speed(0, 60, 2)
    edit = h.current.to_dict()["audio_edits"][0]
    assert edit["output_ranges_s"] == [[25.0, 26.0]]
    return h, "Bleep edit follows changed playback speed"


def session_10():
    h, _ = initial()
    original_hash = h.current.timeline_hash()
    h.apply_cut(10, 20)
    h.undo()
    assert h.current.timeline_hash() == original_hash
    return h, "Undo restores the previous timeline hash"


def session_11():
    h, _ = initial()
    h.apply_cut(10, 20)
    cut_hash = h.current.timeline_hash()
    h.undo()
    h.redo()
    assert h.current.timeline_hash() == cut_hash
    return h, "Redo restores the edited timeline hash"


def session_12():
    h, _ = initial()
    h.apply_cut(10, 20)
    h.apply_cut(30, 35)
    h.restore_state(1)
    close(h.current.duration_s, 110)
    return h, "Restore an earlier saved history state"


def session_13():
    h, _ = initial()
    h.apply_cut(10, 20)
    h.apply_cut(30, 35)
    h.undo()
    h.apply_cut(50, 55)

    try:
        h.redo()
    except ValueError as exc:
        assert "Nothing to redo" in str(exc)
    else:
        raise AssertionError("New edit should clear the redo branch")

    return h, "New edit clears redo history"


def session_14():
    h, _ = initial()
    before = h.current.timeline_hash()

    try:
        h.apply_cut(0, 120)
    except ValueError:
        pass
    else:
        raise AssertionError("Removing the entire timeline was allowed")

    assert h.current.timeline_hash() == before
    return h, "Reject removal of the entire timeline"


def session_15():
    first, _ = initial()
    second, _ = initial()

    for history in (first, second):
        history.apply_cut(10, 20)
        history.apply_speed(40, 60, 2)
        history.add_caption("c1", "Welcome", 42, 44)

    assert first.current.timeline_hash() == second.current.timeline_hash()
    return first, "Identical edits produce identical hashes"


CASES = [
    session_01,
    session_02,
    session_03,
    session_04,
    session_05,
    session_06,
    session_07,
    session_08,
    session_09,
    session_10,
    session_11,
    session_12,
    session_13,
    session_14,
    session_15,
]


def main():
    SESSIONS.mkdir(parents=True, exist_ok=True)
    results = []

    for number, case in enumerate(CASES, start=1):
        name = f"session_{number:02d}"
        try:
            history, description = case()
            timeline = history.current.to_dict()
            digest = history.current.timeline_hash()

            record = {
                "session_id": name,
                "status": "PASS",
                "description": description,
                "timeline_hash": digest,
                "timeline": timeline,
            }
            print(f"PASS {name}: {description}")

        except Exception as exc:
            record = {
                "session_id": name,
                "status": "FAIL",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"FAIL {name}: {exc}")

        (SESSIONS / f"{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        results.append(record)

    passed = sum(r["status"] == "PASS" for r in results)
    failed = len(results) - passed

    summary = {
        "project": "ProofCut",
        "test_type": "Direct timeline-engine functional sessions",
        "session_count": len(results),
        "passed": passed,
        "failed": failed,
        "results": [
            {
                "session_id": r["session_id"],
                "status": r["status"],
                "description": r.get("description", ""),
                "timeline_hash": r.get("timeline_hash"),
                "error": r.get("error"),
            }
            for r in results
        ],
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# ProofCut — Test Session Results",
        "",
        f"- Sessions: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        "",
        "Test type: direct timeline-engine functional checks.",
        "These tests do not establish real rendered-video or A/V-sync accuracy.",
        "",
        "| Session | Status | Description |",
        "|---|---|---|",
    ]

    for r in results:
        lines.append(
            f"| {r['session_id']} | {r['status']} | "
            f"{r.get('description', r.get('error', ''))} |"
        )

    (OUTPUT / "summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print()
    print(f"Total: {len(results)} | Passed: {passed} | Failed: {failed}")
    print(f"Results: {OUTPUT / 'results.json'}")
    print(f"Summary: {OUTPUT / 'summary.md'}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
