from collections import Counter

from taskflow.traffic.model import Profile, TrafficModel, rate_at

TENANTS = [(1, "enterprise"), (2, "pro"), (3, "free"), (4, "free")]


def test_same_seed_gives_same_events() -> None:
    p = Profile()
    a = TrafficModel(p, seed=7, tenants=TENANTS).events(0, 60)
    b = TrafficModel(p, seed=7, tenants=TENANTS).events(0, 60)
    c = TrafficModel(p, seed=8, tenants=TENANTS).events(0, 60)
    assert a == b
    assert a != c


def test_contiguous_windows_match_single_window() -> None:
    p = Profile()
    m1 = TrafficModel(p, seed=3, tenants=TENANTS)
    m2 = TrafficModel(p, seed=3, tenants=TENANTS)
    whole = m1.events(0, 30)
    parts = m2.events(0, 10) + m2.events(10, 20) + m2.events(20, 30)
    assert [e.t for e in whole] == [e.t for e in parts]


def test_night_is_quieter_than_day_and_weekend_quieter_than_weekday() -> None:
    p = Profile(start_phase=0.0)
    day = p.day_period_s
    assert rate_at(p, 0.0) < rate_at(p, day / 2)  # midnight vs noon
    assert rate_at(p, 5 * day + day / 2) < rate_at(p, day / 2)  # Saturday noon vs Monday noon


def test_event_rate_close_to_profile() -> None:
    p = Profile(base_rps=10, night_factor=1.0, weekend_factor=1.0)
    events = TrafficModel(p, seed=1, tenants=TENANTS).events(0, 300)
    assert 2700 < len(events) < 3300


def test_enterprise_tenants_are_busier() -> None:
    p = Profile(base_rps=20, night_factor=1.0)
    events = TrafficModel(p, seed=2, tenants=TENANTS).events(0, 300)
    per_tenant = Counter(e.tenant_id for e in events)
    assert per_tenant[1] > per_tenant[2] > per_tenant[3]


def test_spike_multiplies_rate() -> None:
    p = Profile(spike_start_s=100, spike_duration_s=50, spike_factor=3.0)
    assert rate_at(p, 120) == 3.0 * rate_at(p.without_spike(), 120)
