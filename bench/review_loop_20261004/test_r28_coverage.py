"""Stale shared observations cannot repair a missing fixed observation week."""
from r28_revision import parse_archive
from r28_coverage import describe_pair


def native(vintage,rows):
    body=('observation_date,NFCI_'+vintage.replace('-','')+'\n'+
          ''.join(day+','+value+'\n' for day,value in rows)).encode()
    return parse_archive(body,vintage)


def test_shared_stale_value_cannot_replace_exact_unknown_week():
    pair=dict(monday='2023-01-02',current_vintage='2022-12-31',
              prior_vintage='2022-12-24',observation_week='2022-12-16')
    current=native(pair['current_vintage'],[('2022-12-09','-0.21479')])
    prior=native(pair['prior_vintage'],[('2022-12-02','-0.21249'),('2022-12-09','-0.21479')])
    result=describe_pair(pair,current,prior)
    assert result['state']=='missing_required_observation'
    assert result['revision_decimal'] is None and not result['required_pair_known']
    assert result['missing_required_sides']==['current','prior']
    assert result['current_latest_observation']=='2022-12-09'
    assert result['prior_latest_observation']=='2022-12-09'


def test_reported_precision_change_is_preserved_without_fitted_noise_tolerance():
    pair=dict(monday='2023-06-05',current_vintage='2023-06-03',
              prior_vintage='2023-05-27',observation_week='2023-05-19')
    current=native(pair['current_vintage'],[('2023-05-19','-0.29')])
    prior=native(pair['prior_vintage'],[('2023-05-19','-0.29435')])
    result=describe_pair(pair,current,prior)
    assert result['state']=='known_same_week_revision' and result['required_pair_known']
    assert result['revision_decimal']=='0.00435'
    assert result['reported_decimal_places_differ']
    assert result['current_reported_decimal_places']==2 and result['prior_reported_decimal_places']==5
