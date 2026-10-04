"""Research-only unlock knowledge gate. No trading, P&L or publisher-clock inference.

Evidence and completeness are caller assertions requiring retained source proof.
This helper validates those assertions and fails closed; it cannot authenticate
a claimed historical publication or discover undisclosed events by itself.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
import re

import pandas as pd


EVIDENCE_TYPES = frozenset({'local_capture', 'verified_publication',
                          'current_page_date_label', 'git_author',
                          'git_committer', 'github_server_event'})
ADMISSIBLE_TYPES = frozenset({'local_capture', 'verified_publication'})


def utc(value):
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Valid timezone-aware timestamp required') from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Valid timezone-aware timestamp required')
    return stamp.tz_convert('UTC')


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Nonempty {field} identity required')
    return value


def window(start, end):
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError('Window start must precede window end')
    return start, end


def decimal_amount(value):
    if not isinstance(value, (str, Decimal)):
        raise ValueError('Exact decimal string or Decimal amount required')
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Valid decimal amount required') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError('Finite nonnegative token amount required')
    return amount


@dataclass(frozen=True)
class SourceEvidence:
    source_id: str
    sha256: str
    known_after: object
    evidence_type: str
    precision: str = 'exact'
    available_at: object = field(init=False, default=None)

    def __post_init__(self):
        nonempty(self.source_id, 'source')
        if not isinstance(self.sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('Source content SHA256 identity required')
        if self.evidence_type not in EVIDENCE_TYPES:
            raise ValueError('Explicit supported evidence type required')
        if self.precision not in ('exact', 'date'):
            raise ValueError('Knowledge precision must be exact or date')
        if self.known_after is None:
            return
        if self.precision == 'date':
            if not isinstance(self.known_after, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.known_after):
                raise ValueError('Date-only knowledge must be a YYYY-MM-DD label')
            try:
                stamp = pd.Timestamp(self.known_after, tz='UTC') + pd.Timedelta(days=1)
            except ValueError as exc:
                raise ValueError('Valid date-only knowledge required') from exc
        else:
            stamp = utc(self.known_after)
        if self.precision == 'exact':
            object.__setattr__(self, 'known_after', stamp)
        object.__setattr__(self, 'available_at', stamp)

    def usable_at(self, decision):
        decision = utc(decision)
        return (self.evidence_type in ADMISSIBLE_TYPES and self.available_at is not None
                and self.available_at <= decision)


@dataclass(frozen=True)
class UnlockEvent:
    event_id: str
    asset: str
    event_at: object
    tokens: object
    category: str
    unit: str
    evidence: SourceEvidence

    def __post_init__(self):
        for field in ('event_id', 'asset', 'category'):
            nonempty(getattr(self, field), field)
        if self.unit != 'token':
            raise ValueError('Event unit must be native token; no scale or price inference')
        if not isinstance(self.evidence, SourceEvidence):
            raise ValueError('Explicit source evidence required')
        object.__setattr__(self, 'event_at', utc(self.event_at))
        object.__setattr__(self, 'tokens', decimal_amount(self.tokens))


@dataclass(frozen=True)
class WindowCoverage:
    asset: str
    start: object
    end: object
    categories: tuple
    evidence: SourceEvidence
    complete: bool
    expected_event_ids: tuple

    def __post_init__(self):
        nonempty(self.asset, 'asset')
        start, end = window(self.start, self.end)
        object.__setattr__(self, 'start', start)
        object.__setattr__(self, 'end', end)
        if type(self.complete) is not bool:
            raise ValueError('Coverage complete must be an explicit boolean')
        if not isinstance(self.evidence, SourceEvidence):
            raise ValueError('Explicit source evidence required')
        for field in ('categories', 'expected_event_ids'):
            values = getattr(self, field)
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f'Explicit tuple of {field} required')
            if len(set(values)) != len(values):
                raise ValueError(f'Duplicate {field} is ambiguous')
        if not self.categories:
            raise ValueError('Coverage categories required')


@dataclass(frozen=True)
class KnowledgeResult:
    known: bool
    tokens: Decimal | None
    event_ids: tuple
    reason: str
    evidence: SourceEvidence | None = None


def select_known_tokens(events, coverages, *, asset, decision, start, end, categories):
    """Return known scheduled tokens in (start,end], or unknown without a numeric zero.

    Use the latest admissible coverage vintage, with no stale fallback following
    an incomplete revision. Its enumerated rows must match both its source clock
    and its explicit complete range/category inventory. No subset implies coverage.
    """
    nonempty(asset, 'asset')
    decision = utc(decision)
    start, end = window(start, end)
    if not isinstance(categories, tuple) or not categories or len(set(categories)) != len(categories):
        raise ValueError('Explicit unique query categories required')
    for category in categories:
        nonempty(category, 'category')
    events, coverages = tuple(events), tuple(coverages)
    if any(not isinstance(e, UnlockEvent) for e in events) or any(not isinstance(c, WindowCoverage) for c in coverages):
        raise ValueError('Validated event and coverage records required')
    ids = [(e.evidence.source_id, e.evidence.sha256, e.event_id) for e in events]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate event IDs within a source vintage')
    eligible = [c for c in coverages if c.asset == asset and c.start <= start and c.end >= end
                and set(categories) <= set(c.categories) and c.evidence.usable_at(decision)]
    if not eligible:
        return KnowledgeResult(False, None, (), 'No known complete window coverage')
    newest = max(c.evidence.available_at for c in eligible)
    candidates = [c for c in eligible if c.evidence.available_at == newest]
    if len(candidates) != 1:
        return KnowledgeResult(False, None, (), 'Ambiguous equal-time coverage vintages')
    cover = candidates[0]
    if not cover.complete:
        return KnowledgeResult(False, None, (), 'Latest known coverage is incomplete', cover.evidence)
    # Compare the entire asserted coverage inventory, not only the query subset.
    source_rows = [e for e in events if e.asset == asset
                   and (e.evidence.source_id, e.evidence.sha256) == (cover.evidence.source_id, cover.evidence.sha256)
                   and cover.start < e.event_at <= cover.end and e.category in cover.categories]
    if set(e.event_id for e in source_rows) != set(cover.expected_event_ids):
        return KnowledgeResult(False, None, (), 'Coverage event inventory absent or incomplete', cover.evidence)
    if any(e.evidence != cover.evidence or not e.evidence.usable_at(decision) for e in source_rows):
        return KnowledgeResult(False, None, (), 'Row knowledge does not match known coverage vintage', cover.evidence)
    selected = sorted((e for e in source_rows if start < e.event_at <= end and e.category in categories),
                      key=lambda e: (e.event_at, e.event_id))
    # Preserve all native decimal digits even if a caller lowers Decimal context.
    least_exponent = min((e.tokens.as_tuple().exponent for e in selected), default=0)
    highest_place = max((len(e.tokens.as_tuple().digits) + e.tokens.as_tuple().exponent
                         for e in selected), default=1)
    required_precision = highest_place - least_exponent + len(str(max(1, len(selected)))) + 2
    with localcontext() as context:
        context.prec = max(context.prec, required_precision)
        total = sum((e.tokens for e in selected), Decimal('0'))
    return KnowledgeResult(True, total, tuple(e.event_id for e in selected),
                           'Known enumerated schedule; forecast supply is not actual sale', cover.evidence)
