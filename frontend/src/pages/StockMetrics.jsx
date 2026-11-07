import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useRoute, useSearch, Link, Redirect } from 'wouter';
import axios from '../lib/apiClient';
import {
    ComposedChart, Bar, Line, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
    CartesianGrid, Cell, ReferenceLine,
} from 'recharts';
import {
    ChevronDown, BarChart3, Layers, CalendarDays, FileQuestion,
    LineChart as LineIcon, AreaChart as AreaIcon, Rows3, Layers2,
} from 'lucide-react';
import StockPageHeader from '../components/StockPageHeader';
import SecSegmentRevenue from '../components/SecSegmentRevenue';
import {
    CHART_GRID, CHART_AXIS, CHART_REFERENCE, chartTooltipStyle, chartTooltipLabelStyle,
    SEGMENT_SERIES, SEGMENT_OTHER,
} from '../components/chartTheme';

/* ─────────────────────────────────────────────────────────────────────────────
   單位判斷
   來源 CSV 沒有單位欄，只能從名字猜。全庫 234 檔實測過的規則順序：
   成長率 → 利潤率 → 分類本身就是錢（Revenue / Expense…，CMG 的「Occupancy」
   是租金不是入住率）→ 名字裡的百分比字 → 比率 → 「per / price」是錢 →
   數量字（deliveries / users / metric tons…）→ 錢字 → 分類裡的數量字 → 預設錢。
   百分比的值已經是 82.7 這種數字，不是 0.827。
───────────────────────────────────────────────────────────────────────────── */
// 「rate」只認結尾那個字：NEE 的「Rate Regulated Transmission」是投資金額不是比率
const RE_PCT_CAT = /margin|\brate$|percentage|penetration|occupancy|retention|adoption|utilization|\bmix\b/i;
const RE_MONEY_CAT = /revenue|expense|profit|income|earnings|ebit|cost|backlog|sales|billings|spend|volume/i;
const RE_PCT = /margin|\brate$|occupancy|load factor|percent|penetration|retention|\bmix\b|share of|adoption|%/i;
// 「Run Rate」（ARR 的講法）是金額，不能被上面的 rate 收走
const RE_RUN_RATE = /run rate/i;
const RE_RATIO = /book to bill|\bratio\b|multiple|turnover/i;
// 「Average Daily Rate」「RevPAR」是每晚房價不是比率，要先被認成錢再輪到 RE_PCT
const RE_PER = /\bper\b|price|arpu|arppu|arpa|daily rate|revpar/i;
const RE_COUNT = /deliver|customer|user|subscriber|member|store|\bunits?\b|vehicle|employee|headcount|\bdau\b|\bmau\b|shipment|passenger|location|restaurant|order|transaction|listing|night|ride|trip|\bhours?\b|device|iphone|ipad|\bmacs?\b|system|license|cards?-in-force|\bcards\b|metric ton|mboed|megawatt|production|throughput|miles|impression|install|download|account|seat|logged|active|count|paid/i;
const RE_MONEY = /revenue|billing|obligation|backlog|\barr\b|recurring|contract value|spend|loan|deposit|merchandise|payment|network volume|booking value|transaction value|trading volume|cost|income|profit|expense|sales|earnings|ebit|assets|\bvar\b|worth|value/i;

function metricKind(name, catName) {
    if (/growth/i.test(name)) return 'growth';
    if (/margin/i.test(name)) return 'pct';
    // 分類先講話：「Gross Margin by Type」底下的列叫 Products / Services，
    // 光看列名看不出是百分比。百分比分類要排在金額分類前面。
    if (RE_PCT_CAT.test(catName)) return 'pct';
    if (RE_MONEY_CAT.test(catName)) return 'money';
    if (RE_PER.test(name) || RE_RUN_RATE.test(name)) return 'money';
    if (RE_PCT.test(name)) return 'pct';
    if (RE_RATIO.test(name)) return 'ratio';
    if (RE_COUNT.test(name)) return 'count';
    if (RE_MONEY.test(name)) return 'money';
    if (RE_COUNT.test(catName)) return 'count';
    return 'money';
}

const KIND_LABEL = { money: 'USD', count: '數量', pct: '%', growth: 'YoY %', ratio: '倍數' };

const CHART_TYPES = [
    { k: 'bar', label: '長條', icon: BarChart3, hint: '看每期的量體大小' },
    { k: 'line', label: '折線', icon: LineIcon, hint: '看走勢與轉折' },
    { k: 'area', label: '面積', icon: AreaIcon, hint: '看累積規模' },
];

const SERIES_MODES = [
    { k: 'single', label: '單一', icon: Rows3, hint: '只畫選到的那一項' },
    { k: 'category', label: '整組', icon: Layers2, hint: '把整個分類的項目畫在同一張圖' },
];

/* 預設圖型跟著指標性質走：金額和數量比大小用長條，
   比率和百分比在乎的是走勢不是量體，用折線比較不會誤導。 */
const DEFAULT_TYPE = { money: 'bar', count: 'bar', growth: 'bar', pct: 'line', ratio: 'line' };

// 一張圖最多幾條序列：顏色只有六個且經過色盲檢查，不夠就併成「其他」
const MAX_SERIES = 6;

/* 哪些分類的項目可以堆疊。
   只有「把一個整體拆開」的分類才行——名字帶 by / Breakdown 的那 155 個
   （分部門營收、地區別營收、費用拆解）。營運指標那 103 個不行：Apple 的
   Global Active iPhones 是 Global Active Devices 的子集，疊起來會變成
   3.76B 台裝置，是假的。
   另外含「Total」列的分類也不能疊，那一列已經是總和，疊下去重複計算。 */
const RE_DECOMPOSITION = /\bby\b|breakdown/i;
const RE_TOTAL_ROW = /^total\b|\btotal$/i;

function isStackable(cat) {
    if (!RE_DECOMPOSITION.test(cat.name)) return false;
    if (cat.rows.some((r) => RE_TOTAL_ROW.test(r.name.trim()))) return false;
    return cat.uniformKind === 'money' || cat.uniformKind === 'count';
}

/* Y 軸刻度自己算。交給 recharts 有兩種壞法：預設會湊到下一個大整數
   （$455B 的資料配 $600B 的軸，上面四分之一是空的），而用函式硬壓上界
   又會生出 $474B 這種讀不動的刻度。這裡挑 1/2/2.5/5 系列的間距，
   往上湊到剛好蓋住資料的整數，兩邊都避開。全正值時才用。 */
function niceStep(raw) {
    const mag = 10 ** Math.floor(Math.log10(raw));
    const norm = raw / mag;
    return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
}

function buildTicks(lo, hi, step) {
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
    return { domain: [lo, hi], ticks };
}

/* 長條圖一定從零起算：柱子的長度就是數值，截掉底部等於把差距放大成謊話。 */
function niceScale(max, target = 5) {
    if (!(max > 0) || !Number.isFinite(max)) return null;
    const step = niceStep(max / target);
    return buildTicks(0, Math.ceil(max / step) * step, step);
}

/* 折線和未堆疊的面積不綁零軸：線的位置才是資訊，長度不是。
   Services 從 93B 走到 117B，硬拉到 0 會變成一條看不出轉折的斜線。 */
function niceRange(min, max, target = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return null;
    const step = niceStep((max - min) / target);
    return buildTicks(Math.floor(min / step) * step, Math.ceil(max / step) * step, step);
}

function compact(abs, decimals = 2) {
    if (abs >= 1e12) return `${(abs / 1e12).toFixed(decimals)}T`;
    if (abs >= 1e9) return `${(abs / 1e9).toFixed(decimals)}B`;
    if (abs >= 1e6) return `${(abs / 1e6).toFixed(decimals >= 2 ? 1 : 0)}M`;
    if (abs >= 1e3) return `${(abs / 1e3).toFixed(decimals >= 2 ? 1 : 0)}K`;
    return Number.isInteger(abs) ? String(abs) : abs.toFixed(2);
}

/* 軸刻度的小數位要看「同一個 T/B/M 級距裡的位數」，不能固定 0：
   1.8B 和 2.2B 都 toFixed(0) 會變成兩個一樣的「2B」刻度。 */
function axisDecimals(abs) {
    const scale = abs >= 1e12 ? 1e12 : abs >= 1e9 ? 1e9 : abs >= 1e6 ? 1e6 : abs >= 1e3 ? 1e3 : 1;
    return abs / scale < 10 ? 1 : 0;
}

function formatValue(val, kind) {
    if (val == null || Number.isNaN(val)) return '—';
    if (kind === 'growth' || kind === 'pct') return `${val.toFixed(1)}%`;
    if (kind === 'ratio') return `${val.toFixed(2)}x`;
    const sign = val < 0 ? '-' : '';
    const body = compact(Math.abs(val));
    return kind === 'money' ? `${sign}$${body}` : `${sign}${body}`;
}

/** 變動量：百分比類用「百分點」，其他用「%」 */
function formatDelta(cur, prev, kind) {
    if (cur == null || prev == null) return null;
    if (kind === 'pct' || kind === 'growth') {
        const pp = cur - prev;
        return { value: pp, text: `${pp >= 0 ? '+' : ''}${pp.toFixed(1)} pp` };
    }
    if (prev === 0) return null;
    const pct = ((cur - prev) / Math.abs(prev)) * 100;
    return { value: pct, text: `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%` };
}

function formatSigned(val) {
    if (val == null) return '—';
    return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
}

/* ── 期間 ───────────────────────────────────────────────────────────────── */
// 分類的期間軸是「Jan 1, 2024」這種就是年度；其他是季度（會計季末日）
const isAnnualAxis = (periods) => periods.length > 0 && periods.every((p) => /^Jan 1,/.test(p));

function periodLabel(p, annual) {
    const parts = p.split(' ');
    if (parts.length < 3) return p;
    return annual ? parts[2] : `${parts[0]} '${parts[2].slice(-2)}`;
}

/* ── 把「X」和「X Growth」配成一列 ──────────────────────────────────────── */
function pairGrowth(metrics) {
    const byName = new Map(metrics.map((m) => [m.name, m]));
    const rows = [];
    for (const m of metrics) {
        const isGrowth = / growth$/i.test(m.name);
        if (isGrowth && byName.has(m.name.replace(/ growth$/i, ''))) continue; // 併進母指標
        const growth = isGrowth ? null : byName.get(`${m.name} Growth`) ?? null;
        rows.push({ ...m, growth });
    }
    return rows;
}

/** 摘要：最新值、較上期、較去年同期、區間高低 */
function seriesStats(row, periods, annual, kind) {
    const vals = row.values;
    const i = vals.findIndex((v) => v != null);
    if (i < 0) return null;
    const latest = vals[i];
    const prev = vals[i + 1] ?? null;
    const nonNull = vals.filter((v) => v != null);
    const officialYoY = row.growth?.values?.[i] ?? null;

    // 第三格。季度軸拿去年同期（往回四季）；年度軸的「去年同期」就是上一期，
    // 跟第二格會是同一個數字，所以改放整段區間的變動。
    let third;
    if (annual) {
        let oldestIdx = -1;
        for (let k = vals.length - 1; k > i; k -= 1) if (vals[k] != null) { oldestIdx = k; break; }
        third = oldestIdx > 0
            ? { label: `自 ${periodLabel(periods[oldestIdx], annual)}`, delta: formatDelta(latest, vals[oldestIdx], kind) }
            : { label: '自最早期', delta: null };
    } else {
        const yearAgo = vals[i + 4] ?? null;
        third = {
            label: `較去年同期${yearAgo != null ? ` · ${periodLabel(periods[i + 4], annual)}` : ''}`,
            delta: officialYoY != null
                ? { value: officialYoY, text: formatSigned(officialYoY) }
                : formatDelta(latest, yearAgo, kind),
        };
    }

    return {
        latest,
        latestPeriod: periodLabel(periods[i], annual),
        prevDelta: formatDelta(latest, prev, kind),
        prevPeriod: prev != null ? periodLabel(periods[i + 1], annual) : null,
        third,
        hi: Math.max(...nonNull),
        lo: Math.min(...nonNull),
        n: nonNull.length,
    };
}

/* ── 列內迷你趨勢線（純 SVG，不用 recharts） ────────────────────────────── */
function Sparkline({ values, width = 64, height = 18 }) {
    const pts = values.filter((v) => v != null);
    if (pts.length < 2) return <span style={{ width, height }} className="inline-block shrink-0" />;
    const series = [...values].reverse().filter((v) => v != null); // 舊 → 新
    const hi = Math.max(...series);
    const lo = Math.min(...series);
    const span = hi - lo || 1;
    const step = width / (series.length - 1);
    const d = series
        .map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`)
        .join(' ');
    const last = series[series.length - 1];
    const lastY = height - 2 - ((last - lo) / span) * (height - 4);
    const up = last >= series[0];
    return (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true"
             className={`mb-spark ${up ? 'is-up' : 'is-down'}`}>
            <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={width} cy={lastY} r="2" fill="currentColor" />
        </svg>
    );
}

function DeltaText({ delta }) {
    if (!delta) return <span className="text-[var(--text-muted)]">—</span>;
    const cls = delta.value > 0 ? 'text-[var(--accent-success-text)]'
        : delta.value < 0 ? 'text-[var(--accent-danger-text)]' : 'text-[var(--text-secondary)]';
    return <span className={cls}>{delta.text}</span>;
}

function Skeleton({ ticker }) {
    return (
        <div className="container py-10 space-y-6" aria-busy="true" aria-label="載入中">
            <StockPageHeader ticker={ticker} />
            <div className="h-[360px] rounded-2xl bg-[var(--bg-tertiary)] animate-pulse opacity-70" />
            {[0, 1, 2].map((i) => (
                <div key={i} className="h-[220px] rounded-2xl bg-[var(--bg-tertiary)] animate-pulse opacity-60" />
            ))}
        </div>
    );
}

/* ═════════════════════════════════════════════════════════════════════════ */
export default function StockMetrics() {
    const [, params] = useRoute('/stock/:ticker/metrics');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [openCats, setOpenCats] = useState({});
    const [selected, setSelected] = useState(null);     // { cat, metric }
    const [chartMode, setChartMode] = useState('value'); // 'value' | 'growth'
    const [chartType, setChartType] = useState(null);    // null = 跟著指標性質自動選
    const [seriesMode, setSeriesMode] = useState('single'); // 'single' | 'category'
    const [visiblePeriods, setVisiblePeriods] = useState(8);
    const chartRef = useRef(null);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        setError(null);
        axios.get(`/api/stocks/${ticker}/metrics-breakdown`)
            .then((res) => {
                setData(res.data);
                const open = {};
                res.data.categories.forEach((c) => { open[c.name] = true; });
                setOpenCats(open);
                const firstCat = res.data.categories[0];
                const first = firstCat ? pairGrowth(firstCat.metrics)[0] : null;
                setSelected(first ? { cat: firstCat.name, metric: first.name } : null);
                setChartMode('value');
                setChartType(null);
                setSeriesMode('single');
            })
            .catch((err) => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load metrics data');
            })
            .finally(() => setLoading(false));
    }, [ticker]);

    // 每個分類自己的期間軸（後端已把整欄空白的期間拿掉）＋配對後的列
    const cats = useMemo(() => {
        if (!data) return [];
        return data.categories.map((c, idx) => {
            const periods = c.periods || data.periods;
            const annual = isAnnualAxis(periods);
            const rows = pairGrowth(c.metrics).map((r) => ({ ...r, kind: metricKind(r.name, c.name) }));
            return {
                ...c,
                id: `mb-cat-${idx}`,
                periods,
                annual,
                rows,
                // 整個分類同一種單位（分部門營收都是 USD）就只在標題標一次；
                // 混單位的分類（營運指標裡數量和金額混著）才逐列標
                uniformKind: rows.every((r) => r.kind === rows[0]?.kind) ? rows[0]?.kind ?? null : null,
            };
        });
    }, [data]);

    const maxPeriods = useMemo(() => cats.reduce((m, c) => Math.max(m, c.periods.length), 0), [cats]);
    const periodOptions = useMemo(() => {
        const opts = [4, 8, 12, 20].filter((n) => n < maxPeriods);
        return [...opts, maxPeriods];
    }, [maxPeriods]);

    const current = useMemo(() => {
        if (!selected) return null;
        const cat = cats.find((c) => c.name === selected.cat);
        const row = cat?.rows.find((r) => r.name === selected.metric);
        if (!cat || !row) return null;
        return { cat, row, stats: seriesStats(row, cat.periods, cat.annual, row.kind) };
    }, [cats, selected]);

    const canShowGrowth = !!current?.row.growth;
    const effectiveMode = canShowGrowth && chartMode === 'growth' ? 'growth' : 'value';

    // 整個分類疊在一起只有在單位一致時才有意義：USD 和 % 疊起來是胡說
    const canShowCategory = !!current && current.cat.rows.length > 1 && !!current.cat.uniformKind;
    const effectiveSeries = canShowCategory && seriesMode === 'category' ? 'category' : 'single';

    const chartKind = effectiveMode === 'growth' ? 'growth' : current?.row.kind;
    const effectiveType = chartType ?? DEFAULT_TYPE[chartKind] ?? 'bar';

    // 分類模式下要畫哪幾條：依最新值取前六名，剩下的加總成「其他」。
    // 只有可以相加的東西（金額、數量）才併；百分比相加沒有意義，多的就不畫。
    const series = useMemo(() => {
        if (!current) return [];
        const { cat, row } = current;
        const pick = (r) => ({
            name: r.name,
            values: effectiveMode === 'growth' ? (r.growth?.values ?? []) : r.values,
        });
        if (effectiveSeries === 'single') return [{ ...pick(row), color: null }];

        const usable = cat.rows.filter((r) => effectiveMode !== 'growth' || r.growth);
        const rank = [...usable].sort((a, b) => {
            const av = a.values.find((v) => v != null) ?? -Infinity;
            const bv = b.values.find((v) => v != null) ?? -Infinity;
            return bv - av;
        });
        const head = rank.slice(0, MAX_SERIES).map((r, i) => ({ ...pick(r), color: SEGMENT_SERIES[i] }));
        const tail = rank.slice(MAX_SERIES);
        if (tail.length && isStackable(cat) && effectiveMode !== 'growth') {
            const values = cat.periods.map((_, i) => {
                const nums = tail.map((r) => r.values[i]).filter((v) => v != null);
                return nums.length ? nums.reduce((a, b) => a + b, 0) : null;
            });
            head.push({ name: `其他 ${tail.length} 項`, values, color: SEGMENT_OTHER });
        }
        return head;
    }, [current, effectiveSeries, effectiveMode]);

    // recharts 吃的是每期一列、每條序列一個欄位
    const chartData = useMemo(() => {
        if (!current || !series.length) return [];
        const { cat } = current;
        return cat.periods
            .map((p, i) => {
                const row = { period: periodLabel(p, cat.annual) };
                series.forEach((sr) => { row[sr.name] = sr.values[i] ?? null; });
                return row;
            })
            .filter((d) => series.some((sr) => d[sr.name] != null))
            .slice(0, 20)
            .reverse();
    }, [current, series]);

    // 有負值就不能堆疊：堆出來的高度會是相消後的數字，看起來像資料錯了
    const hasNegative = chartData.some((d) => series.some((sr) => (d[sr.name] ?? 0) < 0));
    const stacked = effectiveSeries === 'category' && !hasNegative
        && (effectiveType === 'bar' || effectiveType === 'area')
        && effectiveMode !== 'growth'
        && !!current && isStackable(current.cat);
    // 小數位由整段最大值決定一次，全軸一致；逐刻度算會變成 $7.5B / $23B 混排
    const peak = useMemo(() => chartData.reduce((m, d) => {
        const rowPeak = stacked
            ? Math.abs(series.reduce((sum, sr) => sum + (d[sr.name] ?? 0), 0))
            : series.reduce((mm, sr) => Math.max(mm, Math.abs(d[sr.name] ?? 0)), 0);
        return Math.max(m, rowPeak);
    }, 0), [chartData, series, stacked]);

    // 柱狀與堆疊面積用零基線；折線與重疊面積讓刻度貼著資料
    const zeroBased = effectiveType === 'bar' || stacked;
    const extent = useMemo(() => {
        let lo = Infinity; let hi = -Infinity;
        chartData.forEach((d) => {
            const vals = stacked
                ? [series.reduce((sum, sr) => sum + (d[sr.name] ?? 0), 0)]
                : series.map((sr) => d[sr.name]).filter((v) => v != null);
            vals.forEach((v) => { lo = Math.min(lo, v); hi = Math.max(hi, v); });
        });
        return Number.isFinite(lo) ? { lo, hi } : null;
    }, [chartData, series, stacked]);

    const scale = !extent ? null
        : zeroBased
            ? (hasNegative ? niceRange(Math.min(0, extent.lo), extent.hi) : niceScale(peak))
            : niceRange(extent.lo, extent.hi);

    const axisFmt = useMemo(() => {
        const dp = axisDecimals(peak);
        return (v) => {
            if (chartKind === 'growth' || chartKind === 'pct') return `${v}%`;
            if (chartKind === 'ratio') return `${v.toFixed(1)}x`;
            const abs = Math.abs(v);
            return `${v < 0 ? '-' : ''}${chartKind === 'money' ? '$' : ''}${compact(abs, dp)}`;
        };
    }, [peak, chartKind]);

    // 窄畫面上圖表在表格上方、離很遠：點了列卻看不到圖有變，等於沒反應。
    // xl 以上圖是黏住的，本來就在視野裡，不要動使用者的捲動位置。
    /* 面板頂端的大數字。單一指標就是它自己；分類模式給合計，
       但只有可相加的分類才給——子集型的營運指標加起來是假的，那時不顯示。 */
    const readout = useMemo(() => {
        if (!current) return null;
        if (effectiveSeries === 'single') {
            const st = current.stats;
            if (!st) return null;
            return {
                value: formatValue(effectiveMode === 'growth' ? (current.row.growth?.values?.find((v) => v != null) ?? null) : st.latest, chartKind),
                period: st.latestPeriod,
                yoy: st.third.delta,
                prev: st.prevDelta,
            };
        }
        if (!stacked || !chartData.length) return null;
        const at = (idx) => {
            const d = chartData[chartData.length - 1 - idx];
            if (!d) return null;
            const nums = series.map((sr) => d[sr.name]).filter((v) => v != null);
            return nums.length ? nums.reduce((a, b) => a + b, 0) : null;
        };
        const latest = at(0);
        if (latest == null) return null;
        return {
            value: formatValue(latest, chartKind),
            period: `${chartData[chartData.length - 1].period} 合計`,
            yoy: formatDelta(latest, at(current.cat.annual ? 1 : 4), chartKind),
            prev: formatDelta(latest, at(1), chartKind),
        };
    }, [current, effectiveSeries, effectiveMode, chartKind, stacked, chartData, series]);

    const selectRow = useCallback((catName, rowName) => {
        setSelected({ cat: catName, metric: rowName });
        if (typeof window !== 'undefined' && window.innerWidth < 1280 && chartRef.current) {
            const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            chartRef.current.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
        }
    }, []);

    const toggleCat = (name) => setOpenCats((prev) => ({ ...prev, [name]: !prev[name] }));
    const jumpTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // 三大表已拆到 /financials；舊的 /metrics?tab=income 書籤與外部連結直接轉過去
    const search = useSearch();
    const legacyTab = new URLSearchParams(search || '').get('tab');
    if (['income', 'balance', 'cashflow', 'metrics'].includes(legacyTab)) {
        return <Redirect to={`/stock/${ticker}/financials?tab=${legacyTab}`} replace />;
    }

    if (loading) return <Skeleton ticker={ticker} />;

    if (error) {
        // 爬來的 metrics CSV 不是每檔都有。以前這裡整頁 bail，現在至少把
        // SEC 推算的基本面顯示出來——那份資料不依賴 CSV。
        // 連 SEC 那幾塊也沒東西時（它們自己 return null），整頁只剩後端那句
        // 英文錯誤訊息孤零零躺著，所以補一張講清楚狀況、給得出去路的空狀態卡。
        return (
            <div className="container py-10 space-y-6 animate-fade-in">
                <StockPageHeader ticker={ticker} />
                <SecSegmentRevenue ticker={ticker} />
                <section className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-6 max-w-2xl">
                    <h2 className="text-[15px] font-bold inline-flex items-center gap-2 mb-2">
                        <FileQuestion size={16} aria-hidden="true" /> {ticker} 沒有營運分項資料
                    </h2>
                    <p className="text-[13.5px] text-[var(--text-body)] leading-relaxed">
                        分部門營收、地區別營收這類拆解來自 stockanalysis.com 的 metrics 頁，
                        不是每檔股票都有那一頁——估值、比率、預估、統計仍然正常。
                    </p>
                    <p className="text-[11.5px] text-[var(--text-meta)] mt-2 break-words">後端回覆：{error}</p>
                    <div className="flex flex-wrap gap-2 mt-4">
                        {[
                            { href: `/stock/${ticker}/ratios`, label: '財務比率' },
                            { href: `/stock/${ticker}/statistics`, label: '統計數據' },
                            { href: `/stock/${ticker}/forecast`, label: '分析師預估' },
                        ].map((l) => (
                            <Link key={l.href} href={l.href}
                                  className="ui-focus tap cursor-pointer px-3 py-2 rounded-full text-[12.5px] font-medium border border-[var(--glass-border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--glass-border-hover)] transition-colors">
                                {l.label}
                            </Link>
                        ))}
                    </div>
                </section>
            </div>
        );
    }

    const totalRows = cats.reduce((n, c) => n + c.rows.length, 0);

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            <StockPageHeader
                ticker={ticker}
                meta={
                    <>
                        {cats.length} 個分類 · {totalRows} 項指標 · 最多 {maxPeriods} 期
                        <span className="mx-1.5 opacity-50">|</span>
                        資料日 {data.date} · 來源 stockanalysis.com
                    </>
                }
                actions={
                    <div className="flex items-center gap-2" role="group" aria-label="顯示期數">
                        <CalendarDays size={14} className="text-[var(--text-muted)]" aria-hidden="true" />
                        {periodOptions.map((n) => {
                            const isAll = n === maxPeriods;
                            const on = visiblePeriods === n || (visiblePeriods > maxPeriods && isAll);
                            return (
                                <button
                                    key={n}
                                    onClick={() => setVisiblePeriods(n)}
                                    aria-pressed={on}
                                    className={`ui-focus tap cursor-pointer px-3 py-1.5 rounded-full text-[12.5px] font-medium border transition-colors duration-200 ${
                                        on
                                            ? 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                            : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)] hover:border-[var(--glass-border-hover)]'
                                    }`}
                                >
                                    {isAll ? `全部 ${n} 期` : `${n} 期`}
                                </button>
                            );
                        })}
                    </div>
                }
            />

            {/* 分部門用 SEC 而不是下面那份爬蟲 CSV：兩邊都有的 22 檔裡，
                CSV 一律只有 8 期、SEC 中位數 37 期。三大表與 Non-GAAP 在 /financials */}
            <SecSegmentRevenue ticker={ticker} />

            {/* 分項拆解區塊標題 + 跳轉 */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-2">
                <h2 className="text-[15px] font-bold inline-flex items-center gap-2">
                    <Layers size={16} aria-hidden="true" /> 營運分項拆解
                </h2>
                <span className="text-[11.5px] text-[var(--text-meta)]">點任一列在圖上看趨勢；「X Growth」已併入母指標，顯示為 YoY</span>
                <nav className="w-full sm:w-auto sm:ml-auto flex gap-1.5 overflow-x-auto styled-scrollbar" aria-label="跳到分類">
                    {cats.map((c) => (
                        <button key={c.id} onClick={() => jumpTo(c.id)}
                                className="ui-focus cursor-pointer shrink-0 px-2.5 py-1 rounded-md text-[12px] text-[var(--text-secondary)] bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors">
                            {c.name.replace(/^.+? (?=Operating Metrics)/, '')}
                        </button>
                    ))}
                </nav>
            </div>

            <div className="xl:grid xl:grid-cols-[minmax(0,1fr)_380px] xl:gap-6 xl:items-start space-y-6 xl:space-y-0">
                {/* ── 圖表 + 摘要（xl 以上黏在右側，表格捲動時圖不跑掉） ── */}
                <aside ref={chartRef} className="mb-panel xl:sticky xl:top-24 xl:order-2" aria-live="polite">
                    {current ? (
                        <>
                            <p className="mb-eyebrow">{current.cat.name}</p>
                            <h3 className="mb-title">
                                {effectiveSeries === 'category' ? `全部 ${current.cat.rows.length} 項` : current.row.name}
                            </h3>

                            {/* 讀數：一個大數字帶兩個變動，取代原本四格方塊。
                                分類模式下大數字是合計，但只有能相加的分類才給——
                                不能相加的（子集型營運指標）加起來是假的，那時只列各項。 */}
                            {readout && (
                                <div className="mb-readout">
                                    <div className="mb-readout-main">
                                        <span className="mb-readout-value num">{readout.value}</span>
                                        <span className="mb-readout-period">{readout.period}</span>
                                    </div>
                                    <dl className="mb-readout-deltas">
                                        <div>
                                            <dt>{current.cat.annual ? '較前期' : 'YoY'}</dt>
                                            <dd><DeltaText delta={readout.yoy} /></dd>
                                        </div>
                                        <div>
                                            <dt>{current.cat.annual ? '自最早' : 'QoQ'}</dt>
                                            <dd><DeltaText delta={readout.prev} /></dd>
                                        </div>
                                    </dl>
                                </div>
                            )}

                            {effectiveSeries === 'category' && (
                                <ul className="mb-series">
                                    {series.map((sr) => {
                                        const latest = sr.values.find((v) => v != null) ?? null;
                                        return (
                                            <li key={sr.name}>
                                                <span className="mb-series-bar" style={{ background: sr.color }} aria-hidden="true" />
                                                <span className="mb-series-name" title={sr.name}>{sr.name}</span>
                                                <span className="mb-series-val num">{formatValue(latest, chartKind)}</span>
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}

                            <div className="mb-plot">
                                <ResponsiveContainer width="100%" height="100%">
                                    {/* key 帶上模式：從「單一」切到「整組」時，React 會依 key 重用
                                        原本那條序列的 <Bar>，被留在原位的它就卡在堆疊最底層，
                                        造成疊出來的順序跟上面的清單對不起來。整棵重建最省事。 */}
                                    <ComposedChart key={`${effectiveSeries}-${effectiveType}-${effectiveMode}`}
                                                   data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
                                        <XAxis dataKey="period" fontSize={11} tick={{ fill: 'var(--text-muted)' }}
                                               axisLine={{ stroke: CHART_AXIS }} tickLine={false}
                                               interval="preserveStartEnd" minTickGap={18} />
                                        {/* 全正值時把上界壓到剛好蓋住資料的整數刻度：recharts 預設會湊到
                                            下一個大整數（$455B 的資料配 $600B 的軸，上面四分之一是空的），
                                            而硬壓上界又會生出 $474B 這種讀不動的刻度。 */}
                                        <YAxis fontSize={11} tick={{ fill: 'var(--text-muted)' }} width={52}
                                               axisLine={false} tickLine={false} tickFormatter={axisFmt}
                                               domain={scale ? scale.domain : undefined}
                                               ticks={scale ? scale.ticks : undefined} />
                                        <Tooltip
                                            cursor={effectiveType === 'bar'
                                                ? { fill: 'rgba(148,163,184,0.12)' }
                                                : { stroke: CHART_REFERENCE, strokeDasharray: '3 3' }}
                                            contentStyle={chartTooltipStyle}
                                            labelStyle={chartTooltipLabelStyle}
                                            formatter={(val, name) => [formatValue(val, chartKind), name]} />
                                        {(effectiveMode === 'growth' || hasNegative) && (
                                            <ReferenceLine y={0} stroke={CHART_REFERENCE} />
                                        )}
                                        {series.map((sr, si) => {
                                            const solo = effectiveSeries === 'single';
                                            const base = solo
                                                ? (effectiveMode === 'growth' ? 'var(--accent-success)' : 'var(--accent-cyan)')
                                                : sr.color;
                                            // key 不能混進 spread：React 19 會警告並且拿不到它
                                            const common = { dataKey: sr.name, name: sr.name, isAnimationActive: false };
                                            if (effectiveType === 'line') {
                                                return <Line key={sr.name} {...common} type="monotone" stroke={base}
                                                             strokeWidth={2} dot={false} activeDot={{ r: 3 }}
                                                             connectNulls />;
                                            }
                                            if (effectiveType === 'area') {
                                                return <Area key={sr.name} {...common} type="monotone" stroke={base}
                                                             fill={base} fillOpacity={solo ? 0.22 : stacked ? 0.55 : 0.2}
                                                             strokeWidth={1.5}
                                                             stackId={stacked ? 'a' : undefined} connectNulls />;
                                            }
                                            return (
                                                <Bar key={sr.name} {...common} radius={stacked && si < series.length - 1 ? 0 : [3, 3, 0, 0]}
                                                     maxBarSize={28} fill={base}
                                                     stackId={stacked ? 'a' : undefined}>
                                                    {solo && chartData.map((entry, i) => {
                                                        const v = entry[sr.name];
                                                        const neg = v < 0;
                                                        const fill = effectiveMode === 'growth'
                                                            ? (neg ? 'var(--accent-danger)' : 'var(--accent-success)')
                                                            : (neg ? 'var(--accent-danger)' : 'var(--accent-cyan)');
                                                        return <Cell key={i} fill={fill}
                                                                     fillOpacity={i === chartData.length - 1 ? 1 : 0.62} />;
                                                    })}
                                                </Bar>
                                            );
                                        })}
                                    </ComposedChart>
                                </ResponsiveContainer>
                            </div>

                            {/* 控制列：純文字加底線，不再是三組膠囊套膠囊 */}
                            <div className="mb-toolbar">
                                <div className="mb-toolgroup" role="group" aria-label="圖表內容">
                                    {[['value', '數值'], ['growth', 'YoY']].map(([k, label]) => {
                                        const disabled = k === 'growth' && !canShowGrowth;
                                        const on = effectiveMode === k;
                                        return (
                                            <button key={k} disabled={disabled} aria-pressed={on}
                                                    onClick={() => setChartMode(k)}
                                                    title={disabled ? '這個指標沒有成長率序列' : undefined}
                                                    className={`ui-focus mb-tool ${on ? 'is-on' : ''}`}>
                                                {label}
                                            </button>
                                        );
                                    })}
                                </div>
                                <span className="mb-tooldiv" aria-hidden="true" />
                                <div className="mb-toolgroup" role="group" aria-label="畫幾條序列">
                                    {SERIES_MODES.map((m) => {
                                        const disabled = m.k === 'category' && !canShowCategory;
                                        const on = effectiveSeries === m.k;
                                        return (
                                            <button key={m.k} disabled={disabled} aria-pressed={on}
                                                    onClick={() => setSeriesMode(m.k)}
                                                    title={disabled ? '這個分類的項目單位不一致，疊在一起沒有意義' : m.hint}
                                                    className={`ui-focus mb-tool ${on ? 'is-on' : ''}`}>
                                                {m.label}
                                            </button>
                                        );
                                    })}
                                </div>
                                <span className="mb-tooldiv" aria-hidden="true" />
                                <div className="mb-toolgroup" role="group" aria-label="圖表類型">
                                    {CHART_TYPES.map((t) => {
                                        const Icon = t.icon;
                                        const on = effectiveType === t.k;
                                        return (
                                            <button key={t.k} aria-pressed={on} title={t.hint}
                                                    onClick={() => setChartType(t.k)}
                                                    className={`ui-focus mb-tool ${on ? 'is-on' : ''}`}>
                                                <Icon size={13} aria-hidden="true" />
                                                {t.label}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            <p className="mb-meta">
                                {chartData.length} 期 · {current.cat.annual ? '年度' : '季度'}
                                {stacked ? ' · 堆疊' : ''}
                                {effectiveSeries === 'single' && current.stats
                                    ? ` · 區間 ${formatValue(current.stats.lo, current.row.kind)}–${formatValue(current.stats.hi, current.row.kind)}`
                                    : ''}
                                <span className="mb-meta-unit">{KIND_LABEL[chartKind]}</span>
                            </p>
                        </>
                    ) : (
                        <p className="text-[13px] text-[var(--text-meta)]">點選下方任一列以顯示趨勢圖。</p>
                    )}
                </aside>

                {/* ── 分類表格 ── */}
                <div className="space-y-6 min-w-0 xl:order-1">
                    {cats.map((cat) => {
                        const periods = cat.periods.slice(0, visiblePeriods);
                        const open = !!openCats[cat.name];
                        const tableId = `${cat.id}-table`;
                        return (
                            <section key={cat.name} id={cat.id} className="metrics-section scroll-mt-24">
                                <button
                                    onClick={() => toggleCat(cat.name)}
                                    aria-expanded={open}
                                    aria-controls={tableId}
                                    className="metrics-section-header ui-focus"
                                >
                                    <div className="flex items-center gap-2.5 min-w-0">
                                        <span className="text-[15px] font-semibold truncate">{cat.name}</span>
                                        <span className={`shrink-0 text-[10.5px] px-1.5 py-0.5 rounded font-medium tracking-wide ${
                                            cat.annual
                                                ? 'bg-[rgba(217,119,6,0.12)] text-[var(--accent-amber-text)]'
                                                : 'bg-[rgba(0,82,255,0.10)] text-[var(--accent-cyan-text)]'}`}>
                                            {cat.annual ? '年度' : '季度'}
                                        </span>
                                        <span className="shrink-0 text-[12.5px] text-[var(--text-meta)] font-normal">
                                            {cat.rows.length} 項 · {cat.periods.length} 期
                                            {cat.uniformKind ? ` · ${KIND_LABEL[cat.uniformKind]}` : ''}
                                        </span>
                                    </div>
                                    <ChevronDown
                                        size={16}
                                        aria-hidden="true"
                                        className={`shrink-0 text-[var(--text-muted)] transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
                                    />
                                </button>

                                {open && (
                                    <div id={tableId} className="metrics-scroller overflow-x-auto styled-scrollbar">
                                        <table className="metrics-table">
                                            <caption className="sr-only">{ticker} {cat.name}，最近 {periods.length} 期</caption>
                                            <thead>
                                                <tr>
                                                    <th scope="col" className="metrics-th-sticky">指標</th>
                                                    {periods.map((p, i) => (
                                                        <th key={p} scope="col" className={`metrics-th ${i === 0 ? 'is-latest' : ''}`}>
                                                            {periodLabel(p, cat.annual)}
                                                        </th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {cat.rows.map((row) => {
                                                    const isActive = selected?.cat === cat.name && selected?.metric === row.name;
                                                    const signed = row.kind === 'growth';
                                                    return (
                                                        <tr
                                                            key={row.name}
                                                            tabIndex={0}
                                                            aria-current={isActive ? 'true' : undefined}
                                                            onClick={() => selectRow(cat.name, row.name)}
                                                            onKeyDown={(e) => {
                                                                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectRow(cat.name, row.name); }
                                                            }}
                                                            className={`metrics-tr ${isActive ? 'active' : ''}`}
                                                        >
                                                            <th scope="row" className="metrics-td-sticky">
                                                                <div className="flex items-center justify-between gap-3">
                                                                    <span className="text-[13.5px] font-medium text-[var(--text-primary)] truncate">
                                                                        {row.name}
                                                                    </span>
                                                                    <span className="hidden sm:inline-flex items-center gap-1.5 shrink-0">
                                                                        {!cat.uniformKind && (
                                                                            <span className="text-[10px] text-[var(--text-meta)] uppercase tracking-wide">{KIND_LABEL[row.kind]}</span>
                                                                        )}
                                                                        <Sparkline values={row.values.slice(0, visiblePeriods)} />
                                                                    </span>
                                                                </div>
                                                            </th>
                                                            {periods.map((p, i) => {
                                                                const val = row.values[i];
                                                                const g = row.growth?.values?.[i];
                                                                let cls = 'text-[var(--text-secondary)]';
                                                                if (signed && val != null) {
                                                                    cls = val > 0 ? 'text-[var(--accent-success-text)]'
                                                                        : val < 0 ? 'text-[var(--accent-danger-text)]' : cls;
                                                                }
                                                                return (
                                                                    <td key={p} className={`metrics-td ${cls} ${i === 0 ? 'is-latest' : ''}`}>
                                                                        {formatValue(val, row.kind)}
                                                                        {row.growth && (
                                                                            <span className={`mb-growth ${
                                                                                g == null ? 'text-[var(--text-muted)]'
                                                                                    : g > 0 ? 'text-[var(--accent-success-text)]'
                                                                                    : g < 0 ? 'text-[var(--accent-danger-text)]'
                                                                                    : 'text-[var(--text-muted)]'}`}>
                                                                                {g == null ? '—' : formatSigned(g)}
                                                                            </span>
                                                                        )}
                                                                    </td>
                                                                );
                                                            })}
                                                        </tr>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </section>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
