"""Replay original bounded NFCI sources; keep observations and public clocks apart."""
from collections import Counter
from html.parser import HTMLParser
import hashlib
import json
import subprocess

from r20_audit import retained
from r28_revision import parse_archive, same_week_revision
import r28_sources as source


class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(body):
    parser = HtmlText()
    parser.feed(body.decode('utf-8'))
    return ' '.join(' '.join(parser.parts).split())


def main():
    targets = source.probe_targets()
    records = json.loads((source.DATA/'manifest.json').read_text())['sources']
    if set(records) != set(targets):
        raise ValueError('Unreviewed bounded source scope')
    parsed, context, provenance, hashes = {}, {}, {}, {}
    for key, url in targets.items():
        record = records[key]
        if record['url'] != url or record['status'] != 200:
            raise ValueError('Required original source unavailable or altered')
        body = retained(source.DATA, record)
        hashes['r28_sources/'+record['path']] = record['sha256']
        provenance[key] = dict(url=url, body_sha256=record['sha256'],
            capture_requested_utc=record['requested_utc'], capture_received_utc=record['retrieved_utc'],
            http_headers=record['headers'], source_capture_is_not_past_publication=True)
        if key.startswith('archive_'):
            parsed[key] = parse_archive(body, key.removeprefix('archive_'))
        elif key == 'publisher_faq':
            if not body.startswith(b'%PDF-'):
                raise ValueError('Native publisher FAQ is not a PDF')
            context[key] = ' '.join(subprocess.check_output(
                ['pdftotext', str(source.DATA/record['path']), '-']).decode('utf-8').split())
        else:
            context[key] = html_text(body)
    required_markers = {
        'alfred_help': ('Observations by Vintage Date', 'not been revised'),
        'alfred_identity': ('(NFCI)', 'Weekly, Ending Friday', 'Index, Not Seasonally Adjusted'),
        'publisher_background': ('105 measures of financial activity', 'sample standard deviations', '1971'),
        'publisher_current': ('8:30 a.m. ET on Wednesday', 'previous Friday', 'updated on Thursday', 'data revisions'),
        'publisher_faq': ('coincident indexes', '8:30 a.m. ET on Wednesday', 'previous Friday', 'Thursday'),
        'publisher_calendar': ('National Financial Conditions Index', '8:30 a.m. ET/7:30 a.m. CT', '2026'),
        'publisher_revision_study': ('unexpected changes in financial conditions', 'estimated model parameters', '105 weekly, monthly, and quarterly'),
    }
    for key, markers in required_markers.items():
        if any(marker not in context[key] for marker in markers):
            raise ValueError('Retained primary context meaning differs '+key)
    pairs = []
    for pair in source.probe_pairs():
        current_key = 'archive_'+pair['current_vintage']
        prior_key = 'archive_'+pair['prior_vintage']
        fact = same_week_revision(pair, parsed[current_key], parsed[prior_key])
        fact.update(current_source_key=current_key, prior_source_key=prior_key,
            current_body_sha256=records[current_key]['sha256'], prior_body_sha256=records[prior_key]['sha256'],
            current_native_rows=len(parsed[current_key]['rows']), prior_native_rows=len(parsed[prior_key]['rows']),
            current_latest_observation=max(parsed[current_key]['rows']).isoformat(),
            prior_latest_observation=max(parsed[prior_key]['rows']).isoformat(),
            units='Standardized financial-condition index; no percent or crypto capital-flow unit',
            decision_availability_is_conservative_as_of_assumption=True)
        pairs.append(fact)
    hashes['r28_sources/manifest.json'] = hashlib.sha256((source.DATA/'manifest.json').read_bytes()).hexdigest()
    result = dict(source_stage_only=True, new_alpha_trial=False, new_performance=False, adopted=False,
        source_plan_commit='d1437ea', original_request_roles=len(targets), retained_attempts=len(records),
        status_counts=dict(Counter(str(r['status']) for r in records.values())),
        native_archive_header='observation_date,NFCI_<YYYYMMDD>',
        earlier_iso_header_assumption_and_failures_retained_in='r28_initial_schema_observation.json',
        known_same_week_revision_samples=sum(f['state']=='known_same_week_revision' for f in pairs), pairs=pairs,
        primary_context_native_body_and_semantic_markers_verified=True, source_provenance=provenance,
        historical_versions_supported_by_primary_archive_semantics=True,
        historical_first_public_receipts_certified=False,
        full_feature_cohort_not_requested_or_evaluated=True,
        permitted_next_stage='Separate critic and one preregistered economic rule before full cohort/outcomes, only if exact same-week sources remain known.',
        limits=['A historical as-of vintage is not an authenticated original publisher receipt at a precise second.',
            'Current HTTP Last-Modified values describe server metadata; they are not first-public timestamps.',
            'Week-ending Friday is an observation label, not the Wednesday/holiday-Thursday publication date.',
            'The current 2026 calendar does not authenticate every 2021-2024 release.',
            'Revisions reflect new inputs, revised inputs, model reestimation and native reporting precision.',
            'The source paper concerns equity volatility; there is no demonstrated crypto lead or alpha.',
            'These three forensic dates are not a selected trade cohort and supply no return outcomes.'],
        no_rule_sign_threshold_day_window_asset_fee_risk_or_missingness_search=True,
        no_strategy_bot_image_or_independent_forward_change=True, retained_inputs_sha256=hashes,
        code_sha256={name: hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() for name in
            ('R28_SOURCE_PLAN.md', 'r28_sources.py', 'r28_revision.py', 'r28_probe_audit.py',
             'test_r28_revision.py', 'r28_initial_schema_observation.json')})
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    path = source.HERE/'r28_probe_audit.json'
    if path.exists() and path.read_text() != text:
        raise ValueError('Frozen bounded source audit changed')
    path.write_text(text)
    print(json.dumps({k: result[k] for k in ('original_request_roles', 'retained_attempts',
          'status_counts', 'known_same_week_revision_samples', 'historical_first_public_receipts_certified')}))


if __name__ == '__main__':
    main()
