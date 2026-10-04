"""Historical access errors and native ABI identity cannot become reserve history."""
import json
import hashlib
from decimal import Decimal, localcontext
import shutil

import pytest

from r30_native import audit, decode_reserves, decode_slot, parse_reply, validate_state
from r30_sources import DATA


def words(*values):
    return '0x'+''.join(format(value, '064x') for value in values)


def state():
    return dict(tokens=('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                        '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'),
                decimals=(6, 18), reserves=(123456789, 987654321000000000, 1000),
                storage=(123456789, 987654321000000000, 1000), block_timestamp=1060)


def test_http200_pruned_history_remains_unavailable():
    reply = dict(jsonrpc='2.0', id=7, error=dict(code=4444, message='pruned history unavailable'))
    outcome = parse_reply(json.dumps(reply).encode(), 200, 7)
    assert outcome['result'] is None
    assert outcome['category'] == 'rpc_error'
    assert outcome['error'] == reply['error']


def test_http403_archive_access_does_not_become_zero_state():
    reply = dict(jsonrpc='2.0', id=7, error=dict(code=-32602, message='Archive requests require a personal token'))
    outcome = parse_reply(json.dumps(reply).encode(), 403, 7)
    assert outcome['result'] is None
    assert outcome['category'] == 'http_denied'


@pytest.mark.parametrize('reply', [dict(jsonrpc='1.0', id=7, result='0x1'),
    dict(jsonrpc='2.0', id=8, result='0x1'), dict(jsonrpc='2.0', id=7, result='0x1', error={}),
    dict(jsonrpc='2.0', id=7)])
def test_ambiguous_or_foreign_response_is_rejected(reply):
    with pytest.raises(ValueError):
        parse_reply(json.dumps(reply).encode(), 200, 7)


def test_native_uint112_uint112_uint32_and_packed_storage_agree():
    r0, r1, stamp = state()['reserves']
    packed = r0+(r1 << 112)+(stamp << 224)
    assert decode_reserves(words(r0, r1, stamp)) == decode_slot(words(packed)) == (r0, r1, stamp)
    checked = validate_state(**state())
    assert checked['normalized_reserves'] == ['123.456789', '0.987654321']
    assert checked['last_update_age_seconds'] == 60


@pytest.mark.parametrize('native', [words(1 << 112, 2, 3), words(1, 1 << 112, 3),
    words(1, 2, 1 << 32), words(1, 2), words(1, 2, 3)+'00', '0xzz'])
def test_invalid_native_widths_do_not_truncate(native):
    with pytest.raises(ValueError):
        decode_reserves(native)


@pytest.mark.parametrize('change', [dict(storage=(987654321000000000, 123456789, 1000)),
    dict(decimals=(18, 6)), dict(tokens=('0x'+'f'*40, '0x'+'e'*40)),
    dict(block_timestamp=999)])
def test_swapped_storage_wrong_units_foreign_tokens_or_future_update_fail(change):
    original = state()
    original.update(change)
    with pytest.raises(ValueError):
        validate_state(**original)


def test_full_uint112_native_decimal_normalization_retains_every_digit():
    original = state()
    reserve = (1 << 112)-1
    original.update(reserves=(reserve,reserve,1000), storage=(reserve,reserve,1000))
    checked = validate_state(**original)
    with localcontext() as context:
        context.prec = 80
        assert [Decimal(v)*10**d for v,d in zip(checked['normalized_reserves'],original['decimals'])] == [reserve,reserve]


def test_complete_original_probe_has_no_historical_reserve_or_alpha():
    checked = audit()
    assert checked['original_request_count'] == 19
    assert checked['response_categories'] == {'result':16,'http_denied':2,'rpc_error':1}
    assert not any(x['native_reserves_available'] for x in checked['fixed_historical_probes'])
    assert checked['current_connectivity_controls'][0]['native_state']['view_matches_storage']
    assert checked['no_alpha_rule_cohort_weights_or_performance'] and not checked['adopted']


@pytest.mark.parametrize('change',['body','historical_height','conditional_scope','finalized_header'])
def test_tampered_native_body_payload_parent_or_control_is_rejected(tmp_path, change):
    destination = tmp_path/'sources'
    shutil.copytree(DATA, destination)
    path = destination/'manifest.json'
    manifest = json.loads(path.read_text())
    if change == 'body':
        native = destination/manifest['sources']['chain']['path']
        native.write_bytes(native.read_bytes()+b' ')
    elif change == 'historical_height':
        manifest['sources']['height_18000000_factory_pair']['request_json']['params'][1] = 'latest'
    elif change == 'conditional_scope':
        manifest['conditional_roles_not_requested'] = []
    else:
        record = manifest['sources']['control_finalized']
        native = destination/record['path']
        reply = json.loads(native.read_bytes())
        reply['result']['stateRoot'] = '0x'+'f'*64
        native.write_text(json.dumps(reply))
        record.update(sha256=hashlib.sha256(native.read_bytes()).hexdigest(),bytes=native.stat().st_size)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        audit(destination)
