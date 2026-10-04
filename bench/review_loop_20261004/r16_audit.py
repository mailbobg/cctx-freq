"""Offline source-field/clock audit of fixed APT/TIA/ARB/SUI panel, without P&L."""
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pandas as pd

from r16_knowledge import select_known_tokens
from r16_sources import DATA, replay


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INCLUDED = ('insiders', 'privateSale', 'publicSale', 'airdrop')
PANEL = ('APT', 'TIA', 'ARB', 'SUI')
OLD_TIA = 'https://raw.githubusercontent.com/celestiaorg/docs/89e75ea2b6c82c08822cf59a02467e583692e251/learn/staking-governance-supply.md'
INTRO_TIA = 'https://raw.githubusercontent.com/celestiaorg/docs/da016459d28a016c07e8d573e9c196d4d9390e07/learn/staking-governance-supply.md'
CURRENT_TIA = 'https://docs.celestia.org/learn/TIA/staking-governance-supply/'
SUI_API = 'https://sui-circulation.suiexplorer.com/api/sui_circulation'
HISTORY_ARB = 'https://api.github.com/repos/ArbitrumFoundation/docs/commits?path=docs%2Fairdrop-eligibility-distribution.md&per_page=100'


class Tables(HTMLParser):
    """Read inert HTML table text. Scripts are neither run nor interpreted."""
    def __init__(self):
        super().__init__(); self.tables = []; self.current = None; self.row = None; self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.current = []
        elif tag == 'tr' and self.current is not None: self.row = []
        elif tag in ('td', 'th') and self.row is not None: self.cell = []

    def handle_data(self, data):
        if self.cell is not None and data.strip(): self.cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(self.cell)); self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.current.append(self.row); self.row = None
        elif tag == 'table' and self.current is not None:
            self.tables.append(self.current); self.current = None


def validate_frozen():
    counts = {}
    for name in ('r16_input_manifest.json', 'r16_cohort_manifest.json'):
        manifest = json.loads((HERE/name).read_text())
        for rel, record in manifest['files'].items():
            if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() != record['sha256']:
                raise ValueError(f'Frozen legacy input changed: {rel}')
        counts[name] = len(manifest['files'])
    return counts


def main():
    frozen = validate_frozen()
    records = json.loads((DATA/'manifest.json').read_text())
    bodies = {url:replay(rec) for url, rec in records.items()}

    def successful(url):
        if records[url]['status'] != 200:
            raise ValueError('HTTP error is not a project schedule')
        return bodies[url].decode('utf-8')

    def json_source(url):
        return json.loads(successful(url), parse_float=Decimal)

    events = pd.read_parquet(ROOT/'bench/events/unlocks.parquet')
    mapping = pd.read_parquet(ROOT/'bench/events/unlock_symbol_map.parquet')
    schedule = pd.read_csv(ROOT/'strategies/unlock_schedule.csv', dtype=str)
    panel = {}
    for asset in PANEL:
        protocols = mapping.loc[mapping.perp.eq(asset+'USDT'), 'protocol'].tolist()
        if len(protocols) != 1:
            raise ValueError('Frozen panel mapping is not one-to-one')
        subset = events.loc[events.protocol.isin(protocols)]
        included = subset.loc[subset.category.isin(INCLUDED)]
        pair = asset+'/USDT:USDT'
        if len(schedule.loc[schedule.pair.eq(pair)]) != len(included):
            raise ValueError('Exported panel row count differs from legacy input')
        panel[asset] = dict(protocol=protocols[0], all_rows=len(subset), included_rows=len(included),
                            all_category_counts={str(k):int(v) for k,v in subset.groupby('category').size().items()},
                            included_categories={})
        for category, group in included.groupby('category'):
            group = group.sort_values('ts')
            panel[asset]['included_categories'][category] = dict(
                rows=len(group), unique_timestamps=group.ts.nunique(),
                first_event_utc=group.ts.iloc[0].isoformat(), last_event_utc=group.ts.iloc[-1].isoformat(),
                # Only a diagnostic of already-rounded legacy float quantities.
                legacy_float_sum_tokens=float(group.tokens.sum()),
                legacy_first_float_amount=repr(float(group.tokens.iloc[0])),
                interval_seconds_counts={str(k):int(v) for k,v in group.ts.diff().dropna().dt.total_seconds().value_counts().items()})
        # This is a field-integrity test, not a changed selection or trade simulation.
        panel[asset]['legacy_historical_known_after_present'] = 'known_after' in events.columns
        panel[asset]['legacy_raw_body_source_identity_present'] = 'source_sha256' in events.columns
        unknown = select_known_tokens([], [], asset=asset, decision='2024-01-01T00:00:00Z',
                                      start='2024-01-01T00:00:00Z', end='2024-01-15T00:00:00Z', categories=INCLUDED)
        panel[asset]['unproven_coverage_example'] = dict(known=unknown.known, tokens=None, reason=unknown.reason)

    old_tia = successful(OLD_TIA)
    if successful(INTRO_TIA) != old_tia:
        raise ValueError('Older TIA documentation is not identical to the originally cited vintage')
    allocation_section = old_tia.split('## TIA allocation at genesis')[1].split('### Unlocks')[0]
    old_allocations = {}
    for line in allocation_section.splitlines():
        if not line.startswith('|'): continue
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if len(cells) == 3 and re.fullmatch(r'\d+(?:\.\d+)?%', cells[-1]):
            old_allocations[cells[0]] = cells[-1]
    core_rule = next(line for line in old_tia.split('### Unlocks')[1].splitlines()
                     if line.startswith('| Initial Core Contributors'))
    if not all(value in core_rule for value in ['33%', '67%', 'year 1 to year 3']):
        raise ValueError('Old core-vesting table definition changed')
    total = Decimal('1000000000')
    core_pool = total * Decimal(old_allocations['Initial Core Contributors'].strip('%'))/100
    core_remainder = core_pool * Decimal('0.67')
    current_parser = Tables(); current_parser.feed(successful(CURRENT_TIA))
    current_allocations, current_unlock_rules = {}, {}
    for table in current_parser.tables:
        if not table: continue
        for row in table[1:]:
            if len(row) == 3 and '%' in row[-1]: current_allocations[row[0]] = row[-1]
            if len(row) == 2 and 'Unlock Schedule' in table[0]: current_unlock_rules[row[0]] = row[-1]
    if 'Initial Core Contributors' not in current_allocations or 'Initial Core Contributors' not in current_unlock_rules:
        raise ValueError('Current TIA allocation/vesting table unavailable')

    sui = json_source(SUI_API)
    if not isinstance(sui, list) or not sui:
        raise ValueError('SUI aggregate circulation schema unavailable')
    months = [pd.Timestamp(year=int(r['year']), month=int(r['month']), day=1, tz='UTC') for r in sui]
    if pd.DatetimeIndex(months).tolist() != pd.date_range(months[0], months[-1], freq='MS').tolist():
        raise ValueError('SUI month labels have gaps or duplicates')
    if any(not isinstance(r['circulatingSupplyValue'], (Decimal, int)) or r['circulatingSupplyValue'] < 0 for r in sui):
        raise ValueError('SUI native decimal circulation values invalid')
    publication_fields = set().union(*(r.keys() for r in sui)) & {'published_at', 'known_after', 'source_sha256', 'category', 'event_at'}

    clocks = []
    for repo, number in [('celestiaorg/docs', 1169), ('celestiaorg/docs', 1227),
                          ('ArbitrumFoundation/docs', 410), ('ArbitrumFoundation/docs', 776)]:
        url = f'https://api.github.com/repos/{repo}/pulls/{number}'
        data = json_source(url)
        clocks.append(dict(url=url, created_at=data['created_at'], merged_at=data['merged_at'],
                           merge_commit_sha=data['merge_commit_sha'], changed_files=data['changed_files'],
                           evidence_type='github_server_event', automatic_historical_admission=False))
    files = []
    for page in (1, 2, 3):
        files.extend(json_source(f'https://api.github.com/repos/celestiaorg/docs/pulls/1169/files?per_page=100&page={page}'))
    filenames = [r['filename'] for r in files]
    if len(filenames) != 250 or len(set(filenames)) != 250 or 'learn/staking-governance-supply.md' not in filenames:
        raise ValueError('TIA documentation-introduction PR file inventory incomplete')

    # Exact identity for this source audit, not a statement that all users had it then.
    audit = dict(source_only=True, no_new_performance=True, no_strategy_or_bot_change=True,
                 frozen_input_files_verified=frozen, retained_http_attempts=len(records),
                 statuses={str(s):sum(r['status']==s for r in records.values()) for s in sorted(set(r['status'] for r in records.values()), key=str)},
                 all_retained_bodies_replayed=True, panel=panel,
                 tia=dict(old_content_sha256=records[OLD_TIA]['sha256'],
                          older_introduction_identical_content=True, introduction_pr_file_count=len(filenames),
                          old_rounded_allocation_percent=old_allocations, current_allocation_percent=current_allocations,
                          old_core_vesting_rule=core_rule.split('|')[2].strip(),
                          current_core_vesting_rule=current_unlock_rules['Initial Core Contributors'],
                          old_rounded_core_pool_tokens=str(core_pool),
                          old_rounded_continuous_core_remainder_tokens=str(core_remainder),
                          observed_legacy_core_rows=panel['TIA']['included_categories']['insiders']['rows'],
                          limitation='Remainder is a forecast derived from rounded published percentages, not exact chain-verified sale or release. Legacy collector keeps cliff metadata only; some backer streams are daily pseudo-cliffs, while core continuous vesting is absent.'),
                 sui=dict(months=len(sui), first_month=months[0].isoformat(), last_month=months[-1].isoformat(),
                          fields=sorted(set().union(*(r.keys() for r in sui))), explicit_event_provenance_fields=sorted(publication_fields),
                          api_source_captured_after=records[SUI_API]['retrieved_utc'],
                          limitation='Current end-month aggregate circulation estimate, explicitly adjustable; not holder-category event times or point-in-time revision history.'),
                 server_clocks=clocks,
                 limitations=['SHA identifies retained content, not publication or historical completeness.',
                              'GitHub server PR events corroborate historical content and clocks; historic public access and the complete broad schedule remain unproven. These are not automatically admitted as verified_publication.',
                              'Old float token amounts cannot be retroactively recovered as exact source decimals.',
                              'APT normal public HTTP request returned 429; this body is retained as an error, not schedule data.',
                              'The guessed public emissions-adapters repository returned 404; no gated/paid endpoint was called.',
                              'No new signal selection, P&L, parameter search, schedule export, or running-cohort modification.'])
    (HERE/'r16_source_audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    print(json.dumps({k:audit[k] for k in ['source_only', 'frozen_input_files_verified', 'retained_http_attempts', 'statuses', 'all_retained_bodies_replayed']}, indent=2))
    print('Fixed panel included rows:', {k:v['included_rows'] for k,v in panel.items()})
    print('TIA old rounded core continuous remainder absent from cliff-only rows:', core_remainder, 'tokens (forecast, not actual sales)')


if __name__ == '__main__':
    main()
