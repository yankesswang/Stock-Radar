# StockRadar 每日／每週報告系統 — 完整流程文件

> 由程式碼還原，2026-08-23；**2026-08-24 可信度改版後更新**。
> **自成一體**：涵蓋 stock-radar 自己跑的五條管線、前置備料，以及外部新聞日報
> （Stock Digest / Horizon 引擎）的完整細節，不需要跳去別的文件。
>
> 前半段是白話總覽（不用懂程式也讀得懂），後半段是每條管線的資料源與過濾門檻，
> 中間新增「第一部分之二：可信度層」——那是 2026-08-24 這次改版的核心。

---

# 第一部分：白話總覽

## 說穿了就是：你睡覺的時候，電腦幫你看盤

美股開盤時台灣是半夜。所以有一組排程在凌晨輪流上工，等你起床，Telegram 和 portal 上就有結論。

它們的分工是**一條管線只做一件事**：

| 誰 | 幾點 | 做什麼 | 一句話 |
|----|------|--------|--------|
| 🧑‍🍳 備料 | 05:00 | 把每檔持倉的 AI 決策卡先算好放著 | 不對外輸出，只是給下一班當材料；前端在「持倉復盤」裡逐檔對照 |
| 🎖️ 戰情官 | 05:30 | 顧**已經買的**股票 | 每檔給明天的明確動作 + 具體停損價 |
| 🔭 獵人 | 06:00 | 找**還沒買的**機會 | 從兩百多檔篩到 20 檔再深挖 |
| ✂️ 剪報員 | 06:40 | 今天發生了什麼跟我有關的事 | 只策展，不給買賣建議 |
| 📰 報童 | 07:00 | 外面的媒體與散戶在聊什麼 | 唯一「不從我的持倉出發」的一條 |
| 🚨 巡邏兵 | 每 5 分鐘 | 有沒有東西該現在看 | 純規則，不動腦不花錢 |
| 📋 品管 | 07:20 | 今天到底缺了什麼 | 缺件直接寫在 portal 與 Telegram，不讓你自己猜 |

## 三個讓它比一般自動報告聰明的設計

**1. 戰情官不是照稿念，它會自己去查。**
舊做法是把所有資料一次塞進 prompt（約 39k 字），模型讀完寫報告。現在只給一份 <12k 字的**精簡簡報**（大盤、持倉一行表、風險旗標、昨日計分卡、Mimi 近進場區），再給它 `curl` 和 `WebSearch` 兩支工具，**要它自己挑 3–6 檔深挖**。查完才下結論。

**2. 它每天要訂正昨天的作業。**
昨天說「跌到 $100 就買」，今天上工第一件事是查真的跌到了沒。系統會自動比對 journal 裡的觸發價與期間 high/low，產出**計分卡**；再加上回看 30 天的**決策帳本**（每筆決策算 5 個交易日前瞻報酬 vs SPY），讓它看見自己的系統性偏差（例：trim 總是太早）。SOP 明訂：帳本裡已觸發的項目**必須逐筆回應**，裝沒看到就是失職。

**3. 報告不准比資料更確定。**（2026-08-24 新增，見「可信度層」）
價格觸發**不等於**成交；報告延遲超過 12 小時就不准再寫「今日執行」；
沒有原始資料的段落寧可整段不寫，也不用「原文未提供」佔版面；每天產出一份完整性 manifest，
缺什麼直接寫在 portal 和 Telegram 上，不讓使用者自己猜。落地前有一支純程式的守則
（`scripts/report_guard.py`）會逐行掃描並**強制改寫**違規句子，改寫紀錄公開在報告裡。

**4. 貴的力氣只花在篩過的東西上。**
機會掃描是兩段漏斗：第一段純 Python、零 API 成本，從 225–260 檔算出訊號分數收斂到 top 20；第二段才讓 claude 逐檔盡調。新聞策展同理：先用來源黑名單、標題正則、代號硬比對砍掉八成雜訊，剩下的才進模型。

## 一天的時間軸（台北時間）

```
04:00  ← 週五美股收盤（台北週六 04:00）

05:00  🧑‍🍳 F  AI 持倉卡預產         每天       launchd
05:30  🎖️ A  盤後復盤 war_room      二～六     backend 迴圈（依賴 F）
06:00  🔭 B  機會掃描 market_scan   二～六     backend 迴圈（依賴 A）
06:40  📰 C  市場新聞 market_news   二～六     backend 迴圈（依賴 B）
07:20  📋 G  完整性檢查 manifest    二～六     backend 迴圈（依賴 C）
07:00  📊 D  Stock Digest 日報      二～五     launchd（週一停跑）
10:00  🗓️ D  Stock Digest 整周復盤  週六       launchd
全天   🚨 E  規則型通知             每 5 分鐘   backend 迴圈
```

> ⚠️ **A/B/C/G 的時間是「最晚啟動時間」，不是固定排程。** 2026-08-24 起改成依賴式工作流
> （`scripts/pipeline_state.py`）：前置階段一完成，下一階段**立刻**接著跑。
> 復盤 06:10 才結束時，掃描就 06:10 起跑，而不是 06:00 硬闖去跟復盤搶 claude／yfinance／backend。
> 失敗也不再「當天永久放棄」——每階段有 `max_attempts` 與冷卻時間，會自動重試。
>
> **為什麼 D 週一停跑？** 台北週一 07:00 的 24 小時窗口只涵蓋週末（美股沒開盤），
> 抓到的必然是週末內容。週二～週五才對得上美股週一～週四收盤；週五收盤由週六的 168h 週報涵蓋。

> **為什麼 A/B/C 是「二～六」？** 美股週一～五收盤 = 台北週二～週六凌晨。週日沒有新交易日，不跑。
> **為什麼 D 的週報排在週六 10:00？** 週五美股收盤是台北週六 04:00，盤後與財報數據更晚才齊；
> 週六 10:00 回看 168 小時，才能完整涵蓋週五 —— 這是平日日報（一～五 07:00）涵蓋不到的。
> **為什麼 F 排在 A 前 30 分鐘？** F 是純快取，A 要讀它產出的逐股 AI 卡當 context。

## 只有兩種觸發者

| 觸發者 | 負責 | 機制 |
|--------|------|------|
| **launchd**（macOS 排程） | D、F | `~/Library/LaunchAgents/com.arthurwang.stock-digest*.plist` → `~/Library/Scripts/*.sh` |
| **backend 內迴圈** | A、B、C、E、G | `notifications.py:telegram_notification_loop()`，`asyncio.sleep(300)` 每 5 分鐘醒一次 |

backend 迴圈**不是 cron**。它每 5 分鐘醒來，問 `scripts/pipeline_state.should_run()`：

```
可以跑 = 今天是該跑的星期
        AND（前置階段全部完成 OR 現在已過 latest_start）
        AND 這階段今天還沒成功
        AND 若曾失敗，重試次數未用完且已過冷卻
```

狀態寫在 `data/agent/pipeline/{date}.json`（`pending / running / success / partial / failed`），
`running` 卡超過 90 分鐘視為死掉、允許重跑（backend 重啟後不會永遠卡住）。
launchd 擁有的階段（F AI 持倉卡）不會自己回報，迴圈開頭用**產物是否存在**補登，
否則 A 的前置永遠不滿足、就退化回純時間排程。

所以「每 5 分鐘檢查、完成即接棒、失敗會重試」。
**backend 沒開 = A/B/C/E/G 全部不會跑**（launchd 的 D/F 仍會跑，但會 degrade 成用舊快照，
而且隔天的完整性 manifest 會把缺件寫出來）。

---

# 第一部分之二：可信度層（2026-08-24 改版核心）

> 舊系統最大的問題不是內容不夠，而是**報告語氣比資料可信度更確定**。
> 這一層全部是純程式（沒有第二個 LLM），跨管線共用，任務只有一個：
> **讓報告只敢講它證明得了的事。**

## 1. 執行狀態機：觸發 ≠ 成交　`scripts/exec_state.py`

事故原型：8/21 復盤寫「FSLY stop loss 已執行，倉位歸零」，隔天 Portfolio 仍持有 40 股。
舊計分卡只能證明**價格觸發**，卻被寫成**已成交**。

```
recommended → triggered → acknowledged → executed → reconciled
                              ↘ abandoned          ↘ expired
```

| 狀態 | 憑什麼進到這一格 |
|------|-----------------|
| `recommended` | journal 有這筆決策 |
| `triggered` | `decision_ledger` 用期間 high/low 判定價格條件已發生 |
| `acknowledged` | 後續 journal 的 DECISIONS 帶了 `ack_prev`（戰情官明確回應） |
| `executed` | **持倉快照差分對帳到方向一致的股數變化**（`data/agent/positions/*.json`） |
| `reconciled` | executed 之後又有一份快照仍然一致（排除盤中暫態／抓錯） |
| `abandoned` / `expired` | 明說放棄／窗口內未觸發 |

**只有 `executed` / `reconciled` 允許寫「已執行」。**其餘一律「條件已觸發，待確認」。
每天跑 war_room 時會先落地一份持倉快照，所以對帳是自動的；讀不到券商對帳單時的逃生門：

```bash
uv run python scripts/exec_state.py --confirm FSLY --date 2026-08-20 --state executed --note "手動確認已市價賣出"
```

## 2. 時效標頭：這是哪一天的資料　`scripts/report_meta.py`

事故原型：8/21 的報告 8/23 16:44 才產出，內文還在寫「今日執行」「明日」。

**交易日不用猜**：直接取 yfinance 最後一根日 K 的日期（`quotes[sym]["as_of"]`）。
報價整批限流時才退回推估，並在標頭標「（推估）」。每份報告最上方固定印：

```text
交易日　：2026-08-21
資料截至：2026-08-21 16:00 ET（收盤）　（2026-08-22 04:00 台北）
產出時間：2026-08-22 05:34 台北
狀　　態：LIVE／即時（收盤後 1.6 小時）
```

| 狀態 | 判定 | 後果 |
|------|------|------|
| `LIVE` | 收盤後 ≤ 12 小時 | 正常 |
| `BACKFILL` | 收盤後 > 12 小時 | 簡報加「時效警告」段落；產出後所有即時動詞被強制改寫 |
| `STALE` | 資料日期落後 > 4 天 | 同上，並警告價位數字全部過期 |
| `UNKNOWN` | 完全取不到報價日期 | 標明時效無法驗證 |

## 3. 產出驗證：模型寫完之後、落地之前　`scripts/report_guard.py`

prompt 講一百次也擋不住，所以用純程式在落地前掃一遍，違規**就地改寫**並留稽核紀錄：

| 守則 | 抓什麼 | 改成什麼 |
|------|--------|---------|
| 執行宣稱 | `已執行`／`已停損`／`倉位歸零`／`已出場`／`已減碼`… | 「條件已觸發，待確認」 |
| 即時語氣 | BACKFILL/STALE 下的 `今日執行`／`明日開盤`／`立即` | 「{交易日} 當日應…」「次一交易日…」 |
| 佔位句 | `原文未提供`／`請參考原文連結`／`待補`／`TBD` | 整行移除 |

兩個防誤傷設計：**同一行提到的 ticker 若全部都有對帳背書就放行**；
**第三方主體**（「Third Point 已賣出 AVGO」）不改寫，只列入稽核。
報告最上方印一行摘要，完整「原句 → 改寫」對照表放在附錄「🔍 產出驗證明細」。

DECISIONS 的 `trigger` / `thesis` 會被直接印進第一層，所以它們也要過同一套守則
（否則正文改了、第一層照抄原句，等於守則只做一半）。

### 3-1. 術語中文化　`scripts/zh_terms.py`

DECISIONS 的英文 enum（`action=trim`、`risk_level=high`、`market_regime=risk-off`、
狀態機 `acknowledged`）是給程式讀的 schema，卻常被原封不動印進人看的句子
（「明日：trim」「（risk=high）」「與宏觀 risk-off 脫鉤」）。兩層處理：

| 層 | 做法 |
|----|------|
| 結構化欄位 | 呼叫端印之前先過 `zh_action()` / `zh_risk()` / `zh_regime()` / `zh_setup()`（首選） |
| 模型自由書寫 | `localize()` 在 `war_room.py` 落地前掃全份報告與 Telegram 文字（收尾網） |

`localize()` 跳過 ``` 圍籬、行內程式碼、URL、JSON 鍵值對，且只比對**小寫**單字，
所以股票代號（NOW / C / ADD）、分析師評級（Buy / Sell / Outperform）不會被誤傷。
journal / 狀態機 / 前端 enum 一律維持英文——只有使用者看到的那一面變中文。
前端徽章的對照表在 `frontend/src/components/opsTheme.js`，改動時兩邊要一起改。

改既有報告（預設只列差異，`--write` 才動檔案）：

```bash
uv run python scripts/zh_terms.py ~/.arthur-digest/stock/*/"🎖️ War Room.md"
uv run python scripts/zh_terms.py --write ~/.arthur-digest/stock/*/"🎖️ War Room.md"
```

## 4. 完整性 manifest：缺什麼要寫出來　`scripts/daily_manifest.py`

事故原型：主報告連續缺檔（8/19、8/20、8/22）、Market Scan 從未成功落地，
但 portal 和 Telegram 都只是「少一塊」，沒有任何地方說「今天是部分完成」。

每天掃描八個產物 × 管線狀態，輸出 `success / partial / failed / missing / stale`：

```json
{
  "trade_date": "2026-08-22",
  "overall": "partial",
  "artifacts": [{"key": "war_room", "status": "missing", "note": "檔案不存在"}, "…"],
  "portfolio": {"as_of": "2026-08-22"},
  "missing": ["🎖️ 盤後復盤", "🔭 機會掃描"],
  "degraded": ["📰 市場新聞"]
}
```

- 落地：`data/agent/manifest/{date}.json` ＋ `~/.arthur-digest/stock/{date}/_manifest.json`
- API：`GET /api/stock-digest/{date}` 回應多帶 `manifest` 與 `completeness`
- Portal：市場個股分頁最上方的橫幅（缺件／降級一律寫出來）
- Telegram：`overall != success` 時發一則，附手動補跑指令
- 空殼檔（產出中斷、字數低於門檻）判 `partial`，比缺檔更誤導所以要抓出來

## 5. 組合層風險：單股 25% 看不到的東西　`scripts/portfolio_risk.py`

「單一持倉 ≤ 25%」對現況幾乎沒有約束力（Top1 只有 6.9%），
真正的風險是 NVDA / SMH / TSM / MU / ARM / ADI / TXN / VRT 共同壓在 AI 半導體同一個因子上。

每天算並寫進簡報與報告：

- **主題曝險**（Main＋Mom 合併）：AI／半導體鏈、大型科技平台、企業軟體、台股…（實測 AI 鏈 40.5%）
- **多批持倉合併**：Mom 的 TSLA 分散在永豐與富邦兩個帳戶，逐帳戶看會低估真實權重
- **停損群聚**：全部停損同時觸發的預估損失（金額 + 佔組合 %）、哪些持倉根本沒設停損
- **財報群聚**：未來 14 天每個財報日會同時影響組合幾 %；指標股（NVDA/TSM/AVGO…）的**外溢**影響
- **幣別**：台股一律標 `US$3.29（NT$104.64）`，不讓 `$1.00` 有 USD/TWD 歧義

> 🔴 **匯率保護**：USD/TWD 抓不到時 portfolio API 會讓台股維持原幣，
> 台股權重會被放大三十幾倍（實測 0050.TW 從 6.9% 變 34.6%、組合停損風險從 7% 變 85%）。
> 這種情況下整節標成不可信，並在每條衍生警告後面加註——不會默默算下去。

## 6. 三層報告：90 秒看完　`scripts/war_room_report.py`

| 層 | 內容 | 誰產的 |
|----|------|--------|
| 第一層「⏱️ 90 秒晨報」 | 市場狀態／資料品質／**今天需要處理**／**等待條件**／**最大風險**／其餘 N 檔維持 | **程式**（讀 DECISIONS + 執行狀態 + 組合風險 + manifest） |
| 第二層「🔎 異常持倉」 | 只展開 action≠hold、風險 high、距停損 ≤4%、7 天內財報、有未結執行的標的；其餘 hold 併成一張表 | 模型內容，程式篩選 |
| 第三層「📚 附錄」 | 大盤判讀、組合風險、執行狀態、其餘逐檔、Mimi、計分卡、決策帳本、完整性、驗證明細 | 模型內容 + 程式區塊 |

- `<<<DECISIONS>>>` 的 JSON **只寫 journal，不進正文**。
- Telegram 發的是「第一層的今天要做／等待條件」+ 模型 SUMMARY，最上面帶時效與完整性。
- **Portal 最上層不再叫第四個 LLM 總結** —— 第一層就是程式組裝的摘要。

## 7. 決策績效重做　`scripts/decision_ledger.py`

舊版：從報告日期起算、hold 有 125 筆而 trim 只有 3 筆卻混在一起、「最佳 call +35%」其實只贏 SPY 0.4%。

新版：

- **從實際觸發日起算**（有價格條件的用觸發日，沒有的才用決策日；未觸發的直接排除）
- 主指標是**相對同主題 ETF 的超額報酬**（AI 鏈比 SMH、大科技比 QQQ…），SPY 只是次要參考
- 分「進場類 / 維持類 / 防禦類」統計 **樣本數、勝率、平均超額、最大不利走勢（MAE）**
- **建議 vs 實際成交**分開評分（沒成交的建議再準也沒進到帳戶）
- **信心校準**：0.8 的決策真的比 0.5 準嗎（分桶看勝率與超額）
- 樣本數 < 5 一律標「⚠️ 樣本不足，不可推導系統性結論」

## 8. 誰能給買賣指令

| 管線 | 定位 | 可以給 buy/add/trim/stop？ |
|------|------|--------------------------|
| **A War Room** | 決策 | ✅ **唯一** |
| B Market Scan | 研究候選 | ❌ 產出標「🔬 研究候選，非交易指令」，隔日由 A 重新驗證 |
| C Market News | 事實與來源 | ❌ payload 帶 `role: facts_only`，前端與 Telegram 都標明 |
| D Stock Digest | 外部敘事 | ❌ 「今日行動清單」已改為「今日觀察重點」，只描述不指令 |

---

# 第一部分之三：觀點層（2026-09-05 新增）

> 舊系統的質化脈絡只有一個來源：`data/finance-digest`（Substack 爬蟲，一天一篇）。
> 那是**別人寫的**。使用者自己每週在 Obsidian 整理的投資筆記——宏觀策略、科技軟體、
> AI 半導體、財報筆記——完全在管線視野外。
> 結果就是：戰情官給 WDAY 建議時，看不到使用者一個月前寫的「續抱不加碼、停損 $181.43」；
> 獵手掃出 GOOGL 時，不知道使用者七月就研究過並結論「不追高」。

## 這一層做兩件事

| | 做什麼 | 進哪裡 |
|---|--------|--------|
| **觀點層** | ticker → 我近 90 天的論點／當時的動作／停損位 | war_room 與 market_scan 簡報的「🧠 我的觀點庫」段落 |
| **universe 擴充** | 近 60 天筆記提到的個股併入 Stage-1 預篩名單 | `opportunity_scanner.build_universe()`（266 → 293 檔） |

兩份簡報的任務指令都加了一條硬要求：**對照觀點庫的標的，必須明講原始論點還成不成立**
（催化劑兌現了沒、當時的疑慮解除了沒、停損位還適不適用），不准無視，也不准硬套舊結論。

## 為什麼要鏡像，不直接讀 vault

vault 在 `~/Documents`，檔案是 iCloud 的 `dataless` 佔位檔（近 90 天筆記約 27%）。
2026-09-05 實測（`launchctl submit` 起的 job）：

```
列目錄            → OK
讀已具現化的檔    → OK
讀 dataless 檔    → OSError(11) Resource deadlock avoided
```

**所以這不是 TCC 權限問題，是檔案具現化問題**：背景行程叫不動 iCloud 下載檔案。
（earnings_notes 那條註解寫的「TCC 擋住」是誤診，結論一樣要鏡像，但原因不同。）

對策是鏡像到 `~/.arthur-digest/vault_notes/vault-mirror`（一般 APFS，永遠具現化）：

- **鏡像**（`vault_notes.mirror()`）：增量（mtime+size 比對）。在有 GUI session 的行程
  跑（互動終端機／Claude Code）能觸發下載，讀得到全部；在 launchd 背景跑只讀得到
  「還在本地的」，dataless 的記進 `pending`。
- **索引與讀取**（`build_index()` / `load_index()`）：只讀鏡像，背景行程 100% 安全。

war_room 開場（Step 0）會自己跑一次 `refresh()`，所以正常情況全自動。
撈不到的留在 pending，**完整性 manifest 會把它列成 partial**，不讓使用者自己猜；
在終端機跑 `uv run python scripts/sync_vault_notes.py` 就會補齊。

## 收哪些、不收哪些

| 收 | 不收 | 為什麼不收 |
|----|------|-----------|
| 宏觀策略、科技軟體、AI半導體、太空、能源原物料、技術面、金融保險、人形機器人、生技醫療、量化交易 | `Stock Digest/` | 管線自己的產出，收進來會變成自我迴圈 |
| `財報筆記/**/*財報筆記.md` | 同資料夾的逐字稿／prepared remarks | 那是素材不是觀點，論點會抽成「Welcome, everyone…」 |
| 根目錄的單篇筆記 | `深度會員/`（825 篇）、`Uncle Stock Notes/`（350 篇） | 量大且多為歷史存檔，訊噪比低 |
| | `mimi操作建議總表.md`、`投資操作建議總表.md` | 已有專門管線在讀，不重複收 |

## 抽取規則（`src/analysis/vault_notes.py`）

- **論點**：財報筆記取 `> [!summary]` callout（也吃 `[!TLDR]` 等變體），主題筆記取
  `## TL;DR` 的「核心主張／關鍵機制」條列，都沒有才退回正文首段。截斷至 260 字。
- **停損價**：一篇筆記常提到好幾個停損位（1 倍 ATR／2 倍 ATR／前低），**只認結論段裡
  那個**，而且掃的是未截斷的結論段——WDAY 那篇就是反例：正解 $181.43 寫在結論最後一句，
  用截斷後的論點去抓會抓到全文較早出現的 $194.01，而那正是筆記明講「不該設」的價位。
  候選還要過濾「1.5 到 2 倍 ATR」（倍數）、「2027 年前」（年份）這類誤判。
- **相關性排序**：綜合型週報會把十幾檔列進 `stocks:`，論點其實跟大多數無關。
  標題點名該檔 +2 分、整篇只談 ≤3 檔 +1 分，分數優先於日期。
- **ETF/指數過濾**：宏觀筆記引用 SPY/QQQ/TLT/XLV 當參照物。它們留在觀點層，
  但不進個股預篩 universe（`NON_STOCK_TICKERS`）。
- **大盤筆記**：`宏觀策略/` 混著個人理財與商業個案（教養支出、Jellycat），
  用 regime 關鍵詞（聯準會／利率／殖利率／CPI／輪動…）過一道才進戰情官簡報。

所有讀取端一律防禦式：索引缺檔／壞 JSON／vault 不見 → degrade 成空段落並照實說
「近 90 天我的筆記沒寫到這批標的」，絕不 raise、絕不拖垮管線。

---

# 第二部分：六條管線的資料源與過濾方式

## 用途分類總表

| # | 用途 | 管線 | 它回答的問題 | LLM |
|---|------|------|--------------|-----|
| **A** | **決策** | `scripts/war_room.py` | 我手上的股票，明天每一檔怎麼辦？ | `claude -p` **agent（帶工具、多回合）** |
| **B** | **開發新標的** | `scripts/market_scan.py` | universe 裡有什麼我還沒買、現在值得看的？ | `claude -p` agent（Stage-2 才用） |
| **C** | **資訊** | `scripts/market_news.py` | 今天發生了什麼跟我有關的事？ | `claude -p` **單輪、不開工具** |
| **D** | **外部視角** | `~/Library/Scripts/stock-digest.sh` → Horizon | 外面的媒體與散戶在寫什麼？ | GPT-4o-mini |
| **E** | **警報／例行** | `notifications.py` 迴圈 | 有沒有東西該「現在」看？ | ❌ 純規則 |
| **F** | **前置備料** | `POST /api/scheduler/ai-report/run` | （不對外，只為 A 準備逐股 context） | 逐檔 claude |
| **G** | **完整性** | `scripts/daily_manifest.py` | 今天到底產出了什麼、缺了什麼？ | ❌ 純規則 |

---

## A. 盤後復盤 War Room（用途：決策）

**一句話**：把持倉逐檔過一遍，給出明天的明確動作與具體停損價，並且**必須延續昨天的論點**。

| 項目 | 值 |
|------|-----|
| 排程 | 台北 **二～六，最晚 05:30 起跑**（AI 持倉卡完成即可提前；`pipeline_state.STAGES`） |
| 觸發 | `notifications.py:_trigger_daily_analysis_script()` → `_run_pipeline_stage("war_room", …)` |
| 重試 | 失敗最多 **2 次**，冷卻 20 分鐘（不再「當天永久放棄」） |
| 腳本 | [`scripts/war_room.py`](../scripts/war_room.py)（不存在時 fallback 到舊版 `daily_analysis.py`） |
| 模型 | `claude -p` sonnet，`--max-turns 30`，逾時 **40 分鐘** |
| 工作目錄 | `agent/`（agent SOP 在 [`agent/CLAUDE.md`](../agent/CLAUDE.md)） |

### A.1 資料源（Python 端組簡報，總長控制在 <12k chars）

| 簡報區塊 | 資料源 | 過濾／取捨 |
|---------|--------|-----------|
| 📈 大盤快照 | yfinance：`MACRO_SYMBOLS` 11 檔 + `SECTOR_ETFS` 11 檔（並行 20 執行緒） | 指數只印 SPY/QQQ/IWM/DIA/^VIX；宏觀只印 TLT/UUP/GLD；板塊全印但**依漲跌排序強→弱** |
| 💼 持倉精簡表 | `portfolio.get_csv_portfolio()`（`data/portfolio.csv` + DB 快照） | 排除 `CASH`／`ETORO`；一檔一行，不展開細節 |
| ⚠️ 風險旗標 | `_generate_csv_portfolio_report(period="daily").risk_flags` | 只帶 flag，不帶原始技術指標 |
| 👁️ Mimi 觀察名單 | `~/Documents/arthurwang_DB/投資/mimi操作建議總表.md`（TCC 擋住時 fallback DB 同步副本） | **只列「接近進場區」的**（`_is_near_entry_zone`）；markdown 表頭殘留由 `_clean_mimi_field` 清成「見策略文件」 |
| 📊 昨日決策計分卡 | `data/agent/journal/` 前一份 + yfinance 期間 high/low | 需有 `trigger_price` + `direction` 才判 triggered；無價格資料保守視為未觸發 |
| 🧾 決策帳本 | `data/agent/journal/*.json` 歷史 | 未結追蹤回看 **10 天**；戰績統計回看 **30 天**（每筆算 **5 交易日前瞻報酬 vs SPY**）；兩區塊合計硬上限 **2800 chars** |
| 🔭 機會掃描回顧 | `data/agent/opportunities/*.json` | 只收 **≤7 天**內的；只留 `action ∈ {buy, accumulate, watch}`；依 conviction 取 **top 6** |
| 📅 財報日曆 | `GET /api/earnings/calendar`（timeout 120s） | 只留 `0 ≤ days_until ≤ 45`；未公布最多 **20 列**、已公布最多 **12 列** |
| 📰 財經 digest | `finance_digest.load_recent_items(14)`（Substack 投資筆記） | 先列**與持倉 ticker 交集**的，再列近 **5 天**大盤敘事；整段硬上限 **1200 chars** |
| ⏰ 時效標頭 | `report_meta.build_freshness()`（交易日取自報價 `as_of`） | 非 LIVE 時額外插入「時效警告」段落，禁止即時動詞 |
| 🔐 決策執行狀態 | `exec_state.sync_states()`（journal 觸發 + 持倉快照差分） | 只列近 14 天可追蹤的決策；`hold` 不進執行對帳 |
| 🧮 組合層風險 | `portfolio_risk.compute_portfolio_risk()` | 主題曝險／多批合併／停損群聚／財報群聚／幣別；匯率缺失時整節標不可信 |

### A.2 過濾方式（claude 端：它自己決定要查什麼）

簡報**只是起點**，不是答案。agent 拿到 SOP 後自主蒐集：

1. **選標的（3–6 檔）** — 符合任一條件才深挖：今日漲跌幅偏大／接近 Mimi 進場區或停損／昨日計分卡已觸發或論點需修正／近期有財報催化。
2. **可用工具** — `Bash(curl http://127.0.0.1:6000/*)`（**只准打本機 backend**）、`WebSearch`、`WebFetch`。
3. **硬規則（SOP 明列，違反即失職）**
   - 每檔持倉都要逐一點評；`hold` 是判斷後的結論，不是預設值
   - 單一持倉權重建議 ≤ **25%**，已超過只能指向 trim
   - 停損**必附具體數字價位**，不准寫「跌破支撐就停損」
   - Mimi 文件的進場區／停損是**第一參考**，偏離必須說明理由
   - 必須延續或明確修正**前一日 journal** 的論點，不可失憶重來
   - 帳本裡 ✅已觸發 的項目**逐筆回應**（執行 or 明說為何放棄）
   - 掃描回顧中已進入進場區的機會**必須重新驗證並給結論**
   - 財報日**只能引用簡報那張表**（防止從新聞標題反推出錯誤日期）
   - **觸發 ≠ 成交**：狀態表不是 `executed`/`reconciled` 就不准寫「已執行」；已觸發項目要在
     DECISIONS 填 `ack_prev`（verdict: executed / abandoned / pending + note）
   - 報告狀態是 BACKFILL/STALE 時**禁止即時動詞**（今日執行／明日開盤／立即）
   - SHARED 收尾要回應一次**組合層風險**（主題曝險、停損群聚金額、指標股財報外溢）
   - DECISIONS 的欄位必須與文字一致 —— 第一層晨報是**程式**從 DECISIONS 組出來的

### A.3 輸出

模型輸出仍是六區塊：`<<<SUMMARY>>> <<<SHARED>>> <<<MAIN>>> <<<MOM>>> <<<MIMI>>> <<<DECISIONS>>>`，
但那只是**素材**：落地前會先過 `report_guard`（產出驗證）再交給 `war_room_report` 重組成三層。

| 去處 | 內容 |
|------|------|
| Telegram + LINE | 時效標頭 + 完整性 + **程式組裝的「今天要做／等待條件」** + 模型 SUMMARY |
| `~/.arthur-digest/stock/{date}/🎖️ War Room.md` | **三層報告**（時效標頭／90 秒晨報／異常持倉／附錄）（**portal「市場日報」分頁讀這裡**） |
| `~/.arthur-digest/stock/{date}/_manifest.json` | 當日完整性 manifest（portal 橫幅讀這裡） |
| Obsidian | `投資/Stock Digest/{date}/🎖️ War Room.md`：與鏡像**同一份**三層報告（不再兩套語氣）。<br>不再走 `save_obsidian_note()`（那支只認舊的 `<<<MARKER>>>` 格式，餵三層報告會得到空殼），也不再覆蓋 Stock Digest 自己的 `📊 Portfolio.md` |
| `data/agent/journal/{date}.json` | 解析出的 DECISIONS（含 `ack_prev`）+ `market_regime` + `session_id` |
| `data/agent/ledger/{date}.json` | 帳本結構化資料 + `exec_state`，供 `/api/war-room/review` 畫圖 |
| `data/agent/positions/{date}.json` | 持倉快照（跨帳戶合併）—— **executed 對帳的唯一基準** |
| `data/agent/exec_state.json` | 決策執行狀態機（跨日累積） |

**失敗行為**：非零退出／逾時 → 發 Telegram 告警（附還剩幾次自動重試 + 手動補跑指令）；
由 `pipeline_state` 控制重試次數與冷卻，不會每 5 分鐘重燒一次 claude。

---

## B. 機會掃描 Market Scan（用途：開發新標的）

**一句話**：兩段漏斗 —— 先用零成本的 Python 從近兩千檔篩到 20 檔，再讓 claude 對這 20 檔做定性盡調。

| 項目 | 值 |
|------|-----|
| 排程 | 台北 **二～六，復盤完成後立刻起跑，最晚 06:00**（依賴 war_room） |
| 定位 | 🔬 **研究候選，非交易指令** —— 買賣結論只由 A 給 |
| 腳本 | [`scripts/market_scan.py`](../scripts/market_scan.py)；Stage-1 在 [`src/analysis/opportunity_scanner.py`](../src/analysis/opportunity_scanner.py) |
| 模型 | `claude -p`，逾時 40 分鐘 |

### B.1 Stage-1 量化預篩 — 純 Python，零 claude 成本

**universe（2026-09-06 起分兩層，約 1,900 檔）**

| 層 | 來源 | 檔數 | 意思 |
|---|---|---|---|
| `curated` 研究過的 | `src/config.STOCK_LIST`（**29 個主題桶、594 檔**）∪ Mimi 觀察名單 ∪ 近 60 天筆記提到的 | ~660 | 我的名單 |
| `discovery` 發現層 | `src/analysis/discovery_universe.py` —— 全美股過三道品質門檻 | ~1,275 | **我沒研究過的** |

改這一段之前，universe 完全等於「我自己抓過的股票」，所以永遠只掃得到自己已經想到的
標的 —— 漏掉的機會**在定義上就不可能出現在榜上**。發現層就是為了補這個洞。

**發現層的三道門檻與它們各自的資料源**（`scripts/refresh_discovery_universe.py --status` 看現況）：

| 門檻 | 值 | 資料源 | 為什麼是這個源 |
|---|---|---|---|
| 市值 | ≥ 20 億美元 | Nasdaq screener API（**一次呼叫回全市場 7,133 檔**）| 微型股訊號不可靠 |
| 日均成交額 | ≥ 2,000 萬美元 | Yahoo v8 chart 的 3 個月均量 × 價 | 沒量的進得去出不來 |
| 分析師覆蓋 | ≥ 3 位 | stockanalysis.com forecast 頁 | 沒人覆蓋的估值類 bucket 一定缺資料白掃 |

漏斗：7,133 →（市值）1,973 →（成交額）1,899 →（分析師）**1,839 檔**，跑一輪約 160 秒，
快取在 `data/universe/discovery.json`，**7 天過期後由掃描自己重抓**（不必另外排程）。

> ⚠️ 這三個源**刻意都避開 yfinance 的 `.info`**：它走 Yahoo 的 crumb 端點，掃到幾百檔就整個
> session 被封（`YFRateLimitError`），連續一秒一發也一樣封。專案其他地方
> （`portfolio._fetch_price_info`、`earnings/price_technicals`）早就為同一個原因繞開它。
> 成交額特意不用 Nasdaq 那欄「當日成交量」：單日值冷清一天就會把好股票踢出去。

**兩層的差別只有標記，訊號計算完全一樣**：發現層股票在簡報標 `🔍未研究`、在 War Room
卡片標 `NEW NAME`，並在同分時排在 curated 後面（見下方保障名額）。

**資料源**（全部並行抓）：

| 資料 | 來源 | 備註 |
|------|------|------|
| 技術面 | `portfolio._fetch_technicals_batch()`（yfinance 6 個月） | RSI14 / MA5-20-50 / 量比 / 支撐壓力 / 突破 / 破位 |
| 估值 + 財測 | 自家 DB 最新 `Valuation` + `Forecast` 快照 | 一次 SQL 撈全 universe |
| DCF 折價 | `data/dcf/{TICKER}_dcf_*.json` | **只讀既有檔案，不 on-demand 重算**（225 檔會太慢） |
| 財報日 | yfinance `Ticker.calendar`（並行 10） | 取最近的未來財報日 |
| 持倉標記 | `get_csv_portfolio()` | 標 `in_portfolio` / `held_portfolios` |

**五個 setup bucket 的觸發門檻**（過濾方式的核心）：

| bucket | 觸發條件 | 子分算法 |
|--------|---------|---------|
| `oversold_bounce` 超賣反彈 | `RSI14 ≤ 32` **且** 非完美空頭排列（bear 排除，交給做空 bucket） | `0.55 × ((32−RSI)/17 + 0.4)` + 觸及支撐加分 `0.25 + 0.05×strength` |
| `undervalued_catalyst` 低估+催化 | （DB `valuation_status` 含 under 且 `diff_pct > 0`）**或** DCF `margin_of_safety ≥ 15%`；**且**（財報 ≤ **21 天** **或** 次年 EPS 成長 ≥ **10%**） | `0.25 + 折價分(≤0.45) + 催化分(0.25~0.3)`；兩套估值同向 **+0.12**；**封頂 0.85** |
| `momentum_breakout` 動能突破 | 有突破壓力 **且** MA 多頭排列（bull／short_bull）**且** 量比 ≥ **1.5** | 排列 0.5／0.3 + 量能分 + 壓力強度 + 0.1 |
| `mimi_entry` Mimi 進場區 | 現價進入／接近 Mimi 進場區 | 固定 **0.7** |
| `breakdown_short` 破位做空 | 有破位 **且** 空頭排列 **且** `25 < RSI < 45`（避免在超賣區追空） | 排列 0.5／0.3 + RSI 轉弱分 + 支撐強度；`direction = short` |

> ⚠️ 估值語意（已用真實 DB 驗證）：`valuation_diff_pct > 0 = 低估`。
> **不用** raw `estimated_pe` vs 5 年中位當觸發 —— `pe_median_price` 受次年 EPS 成長影響，
> NVDA/CMG 會出現 est_pe 遠高於中位卻仍判低估、或 est_pe 為負。只採信 `valuation_status` / `diff_pct` / DCF。

**綜合排名**：一檔可同時命中多個 bucket。

```
signal_score = 最強 bucket 子分 × 權重 + 其餘 bucket 子分 × 權重 × 0.25   （封頂 1.0）
bucket 權重： 低估+催化 1.15 ＞ 動能突破 1.05 ＞ Mimi 進場 1.0 ＞ 超賣反彈 0.95 ＞ 破位做空 0.9
```

排序後取 **top 20**（`--limit`），**其中至少 25%（5 席）保障給發現層**
（`opportunity_scanner.DISCOVERY_QUOTA`）。

> 為什麼要保障名額：`signal_score` 會在 1.0 撞頂，滿分並列動輒二三十檔，而同分決勝是
> 「研究過的優先」。純按排名切的話發現層會被整批擠出 top 20 —— 掃了一千多檔沒研究過的
> 股票卻一檔都送不進 Stage-2，等於白掃。實測：不設額度時 top 30 只有 4 檔發現層。

**防退化機制**：整份結果 TTL 快取 **10 分鐘**；但若 `evaluated < universe × 30%`（yfinance 整批被限流的徵兆）就**不寫快取**，避免毒化後續 10 分鐘的查詢。Stage-1 零入圍時直接放棄本次掃描，不燒 claude。

### B.2 Stage-2 定性盡調 — claude

把 top 20 的量化 context 壓成 <8k chars 簡報（另含 market regime、近 14 天財經 digest，上限 1500 chars），agent 帶 `curl` / `WebSearch` 逐檔查 news / earnings / chart / dcf / peers + 催化劑，技術面＋基本面＋催化劑三者綜合，輸出結構化 JSON。

### B.3 輸出

- `data/agent/opportunities/{date}.json` — 機會清單（`action` / `conviction` / `entry_zone` / `thesis`）
- Telegram 精華，**與前一份比對，新入圍標 🆕**
- `~/.arthur-digest/stock/{date}/🔭 Market Scan.md` 鏡像 + Obsidian（可選）
- **閉環**：隔天 A 的「🔭 機會掃描回顧」會回頭追這些機會的現價 vs 進場區

---

## C. 市場新聞策展（用途：資訊，不給建議）

**一句話**：Python 抓料並砍掉雜訊 → claude 只負責挑重點與說明「對我的意義」→ 連結由程式回填。

| 項目 | 值 |
|------|-----|
| 排程 | 台北 **二～六，掃描完成後立刻起跑，最晚 06:40**（依賴 market_scan） |
| 定位 | 📰 **純事實與來源**（payload 帶 `role: facts_only`），不給持倉操作建議 |
| 腳本 | [`scripts/market_news.py`](../scripts/market_news.py) |
| 模型 | `claude -p` **單輪、不開工具、不上網**，逾時 600s |

### C.1 資料源

| 類別 | query | 數量 |
|------|-------|------|
| 大盤／宏觀 | 5 條固定 Google News query，**全部鎖 Tier-1 媒體白名單**（reuters / cnbc / bloomberg / wsj / ft / apnews / barrons / marketwatch）+ `when:1d`～`2d` | 每 query **10** 則 |
| 個股 | 持倉優先佔額度，剩下才給 Mimi 觀察名單，共最多 **35 檔** | 每檔 **6** 則 |
| 背景 | yfinance 大盤快照（SPY/QQQ/IWM/DIA/^VIX + TLT/UUP/GLD/BTC-USD）+ 持倉一行表 | — |

> 個股一律**用公司名而非純代號**搜尋 —— 純代號會抓到同名雜訊（VT → 佛蒙特州、JBL → 醫院）。
> 公司名解析結果快取在 `data/market-news/_company_names.json`，問過一次就零成本。
> 沒有 site 白名單的話，Google News 會塞滿孟加拉通膨、印度金價這種「關鍵字對但完全無關」的稿子。

### C.2 過濾方式（Python 端四層，全部在 claude 之前）

1. **來源黑名單** `SOURCE_BLOCKLIST` — zacks / motley fool / simply wall st / insider monkey / benzinga / marketbeat / tipranks / stocktwits / trefis… 這些在 Google News 佔比極高但幾乎沒資訊量。
2. **標題正則黑名單** — `best … stocks`、`stocks to buy|watch`、`should you buy`、`price target`、`analyst upgrade/downgrade roundup`、`if you invested`、`stock forecast|prediction`、`jim cramer`…
3. **相關性硬檢查** — 標題必須出現**大小寫敏感的代號**（`(?<![A-Za-z0-9])VT(?![A-Za-z0-9])`）或公司名中有辨識度的字（`Inc`／`Corp`／`Group` 這種無辨識度字不算）。**這一關比任何 prompt 都可靠。**
4. **時間窗 + 去重** — 預設 **30 小時**（台北早上跑，蓋掉整段美股盤中與盤後）；標題指紋去重（去符號、截 80 字）。

存活的素材編號成 `[1][2][3]…` 清單餵給 claude，策展後**輸出 8–15 則**（沒大事的日子就少給幾則，不准湊數）。

**防捏造連結**：claude 只能引用編號，**不准自己寫網址**，URL 由程式用 refs 回填。

**degrade**：claude 沒裝／逾時／回爛 JSON → 退回「未策展」模式，仍照大盤與 ticker 分組落地當日頭條（`curated=false`，前端標註）。分頁永遠有東西可看。

### C.3 輸出

`data/market-news/{date}.json` → stock-radar 戰情室（/war-room）的「新聞線」面板。**預設不推 Telegram**（要推得加 `--telegram`）。

---

## D. Stock Digest 美股財經日報 / 整周復盤（用途：外部視角）

**一句話**：唯一一條「不從我的持倉出發、而是看外面世界」的管線。底層是開源專案 **Horizon**，在其 pipeline 的「總結生成」階段插入大量美股客製邏輯（持倉對照、Finnhub 即時數據、繁中分類）。

| 項目 | 值 |
|------|-----|
| 排程 | 日報 **二～五 07:00**（**2026-08-24 起週一停跑**）；整周復盤 **週六 10:00**（週日不跑） |
| LaunchAgent | `com.arthurwang.stock-digest.plist`（Weekday 2–5 @07:00）／`com.arthurwang.stock-digest-weekly.plist`（Weekday 6 @10:00，傳參數 `weekly`） |
| 定位 | 🌏 **外部敘事**；「今日行動清單」已改為「今日觀察重點」，不對持倉下指令 |
| **實際入口** | `~/Library/Scripts/stock-digest.sh [weekly]`（**repo 外**；版控副本在 `digest/stock/scripts/stock-digest.sh`） |
| 引擎 | `uv run horizon`（`digest/stock/horizon/src/main.py` → `HorizonOrchestrator.run()`） |
| 模型 | GPT-4o-mini（`horizon/data/config.json` → `ai.model`） |
| 視窗 | 日報 24h；週報 `--hours 168` |
| Log | `~/Library/Logs/stock-digest/YYYY-MM-DD.log` |

### D.0 前置：確保持倉快照是今天的

digest 讀 `portfolio_snapshots` 的 `MAX(snapshot_date)`，若當天快照還沒寫就會抓到昨天的持倉。所以腳本開頭先跑 `ensure_stockradar_snapshot()`：

1. health check `:6000` —— 沒開就記 log 並用既有最新快照（不中斷）
2. `POST /api/mimi/sync` 重跑 mimi 操作建議總表 parser → DB（含報酬率／燈號）
3. 查 DB 是否已有當天快照；沒有就 `POST /api/scheduler/report/run`，每 3s 輪詢、最多 ~180s

### D.1 資料源

```
資料源（horizon/data/config.json → sources，由 fetch_all_sources() 並發抓取）
│
├── 📡 RSS：50 個 feed（scrapers/rss.py）
│   ├── [macro-official]  總經官方 ×2      Federal Reserve press_all、Fed Speeches
│   ├── [semiconductor]   半導體產業 ×2    The Next Platform、DigiTimes
│   ├── [valuation]       估值方法 ×1      Damodaran Blog
│   ├── [market-view]     市場觀點 ×1      A Wealth of Common Sense
│   ├── [market-charts]   市場圖表 ×1      Charlie Bilello
│   ├── [ir-releases]     公司 IR ×1       AMD IR
│   └── [stock-news]      個股新聞 ×42     每檔一個 Google News RSS，帶 keywords 過濾、max_items=5
│         NVDA AMD AVGO TSM AAPL MSFT GOOGL AMZN META TSLA
│         PLTR ARM SMCI VRT ANET MU INTC LLY RDW IBM
│         VIAV SWKS NVTS OUST NVDL PDYN BKSY DJT WOLF ACMR
│         CRM ASX LTRX TXN FCEL SOFI QCOM SMH SE JBL SANM VT
│
├── 💬 Reddit：6 個 subreddit（scrapers/reddit.py，各有獨立門檻）
│     r/stocks           hot  min_score=100  limit=15
│     r/investing        hot  min_score=100  limit=15
│     r/options          hot  min_score=50   limit=10
│     r/thetagang        hot  min_score=30   limit=10
│     r/SecurityAnalysis hot  min_score=20   limit=10  (time=week)
│     r/wallstreetbets   hot  min_score=500  limit=10
│     （fetch_comments=3：每篇抓前 3 則留言）
│
└── 🚫 未啟用（程式碼支援但 config enabled=false）
      hackernews / telegram / twitter / openbb / ossinsight / github

個人化資料層（不屬於 sources，總結階段才讀）
├── 🗄️ stock-radar DB  backend/stock_valuation.db
│      表：holding_snapshots / holdings / mimi_watchlist → 我的持倉（Main／Mom）、watchlist、thesis
└── 📡 Finnhub API（summarizer._load_finnhub_context）
       /quote                即時股價 + 當日高低 + 漲跌幅
       /calendar/earnings    未來 30 天財報日曆（濾持倉）
       /stock/recommendation 分析師 Buy/Hold/Sell（key tickers：NVDA AVGO TSM AMD ARM AAPL META MSFT）
```

### D.2 過濾方式（pipeline 8 階段）

| # | 階段 | 做什麼 |
|---|------|--------|
| 1 | **抓取** | 並發抓所有啟用來源 → `ContentItem` list |
| 2 | **跨源去重** | 同 URL（正規化後）合併，保留內容最豐富的，併入其他來源的留言／metadata |
| 3 | **AI 評分** | GPT-4o-mini 給每篇 **0–10 分** + reason + summary + tags（批次、失敗重試 3 次） |
| 4 | **門檻過濾** | 一般 **≥ 6.0**；**個股 ticker feed（feed 名以 " News" 結尾）放寬到 ≥ 4.0** —— 確保持倉冷門新聞進得來 |
| 5 | **語意去重** | AI 判斷同主題，只留分數最高者，併入其餘留言 |
| 6 | **內容 enrich** | 第二次 AI pass：抽概念 → DuckDuckGo 查背景 → 補 `whats_new` / `why_it_matters` / `background` |
| 7 | **生成報告** | 僅 `zh-tw` 跑客製：載入持倉/watchlist → 載入 Finnhub → 分類報告 → 市場報告 → Portfolio 報告 |
| 8 | **分發** | 寫 `data/summaries/`、GitHub Pages、Email、Webhook |

時間窗過濾：`filtering.time_window_hours = 24`（週報由 CLI `--hours 168` 覆寫）。

### D.3 輸出

每天在 `horizon/data/summaries/<YYYY-MM-DD>/` 產出：

| 檔案 | 內容 |
|------|------|
| `📊 今日市場報告.md` | 今日一句話 + Macro 完整分析 + 財報表 + 半導體摘要 + 持倉更新（Main/Mom）+ 行動清單 |
| `📋 個股與產業.md` | 各分類個股與產業動態（含被過濾掉的低分項摘要） |
| `📊 Portfolio.md` | 持倉總表 + 每檔深度動態（含 Finnhub 即時報價） |

同步：逐檔 `cp -f`（失敗重試 5 次）→ Obsidian `~/Documents/arthurwang_DB/Digest/stock/<日期>/` **與** 鏡像 `~/.arthur-digest/stock/<日期>/`。

### D.4 Telegram 推播

**不是**走 Horizon 的 webhook（config 那欄是空的），而是 wrapper 腳本跑完後用 `awk`／`grep` 從報告萃取六段，`curl` 打 Telegram API：

1. `▌今日一句話` → 2. `📊 Macro`（前 6 行）→ 3. `💰 財報摘要`（表格前 5 行）→ 4. `🤖 AI／半導體`（前 5 行）→ 5. `📉 持倉今日漲跌`（從 Portfolio 總表撈，按漲跌排序前 12 檔）→ 6. `💡 行動清單`（前 6 行）+ token 使用量

週報標題改為 `🗓️ 整周復盤 — 週一~週五`。pipeline 非 0 退出時另發一則 `❌ 失敗` 並附 log 路徑。

> ⚠️ launchd 預設 C locale，bash 字串運算會按位元組切壞中文 → Telegram 回 400。
> 腳本開頭強制 `LANG=LC_ALL=en_US.UTF-8`。

---

## E. 規則型通知（用途：即時警報 + 例行績效）

**一句話**：不動腦、不花錢、不會壞的那一層。全部是寫死的門檻。

| 通知 | 觸發條件 | 資料源 |
|------|---------|--------|
| **股價異動** | `\|漲跌%\| ≥ 5.0` **或**（`\|漲跌%\| ≥ 3.0` **且** 量比 ≥ **2.5**） | `get_market_movers()` |
| — 分段規則 | 持倉／估值表非持倉分兩段，**各方向最多 5 檔**，依絕對漲跌排序 | — |
| **財報倒數** | 持倉股 **且** `status ≠ reported` **且** 剩餘天數 ∈ **{7, 3, 1, 0}** | `/api/earnings/calendar` |
| **收盤通知** | 台北 **二～五 05:30** 後，每天一次 | 每個組合 `period="daily"` 報告 + 風險旗標 |
| **週末復盤** | 台北 **六／日 05:30** 後，每 ISO 週一次 | 同上但 `period="weekly"` + 週線大盤快照 + 技術面 |

**去重**：所有事件都有 `event_id` 寫進 `data/telegram_notifications_seen.json`，冷卻 **24 小時**（週報 7 天）。

---

## F. AI 持倉卡預產（用途：給 A 當備料，不對外）

| 項目 | 值 |
|------|-----|
| 排程 | **每天 05:00**（launchd `com.arthurwang.stock-digest-portfolio`） |
| 腳本 | `~/Library/Scripts/stock-digest-portfolio.sh` |
| Log | `~/Library/Logs/stock-digest-portfolio/YYYY-MM-DD.log` |

> **2026-09-03 統一**：F 以前在前端有自己的「AI 持倉」分頁，而且卡片跑完會**再跑一次 claude**
> 做組合層「總體重點分析」（今天要處理哪幾檔／組合姿態／全組合風險）。那跟 A 講的是同一件事
> ——同一批持倉、同一天、兩套答案。現在：
> - `with_overview` 預設 **False**，F 只產逐檔卡（每天省一次 claude 呼叫）；補跑總覽用 `scripts/rebuild_ai_overview.py`。
> - 前端「AI 持倉」分頁刪除，併進 **「持倉復盤」**：`/api/war-room/review/{date}` 回傳 `ai_cards`，
>   對照表一列一檔（War Room 決策 × AI 卡評分／評級／建議），方向相反的標 **AI 反向**。
> - 買賣結論**只由 A 給**；F 是證據層。`/api/ai-report/*` 端點保留給腳本與舊連結。

流程：

1. health check `:6000` —— 沒開就直接放棄（A 改用既有快取或無卡）
2. `POST /api/scheduler/report/run` 刷新當天持倉快照／估值，輪詢至多 ~120s
3. `POST /api/scheduler/ai-report/run` 批次產卡（併發 **2**，claude CLI 慢，保守限流）
4. 輪詢至多 **20 分鐘**，務必趕在 05:30 之前產好

**輸出**：`data/ai_analysis/{TICKER}_{date}.json` 逐檔卡片 + `data/ai_reports/{date}.json` 當日彙整。
**不推 Telegram、不寫 Obsidian** —— 它只是快取。

---

# 第三部分：附錄

## 附錄一：全系統資料源總表

| 來源 | 提供什麼 | 誰在用 | 風險 |
|------|---------|-------|------|
| **yfinance** | 大盤／板塊報價（含 `as_of` 交易日）、6 個月 OHLCV 與技術指標、財報日、計分卡用的期間 high/low | A B C E | 會整批限流；B 有 30% 覆蓋率保護；A 取不到 `as_of` 時交易日改用推估並在報告標「（推估）」；**USD/TWD 一起被限流時台股會未折算 → 組合風險整節標不可信** |
| **自家 SQLite** `backend/stock_valuation.db` | 持倉快照、估值 `Valuation`、財測 `Forecast`、`mimi_watchlist` | A B D E F | 快照可能落後一天 → D 開頭有 `ensure_stockradar_snapshot()` |
| **`data/portfolio.csv`** | 持倉真實部位 | A B C E F | 排除 `CASH` / `ETORO` |
| **Obsidian** `投資/mimi操作建議總表.md` | 進場區／停損／倉位／論點 | A B C | launchd 下讀 `~/Documents` 會 TCC EDEADLK → fallback DB 副本 |
| **Google News RSS** | 大盤與個股新聞 | C（也是 D 的 42 個個股 feed） | 雜訊極多 → C 有四層過濾 |
| **RSS ×50 / Reddit ×6** | 外部媒體與散戶輿論 | D | 各源有獨立 `min_score` 門檻 |
| **Finnhub** | 即時報價、30 天財報日曆、分析師評級 | D | 60 req/min |
| **Alpha Vantage** | 備用財務數據 | D | **25 req/day**，需節省 |
| **`data/dcf/*.json`** | DCF 內在價值與安全邊際 | B | 只讀不重算，檔案舊了就沒訊號 |
| **Finance Digest**（Substack 投資筆記） | 質化敘事脈絡（TLDR + ticker 對應） | A B | 只當補充，SOP 明令不可取代技術面／估值 |
| **`data/agent/{journal,ledger,opportunities}`** | 昨日論點、30 天戰績、上次盡調結果 | A | 這是整套系統的「記憶」 |
| **`data/agent/positions/*.json`** | 每日持倉快照（跨帳戶合併） | A | **executed 對帳的唯一證據**；少於兩份時無法差分，決策只能停在 triggered |
| **`data/agent/exec_state.json`** | 決策五態執行狀態機 | A | 只有 executed/reconciled 允許報告寫「已執行」 |
| **`data/agent/pipeline/{date}.json`** | 每日各階段狀態、嘗試次數 | A B C G | 排程與重試的權威；`running` 卡 >90 分鐘視為死掉 |
| **`data/agent/manifest/{date}.json`** | 當日完整性 | G、portal | 鏡像副本 `~/.arthur-digest/stock/{date}/_manifest.json` 供 portal 讀 |

## 附錄二：過濾方式速查

| 管線 | 第一層（便宜、純程式） | 第二層（貴、走模型） |
|------|--------------------|-------------------|
| A 復盤 | 每個簡報區塊壓到字數上限：digest 1200 / 帳本 2800 / 財報 20 列 / 掃描回顧 6 列，逼總長 <12k | claude 自己選 3–6 檔深挖 |
| B 掃描 | 五個 bucket 的量化門檻 → `signal_score` 加權排名 → **top 20** | claude 逐檔盡調 |
| C 新聞 | 來源黑名單 → 標題正則 → 代號／公司名硬比對 → 30h 窗 + 指紋去重 | claude 單輪挑重點，輸出 **8–15 則** |
| D Digest | 各源 `min_score`（20～500）+ 跨源 URL 去重 | GPT 評分 **≥6.0**（個股 feed ≥4.0）→ 語意去重 → enrich |
| E 通知 | 純門檻（5% ／ 3%+2.5x ／ 財報 7-3-1-0 天） | 無 |
| **產出後（全管線共用）** | `report_guard`：執行宣稱／即時語氣／佔位句逐行掃描並改寫；`daily_manifest`：空殼檔判 partial | 無（純程式，不再叫第二個模型） |

## 附錄三：為什麼所有東西都寫兩份？

launchd 啟動的服務**讀寫 `~/Documents` 會被 macOS TCC 擋**（表現為 `OSError [Errno 11] Resource deadlock avoided`）。因此：

- **權威副本**：Obsidian vault `~/Documents/arthurwang_DB/…`（人看的）
- **鏡像副本**：`~/.arthur-digest/stock/{date}/`（**backend 與 portal 讀這裡**）

寫入一律用 `cp -f` + 重試（Python `shutil` / Node 直接寫會機率性失敗）。日後改同步邏輯時，**不要**在 launchd context 用 Python/Node 直接寫 `~/Documents`。

## 附錄四：關鍵檔案速查

| 角色 | 檔案 |
|------|------|
| 排程迴圈（A/B/C/E/G 的觸發者） | `backend/app/routers/notifications.py` → `telegram_notification_loop()` |
| **依賴式工作流狀態機** | `scripts/pipeline_state.py`（`STAGES` 是排程權威，`latest_start` 是最晚啟動時間） |
| **執行狀態機（觸發≠成交）** | `scripts/exec_state.py` |
| **時效標頭／幣別標註** | `scripts/report_meta.py` |
| **產出驗證（落地前守則）** | `scripts/report_guard.py` |
| **術語中文化** | `scripts/zh_terms.py` |
| **三層報告組裝** | `scripts/war_room_report.py` |
| **完整性 manifest** | `scripts/daily_manifest.py` |
| **組合層風險** | `scripts/portfolio_risk.py` |
| A 盤後復盤 | `scripts/war_room.py` + `agent/CLAUDE.md`（SOP） |
| A 的資料函式與輸出管線 | `scripts/daily_analysis.py`（`fetch_quotes` / `load_portfolio` / `parse_mimi_doc` / `score_yesterday` / `save_obsidian_note` / `send_telegram`） |
| A 的跨日記憶 | `scripts/decision_ledger.py` |
| B 機會掃描 | `scripts/market_scan.py` + `src/analysis/opportunity_scanner.py`（Stage-1） |
| C 市場新聞 | `scripts/market_news.py` |
| D 入口腳本 | `~/Library/Scripts/stock-digest.sh` ＝ 版控副本 `digest/stock/scripts/stock-digest.sh` |
| D 核心管線 | `digest/stock/horizon/src/orchestrator.py`、`src/ai/{summarizer,analyzer,enricher}.py` |
| D 來源設定 | `digest/stock/horizon/data/config.json` |
| F AI 卡批次 | `backend/app/routers/ai_report.py` + `~/Library/Scripts/stock-digest-portfolio.sh` |
| portal 讀取端 | `backend/app/routers/stock_digest.py`（讀鏡像目錄；今日市場報告／Portfolio／個股與產業 → 戰情室「市場日報」面板，盤後復盤／機會掃描 → /review） |
| stock-radar 日報層 | 2026-09-07 起每日報告頁併進戰情室：`frontend/src/components/DailyWire.jsx`（新聞線／市場日報兩面板）＋ `warRoomDaily.js`（日期對齊、策展觸發）；持倉復盤在 `pages/PostMarketReview.jsx`（`/api/war-room/review`，A 的三層報告 + F 的逐檔 AI 卡對照，`reviewMerge.js` 做合併與分歧判定）；Portfolio 頁只留 `WarRoomTeaser.jsx` 摘要卡 |

## 附錄五：手動補跑

```bash
cd ~/Projects/Arthur_App/stock-radar

uv run python scripts/war_room.py                  # A 盤後復盤
uv run python scripts/war_room.py --dry-run        #   只印簡報，不燒 claude
uv run python scripts/market_scan.py --limit 2     # B 小煙霧測試（省錢）
uv run python scripts/market_news.py --no-claude   # C 只抓新聞不策展（零成本）
uv run python scripts/decision_ledger.py           #   單獨看決策帳本（含新版績效統計）

# 可信度層（都可單獨跑，零 claude 成本）
uv run python scripts/report_meta.py                              # 現在的時效判定
uv run python scripts/exec_state.py                               # 決策執行狀態
uv run python scripts/exec_state.py --snapshot                    # 只抓一份持倉快照
uv run python scripts/exec_state.py --confirm FSLY --date 2026-08-20 --state executed --note "手動確認"
uv run python scripts/portfolio_risk.py                           # 組合層風險
uv run python scripts/daily_manifest.py --telegram                # 完整性檢查（缺件才發 Telegram）
uv run python scripts/pipeline_state.py                           # 今天各階段能不能跑、為什麼
uv run python scripts/report_guard.py "~/.arthur-digest/stock/2026-08-21/🎖️ War Room.md"   # 檢查既有報告

launchctl start com.arthurwang.stock-digest          # D 日報（24h）
launchctl start com.arthurwang.stock-digest-weekly   # D 整周復盤（168h）
launchctl start com.arthurwang.stock-digest-portfolio # F AI 卡預產
```

改 D 的腳本時：編輯 repo 版 → `cp digest/stock/scripts/stock-digest.sh ~/Library/Scripts/stock-digest.sh`（LaunchAgent 載入的是後者，repo 版是 source of truth）。

## 附錄六：2026-08-24 可信度改版 — 問題與對應

2026-08-23 對 War Room / Stock Digest / Portfolio 的抽查結論是
「管線設計完整，但**報告語氣比資料可信度更確定**，修正前不適合當可直接執行的交易指令」。
以下是每一項的對應處理：

| # | 原問題 | 對應 | 檔案 |
|---|--------|------|------|
| 1 | 8/21 宣稱 FSLY 已停損倉位歸零，隔天仍持有 40 股 | 五態執行狀態機 + 持倉快照對帳 + 落地前強制改寫 | `exec_state.py`、`report_guard.py` |
| 2 | 8/21 的報告 8/23 才產出，仍寫「今日執行」「明日」 | 時效標頭（交易日取自報價 `as_of`）+ BACKFILL/STALE 時態守則 | `report_meta.py`、`report_guard.py` |
| 3 | 缺資料時用「原文未提供」填版面；Options/DXY 無來源數字 | Horizon prompt 改成「沒資料整項省略」+ 落地前佔位句清除器；Options 無 OI/IV/Put-Call 就整段不產生 | `horizon/src/ai/summarizer.py` |
| 4 | 主報告連續缺檔、Market Scan 從未落地，畫面上看不出來 | 每日完整性 manifest → API `completeness` → portal 橫幅 + Telegram | `daily_manifest.py`、`stock_digest.py`、`DailyDigestPanel.jsx` |
| 5 | 853 行報告、DECISIONS JSON 塞在正文、26 檔 hold 重複敘述 | 三層報告；第一層程式組裝；JSON 只進 journal；hold 併表 | `war_room_report.py` |
| 6 | A/B/D 都給行動清單，三套語氣 | 只有 War Room 能給 buy/add/trim/stop；B 標「研究候選」、C 標 `facts_only`、D 改「觀察重點」 | `market_scan.py`、`market_news.py`、`summarizer.py`、`agent/CLAUDE.md` |
| 7 | 單股 25% 擋不住 AI 半導體因子集中 | 組合層風險（主題曝險 40.5%、多批合併、停損群聚、財報外溢、幣別） | `portfolio_risk.py` |
| 8 | hold 125 筆樣本偏斜、最佳 call 只看絕對漲幅 | 觸發日起算、超額報酬為主、分組統計勝率/MAE、建議 vs 成交、信心校準、樣本不足標記 | `decision_ledger.py` |
| 9 | A/B/C 固定時間會重疊；失敗當天永久放棄；D 週一跑週末內容 | 依賴式工作流（完成即接棒、固定時間只是最晚啟動）+ 重試/冷卻；D 週一停跑 | `pipeline_state.py`、`notifications.py`、launchd plist |

### 順帶修掉的既有缺陷

| 現象 | 影響 | 修法 |
|------|------|------|
| `get_csv_portfolio()` 的 holdings **不含 `weight_pct`**，War Room 簡報每一檔權重都印「—」 | 「單一持倉 ≤25%」這條硬規則等於沒有輸入，agent 只能寫「實際倉位未知」 | 簡報自己用 `market_value / total_value` 算，並附 Main＋Mom 合併權重 |
| 同一 ticker 多批持倉（Mom 的 TSLA 在兩個帳戶）逐列呈現 | 逐帳戶看會低估真實權重 | 簡報與風險模組都先合併，並標「⚠️2批」 |
| USD/TWD 匯率抓不到時，台股維持原幣進入組合 | 台股權重被放大約 30 倍（0050.TW 6.9% → 34.6%，組合停損風險 7% → 85%），而且畫面上看不出來 | `fx_unconverted` 高風險警告置頂 + 每條衍生數字標「不可信」；價格改印 `NT$… ⚠️未折算USD` |
| 未結決策追蹤逐檔列出 30 幾行（多數是距觸發很遠的 hold） | 真正該看的 2-3 行被淹沒 | 只列已觸發／非 hold／距觸發 ≤8%，其餘併成一行計數 |

### 仍待觀察

- **執行對帳需要時間熱身**：`data/agent/positions/` 從 2026-08-23 才開始累積，
  差分至少需要兩份快照。在那之前所有決策最多停在 `triggered` ——
  那代表「沒有證據」，不是「確定沒成交」；狀態表會自己標明這點。
- **`data/agent/opportunities/` 仍只有一份（2026-07-13）**：B 管線尚未穩定落地，
  改成依賴式工作流後才會開始每天有紀錄；在那之前 A 的「🔭 機會掃描回顧」會是空的。
- `/Users/yankesswang/Desktop/Projects/Arthur_App` 是 symlink → `~/Projects/Arthur_App`，
  腳本裡寫死 `Desktop/...` 的路徑都正確解析，**不是問題**。
- Obsidian 目的地已遷移：新 `Digest/stock/<日期>/`；舊 `投資/Stock Digest/<日期>/`（停在 2026-06-10）。
