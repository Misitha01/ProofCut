
import pytest

from backend.app.timeline.engine import Timeline, TimelineHistory


def test_cut_updates_duration():
    timeline = Timeline(100)

    timeline.cut_range(10, 30)

    assert timeline.duration_s == 80
    assert len(timeline.segments) == 2


def test_speed_change_updates_duration():
    timeline = Timeline(100)

    timeline.change_speed(20, 40, 2)

    assert timeline.duration_s == 90


def test_speed_change_can_be_undone_and_redone():
    history = TimelineHistory(Timeline(100))

    history.apply_speed(20, 40, 2)
    assert history.current.duration_s == 90

    history.undo()
    assert history.current.duration_s == 100

    history.redo()
    assert history.current.duration_s == 90


def test_caption_can_be_added_updated_and_deleted():
    history = TimelineHistory(Timeline(100))

    history.add_caption("c1", "Hello", 10, 20)
    assert history.current.captions[0]["text"] == "Hello"

    history.update_caption("c1", text="Welcome")
    assert history.current.captions[0]["text"] == "Welcome"

    history.undo()
    assert history.current.captions[0]["text"] == "Hello"

    history.delete_caption("c1")
    assert history.current.captions == []


def test_caption_ranges_split_when_cut_removes_middle():
    timeline = Timeline(10)
    timeline.add_caption("c1", "Hello", 2, 8)

    timeline.cut_range(4, 6)

    caption = timeline.to_dict()["captions"][0]

    assert caption["output_ranges_s"] == [[2.0, 4.0], [4.0, 6.0]]
    assert caption["output_range_s"] is None


def test_invalid_cut_is_rejected():
    timeline = Timeline(100)

    with pytest.raises(ValueError):
        timeline.cut_range(30, 10)


def test_cannot_remove_entire_video():
    timeline = Timeline(100)

    with pytest.raises(ValueError):
        timeline.cut_range(0, 100)


def test_new_edit_clears_redo_history():
    history = TimelineHistory(Timeline(100))

    history.apply_cut(10, 20)
    history.undo()

    history.apply_speed(0, 10, 2)

    with pytest.raises(ValueError):
        history.redo()
