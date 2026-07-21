from cvbench.config import Thresholds
from cvbench.metrics import calculate_metrics, percentile
from tests.helpers import gt, output


def test_acquisition_latency_is_exactly_250_ms() -> None:
    ground_truth = [gt(1_000_000_000), gt(1_250_000_000), gt(1_500_000_000)]
    records = [output(1_250_000_000)]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds())
    assert metrics["acquisition"]["median"] == 250
    assert metrics["acquisition"]["per_target_latency_ms"]["seq:gt-1"] == 250
    assert metrics["acquisition"]["within_deadline"]["250"] == 1


def test_observed_coverage_and_continuity_are_separate() -> None:
    timestamps = [0, 100_000_000, 200_000_000, 300_000_000, 400_000_000]
    ground_truth = [gt(timestamp) for timestamp in timestamps]
    records = [
        output(0),
        output(100_000_000),
        output(200_000_000, support="predicted", state="coasting"),
        output(400_000_000),
    ]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds(visible_dropout_tolerance_ms=100))
    assert metrics["coverage"]["overall_observed"] == 0.6
    assert metrics["coverage"]["overall_continuity"] == 0.8
    assert metrics["visible_dropouts"]["count"] == 1
    assert metrics["visible_dropouts"]["maximum"] == 200


def test_reacquisition_latency_is_exactly_180_ms_and_same_id() -> None:
    ground_truth = [
        gt(1_000_000_000),
        gt(1_100_000_000, eligible=False, visibility=0, occlusion="full"),
        gt(1_200_000_000, eligible=False, visibility=0, occlusion="full"),
        gt(1_300_000_000),
        gt(1_480_000_000),
        gt(1_580_000_000),
    ]
    records = [
        output(1_000_000_000, track="same"),
        output(1_100_000_000, track="same", support="predicted", state="coasting"),
        output(1_200_000_000, track="same", support="predicted", state="coasting"),
        output(1_480_000_000, track="same", state="reacquired"),
    ]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds())
    assert metrics["reacquisition"]["latency_ms"]["median"] == 180
    assert metrics["reacquisition"]["same_id_rate"] == 1
    assert metrics["robustness"]["occlusion_survival"]["track_active_rate"] == 1


def test_id_switch_count_is_exact() -> None:
    ground_truth = [gt(0), gt(100_000_000), gt(200_000_000)]
    records = [output(0, track="a"), output(100_000_000, track="b"), output(200_000_000, track="b")]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds())
    assert metrics["identity"]["id_switches"] == 1
    assert metrics["identity"]["track_splits"] == 0


def test_duplicate_track_counts_only_extra_unmatched_track() -> None:
    ground_truth = [gt(0)]
    records = [output(0, track="primary"), output(0, track="duplicate", box=[11, 10, 21, 20])]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds())
    assert metrics["identity"]["duplicate_tracks"] == 1
    assert metrics["identity"]["track_splits"] == 1


def test_false_track_duration_is_exactly_two_seconds() -> None:
    ground_truth = [gt(0, box=[100, 100, 110, 110]), gt(1_000_000_000, box=[100, 100, 110, 110])]
    records = [output(0, track="false", box=[0, 0, 10, 10]), output(1_000_000_000, track="false", box=[0, 0, 10, 10])]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds(max_match_center_error_px=5))
    assert metrics["false_detections"]["track_duration_ms"]["median"] == 2000
    assert metrics["false_detections"]["longest_lived_false_track_ms"] == 2000
    assert metrics["false_detections"]["persistent_track_births"] == 1
    assert metrics["false_detections"]["one_frame_false_detections"] == 0


def test_external_latency_uses_collector_timestamp() -> None:
    metrics, _ = calculate_metrics(
        [gt(1_000_000_000)], [output(1_000_000_000, received_offset_ns=17_000_000)], Thresholds()
    )
    assert metrics["latency"]["median"] == 17
    assert metrics["latency"]["maximum"] == 17


def test_acceptance_fixture_exact_220_ms_acquisition_and_350_ms_dropout() -> None:
    timestamps = [
        1_000_000_000,
        1_220_000_000,
        3_900_000_000,
        4_000_000_000,
        4_100_000_000,
        4_200_000_000,
        4_300_000_000,
        4_350_000_000,
    ]
    ground_truth = [gt(timestamp) for timestamp in timestamps]
    records = [output(1_220_000_000), output(3_900_000_000), output(4_350_000_000)]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds(visible_dropout_tolerance_ms=0))
    assert metrics["acquisition"]["median"] == 220
    assert metrics["visible_dropouts"]["maximum"] == 350


def test_predicted_output_never_counts_as_reacquisition() -> None:
    ground_truth = [
        gt(0),
        gt(100_000_000, eligible=False, visibility=0, occlusion="full"),
        gt(200_000_000),
    ]
    records = [
        output(0),
        output(100_000_000, support="predicted", state="coasting"),
        output(200_000_000, support="predicted", state="coasting"),
    ]
    metrics, _ = calculate_metrics(ground_truth, records, Thresholds())
    assert metrics["reacquisition"]["correct_target_rate"] == 0
    assert metrics["coverage"]["overall_observed"] < metrics["coverage"]["overall_continuity"]


def test_freshness_requires_exact_source_timestamp() -> None:
    metrics, _ = calculate_metrics([gt(100)], [output(101)], Thresholds())
    assert metrics["sample_counts"]["matches"] == 0


def test_eof_uses_median_cadence_and_half_open_intervals() -> None:
    ground_truth = [gt(0), gt(100_000_000), gt(200_000_000)]
    metrics, _ = calculate_metrics(ground_truth, [output(0), output(100_000_000)], Thresholds())
    assert metrics["coverage"]["eligible_target_time_ms"] == 300
    assert metrics["coverage"]["overall_observed"] == 2 / 3


def test_percentiles_use_linear_interpolation() -> None:
    assert percentile([0, 100], 0.95) == 95


def test_feed_interruption_recovery_reports_observed_same_id() -> None:
    ground_truth = [gt(0), gt(100_000_000), gt(200_000_000)]
    records = [output(0, track="same"), output(100_000_000, track="same"), output(200_000_000, track="same")]
    metrics, _ = calculate_metrics(
        ground_truth,
        records,
        Thresholds(),
        fault_timestamps={("seq", 100_000_000)},
        fault_events={("seq", 100_000_000): ["feed_interruption"]},
    )
    result = metrics["robustness"]["feed_faults"]["feed_interruption"]
    assert result["observed_recovery_rate"] == 1
    assert result["same_id_recovery_rate"] == 1
    assert metrics["reacquisition"]["after_feed_interruption_rate"] == 1
