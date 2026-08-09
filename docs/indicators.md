# 進階技術指標使用說明

**相關檔案：**
- 指標計算：`backend/app/indicators.py`（純 pandas/numpy，可被任何腳本 import）
- 圖表 API：`backend/app/routers/chart.py` → `GET /api/stocks/{ticker}/chart`
- 全市場掃描：`backend/app/routers/signals.py` → `GET /api/signals/watchlist`
- 前端圖表：`frontend/src/components/PriceChartPanel.jsx`
- 每日簡報：`scripts/daily_analysis.py`（波動狀態 → `risk_level`）

---

## 一、東西在哪裡

新增的指標散落在三個地方，用途不同：

| 介面 | 路徑 | 用途 |
|------|------|------|
| **個股圖表** | `/stock/<TICKER>` | 單一標的深入分析——疊圖、副圖、關鍵價位、技術評級、風險指標 |
| **訊號掃描** | `/signals` | 全市場（DB 內 269 檔）掃過一遍，找出當天觸發訊號的標的 |
| **每日簡報** | `scripts/daily_analysis.py` 自動跑 | 波動百分位餵給 LLM，影響持倉的 `risk_level` 判斷 |

---

## 二、圖表頁：疊圖與副圖

圖表上方有兩排 toggle：**疊圖**（畫在 K 線上）與**副圖**（獨立面板）。

預設只開 `支撐壓力`、`MACD`、`RSI`、`ADX/DMI`、`神奇九轉` —— **這是刻意的，不要全開**。14 個疊圖全開會讓 K 線圖完全看不見。

### 疊圖（14 項）

| 指標 | 參數 | 什麼時候用 |
|------|------|-----------|
| 支撐壓力 | swing+gap+量 | 永遠開著，抓進出場價位 |
| Bollinger Bands | (20,2) | 均值回歸、判斷波動收縮 |
| Keltner Channels | (20,2×ATR) | 搭配布林看擠壓（見 TTM Squeeze） |
| Donchian | (20) | 海龜式突破，20 日新高/新低 |
| EMA | 20/50/200 | 趨勢方向，比 SMA 反應快 |
| VWAP / Anchored | 20 + 擺動高低點 | **錨定 VWAP 是重點**：從波段高/低點起算的平均成本，看誰套牢 |
| Ichimoku | (9,26,52) | 一目均衡表，雲層上下緣是動態支撐壓力 |
| SuperTrend | (10,3) | ATR 移動停損，翻色就是趨勢反轉 |
| Parabolic SAR | (0.02,0.2) | 點狀停利/停損，趨勢末端加速時特別有用 |
| Chandelier Exit | (22,3) | 從最高點回撤 3×ATR 出場，抱波段用 |
| Pivot Points | 月 | 機構常用的當月樞紐 R3~S3 |
| Fibonacci | 自動抓波段 | 回檔目標位 |
| Volume Profile | POC / 價值區 | 籌碼分布，POC 是最大成交量價位 |
| 神奇九轉 | 低9 / 高9 | TD Setup 衰竭計數 |

### 副圖（18 項）

依用途分四組：

**動能／超買超賣** — MACD、RSI、Stochastic RSI、KDJ、CCI、Williams %R
Stoch RSI 比 RSI 敏感，轉折早；CCI ±100 是門檻。

**趨勢強度** — ADX/DMI、Aroon
ADX ≥ 25 才叫「有趨勢」，< 20 是盤整（此時所有趨勢型指標都會失效，別用 SuperTrend 進場）。+DI / −DI 決定方向。

**波動** — ATR%/HV/EWMA、TTM Squeeze、BB %B/帶寬
TTM Squeeze：布林縮進肯特納內 = 黃點（壓縮中），釋放 = 綠點。壓縮之後通常有大行情，但**方向要靠柱狀圖**（綠上紅下）。

**資金流／相對強度** — MFI、CMF、OBV、A/D Line、相對量、RS vs SPY、滾動 Beta/Alpha
RS vs SPY 上升 = 跑贏大盤，這是法人選股的核心邏輯。

---

## 三、幾組實用搭配

不要一次開十個。依你當下要回答的問題選：

| 你想知道 | 開這些 |
|---------|-------|
| 這是不是趨勢，該不該跟 | SuperTrend + ADX/DMI + EMA |
| 現在是不是超買，該不該減 | Bollinger + Stoch RSI + Williams %R |
| 會不會突破，什麼時候 | TTM Squeeze + Donchian + 相對量 |
| 停損停利放哪 | Chandelier Exit + Parabolic SAR + ATR%/HV/EWMA |
| 這檔比大盤強嗎 | RS vs SPY + 滾動 Beta/Alpha |
| 籌碼在誰手上 | Volume Profile + VWAP/Anchored + CMF |

---

## 四、關鍵價位卡

三欄並列，都會自動標出**離現價最近**的那一格。

- **Pivot Points（月）** — 上個完整月份的高低收算出 R3~S3，同時給 Classic 與 Fibonacci 兩種算法。當月有效。
- **Fibonacci** — 自動抓近一年（日線）最顯著的波段，判斷是上升段回檔還是下跌段反彈，給 0~161.8% 全部價位。
- **Volume Profile** — POC（最大成交量價位）、VAH/VAL（70% 價值區上下緣）。窗口限縮在近一年，否則五年圖會把 POC 放在幾年前的價位變成廢數字。

實務上：**POC 和價值區邊緣是最強的支撐壓力**，比單純的擺動高低點可靠，因為那裡真的有籌碼。

---

## 五、技術評級卡

19 項指標投票，分三軌：均線 / 擺盪 / 綜合，各自給 Strong Buy ~ Strong Sell。

**怎麼用**：不要當買賣訊號，當作「一分鐘體檢」。真正有訊息量的是**三軌不一致**的時候——
均線 Strong Buy 但擺盪 Sell = 趨勢還在但短線過熱，通常是加碼要等回檔，不是出場。

---

## 六、風險指標（重點）

這排數字是給部位管理用的，不是給看盤用的。

### 波動三兄弟

| 欄位 | 意思 | 差異在哪 |
|------|------|---------|
| HV 20D | 固定 20 根窗口的年化波動 | 20 天前的衝擊和昨天等權，然後突然掉出窗口 |
| **EWMA 波動** | RiskMetrics λ=0.94 指數加權 | 平滑衰減，**事件當天就反應**。這個最準 |
| 去極端波動 | 報酬截尾 ±2σ 後重算 | 和「年化波動」差距大 = 波動主要來自少數跳空日，不是持續性的 |

### 百分位排名（最實用的新東西）

底下那排色帶，每項指標與**這檔股票自己過去一年**比較 —— 不是跨股票比較。

> 「ATR% = 2.85」你無法判斷。「ATR% 在第 92 百分位」你立刻知道現在波動異常。

- ≥ 90（紅）= 極端
- 70–90（黃）= 偏高
- ≤ 10（藍）= 壓縮，常是變盤前兆

右上角的「波動偏高／極端／壓縮」標籤就是 EWMA 波動百分位的結論。

**這會出現在低 beta 的防禦股上**。KO 是低波動消費股，但某天跳 +5% 之後 EWMA 波動衝到自身第 94 百分位 —— 這種訊號用絕對波動率永遠看不出來。

### 停損停利

- **2×ATR 停損** — 常用的初始停損位，會隨波動自動放寬/收緊
- **3R 目標** — 賺賠比 3:1 的目標價

### Beta

- `BETA` = 全期間
- `BETA 近期` = 滾動 63 根，hover 會顯示「一季前多少、區間多少」

**單一 Beta 會騙人**：NVDA 兩年平均 2.14，但區間是 1.41–3.07。看到近期 Beta 明顯高於一季前（欄位轉紅），代表這檔對大盤的敏感度正在放大，同樣的部位風險已經變大了。

---

## 七、訊號掃描頁 `/signals`

全市場掃一遍，只列出當天有觸發的標的，依訊號數排序。約 20 秒跑完 269 檔。

新增的訊號類型：SuperTrend 翻轉、TTM Squeeze 釋放、ADX 趨勢分級、Stoch RSI、MFI、CCI、CMF、Ichimoku 雲層突破、Parabolic SAR 翻轉、黃金/死亡交叉、52 週新高低、爆量。

**注意**：一檔股票同時亮 6 個超買訊號，不代表比亮 2 個更該賣 —— RSI/KDJ/Stoch RSI/Williams %R/CCI 本質上都在測同一件事，會一起亮。**要看的是不同類別同時亮**（例如：超買 + 爆量 + 跌破支撐）。

快取 5 分鐘，要強制重掃：

```bash
curl -X DELETE http://localhost:6000/api/signals/watchlist/cache
```

---

## 八、每日簡報怎麼吃到這些

`scripts/daily_analysis.py` 每天自動跑，每個持倉的表格多了兩列：

```
| 波動狀態 | EWMA 25.3%，近一年第 94 百分位 → 🔴 極端（波動遠高於自身常態） |
| 價格位階 | 近一年第 98 百分位 |
```

prompt 裡明確告訴 LLM：這是跟自己比不是跨股票比；極端要調高 `risk_level`、縮小部位或收緊停損；壓縮要預先設好雙向觸發價。

所以你在 War Room 看到某個持倉標成 `high` 風險，除了貼停損、破位之外，現在多了一個觸發原因：**它自己的波動進入極端區**。

---

## 九、給腳本用（API）

```bash
# 完整指標 + 分析
curl "http://localhost:6000/api/stocks/NVDA/chart?period=2y&interval=day"
```

回傳結構：

```jsonc
{
  "bars": [ /* 每根 K 棒 72 個欄位 */ ],
  "sr":   { "resistance": [...], "support": [...] },
  "analytics": {
    "pivots":         { "monthly": {...}, "weekly": {...} },
    "volume_profile": { "poc": 181.4, "vah": 200.3, "val": 173.1, "bins": [...] },
    "fib":            { "direction": "up", "levels": [...] },
    "stats":          { "ewma_vol": 40.7, "beta_now": 2.06, "percentiles": {...}, ... },
    "rating":         { "overall": {...}, "moving_averages": {...}, "oscillators": {...} }
  }
}
```

參數：`period` = `1m|3m|6m|1y|2y|5y|10y|max`，`interval` = `day|week|month`。
10y/max 會自動降成週線。

直接在 Python 裡用（不經過 API）：

```python
from backend.app.indicators import (
    compute_indicator_frame, compute_analytics,
    ewma_volatility, percentile_rank, volatility_regime,
)

ind = compute_indicator_frame(df, bench_close=spy_close)   # 49 條序列（含大盤比較）
vol = ewma_volatility(df["Close"])                          # 單獨用
pct = percentile_rank(vol, 252).iloc[-1]                    # 目前百分位
print(volatility_regime(pct))                               # extreme / elevated / normal / compressed
```

---

## 十、注意事項

1. **時間框架會改變意義** —— 所有窗口都是「根數」不是「天數」。切到週線，HV20 就是 20 週；ADX(14) 是 14 週。月線圖的百分位是跟幾十年歷史比。

2. **百分位是跟自己比** —— 兩檔都顯示「第 95 百分位」不代表波動一樣大，只代表兩檔都處在各自的極端。

3. **Ichimoku 雲層不往前延伸** —— 標準畫法會把雲層畫到未來 26 根，這裡對齊在現有 K 棒上，所以看不到未來雲。遲行線（Chikou）最後 26 根是空的，這是正常的。

4. **ADX < 20 時別用趨勢指標** —— SuperTrend、PSAR、Donchian 在盤整時會來回被巴。先看 ADX 再決定用哪一組。

5. **資料源限流** —— yfinance 常被 rate limit，圖表、掃描、持倉技術面都已經有 Yahoo chart API 的 fallback。但 `/api/stocks/{ticker}/price`（即時報價）目前**還沒有** fallback，限流時會回 500，前端會退回資料庫價格。
