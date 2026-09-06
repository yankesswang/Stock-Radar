"""把期間結束日換成季度標記。

預設是**日曆季**（25Q4）：跨公司比較時，各家會計年度末不同會讓同一段時間
標成不同季別，沒辦法並排看。會計季（fiscal_label）保留著，放在滑鼠提示裡。

日曆季用「期間中點」判斷落在哪一季，不是用結束日：NVDA 的季度是 11 月到隔年
1 月，用結束日會標成 26Q1，跟同樣涵蓋 11-12 月的同業錯開一整格；用中點
（約 12 月中）標成 25Q4，才跟 TSLA、AAPL 的同期對得上。

以下是會計季的說明（fiscal_label）：

不能用日曆季：AAPL 的會計年度九月底結束，2025-12-27 那一季是 FY2026 的第一季，
用日曆看卻是「2025 年第四季」，差一個年度。NVDA 與 WDAY 一月結束，差得更多。

也不直接用 companyfacts 的 fy/fp 欄位：那兩個欄位講的是「這筆數字出現在哪一份
申報」，不是「這筆數字屬於哪一季」。季報裡帶的去年同期比較數，fy/fp 會標成當期
的年度與季別，照抄會把去年的數字標成今年。

所以照公司自己的會計年度末推算：先從 fp='FY' 的期間結束日取月份眾數當年度末，
再看每一季的結束日離年度末幾個月。
"""
import sqlite3
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


@lru_cache(maxsize=512)
def fiscal_year_end_month(db: str, ticker: str) -> int | None:
    """公司的會計年度結束月份。取 fp='FY' 期間結束日的月份眾數——
    52/53 週制的公司年度末會在月底前後飄，眾數比單看最新一年穩。"""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        months = [int(p[5:7]) for (p,) in con.execute(
            "SELECT DISTINCT period_end FROM financial_facts "
            "WHERE ticker=? AND period_months=12 AND fp='FY'", (ticker,))]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return Counter(months).most_common(1)[0][0] if months else None


def fiscal_label(period_end: str, fye_month: int | None, months: int = 3) -> str:
    """→ '26Q3'（季）或 'FY26'（年）。推不出年度末就退回日期本身，不亂猜。"""
    if not fye_month or not period_end:
        return period_end or ""
    try:
        d = date.fromisoformat(period_end)
    except ValueError:
        return period_end

    # 會計年度以「結束的那個日曆年」命名（Apple 的 FY2025 結束於 2025 年 9 月）。
    # 判斷方式：這一季的月份離當年年度末還有幾個月；已經超過年度末一個月以上，
    # 就是下一個年度。容忍 −1 是給 52/53 週制的公司——它們的年度末會在月底
    # 前後飄，2025-10-01 這種日期仍屬於 9 月結束的那個年度。
    anchor = date(d.year, fye_month, 15)
    fy = d.year if _months_between(d.replace(day=15), anchor) >= -1 else d.year + 1
    if months == 12:
        return f"FY{fy % 100:02d}"

    fy_end = date(fy, fye_month, 15)          # 15 號當代表日，避開 52/53 週的日期飄移
    # 離年度末還有幾個月 → 第幾季。0~1 個月是第四季，往前每三個月退一季
    gap = _months_between(d.replace(day=15), fy_end)
    q = 4 - min(3, max(0, round(gap / 3)))
    return f"{fy % 100:02d}Q{q}"


def calendar_label(period_end: str, months: int = 3) -> str:
    """→ '25Q4'（季）或 '2025'（年）。以期間中點落在哪個日曆季為準。

    中點而不是結束日：11 月到隔年 1 月那種季度，結束日在 1 月會被標成下一年的
    Q1，但它涵蓋的其實是上一年的 Q4。中點讓不同會計年度的公司對得齊。
    """
    if not period_end:
        return ""
    try:
        d = date.fromisoformat(period_end)
    except ValueError:
        return period_end
    # 往回推半個期間長度當中點。用 30.44 天（平均月長）避免月底日期溢位
    mid = d - timedelta(days=int(months * 30.44 / 2))
    if months >= 12:
        return str(mid.year)
    return f"{mid.year % 100:02d}Q{(mid.month - 1) // 3 + 1}"
