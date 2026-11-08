/**
 * StockPageHeader — 個股主頁與 11 個子頁共用的標頭：麵包屑、代號、估值狀態、
 * 公司名、產業／市值、現價，再加兩層分頁列（StockTabs）。
 *
 * 以前每個子頁自己畫一套：h1 有 3xl／2xl／xl 三種、回上頁文案四種（Back to／返回／
 * 回到／回）、沒有一頁看得到現價 —— 但 DCF、PE、預估全都在跟現價比。統一成一個元件
 * 之後，切分頁時標頭位置不動、資料不閃，只有底下內容在換。
 *
 * - title 不給時用 active 分頁的名字（主頁不顯示），出現在 badge 與麵包屑最後一節。
 * - meta 是標題底下那行「資料多新、幾筆」；chips 是標題旁的額外 badge（例如 DCF 可信度）；
 *   actions 放右側現價底下（期數切換、重新分析…）。
 * - info／price 給主頁用：它自己已經抓了估值歷史和現價，傳進來就不再打 API。
 * - industryNode 讓主頁換成可以改產業的 GroupSelect，其他頁是靜態 badge。
 */
import { useEffect } from 'react';
import { Link, useLocation } from 'wouter';
import { ChevronRight, TrendingUp, TrendingDown } from 'lucide-react';
import ClusterTabs from './ClusterTabs';
import { useStockHeader, useStockTabs } from './useStockHeader';
import { STOCK_TAB_GROUPS, findStockTab, stockTabHref, activeStockTabKey } from './stockTabsDef';

export function StockTabs({ ticker, className = '' }) {
  const [location] = useLocation();
  const activeKey = activeStockTabKey(location);
  // 每個切面有沒有資料：沒有的變灰（仍可點，進去看那頁自己的空狀態說明），有計數的加角標
  const tabsInfo = useStockTabs(ticker);
  const active = findStockTab(activeKey);
  const activeGroup = STOCK_TAB_GROUPS.find((g) => g.key === active?.group) ?? null;

  const groupItems = STOCK_TAB_GROUPS.map((g) => ({
    href: stockTabHref(ticker, g.tabs[0]),
    label: g.label,
    icon: g.icon,
  }));
  const activeGroupHref = activeGroup ? stockTabHref(ticker, activeGroup.tabs[0]) : null;

  // 滑到分頁上先把那頁的程式碼抓回來（App.jsx 的 lazy() 用同一個模組路徑，chunk 共用）
  const prefetch = (t) => { try { t.prefetch?.(); } catch { /* 預載失敗不影響點擊 */ } };

  return (
    <div className={`space-y-2 ${className}`}>
      <ClusterTabs items={groupItems} active={activeGroupHref} />
      {activeGroup && activeGroup.tabs.length > 1 && (
        <div className="flex max-w-full items-center gap-1 overflow-x-auto styled-scrollbar" role="tablist" aria-label={`${activeGroup.label}切面`}>
          {activeGroup.tabs.map((t) => {
            const Icon = t.icon;
            const on = t.key === activeKey;
            const info = tabsInfo?.tabs?.[t.key];
            const empty = info?.available === false;
            const count = info?.count;
            const cls = `ui-focus inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition-colors duration-150 ${
              on
                ? 'border-[rgba(0,82,255,0.35)] bg-[rgba(0,82,255,0.10)] text-[var(--accent-cyan-text)]'
                : empty
                  ? 'border-dashed border-[var(--glass-border)] bg-transparent text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
                  : 'border-[var(--glass-border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]'
            }`;
            const badge = count != null && count > 0 && (
              <span className={`ml-0.5 rounded-full px-1.5 text-[10.5px] font-bold tabular-nums ${
                on ? 'bg-[rgba(0,82,255,0.16)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
              }`}>{count}</span>
            );
            if (t.anchor) {
              // 區塊錨點：在主頁直接跳；在子頁先回主頁，主頁載完再捲過去（StockDetail 讀 hash）
              const onOverview = activeKey === 'overview';
              return onOverview
                ? <a key={t.key} href={t.path} className={cls} role="tab" aria-selected={false}><Icon size={13} /> {t.label}</a>
                : <Link key={t.key} href={`/stock/${ticker}${t.path}`} className={cls} role="tab" aria-selected={false}><Icon size={13} /> {t.label}</Link>;
            }
            return (
              <Link
                key={t.key}
                href={stockTabHref(ticker, t)}
                role="tab"
                aria-selected={on}
                aria-current={on ? 'page' : undefined}
                className={cls}
                title={empty ? `${t.label}：這檔還沒有資料` : undefined}
                onMouseEnter={() => prefetch(t)}
                onFocus={() => prefetch(t)}
              >
                <Icon size={13} /> {t.label}{badge}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function StockPageHeader({
  ticker, title, meta, chips, actions, industryNode, info: infoOverride, price: priceOverride,
}) {
  const [location] = useLocation();
  const activeKey = activeStockTabKey(location);
  const active = findStockTab(activeKey);
  const pageTitle = title ?? (activeKey && activeKey !== 'overview' ? active?.label : null);

  const overrides = {};
  if (infoOverride !== undefined) overrides.info = infoOverride;
  if (priceOverride !== undefined) overrides.price = priceOverride;
  const { info, price } = useStockHeader(ticker, overrides);

  useEffect(() => {
    const parts = [ticker, pageTitle, 'StockRadar'].filter(Boolean);
    document.title = parts.join(' · ');
  }, [ticker, pageTitle]);

  const status = info?.valuation_status;
  const under = status === 'undervalued';
  const shownPrice = price?.price ?? null;
  const change = price?.change;
  const up = change != null && change >= 0;

  return (
    <header className="space-y-5 border-b border-[var(--glass-border)] pb-5">
      <nav aria-label="麵包屑" className="flex flex-wrap items-center gap-1 text-[12.5px] text-[var(--text-muted)]">
        <Link href="/valuations" className="ui-focus rounded transition-colors hover:text-[var(--text-primary)]">估值清單</Link>
        <ChevronRight size={12} aria-hidden="true" />
        {pageTitle
          ? <Link href={`/stock/${ticker}`} className="ui-focus rounded font-mono transition-colors hover:text-[var(--text-primary)]">{ticker}</Link>
          : <span className="font-mono text-[var(--text-secondary)]">{ticker}</span>}
        {pageTitle && (
          <>
            <ChevronRight size={12} aria-hidden="true" />
            <span className="text-[var(--text-secondary)]">{pageTitle}</span>
          </>
        )}
      </nav>

      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
            {status && (
              <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${
                under
                  ? 'border-[rgba(52,211,153,0.15)] bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)]'
                  : 'border-[rgba(248,113,113,0.15)] bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)]'
              }`}>
                {under ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {under ? '低估' : '高估'} {Math.abs(info.valuation_diff_pct ?? 0)}%
              </span>
            )}
            {pageTitle && <span className="badge badge-neutral">{pageTitle}</span>}
            {chips}
          </div>
          {info?.company_name && (
            <p className="mb-2 text-lg text-[var(--text-secondary)]">{info.company_name}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {industryNode ?? (info?.industry && <span className="badge badge-neutral">{info.industry}</span>)}
            {info?.market_cap_display && <span className="badge badge-neutral">市值 {info.market_cap_display}</span>}
          </div>
          {meta && <p className="mt-2 text-[13.5px] text-[var(--text-meta)]">{meta}</p>}
        </div>

        <div className="flex shrink-0 flex-col items-start gap-3 md:items-end">
          {shownPrice != null && (
            <div className="text-left md:text-right">
              <div className="mb-1 text-xs uppercase tracking-wider text-[var(--text-muted)]">
                {price?.market_state === 'REGULAR' ? '即時價' : '現價'}
              </div>
              <div className="font-mono text-3xl font-extrabold tabular-nums tracking-tight">
                ${shownPrice.toFixed(2)}
              </div>
              {change != null && (
                <div className={`mt-1 flex items-center gap-1.5 text-sm font-semibold md:justify-end ${
                  up ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                }`}>
                  {up ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {up ? '+' : ''}{change.toFixed(2)} ({price.change_percent?.toFixed(2)}%)
                </div>
              )}
            </div>
          )}
          {actions}
        </div>
      </div>

      <StockTabs ticker={ticker} />
    </header>
  );
}

/**
 * 子頁載入中／出錯時的整頁狀態。以前出錯只剩一句英文訊息加「Back to」，
 * 使用者連別的切面都到不了；現在標頭與分頁列照常在，只有內容區換成訊息。
 */
export function StockPageFallback({ ticker, error }) {
  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      <StockPageHeader ticker={ticker} />
      {error ? (
        <div
          role="alert"
          className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-6 text-center text-[var(--text-muted)]"
        >
          {error}
        </div>
      ) : (
        <div className="flex justify-center py-16" role="status" aria-label="載入中">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent-purple)] border-t-transparent" />
        </div>
      )}
    </div>
  );
}
