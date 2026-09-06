import { Gauge } from 'lucide-react';

// 技術指標速覽 —— 指數頁與個股頁共用。
// 資料來自 PriceChartPanel 的 onAnalytics 回呼（同一次 chart API，不重抓）。
// 八格：評級 / RSI / MACD / ADX / 對 MA50 / 對 MA250 / 年化波動 / 距 52 週高。
// 每格「數值 + 判讀字」，判讀門檻跟 PriceChartPanel 的分析卡一致（ADX 25/20、RSI 70/30）。

const INTERVAL_LABEL = { day: '日線', week: '週線', month: '月線' };
const VOL_REGIME_LABEL = { extreme: '極端', elevated: '偏高', normal: '正常', compressed: '壓縮' };

function buildTechCells(tech) {
  const bar = tech?.lastBar || {};
  const stats = tech?.analytics?.stats || {};
  const overall = tech?.analytics?.rating?.overall;
  const close = Number(bar.close);

  const vsMa = (ma) => {
    const m = Number(ma);
    if (!Number.isFinite(close) || !Number.isFinite(m) || m <= 0) return null;
    return (close - m) / m * 100;
  };
  const ma50 = vsMa(bar.ma50);
  const ma250 = vsMa(bar.ma250);
  const pctText = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`);
  const numText = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));

  const ratingTone = !overall ? 'neutral'
    : /Buy/.test(overall.label) ? 'good'
    : /Sell/.test(overall.label) ? 'bad' : 'neutral';

  const rsi = bar.rsi != null ? Number(bar.rsi) : null;
  const rsiRead = rsi == null ? null : rsi >= 70 ? ['超買', 'bad'] : rsi <= 30 ? ['超賣', 'good'] : ['中性', 'neutral'];

  // MACD 值的顏色看自己正負；判讀看柱狀圖（MACD − Signal）：正＝動能轉強、負＝轉弱。
  // 兩者分開標，才不會出現「+110 卻寫空頭」這種讀起來矛盾的格子。
  const macd = bar.macd != null ? Number(bar.macd) : null;
  const hist = bar.hist != null ? Number(bar.hist) : null;
  const macdTone = macd == null ? 'neutral' : macd > 0 ? 'good' : macd < 0 ? 'bad' : 'neutral';
  const macdRead = hist == null ? null
    : [`柱 ${hist > 0 ? '+' : ''}${hist.toFixed(2)} · ${hist > 0 ? '轉強' : hist < 0 ? '轉弱' : '持平'}`,
       hist > 0 ? 'good' : hist < 0 ? 'bad' : 'neutral'];

  const adx = stats.adx ?? (bar.adx != null ? Number(bar.adx) : null);
  const adxRead = adx == null ? null : adx >= 25 ? ['趨勢明確', 'neutral'] : adx < 20 ? ['盤整', 'neutral'] : ['轉折中', 'neutral'];

  const maRead = (v) => (v == null ? null : v >= 0 ? ['站上', 'good'] : ['跌破', 'bad']);
  const regime = stats.percentiles?.vol_regime;

  return [
    { label: '綜合評級', value: overall?.label ?? '—', read: overall ? [`分數 ${Number(overall.score).toFixed(2)}`, ratingTone] : null, tone: ratingTone, text: true },
    { label: 'RSI (14)', value: numText(rsi), read: rsiRead, tone: rsiRead?.[1] },
    { label: 'MACD', value: numText(macd, 2), read: macdRead, tone: macdTone },
    { label: 'ADX (14)', value: numText(adx), read: adxRead, tone: 'neutral' },
    { label: '對 MA50', value: pctText(ma50), read: maRead(ma50), tone: maRead(ma50)?.[1] },
    { label: '對 MA250', value: pctText(ma250), read: maRead(ma250), tone: maRead(ma250)?.[1] },
    { label: '年化波動', value: stats.annual_vol != null ? `${stats.annual_vol}%` : '—',
      read: regime ? [VOL_REGIME_LABEL[regime] ?? regime, regime === 'extreme' || regime === 'elevated' ? 'bad' : 'neutral'] : null, tone: 'neutral' },
    { label: '距 52 週高', value: pctText(stats.pct_from_high, 2),
      read: stats.pct_from_low != null ? [`距低 ${pctText(stats.pct_from_low, 1)}`, 'neutral'] : null, tone: 'neutral' },
  ];
}

const TONE_CLASS = {
  good: 'text-[var(--accent-success-text)]',
  bad: 'text-[var(--accent-danger-text)]',
  neutral: 'text-[var(--text-primary)]',
};

function TechStrip({ tech }) {
  const cells = buildTechCells(tech);
  const ready = !!tech?.lastBar;
  const scope = ready
    ? `${tech.period.toUpperCase()} ${INTERVAL_LABEL[tech.interval] ?? tech.interval} · 最新一根 K 線`
    : '載入中';

  return (
    <section aria-label="技術指標速覽" className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-[var(--text-meta)]">
        <Gauge size={13} className="shrink-0" />
        <span className="font-semibold uppercase tracking-[0.08em]">技術指標</span>
        <span>{scope}</span>
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        {cells.map(c => (
          <div key={c.label} className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-3 py-2.5">
            <dt className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-meta)]">{c.label}</dt>
            {ready ? (
              <>
                <dd className={`mt-1 font-mono text-lg font-semibold leading-tight tabular-nums ${c.text ? 'font-sans text-base' : ''} ${TONE_CLASS[c.tone] ?? TONE_CLASS.neutral}`}>
                  {c.value}
                </dd>
                <dd className={`mt-0.5 text-[11px] ${c.read ? (TONE_CLASS[c.read[1]] ?? '') : ''} ${!c.read || c.read[1] === 'neutral' ? 'text-[var(--text-meta)]' : ''}`}>
                  {c.read ? c.read[0] : ' '}
                </dd>
              </>
            ) : (
              <>
                <dd className="mt-1.5 h-5 w-16 animate-pulse rounded bg-[var(--bg-tertiary)]" />
                <dd className="mt-1.5 h-3 w-10 animate-pulse rounded bg-[var(--bg-tertiary)]" />
              </>
            )}
          </div>
        ))}
      </dl>
    </section>
  );
}

export default TechStrip;
