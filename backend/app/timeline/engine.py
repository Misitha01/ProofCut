
from copy import deepcopy
from math import isfinite
import hashlib
import json


class Timeline:
    def __init__(self, duration_s):
        duration = self._number(duration_s, "Duration")
        if duration <= 0:
            raise ValueError("Duration must be positive")

        self.source_duration_s = duration
        self.segments = [
            {
                "source_start_s": 0.0,
                "source_end_s": duration,
                "speed": 1.0,
            }
        ]
        self.captions = []
        self.audio_edits = []
        self.background_music = None

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc

        if not isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        return number

    def _validate_range(self, start_s, end_s, name="range"):
        start = self._number(start_s, "Start time")
        end = self._number(end_s, "End time")

        if not 0 <= start < end <= self.source_duration_s:
            raise ValueError(
                f"Invalid {name}: expected 0 <= start < end "
                f"<= {self.source_duration_s}"
            )

        return start, end

    def _validate_segments(self):
        if not self.segments:
            raise ValueError(
                "A timeline must contain at least one segment"
            )

        intervals = []

        for segment in self.segments:
            start = self._number(
                segment.get("source_start_s"), "Segment start"
            )
            end = self._number(
                segment.get("source_end_s"), "Segment end"
            )
            speed = self._number(
                segment.get("speed"), "Segment speed"
            )

            if not 0 <= start < end <= self.source_duration_s:
                raise ValueError("Invalid segment boundaries")

            if not 0 < speed <= 16:
                raise ValueError(
                    "Speed must be greater than 0 and at most 16"
                )

            intervals.append((start, end))

        # Check overlap in source time without changing playback order.
        intervals.sort()

        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1] - 1e-9:
                raise ValueError("Timeline segments must not overlap")

    @property
    def duration_s(self):
        return sum(
            (
                segment["source_end_s"]
                - segment["source_start_s"]
            ) / segment["speed"]
            for segment in self.segments
        )

    def _visible_parts(self, start, end):
        """Return visible portions of a source-time range."""
        parts = []

        # Preserve the existing timeline playback order.
        for segment in self.segments:
            left = max(start, segment["source_start_s"])
            right = min(end, segment["source_end_s"])

            if right > left:
                parts.append({
                    "source_start_s": left,
                    "source_end_s": right,
                    "speed": segment["speed"],
                })

        return parts

    def cut_range(self, start_s, end_s):
        """Remove a source-time range from every matching segment."""
        start, end = self._validate_range(
            start_s, end_s, "cut range"
        )

        updated = []
        did_cut = False

        for segment in self.segments:
            a = segment["source_start_s"]
            b = segment["source_end_s"]
            speed = segment["speed"]

            # This segment does not overlap the cut.
            if b <= start or a >= end:
                updated.append(deepcopy(segment))
                continue

            did_cut = True

            # Keep the part before the cut.
            if a < start:
                updated.append({
                    "source_start_s": a,
                    "source_end_s": min(start, b),
                    "speed": speed,
                })

            # Keep the part after the cut.
            if b > end:
                updated.append({
                    "source_start_s": max(end, a),
                    "source_end_s": b,
                    "speed": speed,
                })

        if not did_cut:
            raise ValueError(
                "The cut range does not overlap the timeline"
            )

        if not updated:
            raise ValueError(
                "Cannot remove the entire timeline"
            )

        # Captions and audio edits remain anchored to source time.
        self.segments = updated
        self._validate_segments()

    def keep_range(self, start_s, end_s):
        """Keep only the specified source-time range."""
        start, end = self._validate_range(
            start_s, end_s, "keep range"
        )

        updated = self._visible_parts(start, end)

        if not updated:
            raise ValueError(
                "The keep range does not overlap the timeline"
            )

        self.segments = updated
        self._validate_segments()

    def reorder_sections(self, ranges):
        """
        Reorder selected source-time sections.

        Example: [[30, 45], [0, 10], [10, 30]].
        Removed portions remain removed.
        """
        if not isinstance(ranges, list) or not ranges:
            raise ValueError(
                "Provide a non-empty list of [start, end] ranges"
            )

        checked = []

        for item in ranges:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(
                    "Each section must contain start and end"
                )

            checked.append(
                self._validate_range(
                    item[0], item[1], "section"
                )
            )

        sorted_ranges = sorted(checked)

        for previous, current in zip(
            sorted_ranges, sorted_ranges[1:]
        ):
            if current[0] < previous[1]:
                raise ValueError(
                    "Reordered sections must not overlap"
                )

        updated = []

        for start, end in checked:
            updated.extend(self._visible_parts(start, end))

        if not updated:
            raise ValueError(
                "None of the selected sections are on the timeline"
            )

        self.segments = updated
        self._validate_segments()

    def change_speed(self, start_s, end_s, speed):
        """Change playback speed within a source-time range."""
        start, end = self._validate_range(
            start_s, end_s, "speed range"
        )
        speed = self._number(speed, "Speed")

        if not 0 < speed <= 16:
            raise ValueError(
                "Speed must be greater than 0 and at most 16"
            )

        updated = []
        changed = False

        for segment in self.segments:
            a = segment["source_start_s"]
            b = segment["source_end_s"]
            old_speed = segment["speed"]

            if b <= start or a >= end:
                updated.append(deepcopy(segment))
                continue

            changed = True
            left = max(a, start)
            right = min(b, end)

            if a < left:
                updated.append({
                    "source_start_s": a,
                    "source_end_s": left,
                    "speed": old_speed,
                })

            updated.append({
                "source_start_s": left,
                "source_end_s": right,
                "speed": speed,
            })

            if right < b:
                updated.append({
                    "source_start_s": right,
                    "source_end_s": b,
                    "speed": old_speed,
                })

        if not changed:
            raise ValueError(
                "The speed range does not overlap the timeline"
            )

        self.segments = updated
        self._validate_segments()

    def add_caption(
        self, caption_id, text, start_s, end_s, color="#FFFFFF"
    ):
        if not isinstance(caption_id, str) or not caption_id.strip():
            raise ValueError("Caption ID must not be empty")

        if any(c["id"] == caption_id for c in self.captions):
            raise ValueError("Caption ID already exists")

        if not isinstance(text, str) or not text.strip():
            raise ValueError("Caption text must not be empty")

        if not isinstance(color, str) or not color.strip():
            raise ValueError("Caption color must not be empty")

        start, end = self._validate_range(
            start_s, end_s, "caption range"
        )

        self.captions.append({
            "id": caption_id,
            "text": text,
            "anchor_start_s": start,
            "anchor_end_s": end,
            "color": color,
        })

    def update_caption(
        self,
        caption_id,
        text=None,
        start_s=None,
        end_s=None,
        color=None,
    ):
        caption = next(
            (c for c in self.captions if c["id"] == caption_id),
            None,
        )

        if caption is None:
            raise ValueError("Caption not found")

        new_text = caption["text"] if text is None else text
        new_color = caption["color"] if color is None else color
        new_start = (
            caption["anchor_start_s"]
            if start_s is None else start_s
        )
        new_end = (
            caption["anchor_end_s"]
            if end_s is None else end_s
        )

        if not isinstance(new_text, str) or not new_text.strip():
            raise ValueError("Caption text must not be empty")

        if not isinstance(new_color, str) or not new_color.strip():
            raise ValueError("Caption color must not be empty")

        new_start, new_end = self._validate_range(
            new_start, new_end, "caption range"
        )

        caption.update({
            "text": new_text,
            "anchor_start_s": new_start,
            "anchor_end_s": new_end,
            "color": new_color,
        })

    def delete_caption(self, caption_id):
        for index, caption in enumerate(self.captions):
            if caption["id"] == caption_id:
                del self.captions[index]
                return

        raise ValueError("Caption not found")

    def add_audio_edit(
        self, edit_id, action, start_s, end_s, word=""
    ):
        """Record a source-anchored mute or bleep edit."""
        if action not in {"mute", "bleep"}:
            raise ValueError(
                "Audio action must be 'mute' or 'bleep'"
            )

        if not isinstance(edit_id, str) or not edit_id.strip():
            raise ValueError("Audio edit ID must not be empty")

        if any(edit["id"] == edit_id for edit in self.audio_edits):
            raise ValueError("Audio edit ID already exists")

        start, end = self._validate_range(
            start_s, end_s, "audio edit range"
        )

        self.audio_edits.append({
            "id": edit_id,
            "action": action,
            "anchor_start_s": start,
            "anchor_end_s": end,
            "word": str(word),
        })

    def remove_audio_edit(self, edit_id):
        for index, edit in enumerate(self.audio_edits):
            if edit["id"] == edit_id:
                del self.audio_edits[index]
                return

        raise ValueError("Audio edit not found")

    def set_background_music(
        self, file_path, volume=0.15, ducking=True
    ):
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("Music file path must not be empty")

        volume = self._number(volume, "Music volume")

        if not 0 <= volume <= 1:
            raise ValueError(
                "Music volume must be between 0 and 1"
            )

        if not isinstance(ducking, bool):
            raise ValueError("Ducking must be true or false")

        self.background_music = {
            "file_path": file_path,
            "volume": volume,
            "ducking": ducking,
        }

    def remove_background_music(self):
        self.background_music = None

    def caption_output_ranges(self, caption):
        """Map a source-anchored caption to output-time ranges."""
        cursor = 0.0
        ranges = []

        for segment in self.segments:
            a = segment["source_start_s"]
            b = segment["source_end_s"]
            speed = segment["speed"]

            left = max(caption["anchor_start_s"], a)
            right = min(caption["anchor_end_s"], b)

            if right > left:
                output_start = cursor + (left - a) / speed
                output_end = cursor + (right - a) / speed

                ranges.append([
                    round(output_start, 3),
                    round(output_end, 3),
                ])

            cursor += (b - a) / speed

        return ranges

    def audio_edit_output_ranges(self, audio_edit):
        """Map a source-anchored audio edit to output-time ranges."""
        return self.caption_output_ranges(audio_edit)

    def caption_output_range(self, caption):
        ranges = self.caption_output_ranges(caption)
        return ranges[0] if len(ranges) == 1 else None

    def to_dict(self):
        captions = []

        for caption in self.captions:
            item = deepcopy(caption)
            ranges = self.caption_output_ranges(caption)
            item["output_ranges_s"] = ranges
            item["output_range_s"] = (
                ranges[0] if len(ranges) == 1 else None
            )
            captions.append(item)

        audio_edits = []

        for edit in self.audio_edits:
            item = deepcopy(edit)
            item["output_ranges_s"] = (
                self.audio_edit_output_ranges(edit)
            )
            audio_edits.append(item)

        return {
            "source_duration_s": self.source_duration_s,
            "duration_s": round(self.duration_s, 3),
            "segments": deepcopy(self.segments),
            "captions": captions,
            "audio_edits": audio_edits,
            "background_music": deepcopy(self.background_music),
        }

    def timeline_hash(self):
        """Return a deterministic SHA-256 hash of timeline content."""
        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()


class TimelineHistory:
    def __init__(self, timeline):
        self.states = [deepcopy(timeline)]
        self.index = 0

    @property
    def current(self):
        return deepcopy(self.states[self.index])

    def _commit(self, updated):
        updated._validate_segments()

        self.states = self.states[:self.index + 1]
        self.states.append(deepcopy(updated))
        self.index += 1

        return self.current

    def apply_cut(self, start_s, end_s):
        updated = self.current
        updated.cut_range(start_s, end_s)
        return self._commit(updated)

    def apply_keep(self, start_s, end_s):
        updated = self.current
        updated.keep_range(start_s, end_s)
        return self._commit(updated)

    def apply_reorder(self, ranges):
        updated = self.current
        updated.reorder_sections(ranges)
        return self._commit(updated)

    def apply_speed(self, start_s, end_s, speed):
        updated = self.current
        updated.change_speed(start_s, end_s, speed)
        return self._commit(updated)

    def add_caption(
        self, caption_id, text, start_s, end_s, color="#FFFFFF"
    ):
        updated = self.current
        updated.add_caption(
            caption_id, text, start_s, end_s, color
        )
        return self._commit(updated)

    def update_caption(
        self,
        caption_id,
        text=None,
        start_s=None,
        end_s=None,
        color=None,
    ):
        updated = self.current
        updated.update_caption(
            caption_id, text, start_s, end_s, color
        )
        return self._commit(updated)

    def delete_caption(self, caption_id):
        updated = self.current
        updated.delete_caption(caption_id)
        return self._commit(updated)

    def add_audio_edit(
        self, edit_id, action, start_s, end_s, word=""
    ):
        updated = self.current
        updated.add_audio_edit(
            edit_id, action, start_s, end_s, word
        )
        return self._commit(updated)

    def remove_audio_edit(self, edit_id):
        updated = self.current
        updated.remove_audio_edit(edit_id)
        return self._commit(updated)

    def set_background_music(
        self, file_path, volume=0.15, ducking=True
    ):
        updated = self.current
        updated.set_background_music(
            file_path, volume, ducking
        )
        return self._commit(updated)

    def remove_background_music(self):
        updated = self.current
        updated.remove_background_music()
        return self._commit(updated)

    def restore_state(self, state_index):
        """Restore an earlier state as a new history step."""
        if isinstance(state_index, bool) or not isinstance(
            state_index, int
        ):
            raise ValueError("History index must be an integer")

        if not 0 <= state_index < len(self.states):
            raise ValueError("History state not found")

        updated = deepcopy(self.states[state_index])
        return self._commit(updated)

    def undo(self):
        if self.index == 0:
            raise ValueError("Nothing to undo")

        self.index -= 1
        return self.current

    def redo(self):
        if self.index >= len(self.states) - 1:
            raise ValueError("Nothing to redo")

        self.index += 1
        return self.current
