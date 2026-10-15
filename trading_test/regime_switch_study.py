"""Regime-conditional strategy switching — can a state rule pick the right strategy?

STATE (all lagged one trading day, so nothing is known before the decision):
  regime     the engine's own label (bull/normal/bear/volatile), from pick_stocks market_pattern
  bear_run   consecutive bear days ending at that point (0/1/2/3+)
  drop       SSE trailing 5-day return, bucketed (<= -4% = "sharp drop")

TARGET: each strategy's FORWARD 21-trading-day return, taken from that run's own daily
equity curve (report_period daily table) — i.e. what it would have earned if you had been
running it. Same proxy caveat as gate_sim.py: no cross-strategy capital continuity.

Outputs: overall per-strategy stats, the per-regime means (which strategy wins where), the
explicit "N bear days + a sharp drop -> ts_ao_er / bull -> default" rule, and a walk-forward
(fit the per-state pick on the earlier years, apply to the later ones).

Run: .venv/bin/python trading_test/regime_switch_study.py
"""
import re, glob, os, json, sqlite3, zlib, pickle
import pandas as pd

REP = 'backtest/results_backups'
REP2 = 'backtest/_results_backups_archive'
H = 21


def parse_equity(path):
    rows = {}
    for line in open(path, encoding='utf-8', errors='ignore'):
        m = re.match(r'^\|\s*(\d{8})\s*\|.*?¥\s*([\d,]+\.\d\d)\s*\(\s*(-?[\d\.]+)%\)\s*\|', line)
        if m:
            rows[m.group(1)] = float(m.group(2).replace(',', ''))
    if not rows:
        return None
    s = pd.Series(rows)
    s.index = pd.to_datetime(s.index, format='%Y%m%d')
    return s.sort_index()


# ---- runs: strategy -> {year: daily equity} ----
runs = {}
def add(strat, d):
    ps = glob.glob(f'{d}/report_period_*.md')
    if not ps:
        return
    yr = re.search(r'report_period_(\d{8})_', os.path.basename(ps[0])).group(1)[:4]
    eq = parse_equity(ps[0])
    if eq is not None:
        runs.setdefault(strat, {})[yr] = eq

for d in sorted(glob.glob(f'{REP}/*_sweep*') + glob.glob(f'{REP2}/*_sweep*')):
    mm = re.match(r'(\d{8})_(\d{8})_(.+?)_sweep', os.path.basename(d))
    if mm:
        add(mm.group(3), d)
for tag in ['nostop_2023', 'nostop_2024OOS', 'nostop_LHBfull', 'nostop_2025clean', 'nostop_dedupcache']:
    for d in sorted(glob.glob(f'{REP}/*_{tag}*') + glob.glob(f'{REP2}/*_{tag}*')):
        add('default', d)

print("runs loaded:", {s: sorted(y) for s, y in runs.items()})

# ---- the regime label per trading day (market-wide; any run's pick files) ----
regime = {}
for d in sorted(glob.glob(f'{REP}/*/') + glob.glob(f'{REP2}/*/')):
    for f in glob.glob(f'{d}pick_stocks_*.json'):
        try:
            j = json.load(open(f))
            k = str(j.get('target_trading_date') or j.get('pick_date'))
            if j.get('market_pattern') and k and k != 'None':
                regime[k] = j['market_pattern']
        except Exception:
            pass
dates = sorted(regime)
st = pd.DataFrame(index=pd.to_datetime(dates, format='%Y%m%d'))
st['regime'] = [regime[d] for d in dates]
run_len, n = [], 0
for r in st['regime']:
    n = n + 1 if r == 'bear' else 0
    run_len.append(n)
st['bear_run'] = run_len
print(f"regime labels: {len(st)} days {st.index[0].date()}..{st.index[-1].date()}  {st['regime'].value_counts().to_dict()}")

# ---- SSE trailing 5-day drop ----
c = sqlite3.connect('shared/db/db_cache.db')
vals = {}
for dt, v in c.execute("select trade_date,data from daily_data where symbol='000001.SH' and data_type='index_data'"):
    try:
        vals[str(dt)[:10]] = float(pickle.loads(zlib.decompress(v[3:])).iloc[-1]['close'])
    except Exception:
        pass
sse = pd.Series(vals)
sse.index = pd.to_datetime(sse.index, format='%Y%m%d')
sse5 = sse.sort_index().pct_change(5) * 100

# ---- lag the state by one day (no look-ahead) ----
lag = st.copy()
lag['regime'] = st['regime'].shift(1)
lag['bear_run'] = st['bear_run'].shift(1)
lag['drop5'] = sse5.reindex(st.index).shift(1)

# ---- observations: (date, state, strategy, forward 21d) ----
rows = []
for strat, yrs in runs.items():
    for yr, eq in yrs.items():
        idx = eq.index
        for i, d in enumerate(idx):
            if i + H >= len(idx) or d not in lag.index:
                continue
            s = lag.loc[d]
            if pd.isna(s['regime']):
                continue
            rows.append(dict(date=d, strat=strat, fwd=eq.iloc[i + H] / eq.iloc[i] - 1,
                             regime=s['regime'], bear_run=0 if pd.isna(s['bear_run']) else int(s['bear_run']),
                             drop5=-99 if pd.isna(s['drop5']) else float(s['drop5'])))
df = pd.DataFrame(rows)
print(f"\nobservations: {len(df)}  ({df['date'].min().date()}..{df['date'].max().date()})")


def table(d, by):
    g = d.groupby([by, 'strat'])['fwd'].agg(['mean', 'count'])
    out = g['mean'].unstack(0) * 100
    cnt = g['count'].unstack(0)
    best = g['mean'].unstack(0).max(axis=1)
    print(f"\n=== forward {H}d mean return (%) by {by} ===")
    print(out.round(2).to_string())
    print("best per bucket:")
    for b in out.columns:
        r = out[b].dropna()
        if len(r):
            print(f"   {str(b):>8s}: best = {r.idxmax():26s} {r.max():+6.2f}%   (runner-up "
                  f"{r.sort_values().index[-2] if len(r)>1 else '-':26s} "
                  f"{r.sort_values().iloc[-2] if len(r)>1 else 0.0:+6.2f}%)  n={int(cnt[b].dropna().iloc[0])}")


table(df, 'regime')
df['bear_bucket'] = df['bear_run'].clip(upper=3)
table(df, 'bear_bucket')

# ---- the explicit requested rule ----
print("\n=== the requested rule shape ===")
def state(d):
    if d['regime'] == 'bull':
        return 'BULL'
    if d['regime'] == 'bear' and d['bear_run'] >= 3 and d['drop5'] <= -4:
        return 'BEAR>=3 + sharp drop'
    if d['regime'] == 'bear':
        return 'BEAR (other)'
    return 'NORMAL/other'

df['state'] = df.apply(state, axis=1)
for s, sub in df.groupby('state'):
    m = sub.groupby('strat')['fwd'].agg(['mean', 'count'])
    m['mean'] *= 100
    print(f"\n  {s}  (n={len(sub)})")
    print(m.sort_values('mean', ascending=False).round(2).head(6).to_string())

# ---- rule BACKTEST: compound the picked strategy's daily returns ----
daily = {}
for strat, yrs in runs.items():
    for yr, eq in yrs.items():
        daily.setdefault(strat, {})[yr] = eq.pct_change().dropna()


def run_rule(rule_fn, years, label):
    tot, picks, per = 1.0, {}, {}
    for y in years:
        ys = str(y)
        yr_tot = 1.0
        base = runs['default'][ys].index
        for d in base:
            if d not in lag.index:
                continue
            s = lag.loc[d]
            if pd.isna(s['regime']):
                continue
            pick = rule_fn(s)
            has = pick in daily and ys in daily[pick] and d in daily[pick][ys].index
            if not has:
                pick = 'default'
                if ys not in daily.get('default', {}) or d not in daily['default'][ys].index:
                    continue
            r = daily[pick][ys].loc[d]
            tot *= (1 + r)
            yr_tot *= (1 + r)
            picks[pick] = picks.get(pick, 0) + 1
        per[y] = (yr_tot - 1) * 100
    print(f"  {label:44s} {years[0]}-{years[-1]}: {(tot-1)*100:+8.2f}%   "
          + " ".join(f"{y}:{v:+6.1f}%" for y, v in per.items()) + f"   days={sum(picks.values())} {picks}")
    return tot


print("\n=== rule backtest (daily compounding of the picked strategy's daily returns) ===")
RULES = {
    'static default':                              lambda s: 'default',
    'static ts_ao_er':                             lambda s: 'ts_ao_er',
    'A: bear_run>=3 -> ao_er, else default':       lambda s: 'ts_ao_er' if s['bear_run'] >= 3 else 'default',
    'B: bear -> ao_er, bull -> default':           lambda s: 'ts_ao_er' if s['regime'] == 'bear' else 'default',
    'D: A + normal -> ts_longup':                  lambda s: 'ts_ao_er' if s['bear_run'] >= 3 else ('ts_longup' if s['regime'] == 'normal' else 'default'),
    'E: A + normal -> ts_hma':                     lambda s: 'ts_ao_er' if s['bear_run'] >= 3 else ('ts_hma' if s['regime'] == 'normal' else 'default'),
    'F: A + bull -> ts_hma':                       lambda s: 'ts_ao_er' if s['bear_run'] >= 3 else ('ts_hma' if s['regime'] == 'bull' else 'default'),
    'G: D + bull -> ts_hma':                       lambda s: 'ts_ao_er' if s['bear_run'] >= 3 else ('ts_hma' if s['regime'] in ('bull', 'normal') else 'default'),
}
for lbl, fn in RULES.items():
    run_rule(fn, [2023, 2024, 2025, 2026], lbl)

print("\n=== walk-forward: fit per-regime pick on 2023-24, apply to 2025-26 ===")
best = {}
for rg in sorted(df['regime'].unique()):
    sub = df[(df['regime'] == rg) & (df['date'].dt.year.isin([2023, 2024]))]
    if len(sub):
        best[rg] = sub.groupby('strat')['fwd'].mean().idxmax()
print("  fitted map:", best)
run_rule(lambda s: best.get(s['regime'], 'default'), [2025, 2026], 'walk-forward (regime fit 23-24)')
run_rule(lambda s: 'default', [2025, 2026], 'static default (same window)')
run_rule(lambda s: 'ts_ao_er', [2025, 2026], 'static ts_ao_er (same window)')
