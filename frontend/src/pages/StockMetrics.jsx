import { useState, useEffect, useRef } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts';
import { ArrowLeft, ChevronDown, BarChart3 } from 'lucide-react';
import { CHART_GRID, CHART_AXIS } from '../components/chartTheme';

const COLORS = [
    'var(--accent-cyan)',
    'var(--accent-purple)',
    'var(--accent-success)',
    'var(--accent-amber)',
    'var(--accent-danger)',
    '#60a5fa',
    '#a78bfa',
    '#f472b6',
    '#2dd4bf',
    '#fb923c',
];

function formatValue(val, metricName) {
    if (val == null) return '—';
    const name = metricName.toLowerCase();
    const isGrowth = name.includes('growth');
    const isMargin = name.includes('margin');
    const isPct = isGrowth || isMargin;

    if (isPct) return `${val.toFixed(2)}%`;

    const abs = Math.abs(val);
    if (abs >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
    if (abs >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
    return `$${val.toFixed(2)}`;
}

function formatCompactPeriod(period) {
    // "Dec 31, 2025" → "Q4'25"  /  "Mar 31, 2025" → "Q2'25"
    const monthMap = {
        'Mar': 'Q2', 'Jun': 'Q3', 'Sep': 'Q4', 'Dec': 'Q1',
    };
    const parts = period.split(' ');
    if (parts.length < 3) return period;
    const month = parts[0];
    const year = parts[2];
    const q = monthMap[month] || month;
    // Dec 31, 2025 is fiscal Q1 of 2026 for Apple, but let's keep calendar quarter
    // Actually let's use simpler: just month + short year
    return `${month}'${year.slice(-2)}`;
}

export default function StockMetrics() {
    const [, params] = useRoute('/stock/:ticker/metrics');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [openCats, setOpenCats] = useState({});
    const [selectedMetric, setSelectedMetric] = useState(null);
    // How many periods to show in the table
    const [visiblePeriods, setVisiblePeriods] = useState(12);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/metrics-breakdown`)
            .then(res => {
                setData(res.data);
                // Open all categories by default
                const open = {};
                res.data.categories.forEach(c => { open[c.name] = true; });
                setOpenCats(open);
                // Select first non-growth metric for chart
                const first = res.data.categories[0]?.metrics[0];
                if (first) setSelectedMetric({ cat: res.data.categories[0].name, metric: first.name });
            })
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || 'Failed to load metrics data');
            })
            .finally(() => setLoading(false));
    }, [ticker]);

    if (loading) {
        return (
            <div className="container py-20 flex justify-center">
                <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="container py-20 text-center">
                <p className="text-[var(--text-muted)] text-base">{error}</p>
                <Link href={`/stock/${ticker}`} className="text-sm text-[var(--accent-purple)] hover:underline mt-4 inline-block">
                    Back to {ticker}
                </Link>
            </div>
        );
    }

    const toggleCat = (name) => setOpenCats(prev => ({ ...prev, [name]: !prev[name] }));

    // Build chart data for the selected metric
    let chartData = [];
    if (selectedMetric && data) {
        const cat = data.categories.find(c => c.name === selectedMetric.cat);
        const met = cat?.metrics.find(m => m.name === selectedMetric.metric);
        if (met) {
            // Take up to 20 most recent non-null periods for the chart
            chartData = data.periods
                .map((p, i) => ({ period: formatCompactPeriod(p), value: met.values[i] }))
                .filter(d => d.value != null)
                .slice(0, 20)
                .reverse();
        }
    }

    const isSelectedGrowthOrMargin = selectedMetric?.metric.toLowerCase().includes('growth')
        || selectedMetric?.metric.toLowerCase().includes('margin');

    const periods = data.periods.slice(0, visiblePeriods);

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            {/* Navigation */}
            <Link
                href={`/stock/${ticker}`}
                className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            >
                <ArrowLeft size={14} /> Back to {ticker}
            </Link>

            {/* Header */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <div className="flex items-center gap-3 mb-2">
                        <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
                        <span className="badge badge-neutral">Metrics Breakdown</span>
                    </div>
                    <p className="text-[15px] text-[var(--text-muted)]">
                        {data.categories.length} sections &middot; {data.periods.length} quarterly periods &middot; Data as of {data.date}
                    </p>
                </div>
                {/* Period range selector */}
                <div className="flex items-center gap-2">
                    {[8, 12, 20, data.periods.length].map(n => (
                        <button
                            key={n}
                            onClick={() => setVisiblePeriods(n)}
                            className={`px-3 py-1.5 rounded-lg text-[13px] font-medium transition-colors ${
                                visiblePeriods === n
                                    ? 'bg-[var(--accent-purple)] text-white'
                                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-bg)] border border-[var(--glass-border)]'
                            }`}
                        >
                            {n === data.periods.length ? 'All' : `${n}Q`}
                        </button>
                    ))}
                </div>
            </div>

            {/* Chart */}
            {chartData.length > 0 && selectedMetric && (
                <div className="metrics-chart-card">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="text-[15px] font-semibold text-[var(--text-secondary)]">
                            <BarChart3 size={16} className="inline mr-2 opacity-50" />
                            {selectedMetric.metric}
                        </h3>
                        <span className="text-[13px] text-[var(--text-muted)]">
                            {selectedMetric.cat}
                        </span>
                    </div>
                    <div className="h-[260px]">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                                <XAxis
                                    dataKey="period"
                                    stroke="var(--text-muted)"
                                    fontSize={11}
                                    tick={{ fill: 'var(--text-muted)' }}
                                    axisLine={{ stroke: CHART_AXIS }}
                                    tickLine={false}
                                />
                                <YAxis
                                    stroke="var(--text-muted)"
                                    fontSize={11}
                                    tick={{ fill: 'var(--text-muted)' }}
                                    axisLine={{ stroke: CHART_AXIS }}
                                    tickLine={false}
                                    tickFormatter={v => {
                                        if (isSelectedGrowthOrMargin) return `${v}%`;
                                        if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
                                        if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(0)}B`;
                                        if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
                                        return v;
                                    }}
                                />
                                <Tooltip
                                    contentStyle={{
                                        backgroundColor: 'var(--bg-secondary)',
                                        border: '1px solid var(--glass-border)',
                                        borderRadius: '10px',
                                        fontSize: '13px',
                                        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                                    }}
                                    labelStyle={{ color: 'var(--text-muted)', marginBottom: 4 }}
                                    formatter={(val) => [formatValue(val, selectedMetric.metric), selectedMetric.metric]}
                                />
                                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                                    {chartData.map((entry, i) => (
                                        <Cell
                                            key={i}
                                            fill={entry.value < 0 ? 'var(--accent-danger)' : 'var(--accent-cyan)'}
                                            fillOpacity={0.75}
                                        />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

            {/* Category tables */}
            {data.categories.map((cat, catIdx) => (
                <div key={cat.name} className="metrics-section">
                    {/* Category header */}
                    <button
                        onClick={() => toggleCat(cat.name)}
                        className="metrics-section-header"
                    >
                        <div className="flex items-center gap-3">
                            <span
                                className="w-3 h-3 rounded-sm shrink-0"
                                style={{ background: COLORS[catIdx % COLORS.length] }}
                            />
                            <span className="text-[15px] font-semibold">{cat.name}</span>
                            <span className="text-[13px] text-[var(--text-muted)] font-normal">
                                {cat.metrics.length} metrics
                            </span>
                        </div>
                        <ChevronDown
                            size={16}
                            className={`text-[var(--text-muted)] transition-transform duration-200 ${openCats[cat.name] ? 'rotate-180' : ''}`}
                        />
                    </button>

                    {/* Table */}
                    {openCats[cat.name] && (
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">Metric</th>
                                        {periods.map(p => (
                                            <th key={p} className="metrics-th">{formatCompactPeriod(p)}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {cat.metrics.map((met, metIdx) => {
                                        const isActive = selectedMetric?.cat === cat.name && selectedMetric?.metric === met.name;
                                        const isGrowth = met.name.toLowerCase().includes('growth');
                                        const isMargin = met.name.toLowerCase().includes('margin');

                                        return (
                                            <tr
                                                key={met.name}
                                                onClick={() => setSelectedMetric({ cat: cat.name, metric: met.name })}
                                                className={`metrics-tr ${isActive ? 'active' : ''}`}
                                            >
                                                <td className="metrics-td-sticky">
                                                    <span className="text-[14px] font-medium text-[var(--text-primary)]">
                                                        {met.name}
                                                    </span>
                                                </td>
                                                {periods.map((p, pIdx) => {
                                                    const val = met.values[pIdx];
                                                    const prevVal = met.values[pIdx + 1]; // older period

                                                    let textColor = 'text-[var(--text-secondary)]';
                                                    if ((isGrowth || isMargin) && val != null) {
                                                        textColor = val >= 0
                                                            ? 'text-[var(--accent-success)]'
                                                            : 'text-[var(--accent-danger)]';
                                                    }

                                                    return (
                                                        <td key={p} className={`metrics-td ${textColor}`}>
                                                            {formatValue(val, met.name)}
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
                </div>
            ))}
        </div>
    );
}
