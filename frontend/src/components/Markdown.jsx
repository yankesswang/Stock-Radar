import { Component, useMemo } from 'react';
import { Link } from 'wouter';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { clean, headingId } from './markdownText';

// 防止單一筆內容 render 失敗就讓整頁白屏（App 無全域 error boundary）
class MarkdownBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err) { console.error('[Markdown] 渲染失敗:', err); }
  render() {
    if (this.state.failed) {
      return (
        <div className="text-[var(--accent-danger)] text-sm">
          ⚠️ 內容渲染失敗。
          <pre className="mt-2 whitespace-pre-wrap text-[var(--text-muted)] text-xs">{this.props.raw}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

// React children → 純文字（標題要拿它算 slug，strong 要拿它比對代號／動作詞）
function textOf(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node.props?.children !== undefined) return textOf(node.props.children);
  return '';
}

// ── 數字強調 ─────────────────────────────────────────────────────────────
// 報告裡真正要掃的是價位與百分比，但它們原本跟散文同一個灰。用 rehype plugin
// 在 hast 階段把它們切出來包成 <span class="md-num">，比在 React 端拆 children
// 安全：走得到每一層，也天然跳過 code/pre/a。
const SIGN = String.raw`[-+\u2212\uFF0B\uFF0D]?`;
const NUM_SRC = String.raw`(?:US\$|NT\$|\$)\s?` + SIGN + String.raw`\d[\d,]*(?:\.\d+)?[KMB]?|` +
                SIGN + String.raw`\d[\d,]*(?:\.\d+)?%`;
const NUM_SKIP_TAGS = new Set(['code', 'pre', 'a', 'script', 'style']);

function splitNumbers(value) {
  const re = new RegExp(NUM_SRC, 'g');
  const parts = [];
  let last = 0;
  let m;
  while ((m = re.exec(value)) !== null) {
    if (m.index > last) parts.push({ type: 'text', value: value.slice(last, m.index) });
    parts.push({
      type: 'element',
      tagName: 'span',
      properties: { className: ['md-num'] },
      children: [{ type: 'text', value: m[0] }],
    });
    last = m.index + m[0].length;
  }
  if (!parts.length) return null;
  if (last < value.length) parts.push({ type: 'text', value: value.slice(last) });
  return parts;
}

// 報告裡代號有兩種寫法：`**LLY**：建議…`（Strong 接手）和沒加粗的
// `🔴 ICHR（risk=high）：…`、以及整段只列代號的「其餘 13 檔」。後兩種位置固定，
// 認得出來就一起變成藥丸，不然同一份報告裡一半代號是藥丸、一半是散文。
const LEAD_TICKER_RE = /^([^\p{L}\p{N}]*)([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?=[（(：:])/u;
const BARE_TICKER_RE = /^[A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?$/;

const tickerNode = (t) => ({
  type: 'element',
  tagName: 'span',
  properties: { className: ['md-ticker-ref'] },
  children: [{ type: 'text', value: t }],
});

// 「0050.TW 0056.TW 00878.TW AAPL ARM …」整段都是代號才動它
function splitBareTickerLine(value) {
  const tokens = value.trim().split(/\s+/);
  if (tokens.length < 3 || !tokens.every(t => BARE_TICKER_RE.test(t))) return null;
  if (!tokens.some(t => /[A-Z]/.test(t))) return null;
  const parts = [];
  tokens.forEach((t, i) => {
    if (i) parts.push({ type: 'text', value: ' ' });
    parts.push(tickerNode(t));
  });
  return parts;
}

function splitLeadTicker(value) {
  const m = LEAD_TICKER_RE.exec(value);
  if (!m || NOT_TICKERS.has(m[2])) return null;
  const parts = [];
  if (m[1]) parts.push({ type: 'text', value: m[1] });
  parts.push(tickerNode(m[2]));
  const rest = value.slice(m[0].length);
  if (rest) parts.push(...(splitNumbers(rest) || [{ type: 'text', value: rest }]));
  return parts;
}

function rehypeReportText() {
  const walk = (node) => {
    if (!node.children?.length) return;
    // 條列開頭的代號只認第一個文字節點，掃全文會把 AI／ET／LIVE 全連出去
    const leadHost = node.tagName === 'li' || node.tagName === 'p';
    let out = null;
    node.children.forEach((child, i) => {
      if (child.type === 'element') {
        if (!NUM_SKIP_TAGS.has(child.tagName)) walk(child);
        if (out) out.push(child);
        return;
      }
      let split = null;
      if (child.type === 'text') {
        if (leadHost && i === 0) split = splitLeadTicker(child.value);
        if (!split && node.tagName === 'p' && node.children.length === 1) {
          split = splitBareTickerLine(child.value);
        }
        if (!split) split = splitNumbers(child.value);
      }
      if (split) {
        if (!out) out = node.children.slice(0, i);
        out.push(...split);
      } else if (out) {
        out.push(child);
      }
    });
    if (out) node.children = out;
  };
  return walk;
}

// ── 代號 ─────────────────────────────────────────────────────────────────
// 只認 **AAPL** 這種被 bold 起來的單一 token（報告產生器就是這樣寫的），不掃
// 自由文字 —— 掃全文的話 "AI"、"ET"、"LIVE" 全都會變成連到 /stock/LIVE。
const TICKER_RE = /^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$/;

// 沒有 knownTickers 時的保底：報告裡最常出現、長得像代號的縮寫
const NOT_TICKERS = new Set([
  'AI', 'ET', 'US', 'UK', 'EU', 'TW', 'JP', 'CN', 'HK', 'KR', 'ETF', 'LIVE', 'RR', 'OK', 'NA', 'TBD',
  'EPS', 'PE', 'PEG', 'ROE', 'ROIC', 'FCF', 'CAGR', 'YOY', 'QOQ', 'IPO', 'CEO', 'CFO', 'COO', 'CTO',
  'GDP', 'CPI', 'PPI', 'PCE', 'FOMC', 'FED', 'TAM', 'SAM', 'ARR', 'MRR', 'GAAP', 'DCF', 'WACC', 'IRR',
  'NAV', 'AUM', 'LTM', 'NTM', 'YTD', 'MTD', 'QTD', 'EBIT', 'CAPEX', 'OPEX', 'ADR', 'ADS', 'SEC', 'IRS',
  'FDA', 'FTC', 'DOJ', 'API', 'LLM', 'GPU', 'CPU', 'HBM', 'ASP', 'BPS', 'MOM', 'HOLD', 'BUY', 'SELL',
]);

// 動作詞在原排版裡全是同一種灰粗體 —— 停損跟加倉長得一模一樣。
const GREEN = 'var(--accent-success)';
const RED = 'var(--accent-danger)';
const AMBER = 'var(--accent-amber)';
const BLUE = 'var(--accent-cyan)';
const ACTION_TONE = {
  停損: RED, 出場: RED, 賣出: RED, 賣進: RED, 放空: RED, 清倉: RED,
  減碼: AMBER, 減倉: AMBER, 停利: BLUE,
  加倉: GREEN, 加碼: GREEN, 建倉: GREEN, 買進: GREEN, 買入: GREEN,
  分批買進: GREEN, 分批買入: GREEN, 分批進場: GREEN,
  持有: 'var(--text-secondary)', 觀望: 'var(--text-secondary)', 維持: 'var(--text-secondary)',
};

// 機會掃描報告的動作是英文（buy / accumulate / …），跟上面那組中文動作詞同一件事。
// 翻成中文再上色，跟入圍清單的徽章對得起來。
const OPP_ACTION = {
  buy:        { label: '買入', tone: GREEN },
  accumulate: { label: '加碼', tone: GREEN },
  sell_short: { label: '放空', tone: RED },
  watch:      { label: '觀察', tone: AMBER },
  avoid:      { label: '避開', tone: 'var(--text-muted)' },
};

function Strong({ children, tickers }) {
  const text = textOf(children).trim();

  const opp = OPP_ACTION[text.toLowerCase()];
  if (opp) {
    return (
      <strong className="md-action" style={{ color: opp.tone, background: `color-mix(in srgb, ${opp.tone} 12%, transparent)` }}>
        {opp.label}
      </strong>
    );
  }

  const tone = ACTION_TONE[text];
  if (tone) {
    return (
      <strong className="md-action" style={{ color: tone, background: `color-mix(in srgb, ${tone} 12%, transparent)` }}>
        {text}
      </strong>
    );
  }

  if (TICKER_RE.test(text)) {
    // tickers 是「一定連得出去」的白名單（持倉），單字母的 C 這種只能靠它救；
    // 名單外的照黑名單＋長度啟發式判斷，不然機會掃描的新標的全都連不出去。
    if (tickers?.has(text) || (text.length >= 2 && !NOT_TICKERS.has(text))) {
      return <Link href={`/stock/${encodeURIComponent(text)}`} className="md-ticker">{text}</Link>;
    }
    if (!NOT_TICKERS.has(text)) return <span className="md-ticker">{text}</span>;
  }

  return <strong>{children}</strong>;
}

// 機會掃描每檔的標題是「### 🆕 NVDA（NVIDIA）— momentum_breakout」，代號在最前面。
const HEAD_TICKER_RE = /^(?:🆕\s*)?([A-Z][A-Z0-9.-]{0,6})(?=[（(\s—-]|$)/;

// 掃描報告掃的是全市場，命中手上已有的部位時那不是新機會、是加減碼決策。
function HeldMark({ text, tickers }) {
  const t = HEAD_TICKER_RE.exec(text.trim())?.[1];
  if (!t || !tickers?.has(t)) return null;
  return (
    <span
      className="md-held"
      title="這檔已經在持倉裡，不是陌生開發的新標的"
      style={{
        marginLeft: '0.5em', padding: '0.1em 0.4em', borderRadius: 4,
        fontSize: '0.62em', fontWeight: 700, verticalAlign: 'middle',
        color: BLUE, background: `color-mix(in srgb, ${BLUE} 14%, transparent)`,
      }}
    >
      持有中
    </span>
  );
}

// 報告寫的進場/目標/停損是產出當下的計畫；沒有現價就不知道現在離那些價位多遠。
function HeadQuote({ text, quotes }) {
  const t = HEAD_TICKER_RE.exec(text.trim())?.[1];
  const q = t && quotes?.[t];
  if (!q || q.price == null) return null;
  const chg = q.change_percent;
  const tone = chg == null ? 'var(--text-secondary)' : chg >= 0 ? GREEN : RED;
  return (
    <span
      className="md-quote"
      title={q.last_date ? `${q.last_date} 收盤，非即時報價` : '非即時報價'}
      style={{ marginLeft: '0.5em', fontSize: '0.68em', fontWeight: 600, verticalAlign: 'middle' }}
    >
      <span style={{ color: 'var(--text-meta)' }}>現價 </span>
      <span style={{ color: 'var(--text-primary)' }}>{q.price.toFixed(2)}</span>
      {chg != null && (
        <span style={{ color: tone, marginLeft: '0.35em' }}>
          {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
        </span>
      )}
    </span>
  );
}

function buildComponents(tickers, markHeld, quotes) {
  return {
    h1: ({ children }) => <h1 id={headingId(textOf(children))}>{children}</h1>,
    h2: ({ children }) => <h2 id={headingId(textOf(children))}>{children}</h2>,
    h3: ({ children }) => {
      const text = textOf(children);
      return (
        <h3 id={headingId(text)}>
          {children}
          {markHeld && <HeldMark text={text} tickers={tickers} />}
          {quotes && <HeadQuote text={text} quotes={quotes} />}
        </h3>
      );
    },
    h4: ({ children }) => <h4 id={headingId(textOf(children))}>{children}</h4>,
    strong: ({ children }) => <Strong tickers={tickers}>{children}</Strong>,
    span: ({ className, children }) => {
      if (className !== 'md-ticker-ref') return <span className={className}>{children}</span>;
      const t = textOf(children);
      return <Link href={`/stock/${encodeURIComponent(t)}`} className="md-ticker">{t}</Link>;
    },
    a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
    // react-markdown v10 移除了 inline prop → 改用「有換行或 language- 才算區塊」判斷
    code: ({ className, children }) => {
      const isBlock = /language-/.test(className || '') || String(children ?? '').includes('\n');
      return <code className={isBlock ? 'md-block' : 'md-inline'}>{children}</code>;
    },
    pre: ({ children }) => <>{children}</>,   // code 自己就是區塊，pre 再包一層會變雙框
    table: ({ children }) => <div className="md-table-wrap"><table>{children}</table></div>,
    img: ({ alt, src, title }) => <img alt={alt || ''} src={src} title={title} loading="lazy" />,
  };
}

const REMARK = [remarkGfm];
const REHYPE = [rehypeReportText];

/**
 * 報告用的 markdown 排版。
 *
 * @param {string}   children  markdown 原文
 * @param {string[]} tickers   已知代號白名單，用來救單字母／黑名單誤傷的代號
 * @param {boolean}  compact   面板內的短片段用，字級與標題間距收窄
 * @param {boolean}  markHeld  代號標題上標「持有中」（只有機會掃描那種陌生開發的報告要）
 * @param {object}   quotes    ticker → { price, change_percent, last_date }，掛在代號標題上
 */
export default function Markdown({ children, tickers, compact = false, markHeld = false, quotes = null }) {
  const cleaned = useMemo(() => clean(children), [children]);
  // components 每次 render 都換新物件的話，兩萬字的樹會整棵重掛。quotes 是抓完才進來
  // 的一顆穩定物件（useQuotes 的 state），所以只會在報價到齊時重掛一次。
  const key = tickers?.length ? tickers.join(',') : '';
  const components = useMemo(
    () => buildComponents(key ? new Set(key.split(',')) : null, markHeld, quotes),
    [key, markHeld, quotes],
  );
  return (
    <MarkdownBoundary raw={cleaned}>
      <div className={`md-prose${compact ? ' md-compact' : ''}`}>
        <ReactMarkdown
          remarkPlugins={REMARK}
          rehypePlugins={REHYPE}
          components={components}
        >
          {cleaned}
        </ReactMarkdown>
      </div>
    </MarkdownBoundary>
  );
}
