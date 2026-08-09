# earnings/ — 財報筆記自動化

財報公布後自動生成分析師級筆記到 Obsidian（`投資/財報筆記/`）。
工作流程定義在 `~/.claude/skills/earnings-note/SKILL.md`（trigger `/earnings-note TICKER`）。

## 元件

| 檔案 | 職責 |
|---|---|
| `registry.py` | 個股情報資料表（`data/earnings_registry.db`）：companies（CIK/IR 模板/fiscal 規則/逐字稿來源/ticker 對映）＋ earnings_events（每季文件 URL/報告日/股價反應/筆記路徑）。`show / upsert / record` |
| `fetch_ir_docs.py` | EDGAR 8-K 新聞稿＋IR 官方 PDF 抓取轉檔，讀寫 registry |
| `stock_context.py` | 估值/DCF/持倉/股價反應 JSON，自動處理 GOOGL→GOOG 對映；內含 `portfolio_context` 區塊 |
| `portfolio_context.py` | 部位脈絡：權重、排名、等權倍數、超配判定、產業集中度、調到各目標權重要買賣幾股與現金夠不夠。讀 `data/portfolio.csv`（CASH 用 avg_cost 當餘額、leverage 放大曝險、`.TW` 走 TWD=X 換匯），供筆記的操作結論分支使用 |
| `price_technicals.py` | 技術面 JSON＋日 K 線圖 PNG。JSON：均線排列與交叉、RSI/MACD/KDJ/威廉/OBV、布林、ATR 與停損寬度、52 週區間位置、支撐壓力（swing pivot 分群＋均線＋52 週極值＋未回補財報跳空缺口，含強中弱分級）、`signals` 指標訊號清單、財報後價格行為。圖：K 棒＋MA20/50/200＋布林＋支撐壓力線＋歷史財報標記（來自 registry 的 earnings_events），下接成交量、MACD、RSI 面板，`--panels` 可加 kdj/willr/obv。指標公式與前端 `backend/app/routers/chart.py` 對齊，訊號規則與 `PriceChartPanel.jsx` 的 Signal Alerts 對齊 |
| `earnings_calendar.py` | 行事曆（yfinance 下次財報日）＋公布偵測（EDGAR 8-K Item 2.02 確認）。`update / show / pending / watch` |
| `earnings_cron.sh` | 每日 10:30 由 launchd（`com.arthur.earnings-notes`）觸發：update → pending → 對每檔跑 `claude -p /earnings-note`。`DRY_RUN=1` 可測試 |
| `sync_staged.sh` | 把 headless 產出的筆記從 `~/.arthur-digest/earnings_notes/vault-staging/` 搬進 vault（launchd 因 TCC 寫不進 ~/Documents，要互動執行），並接著更新待閱讀清單 |
| `add_to_reading_list.py` | 新筆記加進 `待閱讀清單.md` 的「本週新增 → 投資」（附隔日股價反應）。跨週會自動開新區塊並把舊的降級成「上週」。冪等，`--sync-all` 可隨時補齊漏的 |

## 常用操作

```bash
python3 registry.py show                # 看全部個股情報
python3 portfolio_context.py MU         # 只看部位權重與加減碼情境
python3 price_technicals.py MU --outdir /tmp/t   # 技術面 JSON＋走勢圖
python3 earnings_calendar.py show      # 看行事曆
python3 earnings_calendar.py watch TSM # 加入監控
DRY_RUN=1 ./earnings_cron.sh           # 測試自動化不花錢
./sync_staged.sh                       # 同步 headless 筆記進 vault
tail -f ../../../logs/earnings_cron.log
```

## 機制

每日 10:30（台北）：行事曆更新 → EDGAR 確認「真的公布了」（8-K Item 2.02，
行事曆日期會變但 filing 不會騙人）→ 公布 2 到 6 天內且筆記未生成的 ticker →
`claude -p` headless 生成（每檔每次公布只試一次，state 檔在
`~/.arthur-digest/earnings_notes/state/`）。等 2 天 ≈ 美國時間隔日，逐字稿保證已出。
