#!/usr/bin/env python3
"""
DCF 回測 —— 用過去的資料算估值，再用之後真實發生的股價檢驗算法合不合理。

估值模型的每一個假設（終期利潤率怎麼常態化、資本支出要不要正常化、折現率怎麼
拆）在紙上都能各自講出道理。要分出高下只有一個辦法：把模型退回到過去某一天，
只餵它那天以前就存在的檔案，算出估值，然後看接下來股價怎麼走。

資料來源都是 point-in-time：
    data/statistics/{T}_statistics_{d}.json      當日快照
    data/forecast/detail/{T}_forecast_{d}.json   當日分析師預估
    data/financials/{T}_ratios_{d}.csv           每週一檔，含 Last Close Price
`FundamentalBundle(as_of=...)` 會過濾掉日期在形成日之後的檔案，所以模型看不到
未來。價格序列則從每週的 ratios 檔抽出來。

衡量指標：
    Spearman IC   估值上檔空間與後續報酬的等級相關（跨標的，市場整體漲跌會抵銷）
    五分位價差    最被低估的一組 減 最被高估的一組，之後的平均報酬差
    命中率        判定「低估」的標的，後續報酬贏過中位數的比例
    正交 IC       把自由現金流殖利率的成分抽掉之後，DCF 還剩多少資訊
                  —— 這是最關鍵的一項。DCF 排序與殖利率的相關性高達 0.80，
                  如果抽掉之後什麼都不剩，那整套模型就只是把一行比率重算一遍。
    閘門檢定      被判定「不適用」的標的，後續報酬是否真的比較差
                  —— 模型說「不要引用這個數字」時，那句話有沒有價值

已知限制（讀結果前先看）：
    * 價格序列只有 2026-02 至 2026-09，最長前瞻窗口 7 個月。DCF 是多年期的
      論點，7 個月主要在測動能與風格輪動。
    * 三個形成日在時間與持股上高度重疊，等於約 1.5 個獨立觀測，顯著性星號
      會高估把握度。
    * 有強 IC 的兩個窗口市場中位報酬都是負的，只有一個市場方向。

用法：
    uv run python scripts/backtest_dcf.py                    # 全部版本、全部形成日
    uv run python scripts/backtest_dcf.py --variant owner
    uv run python scripts/backtest_dcf.py --formation 2026-02-02
"""
from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import re
import statistics as st
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.dcf_calculator import DCFCalculator, TERMINAL_VARIANTS  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"

# 有 statistics + forecast + ratios 三者齊全、且後面還有夠長價格序列的日子。
# 2026-02-01 那天財報三表還沒落地（檔案日期是 02-02），所以形成日取 02-02。
DEFAULT_FORMATIONS = ["2026-02-02", "2026-06-01", "2026-07-02"]

MIN_NAMES = 25          # 少於這個數量的橫斷面不值得算統計量
QUANTILES = 5


# ── 價格序列 ────────────────────────────────────────────────────────────────

def load_price_series() -> dict[str, dict[date, float]]:
    """從每週的 ratios 檔抽出收盤價，組成 {ticker: {date: price}}。"""
    out: dict[str, dict[date, float]] = {}
    for f in glob.glob(str(DATA_DIR / "financials" / "*_ratios_*.csv")):
        base = os.path.basename(f)
        ticker = base.split("_ratios_")[0]
        m = re.search(r"(\d{4}-\d{2}-\d{2})", base)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        try:
            with open(f, newline="", encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) > 2 and row[1].strip() == "Last Close Price":
                        try:
                            v = float(row[2].replace(",", ""))
                        except ValueError:
                            break
                        if v > 0:
                            out.setdefault(ticker, {})[d] = v
                        break
        except OSError:
            continue
    return out


def price_at(series: dict[date, float], target: date, *, back: bool) -> tuple[date, float] | None:
    """最接近 target 的觀測點；back=True 取之前，False 取之後。"""
    cands = [d for d in series if (d <= target if back else d >= target)]
    if not cands:
        return None
    d = max(cands) if back else min(cands)
    return d, series[d]


# ── 統計量 ──────────────────────────────────────────────────────────────────

def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def quantile_spread(pairs: list[tuple[float, float]], q: int = QUANTILES) -> dict:
    """pairs = [(訊號, 後續報酬)]，依訊號排序後分組，回傳各組平均報酬。"""
    s = sorted(pairs, key=lambda p: p[0])
    n = len(s)
    buckets = []
    for i in range(q):
        lo, hi = n * i // q, n * (i + 1) // q
        chunk = s[lo:hi]
        buckets.append(st.mean(r for _, r in chunk) if chunk else float("nan"))
    return {"buckets": buckets, "spread": buckets[-1] - buckets[0]}


# ── 單次橫斷面 ──────────────────────────────────────────────────────────────

def run_cross_section(formation: date, variant: str, prices: dict, horizon_days: int | None) -> dict | None:
    tickers = sorted({
        os.path.basename(f).split("_statistics_")[0]
        for f in glob.glob(str(DATA_DIR / "statistics" / "*_statistics_*.json"))
    })
    calc = DCFCalculator(data_dir=DATA_DIR, as_of=formation, terminal_variant=variant)

    rows = []
    for t in tickers:
        series = prices.get(t)
        if not series:
            continue
        p0 = price_at(series, formation, back=True)
        if not p0 or (formation - p0[0]).days > 21:
            continue
        target = date.fromordinal(formation.toordinal() + horizon_days) if horizon_days else max(series)
        p1 = price_at(series, target, back=False) or price_at(series, target, back=True)
        if not p1 or p1[0] <= p0[0]:
            continue
        fwd = p1[1] / p0[1] - 1

        try:
            r = calc.compute(t)
        except Exception:
            continue
        if not r or r["fair_value"]["upside_pct"] is None:
            continue

        from src.analysis.dcf_inputs import FundamentalBundle
        rows.append({
            "ticker": t,
            "upside": r["fair_value"]["upside_pct"],
            "fwd": fwd * 100,
            "days": (p1[0] - p0[0]).days,
            "meaningful": r["applicability"]["meaningful"],
            "confidence": r["confidence"]["score"],
            "fcf_yield": FundamentalBundle(t, DATA_DIR, as_of=formation)
                         .stat("Dividends & Yields", "FCF Yield"),
        })

    if len(rows) < MIN_NAMES:
        return None

    def stats_for(subset: list[dict]) -> dict:
        if len(subset) < MIN_NAMES:
            return {}
        up = [x["upside"] for x in subset]
        fw = [x["fwd"] for x in subset]
        ic = spearman(up, fw)
        qs = quantile_spread(list(zip(up, fw)))
        med = st.median(fw)
        flagged = [x for x in subset if x["upside"] > 0]
        hit = (sum(1 for x in flagged if x["fwd"] > med) / len(flagged)) if flagged else None
        return {
            "n": len(subset),
            "ic": ic,
            "ic_se": 1 / math.sqrt(len(subset) - 1),
            "buckets": qs["buckets"],
            "spread": qs["spread"],
            "hit_rate": hit,
            "n_flagged": len(flagged),
            "median_fwd": med,
        }

    # 正交檢定：抽掉自由現金流殖利率之後，DCF 還剩多少
    paired = [x for x in rows if x["fcf_yield"] is not None]
    ortho = None
    if len(paired) >= MIN_NAMES:
        y = [x["fcf_yield"] for x in paired]
        u = [x["upside"] for x in paired]
        f = [x["fwd"] for x in paired]
        ortho = {
            "n": len(paired),
            "dcf": spearman(u, f),
            "yield": spearman(y, f),
            "dcf_resid": spearman(orthogonalise(y, u), f),
            "yield_resid": spearman(orthogonalise(u, y), f),
            "corr": spearman(u, y),
        }

    # 閘門檢定：模型說「不適用」的標的，後續報酬真的比較差嗎
    ok = [x["fwd"] for x in rows if x["meaningful"]]
    bad = [x["fwd"] for x in rows if not x["meaningful"]]
    gate = None
    if len(ok) >= 20 and len(bad) >= 10:
        gate = {"n_ok": len(ok), "med_ok": st.median(ok),
                "n_bad": len(bad), "med_bad": st.median(bad)}

    # 可信度當篩選器
    hi = [x for x in rows if x["confidence"] >= 60]
    conf_filter = None
    if len(hi) >= MIN_NAMES:
        conf_filter = {"n": len(hi),
                       "ic": spearman([x["upside"] for x in hi], [x["fwd"] for x in hi])}

    return {
        "formation": formation,
        "variant": variant,
        "days": int(st.median(x["days"] for x in rows)),
        "all": stats_for(rows),
        "meaningful_only": stats_for([x for x in rows if x["meaningful"]]),
        "orthogonal": ortho,
        "gate": gate,
        "conf_filter": conf_filter,
        "rows": rows,
    }


def orthogonalise(control: list[float], signal: list[float]) -> list[float]:
    """把 signal 對 control 做等級迴歸後的殘差 —— control 解釋不了的那部分。"""
    rc, rs = _ranks(control), _ranks(signal)
    mc, ms = st.mean(rc), st.mean(rs)
    scc = sum((a - mc) ** 2 for a in rc)
    beta = sum((a - mc) * (b - ms) for a, b in zip(rc, rs)) / scc if scc else 0.0
    return [b - (ms + beta * (a - mc)) for a, b in zip(rc, rs)]


# ── 對照組：不用 DCF 的簡單訊號 ──────────────────────────────────────────────

def naive_signals(formation: date, prices: dict, horizon_days: int | None) -> dict:
    """自由現金流殖利率與盈餘殖利率 —— 如果 DCF 贏不過這兩個，它就沒有存在價值。"""
    from src.analysis.dcf_inputs import FundamentalBundle

    tickers = sorted({
        os.path.basename(f).split("_statistics_")[0]
        for f in glob.glob(str(DATA_DIR / "statistics" / "*_statistics_*.json"))
    })
    out: dict[str, list[tuple[float, float]]] = {"FCF 殖利率": [], "盈餘殖利率": []}
    for t in tickers:
        series = prices.get(t)
        if not series:
            continue
        p0 = price_at(series, formation, back=True)
        if not p0 or (formation - p0[0]).days > 21:
            continue
        target = date.fromordinal(formation.toordinal() + horizon_days) if horizon_days else max(series)
        p1 = price_at(series, target, back=False) or price_at(series, target, back=True)
        if not p1 or p1[0] <= p0[0]:
            continue
        fwd = (p1[1] / p0[1] - 1) * 100
        b = FundamentalBundle(t, DATA_DIR, as_of=formation)
        if not b.statistics:
            continue
        for label, section, field in (("FCF 殖利率", "Dividends & Yields", "FCF Yield"),
                                      ("盈餘殖利率", "Dividends & Yields", "Earnings Yield")):
            v = b.stat(section, field)
            if v is not None:
                out[label].append((v, fwd))
    return {
        k: {"n": len(v), "ic": spearman([a for a, _ in v], [c for _, c in v]),
            **quantile_spread(v)}
        for k, v in out.items() if len(v) >= MIN_NAMES
    }


# ── 輸出 ────────────────────────────────────────────────────────────────────

def fmt_stats(label: str, s: dict) -> str:
    if not s:
        return f"    {label:<16s} 樣本不足"
    ic = s["ic"]
    sig = "" if ic is None else ("  ***" if abs(ic) > 2.6 * s["ic_se"] else
                                 "  **" if abs(ic) > 1.96 * s["ic_se"] else
                                 "  *" if abs(ic) > 1.64 * s["ic_se"] else "")
    b = "  ".join(f"{x:>+6.1f}" for x in s["buckets"])
    hit = f"{s['hit_rate']:.0%}" if s["hit_rate"] is not None else "—"
    return (f"    {label:<16s} n={s['n']:>3}  IC={ic:+.3f}±{s['ic_se']:.3f}{sig:<5s}"
            f"  五分位 [{b}]  價差 {s['spread']:+.1f}pp  低估命中率 {hit}")


def main() -> None:
    ap = argparse.ArgumentParser(description="DCF 回測")
    ap.add_argument("--variant", action="append", choices=list(TERMINAL_VARIANTS))
    ap.add_argument("--formation", action="append")
    ap.add_argument("--horizon", type=int, help="前瞻天數（預設用到資料末端）")
    args = ap.parse_args()

    variants = args.variant or list(TERMINAL_VARIANTS)
    formations = [date.fromisoformat(d) for d in (args.formation or DEFAULT_FORMATIONS)]

    print("=" * 96)
    print("  DCF 回測 —— 用形成日以前的資料估值，用之後的股價檢驗")
    print("=" * 96)
    prices = load_price_series()
    print(f"  價格序列 {len(prices)} 檔；IC 顯著性 * 90% / ** 95% / *** 99%\n")

    summary: dict[str, list[float]] = {v: [] for v in variants}
    for f in formations:
        header_done = False
        last = None
        for v in variants:
            res = run_cross_section(f, v, prices, args.horizon)
            if not res:
                continue
            if not header_done:
                print(f"  ── 形成日 {f}　前瞻 {res['days']} 天 " + "─" * 42)
                naive = naive_signals(f, prices, args.horizon)
                for k, s in naive.items():
                    b = "  ".join(f"{x:>+6.1f}" for x in s["buckets"])
                    print(f"    {'[對照] ' + k:<16s} n={s['n']:>3}  IC={s['ic']:+.3f}"
                          f"{'':<11s}  五分位 [{b}]  價差 {s['spread']:+.1f}pp")
                header_done = True
            print(fmt_stats(v, res["all"]))
            m = res["meaningful_only"]
            if m and m["n"] != res["all"]["n"]:
                print(fmt_stats("  └ 僅可用標的", m))
            if res["all"].get("ic") is not None:
                summary[v].append(res["all"]["ic"])
            last = res
        if last:
            o = last.get("orthogonal")
            if o:
                print(f"\n    正交檢定（{last['variant']}）　DCF 與 FCF 殖利率的排序相關 {o['corr']:+.3f}")
                print(f"      DCF 單獨            IC {o['dcf']:+.3f}")
                print(f"      FCF 殖利率單獨      IC {o['yield']:+.3f}")
                print(f"      DCF ⟂ 殖利率        IC {o['dcf_resid']:+.3f}   ← 抽掉殖利率後 DCF 剩下的")
                print(f"      殖利率 ⟂ DCF        IC {o['yield_resid']:+.3f}   ← 抽掉 DCF 後殖利率剩下的")
            g = last.get("gate")
            if g:
                print(f"    閘門檢定　判定可用 n={g['n_ok']} 中位報酬 {g['med_ok']:+.1f}%　"
                      f"判定不適用 n={g['n_bad']} 中位報酬 {g['med_bad']:+.1f}%　"
                      f"差 {g['med_ok'] - g['med_bad']:+.1f}pp")
            cf = last.get("conf_filter")
            if cf and last["all"].get("ic") is not None:
                print(f"    可信度篩選　全部 IC {last['all']['ic']:+.3f} → 可信度≥60 "
                      f"(n={cf['n']}) IC {cf['ic']:+.3f}")
        print()

    print("  ── 各版本平均 IC " + "─" * 60)
    for v in variants:
        ics = summary[v]
        if ics:
            print(f"    {v:<16s} 平均 IC {st.mean(ics):+.3f}  （{len(ics)} 個橫斷面："
                  f"{', '.join(f'{x:+.3f}' for x in ics)}）")
    print("=" * 96)


if __name__ == "__main__":
    main()
