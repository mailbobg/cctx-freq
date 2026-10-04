"""Historical version identity, signed precision and same-week revisions."""
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs,urlparse

import pytest

from r28_sources import probe_pairs,probe_targets,archive_url
from r28_revision import parse_archive,same_week_revision


def body(vintage,rows):
    return ('observation_date,NFCI_'+vintage.replace('-','')+'\n'+''.join(day+','+value+'\n' for day,value in rows)).encode()


def parsed(vintage,rows):
    return parse_archive(body(vintage,rows),vintage)


def test_fixed_probe_dates_roles_and_original_vintage_query():
    pairs=probe_pairs();assert len(pairs)==3 and len(probe_targets())==13
    first=pairs[0]
    assert first==dict(monday='2021-02-01',current_vintage='2021-01-30',prior_vintage='2021-01-23',observation_week='2021-01-15')
    q=parse_qs(urlparse(archive_url('2021-01-30')).query)
    assert q==dict(id=['NFCI'],vintage_date=['2021-01-30'],cosd=['2021-01-02'],coed=['2021-01-30'])


def test_negative_native_values_and_sub_float_revision_are_preserved():
    old=parsed('2021-01-23',[('2021-01-15','-0.5000000000000001')])
    now=parsed('2021-01-30',[('2021-01-15','-0.5000000000000000'),('2021-01-22','-0.4')])
    result=same_week_revision(probe_pairs()[0],now,old)
    assert result['state']=='known_same_week_revision'
    assert result['revision_decimal']=='0.0000000000000001'
    assert result['prior_native']=='-0.5000000000000001'
    assert result['current_native']=='-0.5000000000000000'
    assert result['observation_week']=='2021-01-15'
    assert not result['historical_first_public_receipt_certified']


def test_missing_blank_dot_and_actual_signed_zero_do_not_become_cash():
    old=parsed('2021-01-23',[('2021-01-15','-0.000')])
    for value in ('','.'):
        now=parsed('2021-01-30',[('2021-01-15',value)])
        out=same_week_revision(probe_pairs()[0],now,old)
        assert out['state']=='unknown_native_value' and out['revision_decimal'] is None
    out=same_week_revision(probe_pairs()[0],parsed('2021-01-30',[]),old)
    assert out['state']=='missing_required_observation' and out['revision_decimal'] is None
    known=same_week_revision(probe_pairs()[0],parsed('2021-01-30',[('2021-01-15','0.000')]),old)
    assert Decimal(known['revision_decimal'])==0 and known['state']=='known_same_week_revision'
    assert old['rows'][date(2021,1,15)]['native_value']=='-0.000'


@pytest.mark.parametrize('changed',[
    b'observation_date,ANFCI_20210130\n2021-01-15,-0.2\n',
    b'observation_date,NFCI_20210206\n2021-01-15,-0.2\n',
    b'observation_date,NFCI_2021-01-30\n2021-01-15,-0.2\n',
    body('2021-01-30',[('2021-01-14','-0.2')]),
    body('2021-01-30',[('2021-02-05','-0.2')]),
    body('2021-01-30',[('2021-01-15','NaN')]),
    body('2021-01-30',[('2021-01-15','inf')]),
    body('2021-01-30',[('2021-01-15','0.1'),('2021-01-15','0.2')]),
    body('2021-01-30',[('2021-01-22','0.1'),('2021-01-15','0.2')]),
])
def test_wrong_series_version_grid_future_duplicate_or_invalid_native_rejected(changed):
    with pytest.raises(ValueError):parse_archive(changed,'2021-01-30')


def test_wrong_vintage_pair_cannot_compare_different_known_versions():
    now=parsed('2021-01-30',[('2021-01-15','-0.2')])
    old=parsed('2021-01-30',[('2021-01-15','-0.1')])
    with pytest.raises(ValueError):same_week_revision(probe_pairs()[0],now,old)
