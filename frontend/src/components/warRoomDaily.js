// 戰情室的「日報層」資料：市場新聞（/api/market-news）與市場日報（/api/stock-digest）。
//
// 2026-09-07 起原本獨立的「每日報告」頁（/briefing）併進戰情室：總覽＝戰情室本身、
// 市場新聞＝新聞線面板、市場個股＝市場日報面板；持倉復盤早已在 /review。
// 這裡只放 hook 與純函式，元件在 DailyWire.jsx（元件檔混出常數會讓 vite fast refresh 整檔重載）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from '../lib/apiClient';
import { C } from './opsTheme';

export const NEWS_API = '/api/market-news';
export const DIGEST_API = '/api/stock-digest';

// 戰情室看的是「本場」journal 日期；日報與新聞各有各的日期清單、常慢一兩天。
// 本場是最新一場 → 給最新一份（新聞可能比 journal 還新）；翻回歷史場次 → 給不晚於該日的最近一份。
export function pickReportDate(dates, activeDate, isLatestSession) {
  if (!dates?.length) return null;
  if (!activeDate || isLatestSession) return dates[0];
  if (dates.includes(activeDate)) return activeDate;
  return dates.find((d) => d < activeDate) || null; // dates 新→舊
}

function useReport(api, activeDate, isLatestSession) {
  const [dates, setDates] = useState(null); // null＝清單還沒到
  // 內容快取是 state（date → payload）：切日期時 data／loading 從快取「算」出來，
  // effect 只負責補抓缺的那天，不同步 setState（react-hooks/set-state-in-effect）。
  const [cache, setCache] = useState({});
  const [errors, setErrors] = useState({});
  const inflight = useRef(new Set());

  const reloadDates = useCallback(
    () =>
      axios
        .get(api)
        .then((r) => {
          const ds = r.data?.dates || [];
          setDates(ds);
          return ds;
        })
        .catch(() => {
          setDates([]);
          return [];
        }),
    [api]
  );
  useEffect(() => {
    reloadDates();
  }, [reloadDates]);

  const date = useMemo(
    () => pickReportDate(dates || [], activeDate, isLatestSession),
    [dates, activeDate, isLatestSession]
  );

  useEffect(() => {
    if (!date || cache[date] || errors[date] || inflight.current.has(date)) return undefined;
    const set = inflight.current;
    set.add(date);
    axios
      .get(`${api}/${date}`)
      .then((r) => setCache((c) => ({ ...c, [date]: r.data })))
      .catch(() => setErrors((e) => ({ ...e, [date]: `無法載入 ${date} 的資料` })))
      .finally(() => set.delete(date));
    return undefined;
  }, [api, date, cache, errors]);

  const invalidate = useCallback((d) => {
    setCache((c) => {
      if (!d) return {};
      const next = { ...c };
      delete next[d];
      return next;
    });
    setErrors({});
  }, []);

  const data = date ? cache[date] || null : null;
  const error = date ? errors[date] || '' : '';
  const loading = dates === null || (!!date && !data && !error);
  return { dates: dates || [], ready: dates !== null, date, data, loading, error, reloadDates, invalidate };
}

export function useDailyReports({ activeDate, isLatestSession }) {
  const news = useReport(NEWS_API, activeDate, isLatestSession);
  const digest = useReport(DIGEST_API, activeDate, isLatestSession);

  // 市場新聞：手動觸發今日策展（抓幾十檔新聞＋一輪 claude，約 2–5 分鐘），輪詢 status。
  const [status, setStatus] = useState('idle');
  const [runError, setRunError] = useState('');
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);
  const { invalidate, reloadDates } = news;
  const regenerate = useCallback(async () => {
    setStatus('running');
    setRunError('');
    try {
      await axios.post(`${NEWS_API}/run`);
      pollRef.current = setInterval(async () => {
        try {
          const { data } = await axios.get(`${NEWS_API}/status`);
          if (data.status === 'running') return;
          clearInterval(pollRef.current);
          setStatus('idle');
          if (data.error) {
            setRunError(`市場新聞產生失敗：${data.error}`);
            return;
          }
          invalidate();
          await reloadDates();
        } catch {
          clearInterval(pollRef.current);
          setStatus('idle');
        }
      }, 5000);
    } catch (e) {
      setStatus('idle');
      setRunError(e.response?.data?.detail || '觸發失敗');
    }
  }, [invalidate, reloadDates]);

  return { news: { ...news, status, runError, regenerate }, digest };
}

// ── 市場新聞 ─────────────────────────────────────────────────────────────────
export const NEWS_SCOPES = [
  { key: 'holding', label: '持倉' },
  { key: 'market', label: '大盤・宏觀' },
  { key: 'watchlist', label: '觀察名單' },
];
const SCOPE_RANK = { holding: 0, market: 1, watchlist: 2 };
export const normScope = (s) => (s in SCOPE_RANK ? s : 'market'); // 對不上的歸大盤，不能整則消失

// 語意色＋文字徽章，不做 color-alone
export const IMPACT_META = {
  利多: { label: '利多', color: C.long },
  利空: { label: '利空', color: C.short },
  中性: { label: '中性', color: C.muted },
};

// 排序：本場範圍內持有的標的最前（同一組內保留策展原順序），再依 持倉 → 大盤 → 觀察名單。
export function sortNews(items, heldTickers = {}) {
  const held = (it) => (it.tickers || []).some((t) => heldTickers[t]);
  return items
    .map((it, i) => ({ it, i }))
    .sort((a, b) => {
      const ha = held(a.it) ? 0 : 1;
      const hb = held(b.it) ? 0 : 1;
      if (ha !== hb) return ha - hb;
      const ra = SCOPE_RANK[normScope(a.it.scope)];
      const rb = SCOPE_RANK[normScope(b.it.scope)];
      if (ra !== rb) return ra - rb;
      return a.i - b.i;
    })
    .map((x) => x.it);
}

export function splitSentences(text) {
  const out = [];
  const re = /[^。]*。[」』）)]*|[^。]+$/g;
  let m;
  while ((m = re.exec(String(text || ''))) !== null) {
    const t = m[0].trim();
    if (t) out.push(t);
  }
  return out;
}

// ── 市場日報 ─────────────────────────────────────────────────────────────────
// 盤後復盤與機會掃描已在 /review 完整呈現，這裡不重畫。
const REVIEW_KEYS = new Set(['warroom', 'scan']);
export const digestSections = (data) => (data?.sections || []).filter((s) => !REVIEW_KEYS.has(s.key));

// 報告開頭都是同一組東西：`# 📊 …— 2026-09-01`、一塊 ```text 的交易日／資料截至／狀態、
// 再一段 `> 產出驗證`。抽出來合成一列，正文從真正的內容開始；認不出格式就整份原樣退回。
const META_LINE = /^([^：:]{1,12})[：:]\s*(.+?)\s*$/;

export function splitFrontMatter(md) {
  const src = String(md || '');
  const lines = src.split('\n');
  let i = 0;
  const skipBlank = () => {
    while (i < lines.length && !lines[i].trim()) i++;
  };
  if (lines[0]?.trim() === '---') {
    const end = lines.indexOf('---', 1);
    if (end > 0) i = end + 1;
  }
  skipBlank();
  const h1 = /^#\s+(.+?)\s*$/.exec(lines[i] || '');
  if (!h1) return { front: null, body: src };
  const title = h1[1].replace(/[*`]/g, '').trim();
  i++;
  skipBlank();

  const meta = [];
  if (/^```/.test(lines[i] || '')) {
    const close = lines.indexOf('```', i + 1);
    if (close > i) {
      const raw = lines.slice(i + 1, close).filter((l) => l.trim());
      const rows = raw.map((l) => META_LINE.exec(l.replace(/\u3000/g, ' ').trim())).filter(Boolean);
      if (rows.length && rows.length === raw.length) {
        rows.forEach((m) => meta.push({ k: m[1].replace(/\s+/g, ''), v: m[2] }));
        i = close + 1;
      }
    }
  }
  skipBlank();

  // 開頭引言只收「產出驗證」這種例行註記；其餘警語留在正文。
  const notes = [];
  while (/^>\s?/.test(lines[i] || '')) {
    const start = i;
    const buf = [];
    while (/^>\s?/.test(lines[i] || '')) {
      buf.push(lines[i].replace(/^>\s?/, '').trim());
      i++;
    }
    const text = buf.join(' ').trim();
    if (!/產出驗證/.test(text)) {
      i = start;
      break;
    }
    notes.push(text);
    skipBlank();
  }
  return { front: { title, meta, notes }, body: lines.slice(i).join('\n').trim() };
}

const metaVal = (meta, key) => meta.find((m) => m.k === key)?.v || '';
const shortDate = (s) => s.replace(/^\d{4}-/, '').replace(/-/g, '/');

// 摘要列只放「夠不夠新、可不可信」，完整原值留給展開的明細。
export function summaryChips(front) {
  if (!front) return [];
  const { meta, notes } = front;
  const chips = [];
  const asOf = (/^[^）]*）|^\S+ \S+/.exec(metaVal(meta, '資料截至')) || [''])[0].trim();
  if (asOf) chips.push({ k: '資料', v: shortDate(asOf).replace(/[（）]/g, ' ').replace(/\s+/g, ' ').trim() });
  else if (metaVal(meta, '交易日')) chips.push({ k: '交易日', v: shortDate(metaVal(meta, '交易日')) });
  const state = metaVal(meta, '狀態').split('（')[0].trim();
  if (state) chips.push({ k: '狀態', v: state });
  const verify = notes.find((n) => /產出驗證/.test(n));
  if (verify && !/^✅/.test(verify)) chips.push({ k: '', v: '產出驗證有異動', tone: C.amber });
  return chips;
}

export const charCount = (s) => String(s || '').replace(/\s/g, '').length;
