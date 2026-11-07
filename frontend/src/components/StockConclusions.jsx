/**
 * StockConclusions — 個股主頁的總覽列：每個切面一句話結論，證據在各自的分頁。
 *
 * 以前主頁上有「Valuation Analysis」兩張卡（PE 中位數合理價、明年 EPS 合理價）、
 * 三張 StatCard（其中一張又是 PE 合理價）、一張同業摘要卡，講的都是 DCF／PE／同業
 * 分頁裡的一小部分，而且彼此重複。這裡改成一列六格：四個合理價來源（DCF、PE 歷史、
 * 分析師目標價、同業推算）對現價的上檔，加 AI 立場與最新一季財報筆記。每格整格
 * 是連結，點進去看證據。
 *
 * 資料來自 /api/stocks/{t}/tabs（useStockTabs），跟分頁列灰化用同一份；後端算的
 * 上檔是用快照價，這裡拿現價重算一次。
 */
import { Link } from 'wouter';
import { ArrowRight, TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react';
import { findStockTab, stockTabHref } from './stockTabsDef';

const money = (v) => (v == null ? '—'
  : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

// 後端的 upside_pct 用的是估值快照那天的價；有現價就重算
function upsideOf(value, price, fallback) {
  if (value != null && price) return (Number(value) - price) / price * 100;
  return fallback ?? null;
}

function UpsidePill({ pct }) {
  if (pct == null || Number.isNaN(pct)) return null;
  const up = pct > 0;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${
        up
          ? 'bg-[rgba(52,211,153,0.12)] text-[var(--accent-success-text)]'
          : 'bg-[rgba(248,113,113,0.12)] text-[var(--accent-danger-text)]'
      }`}
    >
      {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {up ? '上檔' : '下檔'} {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

function Cell({ ticker, tabKey, children, empty, emptyText, warn }) {
  const tab = findStockTab(tabKey);
  const Icon = tab?.icon;
  return (
    <Link
      href={stockTabHref(ticker, tab)}
      className={`ui-focus group flex min-h-[112px] flex-col justify-between rounded-xl border p-4 transition-colors ${
        empty
          ? 'border-dashed border-[var(--glass-border)] bg-transparent hover:border-[var(--glass-border-hover)]'
          : 'border-[var(--glass-border)] bg-[var(--bg-tertiary)] hover:border-[rgba(0,82,255,0.35)]'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {Icon && <Icon size={12} />} {tab?.label}
          {warn && <AlertTriangle size={12} className="text-[var(--accent-amber)]" aria-label="模型不適用" />}
        </span>
        <ArrowRight size={13} className="text-[var(--text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      {empty
        ? <p className="text-[13px] leading-relaxed text-[var(--text-muted)]">{emptyText ?? '這檔還沒有資料'}</p>
        : children}
    </Link>
  );
}

function ValueCell({ ticker, tabKey, info, price, sub }) {
  // 有數字就畫（估值快照裡的 PE 合理價不依賴 PE 歷史檔）；分頁本身有沒有資料由 note 講
  const has = info?.value != null;
  const pct = has ? upsideOf(info.value, price, info.upside_pct) : null;
  return (
    <Cell ticker={ticker} tabKey={tabKey} empty={!has} emptyText={info?.note} warn={info?.meaningful === false}>
      <div>
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-[22px] font-bold tabular-nums tracking-tight text-[var(--text-primary)]">
            {money(info?.value)}
          </span>
          <UpsidePill pct={pct} />
        </div>
        {(sub ?? info?.note) && (
          <p className="mt-1 text-[12px] leading-snug text-[var(--text-meta)]">{sub ?? info.note}</p>
        )}
      </div>
    </Cell>
  );
}

function Skeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="載入各切面結論">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="h-[112px] animate-pulse rounded-xl bg-[var(--bg-tertiary)] opacity-70" />
      ))}
    </div>
  );
}

export default function StockConclusions({ ticker, tabs, price }) {
  if (tabs?.failed) {
    return (
      <p className="rounded-xl border border-dashed border-[var(--glass-border)] p-4 text-[13px] text-[var(--text-muted)]">
        各切面結論載入失敗，重新整理再試一次。下面各區塊與分頁都不受影響。
      </p>
    );
  }
  const t = tabs?.tabs;
  if (!t) return <Skeleton />;

  // 四個合理價來源裡，幾個高於現價。刻意不給「平均上檔」：四個來源常常一個 -35%
  // 一個 +100%（DCF 與 5 年中位 PE 對高成長股本來就會分岔），等權平均出來的單一
  // 數字看起來像個結論，其實只是把分歧抹掉。分歧本身才是要看的東西。
  const sources = ['dcf', 'pe', 'forecast', 'peers']
    .map((k) => ({ k, info: t[k] }))
    .filter(({ info }) => info?.value != null)
    .map(({ k, info }) => ({ k, pct: upsideOf(info.value, price, info.upside_pct) }))
    .filter(({ pct }) => pct != null);
  const above = sources.filter((s) => s.pct > 0).length;
  const spread = sources.length > 1
    ? { lo: Math.min(...sources.map((s) => s.pct)), hi: Math.max(...sources.map((s) => s.pct)) }
    : null;

  const pe = t.pe;
  const peSub = pe?.next_year_value != null
    ? `${pe.note ?? ''}${pe.note ? ' · ' : ''}明年 EPS 推 ${money(pe.next_year_value)}`
    : pe?.note;

  const ai = t.ai;
  const notes = t.notes;

  return (
    <section className="space-y-3" aria-label="各切面結論">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold text-[var(--text-secondary)]">各切面結論</h3>
        {sources.length > 0 && (
          <p className="text-[12.5px] text-[var(--text-meta)]">
            {sources.length} 個估值來源中{' '}
            <b className="font-mono text-[var(--text-primary)]">{above}</b> 個高於現價
            {spread && (
              <>
                ，區間{' '}
                <b className="font-mono text-[var(--text-primary)]">
                  {spread.lo > 0 ? '+' : ''}{spread.lo.toFixed(0)}% ～ {spread.hi > 0 ? '+' : ''}{spread.hi.toFixed(0)}%
                </b>
              </>
            )}
            {tabs.as_of && <span className="ml-2 opacity-70">估值快照 {tabs.as_of}</span>}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <ValueCell ticker={ticker} tabKey="dcf" info={t.dcf} price={price} />
        <ValueCell ticker={ticker} tabKey="pe" info={pe} price={price} sub={peSub} />
        <ValueCell ticker={ticker} tabKey="forecast" info={t.forecast} price={price} />
        <ValueCell ticker={ticker} tabKey="peers" info={t.peers} price={price} />

        <Cell ticker={ticker} tabKey="ai" empty={!ai?.available} emptyText={ai?.note}>
          <div>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-[18px] font-bold text-[var(--text-primary)]">{ai?.headline ?? '—'}</span>
              {ai?.score != null && (
                <span className="font-mono text-[12px] tabular-nums text-[var(--text-muted)]">分數 {ai.score}</span>
              )}
              {ai?.target && (
                <span className="font-mono text-[12px] tabular-nums text-[var(--text-muted)]">目標 {ai.target}</span>
              )}
            </div>
            {ai?.note && <p className="mt-1 line-clamp-2 text-[12px] leading-snug text-[var(--text-meta)]">{ai.note}</p>}
            {ai?.date && <p className="mt-1 text-[11px] text-[var(--text-muted)]">產生於 {ai.date}</p>}
          </div>
        </Cell>

        <Cell ticker={ticker} tabKey="notes" empty={!notes?.available} emptyText="還沒有財報筆記；財報公布隔日會自動生成">
          <div>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-[18px] font-bold text-[var(--text-primary)]">{notes?.headline}</span>
              {notes?.count > 1 && (
                <span className="text-[12px] text-[var(--text-muted)]">共 {notes.count} 季</span>
              )}
            </div>
            {notes?.note && <p className="mt-1 line-clamp-2 text-[12px] leading-snug text-[var(--text-meta)]">{notes.note}</p>}
            {notes?.date && <p className="mt-1 text-[11px] text-[var(--text-muted)]">財報日 {notes.date}</p>}
          </div>
        </Cell>
      </div>
    </section>
  );
}
