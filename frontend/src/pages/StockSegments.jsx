/**
 * 分部門營收與利潤。
 *
 * 版面的順序就是讀的順序，三層由粗到細：
 *
 *   1. 最新一期摘要 —— 一個部門一列，營收、佔比、YoY、利潤率並排，依營收排序。
 *      「這家公司靠什麼賺錢、誰在成長」在這一層就要答完，不用讀表格。
 *   2. 三張圖 —— 組成走勢（誰的份額在被吃掉）、YoY（誰在成長）、利潤率走勢
 *      （誰在賺錢／惡化）。三個問題各一張圖，不塞雙 Y 軸。
 *   3. 完整表格 —— 精確數字與逐部門科目明細，一次只顯示一種指標
 *      （營收／佔比／YoY／利潤率），四個數字擠在同一格是先前最難讀的地方。
 *
 * 圖上最多 6 個部門加「其他」：超過 6 條線就只剩噪音，而 ABBV 有 182 個部門標籤。
 * 表格不設限，該看細節的人往下捲。
 *
 * 顏色只是輔助 —— 部門在摘要、圖例、圖上是同一個顏色，但每處都帶文字標籤，
 * 色盲讀者不會少任何資訊（配色檢查寫在 chartTheme.js）。
 */
import { Fragment, useState, useEffect, useMemo } from 'react';
import { useRoute } from 'wouter';
import axios from '../lib/apiClient';
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, Legend,
    ResponsiveContainer, CartesianGrid, ReferenceLine, Cell, LabelList,
} from 'recharts';
import { Layers, TrendingUp, Percent, Activity } from 'lucide-react';
import StockPageHeader, { StockPageFallback } from '../components/StockPageHeader';
import {
    CHART_GRID, CHART_AXIS, CHART_REFERENCE,
    chartTooltipStyle, chartTooltipLabelStyle,
    SEGMENT_SERIES, SEGMENT_OTHER,
} from '../components/chartTheme';

const MONTH_OPTIONS = [
    { v: 3,  label: '單季' },
    { v: 6,  label: '半年' },
    { v: 9,  label: '前三季' },
    { v: 12, label: '全年' },
];

// 表格一次只顯示一種指標。四個數字擠在同一格時，跨期間比較要用眼睛在雜訊裡挑，
// 換成一次一種，整張表就變成可以橫著掃的一件事。
const TABLE_METRICS = [
    { key: 'revenue', label: '營收',   hint: '每期各部門的營收金額' },
    { key: 'profit',  label: '利潤',   hint: '每期各部門的利潤金額（口徑標在表格上方）' },
    { key: 'share',   label: '佔比',   hint: '各部門佔該期部門加總的百分比' },
    { key: 'yoy',     label: 'YoY',    hint: '營收對去年同期的成長率' },
    { key: 'margin',  label: '利潤率', hint: '部門利潤 ÷ 部門營收' },
];

const KIND_LABELS = { revenue: '營收', profit: '利潤', cost: '成本與費用',
                      capital: '投資與資產', other: '其他' };
const KIND_ORDER = ['revenue', 'profit', 'cost', 'capital', 'other'];

// recharts 的 Legend 預設照名稱字母排序，圖例順序就跟堆疊順序、摘要排序全錯開。
// 常數 key 讓它的穩定排序原封不動地保留我們給的順序（營收由大到小）。
const LEGEND_KEEP_ORDER = () => 0;

const OTHER_KEY = '其他部門';
const MAX_SERIES = SEGMENT_SERIES.length;
const MAX_SUMMARY_ROWS = 14;

function money(v) {
    if (v == null) return '—';
    const a = Math.abs(v);
    // 負號放在錢字號前面。「$-14.04B」要多看一眼才知道是負的，
    // 未分配項與對帳列（AAPL 的 Corporate non-segment）整欄都是負數。
    const sign = v < 0 ? '-' : '';
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
    return `${sign}$${a.toFixed(0)}`;
}
// 座標軸要的是刻度不是精度，多一位小數只會讓軸變寬、把圖擠小
function axisMoney(v) {
    if (v == null) return '';
    const a = Math.abs(v);
    if (a >= 1e9) return `${(v / 1e9).toFixed(0)}B`;
    if (a >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
    if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
    return `${v}`;
}
const signed = (v, digits = 1) =>
    v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const toneOf = (v) => (v == null ? 'var(--text-meta)'
    : v >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)');

// 2026-03-31 → 2026-03。圖的 X 軸擠 40 期時，日不帶任何資訊
// 圖的 x 軸也用會計季；拿不到標記才退回年月
const shortPeriod = (iso, label) => label || (iso || '').slice(0, 7);

const daysBetween = (a, b) =>
    Math.round((new Date(`${a}T00:00:00Z`) - new Date(`${b}T00:00:00Z`)) / 86400000);

/**
 * 去年同期＝「期末日往回 300~430 天的那一期」，不是「上一筆」。
 * 同一份申報同時帶本季與去年同季，取上一筆會拿到相鄰季度，YoY 就成了 QoQ。
 * 後端 /latest 用同一套判斷，兩邊的數字才會對得起來。
 */
function priorYearIndex(periods, i) {
    const cur = periods[i]?.period_end;
    if (!cur) return -1;
    for (let j = i + 1; j < periods.length; j++) {
        const d = daysBetween(cur, periods[j].period_end);
        if (d >= 300 && d <= 430) return j;
        if (d > 430) return -1;
    }
    return -1;
}

function SegmentDot({ color }) {
    // 顏色是輔助編碼，色點永遠跟著文字標籤走，所以對輔助技術隱藏
    return <span aria-hidden="true" className="inline-block w-2 h-2 rounded-full shrink-0"
                 style={{ background: color }} />;
}

/** 佔比的橫條。數字本身一定在旁邊，條只是讓「誰比誰大」一眼看得出來。 */
function ShareBar({ pct, color }) {
    return (
        <div className="h-1.5 w-full rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
            <div className="h-full rounded-full" aria-hidden="true"
                 style={{ width: `${Math.max(0, Math.min(100, pct))}%`, background: color }} />
        </div>
    );
}

/**
 * YoY 條的數值標籤。正條標在右端、負條標在左端 —— recharts 內建的
 * position="right" 對負條會把標籤丟到 0 軸上，正好壓在參考線與相鄰的條上。
 */
function GrowthLabel({ x, y, width, height, value }) {
    if (value == null) return null;
    const positive = value >= 0;
    // 負值的條，recharts 給的 x 是 0 軸那端、width 是負數。取兩端的極值才能不管
    // 慣例都把標籤放在條的外側；直接用 x 會把標籤壓在條上面。
    const left = Math.min(x, x + width);
    const right = Math.max(x, x + width);
    return (
        <text x={positive ? right + 6 : left - 6} y={y + height / 2} dy={4}
              textAnchor={positive ? 'start' : 'end'}
              fill="var(--text-secondary)" fontSize={11}
              style={{ fontVariantNumeric: 'tabular-nums' }}>
            {signed(value)}
        </text>
    );
}

function ChartTooltip({ active, payload, label, unit }) {
    if (!active || !payload?.length) return null;
    const rows = payload.filter((p) => p.value != null);
    if (!rows.length) return null;
    const total = unit === 'money'
        ? rows.reduce((a, p) => a + (p.value || 0), 0) : null;
    return (
        <div style={chartTooltipStyle} className="px-3 py-2">
            <div style={chartTooltipLabelStyle} className="num text-[11px]">{label}</div>
            {rows.map((p) => (
                <div key={p.dataKey} className="flex items-center gap-2 text-[12px] leading-5">
                    <SegmentDot color={p.color || p.stroke || p.fill
                                || (unit === 'yoy' ? toneOf(p.value) : SEGMENT_OTHER)} />
                    <span className="text-[var(--text-secondary)] max-w-[220px] truncate">{p.name}</span>
                    <span className="num ml-auto text-[var(--text-primary)] font-medium">
                        {unit === 'money' ? money(p.value)
                            : unit === 'pct' ? `${p.value.toFixed(1)}%`
                            : signed(p.value)}
                    </span>
                </div>
            ))}
            {total != null && rows.length > 1 && (
                <div className="num mt-1 pt-1 border-t border-[var(--glass-border)]
                                flex justify-between gap-4 text-[11.5px] text-[var(--text-meta)]">
                    <span>合計</span><span>{money(total)}</span>
                </div>
            )}
        </div>
    );
}

/** 圖表外框：標題、副標、右上角的切換都長一樣，省得每張圖各長各的 */
function ChartCard({ icon, title, note, action, children, className = '' }) {
    return (
        <figure className={`rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                            p-4 m-0 ${className}`}>
            <figcaption className="flex items-start gap-3 mb-3">
                <div className="min-w-0">
                    <h2 className="text-[13.5px] font-semibold text-[var(--text-primary)]
                                   flex items-center gap-1.5">
                        {icon} {title}
                    </h2>
                    {note && <p className="text-[11.5px] text-[var(--text-meta)] mt-0.5">{note}</p>}
                </div>
                {action && <div className="ml-auto shrink-0">{action}</div>}
            </figcaption>
            {children}
            <p className="sr-only">圖為視覺摘要，完整數值見下方「完整明細」表格。</p>
        </figure>
    );
}

function Toggle({ options, value, onChange, size = 'sm' }) {
    const pad = size === 'sm' ? 'px-2.5 py-1.5 text-[11.5px]' : 'px-3 py-2 text-[13px]';
    return (
        <div className="inline-flex rounded-lg border border-[var(--glass-border)]
                        bg-[var(--bg-primary)] p-0.5 gap-0.5">
            {options.map((o) => (
                <button key={o.key} type="button" onClick={() => onChange(o.key)}
                        aria-pressed={value === o.key} title={o.hint}
                        className={`ui-focus cursor-pointer rounded-md font-medium ${pad}
                                    transition-colors duration-200 ${
                            value === o.key
                                ? 'bg-[rgba(0,82,255,0.10)] text-[var(--accent-cyan-text)]'
                                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
                    {o.label}
                </button>
            ))}
        </div>
    );
}

// 展開後的完整科目：一列一個科目、一欄一個期間，直接看得出成本結構與資本支出
// 在各期間怎麼走。資料本來就在 API 的 items 裡。
function SegmentDetail({ name, periods, byKey }) {
    const rows = useMemo(() => {
        const map = new Map();                      // concept -> {label, kind, values{}}
        periods.forEach((p) => {
            const seg = byKey[`${p.period_end}|${name}`];
            (seg?.items || []).forEach((it) => {
                if (!map.has(it.concept)) {
                    map.set(it.concept, { label: it.label, kind: it.kind, values: {} });
                }
                map.get(it.concept).values[p.period_end] = it.value;
            });
        });
        return [...map.values()].sort(
            (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)
                      || a.label.localeCompare(b.label));
    }, [name, periods, byKey]);

    if (!rows.length) {
        return <div className="text-[12px] text-[var(--text-meta)]">這個部門沒有更細的科目</div>;
    }
    return (
        <table className="w-full text-[12px] border-collapse">
            <caption className="sr-only">{name} 的完整科目明細</caption>
            <tbody>
                {rows.map((r, i) => {
                    const previousKind = i > 0 ? rows[i - 1].kind : null;
                    const header = r.kind !== previousKind ? r.kind : null;
                    return (
                        // Fragment 要帶 key，否則 React 會對整份清單發出 key 警告
                        <Fragment key={`${r.concept || r.label}-${i}`}>
                            {header && (
                                <tr>
                                    <td colSpan={periods.length + 1}
                                        className="pt-2 pb-1 text-[10.5px] uppercase tracking-wide text-[var(--text-meta)]">
                                        {KIND_LABELS[r.kind] || r.kind}
                                    </td>
                                </tr>
                            )}
                            <tr>
                                <td className="py-0.5 pr-3 text-[var(--text-secondary)] whitespace-nowrap">
                                    {r.label}
                                </td>
                                {periods.map((p) => (
                                    <td key={p.period_end}
                                        className="num py-0.5 px-3 text-right text-[var(--text-primary)] whitespace-nowrap">
                                        {r.values[p.period_end] != null ? money(r.values[p.period_end]) : '—'}
                                    </td>
                                ))}
                            </tr>
                        </Fragment>
                    );
                })}
            </tbody>
        </table>
    );
}

export default function StockSegments() {
    const [, params] = useRoute('/stock/:ticker/segments');
    const ticker = params?.ticker;

    const [months, setMonths]   = useState(3);
    const [ccy, setCcy]         = useState(null);   // 雙幣別申報才用得到
    const [cols, setCols]       = useState(8);      // 顯示幾個期間（圖與表共用）
    const [openSeg, setOpenSeg] = useState(null);   // 展開哪個部門的完整科目
    const [mixMode, setMixMode] = useState('amount');   // 組成走勢：金額 or 佔比
    const [metric, setMetric]   = useState('revenue');  // 表格顯示哪一種指標
    const [data, setData]       = useState(null);
    const [axisIdx, setAxisIdx] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState('');

    const [narrow, setNarrow] = useState(
        () => typeof window !== 'undefined' && window.matchMedia?.('(max-width: 639px)').matches);
    useEffect(() => {
        const mq = window.matchMedia?.('(max-width: 639px)');
        if (!mq) return undefined;
        const onChange = (e) => setNarrow(e.matches);
        mq.addEventListener('change', onChange);
        return () => mq.removeEventListener('change', onChange);
    }, []);

    // recharts 不會自己看系統偏好，動畫得自己關
    const reduceMotion = useMemo(
        () => typeof window !== 'undefined'
              && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches, []);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        setError('');
        axios.get(`/api/segments/${ticker}`, { params: { months, ...(ccy ? { currency: ccy } : {}) } })
            .then(({ data: d }) => {
                setData(d);
                setAxisIdx(0);
                if (!d.axes?.length) setError(d.note || '這檔沒有分部門資料');
            })
            // 503 = 背景排程正在寫入 DB。講清楚是「暫時」的，不然使用者會以為
            // 資料掉了而去重跑爬蟲（見 backend/app/dbro.py）
            .catch((e) => setError(e?.response?.status === 503
                ? '資料庫正在更新中，稍後重新整理即可'
                : '讀取分部門資料失敗'))
            .finally(() => setLoading(false));
    }, [ticker, months, ccy]);

    const axis = data?.axes?.[axisIdx] ?? null;

    // 每個部門一列、每個期間一欄；期間新到舊。
    // 小計與合併列另外分出來排到最後：它們跟真部門並排時會被誤讀成「又一個部門」。
    const table = useMemo(() => {
        if (!axis) return null;
        const periods = axis.periods.slice(0, cols);
        const byKey = {};
        const seen = [];
        const totals = new Set();
        periods.forEach((p) => p.segments.forEach((s) => {
            byKey[`${p.period_end}|${s.segment}`] = s;
            if (!seen.includes(s.segment)) seen.push(s.segment);
            if (s.is_total) totals.add(s.segment);
        }));
        const latest = periods[0]?.period_end;
        const revOf = (n) => byKey[`${latest}|${n}`]?.revenue ?? -Infinity;
        // 依最新一期營收排序：表格第一列就是最重要的部門，不是申報上的字母序
        const real = seen.filter((n) => !totals.has(n)).sort((a, b) => revOf(b) - revOf(a));
        const totalRows = seen.filter((n) => totals.has(n));
        const colorOf = Object.fromEntries(
            real.map((n, i) => [n, i < MAX_SERIES ? SEGMENT_SERIES[i] : SEGMENT_OTHER]));
        const measures = [...new Set(Object.values(byKey)
            .map((s) => s.profit_measure).filter(Boolean))];
        return { periods, real, totalRows, names: [...real, ...totalRows], byKey,
                 colorOf, latest, measures, recon: periods[0]?.reconciliation };
    }, [axis, cols]);

    // 最新一期摘要：營收、佔比、YoY、利潤率各一欄，一列一個部門
    const summary = useMemo(() => {
        if (!table?.periods.length) return null;
        const { periods, real, byKey } = table;
        const cur = periods[0];
        const pIdx = priorYearIndex(periods, 0);
        const prior = pIdx >= 0 ? periods[pIdx] : null;
        const base = real.reduce((a, n) => a + (byKey[`${cur.period_end}|${n}`]?.revenue || 0), 0);
        const rows = real.map((n) => {
            const s = byKey[`${cur.period_end}|${n}`];
            const q = prior ? byKey[`${prior.period_end}|${n}`] : null;
            const yoy = s?.revenue != null && q?.revenue
                ? (s.revenue - q.revenue) / Math.abs(q.revenue) * 100 : null;
            return {
                name: n, color: table.colorOf[n],
                revenue: s?.revenue ?? null,
                share: base && s?.revenue != null ? s.revenue / base * 100 : null,
                yoy, margin: s?.margin_pct ?? null,
                measure: s?.profit_measure ?? null,
            };
        }).filter((r) => r.revenue != null);
        // 摘要要能一眼看完；超過的部門在下方表格裡一個都不會少
        return { periodEnd: cur.period_end, priorEnd: prior?.period_end ?? null, base,
                 rows: rows.slice(0, MAX_SUMMARY_ROWS), hidden: Math.max(0, rows.length - MAX_SUMMARY_ROWS) };
    }, [table]);

    // 圖用的序列：最新一期營收前 6 名各一條，其餘併成「其他部門」。
    // 超過 6 條線就只剩噪音；被併掉的部門在表格裡仍然一列一個。
    const chart = useMemo(() => {
        if (!table?.periods.length) return null;
        const { periods, real, byKey } = table;
        const top = real.slice(0, MAX_SERIES);
        const rest = real.slice(MAX_SERIES);
        const keys = [...top, ...(rest.length ? [OTHER_KEY] : [])];
        const colorOf = { ...table.colorOf, [OTHER_KEY]: SEGMENT_OTHER };

        const chrono = [...periods].reverse();      // 圖是舊到新，表是新到舊
        const mix = chrono.map((p) => {
            const row = { label: shortPeriod(p.period_end, p.label) };
            let total = 0;
            top.forEach((n) => {
                const v = byKey[`${p.period_end}|${n}`]?.revenue ?? null;
                row[n] = v; total += v || 0;
            });
            if (rest.length) {
                const v = rest.reduce((a, n) => a + (byKey[`${p.period_end}|${n}`]?.revenue || 0), 0);
                row[OTHER_KEY] = v || null; total += v;
            }
            row.__total = total;
            return row;
        });
        // 佔比模式：分母是同一期各部門加總，不是合併數 —— 合併數常含未分配項，
        // 用它當分母會讓每期的堆疊都湊不滿 100%，反而看不出份額變化。
        const mixPct = mix.map((r) => {
            const o = { label: r.label, __total: r.__total };
            keys.forEach((k) => { o[k] = r.__total && r[k] != null ? r[k] / r.__total * 100 : null; });
            return o;
        });

        // 利潤率走勢只在這個拆法真的揭露部門利潤時才有意義
        const marginRows = chrono.map((p) => {
            const row = { label: shortPeriod(p.period_end, p.label) };
            top.forEach((n) => { row[n] = byKey[`${p.period_end}|${n}`]?.margin_pct ?? null; });
            return row;
        });
        const hasMargin = marginRows.some((r) => top.some((n) => r[n] != null));

        // YoY 用橫條：部門名稱是長字串，直條會擠成斜排看不清
        const pIdx = priorYearIndex(periods, 0);
        const prior = pIdx >= 0 ? periods[pIdx] : null;
        const growth = prior ? real.map((n) => {
            const a = byKey[`${periods[0].period_end}|${n}`]?.revenue;
            const b = byKey[`${prior.period_end}|${n}`]?.revenue;
            return a != null && b ? { name: n, yoy: (a - b) / Math.abs(b) * 100, revenue: a } : null;
        }).filter(Boolean).sort((x, y) => y.yoy - x.yoy).slice(0, 12) : [];

        return { keys, colorOf, mix, mixPct, marginRows, hasMargin, growth,
                 priorEnd: prior?.period_end ?? null, hiddenCount: rest.length };
    }, [table]);

    if (loading) {
        // 骨架而不是置中 spinner：高度接近實際版面，資料到位時不會整個跳動
        return (
            <div className="container py-10 space-y-6" aria-busy="true" aria-live="polite">
                <div className="sr-only">載入分部門資料中</div>
                <StockPageHeader ticker={ticker} />
                <div className="flex gap-2 mb-4">
                    {[0, 1, 2, 3].map((i) => (
                        <div key={i} className="h-9 w-20 rounded-full bg-[var(--bg-tertiary)] animate-pulse" />
                    ))}
                </div>
                <div className="rounded-xl border border-[var(--glass-border)] overflow-hidden mb-4">
                    {Array.from({ length: 5 }).map((_, i) => (
                        <div key={i} className="h-12 border-b border-[var(--glass-border)]
                                                bg-[var(--bg-tertiary)] animate-pulse opacity-70" />
                    ))}
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                    <div className="h-[300px] rounded-xl bg-[var(--bg-tertiary)] animate-pulse lg:col-span-2" />
                    <div className="h-[280px] rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />
                    <div className="h-[280px] rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />
                </div>
            </div>
        );
    }

    const axisTicks = { fill: 'var(--text-meta)', fontSize: 11 };
    const cat = narrow
        ? { width: 88,  chars: 10, pad: { left: 22, right: 30 } }
        : { width: 140, chars: 20, pad: { left: 44, right: 48 } };

    return (
        <div className="container py-10 animate-fade-in">
            <StockPageHeader
                ticker={ticker}
                meta={
                    <span className="num">
                        {summary && `最新一期 ${summary.periodEnd}`}
                        {summary?.priorEnd && ` · 對比去年同期 ${summary.priorEnd}`}
                        {data?.currency && ` · ${data.currency}`}
                        {' · 來源：SEC 申報的分部門報表'}
                    </span>
                }
            />
            <div className="mt-6">
            {/* 控制列：期間長度是主要的，幣別與期間數是次要的，所以分兩段擺 */}
            <div className="flex gap-2 flex-wrap items-center mb-4">
                {MONTH_OPTIONS.map((o) => (
                    <button key={o.v} onClick={() => setMonths(o.v)} aria-pressed={months === o.v}
                        className={`ui-focus tap cursor-pointer px-3 py-2 rounded-full text-[13px] font-medium border transition-colors duration-200 ${
                            months === o.v
                                ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                        {o.label}
                    </button>
                ))}
                <span className="hidden sm:inline-block w-px h-6 bg-[var(--glass-border)] mx-1"
                      aria-hidden="true" />
                {/* TSM / ASX 是台幣申報並附 USD 折算，同一期間兩種幣別都有，要能切 */}
                {data?.available_currencies?.length > 1 && data.available_currencies.map((c) => (
                    <button key={c} onClick={() => setCcy(c)}
                        aria-pressed={(ccy || data.currency) === c}
                        className={`ui-focus tap cursor-pointer px-2.5 py-2 rounded-full text-[12px] font-medium border transition-colors duration-200 ${
                            (ccy || data.currency) === c
                                ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.12)] border-[rgba(0,82,255,0.35)]'
                                : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                        {c}
                    </button>
                ))}
                <label className="text-[12px] text-[var(--text-meta)] inline-flex items-center gap-1.5">
                    期間數
                    <select value={cols} onChange={(e) => setCols(Number(e.target.value))}
                            aria-label="圖表與表格顯示幾個期間"
                            className="ui-focus tap num rounded-lg border border-[var(--glass-border)]
                                       bg-[var(--bg-secondary)] px-2 py-1.5 text-[12px]
                                       text-[var(--text-primary)] cursor-pointer">
                        {[4, 6, 8, 12, 20, 40].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>

            {error && (
                <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                                p-6 text-center text-[var(--text-meta)] text-[13px]">
                    {error}
                </div>
            )}

            {/* 一家公司常有好幾種拆法（終端市場別 / 通路別 / 部門別），各自加總都等於
                總營收，所以分開選、不合併顯示 */}
            {data?.axes?.length > 1 && (
                <div className="flex gap-2 flex-wrap mb-4">
                    {data.axes.map((a, i) => (
                        <button key={a.report_name} onClick={() => { setAxisIdx(i); setOpenSeg(null); }}
                            aria-pressed={axisIdx === i}
                            className={`ui-focus tap cursor-pointer px-3 py-2 rounded-lg text-[12px] border text-left transition-colors duration-200 ${
                                axisIdx === i
                                    ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.08)] border-[rgba(0,82,255,0.35)]'
                                    : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)]'}`}>
                            <Layers size={11} className="inline mr-1" aria-hidden="true" />
                            {a.report_name.length > 46 ? `${a.report_name.slice(0, 46)}…` : a.report_name}
                            <span className="opacity-60"> · {a.segment_count} 部門{a.has_profit ? ' · 含利潤' : ''}</span>
                        </button>
                    ))}
                </div>
            )}

            {/* 同一張申報表常同時塞好幾種互相重疊的拆法（地區別＋收入類型別）、
                或父層與子層並列，加總自然對不上合併數。照實說，不假裝精準。

                這段擺在摘要與圖之前，不是擺在頁尾：加總對不上時，下面每一個佔比、
                每一根堆疊都被重複計算過，得先知道這件事再讀，事後補一句沒有用。 */}
            {table?.recon?.reconciles === false && (
                <div className="mb-4 rounded-lg border border-[rgba(245,158,11,0.3)]
                                bg-[rgba(245,158,11,0.06)] p-3 text-[12px] text-[var(--text-secondary)]">
                    這個拆法的部門加總是 {money(table.recon.sum_of_segments)}，
                    跟合併數 {money(table.recon.total)} 差 {table.recon.diff_pct}%，
                    所以下面的佔比與堆疊圖含重複計算的部分。
                    通常是因為這張表同時列了好幾種重疊的拆法（例如地區別與收入類型別各一套），
                    或父層與子層並列，也可能它本來就只揭露部分產品線。
                    要看不重疊的拆法，換上面另一個維度。
                </div>
            )}

            {/* ── 第一層：最新一期摘要 ───────────────────────────────────────── */}
            {summary?.rows.length > 0 && (
                <section aria-labelledby="seg-summary"
                         className="rounded-xl border border-[var(--glass-border)]
                                    bg-[var(--bg-secondary)] overflow-hidden mb-4">
                    <h2 id="seg-summary" className="sr-only">最新一期各部門摘要</h2>
                    <div className="hidden sm:grid grid-cols-[minmax(0,1fr)_100px_136px_92px_92px]
                                    gap-3 px-4 py-2 text-[11px] text-[var(--text-meta)]
                                    border-b border-[var(--glass-border)]">
                        <span>部門</span>
                        <span className="text-right">營收</span>
                        <span className="text-right">佔比</span>
                        <span className="text-right">YoY</span>
                        <span className="text-right">利潤率</span>
                    </div>
                    {summary.rows.map((r) => (
                        <div key={r.name}
                             className="grid grid-cols-2 sm:grid-cols-[minmax(0,1fr)_100px_136px_92px_92px]
                                        gap-x-3 gap-y-1 px-4 py-2.5 items-center
                                        border-b border-[var(--glass-border)] last:border-b-0">
                            <div className="col-span-2 sm:col-span-1 flex items-center gap-2 min-w-0">
                                <SegmentDot color={r.color} />
                                <span className="text-[13px] text-[var(--text-primary)] truncate"
                                      title={r.name}>{r.name}</span>
                            </div>
                            <div className="num text-[13px] font-medium text-[var(--text-primary)] text-right">
                                {money(r.revenue)}
                            </div>
                            <div className="flex items-center gap-2">
                                <ShareBar pct={r.share ?? 0} color={r.color} />
                                <span className="num text-[11.5px] text-[var(--text-meta)] w-11 text-right shrink-0">
                                    {r.share != null ? `${r.share.toFixed(1)}%` : '—'}
                                </span>
                            </div>
                            <div className="num text-[12.5px] text-right" style={{ color: toneOf(r.yoy) }}>
                                <span className="sm:hidden text-[10.5px] text-[var(--text-meta)] mr-1">YoY</span>
                                {signed(r.yoy)}
                            </div>
                            <div className="num text-[12.5px] text-right text-[var(--text-secondary)]"
                                 title={r.measure ? `利潤口徑：${r.measure}` : undefined}>
                                <span className="sm:hidden text-[10.5px] text-[var(--text-meta)] mr-1">利潤率</span>
                                {r.margin != null ? `${r.margin.toFixed(1)}%` : '—'}
                            </div>
                        </div>
                    ))}
                    <div className="num flex flex-wrap gap-x-4 gap-y-1 px-4 py-2
                                    text-[11.5px] text-[var(--text-meta)] bg-[var(--bg-tertiary)]">
                        <span>部門加總 {money(summary.base)}</span>
                        {table?.recon?.total != null && <span>合併數 {money(table.recon.total)}</span>}
                        {summary.hidden > 0 && <span>另有 {summary.hidden} 個部門，見下方表格</span>}
                        {!summary.priorEnd && <span>沒有可比的去年同期，YoY 從缺</span>}
                    </div>
                </section>
            )}

            {/* ── 第二層：圖 ─────────────────────────────────────────────────── */}
            {chart && chart.mix.length > 1 && (
                <div className="grid gap-4 lg:grid-cols-2 mb-4">
                    <ChartCard
                        className="lg:col-span-2"
                        icon={mixMode === 'amount'
                            ? <TrendingUp size={14} aria-hidden="true" />
                            : <Percent size={14} aria-hidden="true" />}
                        title="營收組成走勢"
                        note={mixMode === 'amount'
                            ? '堆疊高度是各部門加總；看的是總量怎麼長、誰貢獻的'
                            : '每期歸一到 100%；看的是份額被誰吃掉'}
                        action={<Toggle value={mixMode} onChange={setMixMode}
                                        options={[{ key: 'amount', label: '金額' },
                                                  { key: 'share',  label: '佔比' }]} />}>
                        <div className="h-[300px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={mixMode === 'amount' ? chart.mix : chart.mixPct}
                                          margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                                    <CartesianGrid stroke={CHART_GRID} vertical={false} />
                                    <XAxis dataKey="label" tick={axisTicks} stroke={CHART_AXIS}
                                           minTickGap={16} tickLine={false} />
                                    <YAxis tick={axisTicks} stroke={CHART_AXIS} width={52}
                                           tickLine={false} axisLine={false}
                                           domain={mixMode === 'share' ? [0, 100] : undefined}
                                           tickFormatter={mixMode === 'amount'
                                               ? axisMoney : (v) => `${Math.round(v)}%`} />
                                    <Tooltip cursor={{ fill: 'rgba(148,163,184,0.12)' }}
                                             content={<ChartTooltip unit={mixMode === 'amount' ? 'money' : 'pct'} />} />
                                    <Legend wrapperStyle={{ fontSize: 11.5, paddingTop: 6 }}
                                            iconType="circle" iconSize={8}
                                            itemSorter={LEGEND_KEEP_ORDER} />
                                    {chart.keys.map((k) => (
                                        <Bar key={k} dataKey={k} name={k} stackId="rev"
                                             fill={chart.colorOf[k]} maxBarSize={56}
                                             isAnimationActive={!reduceMotion} />
                                    ))}
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                        {chart.hiddenCount > 0 && (
                            <p className="text-[11px] text-[var(--text-meta)] mt-2">
                                圖上只畫營收前 {MAX_SERIES} 名，其餘 {chart.hiddenCount} 個部門併成「{OTHER_KEY}」。
                                完整清單在下方表格。
                            </p>
                        )}
                    </ChartCard>

                    <ChartCard icon={<TrendingUp size={14} aria-hidden="true" />} title="各部門營收 YoY"
                               note={chart.priorEnd
                                   ? `${summary?.periodEnd} 對 ${chart.priorEnd}`
                                   : '沒有可比的去年同期'}>
                        {chart.growth.length ? (
                            <div style={{ height: Math.max(200, chart.growth.length * 30 + 32) }}>
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={chart.growth} layout="vertical"
                                              margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
                                        <CartesianGrid stroke={CHART_GRID} horizontal={false} />
                                        {/* 兩端各留一段像素給數值標籤。用 padding 而不是自訂 domain：
                                            自訂 domain 會讓 recharts 拿沒進位過的邊界當刻度，
                                            軸上就出現「-22.628053585500039%」這種東西。 */}
                                        <XAxis type="number" tick={axisTicks} stroke={CHART_AXIS}
                                               tickFormatter={(v) => `${+v.toFixed(1)}%`} tickLine={false}
                                               padding={cat.pad} />
                                        <YAxis type="category" dataKey="name" width={cat.width}
                                               tick={{ ...axisTicks, fontSize: 11 }} stroke={CHART_AXIS}
                                               tickLine={false} axisLine={false}
                                               tickFormatter={(v) => (v.length > cat.chars
                                                   ? `${v.slice(0, cat.chars)}…` : v)} />
                                        <Tooltip cursor={{ fill: 'rgba(148,163,184,0.12)' }}
                                                 content={<ChartTooltip unit="yoy" />} />
                                        {/* 0 是成長與衰退的分界，沒有這條線就看不出條的方向代表什麼 */}
                                        <ReferenceLine x={0} stroke={CHART_REFERENCE} />
                                        <Bar dataKey="yoy" name="YoY" maxBarSize={18}
                                             isAnimationActive={!reduceMotion}>
                                            <LabelList dataKey="yoy" content={GrowthLabel} />
                                            {chart.growth.map((g) => (
                                                <Cell key={g.name}
                                                      fill={g.yoy >= 0 ? 'var(--accent-success)'
                                                                       : 'var(--accent-danger)'} />
                                            ))}
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        ) : (
                            <div className="h-[200px] flex items-center justify-center text-center
                                            text-[12px] text-[var(--text-meta)] px-6">
                                這個期間長度往回找不到差 300 到 430 天的一期，算不出 YoY。
                                外國發行人常只申報全年，改選「全年」通常就有。
                            </div>
                        )}
                    </ChartCard>

                    <ChartCard icon={<Activity size={14} aria-hidden="true" />} title="部門利潤率走勢"
                               note={chart.hasMargin
                                   ? '利潤 ÷ 該部門營收；各家的利潤口徑不同，跨公司別直接比'
                                   : '這個拆法只揭露營收'}>
                        {chart.hasMargin ? (
                            <div className="h-[260px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <LineChart data={chart.marginRows}
                                               margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                                        <CartesianGrid stroke={CHART_GRID} vertical={false} />
                                        <XAxis dataKey="label" tick={axisTicks} stroke={CHART_AXIS}
                                               minTickGap={16} tickLine={false} />
                                        <YAxis tick={axisTicks} stroke={CHART_AXIS} width={48}
                                               tickLine={false} axisLine={false}
                                               tickFormatter={(v) => `${+v.toFixed(1)}%`} />
                                        <Tooltip content={<ChartTooltip unit="pct" />} />
                                        <Legend wrapperStyle={{ fontSize: 11.5, paddingTop: 6 }}
                                                iconType="plainline" iconSize={12}
                                                itemSorter={LEGEND_KEEP_ORDER} />
                                        <ReferenceLine y={0} stroke={CHART_REFERENCE} />
                                        {chart.keys.filter((k) => k !== OTHER_KEY).map((k) => (
                                            <Line key={k} dataKey={k} name={k} type="monotone"
                                                  stroke={chart.colorOf[k]} strokeWidth={2}
                                                  dot={false} activeDot={{ r: 3.5 }} connectNulls
                                                  isAnimationActive={!reduceMotion} />
                                        ))}
                                    </LineChart>
                                </ResponsiveContainer>
                            </div>
                        ) : (
                            <div className="h-[260px] flex items-center justify-center text-center
                                            text-[12px] text-[var(--text-meta)] px-6">
                                部門利潤是公司自行選擇揭露的衡量指標，這個拆法沒揭露。
                                上面若有其他維度，換一個試試。
                            </div>
                        )}
                    </ChartCard>
                </div>
            )}

            {/* ── 第三層：完整明細 ───────────────────────────────────────────── */}
            {table && (
                <>
                    <div className="flex flex-wrap items-center gap-3 mb-2">
                        <h2 className="text-[13.5px] font-semibold text-[var(--text-primary)]">完整明細</h2>
                        {(metric === 'profit' || metric === 'margin') && table.measures.length > 0 && (
                            <span className="text-[11.5px] text-[var(--text-meta)]">
                                利潤口徑：{table.measures.join('／')}
                            </span>
                        )}
                        <div className="ml-auto flex items-center gap-2">
                            <span className="text-[11.5px] text-[var(--text-meta)]">顯示</span>
                            <Toggle value={metric} onChange={setMetric} options={TABLE_METRICS} />
                        </div>
                    </div>
                    {/* 部門多、期間多的公司（DHI 有 13 個部門）表格會很長，
                        限制高度並釘住標題列與部門欄，捲到哪都知道自己在看什麼 */}
                    <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)]
                                    overflow-auto max-h-[70vh]">
                        <table className="data-table w-full min-w-[720px] text-[12.5px] border-collapse">
                            <caption className="sr-only">
                                {ticker} 各部門的{TABLE_METRICS.find((m) => m.key === metric)?.label}，依期間排列
                            </caption>
                            <thead>
                                <tr className="text-[var(--text-meta)] text-left">
                                    <th scope="col" className="py-2 px-3 font-medium sticky-col
                                                                min-w-[176px]">部門</th>
                                    {table.periods.map((p) => (
                                        <th key={p.period_end} scope="col"
                                            title={`${p.period_end}${p.fiscal_label ? `　會計季 ${p.fiscal_label}` : ''}`}
                                            className="num py-2 px-3 font-medium whitespace-nowrap text-right">
                                            {p.label || p.period_end}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {table.names.flatMap((name) => {
                                    const isTotal = table.totalRows.includes(name);
                                    return [
                                        <tr key={name}
                                            className={`border-t border-[var(--glass-border)] align-top ${
                                                isTotal ? 'bg-[var(--bg-tertiary)]' : ''}`}>
                                            <th scope="row" className={`py-2 px-3 text-left font-normal sticky-col
                                                    min-w-[176px] max-w-[240px] ${
                                                    isTotal ? 'text-[var(--text-meta)]' : ''}`}>
                                                {/* 點部門名稱展開該部門的完整科目：成本、研發、
                                                    資本支出、折舊…DB 都有，不用只看營收與利潤 */}
                                                <button
                                                    onClick={() => setOpenSeg(openSeg === name ? null : name)}
                                                    aria-expanded={openSeg === name}
                                                    className="ui-focus cursor-pointer inline-flex items-center gap-2 text-left
                                                               hover:text-[var(--accent-cyan-text)] transition-colors duration-200">
                                                    {!isTotal && <SegmentDot color={table.colorOf[name]} />}
                                                    <span className={isTotal ? '' : 'text-[var(--text-primary)]'}>
                                                        {name}
                                                    </span>
                                                    <span className="text-[10px] text-[var(--text-meta)]">
                                                        {openSeg === name ? '▾' : '▸'}
                                                    </span>
                                                </button>
                                            </th>
                                            {table.periods.map((p, i) => {
                                                const s = table.byKey[`${p.period_end}|${name}`];
                                                if (!s) {
                                                    return <td key={p.period_end}
                                                               className="py-2 px-3 text-right text-[var(--text-meta)]">—</td>;
                                                }
                                                if (metric === 'revenue') {
                                                    return (
                                                        <td key={p.period_end}
                                                            className="num py-2 px-3 text-right whitespace-nowrap
                                                                       text-[var(--text-primary)] font-medium">
                                                            {money(s.revenue)}
                                                        </td>
                                                    );
                                                }
                                                if (metric === 'profit') {
                                                    return (
                                                        <td key={p.period_end}
                                                            className="num py-2 px-3 text-right whitespace-nowrap
                                                                       text-[var(--text-primary)] font-medium">
                                                            {/* 口徑一致時只在表格上方標一次；同一張表混了好幾種
                                                                （毛利與營業利益並列）才逐格標，否則整欄都是重複的字 */}
                                                            {table.measures.length > 1 && s.profit != null && (
                                                                <span className="font-normal text-[11px] text-[var(--text-meta)] mr-1">
                                                                    {s.profit_measure}
                                                                </span>
                                                            )}
                                                            {s.profit != null ? money(s.profit) : '—'}
                                                        </td>
                                                    );
                                                }
                                                if (metric === 'margin') {
                                                    return (
                                                        <td key={p.period_end}
                                                            className="num py-2 px-3 text-right whitespace-nowrap text-[var(--text-primary)]">
                                                            {s.margin_pct != null ? `${s.margin_pct}%` : '—'}
                                                        </td>
                                                    );
                                                }
                                                if (metric === 'share') {
                                                    const base = table.real.reduce(
                                                        (a, n) => a + (table.byKey[`${p.period_end}|${n}`]?.revenue || 0), 0);
                                                    const v = base && s.revenue != null ? s.revenue / base * 100 : null;
                                                    return (
                                                        <td key={p.period_end}
                                                            className="num py-2 px-3 text-right whitespace-nowrap text-[var(--text-primary)]">
                                                            {v != null ? `${v.toFixed(1)}%` : '—'}
                                                        </td>
                                                    );
                                                }
                                                // YoY：每一欄都跟自己的去年同期比，不是跟隔壁那欄比
                                                const pIdx = priorYearIndex(table.periods, i);
                                                const q = pIdx >= 0
                                                    ? table.byKey[`${table.periods[pIdx].period_end}|${name}`] : null;
                                                const yoy = s.revenue != null && q?.revenue
                                                    ? (s.revenue - q.revenue) / Math.abs(q.revenue) * 100 : null;
                                                return (
                                                    <td key={p.period_end}
                                                        className="num py-2 px-3 text-right whitespace-nowrap"
                                                        style={{ color: toneOf(yoy) }}>
                                                        {signed(yoy)}
                                                    </td>
                                                );
                                            })}
                                        </tr>,
                                        openSeg === name && (
                                            <tr key={`${name}-detail`} className="border-t border-[var(--glass-border)]">
                                                <td colSpan={table.periods.length + 1} className="p-0">
                                                    <div className="px-3 py-2 bg-[var(--bg-tertiary)]">
                                                        <SegmentDetail name={name} periods={table.periods}
                                                                       byKey={table.byKey} />
                                                    </div>
                                                </td>
                                            </tr>
                                        ),
                                    ];
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {table?.recon?.reconciles === true && (
                <div className="mt-3 text-[12px] text-[var(--text-meta)]">
                    ✓ 部門加總 {money(table.recon.sum_of_segments)} 與合併數相符
                </div>
            )}

            {axis && !axis.has_profit && (
                <div className="text-[12px] text-[var(--text-meta)] mt-3">
                    這個拆法只揭露營收。部門利潤是公司自行選擇揭露的衡量指標，
                    單一部門公司通常不會分開揭露。
                </div>
            )}
            </div>
        </div>
    );
}
