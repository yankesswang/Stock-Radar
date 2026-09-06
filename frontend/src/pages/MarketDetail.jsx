import { useCallback, useEffect, useState } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import { ArrowLeft, TrendingDown, TrendingUp } from 'lucide-react';
import PriceChartPanel from '../components/PriceChartPanel';
import { useExtendedQuotes } from '../components/useExtendedQuotes';
import ExtendedQuoteBadge from '../components/ExtendedQuote';
import TechStrip from '../components/TechStrip';

// 指數／市場商品頁。
// 版面順序：標題＋現價 → 一列關鍵數字 → 走勢圖（置頂主角）→ 圖層／技術分析折疊區。
// 資產類型色標與首頁 MarketCard 同一套，點進來視覺不斷裂。

const TYPE_TONE = {
  Volatility: 'bg-[rgba(217,119,6,0.1)] text-[var(--accent-amber-text)]',
  Crypto:     'bg-[rgba(139,92,246,0.1)] text-[var(--accent-purple-text)]',
  Rates:      'bg-[rgba(0,82,255,0.08)] text-[var(--accent-cyan-text)]',
  Commodity:  'bg-[rgba(251,191,36,0.12)] text-[var(--accent-amber-text)]',
};
const typeTone = (t) => TYPE_TONE[t] ?? 'bg-[rgba(0,82,255,0.07)] text-[var(--text-meta)]';

export default function MarketDetail() {
  const [, params] = useRoute('/market/:symbol');
  const symbol = decodeURIComponent(params?.symbol || '').toUpperCase();
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  // 圖表元件載入完回傳的技術面資料（隨圖表區間切換更新）
  const [tech, setTech] = useState(null);
  const onAnalytics = useCallback((payload) => setTech(payload), []);
  const extended = useExtendedQuotes(symbol ? [symbol] : []);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setTech(null);
    axios.get('/api/market/overview', { params: { symbols: symbol } })
      .then(res => setItem((res.data.items || [])[0] || null))
      .catch(() => setItem(null))
      .finally(() => setLoading(false));
  }, [symbol]);

  // VIX 漲＝風險升，顏色反向；箭頭仍照實際漲跌（跟首頁同一規則）。
  const isVix = symbol === '^VIX';
  const up = (item?.change_pct || 0) >= 0;
  const good = isVix ? !up : up;
  const title = item?.label || symbol;
  const ok = !loading && item?.ok;

  const metaLine = [
    symbol,
    item?.source && item.source,
    item?.last_date && `收盤 ${item.last_date}`,
  ].filter(Boolean).join(' · ');

  return (
    <div className="container py-8 space-y-6 animate-fade-in">
      <Link
        href="/"
        className="inline-flex min-h-[44px] items-center gap-2 text-sm text-[var(--text-meta)] transition-colors duration-200 hover:text-[var(--text-primary)]"
      >
        <ArrowLeft size={14} /> 回首頁
      </Link>

      {/* 標題列：左邊是名稱／代號，右邊是現價與漲跌 */}
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">{title}</h1>
            <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide ${typeTone(item?.asset_type)}`}>
              {item?.asset_type || 'Market'}
            </span>
          </div>
          <p className="mt-1.5 font-mono text-sm text-[var(--text-meta)]">{metaLine}</p>
        </div>

        <div className="md:text-right" aria-live="polite">
          {loading ? (
            <div className="space-y-2 md:flex md:flex-col md:items-end">
              <div className="h-10 w-40 animate-pulse rounded-lg bg-[var(--bg-tertiary)]" />
              <div className="h-6 w-24 animate-pulse rounded-full bg-[var(--bg-tertiary)]" />
            </div>
          ) : ok ? (
            <>
              <p className="font-mono text-4xl font-semibold tabular-nums tracking-[-0.03em] text-[var(--text-primary)]">
                {formatPrice(item)}
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 md:justify-end">
                <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-sm font-semibold ${
                  good
                    ? 'bg-[rgba(5,150,105,0.1)] text-[var(--accent-success-text)]'
                    : 'bg-[rgba(220,38,38,0.08)] text-[var(--accent-danger-text)]'
                }`}>
                  {up ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {fmtPct(item.change_pct)}
                </span>
                {item.change != null && (
                  <span className="font-mono text-sm tabular-nums text-[var(--text-meta)]">
                    {item.change >= 0 ? '+' : ''}{Number(item.change).toFixed(2)}
                  </span>
                )}
                {extended[symbol] && (
                  <ExtendedQuoteBadge
                    quote={extended[symbol]}
                    showPrice
                    invert={isVix}
                    upColor="#047857"
                    downColor="#dc2626"
                  />
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-[var(--accent-danger-text)]">{item?.error || '目前沒有報價資料'}</p>
          )}
        </div>
      </header>

      {/* 關鍵數字一列帶過：不做大色塊，數字本身就是重點 */}
      <dl className="grid grid-cols-2 border-y border-[var(--glass-border)] sm:grid-cols-4 [&>*:nth-child(even)]:border-l sm:[&>*+*]:border-l [&>*]:border-[var(--glass-border)]">
        <Stat label="今年以來" value={item?.ytd_return_pct} loading={loading} signed />
        <Stat label="近 1 個月" value={item?.one_month_return_pct} loading={loading} signed />
        <Stat label="最大回撤（近 1 年）" value={item?.max_drawdown_pct} loading={loading} />
        <Stat label="前一收盤" value={item?.previous_close} loading={loading} plain />
      </dl>

      {/* 技術指標速覽：數字＋一個文字判讀，不靠顏色單獨傳達 */}
      <TechStrip tech={tech} />

      {/* 走勢圖置頂，其餘技術資訊收進折疊區 */}
      <PriceChartPanel ticker={symbol} chartFirst onAnalytics={onAnalytics} />
    </div>
  );
}

// signed → 正綠負紅；否則（回撤、前收）維持中性色，免得整列都在閃警示。
function Stat({ label, value, loading, signed = false, plain = false }) {
  const num = Number(value);
  const has = value != null && Number.isFinite(num);
  const tone = signed && has
    ? (num > 0 ? 'text-[var(--accent-success-text)]' : num < 0 ? 'text-[var(--accent-danger-text)]' : 'text-[var(--text-primary)]')
    : 'text-[var(--text-primary)]';
  const text = !has ? '—' : plain
    ? num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : fmtPct(num);

  return (
    <div className="px-4 py-3 first:pl-0 sm:first:pl-0">
      <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-meta)]">{label}</dt>
      {loading ? (
        <dd className="mt-1.5 h-6 w-20 animate-pulse rounded bg-[var(--bg-tertiary)]" />
      ) : (
        <dd className={`mt-1 font-mono text-xl font-semibold tabular-nums ${tone}`}>{text}</dd>
      )}
    </div>
  );
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return 'N/A';
  const num = Number(value);
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
}

function formatPrice(item) {
  if (item.price == null) return 'N/A';
  const digits = item.symbol?.includes('BTC') || item.symbol?.includes('ETH') ? 0 : 2;
  const prefix = item.asset_type === 'Index' || item.asset_type === 'Volatility' ? '' : '$';
  return `${prefix}${Number(item.price).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}
