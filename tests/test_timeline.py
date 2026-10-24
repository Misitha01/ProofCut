
import pytest

from backend.app.timeline.engine import Timeline, TimelineHistory


def create_demo():
    timeline = Timeline(120)
    timeline.add_caption(
        "caption_1",
        "Welcome!",
        42,
        44,
    )
    return timeline


def test_caption_moves_after_cut():
    timeline = create_demo()

    timeline.cut_range(0, 30)

    assert timeline.duration_s == pytest.approx(90)
    assert timeline.caption_output_range(
        timeline.captions[0]
    ) == [12.0, 14.0]


def test_caption_disappears_if_its_content_is_cut():
    timeline = create_demo()

    timeline.cut_range(40, 45)

    assert timeline.caption_output_range(
        timeline.captions[0]
    ) is None


def test_speed_change():
    timeline = Timeline(120)

    timeline.change_speed(60, 90, 2)

    assert timeline.duration_s == pytest.approx(105)


def test_undo_redo():
    history = TimelineHistory(create_demo())

    history.apply_cut(0, 30)
    assert history.current.duration_s == pytest.approx(90)

    history.undo()
    assert history.current.duration_s == pytest.approx(120)

    history.redo()
    assert history.current.duration_s == pytest.approx(90)


def test_invalid_cut():
    timeline = create_demo()

    with pytest.raises(ValueError):
        timeline.cut_range(0, 130)
