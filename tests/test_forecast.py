from datetime import date
from decimal import Decimal as D

import pytest

from core.forecast import INFLOW, OBLIGATION, Event, forecast

S = date(2026, 10, 1)
E = date(2026, 10, 10)


def d(day):
    return date(2026, 10, day)


def test_no_shortfall():
    f = forecast(D("5000"), S, E, [Event("rent", d(5), D("1800"), OBLIGATION)])
    assert f.min_balance == D("3200")
    assert f.min_balance_date == d(5)
    assert f.first_shortfall_date is None
    assert f.topup_needed_cny == D("0")
    assert f.transfer_by is None
    assert f.is_late is False


def test_shortfall_and_topup():
    ev = [Event("rent", d(5), D("1800"), OBLIGATION),
          Event("home", d(3), D("500"), INFLOW)]
    f = forecast(D("1000"), S, E, ev, lead_days=3)
    assert f.min_balance == D("-300")
    assert f.first_shortfall_date == d(5)
    assert f.topup_needed_cny == D("300")
    assert f.transfer_by == d(2)
    assert f.is_late is False


def test_buffer_raises_topup():
    ev = [Event("rent", d(5), D("1800"), OBLIGATION),
          Event("home", d(3), D("500"), INFLOW)]
    f = forecast(D("1000"), S, E, ev, buffer=D("200"), lead_days=3)
    assert f.first_shortfall_date == d(5)
    assert f.topup_needed_cny == D("500")


def test_late_transfer_flagged():
    ev = [Event("fees", d(2), D("850"), OBLIGATION)]
    f = forecast(D("1000"), S, E, ev, buffer=D("200"), lead_days=3)
    assert f.first_shortfall_date == d(2)
    assert f.topup_needed_cny == D("50")
    assert f.transfer_by == S
    assert f.is_late is True


def test_multiple_deficits_one_topup_covers_deepest():
    ev = [Event("tuition", d(2), D("1000"), OBLIGATION),
          Event("home", d(3), D("300"), INFLOW),
          Event("rent", d(4), D("500"), OBLIGATION)]
    f = forecast(D("0"), S, date(2026, 10, 5), ev)
    assert f.min_balance == D("-1200")
    assert f.min_balance_date == d(4)
    assert f.first_shortfall_date == d(2)
    assert f.topup_needed_cny == D("1200")


def test_same_day_events_net_out():
    ev = [Event("rent", d(2), D("1000"), OBLIGATION),
          Event("home", d(2), D("1000"), INFLOW)]
    f = forecast(D("0"), S, date(2026, 10, 3), ev)
    assert f.first_shortfall_date is None
    assert f.min_balance == D("0")


def test_events_after_end_ignored():
    ev = [Event("rent", d(20), D("1800"), OBLIGATION)]
    f = forecast(D("1000"), S, E, ev)
    assert f.min_balance == D("1000")
    assert f.first_shortfall_date is None


def test_event_before_start_rejected():
    ev = [Event("old", date(2026, 9, 30), D("10"), OBLIGATION)]
    with pytest.raises(ValueError):
        forecast(D("100"), S, E, ev)


def test_daily_covers_every_day():
    f = forecast(D("100"), S, E, [])
    assert len(f.daily) == 10
    assert f.daily[0][0] == S
    assert f.daily[-1][0] == E


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        forecast(D("1"), E, S, [])
    with pytest.raises(ValueError):
        forecast(D("1"), S, E, [], buffer=D("-1"))
    with pytest.raises(ValueError):
        forecast(D("1"), S, E, [], lead_days=-1)
    with pytest.raises(ValueError):
        Event("x", S, D("0"), OBLIGATION)
    with pytest.raises(ValueError):
        Event("x", S, D("5"), "gift")