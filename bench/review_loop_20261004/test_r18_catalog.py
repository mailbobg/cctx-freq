"""Current-snapshot source tests; no synthetic historical publication proof."""
import copy
import json

import pytest

from r18_catalog import parse_page,stable_catalog,parse_detail,body_text


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


def detail(code='0'*32):
    return {'code':'000000','success':True,'data':{'code':code,'title':'Binance Will Delist ALPACA',
       'publishDate':1745472005257,'lastUpdateTime':0,'articleType':1,
       'body':json.dumps({'node':'root','child':[{'node':'element','tag':'p','attr':{'style':'irrelevant'},'child':[{'node':'text','text':'Native article text'}]}]})}}


def test_detail_identity_and_role_must_match_source_not_http_status():
    raw=detail();parsed=parse_detail(json.dumps(raw).encode(),'0'*32)
    assert parsed['title']=='Binance Will Delist ALPACA'
    assert body_text(parsed['body'])=='Native article text'
    with pytest.raises(ValueError,match='identity'):parse_detail(json.dumps(raw).encode(),'1'*32)
    with pytest.raises(ValueError,match='role'):parse_detail(json.dumps(raw).encode(),'0'*32,title_pattern='Portfolio')
    raw['data']['title']=''
    with pytest.raises(ValueError):parse_detail(json.dumps(raw).encode(),'0'*32)


def test_current_update_zero_is_unknown_not_historical_unrevised_proof():
    parsed=parse_detail(json.dumps(detail()).encode(),'0'*32)
    assert parsed['lastUpdateTime']==0
    for field,value in [('lastUpdateTime',False),('lastUpdateTime',-1),('publishDate',1745472005)]:
        raw=detail();raw['data'][field]=value
        with pytest.raises(ValueError):parse_detail(json.dumps(raw).encode(),'0'*32)


def test_structured_body_only_text_children_not_layout_or_executable_code():
    raw=json.dumps({'node':'root','attr':{'text':'not body'},'child':[{'node':'text','text':'ALPACA'},
                   {'node':'element','tag':'p','child':[{'node':'text','text':'USDT contract'}]}]})
    assert body_text(raw)=='ALPACA USDT contract'
    for value in ['<html>unknown body</html>',json.dumps({'child':[]}),json.dumps({'node':'root','child':'not a child list'})]:
        with pytest.raises(ValueError):body_text(value)
