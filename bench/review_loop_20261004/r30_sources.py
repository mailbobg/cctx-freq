"""Bounded public read-only historical state probe, preserving native RPC failures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE/'r30_sources'
URL = 'https://ethereum.publicnode.com'
FACTORY = '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f'
TOKENS = ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
          '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48')
HEIGHTS = (11000000, 18000000, 22000000)
GET_PAIR = '0xe6a43905'+''.join(t[2:].rjust(64, '0') for t in TOKENS)
READ_METHODS = {'eth_chainId', 'eth_getBlockByNumber', 'eth_getBlockByHash',
                'eth_getCode', 'eth_call', 'eth_getStorageAt'}


def collect(fetch=False):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    path = DATA/'manifest.json'
    records = json.loads(path.read_text())['sources'] if path.exists() else {}
    skipped = []
    session = requests.Session()

    def save():
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(source_plan_commit='a84e75d', sources=records,
            conditional_roles_not_requested=skipped, source_stage_only=True,
            no_alpha_rule_cohort_weights_or_performance=True), indent=2, sort_keys=True)+'\n')
        tmp.replace(path)

    def request(key, method, params):
        if method not in READ_METHODS:
            raise ValueError('Unregistered or non-read-only RPC method')
        payload = dict(jsonrpc='2.0', id=int(hashlib.sha256(key.encode()).hexdigest()[:8], 16),
                       method=method, params=params)
        if key in records:
            record = records[key]
            if record['url'] != URL or record['request_json'] != payload:
                raise ValueError('Original historical RPC identity changed')
            body = (DATA/record['path']).read_bytes() if record.get('path') else None
            if body is not None and hashlib.sha256(body).hexdigest() != record['sha256']:
                raise ValueError('Original native RPC body changed')
        else:
            if not fetch:
                raise ValueError('Explicit original bounded fetch required '+key)
            start = datetime.now(timezone.utc).isoformat()
            try:
                response = session.post(URL, json=payload, timeout=(25, 25))
                body = response.content
                filename = hashlib.sha256((key+' '+json.dumps(payload, sort_keys=True)).encode()).hexdigest()
                raw = DATA/'raw'/filename
                if raw.exists():
                    raise ValueError('Unregistered original RPC response exists')
                raw.write_bytes(body)
                record = dict(url=URL, final_url=response.url, request_json=payload,
                    requested_utc=start, retrieved_utc=datetime.now(timezone.utc).isoformat(),
                    status=response.status_code, path='raw/'+filename, bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    headers={k:response.headers[k] for k in ('Date', 'Content-Type', 'ETag', 'Last-Modified') if k in response.headers})
            except requests.RequestException as exc:
                body = None
                record = dict(url=URL, request_json=payload, requested_utc=start,
                    failed_utc=datetime.now(timezone.utc).isoformat(), status=None, path=None,
                    source_error=type(exc).__name__+': '+str(exc))
            records[key] = record
            save()
            print(key, record['status'], record.get('bytes', 0), flush=True)
        if body is None or record['status'] != 200:
            return None
        try:
            reply = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        if reply.get('jsonrpc') != '2.0' or reply.get('id') != payload['id']:
            raise ValueError('RPC version/response identity differs')
        if 'error' in reply:
            return None  # Preserve the actual HTTP200/RPC error in the raw body.
        return reply.get('result')

    def address(value):
        if not isinstance(value, str) or len(value) != 66 or not value.startswith('0x'):
            return None
        if value[2:26] != '0'*24:
            raise ValueError('Unknown ABI address word')
        int(value[2:], 16)
        return None if int(value[2:], 16) == 0 else '0x'+value[-40:].lower()

    chain = request('chain', 'eth_chainId', [])
    if chain != '0x1':
        skipped.append(dict(parent='chain', roles='all_header_and_contract_roles',
                            reason='Mainnet identity/connectivity not established'))
        save()
        return records
    current = request('control_finalized', 'eth_getBlockByNumber', ['finalized', False])
    probes = [('height_'+str(h), hex(h)) for h in HEIGHTS]
    if isinstance(current, dict) and current.get('number'):
        probes.append(('control_state', current['number']))
    else:
        skipped.append(dict(parent='control_finalized', roles='current_state_control', reason='No known finalized header'))
    for key, height in probes:
        header = request(key+'_header', 'eth_getBlockByNumber', [height, False])
        if not isinstance(header, dict) or not header.get('hash'):
            skipped.append(dict(parent=key+'_header', roles='by_hash_and_native_state', reason='Required native header unavailable'))
            save()
            continue
        if header['number'] != height:
            raise ValueError('Provider substituted a different native block')
        counterpart = request(key+'_header_hash', 'eth_getBlockByHash', [header['hash'], False])
        if counterpart is not None and counterpart != header:
            raise ValueError('Native number/hash block counterparts differ')
        pair_value = request(key+'_factory_pair', 'eth_call', [dict(to=FACTORY, data=GET_PAIR), height])
        pair = address(pair_value)
        if pair is None:
            skipped.append(dict(parent=key+'_factory_pair', roles='pair_code_token_reserve_storage_decimals',
                                reason='Required historical pair lookup unavailable/zero'))
            save()
            continue
        request(key+'_pair_code', 'eth_getCode', [pair, height])
        identities = []
        for i, selector in enumerate(('0x0dfe1681', '0xd21220a7')):
            token = address(request(key+'_token'+str(i), 'eth_call', [dict(to=pair, data=selector), height]))
            identities.append(token)
            if token is not None:
                request(key+'_decimals'+str(i), 'eth_call', [dict(to=token, data='0x313ce567'), height])
        if all(t is not None for t in identities) and set(identities) != set(TOKENS):
            raise ValueError('Native factory-derived pair token identity differs')
        request(key+'_reserves', 'eth_call', [dict(to=pair, data='0x0902f1ac'), height])
        request(key+'_reserve_slot', 'eth_getStorageAt', [pair, '0x8', height])
        save()
    save()
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    collect(args.fetch)
