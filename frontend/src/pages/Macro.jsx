/**
 * Macro — 總經指標的詳細圖表版。
 *
 * 原本這一頁是「九張數字卡 + 三張寫死的 1 年小圖」：看得到現值，看不出脈絡
 * （現在的殖利率在這一年是高是低？曲線是正是倒？油價跟殖利率是不是同向？）。
 * 改成四層，由上而下就是讀總經的順序：
 *
 *   1. 殖利率曲線（期限結構）—— 今天 vs 1 個月前 vs 1 年前疊在同一張，
 *      整條往上平移、變陡還是倒掛，一眼看完，不必比對四張各自的走勢圖。
 *   2. 利差歷史 —— 10Y−3M 為主（0 是倒掛分界，跌破塗紅），10Y−5Y 為輔。
 *   3. 指標卡 —— 現值＋當日變動＋所選區間的走勢與「現在落在區間的哪裡」。
 *   4. 單檔大圖 + 疊圖比較（rebase 100）—— 單位不同的東西只有 rebase 過才比得起來，
 *      這也是為什麼這一頁沒有任何雙 Y 軸圖。
 *
 * 區間切換（1M～5Y）是全頁共用的，四層一起換 —— 讀的是同一段時間。
 *
 * 配色：類別色取自設計系統並跑過 validate_palette（light 模式、all-pairs 全過，
 * CVD 落在 6–8 的下限帶 → 每條線都直接標在線尾，識別不靠顏色）。
 * 殖利率曲線的三條線是時間先後而不是類別，用「今天實線／1個月前虛線／1年前點線」
 * 的線型排序，顏色只是輔助。
 */
import { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import {
  TrendingUp, TrendingDown, Activity, RefreshCw, Loader2,
  AlertTriangle, Check,
} from 'lucide-react';
import {
  LineChart, Line, AreaChart, Area, ComposedChart, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine, Legend,
} from 'recharts';
import { CHART_GRID, CHART_AXIS, CHART_REFERENCE, chartTooltipStyle, chartTooltipLabelStyle } from '../components/chartTheme';
import ClusterTabs from '../components/ClusterTabs';
import { MARKET_TABS } from '../components/clusterTabsDef';

// ── 配色 ──────────────────────────────────────────────────────────────────────
// 固定順序的類別色，永不循環：第 5 條線不存在（疊圖上限就是 4 檔）。
// node scripts/validate_palette.js "#0052ff,#d97706,#0d9488,#be185d" --mode light --pairs all
const SERIES = ['#0052ff', '#d97706', '#0d9488', '#be185d'];
const MAX_COMPARE = SERIES.length;

// 曲線的三條線是時間先後，不是類別 —— 線型排序（實線＝今天）才是主要編碼
const CURVE_LINES = [
  { key: 'y1', label: '1 年前', color: '#64748b', dash: '2 4' },
  { key: 'm1', label: '1 個月前', color: '#4f7ac4', dash: '6 4' },
  { key: 'now', label: '今天', color: '#0052ff', dash: null },
];

const POSITIVE = 'var(--accent-success)';
const NEGATIVE = 'var(--accent-danger)';

const PERIODS = [
  { key: '1mo', label: '1M' },
  { key: '3mo', label: '3M' },
  { key: '6mo', label: '6M' },
  { key: '1y', label: '1Y' },
  { key: '2y', label: '2Y' },
  { key: '5y', label: '5Y' },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(val, unit) {
  if (val == null) return '—';
  const n = Number(val);
  if (unit === '%') return `${n.toFixed(3)}%`;
  if (unit === '$/bbl' || unit === '$/oz' || unit === '$/MMBtu') return `$${n.toFixed(2)}`;
  return n.toFixed(2);
}

// 殖利率的「漲了 17%」沒有意義，要講的是「上升 0.70 個百分點」
function fmtDelta(stats, unit) {
  if (!stats || stats.change == null) return '—';
  const sign = stats.change > 0 ? '+' : '';
  if (unit === '%') return `${sign}${stats.change.toFixed(3)} pp`;
  return `${sign}${stats.change_pct?.toFixed(1)}%`;
}

const mmdd = (d) => (d ? d.slice(5) : '');
const yymm = (d) => (d ? `${d.slice(2, 4)}/${d.slice(5, 7)}` : '');

function ChangeChip({ item }) {
  const isYield = item.unit === '%';
  const val = isYield ? item.change : item.change_pct;
  if (val == null) return <span className="text-[var(--text-muted)]">—</span>;
  const pos = val >= 0;
  return (
    <span
      title={isYield ? '當日變動（百分點）' : '當日變動'}
      className={`inline-flex items-center gap-0.5 text-xs font-semibold
      ${pos ? 'text-[var(--accent-success-text)]' : 'text-[var(--accent-danger-text)]'}`}>
      {pos ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {pos ? '+' : ''}{isYield ? `${val.toFixed(3)} pp` : `${val.toFixed(2)}%`}
    </span>
  );
}

// 共用的 X 軸日期刻度：資料點很多，只留 5 個標籤，其餘留白給線。
// 超過一年的區間要印年份，否則 5Y 圖上會出現五個一模一樣的「09-07」。
const dateAxis = (rows) => {
  const n = rows.length;
  const spanDays = n > 1
    ? (new Date(rows[n - 1].date) - new Date(rows[0].date)) / 86400000
    : 0;
  return {
    dataKey: 'date',
    tick: { fill: 'var(--text-muted)', fontSize: 10 },
    tickLine: false,
    axisLine: { stroke: CHART_AXIS },
    interval: Math.max(0, Math.floor(n / 5)),
    tickFormatter: spanDays > 400 ? yymm : mmdd,
    minTickGap: 24,
  };
};

const numAxis = (extra = {}) => ({
  tick: { fill: 'var(--text-muted)', fontSize: 10 },
  tickLine: false,
  axisLine: false,
  width: 46,
  ...extra,
});

// 線尾直接標名字 —— CVD 分離度落在下限帶時，識別不能只靠顏色
function endLabel(text, color, lastIndex) {
  return function EndLabel(props) {
    const { x, y, index } = props;
    if (index !== lastIndex || x == null || y == null) return null;
    return (
      <text x={x + 6} y={y} dy={4} fontSize={11} fontWeight={600} fill={color}>
        {text}
      </text>
    );
  };
}

function ChartCard({ title, sub, right, children, className = '' }) {
  return (
    <section className={`glass rounded-xl p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div>
          <h2 className="text-sm font-bold text-[var(--text-primary)]">{title}</h2>
          {sub && <p className="text-[11px] leading-relaxed text-[var(--text-meta)]">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

// ── 1. 殖利率曲線（期限結構）──────────────────────────────────────────────────

function CurveTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={chartTooltipStyle} className="px-3 py-2">
      <p style={chartTooltipLabelStyle} className="text-[11px] font-semibold">{label} 公債</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="flex items-center gap-1.5 text-[12px] text-[var(--text-primary)]">
          <span className="inline-block h-0.5 w-3 rounded" style={{ background: p.color }} />
          {CURVE_LINES.find(c => c.key === p.dataKey)?.label}
          <b className="font-mono tabular-nums">{p.value?.toFixed(3)}%</b>
        </p>
      ))}
    </div>
  );
}

function YieldCurve({ points }) {
  const shape = useMemo(() => {
    const short = points.find(p => p.label === '3M')?.now;
    const long = points.find(p => p.label === '10Y')?.now;
    if (short == null || long == null) return null;
    const d = long - short;
    if (d < 0) return { text: '倒掛', tone: NEGATIVE, note: '短天期利率高於長天期 —— 市場在定價降息／衰退' };
    if (d < 0.5) return { text: '平坦', tone: 'var(--accent-amber-text)', note: '長短天期差距小，曲線接近水平' };
    return { text: '正斜率', tone: POSITIVE, note: '長天期高於短天期，是常態形狀' };
  }, [points]);

  return (
    <ChartCard
      title="殖利率曲線 · Yield Curve"
      sub={shape ? shape.note : '公債期限結構'}
      right={shape && (
        <span className="rounded-md px-2 py-0.5 text-xs font-bold" style={{ color: shape.tone, background: 'var(--bg-tertiary)' }}>
          {shape.text}
        </span>
      )}
    >
      <ResponsiveContainer width="100%" height={230}>
        <LineChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: 'var(--text-secondary)', fontSize: 11, fontWeight: 600 }}
                 tickLine={false} axisLine={{ stroke: CHART_AXIS }} />
          <YAxis {...numAxis({ domain: ['auto', 'auto'], tickFormatter: v => `${v.toFixed(1)}%` })} />
          <Tooltip content={<CurveTooltip />} cursor={{ stroke: CHART_REFERENCE, strokeDasharray: '3 3' }} />
          {CURVE_LINES.map(c => (
            <Line
              key={c.key}
              type="monotone"
              dataKey={c.key}
              name={c.label}
              stroke={c.color}
              strokeWidth={2}
              strokeDasharray={c.dash || undefined}
              dot={{ r: 3, fill: c.color, strokeWidth: 0 }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
          ))}
          <Legend verticalAlign="top" height={22} iconType="plainline" iconSize={16}
                  wrapperStyle={{ fontSize: 11, color: 'var(--text-meta)' }} />
        </LineChart>
      </ResponsiveContainer>

      {/* 圖是走勢，數字要看得到精確值 —— 同一份資料的表格版 */}
      <table className="mt-2 w-full text-[11.5px]">
        <thead>
          <tr className="text-[var(--text-muted)]">
            <th className="py-1 text-left font-semibold">天期</th>
            {CURVE_LINES.map(c => <th key={c.key} className="py-1 text-right font-semibold">{c.label}</th>)}
            <th className="py-1 text-right font-semibold">年變動</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {points.map(p => {
            const d = p.now != null && p.y1 != null ? p.now - p.y1 : null;
            return (
              <tr key={p.symbol} className="border-t border-[var(--glass-border)]">
                <td className="py-1 font-sans font-semibold text-[var(--text-primary)]">{p.label}</td>
                {CURVE_LINES.map(c => (
                  <td key={c.key} className="py-1 text-right text-[var(--text-secondary)]">
                    {p[c.key] != null ? `${p[c.key].toFixed(3)}%` : '—'}
                  </td>
                ))}
                <td className="py-1 text-right font-semibold"
                    style={{ color: d == null ? 'var(--text-muted)' : d >= 0 ? POSITIVE : NEGATIVE }}>
                  {d == null ? '—' : `${d > 0 ? '+' : ''}${d.toFixed(3)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ChartCard>
  );
}

// ── 2. 利差歷史 ───────────────────────────────────────────────────────────────

function SpreadTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const s = payload.find(p => p.dataKey === 's10y3m')?.value;
  return (
    <div style={chartTooltipStyle} className="px-3 py-2">
      <p style={chartTooltipLabelStyle} className="text-[11px]">{label}</p>
      {payload.map(p => (
        <p key={p.dataKey} className="flex items-center gap-1.5 text-[12px] text-[var(--text-primary)]">
          <span className="inline-block h-0.5 w-3 rounded" style={{ background: p.color }} />
          {p.dataKey === 's10y3m' ? '10Y − 3M' : '10Y − 5Y'}
          <b className="font-mono tabular-nums">{p.value?.toFixed(3)}</b>
        </p>
      ))}
      {s != null && s < 0 && (
        <p className="mt-1 text-[11px] font-semibold" style={{ color: NEGATIVE }}>⚠ 倒掛</p>
      )}
    </div>
  );
}

function SpreadHistory({ spreads, invertedDays, lastInverted }) {
  const rows = useMemo(() => spreads.filter(s => s.s10y3m != null), [spreads]);
  // 0 在 Y 軸的哪個高度 —— 漸層要在那裡換色，才是「跌破 0 才變紅」而不是整條紅
  const zeroOffset = useMemo(() => {
    const vals = rows.map(r => r.s10y3m);
    const hi = Math.max(...vals, 0);
    const lo = Math.min(...vals, 0);
    return hi === lo ? 0.5 : hi / (hi - lo);
  }, [rows]);

  if (!rows.length) {
    return <ChartCard title="利差 · Yield Spread"><Empty /></ChartCard>;
  }
  const last = rows[rows.length - 1];

  return (
    <ChartCard
      title="利差 · 10Y − 3M"
      sub="0 是倒掛分界。倒掛＝短錢比長錢貴，歷史上領先衰退約 12–18 個月"
      right={
        <span className="text-right">
          <span className="block font-mono text-lg font-bold tabular-nums leading-none"
                style={{ color: last.s10y3m >= 0 ? POSITIVE : NEGATIVE }}>
            {last.s10y3m > 0 ? '+' : ''}{last.s10y3m.toFixed(3)}
          </span>
          <span className="text-[10.5px] text-[var(--text-meta)]">
            區間內倒掛 {invertedDays} 天{lastInverted ? ` · 最近 ${lastInverted}` : ''}
          </span>
        </span>
      }
    >
      <ResponsiveContainer width="100%" height={230}>
        <ComposedChart data={rows} margin={{ top: 8, right: 60, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="spreadFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset={zeroOffset} stopColor="#0052ff" stopOpacity={0.18} />
              <stop offset={zeroOffset} stopColor="#dc2626" stopOpacity={0.18} />
            </linearGradient>
            <linearGradient id="spreadStroke" x1="0" y1="0" x2="0" y2="1">
              <stop offset={zeroOffset} stopColor="#0052ff" />
              <stop offset={zeroOffset} stopColor="#dc2626" />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis {...dateAxis(rows)} />
          <YAxis {...numAxis({ domain: ['auto', 'auto'], tickFormatter: v => v.toFixed(1) })} />
          <Tooltip content={<SpreadTooltip />} cursor={{ stroke: CHART_REFERENCE, strokeDasharray: '3 3' }} />
          <ReferenceLine y={0} stroke={CHART_REFERENCE} strokeWidth={1.5} />
          <Area type="monotone" dataKey="s10y3m" name="10Y − 3M"
                stroke="url(#spreadStroke)" strokeWidth={2} fill="url(#spreadFill)"
                dot={false} activeDot={{ r: 4 }} isAnimationActive={false}
                label={endLabel('10Y−3M', '#0052ff', rows.length - 1)} />
          <Line type="monotone" dataKey="s10y5y" name="10Y − 5Y"
                stroke={SERIES[1]} strokeWidth={1.5} strokeDasharray="5 3"
                dot={false} activeDot={{ r: 4 }} isAnimationActive={false}
                label={endLabel('10Y−5Y', SERIES[1], rows.length - 1)} />
          <Legend verticalAlign="top" height={22} iconType="plainline" iconSize={14}
                  wrapperStyle={{ fontSize: 11, color: 'var(--text-meta)' }} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ── 3. 指標卡 ─────────────────────────────────────────────────────────────────

// 現值落在所選區間的哪裡 —— 「4.78% 是高是低」要有參照才答得出來
function RangeMeter({ stats, unit }) {
  if (!stats || stats.percentile == null) return null;
  return (
    <div className="mt-1.5">
      <div className="relative h-1.5 rounded-full bg-[var(--bg-tertiary)]">
        <span className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 rounded-full bg-[var(--accent-cyan)]"
              style={{ left: `calc(${stats.percentile}% - 1.5px)` }} />
      </div>
      <div className="mt-0.5 flex justify-between text-[10px] tabular-nums text-[var(--text-muted)]">
        <span>{fmt(stats.min, unit)}</span>
        <span className="font-semibold text-[var(--text-meta)]">區間位置 {stats.percentile}%</span>
        <span>{fmt(stats.max, unit)}</span>
      </div>
    </div>
  );
}

function IndicatorCard({ item, series, selected, compared, onSelect, onCompare, compareColor, canCompare }) {
  const stats = series?.stats;
  const points = series?.points || [];
  const up = (stats?.change ?? 0) >= 0;

  return (
    <div
      className={`rounded-xl border p-3 transition-colors ${
        selected
          ? 'border-[var(--accent-cyan)] bg-[rgba(0,82,255,0.04)]'
          : 'border-[var(--glass-border)] bg-[var(--bg-secondary)] hover:border-[var(--glass-border-hover)]'
      }`}
    >
      <button type="button" onClick={() => onSelect(item.symbol)}
              className="w-full cursor-pointer text-left" aria-pressed={selected}>
        <div className="flex items-start justify-between gap-2">
          <span className="text-xs font-medium text-[var(--text-muted)]">{item.label}</span>
          <ChangeChip item={item} />
        </div>
        <span className="mt-0.5 block font-mono text-xl font-bold tabular-nums">
          {fmt(item.price, item.unit)}
        </span>

        <div className="mt-1 h-9">
          {points.length > 1 && (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                <Area type="monotone" dataKey="close" isAnimationActive={false}
                      stroke={up ? POSITIVE : NEGATIVE} strokeWidth={1.5}
                      fill={up ? 'rgba(5,150,105,0.12)' : 'rgba(220,38,38,0.12)'} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="flex items-baseline justify-between text-[11px]">
          <span className="text-[var(--text-meta)]">區間變動</span>
          <span className="font-mono font-semibold tabular-nums"
                style={{ color: up ? POSITIVE : NEGATIVE }}>{fmtDelta(stats, item.unit)}</span>
        </div>
        <RangeMeter stats={stats} unit={item.unit} />
      </button>

      <button
        type="button"
        onClick={() => onCompare(item.symbol)}
        disabled={!compared && !canCompare}
        title={!compared && !canCompare ? `疊圖最多 ${MAX_COMPARE} 檔，先取消一檔` : '加入下方的疊圖比較'}
        className={`mt-2 flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-semibold transition-colors disabled:cursor-default disabled:opacity-40 ${
          compared ? 'text-white' : 'border-[var(--glass-border)] text-[var(--text-meta)] hover:text-[var(--text-primary)]'
        }`}
        style={compared ? { background: compareColor, borderColor: compareColor } : undefined}
      >
        {compared ? <Check size={11} /> : null}
        {compared ? '疊圖中' : '加入疊圖'}
      </button>
    </div>
  );
}

// ── 4. 單檔大圖 ───────────────────────────────────────────────────────────────

function DetailTooltip({ active, payload, label, unit, name }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={chartTooltipStyle} className="px-3 py-2">
      <p style={chartTooltipLabelStyle} className="text-[11px]">{label}</p>
      <p className="text-[13px] font-semibold text-[var(--text-primary)]">
        {name} <b className="font-mono tabular-nums">{fmt(payload[0].value, unit)}</b>
      </p>
    </div>
  );
}

function DetailChart({ item, series, period }) {
  const points = series?.points || [];
  const stats = series?.stats;
  if (!points.length) return <ChartCard title="走勢"><Empty /></ChartCard>;

  const up = (stats?.change ?? 0) >= 0;
  const color = up ? '#0052ff' : NEGATIVE;

  return (
    <ChartCard
      title={`${item.label} · ${item.symbol}`}
      sub={`${PERIODS.find(p => p.key === period)?.label} 走勢 · ${stats?.n ?? 0} 個交易日`}
      right={
        <div className="flex items-end gap-4 text-right">
          <span>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--text-muted)]">區間低</span>
            <span className="font-mono text-[13px] font-semibold tabular-nums text-[var(--text-secondary)]">
              {fmt(stats?.min, item.unit)}
            </span>
          </span>
          <span>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--text-muted)]">區間高</span>
            <span className="font-mono text-[13px] font-semibold tabular-nums text-[var(--text-secondary)]">
              {fmt(stats?.max, item.unit)}
            </span>
          </span>
          <span>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--text-muted)]">現值</span>
            <span className="font-mono text-lg font-bold tabular-nums">{fmt(stats?.last, item.unit)}</span>
          </span>
          <span>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--text-muted)]">區間變動</span>
            <span className="font-mono text-lg font-bold tabular-nums" style={{ color: up ? POSITIVE : NEGATIVE }}>
              {fmtDelta(stats, item.unit)}
            </span>
          </span>
        </div>
      }
    >
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="detailFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis {...dateAxis(points)} />
          <YAxis {...numAxis({ domain: ['auto', 'auto'], tickFormatter: v => (item.unit === '%' ? v.toFixed(1) : v.toFixed(0)) })} />
          <Tooltip content={<DetailTooltip unit={item.unit} name={item.label} />}
                   cursor={{ stroke: CHART_REFERENCE, strokeDasharray: '3 3' }} />
          {/* 高低線不標字：序列常常剛好貼著高點，字會壓在線上；數字在標題列 */}
          {stats?.max != null && <ReferenceLine y={stats.max} stroke={CHART_REFERENCE} strokeDasharray="4 4" />}
          {stats?.min != null && <ReferenceLine y={stats.min} stroke={CHART_REFERENCE} strokeDasharray="4 4" />}
          <Area type="monotone" dataKey="close" stroke={color} strokeWidth={2}
                fill="url(#detailFill)" dot={false} activeDot={{ r: 5, strokeWidth: 2, stroke: 'var(--bg-primary)' }}
                isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ── 5. 疊圖比較（rebase 100）──────────────────────────────────────────────────

function CompareTooltip({ active, payload, label, meta }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={chartTooltipStyle} className="px-3 py-2">
      <p style={chartTooltipLabelStyle} className="text-[11px]">{label}</p>
      {payload.map(p => {
        const m = meta[p.dataKey];
        return (
          <p key={p.dataKey} className="flex items-center gap-1.5 text-[12px] text-[var(--text-primary)]">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: p.color }} />
            {m?.label || p.dataKey}
            <b className="font-mono tabular-nums">{p.value?.toFixed(1)}</b>
            <span className="text-[var(--text-muted)]">（{fmt(m?.byDate?.[label], m?.unit)}）</span>
          </p>
        );
      })}
    </div>
  );
}

function CompareChart({ symbols, series, period }) {
  // 各檔的交易日不完全一致（期貨、公債、指數的假日規則不同）→ 以日期為鍵合併
  const { rows, meta } = useMemo(() => {
    const byDate = new Map();
    const meta = {};
    symbols.forEach((sym) => {
      const s = series[sym];
      if (!s) return;
      meta[sym] = { label: s.label, unit: s.unit, byDate: {} };
      (s.points || []).forEach((p) => {
        if (!byDate.has(p.date)) byDate.set(p.date, { date: p.date });
        byDate.get(p.date)[sym] = p.rebased;
        meta[sym].byDate[p.date] = p.close;
      });
    });
    return { rows: [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date)), meta };
  }, [symbols, series]);

  if (!rows.length) return <ChartCard title="疊圖比較"><Empty /></ChartCard>;

  return (
    <ChartCard
      title="疊圖比較 · 相對走勢"
      sub={`全部以區間第一天 = 100 重新基準化。單位不同的指標只有這樣才比得起來 —— 這一頁沒有任何雙 Y 軸圖。最多 ${MAX_COMPARE} 檔。`}
      right={<span className="text-[11px] text-[var(--text-meta)]">{PERIODS.find(p => p.key === period)?.label}</span>}
    >
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={rows} margin={{ top: 8, right: 76, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis {...dateAxis(rows)} />
          <YAxis {...numAxis({ domain: ['auto', 'auto'] })} />
          <Tooltip content={<CompareTooltip meta={meta} />} cursor={{ stroke: CHART_REFERENCE, strokeDasharray: '3 3' }} />
          <ReferenceLine y={100} stroke={CHART_REFERENCE} strokeDasharray="4 4" />
          {symbols.map((sym, i) => (
            <Line key={sym} type="monotone" dataKey={sym} name={meta[sym]?.label || sym}
                  stroke={SERIES[i]} strokeWidth={2} dot={false}
                  activeDot={{ r: 5, strokeWidth: 2, stroke: 'var(--bg-primary)' }}
                  connectNulls isAnimationActive={false}
                  label={endLabel(meta[sym]?.label || sym, SERIES[i], rows.length - 1)} />
          ))}
          <Legend verticalAlign="top" height={22} iconType="plainline" iconSize={14}
                  wrapperStyle={{ fontSize: 11, color: 'var(--text-meta)' }} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function Empty() {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
      這個區間沒有資料
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Macro() {
  const [data, setData] = useState(null);
  const [curve, setCurve] = useState(null);
  const [series, setSeries] = useState({});
  const [period, setPeriod] = useState('1y');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [seriesLoading, setSeriesLoading] = useState(true);

  const [detail, setDetail] = useState('^TNX');
  const [compare, setCompare] = useState(['^TNX', 'CL=F', '^VIX']);

  const items = useMemo(() => data?.items || [], [data]);
  // 卡片的迷你走勢、單檔大圖、疊圖吃的是同一份序列 —— 聯集一次抓完，不是每張圖各打一次
  const needed = useMemo(
    () => [...new Set([...items.map(i => i.symbol), detail, ...compare])].filter(Boolean),
    [items, detail, compare],
  );

  const loadOverview = useCallback((bust = false) => {
    if (bust) setRefreshing(true); else setLoading(true);
    const head = bust ? axios.delete('/api/macro/cache') : Promise.resolve();
    head
      .then(() => Promise.all([
        axios.get('/api/macro/overview'),
        axios.get('/api/macro/curve', { params: { period } }),
      ]))
      .then(([o, c]) => { setData(o.data); setCurve(c.data); })
      .catch(() => { setData(null); setCurve(null); })
      .finally(() => { setLoading(false); setRefreshing(false); });
  }, [period]);

  useEffect(() => { loadOverview(); }, [loadOverview]);

  useEffect(() => {
    if (!needed.length) return;
    let alive = true;
    setSeriesLoading(true);
    axios.get('/api/macro/series', { params: { symbols: needed.join(','), period } })
      .then(r => { if (alive) setSeries(r.data.series || {}); })
      .catch(() => { if (alive) setSeries({}); })
      .finally(() => { if (alive) setSeriesLoading(false); });
    return () => { alive = false; };
  }, [needed, period]);

  const toggleCompare = (sym) => setCompare((cur) => (
    cur.includes(sym) ? cur.filter(s => s !== sym) : cur.length < MAX_COMPARE ? [...cur, sym] : cur
  ));

  const byType = (type) => items.filter(i => i.type === type);
  const detailItem = items.find(i => i.symbol === detail) || items[0];
  const yc = data?.yield_curve;

  return (
    <div className="container space-y-5 py-6">
      <ClusterTabs items={MARKET_TABS} active="/macro" />

      {/* Header + 全頁共用的區間切換 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] shadow-lg">
            <Activity size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold">Macro Overview</h1>
            <p className="text-sm text-[var(--text-muted)]">殖利率曲線 · 利差 · 商品 · DXY · VIX</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-0.5"
               role="group" aria-label="時間區間">
            {PERIODS.map(p => (
              <button
                key={p.key}
                onClick={() => setPeriod(p.key)}
                aria-pressed={period === p.key}
                className={`cursor-pointer rounded-md px-2.5 py-1 font-mono text-xs font-bold transition-colors ${
                  period === p.key
                    ? 'bg-[rgba(0,82,255,0.12)] text-[var(--accent-cyan)]'
                    : 'text-[var(--text-meta)] hover:text-[var(--text-primary)]'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => loadOverview(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--glass-border)] px-3 py-1.5 text-xs text-[var(--text-muted)] transition-colors hover:bg-[rgba(0,0,0,0.03)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {loading && (
        <div className="glass flex items-center justify-center gap-3 rounded-xl p-16 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-cyan)]" />
          <span className="text-sm">Fetching macro data…</span>
        </div>
      )}

      {!loading && !data && (
        <div className="glass flex items-center justify-center gap-2 rounded-xl p-12 text-sm text-[var(--accent-danger-text)]">
          <AlertTriangle size={16} /> 抓不到總經資料
        </div>
      )}

      {!loading && data && (
        <>
          {/* 倒掛橫幅：狀態色一律配文字與圖示，不做 color-alone */}
          {yc && (
            <div className={`glass flex items-center gap-4 rounded-xl border px-5 py-4 ${
              yc.inverted_10y_3m
                ? 'border-[rgba(220,38,38,0.3)] bg-[rgba(220,38,38,0.04)]'
                : 'border-[rgba(5,150,105,0.25)] bg-[rgba(5,150,105,0.04)]'
            }`}>
              {yc.inverted_10y_3m
                ? <AlertTriangle size={18} className="shrink-0 text-[var(--accent-danger-text)]" />
                : <TrendingUp size={18} className="shrink-0 text-[var(--accent-success-text)]" />}
              <div>
                <p className="text-sm font-semibold">
                  殖利率曲線{yc.inverted_10y_3m ? '倒掛' : '正常'} · Yield Curve {yc.inverted_10y_3m ? 'Inverted' : 'Normal'}
                </p>
                <p className="mt-0.5 text-xs text-[var(--text-meta)]">
                  10Y − 3M：<span className="font-mono font-semibold">{yc.spread_10y_3m > 0 ? '+' : ''}{yc.spread_10y_3m}%</span>
                  &nbsp;·&nbsp;
                  10Y − 5Y：<span className="font-mono font-semibold">{yc.spread_10y_5y > 0 ? '+' : ''}{yc.spread_10y_5y}%</span>
                </p>
              </div>
            </div>
          )}

          {/* 1 + 2：曲線與利差 */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {curve?.points?.length
              ? <YieldCurve points={curve.points} />
              : <ChartCard title="殖利率曲線 · Yield Curve"><Empty /></ChartCard>}
            {curve?.spreads?.length
              ? <SpreadHistory spreads={curve.spreads} invertedDays={curve.inverted_days} lastInverted={curve.last_inverted} />
              : <ChartCard title="利差 · 10Y − 3M"><Empty /></ChartCard>}
          </div>

          {/* 3：指標卡 —— 點卡片換大圖，點下方按鈕加進疊圖 */}
          {[
            { type: 'yield', title: 'Treasury Yields', cols: 'sm:grid-cols-4' },
            { type: 'commodity', title: 'Commodities', cols: 'sm:grid-cols-3' },
            { type: 'fx', title: 'FX / Volatility', cols: 'sm:grid-cols-4' },
          ].map(sec => {
            const list = sec.type === 'fx'
              ? [...byType('currency'), ...byType('volatility')]
              : byType(sec.type);
            if (!list.length) return null;
            return (
              <section key={sec.type}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-[var(--text-muted)]">
                  {sec.title}
                </p>
                <div className={`grid grid-cols-2 gap-3 ${sec.cols}`}>
                  {list.map(item => (
                    <IndicatorCard
                      key={item.symbol}
                      item={item}
                      series={series[item.symbol]}
                      selected={detail === item.symbol}
                      compared={compare.includes(item.symbol)}
                      compareColor={SERIES[compare.indexOf(item.symbol)]}
                      canCompare={compare.length < MAX_COMPARE}
                      onSelect={setDetail}
                      onCompare={toggleCompare}
                    />
                  ))}
                </div>
              </section>
            );
          })}

          {seriesLoading && (
            <p className="flex items-center justify-center gap-2 text-xs text-[var(--text-muted)]">
              <Loader2 size={13} className="animate-spin" /> 載入 {PERIODS.find(p => p.key === period)?.label} 序列…
            </p>
          )}

          {/* 4 + 5：單檔大圖與疊圖比較 */}
          {detailItem && <DetailChart item={detailItem} series={series[detailItem.symbol]} period={period} />}
          {compare.length > 0 && <CompareChart symbols={compare} series={series} period={period} />}

          <p className="text-right text-[10px] text-[var(--text-muted)]">
            Data via yfinance · 快取 30 分 · last fetch {data.fetched_at?.slice(0, 19).replace('T', ' ')} UTC
          </p>
        </>
      )}
    </div>
  );
}
