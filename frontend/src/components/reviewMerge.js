// 「持倉復盤」把兩份東西併成一張表：
//   - 🎖️ War Room 的當日決策（journal.decisions，英文 action：stop_loss / trim / add …）
//   - 🧑‍🍳 05:00 預產的逐檔 AI 卡（ai_cards.cards，中文 action：分批買入 / 減碼 / 觀望 …）
// 兩邊對同一檔持倉各自有看法。買賣結論以 War Room 為準；AI 卡是證據層，
// 唯一值得多看一眼的是「兩邊方向相反」的那幾檔。這裡只做純資料合併與分歧判定，
// 不放元件（DailyDigestPanel 與 PostMarketReview 共用）。

// War Room 決策動作 → 中文標籤 + 方向（+1 加、-1 減、0 不動）
export const WR_ACTION_META = {
  stop_loss:  { label: '停損',     dir: -1 },
  exit:       { label: '出場',     dir: -1 },
  sell:       { label: '賣出',     dir: -1 },
  sell_short: { label: '放空',     dir: -1 },
  trim:       { label: '減碼',     dir: -1 },
  reduce:     { label: '減碼',     dir: -1 },
  add:        { label: '加倉',     dir: +1 },
  open:       { label: '建倉',     dir: +1 },
  buy:        { label: '買進',     dir: +1 },
  accumulate: { label: '分批買進', dir: +1 },
  hold:       { label: '持有',     dir: 0 },
  watch:      { label: '觀望',     dir: 0 },
  avoid:      { label: '迴避',     dir: 0 },
};

// AI 卡的建議是中文自由詞；只認得出方向就好
export const AI_ACTION_DIR = {
  '分批買入': +1, '買入': +1, '買進': +1, '分批買進': +1, '加碼': +1, '加倉': +1, '建倉': +1,
  '持有': 0, '觀望': 0,
  '減碼': -1, '減倉': -1, '停利': -1, '停損': -1, '賣出': -1, '出場': -1,
};

export const wrActionMeta = (a) =>
  WR_ACTION_META[a] || { label: a ? String(a).replace(/_/g, ' ') : '—', dir: 0 };

export const aiActionDir = (a) => (a in AI_ACTION_DIR ? AI_ACTION_DIR[a] : null);

// 'opposite'：一邊加、一邊減（值得看）；'lean'：一邊不動、一邊要動（輕微）；null：同調
export function divergenceOf(wrAction, aiAction) {
  if (!wrAction || !aiAction) return null;
  const a = wrActionMeta(wrAction).dir;
  const b = aiActionDir(aiAction);
  if (b === null) return null;
  if (a * b < 0) return 'opposite';
  if (a !== b) return 'lean';
  return null;
}

// 決策動作的處理順序：先出場／減碼，再加碼／建倉，最後不動的。同組內重倉在前。
const ACTION_RANK = { '-1': 0, '1': 1, '0': 2 };

/**
 * @param {Array}   decisions  journal.decisions
 * @param {Array}   cards      ai_cards.cards
 * @param {Array}   holdings   ai_cards.holdings（帶 weight_pct 的備援）
 * @param {object}  weights    ticker → 權重%（Portfolio 頁的即時值，優先）
 * @returns 每檔一列：{ ticker, weight, decision, card, divergence, portfolios }
 */
export function mergeDecisionsWithCards(decisions, cards, holdings, weights) {
  const byTicker = new Map();
  const up = (t) => String(t || '').toUpperCase();
  const row = (t) => {
    const key = up(t);
    if (!byTicker.has(key)) {
      byTicker.set(key, { ticker: key, weight: null, decision: null, card: null, divergence: null, portfolios: [] });
    }
    return byTicker.get(key);
  };
  // 同一檔可能 Main、Mom 都有；帳戶篩選要兩邊都認得，所以列出全部而不是只留一個
  const addPortfolio = (r, name) => {
    if (name && !r.portfolios.includes(name)) r.portfolios.push(name);
  };

  for (const d of decisions || []) {
    if (!d?.ticker) continue;
    const r = row(d.ticker);
    addPortfolio(r, d.portfolio);
    // 同一檔跨組合各有一筆時，保留較「重」的那筆（有動作的贏過 hold）
    if (!r.decision || (wrActionMeta(d.action).dir !== 0 && wrActionMeta(r.decision.action).dir === 0)) {
      r.decision = d;
    }
  }
  for (const c of cards || []) {
    if (c?.ticker) row(c.ticker).card = c;
  }

  const holdingWeight = new Map((holdings || []).map(h => [up(h.ticker), h.weight_pct]));
  const holdingPortfolio = new Map((holdings || []).map(h => [up(h.ticker), h.portfolio]));
  for (const r of byTicker.values()) {
    addPortfolio(r, holdingPortfolio.get(r.ticker));
    r.weight = weights?.[r.ticker] ?? holdingWeight.get(r.ticker) ?? null;
    r.divergence = divergenceOf(r.decision?.action, r.card?.action);
  }

  return [...byTicker.values()].sort((a, b) => {
    const ra = ACTION_RANK[String(wrActionMeta(a.decision?.action).dir)] ?? 3;
    const rb = ACTION_RANK[String(wrActionMeta(b.decision?.action).dir)] ?? 3;
    if (a.decision && !b.decision) return -1;
    if (!a.decision && b.decision) return 1;
    if (ra !== rb) return ra - rb;
    return (b.weight ?? -1) - (a.weight ?? -1);
  });
}

// ── 復盤全文裡的逐檔段落 ───────────────────────────────────────────────────
// 「異常持倉」與附錄「其餘持倉逐檔分析」都用同一種純文字格式：
//   ▍ TICKER
//   今日原因：…／Mimi：…／技術：…／✅ 明日：…
// 整份掃一遍，一檔一個物件（同一檔出現兩次以先出現的為準 —— 異常持倉在前）。
// 這是把「復盤全文」併進逐檔那一列的關鍵：讀者展開一列就看到這檔的全部論述，
// 不必再去全文裡找同一個代號第二次。
const BLOCK_TICKER_RE = /^[▍▌]\s*([A-Z0-9][A-Z0-9.-]{0,9})\s*$/;
const BLOCK_FIELD_RE = /^(今日原因|Mimi|技術)\s*[：:]\s*(.*)$/i;
const BLOCK_VERDICT_RE = /^(✅|⚠️|⚠|🔴|🟡|🟢|⛔|❌)?\s*明日\s*[：:]\s*(.*)$/u;

export function parseTickerBlocks(md) {
  const out = {};
  if (!md) return out;
  let cur = null;
  let last = null;
  const close = () => { if (cur && !out[cur.ticker]) out[cur.ticker] = cur; cur = null; last = null; };
  for (const raw of md.replace(/\r/g, '').split('\n')) {
    const line = raw.trimEnd();
    const tm = line.match(BLOCK_TICKER_RE);
    if (tm) { close(); cur = { ticker: tm[1].toUpperCase(), reason: '', mimi: '', tech: '', verdict: null, extra: [] }; continue; }
    if (!cur) continue;
    if (line.trim() === '' || /^#{1,4}\s/.test(line) || line === '---') { close(); continue; }
    const fm = line.match(BLOCK_FIELD_RE);
    if (fm) {
      const key = fm[1].toLowerCase() === 'mimi' ? 'mimi' : fm[1] === '今日原因' ? 'reason' : 'tech';
      cur[key] = fm[2].trim(); last = key; continue;
    }
    const vm = line.match(BLOCK_VERDICT_RE);
    if (vm) { cur.verdict = { emoji: (vm[1] || '').replace(/️/g, ''), text: vm[2].trim() }; last = 'verdict'; continue; }
    if (last === 'verdict' && cur.verdict) cur.verdict.text += ' ' + line.trim();
    else if (last) cur[last] += ' ' + line.trim();
    else cur.extra.push(line.trim());
  }
  close();
  return out;
}

// 依標題切出一段 markdown（到下一個同級或更高階標題為止），標題行本身不含。
// 用來把組合層的段落（大盤形勢／組合層風險／決策執行狀態）各自拿出來單獨放，
// 而不是整份全文再印一次。
export function sliceSection(md, titlePattern) {
  if (!md) return '';
  const lines = md.replace(/\r/g, '').split('\n');
  const re = titlePattern instanceof RegExp ? titlePattern : new RegExp(`^(#{1,3})\\s*${titlePattern}`);
  let i = lines.findIndex(l => re.test(l));
  if (i < 0) return '';
  const level = (lines[i].match(/^(#+)/) || ['', '##'])[1].length;
  const out = [];
  for (i += 1; i < lines.length; i++) {
    const h = lines[i].match(/^(#+)\s/);
    if (h && h[1].length <= level) break;
    out.push(lines[i]);
  }
  return out.join('\n').trim();
}
