import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from r20_audit import retained
from r32_delayed_source import DATA,collect
from r32_publication import audit,parse_delayed_report


def body():return retained(DATA,collect())


def test_delayed_release_and_vintage_value_do_not_certify_availability():
    x=audit()
    assert x['publisher']['week_average_million_usd']==2955581
    assert x['archive_value_exactly_matches_publisher_average']
    assert x['source_publication_date_after_decision_eastern_date']
    assert x['vintage_label_is_not_sufficient_publication_clock_certificate']
    assert not x['full_required_source_gate'] and not x['adopted']


def test_forensic_report_rejects_wrong_release_identity():
    with pytest.raises(ValueError,match='release identity'):
        parse_delayed_report(body().replace(b'December 29, 2025',b'December 30, 2025'))


def test_forensic_report_cannot_take_stock_as_average():
    with pytest.raises(ValueError,match='semantic'):
        parse_delayed_report(body().replace(b't2r1c1 t2h1c2 t2h2c1 ',b't2r1c1 t2h1c3 '))


def test_forensic_report_cannot_guess_units():
    original=body();changed=original.replace(b'Millions',b'Billions')
    assert changed!=original
    with pytest.raises(ValueError,match='unit'):
        parse_delayed_report(changed)
