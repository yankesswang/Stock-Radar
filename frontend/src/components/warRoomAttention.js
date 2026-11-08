// 戰情中心報告「第二層：異常持倉」的解析器。
//
// 報告是 markdown（scripts/war_room_report.py 組裝），但逐檔段落是模型寫的純文字：
//
//   ▍ TICKER
//   今日原因：…
//   Mimi：…
//   技術：…
//   ✅/⚠️/🔴 明日：…
//
// 這些行之間只有換行、沒有空行，markdown 會把整段接成一行讀不斷。
// 這裡把它切成結構化物件，讓前端用卡片＋欄位排版；純函式、無 React 依賴，可直接用 node 測。

const TICKER_RE = /^[▍▌]\s*([A-Z0-9][A-Z0-9.-]{0,9})\s*$/;
const FIELD_RE = /^(今日原因|Mimi|技術)\s*[：:]\s*(.*)$/i;
const VERDICT_RE = /^(✅|⚠️|⚠|🔴|🟡|🟢|⛔|❌)?\s*明日\s*[：:]\s*(.*)$/u;
// 標題有時是「明日操作計劃：」、有時是「**明日操作計劃**」（沒冒號）—— 兩種都要認得，
// 否則整段計劃會落到 trailing 變成一坨純文字。
const PLAN_HEAD_RE = /^\**\s*明日操作計劃[^：:]*[：:]?\s*\**\s*$/;
const ASSESS_RE = /^\**\s*整體評估\s*\**\s*[：:]\s*(.*)$/;
const PORTFOLIO_H_RE = /^###\s*【(.+?)】\s*$/;
const HOLD_H_RE = /^###\s*其餘持倉/;
const ATTENTION_H_RE = /^## 🔎 異常持倉.*$/m;
// 報告落地前會過 scripts/zh_terms.py，動作寫的是中文（減碼／停損／…）；
// 舊報告與模型偶爾漏網的英文 enum 也要照樣認得出來，所以兩種都收。
const ACTION_TOKEN_RE = /^(stop_loss|trim|add|open|exit|hold|watch|reduce|accumulate|buy|avoid|sell)\b/i;
const ZH_ACTION_TOKEN = [
  ['停損', 'stop_loss'], ['減碼', 'trim'], ['減倉', 'trim'], ['出清', 'exit'],
  ['出場', 'exit'], ['清倉', 'exit'], ['加倉', 'add'], ['加碼', 'add'],
  ['建倉', 'open'], ['分批買進', 'accumulate'], ['分批買入', 'accumulate'],
  ['買進', 'buy'], ['賣出', 'sell'], ['放空', 'sell_short'],
  ['維持', 'hold'], ['持有', 'hold'], ['觀察', 'watch'], ['迴避', 'avoid'],
];
const ZH_ACTION_HEAD_RE = new RegExp(`^(${ZH_ACTION_TOKEN.map(([zh]) => zh).join('|')})`);

/** 句首的動作詞（中英皆可）→ { action, rest }；不是動作開頭則回 null。 */
export function matchActionToken(text) {
  const t = (text || '').trim();
  const en = t.match(ACTION_TOKEN_RE);
  if (en) return { action: en[1].toLowerCase(), rest: t.slice(en[0].length).trim() };
  const zh = t.match(ZH_ACTION_HEAD_RE);
  if (zh) {
    const hit = ZH_ACTION_TOKEN.find(([w]) => w === zh[1]);
    return { action: hit[1], rest: t.slice(zh[0].length).trim() };
  }
  return null;
}

const LEVEL_BY_EMOJI = {
  '🔴': 'act', '⛔': 'act', '❌': 'act',
  '⚠': 'watch', '🟡': 'watch',
  '✅': 'ok', '🟢': 'ok',
};

/** 把第一＋二層的 markdown 切成「90 秒晨報（含標頭）」與「異常持倉」兩段。 */
export function splitBriefSections(md) {
  if (!md) return { briefMd: '', attentionMd: '' };
  const m = md.match(ATTENTION_H_RE);
  if (!m) return { briefMd: md, attentionMd: '' };
  return { briefMd: md.slice(0, m.index), attentionMd: md.slice(m.index) };
}

function inferLevel(text) {
  const t = text || '';
  if (/stop_loss|停損|出場|清倉|\bexit\b|\btrim\b|減碼|減倉/i.test(t)) return 'act';
  if (/risk_level\s*=?\s*high|風險等級\s*[=:：]?\s*高|盯盤|觀察線|警示|留意|貼近/i.test(t)) return 'watch';
  return 'ok';
}

const ZH_ACTION = [
  [/^停損/, 'stop_loss'], [/^減碼|^減倉/, 'trim'], [/^加碼|^加倉/, 'add'],
  [/^建倉/, 'open'], [/^出場|^清倉/, 'exit'], [/^持有|^維持|^hold/i, 'hold'],
];

/** 從「明日：」句子推測動作（journal 有值時以 journal 為準，這只是後備）。 */
export function inferAction(text) {
  // 「維持觀察」「繼續減碼」：前綴剝掉後才看得到真正的動作詞
  const stripped = (text || '').trim().replace(/^(維持|繼續|重申)\s*(?=[^\s])/, '');
  for (const t of [stripped, (text || '').trim()]) {
    const m = matchActionToken(t);
    if (m) return m.action;
    for (const [re, action] of ZH_ACTION) if (re.test(t)) return action;
  }
  return null;
}

function makeVerdict(emoji, text) {
  const e = (emoji || '').replace(/️/g, '');
  const body = (text || '').trim();
  return { level: LEVEL_BY_EMOJI[e] || inferLevel(body), text: body, action: inferAction(body) };
}

/** 「IBM stop_loss｜跌破232.21已觸發｜目標倉位0%｜備註」→ 結構化列。 */
export function parsePlanRow(line) {
  const cells = line.split(/\s*[｜|]\s*/).map((s) => s.trim());
  while (cells.length && cells[0] === '') cells.shift();
  while (cells.length && cells[cells.length - 1] === '') cells.pop();
  const head = cells[0] || '';
  let rest = cells.slice(1);
  let label = head;
  let action = null;
  let actionNote = '';
  // 「LLY 減碼」有空白、「LLY減碼」沒有——中文動作黏著代號時也要切得開
  const hm = head.match(/^(\S+)\s+(\S.*)$/)
    || head.match(new RegExp(`^([A-Z0-9][A-Z0-9.-]{0,9})\\s*(${ZH_ACTION_TOKEN.map(([zh]) => zh).join('|')}.*)$`));
  const headTok = hm ? matchActionToken(hm[2]) : null;
  const cellTok = rest.length ? matchActionToken(rest[0]) : null;
  if (headTok) {
    label = hm[1];
    action = headTok.action;
    actionNote = headTok.rest;
  } else if (cellTok) {
    action = cellTok.action;
    actionNote = cellTok.rest;
    rest = rest.slice(1);
  }
  const tickers = label
    .replace(/^其餘\s*[（(]?/, '')
    .replace(/[）)]\s*$/, '')
    .split(/[/、,]/)
    .map((s) => s.trim())
    .filter((s) => /^[A-Z0-9][A-Z0-9.-]{0,9}$/.test(s));
  return {
    raw: line,
    label,
    tickers,
    isRest: /^其餘/.test(label),
    action,
    actionNote,
    condition: rest[0] || '',
    target: rest[1] || '',
    note: rest.slice(2).join('｜'),
  };
}

/** GFM 表格 → { headers, rows }（只吃最單純的 | a | b | 形式）。 */
export function parseMdTable(md) {
  const rows = [];
  let headers = [];
  for (const raw of (md || '').split('\n')) {
    const line = raw.trim();
    if (!line.startsWith('|')) continue;
    if (/^\|?\s*:?-{2,}/.test(line)) continue;
    const cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((s) => s.trim());
    if (!headers.length) headers = cells;
    else rows.push(cells);
  }
  return { headers, rows };
}

/**
 * 解析「## 🔎 異常持倉」整段。
 * 回傳：{ criteria, missing[], portfolios[{ name, intro[], tickers[], plan[], assessment, trailing[] }],
 *         holdTable{ headers, rows } | null, tickerCount }
 */
export function parseAttention(md) {
  const res = { criteria: '', missing: [], portfolios: [], holdTable: null, tickerCount: 0 };
  if (!md) return res;
  const lines = md.replace(/\r/g, '').split('\n');

  let cur = null;      // 目前的組合
  let tk = null;       // 目前的逐檔段落
  let lastField = null;
  let state = 'pre';   // pre | intro | ticker | after | plan | assess
  let holdLines = null;

  const finishTicker = () => {
    if (tk && cur) cur.tickers.push(tk);
    tk = null;
    lastField = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (ATTENTION_H_RE.test(line) || line === '---') continue;
    if (holdLines) { holdLines.push(line); continue; }

    const ph = line.match(PORTFOLIO_H_RE);
    if (ph) {
      finishTicker();
      cur = { name: ph[1].trim(), intro: [], tickers: [], plan: [], assessment: '', trailing: [] };
      res.portfolios.push(cur);
      state = 'intro';
      continue;
    }
    if (HOLD_H_RE.test(line)) { finishTicker(); holdLines = [line]; continue; }

    if (line.startsWith('>')) {
      const body = line.replace(/^>\s*/, '');
      const mm = body.match(/沒有逐檔段落[：:]\s*([A-Z0-9.,\-\s]+?)(?:\u3000|\s*[（(]|$)/);
      if (mm) { res.missing = mm[1].split(/[,\s]+/).map((s) => s.trim()).filter(Boolean); continue; }
      const cm = body.match(/^納入條件[：:]\s*(.*)$/);
      if (cm) { res.criteria = cm[1].trim(); continue; }
      if (cur && state === 'intro') cur.intro.push(body);
      continue;
    }
    if (!cur) continue;

    const tm = line.match(TICKER_RE);
    if (tm) {
      finishTicker();
      tk = { ticker: tm[1].toUpperCase(), fields: { reason: '', mimi: '', tech: '' }, extra: [], verdict: null };
      state = 'ticker';
      continue;
    }
    if (PLAN_HEAD_RE.test(line)) { finishTicker(); state = 'plan'; continue; }
    const am = line.match(ASSESS_RE);
    if (am) { finishTicker(); state = 'assess'; cur.assessment = am[1].trim(); continue; }

    if (line.trim() === '') {
      if (state === 'ticker') { finishTicker(); state = 'after'; }
      else if (state === 'plan' && cur.plan.length) state = 'after';
      else if (state === 'assess') state = 'after';
      continue;
    }

    if (state === 'ticker' && tk) {
      const fm = line.match(FIELD_RE);
      if (fm) {
        const key = fm[1].toLowerCase() === 'mimi' ? 'mimi' : fm[1] === '今日原因' ? 'reason' : 'tech';
        tk.fields[key] = fm[2].trim();
        lastField = key;
        continue;
      }
      const vm = line.match(VERDICT_RE);
      if (vm) { tk.verdict = makeVerdict(vm[1], vm[2]); lastField = 'verdict'; continue; }
      if (lastField === 'verdict' && tk.verdict) tk.verdict.text += ' ' + line.trim();
      else if (lastField) tk.fields[lastField] += ' ' + line.trim();
      else tk.extra.push(line.trim());
      continue;
    }
    if (state === 'plan') { cur.plan.push(parsePlanRow(line)); continue; }
    if (state === 'assess') { cur.assessment += ' ' + line.trim(); continue; }
    if (state === 'intro') { cur.intro.push(line.trim()); continue; }
    cur.trailing.push(line.trim());
  }
  finishTicker();

  if (holdLines) {
    const t = parseMdTable(holdLines.join('\n'));
    if (t.rows.length) res.holdTable = t;
  }
  res.tickerCount = res.portfolios.reduce((n, p) => n + p.tickers.length, 0);
  return res;
}

/**
 * 附錄（第三層）也有同樣的逐檔純文字段落 —— 交給 markdown 之前先補結構：
 * `▍ TICKER` → h4、欄位行 → 條列、明日操作計劃的每一列 → 條列、整體評估 → 引用。
 */
export function normalizeTickerBlocks(md) {
  if (!md) return '';
  const out = [];
  let inBlock = false;
  let inPlan = false;
  for (const raw of md.replace(/\r/g, '').split('\n')) {
    const line = raw.trimEnd();
    const tm = line.match(TICKER_RE);
    if (tm) { inBlock = true; inPlan = false; out.push('', `#### ${tm[1]}`, ''); continue; }
    if (PLAN_HEAD_RE.test(line)) { inBlock = false; inPlan = true; out.push('', '**明日操作計劃**', ''); continue; }
    const am = line.match(ASSESS_RE);
    if (am) { inBlock = false; inPlan = false; out.push('', `> **整體評估**：${am[1]}`, ''); continue; }
    if (line.trim() === '') { inBlock = false; inPlan = false; out.push(''); continue; }
    if (inBlock) {
      const fm = line.match(FIELD_RE);
      if (fm) { out.push(`- **${fm[1]}**：${fm[2]}`); continue; }
      const vm = line.match(VERDICT_RE);
      if (vm) { out.push(`- ${vm[1] ? `${vm[1]} ` : ''}**明日**：${vm[2]}`); continue; }
      out.push(`- ${line.trim()}`);
      continue;
    }
    if (inPlan) { out.push(`- ${line.trim()}`); continue; }
    out.push(line);
  }
  return out.join('\n');
}
