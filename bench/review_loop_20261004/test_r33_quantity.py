from copy import deepcopy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from r33_calendar import cohort
from r33_quantity import feature,overlay
import pandas as pd


def fixture():
    pair=next(x for x in cohort() if x['monday']=='2026-01-26')
    reports={}
    for kind,value in [('current',100),('prior',110)]:
        r=pair[kind];reports[r['release_date']]=dict(release_date=r['release_date'],
            observation_date=r['expected_observation_date'],native_units='Millions of dollars',
            week_average_million_usd=value,source_body_sha256='a'*64)
    return pair,reports


def test_strict_contraction_equality_and_expansion():
    pair,reports=fixture();assert feature(pair,reports)['contraction']
    for value in [110,111]:
        reports[pair['current']['release_date']]['week_average_million_usd']=value
        assert not feature(pair,reports)['contraction']


def test_absent_quantity_cannot_be_zero_or_favorable_cash():
    pair,reports=fixture();reports[pair['current']['release_date']]['week_average_million_usd']=None
    with pytest.raises(ValueError,match='quantity'):feature(pair,reports)


def test_exact_observation_not_nearest_report():
    pair,reports=fixture();reports[pair['prior']['release_date']]['observation_date']='2025-12-17'
    with pytest.raises(ValueError,match='observation'):feature(pair,reports)


def test_no_unit_inference_from_magnitude():
    pair,reports=fixture();reports[pair['current']['release_date']]['native_units']='Billions of dollars'
    with pytest.raises(ValueError,match='unit'):feature(pair,reports)


def test_future_eligibility_cannot_supply_current_feature():
    pair,reports=fixture();pair=deepcopy(pair);pair['current']['modeled_eligible_utc']='2026-01-27T00:00:00+00:00'
    with pytest.raises(ValueError,match='eligibility'):feature(pair,reports)


def test_joint_veto_no_companion_rescale_or_missing_feature_default():
    pair,reports=fixture();f=feature(pair,reports)
    idx=pd.DatetimeIndex(['2026-01-25'],tz='UTC');base=pd.DataFrame({'BTC':[.05],'ETH':[.025]},index=idx)
    monday=idx[0]+pd.Timedelta(days=1)
    out,audit=overlay(base,{monday:f});assert out.iloc[0].eq(0).all()
    reports[pair['current']['release_date']]['week_average_million_usd']=110
    f=feature(pair,reports);same,_=overlay(base,{monday:f});pd.testing.assert_frame_equal(same,base)
    with pytest.raises(ValueError,match='feature'):overlay(base,{})
