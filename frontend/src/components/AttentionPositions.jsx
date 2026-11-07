/**
 * AttentionPositionsPanel — 盤後復盤「第二層：異常持倉」的條列式排版。
 *
 * 原本整段丟給 markdown：逐檔的「今日原因／Mimi／技術／明日」只有換行沒有空行，
 * 被接成一整段文字牆。這裡改成：
 *   1. 依重要性排序，不再先分組合 —— 需執行 → 盯盤 → 論點延續；同一級內
 *      破停損 → 條件已觸發 → 風險高 → 距觸發近 的順序往前排；
 *   2. 兩欄卡片 —— 每檔一張，標題列：代號＋組合／動作／風險標籤＋現價／停損／距觸發，
 *      底下固定條列：明日（結論，第一行、用判定色）／原因／Mimi／技術，標籤欄對齊；
 *   3. 判定用色條＋圖示＋文字三重編碼；
 *   4. 內文 19px、行距 1.75；
 *   5. 各組合的開頭摘要／明日操作計劃／整體評估收在清單後面；其餘 hold 收進可展開的合併表。
 */
import { useMemo, useState } from 'react';
import { OctagonX, TriangleAlert, CircleCheck, ListTodo, ChevronDown, Search } from 'lucide-react';
import { C, alpha, actionMeta, RISK_META, fmt, signPct } from './opsTheme';
import { Panel, Badge, Empty, FilterChip } from './opsUi';
import { parseAttention } from './warRoomAttention';

const LEVEL_META = {
  act: { label: '需執行', hint: '明日要動作：停損／減碼／加碼', color: C.short, Icon: OctagonX, rank: 0 },
  watch: { label: '盯盤', hint: '持有但貼近觀察線或風險高，隨時可能升級', color: C.amber, Icon: TriangleAlert, rank: 1 },
  ok: { label: '論點延續', hint: '持有觀察，無需動作', color: C.long, Icon: CircleCheck, rank: 2 },
};
const LEVELS = ['act', 'watch', 'ok'];
const RISK_RANK = { high: 0, medium: 1, low: 2 };

function Stat({ label, value, color }) {
  return (
    <span className="inline-flex items-baseline gap-1 whitespace-nowrap">
      <span className="text-[15.5px]" style={{ color: C.dim }}>{label}</span>
      <span className="font-mono text-[18px] font-semibold tabular-nums" style={{ color: color || C.text }}>{value}</span>
    </span>
  );
}

// 一行條列：圓點 ＋ 固定寬度標籤 ＋ 內文。標籤欄對齊，眼睛直接往下掃「原因／Mimi／技術」那一欄。
function Bullet({ label, text, color, strong }) {
  if (!text) return null;
  return (
    <li className="flex items-start gap-3">
      <span aria-hidden="true" className="mt-[13px] h-2 w-2 shrink-0 rounded-full" style={{ background: color || C.muted }} />
      <span className="w-16 shrink-0 text-[18px] font-semibold leading-[1.75]" style={{ color: color || C.dim }}>{label}</span>
      <span
        className={`min-w-0 flex-1 text-[19px] leading-[1.75] ${strong ? 'font-semibold' : ''}`}
        style={{ color: strong ? C.text : C.sub }}
      >
        {text}
      </span>
    </li>
  );
}

function TickerCard({ item }) {
  const { t: tk, portfolio, decision, open, level, belowStop, triggered, dist } = item;
  const v = tk.verdict || { level: 'ok', text: '', action: null };
  const meta = LEVEL_META[level];
  const LevelIcon = meta.Icon;
  const action = decision?.action || v.action;
  const am = action ? actionMeta(action) : null;
  const rm = decision?.risk_level ? RISK_META[decision.risk_level] : null;
  const price = open?.last_close ?? null;
  const stop = decision?.stop ?? null;
  const distColor = dist == null ? C.dim : Math.abs(dist) < 3 ? C.short : Math.abs(dist) < 8 ? C.amber : C.dim;
  const trigger = decision?.trigger
    || (open?.trigger_price != null ? `${open.direction === 'above' ? '漲破' : '跌破'} ${open.trigger_price}` : null);

  return (
    <li
      className="flex flex-col border py-5 pl-4 pr-4"
      style={{ borderColor: C.border, borderLeft: `4px solid ${meta.color}`, background: alpha(C.ink, 0.015) }}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <LevelIcon size={22} aria-label={meta.label} style={{ color: meta.color, flexShrink: 0 }} />
        <span className="font-mono text-[25px] font-bold tracking-wide" style={{ color: C.text }}>{tk.ticker}</span>
        {portfolio && <Badge color={C.muted}>{portfolio}</Badge>}
        {am && <Badge color={am.color}>{am.label}</Badge>}
        {rm && (
          <span className="inline-flex items-center gap-1" title={`模型標的風險等級：${decision.risk_level}`}>
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: rm.color }} />
            <span className="font-mono text-[14px]" style={{ color: rm.color }}>{rm.label}</span>
          </span>
        )}
        {belowStop && <Badge color={C.short}>破停損</Badge>}
      </div>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
        <Stat label="現價" value={price != null ? fmt(price) : '—'} />
        <Stat label="停損" value={stop != null ? fmt(stop) : '—'} color={belowStop ? C.short : undefined} />
        <Stat
          label="距觸發"
          value={triggered ? '已觸發' : dist != null ? signPct(dist) : '—'}
          color={triggered ? C.short : distColor}
        />
      </div>

      <ul className="mt-3.5 space-y-2.5">
        <Bullet label="明日" text={v.text || '（模型沒有給明日判定）'} color={meta.color} strong />
        <Bullet label="原因" text={tk.fields.reason} />
        <Bullet label="Mimi" text={tk.fields.mimi} />
        <Bullet label="技術" text={tk.fields.tech} />
        {trigger && <Bullet label="觸發" text={trigger} />}
        {tk.extra.map((x, i) => <Bullet key={i} label="其他" text={x} />)}
      </ul>
    </li>
  );
}

// 明日操作計劃：真正的表格 —— 代號｜動作｜條件｜目標倉位｜備註，一列一檔。
// 報告原文是「LLY 減碼｜跌破1230已觸發｜目標降至5%以下｜多日未對帳」這種豎線分隔，
// 攤成表格才對得齊；窄螢幕橫向捲動，不讓整頁變寬。
const PLAN_HEADERS = ['代號', '動作', '條件', '目標倉位', '備註'];
function PlanList({ rows }) {
  const th = 'px-3 py-2 text-left font-mono text-[12.5px] font-semibold uppercase tracking-[0.14em] whitespace-nowrap';
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[16px]">
        <thead>
          <tr style={{ background: alpha(C.ink, 0.03) }}>
            {PLAN_HEADERS.map((h) => <th key={h} className={th} style={{ color: C.dim }}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const am = r.action ? actionMeta(r.action) : null;
            return (
              <tr key={`${r.label}-${i}`} style={{ borderTop: `1px solid ${C.border}` }}>
                <td className="px-3 py-2.5 align-top font-mono text-[16px] font-bold leading-relaxed" style={{ color: C.text }}>
                  <span className="flex flex-wrap gap-x-1.5 gap-y-0.5">
                    {r.isRest && <span className="font-normal" style={{ color: C.dim }}>其餘</span>}
                    {r.tickers.length ? r.tickers.map((x) => <span key={x} className="whitespace-nowrap">{x}</span>) : r.label}
                  </span>
                </td>
                <td className="px-3 py-2.5 align-top whitespace-nowrap">
                  {am ? <Badge color={am.color}>{am.label}</Badge> : <span style={{ color: C.muted }}>—</span>}
                  {r.actionNote && <span className="ml-1.5 text-[13.5px]" style={{ color: C.dim }}>{r.actionNote}</span>}
                </td>
                <td className="min-w-[160px] px-3 py-2.5 align-top leading-relaxed" style={{ color: C.sub }}>{r.condition || '—'}</td>
                <td className="min-w-[110px] px-3 py-2.5 align-top font-mono text-[15px] tabular-nums leading-relaxed" style={{ color: C.text }}>{r.target || '—'}</td>
                <td className="min-w-[180px] px-3 py-2.5 align-top leading-relaxed" style={{ color: C.dim }}>{r.note || '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HoldTable({ table }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-1.5 border px-2.5 py-2 font-mono text-[15px] uppercase tracking-wider transition-colors duration-200"
        style={{ borderColor: C.border, color: C.dim, background: alpha(C.ink, 0.015) }}
      >
        <ChevronDown size={14} style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 200ms' }} />
        {open ? '收起' : `其餘 ${table.rows.length} 檔維持（合併檢視，可展開）`}
      </button>
      {open && (
        <div className="mt-1.5 overflow-x-auto">
          <table className="w-full border-collapse text-[16.5px]">
            <thead>
              <tr>
                {table.headers.map((h) => (
                  <th
                    key={h}
                    className="px-2 py-2 text-left font-mono text-[13.5px] font-semibold uppercase tracking-[0.14em]"
                    style={{ color: C.dim, background: alpha(C.ink, 0.03) }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((cells, i) => (
                <tr key={`${cells[0]}-${i}`} style={{ borderTop: `1px solid ${C.border}` }}>
                  {cells.map((c, j) => (
                    <td
                      key={j}
                      className={`px-2 py-2 align-top leading-relaxed ${j === 0 ? 'font-mono font-bold' : ''}`}
                      style={{ color: j === 0 ? C.text : C.sub }}
                    >
                      {c || '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function MissingAlert({ tickers, decisionOf, openOf }) {
  return (
    <div className="flex items-start gap-2.5 border px-3 py-2.5" style={{ borderColor: `${alpha(C.amber, 0.333)}`, background: `${alpha(C.amber, 0.051)}` }}>
      <TriangleAlert size={20} aria-hidden="true" style={{ color: C.amber, flexShrink: 0, marginTop: 5 }} />
      <div className="min-w-0 flex-1">
        <div className="text-[18px] leading-[1.75]" style={{ color: C.sub }}>
          <span className="font-mono font-bold" style={{ color: C.amber }}>{tickers.length} 檔</span>
          達到展開條件，但模型沒有逐檔段落（SOP「每檔持倉都要逐一點評」可能被違反）。以下用當日決策資料補上。
        </div>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {tickers.map((t) => {
            const d = decisionOf(t);
            const o = openOf(t);
            const am = d ? actionMeta(d.action) : null;
            return (
              <li
                key={t}
                className="inline-flex items-center gap-2 border px-2.5 py-1.5 font-mono text-[15.5px]"
                style={{ borderColor: `${alpha(C.amber, 0.267)}`, color: C.text }}
                title={d?.thesis || ''}
              >
                <span className="font-bold">{t}</span>
                {am && <Badge color={am.color}>{am.label}</Badge>}
                {o?.status === 'triggered'
                  ? <span style={{ color: C.short }}>已觸發</span>
                  : o?.distance_pct != null && <span style={{ color: C.dim }}>距 {signPct(o.distance_pct)}</span>}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// 組合層：開頭摘要 ＋ 明日操作計劃 ＋ 整體評估（放在逐檔清單後面）。
function PortfolioSummary({ p }) {
  if (!p.intro.length && !p.plan.length && !p.assessment && !p.trailing.length) return null;
  return (
    <section className="space-y-3">
      <header>
        <h3 className="font-mono text-[18px] font-bold uppercase tracking-[0.14em]" style={{ color: C.accent }}>▍ {p.name}</h3>
        {p.intro.length > 0 && (
          <p className="mt-2.5 text-[19px] leading-[1.75]" style={{ color: C.sub }}>{p.intro.join(' ')}</p>
        )}
      </header>
      {p.plan.length > 0 && (
        <div className="border" style={{ borderColor: `${alpha(C.accent, 0.2)}` }}>
          <div
            className="flex items-center gap-2 border-b px-3 py-2.5 font-mono text-[14.5px] uppercase tracking-[0.14em]"
            style={{ borderColor: `${alpha(C.accent, 0.2)}`, color: C.accent }}
          >
            <ListTodo size={16} aria-hidden="true" /> 明日操作計劃 · {p.name}
          </div>
          <PlanList rows={p.plan} />
        </div>
      )}
      {p.assessment && (
        <div
          className="border-l-2 px-4 py-3.5 text-[19px] leading-[1.75]"
          style={{ borderColor: C.mimi, color: C.sub, background: alpha(C.ink, 0.015) }}
        >
          <span className="mr-2 font-semibold" style={{ color: C.mimi }}>整體評估</span>
          {p.assessment}
        </div>
      )}
      {p.trailing.length > 0 && (
        <div className="text-[17px] leading-[1.75]" style={{ color: C.dim }}>{p.trailing.join(' ')}</div>
      )}
    </section>
  );
}

function buildIndex(rows) {
  const m = new Map();
  for (const r of rows || []) {
    const t = String(r.ticker || '').toUpperCase();
    if (!t) continue;
    if (!m.has(t)) m.set(t, []);
    m.get(t).push(r);
  }
  return m;
}

export default function AttentionPositionsPanel({ md, decisions, openRows }) {
  const parsed = useMemo(() => parseAttention(md), [md]);
  const [level, setLevel] = useState('all');
  const [pf, setPf] = useState('all');
  const [query, setQuery] = useState('');

  const decisionIndex = useMemo(() => buildIndex(decisions), [decisions]);
  const openIndex = useMemo(() => buildIndex(openRows), [openRows]);
  const decisionOf = (ticker, portfolio) => {
    const list = decisionIndex.get(String(ticker).toUpperCase()) || [];
    if (!list.length) return null;
    const p = String(portfolio || '').toLowerCase();
    return list.find((d) => String(d.portfolio || '').toLowerCase() === p) || list[0];
  };
  const openOf = (ticker) => (openIndex.get(String(ticker).toUpperCase()) || [null])[0];

  // 全部標的攤平，補上結構化資料與重要性排序鍵。
  const items = useMemo(() => {
    const out = [];
    parsed.portfolios.forEach((p, pi) => {
      p.tickers.forEach((t, ti) => {
        const list = decisionIndex.get(t.ticker) || [];
        const pName = p.name.toLowerCase();
        const decision = list.find((d) => String(d.portfolio || '').toLowerCase() === pName) || list[0] || null;
        const open = (openIndex.get(t.ticker) || [null])[0];
        const action = decision?.action || t.verdict?.action;
        const price = open?.last_close ?? null;
        const stop = decision?.stop ?? null;
        const dist = open?.distance_pct ?? null;
        const lvl = t.verdict?.level || 'ok';
        const belowStop = price != null && stop != null && action !== 'sell_short' && price < stop;
        const triggered = open?.status === 'triggered';
        out.push({
          t, portfolio: p.name, decision, open, level: lvl, belowStop, triggered, dist,
          key: [
            LEVEL_META[lvl].rank,
            belowStop ? 0 : 1,
            triggered ? 0 : 1,
            RISK_RANK[decision?.risk_level] ?? 3,
            dist == null ? 99 : Math.abs(dist),
            pi, ti,
          ],
        });
      });
    });
    out.sort((a, b) => {
      for (let i = 0; i < a.key.length; i += 1) if (a.key[i] !== b.key[i]) return a.key[i] - b.key[i];
      return 0;
    });
    return out;
  }, [parsed, decisionIndex, openIndex]);

  const counts = useMemo(() => {
    const c = { act: 0, watch: 0, ok: 0 };
    for (const it of items) c[it.level] += 1;
    return c;
  }, [items]);

  const q = query.trim().toUpperCase();
  const visible = items
    .filter((it) => level === 'all' || it.level === level)
    .filter((it) => pf === 'all' || it.portfolio === pf)
    .filter((it) => !q || it.t.ticker.includes(q));
  const groups = LEVELS
    .map((l) => ({ l, meta: LEVEL_META[l], items: visible.filter((it) => it.level === l) }))
    .filter((g) => g.items.length);

  const total = items.length;
  const status = total
    ? <Badge color={counts.act ? C.short : C.accent}>{total} 檔展開・{counts.act} 需執行</Badge>
    : parsed.missing.length
      ? <Badge color={C.amber}>{parsed.missing.length} 檔缺逐檔段落</Badge>
      : <Badge color={C.long}>無需判斷項目</Badge>;

  return (
    <Panel label="異常持倉" status={status}>
      {!md ? (
        <Empty>該日報告沒有「異常持倉」段落</Empty>
      ) : (
        <div className="space-y-6">
          {total > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 border-b pb-3" style={{ borderColor: C.border }}>
              <FilterChip active={level === 'all'} onClick={() => setLevel('all')}>ALL {total}</FilterChip>
              {LEVELS.map((l) => {
                const m = LEVEL_META[l];
                const I = m.Icon;
                return (
                  <FilterChip key={l} active={level === l} color={m.color} onClick={() => setLevel(level === l ? 'all' : l)}>
                    <I size={13} aria-hidden="true" />{m.label} {counts[l]}
                  </FilterChip>
                );
              })}
              {parsed.portfolios.length > 1 && (
                <>
                  <span className="mx-1 h-4 w-px" style={{ background: C.border }} aria-hidden="true" />
                  {parsed.portfolios.map((p) => (
                    <FilterChip key={p.name} active={pf === p.name} onClick={() => setPf(pf === p.name ? 'all' : p.name)}>
                      {p.name} {p.tickers.length}
                    </FilterChip>
                  ))}
                </>
              )}
              <div className="ml-auto flex max-w-full items-center gap-1.5 border px-2 py-1" style={{ borderColor: C.border, background: C.deep }}>
                <Search size={13} aria-hidden="true" style={{ color: C.muted }} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="篩選代號…"
                  aria-label="篩選標的"
                  spellCheck={false}
                  className="w-36 min-w-0 bg-transparent font-mono text-[15px] uppercase tracking-wider outline-none"
                  style={{ color: C.text }}
                />
              </div>
              {parsed.criteria && (
                <div className="basis-full text-[15.5px] leading-relaxed" style={{ color: C.dim }}>
                  納入條件：{parsed.criteria} ｜ 排序：需執行 → 盯盤 → 論點延續；同級內破停損、已觸發、風險高、距觸發近者在前。
                </div>
              )}
            </div>
          )}

          {parsed.missing.length > 0 && (
            <MissingAlert tickers={parsed.missing} decisionOf={(t) => decisionOf(t)} openOf={openOf} />
          )}

          {total === 0 && !parsed.missing.length && <Empty>今日沒有需要展開判斷的持倉</Empty>}
          {total > 0 && !visible.length && <Empty>無符合條件</Empty>}

          {groups.map(({ l, meta, items: gi }) => {
            const I = meta.Icon;
            return (
              <section key={l} className="space-y-3">
                <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h3 className="inline-flex items-center gap-2 font-mono text-[19px] font-bold tracking-wide" style={{ color: meta.color }}>
                    <I size={20} aria-hidden="true" />{meta.label}
                    <span className="tabular-nums">{gi.length}</span>
                  </h3>
                  <span className="text-[16px]" style={{ color: C.dim }}>{meta.hint}</span>
                </header>
                <ol className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {gi.map((it) => <TickerCard key={`${it.portfolio}-${it.t.ticker}`} item={it} />)}
                </ol>
              </section>
            );
          })}

          {parsed.portfolios
            .filter((p) => pf === 'all' || p.name === pf)
            .map((p) => <PortfolioSummary key={p.name} p={p} />)}

          {parsed.holdTable && <HoldTable table={parsed.holdTable} />}
        </div>
      )}
    </Panel>
  );
}
