import { useState, useEffect } from 'react';
import { useRoute } from 'wouter';
import axios from 'axios';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts';
import { ArrowLeft, TrendingUp, TrendingDown, BarChart3, TableProperties, Ratio, Target, DollarSign } from 'lucide-react';
import { Link } from 'wouter';

export default function StockDetail() {
    const [match, params] = useRoute("/stock/:ticker");
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [livePrice, setLivePrice] = useState(null);

    const ticker = params?.ticker;

    useEffect(() => {
        if (ticker) {
            axios.get(`/api/valuations/${ticker}/history`)
                .then(res => {
                    setData(res.data);
                    setLoading(false);
                })
                .catch(err => {
                    console.error(err);
                    setLoading(false);
                });

            // Fetch live price
            axios.get(`/api/stocks/${ticker}/price`)
                .then(res => setLivePrice(res.data))
                .catch(err => console.error('Failed to fetch live price:', err));
        }
    }, [ticker]);

    if (loading) {
        return (
            <div className="container py-20 flex justify-center">
                <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (!data.length) {
        return (
            <div className="container py-20 text-center">
                <p className="text-[var(--text-muted)]">No data found for <span className="font-mono font-bold text-[var(--text-primary)]">{ticker}</span></p>
            </div>
        );
    }

    const latest = data[data.length - 1];
    const isUndervalued = latest.valuation_status === 'undervalued';

    return (
        <div className="container py-10 space-y-8 animate-fade-in">
            {/* Back button */}
            <Link href="/valuations" className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
                <ArrowLeft size={14} /> Back to Valuations
            </Link>

            {/* Header */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-6 pb-8 border-b border-[var(--glass-border)]">
                <div>
                    <div className="flex items-center gap-3 mb-3">
                        <h1 className="text-4xl font-extrabold tracking-tight">{latest.ticker}</h1>
                        <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
                            isUndervalued
                                ? 'bg-[rgba(52,211,153,0.1)] text-[var(--accent-success)] border border-[rgba(52,211,153,0.15)]'
                                : 'bg-[rgba(248,113,113,0.1)] text-[var(--accent-danger)] border border-[rgba(248,113,113,0.15)]'
                        }`}>
                            {isUndervalued ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                            {latest.valuation_status?.toUpperCase()} {Math.abs(latest.valuation_diff_pct)}%
                        </div>
                    </div>
                    <p className="text-lg text-[var(--text-secondary)] mb-2">{latest.company_name}</p>
                    <div className="flex items-center gap-2 flex-wrap">
                        <span className="badge badge-neutral">{latest.industry}</span>
                        {latest.market_cap_display && (
                            <span className="badge badge-neutral">{latest.market_cap_display} Cap</span>
                        )}
                        <Link
                            href={`/stock/${ticker}/metrics`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-[13px] font-semibold text-[var(--accent-cyan)] bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.15)] hover:bg-[rgba(0,212,255,0.14)] transition-colors"
                        >
                            <BarChart3 size={13} /> Metrics Breakdown
                        </Link>
                        <Link
                            href={`/stock/${ticker}/statistics`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-[13px] font-semibold text-[var(--accent-purple)] bg-[rgba(139,92,246,0.08)] border border-[rgba(139,92,246,0.15)] hover:bg-[rgba(139,92,246,0.14)] transition-colors"
                        >
                            <TableProperties size={13} /> Key Statistics
                        </Link>
                        <Link
                            href={`/stock/${ticker}/ratios`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-[13px] font-semibold text-[var(--accent-amber)] bg-[rgba(251,191,36,0.08)] border border-[rgba(251,191,36,0.15)] hover:bg-[rgba(251,191,36,0.14)] transition-colors"
                        >
                            <Ratio size={13} /> Financial Ratios
                        </Link>
                        <Link
                            href={`/stock/${ticker}/forecast`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-[13px] font-semibold text-[var(--accent-success)] bg-[rgba(52,211,153,0.08)] border border-[rgba(52,211,153,0.15)] hover:bg-[rgba(52,211,153,0.14)] transition-colors"
                        >
                            <Target size={13} /> Analyst Forecast
                        </Link>
                        <Link
                            href={`/stock/${ticker}/dcf`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-[13px] font-semibold text-[var(--accent-danger)] bg-[rgba(248,113,113,0.08)] border border-[rgba(248,113,113,0.15)] hover:bg-[rgba(248,113,113,0.14)] transition-colors"
                        >
                            <DollarSign size={13} /> DCF Valuation
                        </Link>
                    </div>
                </div>
                <div className="text-left md:text-right">
                    <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-1">
                        {livePrice ? 'Live Price' : 'Current Price'}
                    </div>
                    <div className="text-4xl font-extrabold font-mono tabular-nums tracking-tight">
                        ${livePrice?.price?.toFixed(2) ?? latest.current_price?.toFixed(2)}
                    </div>
                    {livePrice && (
                        <div className={`flex items-center gap-1.5 mt-1 text-sm font-semibold ${
                            livePrice.change >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                        }`}>
                            {livePrice.change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                            {livePrice.change >= 0 ? '+' : ''}{livePrice.change?.toFixed(2)} ({livePrice.change_percent?.toFixed(2)}%)
                        </div>
                    )}
                </div>
            </div>

            {/* PE Valuation Comparison */}
            {(() => {
                const currentPrice = livePrice?.price ?? latest.current_price;
                const peFairValue = latest.pe_median_price;
                const growthFairValue = latest.current_year_fair_price;
                const nextYearFairValue = latest.next_year_fair_price;

                const peDiff = peFairValue ? ((currentPrice - peFairValue) / peFairValue * 100).toFixed(1) : null;
                const growthDiff = growthFairValue ? ((currentPrice - growthFairValue) / growthFairValue * 100).toFixed(1) : null;
                const nextYearDiff = nextYearFairValue ? ((currentPrice - nextYearFairValue) / nextYearFairValue * 100).toFixed(1) : null;

                const isPeUndervalued = peDiff && parseFloat(peDiff) < 0;
                const isGrowthUndervalued = growthDiff && parseFloat(growthDiff) < 0;
                const isNextYearUndervalued = nextYearDiff && parseFloat(nextYearDiff) < 0;

                return (
                    <div className="card !p-6">
                        <h3 className="text-base font-semibold mb-4 text-[var(--text-secondary)]">
                            {livePrice ? 'Live Valuation Analysis' : 'Valuation Analysis'}
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {/* PE Fair Value Comparison */}
                            <div className="p-4 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                                <div className="flex justify-between items-start mb-3">
                                    <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider">vs PE Fair Value</div>
                                    <div className={`px-2 py-0.5 rounded text-xs font-semibold ${
                                        isPeUndervalued
                                            ? 'bg-[rgba(52,211,153,0.15)] text-[var(--accent-success)]'
                                            : 'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)]'
                                    }`}>
                                        {isPeUndervalued ? 'Undervalued' : 'Overvalued'}
                                    </div>
                                </div>
                                <div className="flex items-baseline gap-2">
                                    <span className="text-2xl font-bold font-mono">${peFairValue}</span>
                                    <span className={`text-sm font-semibold ${
                                        isPeUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                    }`}>
                                        {isPeUndervalued ? '' : '+'}{peDiff}%
                                    </span>
                                </div>
                                <div className="text-xs text-[var(--text-muted)] mt-1">
                                    Based on 5Y Median PE: {latest.pe_median_5y}
                                </div>
                            </div>

                            {/* Growth Fair Value Comparison */}
                            <div className="p-4 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                                <div className="flex justify-between items-start mb-3">
                                    <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider">vs Growth Fair Value</div>
                                    <div className={`px-2 py-0.5 rounded text-xs font-semibold ${
                                        isGrowthUndervalued
                                            ? 'bg-[rgba(52,211,153,0.15)] text-[var(--accent-success)]'
                                            : 'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)]'
                                    }`}>
                                        {isGrowthUndervalued ? 'Undervalued' : 'Overvalued'}
                                    </div>
                                </div>
                                <div className="flex items-baseline gap-2">
                                    <span className="text-2xl font-bold font-mono">${growthFairValue}</span>
                                    <span className={`text-sm font-semibold ${
                                        isGrowthUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                    }`}>
                                        {isGrowthUndervalued ? '' : '+'}{growthDiff}%
                                    </span>
                                </div>
                                <div className="text-xs text-[var(--text-muted)] mt-1">
                                    Based on Est PE: {latest.estimated_pe} × EPS: ${latest.current_year_eps}
                                </div>
                            </div>

                            {/* Next Year Fair Value Comparison */}
                            <div className="p-4 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                                <div className="flex justify-between items-start mb-3">
                                    <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider">vs Next Year Fair Value</div>
                                    <div className={`px-2 py-0.5 rounded text-xs font-semibold ${
                                        isNextYearUndervalued
                                            ? 'bg-[rgba(52,211,153,0.15)] text-[var(--accent-success)]'
                                            : 'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)]'
                                    }`}>
                                        {isNextYearUndervalued ? 'Undervalued' : 'Overvalued'}
                                    </div>
                                </div>
                                <div className="flex items-baseline gap-2">
                                    <span className="text-2xl font-bold font-mono">${nextYearFairValue}</span>
                                    <span className={`text-sm font-semibold ${
                                        isNextYearUndervalued ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'
                                    }`}>
                                        {isNextYearUndervalued ? '' : '+'}{nextYearDiff}%
                                    </span>
                                </div>
                                <div className="text-xs text-[var(--text-muted)] mt-1">
                                    Based on Next Year EPS: ${latest.next_year_eps}
                                </div>
                            </div>
                        </div>
                    </div>
                );
            })()}

            {/* Stats Grid */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard
                    label="Fair Value (PE)"
                    value={`$${latest.pe_median_price}`}
                    sub={`Median PE: ${latest.pe_median_5y}`}
                    accent="var(--accent-success)"
                />
                <StatCard
                    label="Fair Value (Growth)"
                    value={`$${latest.current_year_fair_price}`}
                    sub={`Est PE: ${latest.estimated_pe}`}
                    accent="var(--accent-purple)"
                />
                <StatCard
                    label="EPS (Current)"
                    value={`$${latest.current_year_eps}`}
                    sub={`Growth: ${latest.eps_growth_past}%`}
                    accent="var(--accent-cyan)"
                />
                <StatCard
                    label="EPS (Next Year)"
                    value={`$${latest.next_year_eps}`}
                    sub={`5Y Growth: ${latest.eps_growth_5y}%`}
                    accent="var(--accent-amber)"
                />
            </div>

            {/* Chart */}
            <div className="card !p-6">
                <h3 className="text-base font-semibold mb-6 text-[var(--text-secondary)]">Valuation History</h3>
                <div className="h-[380px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                            <XAxis
                                dataKey="snapshot_date"
                                stroke="var(--text-muted)"
                                fontSize={11}
                                tickFormatter={(str) => str.slice(5)}
                                tick={{ fill: 'var(--text-muted)' }}
                                axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                                tickLine={false}
                            />
                            <YAxis
                                stroke="var(--text-muted)"
                                fontSize={11}
                                domain={['auto', 'auto']}
                                tick={{ fill: 'var(--text-muted)' }}
                                axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                                tickLine={false}
                                tickFormatter={(v) => `$${v}`}
                            />
                            <Tooltip
                                contentStyle={{
                                    backgroundColor: 'var(--bg-secondary)',
                                    border: '1px solid var(--glass-border)',
                                    borderRadius: '10px',
                                    fontSize: '12px',
                                    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                                }}
                                itemStyle={{ color: '#fff', fontSize: '12px' }}
                                labelStyle={{ color: 'var(--text-muted)', marginBottom: '4px' }}
                            />
                            <Legend
                                wrapperStyle={{ fontSize: '12px', paddingTop: '16px' }}
                                iconType="circle"
                                iconSize={8}
                            />
                            <Line
                                type="monotone"
                                dataKey="current_price"
                                stroke="var(--text-primary)"
                                strokeWidth={2}
                                name="Price"
                                dot={false}
                                activeDot={{ r: 4, fill: 'var(--text-primary)', stroke: 'var(--bg-primary)', strokeWidth: 2 }}
                            />
                            <Line
                                type="monotone"
                                dataKey="pe_median_price"
                                stroke="var(--accent-success)"
                                strokeWidth={2}
                                name="Fair Value (PE)"
                                dot={false}
                                strokeDasharray="5 5"
                                activeDot={{ r: 4, fill: 'var(--accent-success)', stroke: 'var(--bg-primary)', strokeWidth: 2 }}
                            />
                            <Line
                                type="monotone"
                                dataKey="current_year_fair_price"
                                stroke="var(--accent-purple)"
                                strokeWidth={2}
                                name="Fair Value (Growth)"
                                dot={false}
                                strokeDasharray="5 5"
                                activeDot={{ r: 4, fill: 'var(--accent-purple)', stroke: 'var(--bg-primary)', strokeWidth: 2 }}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    );
}

function StatCard({ label, value, sub, accent }) {
    return (
        <div className="card group">
            <div className="text-[var(--text-muted)] text-xs uppercase tracking-wider mb-2">{label}</div>
            <div className="text-2xl font-bold font-mono tabular-nums mb-1">{value}</div>
            <div className="text-xs font-medium" style={{ color: accent }}>{sub}</div>
        </div>
    );
}
