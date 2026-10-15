"""Refuse to start a backtest while another engine is running.

Every engine wipes and shares `shared/db/test_imobile.db`, so two concurrent
engines corrupt each other's data (and a "clean" run wipes the other's DB).

A real engine is a PYTHON process whose argv contains an argument that ENDS WITH
`engine.py`. Two earlier versions of this guard matched themselves:

  1. a `pgrep -af "backtest/engine.py"` matched the shell wrapper running the
     command (the pattern is part of that wrapper's own command line), and
  2. a `python -c "...'backtest/engine.py' in c..."` matched its OWN code blob,
     because the path appeared as a substring of the -c argument.

Matching an exact argv element (endswith, not substring) and skipping our own
pid/ppid fixes both. Keep this file in the repo, not the scratch dir: scratch
entries idle for 24h are pruned, and a pruned guard exits non-zero and silently
aborts the launch chain before the engine starts.

Usage:  .venv/bin/python trading_test/engine_preflight.py && <launch>
Exit:   0 = safe to launch, 9 = an engine is running, 2 = usage/environment error.
"""
import os
import glob
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def running_engines() -> list[tuple[int, str]]:
    me = {os.getpid(), os.getppid()}
    found = []
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            pid = int(path.split('/')[2])
        except (IndexError, ValueError):
            continue
        if pid in me:
            continue
        try:
            with open(path, 'rb') as fh:
                cmd = [x.decode('utf-8', 'replace') for x in fh.read().split(b'\0') if x]
        except OSError:
            continue
        if not cmd or 'python' not in os.path.basename(cmd[0]):
            continue
        if any(arg.endswith('engine.py') for arg in cmd):
            found.append((pid, ' '.join(cmd[:4])))
    return found


def main() -> int:
    if not os.path.isdir(os.path.join(REPO, 'backtest')):
        print(f'preflight: no repo at {REPO}', file=sys.stderr)
        return 2
    found = running_engines()
    if found:
        print('!! ENGINE ALREADY RUNNING — refusing to launch (they share the test DB):')
        for pid, line in found:
            print(f'   {pid}: {line}')
        return 9
    print('preflight: no engine running — safe to launch')
    return 0


if __name__ == '__main__':
    sys.exit(main())
