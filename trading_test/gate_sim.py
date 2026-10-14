"""Monthly strategy gate — does re-picking the strategy each month beat just running the default?

Offline: rebuilds each run's OWN daily equity curve from its `report_period_*.md`
(verified — every total reproduces the report exactly), turns it into per-month returns,
then lets a selector choose a strategy for each month using only the trailing signal known
at the start of that month (no look-ahead).

Result (2024-01..2026-09): the monthly gate beats the annual gate decisively (-18.4% ->
+9.9%), but NO causal selector beats the static default (+19.1%). The ex-post monthly
oracle is +573.6%. See docs/STRATEGIES.md, "A faster signal: re-picking monthly".

Caveat: this composes real per-month returns from each year's own run — no cross-strategy
capital continuity, no switch costs. A true switching portfolio needs a month-by-month
runner that re-invokes the engine.

Regime labels are optional (the regime gate is skipped if absent). Regenerate with:
  grep -oE "Market Regime for [0-9]+: [a-z]+" /tmp/sweep_<src>_<YYYY>0101.log \
    | sed -E 's/Market Regime for ([0-9]+): ([a-z]+)/\1 \2/' > /tmp/regime_<src>_<YYYY>0101.txt

Usage:  .venv/bin/python trading_test/gate_sim.py
"""
import re, glob, os, json, sqlite3, zlib, pickle
import pandas as pd

REP = 'backtest/results_backups'
REP2 = 'backtest/_results_backups_archive'  # runs moved here 2026-10-14 (see results_backups/README.txt)
ME = 'ME'


def parse_equity(path):
    """Daily equity (date -> value) from a period report's daily table."""
    rows = {}
    for line in open(path, encoding='utf-8'):
        m = re.match(r'^\|\s*(\d{8})\s*\|.*?¥\s*([\d,]+\.\d\d)\s*\(\s*(-?[\d\.]+)%\)\s*\|', line)
        if m:
            rows[m.group(1)] = float(m.group(2).replace(',', ''))
    if not rows:
        return None
    s = pd.Series(rows)
    s.index = pd.to_datetime(s.index, format='%Y%m%d')
    return s.sort_index()


runs = {}
def add(strat, path):
    yr = re.search(r'report_period_(\d{8})_', os.path.basename(path)).group(1)[:4]
    s = parse_equity(path)
    if s is not None:
        runs.setdefault(strat, {})[yr] = s

for d in sorted(glob.glob(f'{REP}/*_sweep*') + glob.glob(f'{REP2}/*_sweep*')):
    # dirs may carry a trailing _<return> (the keeper naming convention)
    mm = re.match(r'(\d{8})_(\d{8})_(.+?)_sweep', os.path.basename(d))
    if mm:
        for p in glob.glob(f'{d}/report_period_*.md'):
            add(mm.group(3), p)
for tag in ['nostop_2023', 'nostop_2024OOS', 'nostop_LHBfull', 'nostop_2025clean',
            'nostop_dedupcache']:
    for d in sorted(glob.glob(f'{REP}/*_{tag}*') + glob.glob(f'{REP2}/*_{tag}*')):
        for p in glob.glob(f'{d}/report_period_*.md'):
            add('default', p)

mon = {}
for strat, yrs in runs.items():
    ser = []
    for yr in sorted(yrs):
        first = yrs[yr].resample(ME).last().dropna()
        r0 = first.iloc[0] / yrs[yr].iloc[0] - 1
        ser.append(pd.concat([pd.Series([r0], index=[first.index[0]]), first.pct_change().dropna()]))
    mon[strat] = pd.concat(ser).sort_index()
json.dump({s: {str(k.date()): v for k, v in ser.items()} for s, ser in mon.items()},
          open('/tmp/monthly_returns.json', 'w'), indent=0)

c = sqlite3.connect('shared/db/db_cache.db')
vals = {}
for d, v in c.execute("select trade_date,data from daily_data where symbol='000001.SH' and data_type='index_data' order by trade_date"):
    try:
        vals[str(d)[:10]] = float(pickle.loads(zlib.decompress(v[3:])).iloc[-1]['close'])
    except Exception:
        pass
sse = pd.Series(vals)
sse.index = pd.to_datetime(sse.index, format='%Y%m%d')
sse = sse.sort_index().resample(ME).last().dropna()
sig = pd.DataFrame({'close': sse})
sig['above6'] = sse > sse.rolling(6).mean()
sig['t3'] = sse.pct_change(3)

reg = {}
for f in glob.glob('/tmp/regime_sweep_*.txt'):
    for line in open(f):
        p = line.split()
        if len(p) == 2:
            reg[p[0]] = p[1]
reg_dates = sorted(reg)

LM, HM = pd.Timestamp('2023-01-01'), pd.Timestamp('2026-09-30')
allm = sorted({m for s in mon.values() for m in s.index if LM <= m <= HM})


def ret(strat, month):
    s = mon.get(strat)
    return float(s.loc[month]) if (s is not None and month in s.index) else None


def prev_val(x, month):
    idx = x.index[x.index < month]
    return x.loc[idx[-1]] if len(idx) else None


def regime_at(month):
    keys = [k for k in reg_dates if pd.Timestamp(k) < month]
    return reg[keys[-1]] if keys else None


def go(picks):
    tot = 1.0
    for t, s in picks:
        r = ret(s, t)
        if r is None:
            return None
        tot *= (1 + r)
    return tot - 1


SIGMA = {
    'always default':          lambda t: 'default',
    'always ts_hma':           lambda t: 'ts_hma',
    'MA6 gate up->ts_hma':     lambda t: 'ts_hma' if prev_val(sig['above6'], t) else 'default',
    'MA6 gate INVERTED':       lambda t: 'default' if prev_val(sig['above6'], t) else 'ts_hma',
    'MA6 gate up->ao_er':      lambda t: 'ts_ao_er' if prev_val(sig['above6'], t) else 'default',
    '3m gate up->ts_hma':      lambda t: 'ts_hma' if (prev_val(sig['t3'], t) or 0) > 0 else 'default',
}
if reg:
    SIGMA.update({
        'REGIME gate bear->default': lambda t: 'default' if regime_at(t) in ('bear', 'volatile') else 'ts_hma',
        'REGIME gate INVERTED':      lambda t: 'ts_hma' if regime_at(t) in ('bear', 'volatile') else 'default',
        'REGIME+MA6 both':           lambda t: 'default' if (regime_at(t) in ('bear', 'volatile') or not prev_val(sig['above6'], t)) else 'ts_hma',
    })
else:
    print("(no /tmp/regime_sweep_*.txt found — regime variants skipped)\n")

print(f"window {allm[0].date()} .. {allm[-1].date()} ({len(allm)} months)\n")
print(f"{'variant':30s} {'total':>9s}    2024     2025     2026")
for name, fn in SIGMA.items():
    picks = [(t, fn(t)) for t in allm]
    yrs = {y: go([(t, s) for t, s in picks if str(t.year) == y]) for y in ('2024', '2025', '2026')}
    print(f"{name:30s} {go(picks)*100:+8.2f}%   " +
          "  ".join(f"{yrs[y]*100:+7.1f}%" if yrs[y] is not None else "    n/a" for y in ('2024', '2025', '2026')))

# ex-post: annual oracle, and the monthly (look-ahead) ceiling
ann, comp = [], 1.0
for yr in ('2024', '2025', '2026'):
    best, br = None, -9
    for s, r in runs.items():
        sy = r.get(yr)
        if sy is None:
            continue
        tot = sy.iloc[-1] / sy.iloc[0] - 1
        if tot > br:
            br, best = tot, s
    ann.append((yr, best, br)); comp *= (1 + br)
print("\nex-post ANNUAL oracle:", ", ".join(f"{y}:{b} {v*100:+.1f}%" for y, b, v in ann),
      f"=> compounded {comp*100-100:+.1f}%")

orb = []
for t in allm:
    best, br = None, -9
    for s in mon:
        r = ret(s, t)
        if r is not None and r > br:
            br, best = r, s
    if best:
        orb.append((t, best))
print(f"ex-post MONTHLY oracle: {go(orb)*100:+.1f}%   (look-ahead ceiling)")
