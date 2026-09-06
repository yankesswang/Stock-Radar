"""各部門營收與利潤 —— 資料來自 SEC 申報的 R-file 報表。

由 scripts/earnings/fetch_segments.py 寫進 segment_financials（跟估值同一個庫）。
不走 XBRL companyfacts API 的原因寫在那支腳本裡：那支只回合併數，維度被剝掉了。

兩件事決定了這裡的結構，都不是選擇題：

1. **一家公司同時有好幾種拆法**，各自加總都等於總營收。ADI 就同時揭露
   「終端市場別（Automotive / Industrial…）」與「通路別（Direct / Distributors）」，
   混進同一份清單再加總就會重複計算。R-file 的報表名稱就是維度，照它分組。

2. **部門利潤的口徑各家不同**。MSFT 給營業利益，ADI 在新版 ASC 280 格式下給的是
   Net income，有些公司只給毛利。所以挑一個能用的並標明 profit_measure，
   不假裝大家都是營業利益。

同一期間常同時有 3 / 6 / 9 / 12 個月的欄位（季報會帶累計數），查詢一律指定 months，
預設 3 個月＝單季，否則季度與累計混在一起會算出假的成長率。

Endpoints:
  GET /api/segments/{ticker}          — 各維度 × 各期間 × 各部門
  GET /api/segments/{ticker}/latest   — 最新一期，附 YoY（去年同期）
"""
import re
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..fiscal import calendar_label, fiscal_label, fiscal_year_end_month
from ..xbrl_labels import zh

router = APIRouter()

DB = Path(__file__).resolve().parents[2] / "stock_valuation.db"

# 科目標籤各家不同（Revenue / Net sales / Total revenues…），靠 XBRL 概念名歸類才穩
# 只收「部門總額」層級的概念。像 RevenueFromCloudAndSoftware、
# SoftwareLicensesAndSupportRevenue 這種是部門底下的產品線明細，收進來會把
# 某一條產品線誤當成整個部門的營收（setdefault 取先到的，順序還不保證）。
REVENUE_CONCEPTS = {
    # US GAAP
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues", "RevenuesExcludingDisposalGroups", "SalesRevenueNet",
    "RevenueFromExternalCustomers",
    "RevenueFromExternalCustomersIncludingIntersegmentRevenues",
    # IFRS（TSM/ASX/SAP 走這套，注意是複數 Contracts/Customers）
    "Revenue", "RevenueFromContractsWithCustomers",
}
# 部門利潤的優先序：營業利益最可比，往下退到毛利、稅前、淨利。
# 退到哪一階會寫進 profit_measure，前端要照實顯示，不能一律叫「營業利益」。
PROFIT_PRIORITY = [
    ("OperatingIncomeLoss", "營業利益"),
    ("ProfitLossFromOperatingActivities", "營業利益"),
    ("SegmentReportingInformationOperatingIncomeLoss", "部門營業利益"),
    ("ContributionProfitLoss", "貢獻利潤"),
    ("GrossProfit", "毛利"),
    ("SegmentGrossProfitLoss", "毛利"),
    ("SegmentReportingGrossProfit", "毛利"),
    ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "稅前淨利"),
    ("NetIncomeLoss", "淨利"),
    ("ProfitLoss", "淨利"),
]
PROFIT_RANK = {c: i for i, (c, _) in enumerate(PROFIT_PRIORITY)}

# 人工釘選的預設拆法（比對報表名稱的關鍵字，命中就排第一）。
# 只放規則排不出來、需要產業判斷的：藥廠看藥品別、IBM 看事業別而不是地區別。
AXIS_PINS = {
    "LLY": "by product",
    "IBM": "by major products and service",   # 實際名稱是 "Service Offerings" 不是 "Services"
    "ABBV": "disaggregation of revenue",
}
PROFIT_LABEL = dict(PROFIT_PRIORITY)


# 每個部門底下抓到的所有科目。DB 一直都存著，但先前 API 只吐營收與利潤，
# 成本結構、研發強度、資本支出這些逐部門的資訊等於白抓了。
# 分四類是為了前端能分組顯示，不是一長串看不出重點的清單。
CONCEPT_LABELS = {
    # 成本與費用
    "CostOfGoodsAndServicesSold": ("成本", "cost"),
    "CostOfRevenue": ("營業成本", "cost"),
    "CostsAndExpenses": ("成本費用合計", "cost"),
    "ResearchAndDevelopmentExpense": ("研發費用", "cost"),
    "SellingGeneralAndAdministrativeExpense": ("銷管費用", "cost"),
    "SellingAndMarketingExpense": ("行銷費用", "cost"),
    "GeneralAndAdministrativeExpense": ("管理費用", "cost"),
    "OperatingExpenses": ("營業費用", "cost"),
    "RestructuringCharges": ("重組費用", "cost"),
    "OperatingExpensesAmortizationOfIntangibles": ("無形資產攤銷", "cost"),
    "InterestExpense": ("利息費用", "cost"),
    "IncomeTaxExpenseBenefit": ("所得稅", "cost"),
    # 投資與資產
    "SegmentExpenditureAdditionToLongLivedAssets": ("資本支出", "capital"),
    "PaymentsToAcquirePropertyPlantAndEquipment": ("資本支出", "capital"),
    "DepreciationDepletionAndAmortization": ("折舊攤銷", "capital"),
    "DepreciationAmortizationAndAccretionNet": ("折舊攤銷", "capital"),
    "PropertyPlantAndEquipmentNet": ("不動產廠房設備", "capital"),
    "Assets": ("資產", "capital"),
    "Goodwill": ("商譽", "capital"),
    # 其他損益
    "OtherNonoperatingIncomeExpense": ("業外損益", "other"),
    "NonoperatingIncomeExpense": ("業外損益", "other"),
    "SegmentReportingOtherItemAmount": ("其他項目", "other"),
    "ShareBasedCompensation": ("股權薪酬", "cost"),
}
KIND_LABELS = {"revenue": "營收", "profit": "利潤", "cost": "成本與費用",
               "capital": "投資與資產", "other": "其他"}


def _item_meta(concept: str) -> tuple[str, str]:
    """(中文標籤, 分類)。沒收錄的概念歸「其他」並沿用申報上的原始標籤。"""
    if concept in REVENUE_CONCEPTS:
        return "營收", "revenue"
    if concept in PROFIT_RANK:
        return PROFIT_LABEL[concept], "profit"
    return CONCEPT_LABELS.get(concept, ("", "other"))


def _c(concept: str) -> str:
    return (concept or "").split("_")[-1]


def _axis_key(name: str) -> str:
    """跨年度合併同一張報表的鍵。

    同一張表在不同年度的申報裡寫法會飄：大小寫不同（NET REVENUE vs Net Revenue）、
    結尾 (Detail) 與 (Details) 單複數不同。不正規化就會裂成兩個維度，舊的那個
    因為期數多而排到前面，「最新一期」就停在去年（TSM 卡在 2024 就是這樣）。
    """
    k = " ".join((name or "").split()).lower()
    k = re.sub(r"\s*\(detail(s)?\)\s*$", "", k)
    return k.strip(" -–—")


def _seg_name(raw: str) -> str:
    """R-file 的部門標籤常帶軸的後綴：「Americas | Operating segments」。

    DB 存原文（忠於申報），顯示時才裁掉後綴——這樣之後想追軸的名稱還追得回去。
    """
    name = (raw or "").split("|")[0].strip()
    return name or raw


# 「看起來像部門、其實是對帳科目」的列。IBM 與 MU 的分部門表裡，帶利潤的那張
# 拆出來是 Cost of goods sold / Unallocated / Operating Segments，對投資判斷毫無用處；
# 用原本的 has_profit + 部門數排序會把這種表排到第一個，預設就看到垃圾。
_JUNK_SEG = re.compile(
    r"^(operating segments?|unallocated.*|corporate.*|other|others|eliminations.*|"
    r"reportable segment|segment reporting.*|total.*)$", re.I)
_EXPENSE_SEG = re.compile(
    r"(cost of|research and development|selling, general|amortization|depreciation|"
    r"restructuring|capital expenditure|income tax|interest expense)", re.I)


# 名稱本身就說明是彙總的列。只列「小計」語意的字眼——Corporate / Unallocated /
# Other 不能放進來，那些是真的對帳組成項（AAPL 的 Corporate non-segment −14.0B
# 要算進去才會等於合併數），拿掉反而對不上。
_SUBTOTAL_SEG = re.compile(
    r"^(operating segments?|reportable segments?|segment total|total.*)$", re.I)


def _is_real_segment(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and n != "（合併）" and not _JUNK_SEG.match(n) and not _EXPENSE_SEG.search(n)


def _mark_totals(segs: list[dict]) -> None:
    """標出「其實是小計」的列。

    一張 R-file 常把總計列跟真正的部門並排：ARM 的「Reportable Segment」等於
    License + Royalty 兩條之和，JBL 的「Operating Segments」等於三個事業部之和。
    這種列的營收會等於合併數，直接加總就會重複計算一次。
    """
    total = next((s.get("revenue") for s in segs if s["segment"] == "（合併）"), None)
    for s in segs:
        s["is_total"] = bool(
            s["segment"] == "（合併）"
            or _SUBTOTAL_SEG.match(s["segment"].strip())
            or (total and s.get("revenue") and abs(s["revenue"] - total) / total < 0.01))


def _reconcile(segs: list[dict]) -> dict:
    """部門加總 vs 合併數。

    對不上不代表抓錯——同一張表裡常同時有好幾種互相重疊的拆法（V 這張同時有
    地區別與收入類型別，兩種各自都等於總額；DHI 同時列父層與子層），或者這張
    表本來就只揭露部分產品線（ABBV）。所以照實把差距攤開讓人判斷，不硬選一種。
    """
    total = next((s.get("revenue") for s in segs if s["segment"] == "（合併）"), None)
    parts = [s["revenue"] for s in segs
             if not s.get("is_total") and s.get("revenue") is not None]
    if not total or not parts:
        return {"total": total, "sum_of_segments": sum(parts) if parts else None,
                "reconciles": None}
    ssum = sum(parts)
    diff = abs(ssum - total) / total * 100
    return {"total": total, "sum_of_segments": ssum,
            "diff_pct": round(diff, 1), "reconciles": diff < 1.5}


def _merge_by_segments(axes: list[dict]) -> list[dict]:
    """把「同一種拆法、只是報表改過名字」的維度併起來。

    公司會改報表標題：AAPL 的產品線拆解先後叫過
      Revenue Recognition - Net Sales Disaggregated by Significant Products…（到 2021）
      Revenue - Net Sales Disaggregated by Significant Products…（到 2024）
      Revenue - Disaggregated Net Sales and Portion of Net Sales…（現在）
    `_axis_key` 只正規化大小寫與 (Detail)/(Details)，接不起這種改名，於是同一種
    拆法裂成三個維度、其中兩個停在兩三年前——點進去就像「資料是空的」。

    改用部門名稱集合判斷：名稱重疊度夠高（Jaccard ≥ 0.6）就是同一種拆法。
    地區別與產品別的名稱完全不重疊，不會被誤併。
    """
    def names(a: dict) -> set[str]:
        return {s["segment"] for p in a["periods"] for s in p["segments"]
                if s["segment"] != "（合併）" and not s.get("is_total")}

    merged: list[dict] = []
    for axis in axes:
        na = names(axis)
        target = None
        for m in merged:
            nb = names(m)
            if not na or not nb:
                continue
            overlap = len(na & nb) / len(na | nb)
            if overlap >= 0.6:
                target = m
                break
        if target is None:
            merged.append(axis)
            continue
        # 併進去：同一期間以先到的為準（axes 已照期間新到舊，先到的是較新的報表版本）
        have = {p["period_end"] for p in target["periods"]}
        target["periods"] += [p for p in axis["periods"] if p["period_end"] not in have]
        target["periods"].sort(key=lambda p: p["period_end"], reverse=True)
        target["real_segment_count"] = max(target["real_segment_count"], axis["real_segment_count"])
        target["has_profit"] = target["has_profit"] or axis["has_profit"]
        target.setdefault("merged_from", []).append(axis["report_name"])
    return merged


def _rows(sql: str, args: tuple) -> list[dict]:
    if not DB.exists():
        return []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _build(ticker: str, months: int, currency: str | None = None) -> list[dict]:
    """→ [{report_name, periods:[{period_end, segments:[…]}]}]，維度分開不混。"""
    rows = _rows(
        "SELECT report_name, period_end, segment, line_item, concept, value, scale, "
        "       COALESCE(currency,'') AS currency, form "
        "FROM segment_financials WHERE ticker=? AND period_months=? "
        "ORDER BY period_end DESC, report_name, segment",
        (ticker.upper(), months))

    # 雙幣別申報（TSM/ASX 同時報 TWD 與 USD 折算）：同一期間同一部門會有兩個值，
    # 混在一起數字會差 30 倍。
    #
    # 預設取「主要申報幣別」＝有標幣別的列裡數量最多的那個，不是無腦挑 USD：
    # 有些表根本沒標幣別，那些列其實是申報幣別。先前直接挑 USD 再把未標的一起
    # 留下，等於把台幣的值掛上 USD 的名字（TSM 的 2,894B 被說成美元）。
    from collections import Counter
    counts = Counter(r["currency"] for r in rows if r["currency"])
    available = sorted(counts)
    if counts:
        dominant = counts.most_common(1)[0][0]
        pick = (currency or dominant).upper()
        # 只有選的就是申報幣別時，未標幣別的列才能算進來
        rows = [r for r in rows
                if r["currency"] == pick or (r["currency"] == "" and pick == dominant)]
        report_ccy = pick
    else:
        report_ccy = ""

    # 同一張報表在不同年度的申報裡大小寫可能不同（TSM 的 FY2025 是
    # "NET REVENUE - …"、FY2024 是 "Net Revenue - …"），照原字串分組會裂成兩個
    # 維度，舊的那個部門數還比較多而排到前面，於是「最新一期」停在去年。
    axes: dict[str, dict[str, dict[str, dict]]] = {}
    display: dict[str, str] = {}
    for r in rows:
        key = _axis_key(r["report_name"])
        display.setdefault(key, r["report_name"])   # rows 已照期間新到舊，取最新的寫法
        seg = (axes.setdefault(key, {})
                   .setdefault(r["period_end"], {})
                   .setdefault(_seg_name(r["segment"]),
                               {"segment": _seg_name(r["segment"]), "items": {}}))
        c = _c(r["concept"])
        val = r["value"] * (r["scale"] or 1)
        seg["items"].setdefault(c, {"label": r["line_item"], "value": val})
        if c in REVENUE_CONCEPTS:
            seg.setdefault("revenue", val)
        if c in PROFIT_RANK:
            # 同一個部門可能同時有毛利與營業利益，留排序最前面的那個
            cur = seg.get("_profit_rank")
            if cur is None or PROFIT_RANK[c] < cur:
                seg["_profit_rank"] = PROFIT_RANK[c]
                seg["profit"] = val
                seg["profit_measure"] = PROFIT_LABEL[c]
                seg["profit_concept"] = c

    out = []
    for key, periods in axes.items():
        report = display.get(key, key)
        plist = []
        for pend in sorted(periods, reverse=True):
            segs = []
            for s in periods[pend].values():
                s.pop("_profit_rank", None)
                if s.get("revenue") and s.get("profit") is not None:
                    s["margin_pct"] = round(s["profit"] / s["revenue"] * 100, 1)
                items = []
                for k, v in s["items"].items():
                    label, kind = _item_meta(k)
                    items.append({"concept": k, "kind": kind,
                                  # 三段式：本檔的分類標籤 → 共用 XBRL 中文對照 →
                                  # 申報原文。三段都是為了避免硬翻出錯誤科目名
                                  "label": label or zh(k, v["label"]),
                                  "raw_label": v["label"], "value": v["value"]})
                order = {"revenue": 0, "profit": 1, "cost": 2, "capital": 3, "other": 4}
                items.sort(key=lambda d: (order.get(d["kind"], 9), d["label"]))
                s["items"] = items
                segs.append(s)
            _mark_totals(segs)
            plist.append({"period_end": pend,
                          # 日曆季讓不同會計年度的公司對得齊；會計季放提示用
                          "label": calendar_label(pend, months),
                          "fiscal_label": fiscal_label(
                              pend, fiscal_year_end_month(str(DB), ticker.upper()), months),
                          "segments": segs,
                          "reconciliation": _reconcile(segs)})
        named = [s for p in plist for s in p["segments"]
                 if s["segment"] != "（合併）" and not s.get("is_total")]
        real = [s for s in named if _is_real_segment(s["segment"])]
        out.append({
            "report_name": report,
            "segment_count": len({s["segment"] for s in named}),
            # 排序只看「真部門」：對帳科目不算數
            "real_segment_count": len({s["segment"] for s in real}),
            "has_profit": any(s.get("profit") is not None for s in real),
            "reconciles": plist[0]["reconciliation"].get("reconciles") if plist else None,
            "periods": plist,
        })
    # 排序：至少要有兩個真部門（只有一個等於沒拆）、能對上合併數的優先、
    # 其次有利潤、再其次真部門多。
    out = _merge_by_segments(out)
    out.sort(key=lambda a: (a["real_segment_count"] < 2, a["reconciles"] is not True,
                            not a["has_profit"], -a["real_segment_count"]))
    pinned = AXIS_PINS.get(ticker.upper())
    if pinned:
        # 人工釘選：純靠規則排不出「這家公司該看哪種拆法」時（LLY 看藥品別比
        # 看地區別有用得多），照這裡的關鍵字把該張拉到第一個
        out.sort(key=lambda a: pinned.lower() not in a["report_name"].lower())
    for a in out:
        a["currency"] = report_ccy
        a["available_currencies"] = available
    return out


@router.get("/{ticker}")
def segments(ticker: str, months: int = 3, currency: str | None = None):
    axes = _build(ticker, months, currency)
    if not axes:
        return {"ticker": ticker.upper(), "months": months, "axes": [],
                "note": "沒有這個月數的資料；外國發行人（20-F）通常只有 months=12"}
    return {"ticker": ticker.upper(), "months": months,
            "currency": axes[0].get("currency", ""),
            "available_currencies": axes[0].get("available_currencies", []),
            "axes": axes}


@router.get("/{ticker}/latest")
def latest(ticker: str, months: int = 3, report: str | None = None,
           currency: str | None = None):
    """最新一期各部門營收/利潤 + YoY。

    去年同期是「往回找期間結束日差 300~430 天的那一期」，不是「上一筆」——
    同一份申報同時帶本季與去年同季，取上一筆會拿到相鄰季度，YoY 就成了 QoQ。
    """
    axes = _build(ticker, months, currency)
    if not axes:
        raise HTTPException(404, f"no segment data for {ticker.upper()} (months={months})")
    axis = next((a for a in axes if a["report_name"] == report), axes[0])
    periods = axis["periods"]
    cur = periods[0]
    cur_d = date.fromisoformat(cur["period_end"])
    prior = next((p for p in periods[1:]
                  if 300 <= (cur_d - date.fromisoformat(p["period_end"])).days <= 430), None)

    prev_map = {s["segment"]: s for s in (prior["segments"] if prior else [])}
    reconciliation = cur.get("reconciliation")
    segs = []
    for s in cur["segments"]:
        p = prev_map.get(s["segment"])
        d = dict(s)                       # items 也留著：前端要能展開看完整科目
        for k in ("revenue", "profit"):
            d[f"{k}_prior"] = p.get(k) if p else None
            if p and s.get(k) is not None and p.get(k):
                d[f"{k}_yoy_pct"] = round((s[k] - p[k]) / abs(p[k]) * 100, 1)
        segs.append(d)
    return {"ticker": ticker.upper(), "months": months,
            "currency": axis.get("currency", ""),
            "available_currencies": axis.get("available_currencies", []),
            "report_name": axis["report_name"],
            "available_reports": [a["report_name"] for a in axes],
            "period_end": cur["period_end"],
            "prior_period_end": prior["period_end"] if prior else None,
            "reconciliation": reconciliation,
            "segments": segs}
