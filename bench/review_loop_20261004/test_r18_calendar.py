"""Explicit supported frequency boundary must not hide an ordinary missing event."""
import numpy as np
import pandas as pd
import pytest

from r18_calendar import Boundary,TransitionCalendar
from r17_carry import FundingSeries


def fixture():
    start=pd.Timestamp('2025-04-25',tz='UTC');end=start+pd.Timedelta(hours=12)
    slots=pd.DatetimeIndex([start,start+pd.Timedelta(hours=2),start+pd.Timedelta(hours=4),start+pd.Timedelta(hours=6),
                            start+pd.Timedelta(hours=8),start+pd.Timedelta(hours=9),start+pd.Timedelta(hours=10),start+pd.Timedelta(hours=11)]).as_unit('ns')
    frame=pd.DataFrame({'slot':slots,'rate_decimal':'.001','interval_hours':[2,2,2,2,1,1,1,1]},index=slots)
    boundary=Boundary(slots[3],slots[4],2,1,'notice','a'*64,'2026-10-04T00:00:00Z')
    return frame,start,end,boundary


def test_documented_old_gap_at_first_new_frequency_preserves_actual_events():
    frame,start,end,boundary=fixture()
    with pytest.raises(ValueError,match='gap'):FundingSeries(frame,start,end)
    with pytest.raises(ValueError,match='boundary'):TransitionCalendar(frame,start,end,())
    result=TransitionCalendar(frame,start,end,(boundary,))
    assert result.events.index.equals(frame.index) and len(result.events)==8
    assert result.events.slot.equals(frame.slot) and result.events.interval_hours.equals(frame.interval_hours)
    assert np.array_equal(result.events.rate_decimal.map(float),frame.rate_decimal.astype(float))


def test_boundary_evidence_not_backdated_to_publisher_label():
    frame,start,end,boundary=fixture();result=TransitionCalendar(frame,start,end,(boundary,))
    with pytest.raises(ValueError,match='known'):result.events_known_at('2025-04-25T12:00:00Z')
    assert len(result.events_known_at('2026-10-04T01:00:00Z'))==8


def test_missing_following_event_conflict_or_unanchored_boundary_fail():
    frame,start,end,boundary=fixture()
    with pytest.raises(ValueError):TransitionCalendar(frame.drop(frame.index[5]),start,end,(boundary,))
    wrong=Boundary(boundary.previous_slot,boundary.first_new_slot,4,1,'notice','a'*64,boundary.known_after)
    with pytest.raises(ValueError):TransitionCalendar(frame,start,end,(wrong,))
    with pytest.raises(ValueError):TransitionCalendar(frame,start,end,(boundary,boundary))
    with pytest.raises(ValueError):Boundary(boundary.previous_slot,boundary.first_new_slot,2,1,'notice','bad',boundary.known_after)
