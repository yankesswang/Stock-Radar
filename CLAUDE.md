# StockRadar — Claude Instructions

## Starting the App

Always use `./start.sh` from the project root to start both services:

```bash
./start.sh
```

- **Backend**: `http://localhost:6000` (FastAPI + uvicorn via `uv`)
- **Frontend**: `http://localhost:4000` (Vite + React)

Note: under Arthur.OS the whole stack is started by the root `start-all.sh` (owned by
launchd), and StockRadar is served through the gateway at `/stocks/`. Running this
`start.sh` standalone kills whatever holds :6000/:4000 and opens its own tunnel, so
don't run it while Arthur.OS is up.

## Testing Services

After any backend change, verify the service is working before telling the user it's done:

```bash
# Health check
curl -s http://localhost:6000/api/health

# Test a specific endpoint (replace AAPL / path as needed)
curl -s http://localhost:6000/api/stocks/AAPL/summary | python3 -m json.tool | head -40
```

If the backend isn't running, start it with:

```bash
uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 6000 --reload
```

## Project Structure

```
stock-radar/
├── start.sh                      # Launch both services
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI app + router registration
│   │   ├── config.py             # Settings (ports, paths, DB URL)
│   │   ├── routers/              # One file per feature area
│   │   └── models.py / schemas.py
│   └── stock_valuation.db        # SQLite DB
├── frontend/
│   ├── vite.config.js            # Proxy: /api → localhost:6000
│   └── src/
│       ├── pages/                # Full page components
│       └── components/           # Shared UI components
└── data/                         # Scraped JSON/CSV data files
    ├── statistics/               # {TICKER}_statistics_{date}.json
    ├── forecast/detail/          # {TICKER}_forecast_{date}.json
    ├── financials/               # {TICKER}_ratios_{date}.csv
    └── pe/fullratio/             # {TICKER}_fullratio_pe_{date}.json
```

## Key Conventions

- **uv** is the package/runtime manager — use `uv run python` instead of `.venv/bin/python`
- The `.venv` in this project may have a broken shebang path; always prefer `uv run`
- Backend runs from the **project root** (`backend.app.main:app`), not from inside `backend/`
- New routers must be imported and registered in `backend/app/main.py`
- Frontend proxy is set in `frontend/vite.config.js` — backend port is **6000** (override with `STOCKS_BACKEND_PORT`)
- Frontend port is **4000**, and Vite sets `base: '/stocks/'` — so the app lives at
  `http://localhost:4000/stocks/` even on a direct connection (bare `/` just redirects).
  Don't remove that base: it is what makes asset and HMR-websocket URLs carry the
  `/stocks/` prefix so the Arthur.OS gateway can route them by path. Without it the
  gateway falls back to guessing from Referer/cookie and, on a miss, serves the portal's
  `index.html` as `text/html` with a 200 — the page renders blank with no error in any log.
- Routing uses wouter with a runtime `base` set in `frontend/src/main.jsx`, matching the
  Vite base. New routes need no change; never hardcode `/stocks` into a `<Link>`.

## 觀點層（Obsidian 投資筆記）

`src/analysis/vault_notes.py` 把使用者手寫的投資筆記變成 pipeline 看得到的東西：
ticker → 我的論點（進 war_room / market_scan 簡報），近 60 天提到的個股 → Stage-1
預篩 universe。war_room 開場會自己 refresh，正常不用手動跑。

vault 在 `~/Documents`，iCloud 的 `dataless` 佔位檔背景行程讀不到（`OSError(11)`，
不是 TCC 問題），所以一律讀 `~/.arthur-digest/vault_notes/vault-mirror` 鏡像。
背景撈不到的會留在 pending 並顯示在完整性 manifest；在終端機補跑：

```bash
uv run python scripts/sync_vault_notes.py            # 鏡像 + 重建索引
uv run python scripts/sync_vault_notes.py --status   # 只看現況
uv run python scripts/sync_vault_notes.py --force    # 改了 parser 後全部重抽
```

細節見 `docs/FLOW.md` 的「第一部分之三：觀點層」。

## 投資問答（StockChat）

貼在畫面右緣正中的懸浮圓鈕（`components/StockChatDock.jsx`，設計沿用 FlightClaw 的
AI 助理）。在 App.jsx 只掛一次，**範圍由路由決定**：

| 在哪一頁 | 範圍 | 撈什麼 |
|---|---|---|
| `/stock/:ticker/*` | 那一檔 | 技術面 / 三大財報 / 分部門 / 同業 / 新聞 / 我的筆記與持倉 |
| 其他任何頁 | 大盤・總經 | 指數快照 / 利率匯率原物料 / 板塊 RS / 市場新聞 / 我的組合曝險 |
| 問句點名代號 | 那幾檔（上限 4） | 每檔各撈一份，prompt 裡照 ticker 分段 |

問過的對話收在 `/qa`（`pages/QaLog.jsx`），**以 session 為單位**收合：一段對話一張卡、
標題是第一個問題，展開才看完整往返（追問離開上下文讀不懂，拆成單題列反而找不回
當時在想什麼）。後端是 `routers/stock_chat.py`：

- **資料區塊路由**：`STOCK_SECTIONS`（逐檔）與 `MARKET_SECTIONS`（大盤）兩張
  「關鍵字 → 資料來源」表。問題提到「毛利率」才撈財報、提到「殖利率」才撈總經；
  逐檔區塊每一塊都是一次外部抓取，所以不命中就不撈，大盤預設組則一律帶。
  加新資料源＝加一個 `SectionDef`（builder 回一段 markdown 或 None）。
- **代號辨識**：問句裡的候選代號拿 DB `stocks` 表驗證（純正則會把 PE / CEO 當股票），
  所以「nvda 跟 tsm 比一比」會自己變成兩檔的範圍。
- **續問走 `claude --resume`**：第一問把資料包送進去，之後只送新問題與這輪才點到的
  新區塊，脈絡由 claude 自己保存（`chat_sessions.claude_session_id`）。
- **串流**：`GET /api/stock-chat/stream?q=&ticker=` 是 SSE（EventSource 只能 GET，
  所以問題走 query string）。額度狀態共用 `scripts/lib/claude_guard.py` 的 state。
- **紀錄**：`chat_sessions`（`ticker` 欄是代號或 `MARKET`）/ `chat_messages`（`tickers`
  欄存那一輪實際點名哪幾檔）。`/log` 一列回一整段對話，篩選作用在整段：命中任何
  一則就進來，用 `ticker=NVDA` 也撈得到「在大盤對話裡問過 NVDA」的那段。

```bash
curl -s -X POST localhost:6000/api/stock-chat/ask -H 'Content-Type: application/json' \
  -d '{"question":"NVDA 跟 AMD 誰的估值比較合理？"}'        # 不帶 ticker = 大盤範圍
```

## 掃描 universe（兩層）

Stage-1 預篩掃的不再只是手寫名單。`opportunity_scanner.build_universe()` 回
`(tickers, {ticker: tier})` 兩層：

| tier | 來源 | 檔數 |
|---|---|---|
| `curated` | `src/config.STOCK_LIST`（29 個主題桶 594 檔）∪ Mimi ∪ 近 60 天筆記 | ~660 |
| `discovery` | `src/analysis/discovery_universe.py` —— 全美股過三道門檻 | ~1,275 |

發現層在簡報標 `🔍未研究`、War Room 卡片標 `NEW NAME`，同分排在 curated 後面，
但 top N 有 25% 保障名額（`DISCOVERY_QUOTA`），否則會被滿分並列的 curated 整批擠掉。

```bash
uv run python scripts/refresh_discovery_universe.py --status   # 看門檻與漏斗
uv run python scripts/refresh_discovery_universe.py            # 立刻重建（~160s）
```

快取 `data/universe/discovery.json` 7 天過期後掃描會自己重抓，不用另外排程。

**加新股票到 `STOCK_LIST` 前先驗報價** —— 併購/改名/私有化很常見（檔案裡的註解記了
一串）。`yf.Ticker(t).history(period='1mo')` 空的就是死代號，但要用
`portfolio.chart._fetch_yahoo_chart_df` 再確認一次：yfinance 偶爾會對活著的股票回空。

**別用 `yf.Ticker().info` 做批次**：它走 crumb 端點，掃幾百檔就整個 session 被封
（`YFRateLimitError`），降到一秒一發也一樣。要市值/成交額/分析師覆蓋分別走 Nasdaq
screener、Yahoo v8 chart、stockanalysis.com（見 `discovery_universe.py` 模組說明）。

## Data Scrapers

Run scrapers from the project root:

```bash
uv run python scripts/scrape_fullratio_pe.py --ticker AAPL
uv run python scripts/scrape_ratios.py --ticker AAPL
```

損益表 / 現金流量表（`{T}_income_*.csv`、`{T}_cashflow_*.csv`）不要手動全量跑 ——
它們跟 statistics 一樣掛在 backend 的 rolling top-up 上（每天 04:00，一批 15 檔，
跑完順手重算那批的 DCF）。要插隊補抓或看新鮮度：

```bash
curl -X POST 'localhost:6000/api/scheduler/statements/run?limit=40'
curl -s localhost:6000/api/scheduler/status | jq .statements
```

這兩張表是 DCF 的常態化 FCF margin、利息還原、股數漂移、歷史 EV/FCF 退出倍數的
唯一來源；沒有的話模型會退回單點 spot 值，並在 `confidence` 裡扣分。方法論看
`docs/DCF_README.md`。

## 主題系統（亮 / 暗 / 跟隨系統）

做法與 alpha_note 一致（見 `alpha_note/server/static/theme.js`）：

| 偏好 | 行為 |
|------|------|
| `light` | **預設**。即使系統是暗色也維持亮色 —— 使用者的選擇要蓋過系統 |
| `dark` | 強制暗色 |
| `system` | 才跟隨 `prefers-color-scheme`，並即時反應系統切換 |

- 偏好存在 `localStorage['stockradar.theme']`；同時開多個分頁靠 `storage` 事件同步。
- 實際套用的是 `<html data-theme="light|dark">`。**CSS 只看這個屬性，不准直接寫
  `@media (prefers-color-scheme)`** —— 寫了就會蓋不掉使用者的選擇。Tailwind 的 `dark:`
  變體也綁在同一個屬性（`tailwind.config.js` 的 `darkMode: ['selector', '[data-theme="dark"]']`）。
- 防閃白：`frontend/index.html` 的 `<head>` inline 一段同樣的讀取邏輯，在任何 CSS/JS 之前套好。
  改 `src/theme.js` 的 key 或 resolve 規則時，index.html 那段要一起改。
- 切換鈕：`components/ThemeToggle.jsx`（Navbar 桌機在 Add 左邊、手機在漢堡鈕左邊），
  點一下循環「亮 → 暗 → 跟隨系統」。元件要讀主題用 `useTheme()`（`src/useTheme.js`）。

**暗色是「純新增覆寫」**：`index.css` 亮色規則沒改，暗色全部寫在檔尾的
`:root[data-theme="dark"]` 區塊，只重定義 token。所以亮色跟以前完全一樣，暗色出問題也只會
影響暗色。改暗色時請維持這個做法；元件裡新增顏色一律走 `var(--…)` token，不要寫死
`bg-white` / `text-[#0052ff]`（品牌藍在近黑底上只有 2.9:1，暗色下 `--accent-cyan` 會提亮成 `#4f83ff`）。

- K 線圖（lightweight-charts）畫在 canvas 上不吃 CSS 變數，`PriceChartPanel` 在建圖時讀
  `--chart-*` token、主題切換時再 `applyOptions` 一次。亮色下維持原本的深色圖面。
- War Room / 盤後復盤 / AttentionPositions 走 `components/opsTheme.js` 的獨立暗色終端機調色盤，
  **刻意不跟主題走**，兩種主題下都是深色。
