from dataclasses import replace
from decimal import Decimal, localcontext

import pandas as pd
import pytest

from r16_knowledge import SourceEvidence, UnlockEvent, WindowCoverage, select_known_tokens


START = '2024-01-01T00:00:00Z'
END = '2024-01-15T00:00:00Z'
CATEGORIES = ('insiders', 'privateSale')


def source(**changes):
    defaults = dict(source_id='https://example.test/immutable-schedule',
                    sha256='a'*64, known_after=START,
                    evidence_type='local_capture', precision='exact')
    defaults.update(changes)
    return SourceEvidence(**defaults)


def event(event_id='e1', timestamp='2024-01-12T00:00:00Z', tokens='0.1', **changes):
    defaults = dict(event_id=event_id, asset='APT', event_at=timestamp,
                    tokens=tokens, category='insiders', unit='token', evidence=source())
    defaults.update(changes)
    return UnlockEvent(**defaults)


def coverage(ids=('e1',), **changes):
    defaults = dict(asset='APT', start=START, end=END, categories=CATEGORIES,
                    evidence=source(), complete=True, expected_event_ids=ids)
    defaults.update(changes)
    return WindowCoverage(**defaults)


def query(events, coverages, decision=START, start=START, end=END):
    return select_known_tokens(events, coverages, asset='APT', decision=decision,
                               start=start, end=end, categories=CATEGORIES)


def test_exact_publication_boundary_and_decimal_not_binary_float():
    events = [event(tokens='0.1'), event('e2', tokens='0.2')]
    cov = coverage(ids=('e1', 'e2'))
    assert query(events, [cov], decision='2023-12-31T23:59:59.999999Z').tokens is None
    result = query(events, [cov])
    assert result.known and result.tokens == Decimal('0.3')
    assert result.event_ids == ('e1', 'e2')


def test_date_only_knowledge_is_next_utc_midnight_not_start_of_label_day():
    evidence = source(known_after='2023-12-31', precision='date')
    events = [event(evidence=evidence)]
    cov = coverage(evidence=evidence)
    assert not query(events, [cov], decision='2023-12-31T23:59:59Z').known
    assert query(events, [cov], decision=START).known


def test_unknown_and_current_page_or_git_clocks_are_not_verified_history():
    for evidence in [source(known_after=None),
                     source(evidence_type='current_page_date_label'),
                     source(evidence_type='git_author'),
                     source(evidence_type='git_committer'),
                     source(evidence_type='github_server_event')]:
        result = query([event(evidence=evidence)], [coverage(evidence=evidence)])
        assert not result.known and result.tokens is None
    verified = source(evidence_type='verified_publication')
    assert query([event(evidence=verified)], [coverage(evidence=verified)]).known


def test_event_window_open_start_closed_end():
    events = [event('left', timestamp=START, tokens='1'),
              event('inside', tokens='2'),
              event('right', timestamp=END, tokens='3'),
              event('after', timestamp='2024-01-15T00:00:00.000001Z', tokens='4')]
    result = query(events, [coverage(ids=('inside', 'right'))])
    assert result.tokens == Decimal('5') and result.event_ids == ('inside', 'right')


def test_known_empty_requires_explicit_complete_window_coverage():
    assert not query([], []).known
    assert not query([], [coverage(ids=(), complete=False)]).known
    assert not query([], [coverage(ids=(), end='2024-01-14T23:59:59Z')]).known
    result = query([], [coverage(ids=())])
    assert result.known and result.tokens == Decimal('0')


def test_no_automatic_completeness_from_some_rows_or_filtered_late_rows():
    assert not query([event()], []).known
    assert not query([event()], [coverage(ids=('e1', 'missing'))]).known
    assert not query([event()], [coverage(ids=())]).known
    late = source(known_after='2024-01-02T00:00:00Z')
    assert not query([event(evidence=late)], [coverage()]).known
    unknown = source(known_after=None)
    assert not query([event(evidence=unknown)], [coverage()]).known


def test_vintage_identity_and_category_coverage_cannot_be_mixed():
    assert not query([event(evidence=source(sha256='b'*64))], [coverage()]).known
    assert not query([event()], [coverage(asset='TIA')]).known
    assert not query([event()], [coverage(categories=('insiders',))]).known
    # Events in an unrequested category do not become included supply.
    other = event('other', category='ecosystem', tokens='100')
    assert query([event(), other], [coverage()]).tokens == Decimal('0.1')


def test_latest_known_revision_only_future_revision_cannot_rewrite_history():
    old = source(known_after='2023-12-30T00:00:00Z')
    new = source(sha256='b'*64, known_after='2024-01-02T00:00:00Z')
    events = [event(evidence=old), event(tokens='0.9', evidence=new)]
    coverages = [coverage(evidence=old), coverage(evidence=new)]
    assert query(events, coverages).tokens == Decimal('0.1')
    assert query(events, coverages, decision='2024-01-02T00:00:00Z').tokens == Decimal('0.9')
    # A newly known incomplete revision does not silently fall back to an old one.
    coverages[-1] = replace(coverages[-1], complete=False)
    assert not query(events, coverages, decision='2024-01-02T00:00:00Z').known


def test_conflicting_equal_time_vintages_fail_closed():
    other = source(sha256='b'*64)
    result = query([event(), event(tokens='0.9', evidence=other)],
                   [coverage(), coverage(evidence=other)])
    assert not result.known and result.tokens is None


def test_timezone_conversion_and_naive_timestamps_fail():
    evidence = source(known_after='2024-01-01T08:00:00+08:00')
    assert query([event(evidence=evidence)], [coverage(evidence=evidence)]).known
    with pytest.raises(ValueError, match='timezone'):
        source(known_after='2024-01-01T00:00:00')
    with pytest.raises(ValueError, match='timezone'):
        event(timestamp='2024-01-12')
    with pytest.raises(ValueError, match='timezone'):
        query([], [], decision='2024-01-01')
    with pytest.raises(ValueError, match='(?i)date-only'):
        source(known_after='2023-12-31T12:00:00Z', precision='date')


@pytest.mark.parametrize('tokens', ['-1', 'NaN', 'Infinity', '-Infinity', 0.1, True, '', None])
def test_amount_must_be_exact_nonnegative_finite_native_tokens(tokens):
    with pytest.raises(ValueError, match='decimal|amount'):
        event(tokens=tokens)


@pytest.mark.parametrize('changes', [{'unit': 'USDT'}, {'unit': 'thousand_tokens'},
                                   {'asset': ''}, {'event_id': ''}, {'category': ''}])
def test_row_identity_and_unit_required(changes):
    with pytest.raises(ValueError):
        event(**changes)


@pytest.mark.parametrize('changes', [{'sha256': 'bad'}, {'source_id': ''},
                                   {'precision': 'guessed'}, {'evidence_type': 'made_up'}])
def test_source_identity_and_evidence_types_are_explicit(changes):
    with pytest.raises(ValueError):
        source(**changes)


def test_duplicate_ids_bad_windows_and_boolean_completeness_rejected():
    with pytest.raises(ValueError, match='(?i)duplicate'):
        query([event(), event(tokens='2')], [coverage()])
    with pytest.raises(ValueError, match='(?i)window'):
        coverage(start=END, end=START)
    with pytest.raises(ValueError, match='(?i)window'):
        query([], [], start=END, end=START)
    with pytest.raises(ValueError, match='complete'):
        coverage(complete='yes')
    with pytest.raises(ValueError, match='(?i)duplicate'):
        coverage(ids=('e1', 'e1'))


def test_decimal_sum_preserves_widely_separated_digits_and_date_evidence_copy():
    with localcontext() as context:
        context.prec = 6
        result = query([event(tokens='1E100'), event('e2', tokens='1E-100')],
                       [coverage(ids=('e1', 'e2'))])
        assert result.tokens == Decimal('1'+'0'*100+'.'+'0'*99+'1')
    evidence = source(known_after='2023-12-31', precision='date')
    assert replace(evidence) == evidence


def test_full_source_and_asof_prefix_output_equal_with_later_vintages():
    early = source(known_after='2023-12-20T00:00:00Z')
    late = source(sha256='b'*64, known_after='2024-01-08T00:00:00Z')
    events = [event(evidence=early), event(tokens='12', evidence=late)]
    covers = [coverage(evidence=early), coverage(evidence=late)]
    for decision in pd.date_range('2024-01-01', '2024-01-10', tz='UTC'):
        full = query(events, covers, decision=decision)
        visible_events = [e for e in events if e.evidence.usable_at(decision)]
        visible_covers = [c for c in covers if c.evidence.usable_at(decision)]
        assert full == query(visible_events, visible_covers, decision=decision)
