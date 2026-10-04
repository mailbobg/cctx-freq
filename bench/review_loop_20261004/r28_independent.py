"""Independent direct native-CSV reconciliation of the source-only rejection."""
from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
import hashlib
import io
import json
import re

from r20_audit import retained
import r28_cohort_sources as source


def verify():
    path = source.HERE/'r28_source_coverage.json'
    audit = json.loads(path.read_text())
    for name, sha in audit['retained_inputs_sha256'].items():
        if hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Independent native retained source hash differs')
    for name, sha in audit['code_sha256'].items():
        if hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Independent frozen coverage code differs')
    records = json.loads((source.DATA/'manifest.json').read_text())['sources']
    originals = source.targets()
    cache = {}
    precision = Counter()
    for version in audit['native_version_coverage']:
        vintage = version['vintage']
        record = records[version['source_record']]
        if record['url'] != originals['archive_'+vintage] or record['sha256'] != version['source_sha256']:
            raise ValueError('Independent native version/source counterpart differs')
        body = retained(source.DATA, record)
        reader = csv.reader(io.StringIO(body.decode('utf-8')))
        if next(reader) != ['observation_date', 'NFCI_'+vintage.replace('-', '')]:
            raise ValueError('Independent native version column differs')
        rows = {}
        last = None
        cutoff = date.fromisoformat(vintage)
        for fields in reader:
            if len(fields) != 2:
                raise ValueError('Independent native row width differs')
            day = date.fromisoformat(fields[0])
            if (day.isoformat() != fields[0] or day.weekday() != 4 or
                not cutoff-timedelta(days=28) <= day <= cutoff or last is not None and day <= last):
                raise ValueError('Independent native date/range/order differs')
            text = fields[1]
            if text not in ('', '.') and not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
                raise ValueError('Independent native signed text differs')
            rows[fields[0]] = text
            precision[str(None if text in ('', '.') else max(0, -Decimal(text).as_tuple().exponent))] += 1
            last = day
        if len(rows) != version['native_rows']:
            raise ValueError('Independent native row count differs')
        cache[vintage] = rows
    expected = source.decision_pairs()
    if len(audit['exact_same_week_coverage']) != len(expected) or len(cache) != 293:
        raise ValueError('Independent original calendar coverage differs')
    missing, known, changed_precision, checked = [], 0, 0, 0
    for pair, fact in zip(expected, audit['exact_same_week_coverage']):
        if any(fact[k] != v for k, v in pair.items()):
            raise ValueError('Independent fixed observation week/calendar differs')
        texts = {}
        for side, key in [('current', 'current_vintage'), ('prior', 'prior_vintage')]:
            text = cache[pair[key]].get(pair['observation_week'])
            if text != fact[side+'_native']:
                raise ValueError('Declared same-week value differs from native original')
            texts[side] = text
            checked += 1
        absent = [side for side, text in texts.items() if text is None]
        if absent:
            state = 'missing_required_observation'
            revision = None
            missing.append(dict(monday=pair['monday'], observation_week=pair['observation_week'], sides=absent))
        elif any(text in ('', '.') for text in texts.values()):
            state, revision = 'unknown_native_value', None
        else:
            with localcontext() as ctx:
                ctx.prec = max(50, sum(len(text) for text in texts.values())+2)
                revision = format(Decimal(texts['current'])-Decimal(texts['prior']), 'f')
            state = 'known_same_week_revision'
            known += 1
            changed_precision += int(Decimal(texts['current']).as_tuple().exponent != Decimal(texts['prior']).as_tuple().exponent)
        if state != fact['state'] or revision != fact['revision_decimal']:
            raise ValueError('Independent same-week signed difference/state differs')
    if (known != audit['known_required_pairs'] or changed_precision != audit['pairs_with_reported_precision_change'] or
        sum(len(v['sides']) for v in missing) != audit['missing_required_native_cells'] or
        dict(precision) != audit['native_reported_decimal_place_counts'] or
        audit['source_gate_permits_full_alpha_screen'] != (known == len(expected)) or
        audit['alpha_weight_cohort_or_outcomes_evaluated'] or audit['new_performance'] or audit['adopted']):
        raise ValueError('Independent source rejection summary or performance scope differs')
    return dict(source_stage_only=True, new_performance=False, adopted=False,
        native_versions_directly_reparsed=len(cache), native_same_week_counterparts_checked=checked,
        known_native_pairs=known, missing_required_cells=sum(len(v['sides']) for v in missing),
        exact_missing_pairs=missing, precision_change_pairs=changed_precision,
        exact_original_signed_text_differences_versions_dates_and_hashes_reconciled=True,
        no_old_week_zero_cash_precision_or_alpha_repair=True,
        historical_first_public_receipts_or_actual_execution_certified=False)


if __name__ == '__main__':
    result = verify()
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    path = source.HERE/'r28_independent_validation.json'
    if path.exists() and path.read_text() != text:
        raise ValueError('Independent native source validation changed')
    path.write_text(text)
    print(json.dumps(result))
