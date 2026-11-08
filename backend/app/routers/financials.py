"""三大財報表 —— 損益表、資產負債表、現金流量表，十年以上歷史。

由 scripts/earnings/fetch_financials.py 寫進 financial_line_items（結構）與
financial_facts（數值），兩者分開存：科目標籤與排序一份就夠，不必跟著 80 個
期間複製 80 次。

資料來源分工寫在那支腳本裡，簡述：結構取自 SEC 申報的 R-file（有序、有標籤、
標得出小計列），數值取自 XBRL companyfacts（一次請求拿全歷史）。

period_months 的語意：
  0        資產負債表的時點值（沒有期間長度）
  3/6/9/12 損益表與現金流量表的期間長度

Endpoints:
  GET /api/financials/{ticker}                — 三張表的結構與可用期間
  GET /api/financials/{ticker}/metrics        — 基本面指標時間序列（見下）
  GET /api/financials/{ticker}/supplement     — SEC 給不了的欄位＋對帳結果
  GET /api/financials/{ticker}/nongaap        — GAAP → Non-GAAP 對帳（見下）
  GET /api/financials/{ticker}/{statement}    — 單張表的矩陣（科目 × 期間）
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..dbro import ro_rows
from ..fiscal import calendar_label, fiscal_label, fiscal_year_end_month
from ..xbrl_labels import zh

router = APIRouter()

DB = Path(__file__).resolve().parents[2] / "stock_valuation.db"
STATEMENTS = {"income": "損益表", "balance": "資產負債表", "cashflow": "現金流量表"}


def _mo(statement: str, freq: str) -> int:
    """這張表這個檢視的期間長度。資產負債表是時點，用它所屬的期間長度標記。"""
    return 12 if freq == "annual" else 3


def _fye(ticker: str) -> int | None:
    """該公司的會計年度結束月份，給期間標記用。"""
    return fiscal_year_end_month(str(DB), ticker)


def _fy_ends(ticker: str) -> set[str]:
    """該公司的會計年度結束日。年度視圖用它挑期間，光看 period_months=12 不夠。

    要排掉兩種同樣是 12 個月、但不是會計年度的期間，判準看申報表別：

    10-Q —— 滾動十二個月。AMZN 的 10-Q 現金流量表是用「過去十二個月」呈現的，
    每個季末都留下一組 12 個月的事實。放進來的話同一年會冒出四欄，而且那三欄
    只帶得動現金流那 24 個科目，損益表其餘科目整排變成「—」。

    DEF 14A —— 委任書裡給高管薪酬用的單筆淨利，期間常標成日曆年底。ONTO 的
    會計年度收在 12/28 或 1/3，那筆 12/31 會多長出一根只有一格數字的欄位。

    判表別而不是判 fp='FY'：同一個 (concept, period_end, period_months) 只存
    得下一筆，整個年度都可能是沒有 fp 的申報寫進來的——INTU 最近三年來自 8-K
    財報發布、MSFT 的 FY22 也是——逐筆篩 fp 會把真年度整年丟掉。
    """
    return {r["period_end"] for r in _rows(
        "SELECT DISTINCT period_end FROM financial_facts "
        "WHERE ticker=? AND period_months=12 "
        "AND form NOT LIKE '10-Q%' AND form NOT LIKE 'DEF %'", (ticker,))}


def _rows(sql: str, args: tuple) -> list[dict]:
    # 背景寫入拿著鎖時要等、等不到丟 503，不能回空——回空會讓「資料庫正在忙」
    # 跟「這檔沒有財報」長得一樣，三大表整區憑空消失。理由寫在 app/dbro.py。
    return ro_rows(DB, sql, args)


@router.get("/{ticker}")
def overview(ticker: str):
    t = ticker.upper()
    items = _rows("SELECT statement, COUNT(*) n FROM financial_line_items "
                  "WHERE ticker=? GROUP BY statement", (t,))
    if not items:
        return {"ticker": t, "statements": [], "note": "沒有資料 — 先跑 fetch_financials.py"}
    span = _rows("SELECT MIN(period_end) mn, MAX(period_end) mx, COUNT(DISTINCT period_end) np "
                 "FROM financial_facts WHERE ticker=?", (t,))
    return {
        "ticker": t,
        "statements": [{"key": r["statement"], "label": STATEMENTS.get(r["statement"], r["statement"]),
                        "line_items": r["n"]} for r in items],
        "coverage": span[0] if span else None,
    }


# 從三大表算出來的基本面指標（scripts/earnings/derive_metrics.py 寫入）。
# 分組是給前端排版用的，順序就是閱讀順序：先看規模，再看賺不賺錢，
# 再看現金真的有沒有進來，最後才看財務結構撐不撐得住。
# 每個指標的來源：
#   reported  直接抄公司申報的 XBRL 事實，沒有經過任何加工
#   computed  對申報數字做算術，公式一併給出去，讓人自己驗
# 分清楚是因為「毛利率 46.9%」跟「營業毛利 195.2B」性質不同——後者是公司說的，
# 前者是我除出來的。使用者要能一眼看出哪個是哪個。
FORMULAS = {
    "gross_margin": "營業毛利 ÷ 營業收入",
    "operating_margin": "營業利益 ÷ 營業收入",
    "pretax_margin": "稅前淨利 ÷ 營業收入",
    "profit_margin": "本期淨利 ÷ 營業收入",
    "rnd_intensity": "研究發展費用 ÷ 營業收入",
    "fcf": "營業活動現金流 − 資本支出",
    "fcf_margin": "自由現金流 ÷ 營業收入",
    "current_ratio": "流動資產 ÷ 流動負債",
    "debt_equity": "有息負債 ÷ 股東權益",
    "roe": "本期淨利 ÷ 期末股東權益（非期間平均）",
    "roa": "本期淨利 ÷ 期末資產總計（非期間平均）",
    "roic": "營業利益 ÷（股東權益 ＋ 有息負債）",
    "bvps": "股東權益 ÷ 稀釋股數",
    "asset_turnover": "營業收入 ÷ 資產總計",
    "debt": "各項有息負債科目相加（成分見 components 欄）",
    "net_cash": "現金及約當現金 − 有息負債",
}

METRIC_GROUPS = [
    ("規模", [("revenue", "營業收入", "money"), ("gross_profit", "營業毛利", "money"),
              ("operating_income", "營業利益", "money"), ("pretax_income", "稅前淨利", "money"),
              ("net_income", "本期淨利", "money"), ("eps_diluted", "稀釋每股盈餘", "per_share"),
              ("shares_diluted", "稀釋股數", "shares")]),
    ("獲利能力", [("gross_margin", "毛利率", "pct"), ("operating_margin", "營業利益率", "pct"),
                  ("pretax_margin", "稅前淨利率", "pct"), ("profit_margin", "淨利率", "pct"),
                  ("roe", "股東權益報酬率 ROE", "pct"), ("roa", "資產報酬率 ROA", "pct"),
                  ("roic", "投入資本報酬率 ROIC", "pct")]),
    ("現金流", [("ncfo", "營業活動現金流", "money"), ("capex", "資本支出", "money"),
                ("fcf", "自由現金流", "money"), ("fcf_margin", "自由現金流利潤率", "pct"),
                ("sbc", "股份基礎給付", "money"), ("dna", "折舊及攤銷", "money")]),
    ("費用結構", [("rnd_expense", "研究發展費用", "money"), ("rnd_intensity", "研發強度", "pct"),
                  ("sga_expense", "銷售及管理費用", "money")]),
    ("財務結構", [("total_assets", "資產總計", "money"), ("equity", "股東權益", "money"),
                  ("cash", "現金及約當現金", "money"), ("debt", "有息負債", "money"),
                  ("net_cash", "淨現金", "money"), ("current_ratio", "流動比率", "ratio"),
                  ("debt_equity", "負債權益比", "ratio"), ("bvps", "每股淨值", "per_share"),
                  ("asset_turnover", "資產週轉率", "ratio")]),
]


@router.get("/{ticker}/metrics")
def metrics(ticker: str, freq: str = "annual", limit: int = 12):
    """基本面指標時間序列。

    跟現有的 metrics 表分工：那張是每日快照的股價類比率（PE/PS/PB/EV 倍數），
    只回溯到 2025-07；這裡是從 SEC 三大表算出來的基本面，每個會計期間一筆、
    十年以上。兩者互補，不重疊。

    freq=annual 只取會計年度（fp=FY）。季報也會揭露滾動 12 個月，期間長度同樣是
    12 個月，混進來的話 AMZN 會冒出 74 個「年度」。
    """
    t = ticker.upper()
    if freq == "annual":
        rows = _rows("SELECT * FROM financial_derived WHERE ticker=? AND is_fiscal_year=1 "
                     "ORDER BY period_end DESC LIMIT ?", (t, limit))
    else:
        rows = _rows("SELECT * FROM financial_derived WHERE ticker=? AND period_months=3 "
                     "ORDER BY period_end DESC LIMIT ?", (t, limit))
    if not rows:
        raise HTTPException(404, f"no derived metrics for {t}")

    periods = [r["period_end"] for r in rows]
    by_period = {r["period_end"]: r for r in rows}
    groups = []
    for name, fields in METRIC_GROUPS:
        items = []
        for key, label, fmt in fields:
            values = {p: by_period[p].get(key) for p in periods}
            if all(v is None for v in values.values()):
                continue                      # 這檔完全沒有這個指標就不要占版面
            item = {"key": key, "label": label, "format": fmt, "values": values,
                    "source": "computed" if key in FORMULAS else "reported"}
            if key in FORMULAS:
                item["formula"] = FORMULAS[key]
            if key == "debt":
                # 有息負債是全表唯一「加總多個科目」的欄位，把成分attach上去
                item["components"] = {p: by_period[p].get("debt_components") for p in periods}
            items.append(item)
        if items:
            groups.append({"group": name, "items": items})
    months = 12 if freq == "annual" else 3
    return {"ticker": t, "freq": freq, "periods": periods,
            "period_labels": {p: calendar_label(p, months) for p in periods},
            "fiscal_labels": {p: fiscal_label(p, _fye(t), months) for p in periods},
            "groups": groups}


# stockanalysis 有而 SEC 沒有的欄位。這站只有 TTM 單一快照、沒有歷史，
# 所以它不負責時間序列，只補「當下」這一格。
SUPPLEMENT_GROUPS = [
    ("估值", "valuation", None),
    ("分析師預估", "analystForecasts", None),
    ("公允價值", "fairValue", None),
    ("財務評分", "scores", None),
    ("股利與回饋", "dividends", None),
    ("經營效率", "financialEfficiency", {"employees", "revPerEmployee",
                                         "profitPerEmployee", "wacc", "inventoryturnover"}),
    ("債務負擔", "financialPosition", {"debtEbitda", "debtFcf", "interestCoverage",
                                       "quickRatio"}),
    ("利潤率", "margins", {"ebitdaMargin", "ebitMargin"}),
]


@router.get("/{ticker}/supplement")
def supplement(ticker: str):
    """SEC 拿不到的欄位（員工數、WACC、Z-Score、分析師預估…）＋自算值的對帳結果。

    對帳的 verdict 意義：
      ok          差異在容許範圍內
      definition  口徑本來就不同（對方的現金含短期投資、報酬率用期間平均）
      stale       我的資產負債表比對方舊一季，不能直接比
      check       其餘 —— 真正值得看一眼的
    """
    t = ticker.upper()
    stats = _rows("SELECT * FROM sa_statistics WHERE ticker=?", (t,))
    if not stats:
        return {"ticker": t, "groups": [], "reconciliation": [],
                "note": "沒有補充資料 — 先跑 fetch_stockanalysis.py"}
    by_sec: dict[str, list[dict]] = {}
    for r in stats:
        by_sec.setdefault(r["section"], []).append(r)

    groups = []
    for label, section, only in SUPPLEMENT_GROUPS:
        items = [{"id": r["stat_id"], "label": r["title"],
                  "value": r["value_text"], "value_num": r["value_num"]}
                 for r in by_sec.get(section, [])
                 if only is None or r["stat_id"] in only]
        if items:
            groups.append({"group": label, "items": items})

    recon = _rows("SELECT * FROM metric_reconciliation WHERE ticker=? "
                  "ORDER BY CASE verdict WHEN 'check' THEN 0 WHEN 'stale' THEN 1 "
                  "WHEN 'definition' THEN 2 ELSE 3 END, ABS(diff_pct) DESC", (t,))
    return {
        "ticker": t,
        "source": "stockanalysis.com（TTM 快照）",
        "fetched_at": stats[0]["fetched_at"],
        "groups": groups,
        "reconciliation": recon,
        "flagged": sum(1 for r in recon if r["verdict"] == "check"),
    }


@router.get("/{ticker}/nongaap")
def nongaap(ticker: str, months: int = 3, limit: int = 8):
    """公司自訂的 Non-GAAP，以及它跟 GAAP 的差距。

    來源是財報新聞稿（8-K Item 2.02 / 外國發行人的 6-K）裡的對帳表——
    SEC 的 XBRL 只標記 GAAP，stockanalysis 給的是 S&P 正規化值，
    公司自己定義的 Non-GAAP 只有新聞稿裡有。

    每一列都帶三段：GAAP 起點、逐項調整、Non-GAAP 結果，外加 residual
    （GAAP + 調整 − Non-GAAP，理論上是 0，不是 0 代表有調整項沒被解析到）。

    要注意 Non-GAAP 跨公司不可比：同樣叫 Adjusted EBITDA，各家剔除的項目不同。
    它適合看同一家公司的趨勢，不適合拿來排序。
    """
    t = ticker.upper()
    rows = _rows(
        "SELECT * FROM nongaap_reconciliation WHERE ticker=? AND period_months=? "
        "ORDER BY period_end DESC", (t, months))
    if not rows:
        return {"ticker": t, "months": months, "periods": [], "measures": [],
                "note": "這檔沒有解析到 Non-GAAP 對帳表（有些公司只報 GAAP）"}

    periods = sorted({r["period_end"] for r in rows}, reverse=True)[:limit]
    keep = set(periods)
    by_measure: dict[str, dict] = {}
    for r in rows:
        if r["period_end"] not in keep:
            continue
        m = by_measure.setdefault(r["nongaap_label"], {
            "label": r["nongaap_label"], "gaap_label": r["gaap_label"],
            "unit": r["unit"], "values": {}})
        try:
            adjustments = json.loads(r["adjustments"] or "[]")
        except (TypeError, json.JSONDecodeError):
            adjustments = []
        m["values"][r["period_end"]] = {
            "gaap": r["gaap_value"], "nongaap": r["nongaap_value"],
            "gap": (r["nongaap_value"] - r["gaap_value"])
                   if r["nongaap_value"] is not None and r["gaap_value"] is not None else None,
            "adjustments": adjustments,
            "residual": r["residual"],
        }
    # 金額排前面、百分比在後；同類照最新一期的差距由大到小——差距大的最值得看
    def _key(m):
        v = m["values"].get(periods[0]) or {}
        return (m["unit"] != "amount", -abs(v.get("gap") or 0))

    return {
        "ticker": t, "months": months, "periods": periods,
        "source": "財報新聞稿（8-K / 6-K）",
        "filed": rows[0]["filed"], "source_url": rows[0]["source_url"],
        "period_labels": {p: calendar_label(p, months) for p in periods},
        "measures": sorted(by_measure.values(), key=_key),
    }


@router.get("/{ticker}/{statement}")
def statement(ticker: str, statement: str, freq: str = "annual", limit: int = 12):
    """一張表的「科目 × 期間」矩陣，期間新到舊。

    freq=annual 取會計年度（period_months=12 且 fp='FY'），quarterly 取 3 個月。
    年度一定要連 fp 一起篩：季報也會揭露滾動 12 個月，期間長度同樣是 12 個月，
    混進來的話 AMZN 會冒出 40 個「年度」（同 /metrics 的註解）。

    資產負債表沒有期間長度（存的是時點值），所以兩種 freq 都回同一組時點，只是
    取樣密度不同：年度只留每個會計年度的最後一個時點，季度全留。
    """
    t = ticker.upper()
    if statement not in STATEMENTS:
        raise HTTPException(400, f"statement 只能是 {list(STATEMENTS)}")

    items = _rows("SELECT * FROM financial_line_items WHERE ticker=? AND statement=? "
                  "ORDER BY order_idx", (t, statement))
    if not items:
        raise HTTPException(404, f"no {statement} statement for {t}")

    concepts = [i["concept"] for i in items]
    qs = ",".join("?" * len(concepts))
    if statement == "balance":
        facts = _rows(f"SELECT * FROM financial_facts WHERE ticker=? AND period_months=0 "
                      f"AND concept IN ({qs})", (t, *concepts))
    elif freq == "annual":
        fy = sorted(_fy_ends(t))              # 只留會計年度末，理由見 _fy_ends
        ps = ",".join("?" * len(fy))
        facts = _rows(
            f"SELECT * FROM financial_facts WHERE ticker=? AND period_months=12 "
            + (f"AND period_end IN ({ps}) " if fy else "")
            + f"AND concept IN ({qs})", (t, *fy, *concepts))
    else:
        facts = _rows(f"SELECT * FROM financial_facts WHERE ticker=? AND period_months=3 "
                      f"AND concept IN ({qs})", (t, *concepts))

    by_period: dict[str, dict[str, float]] = {}
    units: dict[str, str] = {}
    derived_periods: set[str] = set()
    for f in facts:
        by_period.setdefault(f["period_end"], {})[f["concept"]] = f["value"]
        units.setdefault(f["concept"], f["unit"])
        if f.get("derived"):
            # 10-K 不單獨報第四季，那一季是「全年 − 前三季」算出來的。
            # 標出來讓人知道這一欄不是申報原數。
            derived_periods.add(f["period_end"])

    periods = sorted(by_period, reverse=True)
    if statement == "balance" and freq == "annual":
        # 資產負債表存的是時點值、每季都有。年度視圖不能用「每個日曆年最後一個」
        # 去挑——AAPL 會計年度 9 月底，那樣會挑到 12 月的季末（Q1）而不是年度數字。
        # 正解是對齊損益表 12 個月期間的結束日，那就是各公司自己的會計年度末。
        fy_ends = _fy_ends(t)
        aligned = [p for p in periods if p in fy_ends]
        periods = aligned or periods          # 對不到就退回原本的時點序列
    periods = periods[:limit]

    return {
        "ticker": t, "statement": statement, "label": STATEMENTS[statement],
        "freq": "instant" if statement == "balance" else freq,
        "periods": periods,
        # 預設用日曆季：跨公司比較時，各家會計年度末不同會讓同一段時間標成不同
        # 季別（AAPL 的 12 月底那季是 FY26Q1、NVDA 的是 FY26Q4），沒辦法並排看。
        # 會計季一併回傳，前端放在滑鼠提示裡。
        "period_labels": {p: calendar_label(p, _mo(statement, freq)) for p in periods},
        "fiscal_labels": {p: fiscal_label(p, _fye(t), _mo(statement, freq)) for p in periods},
        "derived_periods": sorted(derived_periods & set(periods)),
        # 單位必須逐科目給：同一張損益表裡營收是 USD、每股盈餘是 USD/shares、
        # 加權平均股數是 shares，用同一個單位去格式化會把 EPS 顯示成 0.0
        "unit": (facts[0]["unit"] if facts else ""),
        # 全部期間都沒有數值的列是區段標題（Operating expenses:、Current assets:），
        # 標出來讓前端當標題排版，而不是印成一排 0
        "line_items": [
            dict(row, is_header=all(v is None for v in row["values"].values()))
            for row in (
                {"concept": i["concept"],
                 # 中文優先，對不到的保留申報原文（財報科目硬翻比留英文更糟）；
                 # label_en 一併回傳，前端要對照原始申報時看得到
                 "label": zh(i["concept"], i["label"]),
                 "label_en": i["label"],
                 "translated": bool(zh(i["concept"])),
                 "unit": units.get(i["concept"], ""),
                 "is_total": bool(i["is_total"]), "depth": i["depth"],
                 "values": {p: by_period.get(p, {}).get(i["concept"]) for p in periods}}
                for i in items)
        ],
    }
