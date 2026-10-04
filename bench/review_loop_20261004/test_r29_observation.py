"""Native scope failure, unpublished/unknown rates and source identity stay visible."""
import pytest
from r29_observation import describe_native
from r29_sources import probe_targets, probe_pairs, archive_url


def body(rows):
    return ('observation_date,SOFR_20210130,EFFR_20210130\n'+
            ''.join(','.join(row)+'\n' for row in rows)).encode()


def test_frozen_four_vintages_twelve_original_roles():
    assert len(probe_pairs())==4 and len(probe_targets())==12
    assert 'vintage_date=2021-01-30%2C2021-01-30' in archive_url('2021-01-30')


def test_native_extra_old_rows_are_preserved_and_range_contract_fails():
    rows=[('2000-07-03','','7.03'),('2021-01-28','0.05','0.08')]
    result=describe_native(body(rows),'2021-01-30')
    assert result['original_rows']==2 and result['outside_requested_range_rows']==1
    assert not result['original_requested_range_contract_pass']
    assert result['first_original_observation']=='2000-07-03'
    assert result['latest_common_known_observation']=='2021-01-28'
    assert result['latest_common_sofr_native']=='0.05' and result['latest_common_effr_native']=='0.08'
    assert result['latest_common_difference_percentage_points']=='-0.03'
    assert result['latest_common_difference_basis_points']=='-3.00'
    assert not result['registered_alpha_or_performance']


def test_unknown_future_friday_not_backdated_and_real_zero_is_known():
    result=describe_native(body([('2021-01-28','0.00','0.00'),('2021-01-29','','.')]),'2021-01-30')
    assert result['original_requested_range_contract_pass']
    assert result['latest_common_known_observation']=='2021-01-28'
    assert result['latest_common_difference_percentage_points']=='0.00'
    assert result['native_unknown_cells']=={'SOFR':1,'EFFR':1}


@pytest.mark.parametrize('raw',[
    b'observation_date,SOFR_20210130,EFFR_20210206\n2021-01-28,0.05,0.08\n',
    b'observation_date,EFFR_20210130,SOFR_20210130\n2021-01-28,0.05,0.08\n',
    body([('2021-01-31','0.05','0.08')]),
    body([('2021-01-28','NaN','0.08')]),
    body([('2021-01-28','0.05','Infinity')]),
    body([('2021-01-28','0.05','0.08'),('2021-01-28','0.06','0.08')]),
    body([('2021-01-28','0.05','0.08'),('2021-01-27','0.05','0.08')]),
])
def test_foreign_version_identity_future_invalid_value_or_order_rejected(raw):
    with pytest.raises(ValueError):describe_native(raw,'2021-01-30')
