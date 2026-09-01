from app.agents.detection import (
    CUSUM_THRESHOLD,
    SegmentStats,
    _segment_key,
    cusum_alarm,
    evaluate_segment,
    load_cusum_state,
    update_online_detectors,
)


def make_segment(recent_rate, baseline_rate=0.10, n=40, risk=1_000_000) -> SegmentStats:
    failures = round(recent_rate * n)
    return SegmentStats(
        method="upi", bank="HDFC",
        recent_attempts=n, recent_failures=failures,
        baseline_attempts=500, baseline_failures=round(baseline_rate * 500),
        revenue_at_risk_minor=risk,
    )


def test_quiet_segment_no_alarm():
    assert evaluate_segment(make_segment(0.12, 0.10)) is None


def test_spike_fires_window_baseline():
    alarm = evaluate_segment(make_segment(0.70, 0.08))
    assert alarm is not None
    assert "window_baseline" in alarm.detectors_fired
    assert "z_score" in alarm.detectors_fired
    assert alarm.z_score > 5


def test_moderate_spike_below_absolute_floor_ignored():
    assert evaluate_segment(make_segment(0.25, 0.05)) is None


def test_small_sample_ignored():
    assert evaluate_segment(make_segment(0.90, 0.05, n=5)) is None


def test_severity_high_on_massive_spike():
    alarm = evaluate_segment(make_segment(0.95, 0.05, risk=100_000_000))
    assert alarm is not None
    assert alarm.severity == "high"


async def test_online_detectors_cusum_trips_on_failure_streak():
    for _ in range(60):
        await update_online_detectors("upi", "TESTBANK", True, baseline_rate=0.1)
    assert await cusum_alarm("upi", "TESTBANK") is True
    await update_online_detectors("upi", "OTHERBANK", False, baseline_rate=0.1)
    assert await cusum_alarm("upi", "OTHERBANK") is False


async def test_detector_state_survives_a_restart():
    """The sequential statistics live in Postgres, not in process memory."""
    for _ in range(60):
        await update_online_detectors("card", "RESTARTBANK", True, baseline_rate=0.1)

    # Nothing is cached in the module, so a fresh read is what a new replica
    # (or the same process after a deploy) would see.
    state = await load_cusum_state()
    assert state[_segment_key("card", "RESTARTBANK")] >= CUSUM_THRESHOLD


async def test_detector_state_handles_missing_method_and_bank():
    await update_online_detectors(None, None, True, baseline_rate=0.1)
    state = await load_cusum_state()
    assert _segment_key(None, None) in state


async def test_alarm_reports_the_cusum_it_actually_used():
    segment = make_segment(0.70, 0.08)
    key = _segment_key(segment.method, segment.bank)
    alarm = evaluate_segment(segment, {key: 0.9})
    assert alarm is not None
    assert alarm.cusum == 0.9
    assert "cusum" in alarm.detectors_fired
