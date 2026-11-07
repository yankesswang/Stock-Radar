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

## 個股頁標頭與分頁列

`/stock/:ticker` 底下的主頁與 11 個子頁共用 `components/StockPageHeader.jsx`：麵包屑、
代號、估值狀態、公司名、產業／市值、現價，加兩層分頁列（群 → 切面）。分頁定義在
`components/stockTabsDef.js`（純資料，依「總覽 → 估值 → 基本面 → 市場 → 觀點」分群），
active 由網址推出（`activeStockTabKey`），不用各頁自己傳。

- 新增個股切面：在 `stockTabsDef.js` 加一筆（`path` 接在 `/stock/:ticker` 後面、
  `prefetch` 指向同一個 lazy 模組），頁面開頭放 `<StockPageHeader ticker={ticker} />`，
  頁面專屬的資料日／筆數放 `meta`、右側控制項放 `actions`、標題旁 badge 放 `chips`。
- 載入中／出錯用 `StockPageFallback`，標頭與分頁列照常在，只有內容區換成訊息。
- 標頭資料走 `useStockHeader`（模組層快取），切分頁時不會閃；主頁自己抓了估值歷史
  與現價，用 `info` / `price` 傳進去不再多打。
- 「三大財報」（`/financials`，SEC 三大表＋Non-GAAP＋補充欄位）與「營運分項」
  （`/metrics`，stockanalysis 爬的分項拆解＋SEC 分部門摘要）是兩個路由；舊的
  `/metrics?tab=income` 書籤由 StockMetrics 轉到 `/financials?tab=income`。
- 「新聞」是主頁區塊錨點（`#news`），不是路由；子頁點它會回主頁再捲過去。
- `GET /api/stocks/{t}/tabs`（`routers/tabs.py`）回每個切面有沒有資料與一句話結論
  （DCF／PE／分析師／同業四個合理價、AI 立場、財報筆記幾季）。分頁列靠它灰化與加
  角標，主頁的總覽列（`components/StockConclusions.jsx`）靠它畫格子。它只看檔案與
  DB，不觸發爬蟲、DCF 重算或 AI 生成；前端走 `useStockTabs` 五分鐘快取，AI 重新
  產生後要叫 `invalidateStockTabs`。

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
| 問句點名族群 | 那個產業分組（DB `industries`） | 成分股漲跌排行＋族群新聞（「資安股為什麼跌」） |

個股頁也能問大盤：`classify_intent` 看問句主詞 —— 只講大盤/總經/族群就**不撈這檔**
（`market`），同時拉回這檔（點名代號、「這檔」「它」）就兩邊都撈（`both`），
都沒有才是原本的逐檔問答（`stock`）。「毛利率」「黃金交叉」這類含大盤字眼的個股詞
由 `_NOT_MARKET_RE` 先濾掉；在個股頁只說「族群」指的是這檔所屬的那組。

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
uv run python scripts/scrape_fullratio_pe.py --all --missing --workers 2   # 只補還沒有 PE 歷史檔的
```

**PE 資料缺漏的三種來源**（2026-09 查過一輪）：

- `metrics.pe` 空：ratios 頁只取「Current」欄，歷年全虧、只有 TTM 轉正的公司（BE、MDB、
  SYM…）那張表整列 `PE Ratio` 不出現；EPS／淨利／營收也根本不在那張表。現在
  `save_statistics` 存完會用 statistics（id 與 `RATIO_KEY_MAP` 同一套）補 metrics 最新列的
  空欄，只補 None。`src.db_service.backfill_metrics_from_statistics()` 可全庫重補。
  補完仍空的＝`eps < 0` 真虧損，不是漏抓。
- PE 歷史分頁空：它讀 `data/pe/fullratio/*.json`，不讀 DB。每週排程以前只存中位數、
  把歷史丟掉，所以要 `write_json`。沒有歷史列（yfinance 後備）就不寫檔。
  fullratio 本身沒資料的：從未獲利的（ACHR、ASTS…）、REIT（看 P/FFO）、ETF。
- 從 `STOCK_LIST` 拿掉的代號（多半被併購下市）留在 DB 裡、快照停在拿掉那天，
  排程不會再更新它們。

**趨勢線：門檻要用絕對尺度，不要用「佔全圖比例」**（`backend/app/trendlines.py`，2026-09-23 修）。
原本 pivot 窗格、min_span、span_score、recency 全都跟 n 成正比，結果圖拉得越長對近期結構
越瞎：AMZN 5 年日線 w=14，8/28 與 9/21 兩個較低高點連 pivot 都不算，那條兩個月的下降壓力線
**根本沒被當成候選**；就算生得出來，34 根的跨度在 5 年圖上 span_score 只有 0.06（1 年圖上是
0.30），三個名額會被 10~18% 之外的長期上升線佔滿。前端預設就是 5y，所以使用者看到的正是
最瞎的那一張。現在：

- `multi_scale_pivots()` = 結構窗格 ∪ 最近半年的小窗格（`SWING_W=5`）pivot。
- `min_span` 封頂 25 根；`span_score` 以 `ppy*0.5` 飽和；`recency` 以 `ppy*0.35` 衰減（週線月線同樣適用）。
- 選線保留一個名額給離現價最近的線 —— 純照分數排會讓「價格正踩著的那條」被擠掉（實測 40 張圖有 16 張如此）。

**錨點不要只准用 pivot**（同一輪修的第二件事）。候選線只從「pivot 兩兩配對」產生，而
pivot 是某個窗格下的局部極值 —— 一段急跌裡真正撐住線的那幾根往往不是極值。結果畫出來的
是「頭尾相連的弦」：AMZN 8/3→9/21 那條只碰到兩個端點，中間三個高點分別在線下方 11.0、
4.2、6.5 元。窮舉所有 K 棒配對找得到平均浮空 5.17 的線，錨點 8/10 與 8/28 兩根都不是 pivot。

`_fit_side` 內的 `_refine()`：選完線之後，把兩個錨點各往旁邊挪幾根做局部搜尋
（座標下降，不是 (2r+1)² 網格 —— 目標函數夠平滑，評估次數 169→39，5 年圖 90ms→41ms）。
只在「觸及更多、或同樣觸及但更貼」時才換，而且**不准拿已破的線換掉未破的**。
候選評估抽成 `_evaluate(a, b)` 給兩邊共用，新增 `float_atr` 欄位量貼合度。

全域窮舉是 O(n²)（5 年圖 150 萬組）所以不可行；局部搜尋成本固定，而且只對「選中的」
那幾條做 —— refine 改的是「線畫在哪」不是「哪條線重要」。regime 那條路傳 `refine=False`
（它只讀 bounce，不需要精確錨點）。

量測（40 張圖）：線浮在高/低點上方的平均距離 4.71→3.00 ATR、每條線平均觸及 3.62→5.46 次。

**兩個不要踩的耦合**：

1. 形態辨識（`_alternating` → `_reversal_patterns`）**不要**吃多尺度 pivot。頭肩、雙頂是對某個
   尺度的主張，把 5 根的擺動跟 14 根的結構混成一條 zig-zag，認出來的形態不對應任何尺度
   （實測 20 張圖有 7 張形態整個換掉）。
2. `trend_regime` 的 `bounce_atr` 跟線的長度高度相關（700 根的線當然比 34 根彈得遠），所以它
   必須用固定的證據基礎算，不能跟著顯示用的選線政策跑。拿多尺度那份去餵，NVDA 五年線會被
   判成「不趨勢」（40→26 分），形態層跟著整個消失。現在 regime 另外跑一次結構尺度的 `_fit_side`。

代價是 5y 單次計算 14ms → 29ms（多一次 fit）。改完 40 張圖的量測：最近「未破」線距現價
1.97%→1.19%、同檔跨週期最大分歧 2.16%→0.56%。

**現價不要從 DB 直接讀**（2026-09-22 修）：`valuations.current_price` / `metrics.current_price`
都來自每週那輪比率表爬蟲抄的 stockanalysis「Last Close Price」，不是報價。爬蟲排在台北
凌晨、也就是美股盤中，所以那個價最好也落後一個交易日，最壞停一整週（查到的時候 AAPL
的 9/19–9/21 三筆快照都還掛著 9/3 的收盤，落後 12 個交易日；20 檔抽樣有 9 檔差超過 1%）。
`valuation_status` / `valuation_diff_pct` 是拿它跟合理價比出來的，價一舊高估／低估跟著錯。

- **日線收盤後好幾個小時才結算**（2026-09-23 查到的第二個坑）：當天那根 K 棒會帶著
  成交量、但 `close` 是 `null`/`NaN`。所以 `history(...)["Close"].dropna().iloc[-1]`、
  `yf.download` 之類「取最後一根非空日線」的寫法，在台北的早上拿到的都還是**前一天**
  的價。同一時間 `meta.regularMarketPrice` 已經是正確的當日收盤了。
  → 要現價就不要從日線推，看 `regularMarketPrice`。
- 報價一律走 `src/quotes.py`（`get_quotes` / `get_price`）：以 Yahoo chart 端點的
  `meta.regularMarketPrice` 為準，日線只用來取前一日收盤算漲跌；2 分鐘快取、`.TW` 換匯、
  失敗退避重試。chart 端點不走 cookie/crumb，所以不吃 yfinance 那套 `YFRateLimitError`
  限流（`.info`、`.history()`、`option_chain()` 都會被擋）。yfinance 只留作後備。
- **估值列的現價有兩個寫入者，兩個都要是即時價**：`refresh_prices.py`（每天 05:30 更新
  最新快照）以及 `src/db_service.recompute_all_valuations`（每日報告會呼叫，用「每檔最新的
  metrics/forecast/PE」重建**當天整批**估值列）。後者以前讀 `metric.current_price`，等於
  每天早上把前者寫好的正確價蓋回成週更爬蟲的舊價 —— 2026-09-23 查到 9/23 的快照是
  336.13（9/18 收盤），比 9/22 那筆 338.98 還舊，就是這條路徑。
- `scripts/refresh_prices.py` 每天 05:30 由 backend 排程跑，把每檔最新一筆估值快照的
  現價換成真收盤價並重算高估／低估。只動價格與價格衍生的四個欄位，不碰 EPS、
  PE 中位數、合理價，也不改歷史快照。抓不到報價的（下市、改名）保留原值不歸零。
- DCF 的現價也是即時報價，且快取檔的現價跟實際差超過 1% 就整份重算
  （`MAX_PRICE_DRIFT_PCT`）—— 上漲空間、三情境軸、反推估值彼此相關，補一半會自相矛盾。

```bash
uv run python scripts/refresh_prices.py --dry-run          # 只印會改什麼
curl -X POST localhost:6000/api/scheduler/prices/run       # 手動插隊
curl -s localhost:6000/api/scheduler/status | jq .prices
```

**分析師預估一樣是讀檔**：預估頁、DCF 輸入（`dcf_inputs.py`）、摘要、產業比較、
上修下修追蹤（`analyst_revisions.py`，靠每週一個檔排出時間序列）都讀
`data/forecast/detail/*.json`，不讀 DB 的 `forecasts` 表。每週排程以前只 `save_to_db`，
2026-09 查到 350 檔整頁空白（DB 其實有成長率）、其餘停在 7/8 月。補抓：

```bash
uv run python scripts/scrape_forecast.py --all --stale-days 7 --workers 2   # 沒檔或超過 7 天的
```

**PE 中位數合理價的防呆**（`src/db_service.compute_valuation`，`backend/load_data.py` 只是轉接）：

- 外國公司的分析師 EPS 是財報幣別、可能還是每股普通股。`eps_usd_factor` 乘上匯率 × ADS
  比例：幣別取預估 JSON 的 `financial_currency` → SEC 單位；ADS 比例在 {1, `ADS_SHARES[t]`}
  之間，用 stockanalysis 的美元 Forward PE 反推來選。新增 ADR 到清單要順手加 `ADS_SHARES`。
- 5 年中位 PE > 3 × Forward PE（GAAP 歷史對 non-GAAP 預估、剛轉盈），或今年度 EPS 跟
  Forward PE 反推的差兩倍以上（一次性損益）→ 不產生合理價。
- 算不出來存 `None`，不存 0（下游有人用 `is not None` 判斷）。

**財務比率頁的「Profitability」區**：stockanalysis 的 ratios 頁沒有利潤率（在損益表頁），
所以 `routers/ratios.py` 另外組這一區，每格依序取 損益表 CSV → SEC `financial_derived`
（只有 FY 欄）→ statistics（只補 Current）。只有 Current 有值＝那檔還沒有損益表 CSV，
也沒有 SEC 資料。清單加新股後要跑：

```bash
uv run python scripts/earnings/resolve_ciks.py                 # stocks 表 → stock_cik
uv run python scripts/earnings/fetch_financials.py --missing   # 只抓還沒有 SEC 三大表的
uv run python scripts/earnings/derive_metrics.py               # 重算 financial_derived
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
