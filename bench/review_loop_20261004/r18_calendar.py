"""Source-supported frequency boundary, isolated from frozen R17 and alpha."""
from dataclasses import dataclass
import re

import pandas as pd

from r16_knowledge import utc
from r17_carry import FundingSeries


@dataclass(frozen=True)
class Boundary:
    previous_slot:object
    first_new_slot:object
    old_hours:int
    new_hours:int
    source_id:str
    raw_sha256:str
    known_after:object

    def __post_init__(self):
        for field in ('previous_slot','first_new_slot','known_after'):object.__setattr__(self,field,utc(getattr(self,field)))
        if self.previous_slot>=self.first_new_slot or self.previous_slot!=self.previous_slot.floor('h') or self.first_new_slot!=self.first_new_slot.floor('h'):
            raise ValueError('Exact ordered scheduled boundary required')
        if any(type(h) is not int or not 1<=h<=24 for h in (self.old_hours,self.new_hours)) or self.old_hours==self.new_hours:
            raise ValueError('Distinct exact declared source intervals required')
        if not isinstance(self.source_id,str) or not self.source_id or not isinstance(self.raw_sha256,str) or re.fullmatch('[0-9a-f]{64}',self.raw_sha256) is None:
            raise ValueError('Explicit boundary source identity/hash required')


class TransitionCalendar:
    def __init__(self,frame,start,end,boundaries):
        self.boundaries=tuple(boundaries)
        if any(not isinstance(b,Boundary) for b in self.boundaries):raise ValueError('Reviewed source boundary receipt required')
        by_slot={b.first_new_slot:b for b in self.boundaries}
        if len(by_slot)!=len(self.boundaries):raise ValueError('Conflicting/repeated boundary')
        slots=pd.DatetimeIndex(frame.slot)
        if slots.tz is None or not slots.is_unique or not slots.is_monotonic_increasing:raise ValueError('Ordered aware source slots required')
        slots=slots.tz_convert('UTC').as_unit('ns');changes=[]
        for i,b in enumerate(self.boundaries):
            if b.first_new_slot not in slots:raise ValueError('Unanchored boundary source')
            pos=slots.get_loc(b.first_new_slot)
            if pos==0 or slots[pos-1]!=b.previous_slot or frame.interval_hours.iloc[pos-1]!=b.old_hours or frame.interval_hours.iloc[pos]!=b.new_hours:
                raise ValueError('Boundary interval/identity does not match native source')
            changes.append(pos)
        intervals=pd.to_numeric(frame.interval_hours,errors='raise')
        for i in range(1,len(slots)):
            gap=(slots[i]-slots[i-1])/pd.Timedelta(hours=1)
            if gap!=intervals.iloc[i] and slots[i] not in by_slot:raise ValueError('Unreviewed funding boundary or missing event')
        # Validate each original segment with frozen physical/tail/rate checks.
        # At the documented first-new boundary the old segment must still
        # establish coverage up to that slot; no synthetic event/rate is added.
        starts=[0,*sorted(changes)];parts=[]
        for i,first in enumerate(starts):
            after=starts[i+1] if i+1<len(starts) else len(frame)
            lower=utc(start) if i==0 else slots[first]
            upper=slots[after] if after<len(frame) else utc(end)
            parts.append(FundingSeries(frame.iloc[first:after],lower,upper).events)
        self.events=pd.concat(parts);self.coverage_start=utc(start);self.coverage_end=utc(end)
        if len(self.events)!=len(frame):raise AssertionError('Native funding calendar changed')

    def events_known_at(self,decision):
        decision=utc(decision)
        if any(b.first_new_slot<=decision and b.known_after>decision for b in self.boundaries):
            raise ValueError('Boundary explanation not known at this decision; cannot backdate capture')
        return self.events.loc[self.events.slot<=decision].copy()
