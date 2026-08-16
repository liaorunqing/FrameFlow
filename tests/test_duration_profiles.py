from backend.app.director import _durations


def test_campaign_duration_profiles_are_exact_and_provider_safe() -> None:
    expected_counts = {15: 3, 30: 5, 45: 8, 60: 10}
    for duration, count in expected_counts.items():
        shots = _durations(duration)
        assert len(shots) == count
        assert sum(shots) == duration
        assert all(4 <= seconds <= 10 for seconds in shots)
