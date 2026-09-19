#!/usr/bin/env python3
"""
score_forecast_text.py — LLM-as-judge scoring for 业绩预告 text (real text model).

Replaces the failed keyword approach (see algorithmic-trading skill reference
`keyword-nlp-fails-on-financial-text.md`). The keyword gate was anti-predictive
(-2.43pp) because it could not resolve financial-text context:
  - "商誉减值" inside a 扭亏 explanation refers to LAST year's impairment
    (the reason this year is a turnaround — a POSITIVE setup)
  - positive sentences contain negative words ("成本减少")
  - the forecast TYPE dominates the text signal

This pipeline uses a real LLM (OmniRoute -> openrouter/free) with a prompt
built around those exact failure modes, and caches every score to CSV so the
backtest stays deterministic and makes zero API calls.

Architecture (the source article's "third-generation" pattern, adapted):
  - LLM reads & judges  -> structured JSON per forecast record
  - scores cached       -> shared/data/forecast_scores_llm.csv
  - backtest reads CSV  -> deterministic, fast, no network

Usage:
  # validate prompt quality on a sample (always do this first)
  .venv/bin/python utils/score_forecast_text.py --sample 20

  # full scoring run (resumable — re-run to continue after interruption)
  .venv/bin/python utils/score_forecast_text.py --workers 12

  # check progress
  .venv/bin/python utils/score_forecast_text.py --status
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

from openai import OpenAI

FORECAST_CSV = os.path.join(ROOT, 'shared', 'data', 'forecast_2026.csv')
SCORES_CSV = os.path.join(ROOT, 'shared', 'data', 'forecast_scores_llm.csv')

# Model: selected by testing 6 free candidates on 4 varied records (2026-09-19).
#   deepseek/deepseek-v4-flash-0731:free  -> 4/4 parse, 25.1s, correct on the
#       temporal trap (扭亏 -> positive, the case that killed the keyword gate)
#   nex-agi/nex-n2.5-pro:free             -> 3/4, 143.8s (fallback)
#   ling-3.0-flash-fin:free               -> 1/4 (finance-tuned but returns empty)
#   qwen3.8-27b / glm-5.2                 -> 429 rate-limited
#   nemotron-3.5-lightning                -> emits reasoning prose, no JSON
LLM_BASE_URL = os.getenv('NLP_LLM_BASE_URL', 'https://openrouter.ai/api/v1')
LLM_API_KEY = os.getenv('NLP_LLM_API_KEY', os.getenv('OPENROUTER_API_KEY', ''))
LLM_MODEL = os.getenv('NLP_LLM_MODEL', 'deepseek/deepseek-v4-flash-0731:free')
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    'NLP_LLM_FALLBACKS', 'nex-agi/nex-n2.5-pro:free').split(',') if m.strip()]

SCORE_FIELDS = ['ts_code', 'ann_date', 'ftype', 'polarity', 'sustainability',
                'oneoff_driver', 'confidence', 'reason', 'model', 'scored_at']

# ── Prompt: built around the three context failures that killed keywords ───
PROMPT_TMPL = """你是A股业绩预告文本分析师。阅读业绩预告，判断其隐含的信号。

关键规则（必须遵守）：
1. 区分时点：只看"本报告期/本期"的经营驱动。若"商誉减值""资产减值""计提"发生在
   上年同期或去年同期（本期不再发生），这是【正面】信号，不是负面。
2. 区分可持续性：主营业务量价齐升、成本管控、产品结构优化、订单饱满 = 可持续。
   政府补助、资产处置、公允价值变动、投资收益等非经常性损益驱动的增长 = 不可持续。
3. 区分措辞精度：给出具体数字区间 = 信息明确；"一定幅度""预计将""取决于""尚需"
   = 不确定性高，应降低 confidence。
4. 不要因为文本中出现了"下降""减少""风险"等词就判定负面 —— 要看这些词描述的是
   成本/费用的下降（正面）还是收入/利润的下降（负面）。

输出严格 JSON（不要 markdown 代码块，不要额外文字）：
{"polarity":"positive|neutral|negative","sustainability":0.0-1.0,"oneoff_driver":true|false,"confidence":0.0-1.0,"reason":"20字以内"}

---
业绩预告类型: <<FTYPE>>
摘要: <<SUMMARY>>
变动原因: <<CHANGE_REASON>>
"""


def build_prompt(row) -> str:
    # NOTE: .replace() not .format() — the template contains literal JSON braces.
    return (PROMPT_TMPL
            .replace('<<FTYPE>>', str(row.get('type', '') or ''))
            .replace('<<SUMMARY>>', str(row.get('summary', '') or '')[:600])
            .replace('<<CHANGE_REASON>>', str(row.get('change_reason', '') or '')[:1500]))


def parse_response(text: str) -> dict | None:
    """Lenient JSON extraction — the model may wrap in prose or code fences."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    m = re.search(r'\{.*\}', t, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    pol = str(d.get('polarity', '')).lower().strip()
    if pol not in ('positive', 'neutral', 'negative'):
        return None
    try:
        d['sustainability'] = float(d.get('sustainability', 0.5))
        d['confidence'] = float(d.get('confidence', 0.5))
    except (TypeError, ValueError):
        d['sustainability'], d['confidence'] = 0.5, 0.5
    d['oneoff_driver'] = bool(d.get('oneoff_driver', False))
    d['polarity'] = pol
    d['reason'] = str(d.get('reason', ''))[:60]
    return d


def load_done() -> set:
    """Keys already scored (resumability)."""
    done = set()
    if os.path.exists(SCORES_CSV):
        with open(SCORES_CSV, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                done.add((r['ts_code'], r['ann_date']))
    return done


_write_lock = threading.Lock()
_errors: list = []          # recent failure reasons (diagnostics)


def _record_error(msg: str) -> None:
    with _write_lock:
        if len(_errors) < 200:
            _errors.append(msg)


def get_errors() -> list:
    return list(_errors)


def append_rows(rows: list) -> None:
    new_file = not os.path.exists(SCORES_CSV)
    with _write_lock:
        with open(SCORES_CSV, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
            if new_file:
                w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, '') for k in SCORE_FIELDS})


def score_one(client: OpenAI, row) -> dict | None:
    prompt = build_prompt(row)
    for model in [LLM_MODEL] + LLM_FALLBACK_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0,
                max_tokens=900,
            )
            parsed = parse_response(resp.choices[0].message.content or '')
            if parsed is None:
                _record_error(f"[{model}] unparseable: "
                              f"{(resp.choices[0].message.content or '')[:100]!r}")
                continue
            return {
                'ts_code': row['ts_code'],
                'ann_date': str(row['ann_date']),
                'ftype': str(row.get('type', '') or ''),
                'polarity': parsed['polarity'],
                'sustainability': round(parsed['sustainability'], 3),
                'oneoff_driver': parsed['oneoff_driver'],
                'confidence': round(parsed['confidence'], 3),
                'reason': parsed['reason'],
                'model': model,
                'scored_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
        except Exception as e:
            _record_error(f"[{model}] {type(e).__name__}: {str(e)[:120]}")
            time.sleep(0.5)
    return None


def read_forecast() -> list:
    with open(FORECAST_CSV, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=0, help='score only N records (validation)')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='print prompts only')
    args = ap.parse_args()

    rows = read_forecast()
    done = load_done()
    print(f"forecast records: {len(rows)}   already scored: {len(done)}   remaining: {len(rows) - len(done)}")

    if args.status:
        return

    todo = [r for r in rows if (r['ts_code'], str(r['ann_date'])) not in done]
    if args.sample:
        todo = todo[:args.sample]
    if not todo:
        print("nothing to do")
        return

    if args.dry_run:
        print("\n--- sample prompt ---")
        print(build_prompt(todo[0]))
        return

    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=90)
    print(f"scoring {len(todo)} records with {args.workers} workers via {LLM_MODEL}\n")

    t0 = time.time()
    ok = fail = 0
    batch = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(score_one, client, r): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            if res is None:
                fail += 1
            else:
                batch.append(res)
                ok += 1
            if len(batch) >= 25:
                append_rows(batch)
                batch = []
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                rate = i / el if el else 0
                eta = (len(todo) - i) / rate if rate else 0
                print(f"  {i}/{len(todo)}  ok={ok} fail={fail}  {rate:.1f}/s  eta={eta/60:.1f}min")
    if batch:
        append_rows(batch)

    print(f"\nDONE ok={ok} fail={fail} in {(time.time()-t0)/60:.1f}min -> {SCORES_CSV}")
    if _errors:
        print(f"\nfirst failures (of {len(_errors)} recorded):")
        for e in _errors[:5]:
            print(f"  - {e}")


if __name__ == '__main__':
    main()
