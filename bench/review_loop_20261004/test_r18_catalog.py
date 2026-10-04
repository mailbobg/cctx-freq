"""Current-snapshot source tests; no synthetic historical publication proof."""
import copy
import json

import pytest

from r18_catalog import parse_page,stable_catalog


def body(rows,total=2):
    return json.dumps({'code':'000000','success':True,'data':{'catalogs':[{'catalogId':161,'catalogName':'Delisting','total':total,'articles':rows}]}}).encode()


def row(i=1):return {'id':i,'code':f'{i:032x}','title':f'Notice {i}','type':1,'releaseDate':1700000000000+i}


def test_exact_integer_identity_and_current_clock_retained():
    total,rows=parse_page(body([row()]))
    assert total==2 and rows==[row()]
    for field,value in [('id',True),('releaseDate',1700000000000.),('releaseDate',1700000000),('code','../code'),('title','')]:
        bad=row();bad[field]=value
        with pytest.raises(ValueError):parse_page(body([bad]))


def test_provider_error_catalog_mismatch_duplicate_rows_fail():
    for replace in [lambda x:x.update(success=False),lambda x:x['data']['catalogs'][0].update(catalogId=48),
                    lambda x:x['data']['catalogs'][0].update(total=True)]:
        parsed=json.loads(body([row()]));replace(parsed)
        with pytest.raises(ValueError):parse_page(json.dumps(parsed).encode())
    with pytest.raises(ValueError):parse_page(body([row(),row()]))


def test_stable_full_snapshot_not_imputed_historical_coverage():
    pages=[parse_page(body([row(1)],2)),parse_page(body([row(2)],2))]
    assert stable_catalog(pages,pages[0],page_size=1)==[row(1),row(2)]
    for broken in [[pages[0]], [pages[0],pages[0]], [pages[0],(3,[row(2)])],[(2,[]),pages[1]]]:
        with pytest.raises(ValueError):stable_catalog(broken,pages[0],page_size=1)
    with pytest.raises(ValueError):stable_catalog(pages,(2,[row(3)]),page_size=1)
    changed=copy.deepcopy(pages[0]);changed[1][0]['title']='new body label'
    with pytest.raises(ValueError):stable_catalog(pages,changed,page_size=1)


def test_conflicting_id_or_code_across_pages_and_partial_last_page_fail():
    first=[row(1),row(2)];other=row(3);other['code']=row(1)['code']
    with pytest.raises(ValueError):stable_catalog([(3,first),(3,[other])],(3,first),page_size=2)
    other=row(3);other['id']=1
    with pytest.raises(ValueError):stable_catalog([(3,first),(3,[other])],(3,first),page_size=2)
    with pytest.raises(ValueError):stable_catalog([(4,first),(4,[row(3)])],(4,first),page_size=2)
