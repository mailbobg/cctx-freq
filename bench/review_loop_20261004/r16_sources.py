"""Retain normal public project-document responses for a fixed source-only audit.

No tokens, paid unlock API, traded outcome selection or execution of fetched code.
Captured content establishes a local vintage from retrieval onward only.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests


HERE = Path(__file__).resolve().parent
DATA = HERE/'r16_sources'
INITIAL_URLS = (
    'https://aptosnetwork.com/currents/aptos-tokenomics-overview',
    'https://docs.celestia.org/learn/TIA/staking-governance-supply/',
    'https://raw.githubusercontent.com/celestiaorg/docs/89e75ea2b6c82c08822cf59a02467e583692e251/learn/staking-governance-supply.md',
    'https://api.github.com/repos/celestiaorg/docs/commits/89e75ea2b6c82c08822cf59a02467e583692e251',
    'https://api.github.com/repos/celestiaorg/docs/commits/89e75ea2b6c82c08822cf59a02467e583692e251/pulls',
    'https://www.sui.io/token-schedule',
    'https://www.sui.io/blog/token-release-schedule',
    'https://sui-circulation.suiexplorer.com/api/sui_circulation',
    'https://raw.githubusercontent.com/ArbitrumFoundation/docs/main/docs/airdrop-eligibility-distribution.md',
    'https://github.com/DefiLlama/emissions-adapters',
)


def replay(record):
    body = (DATA/record['path']).read_bytes()
    if hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('Captured project source bytes changed')
    return body


def fetch(urls):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    manifest_path = DATA/'manifest.json'
    records = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for url in urls:
        if url in records:
            replay(records[url])
            continue
        attempted_at = datetime.now(timezone.utc).isoformat()
        try:
            response = requests.get(url, timeout=(5, 20), headers={'User-Agent': 'CCXT-Fr-source-research/1.0'})
            body = response.content
            details = dict(status=response.status_code, final_url=response.url,
                           headers={key:response.headers.get(key) for key in ('Content-Type', 'Date', 'ETag', 'Last-Modified')})
        except requests.RequestException as exc:
            body = b''
            details = dict(status=None, final_url=None, error=f'{type(exc).__name__}: {exc}')
        # Unique source URL bytes and exact body; preserve errors without a schedule.
        path = DATA/'raw'/hashlib.sha256(url.encode()).hexdigest()
        path.write_bytes(body)
        records[url] = dict(path=str(path.relative_to(DATA)), sha256=hashlib.sha256(body).hexdigest(),
                            bytes=len(body), attempted_at_utc=attempted_at,
                            retrieved_utc=datetime.now(timezone.utc).isoformat(), **details)
        manifest_path.write_text(json.dumps(records, indent=2)+'\n')
        print(json.dumps({'url':url, 'status':details['status'], 'bytes':len(body)}), flush=True)
    return records


def main(extra_urls=()):
    records = fetch((*INITIAL_URLS, *extra_urls))
    for record in records.values():
        replay(record)
    print(f'Replayed {len(records)} retained HTTP attempts; no P&L or historical publication assigned.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', action='append', default=[])
    main(parser.parse_args().url)
