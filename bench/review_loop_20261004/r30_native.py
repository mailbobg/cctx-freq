"""Strict native state audit; current controls never substitute for denied history."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re

from r30_sources import DATA, FACTORY, GET_PAIR, HEIGHTS, READ_METHODS, TOKENS, URL


def parse_reply(body, status, request_id):
    if body is None:
        return dict(category='transport_failure', result=None)
    try:
        reply = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        if status != 200:
            return dict(category='http_denied' if status in (401, 403) else 'http_error', result=None)
        raise ValueError('Successful HTTP body is not native JSON-RPC')
    if not isinstance(reply, dict) or reply.get('jsonrpc') != '2.0' or reply.get('id') != request_id:
        raise ValueError('Native JSON-RPC identity differs')
    if ('result' in reply) == ('error' in reply):
        raise ValueError('Ambiguous/absent native RPC result/error')
    if status != 200:
        return dict(category='http_denied' if status in (401, 403) else 'http_error',
                    result=None, error=reply.get('error'))
    if 'error' in reply:
        return dict(category='rpc_error', result=None, error=reply['error'])
    return dict(category='result' if reply['result'] is not None else 'null_result', result=reply['result'])


def native_words(value, count):
    if not isinstance(value, str) or not re.fullmatch('0x[0-9a-fA-F]{'+str(64*count)+'}', value):
        raise ValueError('Native ABI word width/encoding differs')
    return tuple(int(value[2+i*64:2+(i+1)*64], 16) for i in range(count))


def decode_reserves(value):
    values = native_words(value, 3)
    if any(x >= 1 << width for x, width in zip(values, (112, 112, 32))):
        raise ValueError('Native reserve integer exceeds original ABI width')
    return values


def decode_slot(value):
    packed, = native_words(value, 1)
    return packed & ((1 << 112)-1), (packed >> 112) & ((1 << 112)-1), packed >> 224


def decode_address(value):
    native, = native_words(value, 1)
    if not 0 < native < 1 << 160:
        raise ValueError('Native address is zero or exceeds ABI width')
    return '0x'+format(native, '040x')


def quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)', value):
        raise ValueError('Native RPC quantity is not canonical')
    return int(value, 16)


def validate_state(tokens, decimals, reserves, storage, block_timestamp):
    expected = {TOKENS[0]:18, TOKENS[1]:6}
    if len(tokens) != 2 or set(tokens) != set(expected) or tuple(decimals) != tuple(expected[t] for t in tokens):
        raise ValueError('Known factory token identity/order/units differ')
    if tuple(reserves) != tuple(storage):
        raise ValueError('Native view and packed reserve storage differ')
    if reserves[2] > block_timestamp:
        raise ValueError('Native reserve update is after the fixed block')
    with localcontext() as context:
        context.prec = 80
        normalized = [format(Decimal(x)/Decimal(10**d), 'f') for x,d in zip(reserves[:2], decimals)]
    return dict(tokens=list(tokens), decimals=list(decimals),
                native_unsigned_reserves=[str(x) for x in reserves[:2]],
                native_last_update_timestamp=reserves[2], block_timestamp=block_timestamp,
                normalized_reserves=normalized,
                last_update_age_seconds=block_timestamp-reserves[2], view_matches_storage=True,
                same_provider_not_independent_or_merkle_proof=True)


def audit(data=DATA):
    manifest_path = data/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if (manifest['source_plan_commit'] != 'a84e75d' or not manifest['source_stage_only'] or
        not manifest['no_alpha_rule_cohort_weights_or_performance']):
        raise ValueError('Source-only preregistration identity differs')
    outcomes = {}
    hashes = {'manifest.json': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    records = manifest['sources']
    for role, record in records.items():
        request = record['request_json']
        identity = int(hashlib.sha256(role.encode()).hexdigest()[:8], 16)
        if (record['url'] != URL or request.get('jsonrpc') != '2.0' or request['id'] != identity or
            request['method'] not in READ_METHODS or set(request) != {'jsonrpc','id','method','params'}):
            raise ValueError('Native request identity or read-only scope differs')
        if record.get('path'):
            raw = data/record['path']
            if raw.parent != data/'raw':
                raise ValueError('Native body escapes registered raw scope')
            body = raw.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != record['sha256'] or len(body) != record['bytes']:
                raise ValueError('Original native response changed')
            hashes[record['path']] = digest
        else:
            body = None
        outcomes[role] = parse_reply(body, record['status'], identity)
    if outcomes['chain']['result'] != '0x1' or records['chain']['request_json']['params'] != []:
        raise ValueError('Ethereum mainnet identity missing')

    def result(role, method, params):
        if records[role]['request_json'] != dict(jsonrpc='2.0', id=int(hashlib.sha256(role.encode()).hexdigest()[:8],16), method=method, params=params):
            raise ValueError('Fixed historical role payload differs '+role)
        return outcomes[role]['result']

    finalized = result('control_finalized', 'eth_getBlockByNumber', ['finalized', False])
    probes = [('height_'+str(h), hex(h)) for h in HEIGHTS]+[('control_state', finalized['number'])]
    headers, history, controls, skipped, used = [], [], [], [], {'chain','control_finalized'}
    for prefix, height in probes:
        role = prefix+'_header'
        header = result(role, 'eth_getBlockByNumber', [height, False])
        used.add(role)
        if header is None:
            skipped.append(dict(parent=role, roles='by_hash_and_native_state', reason='Required native header unavailable'))
            history.append(dict(role=prefix, height=quantity(height), header_available=False,
                                native_reserves_available=False, source_failure=outcomes[role]))
            continue
        if header['number'] != height or not all(re.fullmatch(r'0x[0-9a-fA-F]{64}',header[k]) for k in ('hash','stateRoot')):
            raise ValueError('Fixed native header identity differs')
        if prefix == 'control_state' and header != finalized:
            raise ValueError('Finalized control and exact-number header differ')
        counterpart = result(prefix+'_header_hash', 'eth_getBlockByHash', [header['hash'], False])
        used.add(prefix+'_header_hash')
        if counterpart != header:
            raise ValueError('Native number/hash counterparts unavailable or different')
        stamp = quantity(header['timestamp'])
        header_fact = dict(role=prefix, height=quantity(height), block_hash=header['hash'],
                           state_root=header['stateRoot'], block_timestamp=stamp,
                           consensus_block_utc=datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                           number_hash_counterparts_match=True)
        headers.append(header_fact)
        pair_role = prefix+'_factory_pair'
        pair_native = result(pair_role, 'eth_call', [dict(to=FACTORY, data=GET_PAIR), height])
        used.add(pair_role)
        if pair_native is None:
            skipped.append(dict(parent=pair_role, roles='pair_code_token_reserve_storage_decimals',
                                reason='Required historical pair lookup unavailable/zero'))
            history.append(dict(**header_fact, header_available=True, native_reserves_available=False,
                                source_failure=outcomes[pair_role]))
            continue
        pair = decode_address(pair_native)
        code = result(prefix+'_pair_code', 'eth_getCode', [pair, height])
        used.add(prefix+'_pair_code')
        if not isinstance(code, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+', code):
            raise ValueError('Factory-derived pair lacks native code')
        tokens, decimals = [], []
        for i, selector in enumerate(('0x0dfe1681','0xd21220a7')):
            token = decode_address(result(prefix+'_token'+str(i), 'eth_call', [dict(to=pair, data=selector),height]))
            decimal, = native_words(result(prefix+'_decimals'+str(i), 'eth_call', [dict(to=token,data='0x313ce567'),height]),1)
            tokens.append(token); decimals.append(decimal)
            used.update({prefix+'_token'+str(i), prefix+'_decimals'+str(i)})
        reserves = decode_reserves(result(prefix+'_reserves', 'eth_call', [dict(to=pair,data='0x0902f1ac'),height]))
        storage = decode_slot(result(prefix+'_reserve_slot', 'eth_getStorageAt', [pair,'0x8',height]))
        used.update({prefix+'_reserves',prefix+'_reserve_slot'})
        state = dict(**header_fact, pair=pair, native_reserves_available=True,
            pair_bytecode_bytes=(len(code)-2)//2,
            native_state=validate_state(tokens,decimals,reserves,storage,stamp))
        (controls if prefix=='control_state' else history).append(state)
    if set(records) != used or manifest['conditional_roles_not_requested'] != skipped:
        raise ValueError('Unregistered extra role or altered native parent-failure scope')
    return dict(source_plan_commit='a84e75d', original_request_count=len(records),
        response_categories=dict(Counter(x['category'] for x in outcomes.values())),
        http_status_counts=dict(Counter(str(x['status']) for x in records.values())),
        native_headers=headers, fixed_historical_probes=history, current_connectivity_controls=controls,
        all_original_payloads_and_body_hashes_checked=True,
        native_input_sha256=hashes, historical_reserve_source_gate=all(x['native_reserves_available'] for x in history),
        original_first_public_clock_or_merkle_state_proof_certified=False,
        current_control_never_used_as_historical_state=True,
        no_alpha_rule_cohort_weights_or_performance=True, adopted=False,
        disposition='Historical source unavailable; retain failures, no alpha model or current-state substitution')


if __name__ == '__main__':
    report = audit()
    path = Path(__file__).resolve().parent/'r30_native_source_audit.json'
    path.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    print(json.dumps({k:report[k] for k in ('original_request_count','response_categories',
        'http_status_counts','historical_reserve_source_gate','adopted','disposition')}, indent=2))
