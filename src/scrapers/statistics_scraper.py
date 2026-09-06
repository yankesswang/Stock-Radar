"""
Scrape the /statistics/ page from stockanalysis.com for a given ticker.

The page contains ~104 key-value pairs grouped into ~19 sections
(Total Valuation, Valuation Ratios, Financial Efficiency, etc.).

Output: one JSON per ticker at
    data/statistics/{TICKER}_statistics_{YYYY-MM-DD}.json

Structure:
    {
        "ticker": "AAPL",
        "date": "2026-02-01",
        "sections": [
            {
                "name": "Total Valuation",
                "items": [
                    {"label": "Market Cap", "value": 3809462207200, "display": "3.81T"},
                    ...
                ]
            },
            ...
        ]
    }
"""

from src.utils import fetch_url, parse_html
from src.config import STOCK_LIST
from src import DATA_DIR
from src.db_service import save_statistics

import json
import time
import random
from datetime import datetime, date


class StatisticsScraper:
    BASE_URL = "https://stockanalysis.com/stocks/{ticker}/statistics/"
    # 同一頁的 SvelteKit 資料端點 —— 頁面自己就是吃這包 JSON 渲染出來的。
    # 比爬 HTML 好在三點：15KB vs 123KB（對方省 8 倍頻寬）、快 4 成、
    # 而且每個欄位有穩定的 id（grossMargin / revenue3y / zScore），
    # 不是會被改名的英文標題 —— 2026 年中「Revenue Growth Forecast (5Y)」
    # 改名成「(3Y)」就讓整欄資料在我們這邊斷掉過一次。
    #
    # 它不是有合約的公開 API，隨時可能變 —— 所以解析失敗就退回 HTML。
    DATA_URL = "https://stockanalysis.com/stocks/{ticker}/statistics/__data.json"

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.output_dir = DATA_DIR / "statistics"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── value parsing ────────────────────────────────────────────────

    @staticmethod
    def parse_value(text):
        """Convert a display string to a Python float/str.
        Returns (parsed_value, display_string)."""
        if not text or text in ("n/a", "-", "\u2014", ""):
            return None, text or "—"
        if "Upgrade" in text or "PRO" in text:
            return None, "—"

        display = text.strip()
        cleaned = text.replace("$", "").replace(",", "").strip()

        try:
            if "%" in cleaned:
                return float(cleaned.replace("%", "")), display

            multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
            for suffix, mul in multipliers.items():
                if cleaned.endswith(suffix):
                    return float(cleaned[:-1]) * mul, display

            return float(cleaned), display
        except (ValueError, AttributeError):
            return text, display

    # ── page scrape ──────────────────────────────────────────────────

    # ── SvelteKit flat payload ───────────────────────────────────────
    # nodes[].data 是一個扁平陣列，物件裡的整數是指回同一個陣列的索引。
    # 這樣壓縮可以共用重複字串，代價是要自己還原。

    @staticmethod
    def _hydrate(arr, idx, depth=0):
        if depth > 12 or not isinstance(idx, int) or idx >= len(arr):
            return None
        v = arr[idx]
        if isinstance(v, list):
            return [StatisticsScraper._hydrate(arr, i, depth + 1) if isinstance(i, int) else i for i in v]
        if isinstance(v, dict):
            return {k: (StatisticsScraper._hydrate(arr, i, depth + 1) if isinstance(i, int) else i)
                    for k, i in v.items()}
        return v

    # JSON 的 key → HTML 版的區塊名。沿用 HTML 的名字，資料庫與下游
    # （backend/app/routers/compare.py 的指標目錄）才不用跟著改。
    SECTION_NAMES = {
        "valuation": "Total Valuation",
        "dates": "Important Dates",
        "shares": "Share Statistics",
        "ratios": "Valuation Ratios",
        "evRatios": "Enterprise Valuation",
        "financialPosition": "Financial Position",
        "financialEfficiency": "Financial Efficiency",
        "taxes": "Taxes",
        "stockPrice": "Stock Price Statistics",
        "shortSelling": "Short Selling Information",
        "incomeStatement": "Income Statement",
        "balanceSheet": "Balance Sheet",
        "cashFlow": "Cash Flow",
        "margins": "Margins",
        "dividends": "Dividends & Yields",
        "analystForecasts": "Analyst Forecast",
        "stockSplits": "Stock Splits",
        "scores": "Scores",
        "fairValue": "Fair Value",
    }

    def scrape_json(self, ticker, _redirects=0):
        """從頁面自己的資料端點取統計數字。失敗回 None，呼叫端會退回 HTML。"""
        url = self.DATA_URL.format(ticker=ticker.lower())
        response = fetch_url(url, self.headers)
        if not response:
            return None
        try:
            payload = response.json()
            # 代號改過的（SQ→XYZ、PSTG→P）會回 {"type":"redirect","location":...}。
            # requests 跟不到，因為 200 不是 3xx —— 自己跟一次。
            if payload.get("type") == "redirect" and _redirects < 2:
                loc = (payload.get("location") or "").strip("/")
                new = loc.split("/")[1] if loc.startswith("stocks/") else None
                if new and new != ticker.lower():
                    print(f"  {ticker.upper()} → {new.upper()}（來源已改代號）")
                    return self.scrape_json(new, _redirects + 1)
                return None
            # 統計資料在最後一個 data node（前面幾個是 session/layout）
            arr = None
            for node in reversed(payload.get("nodes", [])):
                if node.get("type") == "data" and isinstance(node.get("data"), list):
                    arr = node["data"]
                    break
            if not arr:
                return None
            root = self._hydrate(arr, 0)
            if not isinstance(root, dict):
                return None
        except (ValueError, KeyError, TypeError) as exc:
            print(f"  {ticker}: __data.json 解析失敗（{exc}），退回 HTML")
            return None

        sections = []
        for key, block in root.items():
            if not isinstance(block, dict) or not isinstance(block.get("data"), list):
                continue
            items = []
            for it in block["data"]:
                if not isinstance(it, dict):
                    continue
                label = it.get("title")
                if not label:
                    continue
                # hover 是全精度（48.653%），value 是顯示用的四捨五入（48.65%）
                raw = it.get("hover") or it.get("value")
                parsed, display = self.parse_value(raw)
                items.append({
                    "id": it.get("id"),
                    "label": label,
                    "value": parsed,
                    "display": display,
                })
            if items:
                sections.append({"name": self.SECTION_NAMES.get(key, key), "items": items})

        if not sections:
            return None
        total = sum(len(s["items"]) for s in sections)
        print(f"  {ticker.upper()}: {total} items across {len(sections)} sections (json)")
        return {"ticker": ticker.upper(), "date": self.current_date, "sections": sections}

    def scrape_statistics(self, ticker):
        """Scrape all statistics for *ticker*, returning structured dict.

        先走頁面的 JSON 端點，解析不出來才退回爬 HTML —— 那個端點沒有相容性
        承諾，改版時我們要還能動。
        """
        data = self.scrape_json(ticker)
        if data:
            return data

        url = self.BASE_URL.format(ticker=ticker.lower())
        print(f"  Fetching {url}")
        response = fetch_url(url, self.headers)

        if not response:
            print(f"  Failed to fetch statistics for {ticker}")
            return None

        soup = parse_html(response.text)

        # Strategy: iterate all <table> elements.  For each table, the
        # nearest preceding <h2> is the section name.  Each <tr> with
        # two <td> cells is a label/value pair.
        tables = soup.find_all("table")
        if not tables:
            print(f"  No tables found for {ticker}")
            return None

        sections = []
        seen_sections = set()

        for table in tables:
            heading = table.find_previous("h2")
            section_name = heading.get_text(strip=True) if heading else "Other"

            items = []
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True)
                if not label:
                    continue

                raw = cells[-1].get("title") or cells[-1].get_text(strip=True)
                parsed, display = self.parse_value(raw)

                items.append({
                    "label": label,
                    "value": parsed,
                    "display": display,
                })

            if items:
                # Multiple tables can share the same heading; merge them
                if section_name in seen_sections:
                    for sec in sections:
                        if sec["name"] == section_name:
                            sec["items"].extend(items)
                            break
                else:
                    sections.append({"name": section_name, "items": items})
                    seen_sections.add(section_name)

        total_items = sum(len(s["items"]) for s in sections)
        print(f"  {ticker.upper()}: {total_items} items across {len(sections)} sections")

        return {
            "ticker": ticker.upper(),
            "date": self.current_date,
            "sections": sections,
        }

    # ── persistence ──────────────────────────────────────────────────

    def save(self, ticker, data):
        """Save as dated JSON and to database, return file path."""
        path = self.output_dir / f"{ticker.upper()}_statistics_{self.current_date}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Saved → {path}")
        
        # Save to database
        snapshot_date = date.fromisoformat(self.current_date)
        save_statistics(ticker, data, snapshot_date)
        
        return str(path)

    # ── batch run ────────────────────────────────────────────────────

    def run(self, tickers=None):
        """Scrape statistics for every ticker in *tickers* (or STOCK_LIST)."""
        if tickers is None:
            tickers = [t for companies in STOCK_LIST.values() for t in companies]

        results = {}
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {ticker}")
            data = self.scrape_statistics(ticker)
            if data:
                path = self.save(ticker, data)
                results[ticker] = path

            if i < len(tickers):
                delay = random.uniform(10, 30)
                print(f"  Waiting {delay:.0f}s …")
                time.sleep(delay)

        print(f"\nDone – scraped {len(results)}/{len(tickers)} tickers")
        return results


# ── quick test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    scraper = StatisticsScraper()

    ticker = "aapl"
    data = scraper.scrape_statistics(ticker)

    if data:
        for sec in data["sections"]:
            print(f"\n{'─'*50}")
            print(f"  {sec['name']} ({len(sec['items'])} items)")
            for item in sec["items"][:4]:
                print(f"    {item['label']}: {item['display']}")
            if len(sec["items"]) > 4:
                print(f"    ... +{len(sec['items']) - 4} more")

        scraper.save(ticker, data)
    else:
        print("Scraping failed.")
