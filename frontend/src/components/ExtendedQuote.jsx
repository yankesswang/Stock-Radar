const LABEL = { pre: '盤前', post: '盤後' };

/**
 * Pre/post-market change badge. Renders nothing when `quote` is absent — i.e.
 * during regular hours or when the market is closed.
 *
 * The four pages that use this each have their own red/green convention (CSS
 * vars, an inline palette, literal hex), so the badge carries its own colors to
 * stay recognisable as one element across all of them. The defaults suit the
 * dark surfaces; pass upColor/downColor on light ones, where they'd wash out.
 * `invert` is for ^VIX, where Home already flips the meaning of up/down.
 */
export default function ExtendedQuoteBadge({
  quote,
  showPrice = false,
  invert = false,
  upColor = '#34d399',
  downColor = '#f87171',
  className = '',
}) {
  if (!quote) return null;

  const up = quote.change_pct >= 0;
  const color = (invert ? !up : up) ? upColor : downColor;
  const asOf = quote.time
    ? new Date(quote.time * 1000).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })
    : null;

  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-[11px] ${className}`}
      title={asOf ? `${LABEL[quote.session]} ${quote.price} · ${asOf} 更新` : undefined}
    >
      <span
        className="rounded px-1 py-0.5 text-[10px] font-medium"
        style={{ background: 'rgba(148,163,184,0.16)', color: '#94a3b8' }}
      >
        {LABEL[quote.session] || quote.session}
      </span>
      {showPrice && (
        <span className="font-mono tabular-nums" style={{ color: '#94a3b8' }}>
          {quote.price}
        </span>
      )}
      <span className="font-mono font-semibold tabular-nums" style={{ color }}>
        {up ? '+' : ''}
        {quote.change_pct}%
      </span>
    </span>
  );
}
