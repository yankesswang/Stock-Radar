/**
 * StockPeers — 單檔的完整同業比較（/stock/:ticker/peers）。
 *
 * 個股頁只放摘要卡（components/PeerValuationSummary.jsx），完整內容在這一頁。
 *
 * 指標目錄由後端給（44 個，取自 stockanalysis 的 key statistics），分八組：
 * 估值倍數 → 成長預估 → 獲利品質 → 資本報酬 → 財務健康 → 股東回饋 → 股價與風險 → 分析師。
 * 一次全攤是四十幾列＋四十幾欄，所以照 Seeking Alpha 的做法用組別切換：
 * 選一組，上面看「本檔 vs 同業中位」的對照，下面同一組指標看逐檔明細 ——
 * 一個選擇器驅動兩個視圖，不會出現「上面在講獲利、下面在列估值」。
 *
 * 這裡沒有散點圖：舊版每個指標畫一條軸散一排無標籤的灰點，哪個點是哪一檔要 hover
 * 才知道。同樣的資訊用「中位數 / p25–p75 / 排第幾」講得更清楚，要看是哪幾檔就看明細表。
 */
import { useState, useEffect, useMemo } from 'react';
import { useRoute, Link } from 'wouter';
import axios from '../lib/apiClient';
import {
  Loader2, Users, Info, TrendingUp, TrendingDown,
} from 'lucide-react';
import StockPageHeader, { StockPageFallback } from '../components/StockPageHeader';
import {
  MIN_RANK_N, fmtNum, fmtMoney, fmtCap, fmtDiff, diffTone,
} from '../components/peerFormat';

const toneColor = (tone) =>
  (tone == null ? 'var(--text-muted)' : tone ? 'var(--accent-success-text)' : 'var(--accent-amber-text)');

// ── 指標對照表（選中那一組）──────────────────────────────────────────────────

function StatRow({ stat, asOf }) {
  const {
    label, unit, target, median, p25, p75, n, rank, rank_total: total, source,
    horizon, horizon_mix: mix,
  } = stat;
  const rankable = n >= MIN_RANK_N && rank != null;
  const stale = source === 'statistics';
  // 同業裡有的是 3Y 預估、有的是 5Y（來源在 2026 年中改過欄位名），
  // 中位數就是混著算的 —— 這件事要寫在臉上，不能讓人以為是同一個期間
  const mixed = mix && Object.keys(mix).length > 1;

  return (
    <tr className="border-t border-[var(--glass-border)]">
      <td className="py-2 pr-2 text-[12.5px] font-semibold text-[var(--text-primary)]">
        {label}
        {horizon && (
          <span className="ml-1 rounded bg-[var(--bg-tertiary)] px-1 align-middle text-[9.5px] font-bold text-[var(--text-meta)]"
                title={`本檔是 ${horizon} 預估`}>{horizon}</span>
        )}
        {mixed && (
          <span className="ml-1 cursor-help align-middle text-[9.5px] font-bold text-[var(--accent-amber-text)]"
                title={`同業裡 ${Object.entries(mix).map(([k, v]) => `${v} 檔 ${k}`).join('、')} —— 資料來源在 2026 年中把「5Y 預估」改名成「3Y 預估」，舊快照只有 5Y。中位數是混著算的。`}>
            ⚠ 期間不一
          </span>
        )}
        {stale && (
          <span className="ml-1.5 cursor-help align-middle text-[9.5px] font-normal text-[var(--text-muted)]"
                title={`這個指標來自 statistics 快照（${asOf?.statistics ?? '較舊'}），比報價那批（${asOf?.metrics ?? '—'}）舊`}>
            ({asOf?.statistics?.slice(5) ?? '舊'})
          </span>
        )}
      </td>
      <td className="px-2 py-2 text-right font-mono text-[13px] font-bold tabular-nums text-[var(--text-primary)]">
        {fmtNum(target, unit)}
      </td>
      <td className="px-2 py-2 text-right font-mono text-[12.5px] tabular-nums text-[var(--text-secondary)]">
        {fmtNum(median, unit)}
      </td>
      <td className="px-2 py-2 text-right font-mono text-[12.5px] font-semibold tabular-nums"
          style={{ color: toneColor(diffTone(stat)) }}>
        {fmtDiff(stat)}
      </td>
      <td className="hidden px-2 py-2 text-right font-mono text-[11.5px] tabular-nums text-[var(--text-muted)] sm:table-cell">
        {p25 == null || p75 == null || n < 4 ? '—' : `${fmtNum(p25, unit)} – ${fmtNum(p75, unit)}`}
      </td>
      <td className="whitespace-nowrap py-2 pl-2 text-right text-[11.5px]">
        {rankable ? (
          <span className="font-mono tabular-nums text-[var(--text-secondary)]">
            <b className="text-[13px] font-bold text-[var(--text-primary)]">{rank}</b>
            <span className="text-[var(--text-muted)]"> / {total}</span>
          </span>
        ) : rank == null && n >= MIN_RANK_N ? (
          <span className="text-[var(--text-muted)]" title="中性指標，沒有「愈高愈好」的方向，所以不排名">中性</span>
        ) : (
          <span className="text-[var(--text-muted)]" title={`只有 ${n} 檔同業有這個欄位，樣本不足以排名`}>
            樣本 {n} 檔
          </span>
        )}
      </td>
    </tr>
  );
}

function StatTable({ rows, asOf }) {
  return (
    <table className="w-full">
      <thead>
        <tr className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
          <th className="pb-1 pr-2 text-left font-semibold">指標</th>
          <th className="px-2 pb-1 text-right font-semibold">本檔</th>
          <th className="px-2 pb-1 text-right font-semibold">同業中位</th>
          <th className="px-2 pb-1 text-right font-semibold"
              title="倍數看相對差（%），百分比型指標看百分點差（pp）">vs 中位</th>
          <th className="hidden px-2 pb-1 text-right font-semibold sm:table-cell"
              title="同業的常態區間：去掉最低與最高的四分之一之後剩下的範圍">
            同業區間 p25–p75
          </th>
          <th className="pb-1 pl-2 text-right font-semibold">排名</th>
        </tr>
      </thead>
      <tbody>{rows.map(s => <StatRow key={s.key} stat={s} asOf={asOf} />)}</tbody>
    </table>
  );
}

// ── 同業推算合理價 ────────────────────────────────────────────────────────────

function ImpliedCard({ title, fair, diffPct, sub }) {
  if (fair == null) {
    return (
      <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
        <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{title}</p>
        <p className="mt-2 text-sm text-[var(--text-muted)]">缺 EPS 或同業倍數，推不出來</p>
      </div>
    );
  }
  const under = diffPct != null && diffPct < 0;
  return (
    <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">{title}</p>
        {diffPct != null && (
          <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold ${
            under
              ? 'bg-[rgba(5,150,105,0.14)] text-[var(--accent-success-text)]'
              : 'bg-[rgba(220,38,38,0.12)] text-[var(--accent-danger-text)]'
          }`}>
            {under ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
            {under ? '低估' : '高估'}
          </span>
        )}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-2xl font-bold tabular-nums">{fmtMoney(fair)}</span>
        {diffPct != null && (
          <span className="text-sm font-semibold"
                style={{ color: under ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
            {diffPct > 0 ? '+' : ''}{diffPct}%
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">{sub}</p>
    </div>
  );
}

// ── 逐檔明細表（欄位＝選中那一組的指標）────────────────────────────────────────

// 不管看哪一組，前三欄固定 —— 沒有這三欄就認不出在看誰
const FIXED = [
  { key: '__cap', label: 'Mkt Cap', get: p => p.market_cap, fmt: fmtCap },
  { key: '__px', label: 'Price', get: p => p.current_price, fmt: fmtMoney },
];

function PeerTable({ peers, ticker, metrics }) {
  const [sortKey, setSortKey] = useState('__cap');
  const [sortDir, setSortDir] = useState('desc');

  const cols = useMemo(() => [
    ...FIXED,
    ...metrics.map(m => ({
      key: m.key, label: m.label, unit: m.unit,
      get: p => p.values?.[m.key],
      fmt: (v, peer) => {
        const h = peer?.horizons?.[m.key];
        return h
          ? <>{fmtNum(v, m.unit)}<span className="ml-1 text-[9.5px] text-[var(--text-muted)]">{h}</span></>
          : fmtNum(v, m.unit);
      },
    })),
  ], [metrics]);

  // 換組之後排序欄位可能已經不在表上 —— 退回市值
  const activeSort = cols.some(c => c.key === sortKey) ? sortKey : '__cap';

  const sorted = useMemo(() => {
    const col = cols.find(c => c.key === activeSort) || cols[0];
    return [...peers].sort((a, b) => {
      const av = col.get(a);
      const bv = col.get(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return sortDir === 'asc' ? av - bv : bv - av;
    });
  }, [peers, cols, activeSort, sortDir]);

  const onSort = (key) => {
    if (activeSort === key) setSortDir(d => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--glass-border)]">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--glass-border)] bg-[var(--bg-secondary)]">
            <th className="sticky left-0 z-10 bg-[var(--bg-secondary)] px-3 py-2.5 text-left text-xs font-semibold text-[var(--text-muted)]">
              Ticker
            </th>
            {cols.map(col => (
              <th key={col.key} onClick={() => onSort(col.key)}
                  className={`cursor-pointer select-none whitespace-nowrap px-3 py-2.5 text-right text-xs font-semibold transition-colors hover:text-[var(--text-primary)] ${
                    activeSort === col.key ? 'text-[var(--accent-cyan)]' : 'text-[var(--text-muted)]'
                  }`}>
                {col.label}{activeSort === col.key && (sortDir === 'desc' ? ' ↓' : ' ↑')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(peer => (
            <tr key={peer.ticker}
                className={`border-b border-[var(--glass-border)] transition-colors last:border-0 ${
                  peer.is_target
                    ? 'bg-[rgba(0,82,255,0.06)] shadow-[inset_2px_0_0_var(--accent-cyan)]'
                    : 'hover:bg-[var(--bg-secondary)]'
                }`}>
              <td className={`sticky left-0 z-10 px-3 py-2.5 ${
                peer.is_target ? 'bg-[#eef3ff]' : 'bg-[var(--bg-primary)]'
              }`}>
                <Link href={`/stock/${peer.ticker}`}
                      className={`font-bold transition-colors hover:text-[var(--accent-cyan)] ${
                        peer.is_target ? 'text-[var(--accent-cyan)]' : 'text-[var(--text-primary)]'
                      }`}>
                  {peer.ticker}
                </Link>
                {peer.is_target && (
                  <span className="ml-1.5 text-[10px] font-semibold text-[var(--accent-cyan)]">本檔</span>
                )}
                <span className="block max-w-[140px] truncate text-[10px] text-[var(--text-muted)]">
                  {peer.company_name}
                </span>
              </td>
              {cols.map(col => {
                const v = col.get(peer);
                return (
                  <td key={col.key} className="px-3 py-2.5 text-right font-mono text-xs tabular-nums">
                    {v == null ? <span className="text-[var(--text-muted)]">—</span> : col.fmt(v, peer)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="px-3 py-2 text-[10.5px] text-[var(--text-muted)]">
        點欄位標題排序 · 藍色列＝{ticker} · 點代號可跳到該檔 · 欄位跟著上面選的組別換
      </p>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function StockPeers() {
  const [, params] = useRoute('/stock/:ticker/peers');
  const ticker = params?.ticker?.toUpperCase() || '';

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [group, setGroup] = useState('valuation');

  useEffect(() => {
    if (!ticker) return;
    let alive = true;
    setLoading(true);
    axios.get(`/api/compare/peers/${ticker}`)
      .then(r => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [ticker]);

  // 本檔沒有值的指標不列 —— 一整列「—」對照「—」沒有資訊
  const shown = useMemo(
    () => (data?.stats || []).filter(s => s.n > 0 && s.target != null),
    [data],
  );
  const groups = useMemo(
    () => (data?.groups || [])
      .map(g => ({ ...g, n: shown.filter(s => s.group === g.key).length }))
      .filter(g => g.n > 0),
    [data, shown],
  );
  const activeGroup = groups.some(g => g.key === group) ? group : groups[0]?.key;
  const rows = useMemo(() => shown.filter(s => s.group === activeGroup), [shown, activeGroup]);
  const groupMeta = groups.find(g => g.key === activeGroup);

  const im = data?.implied || {};

  return (
    <div className="container animate-fade-in space-y-6 py-10">
      <StockPageHeader
        ticker={ticker}
        meta={data ? (
          <>
            {data.industry} · {data.peer_count - 1} 檔同業 · {shown.length} 個指標（本檔不計入中位數與區間）
            {/* 資料多舊要寫在臉上 —— 兩張表來自兩條不同節奏的管線 */}
            {data.as_of && (
              <span className="mt-0.5 block text-[11px]">
                倍數／報酬率 {data.as_of.metrics ?? '—'}（每週管線）
                {data.as_of.statistics && data.as_of.statistics !== data.as_of.metrics && (
                  <> · 利潤率／成長／評分 {data.as_of.statistics}（每日補抓管線輪到才更新）</>
                )}
              </span>
            )}
          </>
        ) : '載入中…'}
      />

      {loading && (
        <div className="glass flex items-center justify-center gap-3 rounded-xl p-16 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-cyan)]" />
          <span className="text-sm">載入同業資料…</span>
        </div>
      )}

      {!loading && !data && (
        <div className="glass rounded-xl p-12 text-center text-sm text-[var(--text-muted)]">
          {ticker} 不在資料庫裡
        </div>
      )}

      {!loading && data && data.peer_count <= 1 && (
        <div className="glass rounded-xl p-12 text-center text-sm text-[var(--text-muted)]">
          {data.industry} 目前只有這一檔在追蹤，沒有同業可比。
        </div>
      )}

      {!loading && data && data.peer_count > 1 && (
        <>
          {/* 1. 同業推算合理價 */}
          <section className="glass rounded-xl p-5">
            <h2 className="text-sm font-bold text-[var(--text-primary)]">同業推算合理價</h2>
            <p className="mb-3 mt-1 flex items-start gap-1.5 text-[11px] leading-relaxed text-[var(--text-meta)]">
              <Info size={12} className="mt-0.5 shrink-0" />
              <span>
                {im.peer_median_pe == null ? (
                  <>這個產業目前沒有同業有 P/E，推不出同業合理價。下面的指標對照不受影響。</>
                ) : (
                  <>
                    用<b className="font-semibold">同業中位數 P/E {im.peer_median_pe}x</b>（{im.peer_n} 檔）× 本檔 EPS 推算。
                    本檔自己 5 年中位 PE 是 {im.own_pe_median_5y ?? '—'}x —— 兩個一起看：
                    差距大代表市場給這檔的倍數跟同業不同調。
                  </>
                )}
              </span>
            </p>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <ImpliedCard
                title="同業推算 · 今年"
                fair={im.current_year?.fair_price}
                diffPct={im.current_year?.diff_pct}
                sub={`同業中位 PE × 今年 EPS $${im.current_year_eps ?? '—'}`}
              />
              <ImpliedCard
                title="同業推算 · 明年"
                fair={im.next_year?.fair_price}
                diffPct={im.next_year?.diff_pct}
                sub={`同業中位 PE × 明年 EPS $${im.next_year_eps ?? '—'}`}
              />
              <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
                <p className="text-xs uppercase tracking-wider text-[var(--text-muted)]">本檔歷史推算</p>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="font-mono text-2xl font-bold tabular-nums text-[var(--text-secondary)]">
                    {fmtMoney(im.own_fair_price)}
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  自己 5 年中位 PE {im.own_pe_median_5y ?? '—'}x · 現價 {fmtMoney(im.current_price)}
                </p>
              </div>
            </div>
          </section>

          {/* 2. 組別切換 —— 一個選擇器同時決定下面兩張表要看哪一組指標 */}
          <div className="flex flex-wrap items-center gap-1.5">
            {groups.map(g => (
              <button
                key={g.key}
                onClick={() => setGroup(g.key)}
                aria-pressed={activeGroup === g.key}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] font-semibold transition-colors ${
                  activeGroup === g.key
                    ? 'border-[rgba(0,82,255,0.3)] bg-[rgba(0,82,255,0.1)] text-[var(--accent-cyan)]'
                    : 'border-[var(--glass-border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {g.title}
                <span className="font-mono text-[11px] font-normal opacity-70">{g.n}</span>
              </button>
            ))}
          </div>

          {/* 3. 指標對照（選中的組）*/}
          <section className="glass rounded-xl p-5">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 className="text-sm font-bold text-[var(--text-primary)]">
                指標對照 · {groupMeta?.title}
              </h2>
              <span className="text-[10.5px] text-[var(--text-muted)]">{groupMeta?.hint}</span>
            </div>
            <div className="max-w-4xl">
              <StatTable rows={rows} asOf={data.as_of} />
              <p className="mt-2 text-[10.5px] leading-relaxed text-[var(--text-muted)]">
                中位數與 p25–p75 都不含本檔（算進去會把中位數往自己拉）。
                「排名」是本檔在含自己的同業裡的名次，方向依欄位而定：倍數與槓桿愈低愈前、報酬率愈高愈前；
                RSI／Beta 這種沒有好壞方向的標成「中性」，只列數字。
                {data.as_of?.statistics && data.as_of?.metrics && data.as_of.statistics !== data.as_of.metrics && (
                  <> 倍數與報酬率來自 {data.as_of.metrics} 的每日快照；
                  利潤率、成長預估、Altman/Piotroski、RSI、目標價這幾類只有 statistics 有，
                  最新一批是 {data.as_of.statistics}，指標名稱後面會標日期。</>
                )}
              </p>
            </div>
          </section>

          {/* 4. 逐檔明細（同一組指標）*/}
          <section className="glass rounded-xl p-5">
            <h2 className="mb-3 text-sm font-bold text-[var(--text-primary)]">
              逐檔明細 · {groupMeta?.title}
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                {data.peer_count} 檔 × {rows.length + FIXED.length} 欄
              </span>
            </h2>
            <PeerTable peers={data.peers} ticker={ticker} metrics={rows} />
          </section>
        </>
      )}
    </div>
  );
}
