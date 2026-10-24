"""Write a CONFIG_FILE variant with a realistic take-profit.

The shipped `config.json` has `take_profit_pct: 2.0` in every regime, which the
engine reads as a FRACTION — the nominal TP lands at x3 the entry price (a ¥5.82
entry gets a ¥17.46 TP) and never fires, so trades only exit on max_hold / the
early-cut rules. A 0.04 value gives an actual +4% bracket.

This copies the live config and changes only the TP (plus optional SL / trailing),
so nothing drifts: every other key is whatever `backtest/config.json` holds today.

Usage:
    python trading_test/make_tp_config.py --tp 0.04 --out /tmp/tp4_config.json
    python trading_test/make_tp_config.py --tp 0.04 --sl 0.03 --keep-trailing
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(REPO, "backtest", "config.json")


def build(src: str, tp: float, sl: float | None, keep_trailing: bool) -> dict:
    with open(src) as f:
        cfg = json.load(f)
    ratios = cfg["trading_rules"]["risk_reward_ratios"]
    out = copy.deepcopy(cfg)
    for regime, block in out["trading_rules"]["risk_reward_ratios"].items():
        base = ratios[regime]
        block["take_profit_pct"] = tp
        if sl is not None:
            block["stop_loss_pct"] = sl
        else:
            block["stop_loss_pct"] = base["stop_loss_pct"]
        if not keep_trailing:
            block["trailing_stop_enabled"] = False
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tp", type=float, default=0.04, help="take-profit as a fraction (0.04 = +4%%)")
    ap.add_argument("--sl", type=float, default=None, help="override stop_loss_pct for every regime")
    ap.add_argument("--keep-trailing", action="store_true", help="leave trailing_stop_enabled as configured")
    ap.add_argument("--src", default=DEFAULT_SRC, help="source config.json")
    ap.add_argument("--out", default="/tmp/tp4_config.json", help="where to write the variant")
    args = ap.parse_args(argv)

    cfg = build(args.src, args.tp, args.sl, args.keep_trailing)
    with open(args.out, "w") as f:
        json.dump(cfg, f, indent=2)
    regimes = cfg["trading_rules"]["risk_reward_ratios"]
    summary = {r: (b["take_profit_pct"], b["stop_loss_pct"], b["trailing_stop_enabled"])
               for r, b in regimes.items()}
    print(f"wrote {args.out} from {args.src}")
    print(f"  (take_profit_pct, stop_loss_pct, trailing_stop_enabled) = {summary}")
    print(f"  use with: CONFIG_FILE={args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
