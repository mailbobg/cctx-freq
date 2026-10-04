"""Failure retention and explicit retry selection, with no live requests."""
import hashlib
import json

import pytest

import r22_sources as source


def records(tmp_path,monkeypatch,retry=True):
    monkeypatch.setattr(source,'DATA',tmp_path);(tmp_path/'raw').mkdir()
    key='spot_SOLUSDT_2021-01';url=source.month_targets(['2021-01'])[key]
    failed=dict(url=url,status=None,path=None,requested_utc='2026-10-04T00:00:00+00:00',
        failed_utc='2026-10-04T00:00:01+00:00',source_error='ReadTimeout: retained original failure')
    out={key:failed}
    if retry:
        body=b'unchanged native response';name=key+'_retry_test'
        (tmp_path/'raw'/'body').write_bytes(body)
        out[name]=dict(url=url,status=200,path='raw/body',bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
            requested_utc='2026-10-04T00:00:02+00:00',retrieved_utc='2026-10-04T00:00:03+00:00')
    (tmp_path/'manifest.json').write_text(json.dumps(dict(sources=out)))
    def no_http(*args,**kwargs):raise AssertionError('Cached source replay must never fetch')
    monkeypatch.setattr(source.requests,'get',no_http)
    return key,url,out


def test_retry_selection_preserves_failed_original_without_implicit_http(tmp_path,monkeypatch):
    key,url,before=records(tmp_path,monkeypatch)
    body,record,name=source.selected({key:url})[key]
    assert body==b'unchanged native response' and name==key+'_retry_test'
    assert json.loads((tmp_path/'manifest.json').read_text())['sources']==before
    assert before[key]['status'] is None


def test_failed_source_without_explicit_retained_retry_halts(tmp_path,monkeypatch):
    key,url,_=records(tmp_path,monkeypatch,retry=False)
    with pytest.raises(ValueError,match='Source failure is not a cash/zero feature'):source.selected({key:url})


def test_bad_retained_retry_body_halts(tmp_path,monkeypatch):
    key,url,_=records(tmp_path,monkeypatch)
    (tmp_path/'raw'/'body').write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='Retained original body changed'):source.selected({key:url})


def test_explicit_fetch_reuses_worker_connection_and_persists_atomic_manifest(tmp_path,monkeypatch):
    monkeypatch.setattr(source,'DATA',tmp_path);calls=[];clients=[]
    class Client:
        def __init__(self):clients.append(self)
        def get(self,url,timeout):
            calls.append((url,timeout))
            class Response:
                content=b'original body';status_code=200;headers={}
            r=Response();r.url=url;return r
    monkeypatch.setattr(source.requests,'Session',Client)
    targets={'one':'https://data.binance.vision/one','two':'https://data.binance.vision/two'}
    source.collect(targets,fetch=True,workers=1,timeouts=(25,25))
    assert len(clients)==1 and [url for url,_ in calls]==list(targets.values())
    assert not (tmp_path/'manifest.json.tmp').exists()
    frozen=json.loads((tmp_path/'manifest.json').read_text())['sources']
    assert set(frozen)==set(targets)
    assert all(r['request_timeout_seconds']==[25,25] and r['collector_workers']==1 for r in frozen.values())
