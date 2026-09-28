"""Reproduce the fixed candidate comparisons; no trading commands or bot config edits.

Usage: .venv/bin/python user_data/bench/review_20260927/run_comparison.py dip|x8 --fee 0.001
Historical windows are reused research data, not untouched confirmation samples.
"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('family', choices=['dip', 'x8'])
    parser.add_argument('--fee', type=float, default=.001)
    parser.add_argument('--timerange', default='20260101-20260923')
    args = parser.parse_args()
    if args.fee < 0:
        parser.error('fee must be nonnegative')
    strategies = {'dip': ['E0V1ENX13', 'E0V1ENBounded'],
                  'x8': ['NostalgiaForInfinityX8PF', 'NostalgiaForInfinityX8Budget']}[args.family]
    label = f'review_{args.family}_{args.timerange}_fee{args.fee}'
    output = ROOT / 'user_data/backtest_results' / label
    output.mkdir(exist_ok=True)
    command = [str(ROOT/'.venv/bin/freqtrade'), 'backtesting',
               '-c', str(Path(__file__).parent / (args.family+'.json')),
               '-c', str(ROOT/'user_data/config-net.json'), '--enable-protections',
               '--strategy-list', *strategies, '--timerange', args.timerange,
               '--timeframe-detail', '1m', '--fee', str(args.fee), '--cache', 'none',
               '--export', 'trades', '--backtest-directory', str(output)]
    with (Path(__file__).parent / (label+'.log')).open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run([str(ROOT/'.venv/bin/python'), str(Path(__file__).parent/'summarize_backtests.py'),
                    str(output)], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
