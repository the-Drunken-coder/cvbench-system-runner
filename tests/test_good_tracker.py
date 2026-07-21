from cvbench.examples.good_tracker import _center, _clamp_box


def test_coasting_box_is_clamped_to_frame_bounds_and_remains_nonempty() -> None:
    box = _clamp_box([-20.0, 115.0, -5.0, 140.0], 160, 120)
    assert box == [0.0, 115.0, 1.0, 120.0]
    center = _center(box)
    assert 0 <= center[0] <= 160
    assert 0 <= center[1] <= 120
