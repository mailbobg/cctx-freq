"""Signed known revisions, native counterpart fidelity and no missingness cash."""
import copy
from decimal import Decimal

import pandas as pd
import pytest

from r28_revision import parse_archive
from r28_policy import feature, overlay


def native(vintage, value, digest):
    data=('observation_date,NFCI_'+vintage.replace('-','')+'\n2021-01-15,'+value+'\n').encode()
    out=parse_archive(data,vintage)
    out.update(source_sha256=digest*64,source_record='archive_'+vintage,
               current_capture_utc='2026-10-04T16:00:00+00:00')
    return out


def f(now, old):
    return feature(pd.Timestamp('2021-02-01',tz='UTC'),
        native('2021-01-30',now,'a'),native('2021-01-23',old,'b'))


def base():
    return pd.DataFrame({'BTC':[.2],'ETH':[.1]},
                        index=pd.DatetimeIndex(['2021-01-31'],tz='UTC'))


@pytest.mark.parametrize('now,old,allow',[
    ('-0.4','-0.5',False),('-0.6','-0.5',True),('0.000','-0.000',True),
    ('-0.5000000000000000','-0.5000000000000001',False),
])
def test_signed_exact_revision_rule_and_original_joint_targets(now,old,allow):
    value=f(now,old);assert value['allow']==allow
    assert Decimal(value['revision_decimal'])==Decimal(now)-Decimal(old)
    out,audit=overlay(base(),{value['decision']:value})
    pd.testing.assert_frame_equal(out,base() if allow else base()*0.)
    assert len(audit)==1


@pytest.mark.parametrize('unknown',['','.'])
def test_unknown_required_source_halts_before_weights(unknown):
    with pytest.raises(ValueError):f(unknown,'-0.5')


def test_missing_required_decision_is_not_economic_veto():
    with pytest.raises(ValueError):overlay(base(),{})


@pytest.mark.parametrize('key,value',[
    ('revision_decimal','0'),('allow',True),('current_native','-0.7'),
    ('source_sha256',''),('prior_source_sha256',''),('prior_vintage','2021-01-30'),
    ('observation_day',pd.Timestamp('2021-01-22',tz='UTC')),
    ('available_at_assumed',pd.Timestamp('2021-02-02',tz='UTC')),
])
def test_altered_native_revision_identity_hash_or_clock_rejected(key,value):
    item=copy.deepcopy(f('-0.4','-0.5'));item[key]=value
    with pytest.raises(ValueError):overlay(base(),{item['decision']:item})
