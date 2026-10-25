"""Attribute an A/B delta between a base strategy and a merge wrapper, session by session.

A merge wrapper can be *inert* without failing: if the names it adds never reach the
order file (cash runs out, the engine truncates, the picker returned nothing), the
return delta is noise and the merge should not be credited or blamed for it. This
script answers "did the merge actually change what got BOUGHT, and on how many days",
which is the first thing to check before reading any return difference.

Usage:
    python trading_test/merge_attribution.py CONTROL_DIR MERGED_DIR

For every trading date present in both dirs it reports:
  n_ctrl / n_merged   picks in each arm's pick_stocks_<date>.json
  added               symbols in merged but not control (the wrapper's extras)
  dropped             control symbols that the merged arm no longer picks
  funded_added        added symbols that actually appear in merged smart_orders_<date>.json
  funded_dropped      dropped symbols that the CONTROL arm had bought
A session where funded_added == funded_dropped == 0 is untouched by the wrapper.
"""
from __future__ import annotations

import glob
import json
import os
import sys


def _load(path, key):
    try:
        with open(path) as fh:
            return json.load(fh).get(key) or []
    except (OSError, json.JSONDecodeError):
        return []


def picks(d, date):
    return [s.get("symbol") for s in _load(os.path.join(d, f"pick_stocks_{date}.json"), "selected_stocks")]


def buys(d, date):
    return [o.get("symbol") for o in _load(os.path.join(d, f"smart_orders_{date}.json"), "smart_orders")]


def dates(d):
    return sorted(os.path.basename(p)[12:20] for p in glob.glob(os.path.join(d, "pick_stocks_*.json")))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ctrl, merged = argv[0], argv[1]
    common = sorted(set(dates(ctrl)) & set(dates(merged)))
    if not common:
        print("no overlapping sessions", file=sys.stderr)
        return 1

    touched = 0
    funded_days = 0
    added_total = dropped_total = funded_added = funded_dropped = 0
    rows = []
    for date in common:
        cp, mp = picks(ctrl, date), picks(merged, date)
        cb, mb = buys(ctrl, date), buys(merged, date)
        added = [s for s in mp if s not in cp]
        dropped = [s for s in cp if s not in mp]
        fa = [s for s in added if s in mb]
        fd = [s for s in dropped if s in cb]
        added_total += len(added)
        dropped_total += len(dropped)
        funded_added += len(fa)
        funded_dropped += len(fd)
        if added or dropped:
            touched += 1
        if fa or fd:
            funded_days += 1
            rows.append((date, len(cp), len(mp), added, dropped, fa, fd))

    print(f"sessions compared: {len(common)}")
    print(f"  pick set changed by the merge : {touched} sessions")
    print(f"  FUNDED set changed by the merge: {funded_days} sessions  <-- the only ones that can move the return")
    print(f"  extras emitted {added_total} / dropped {dropped_total} picks; "
          f"funded extras {funded_added} / funded drops {funded_dropped}")
    if rows:
        print("\nsessions where the bought set actually changed:")
        for date, nc, nm, added, dropped, fa, fd in rows:
            print(f"  {date} picks {nc:>2}->{nm:<2} +{','.join(fa) or '-':<24} -{','.join(fd) or '-':<24}"
                  f" (emitted +{len(added)}/-{len(dropped)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
