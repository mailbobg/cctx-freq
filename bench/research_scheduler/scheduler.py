"""Shared, file-lock based research admission gate (host cron and panel container).

Locks are held throughout each worker lifetime; process exit releases capacity.
Control and leases live in the shared user_data mount, not a PID namespace.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

LOOPS = ('aq_loop', 'strat_loop', 't_loop')
DEFAULT = {'enabled': True, 'max_concurrent': 2}

class Gate:
    def __init__(self, user_data):
        self.ud = Path(user_data)
        self.base = self.ud / 'bench/research_scheduler'
        self.base.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def guard(self):
        with (self.base / 'control.lock').open('a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def control(self):
        p = self.base / 'control.json'
        if not p.exists():
            return dict(DEFAULT)
        d = json.loads(p.read_text())
        if type(d.get('enabled')) is not bool or type(d.get('max_concurrent')) is not int or not 1 <= d['max_concurrent'] <= 8:
            raise ValueError('Invalid scheduler control')
        return d

    def configure(self, enabled=None, max_concurrent=None):
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        if max_concurrent is not None and (type(max_concurrent) is not int or not 1 <= max_concurrent <= 8):
            raise ValueError('max_concurrent must be an integer between 1 and 8')
        with self.guard():
            d = self.control()
            if enabled is not None: d['enabled'] = enabled
            if max_concurrent is not None: d['max_concurrent'] = max_concurrent
            self.write(self.base / 'control.json', d)
            return d

    @staticmethod
    def write(path, data):
        tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(data, indent=2) + '\n')
        os.replace(tmp, path)

    def active(self):
        rows = []
        for p in sorted(self.base.glob('worker-*.lock')):
            with p.open('a+') as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    try: row = json.loads((self.base / (p.stem + '.json')).read_text())
                    except (OSError, ValueError): row = {'name': p.stem.removeprefix('worker-')}
                    rows.append(row)
        return rows

    def allowed(self, name):
        if name not in LOOPS: raise ValueError('Unknown research loop')
        return self.control()['enabled'] and (self.ud / 'bench' / name / 'RUN').exists()

    def status(self):
        with self.guard():
            return {**self.control(), 'active': self.active(), 'interval_seconds': 300,
                    'pause_policy': 'finish_current_window', 'registered': list(LOOPS)}

    def claim(self, name):
        if name not in LOOPS: raise ValueError('Unknown research loop')
        with self.guard():
            if not self.allowed(name): return None
            active = self.active()
            if len(active) >= self.control()['max_concurrent']: return None
            f = (self.base / f'worker-{name}.lock').open('a+')
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.close(); return None
            self.write(self.base / f'worker-{name}.json', {'name': name, 'started_at': time.time(), 'pid': os.getpid()})
            return f

    def run(self, name, command=None):
        lease = self.claim(name)
        if lease is None:
            print(f'gate: {name} deferred (paused, already running, or capacity full)', flush=True)
            return 0
        env = {**os.environ, 'RESEARCH_GATE_LEASE': name, 'RESEARCH_USER_DATA': str(self.ud.resolve())}
        try:
            self.report(name, 'starting')
            print(f'gate: admitted {name}', flush=True)
            # Lease inherited by the child: a killed wrapper cannot prematurely free a live worker slot.
            return subprocess.call(command or ['bash', str(self.ud / 'bench' / name / 'loop.sh')],
                                   env=env, pass_fds=(lease.fileno(),))
        finally: lease.close()

    def report(self, name, phase, item=None, strategy=None, window=None):
        if name not in LOOPS: raise ValueError('Unknown research loop')
        worker = json.loads((self.base / f'worker-{name}.json').read_text())
        self.write(self.ud / 'bench' / name / 'progress.json',
                   {'phase': phase, 'item': item, 'strategy': strategy, 'window': window,
                    'phase_started_at': time.time(), 'worker_started_at': worker['started_at']})

    def tick(self):
        # Prevent concurrent cron ticks; workers also claim under the atomic admission lock.
        with (self.base / 'dispatch.lock').open('a+') as dispatch:
            try: fcntl.flock(dispatch, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            if not self.control()['enabled']: return
            order = []
            for name in LOOPS:
                try: last = json.loads((self.base / f'worker-{name}.json').read_text()).get('started_at', 0)
                except (OSError, ValueError): last = 0
                order.append((last, name))
            for last, name in sorted(order):
                if not self.allowed(name) or time.time() - last < 300: continue
                if any(r['name'] == name for r in self.active()): continue
                logfile = self.ud / 'bench' / name / 'cron.log'
                with logfile.open('ab') as log:
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--user-data', str(self.ud), 'run', name],
                                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--user-data', default=str(Path(__file__).resolve().parents[2]))
    p.add_argument('action', choices=['tick', 'run', 'checkpoint', 'status', 'adopt'])
    p.add_argument('name', nargs='?', choices=LOOPS)
    p.add_argument('--pid', type=int)
    a = p.parse_args(); g = Gate(a.user_data)
    if a.action == 'adopt':
        if not a.name or not a.pid: p.error('name and pid required')
        cmdline = Path(f'/proc/{a.pid}/cmdline')
        try: command = cmdline.read_bytes()
        except OSError: return 0
        if f'/{a.name}/loop.sh'.encode() not in command: return 0
        lease = g.claim(a.name)
        if lease is None: return 1
        try:
            while cmdline.exists():
                try:
                    if cmdline.read_bytes() != command: break
                except OSError: break
                time.sleep(1)
        finally: lease.close()
    elif a.action == 'tick': g.tick()
    elif a.action == 'status': print(json.dumps(g.status()))
    elif a.action == 'checkpoint':
        if not a.name: p.error('name required')
        return 0 if g.allowed(a.name) else 1
    elif a.action == 'run':
        if not a.name: p.error('name required')
        return g.run(a.name)
    return 0

if __name__ == '__main__': raise SystemExit(main())
