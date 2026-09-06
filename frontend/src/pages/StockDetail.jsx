import { useState, useEffect, useRef, useCallback } from 'react';
import { useRoute } from 'wouter';
import axios from 'axios';
import { ArrowLeft, TrendingUp, TrendingDown, BarChart3, TableProperties, Ratio, Target, DollarSign, Activity, RefreshCw, CandlestickChart, Users, Newspaper, Sparkles, FileText, PieChart, Sheet } from 'lucide-react';
import AnalystRevisionPanel from '../components/AnalystRevisionPanel';
import PriceChartPanel from '../components/PriceChartPanel';
import KeyMetricsSummary from '../components/KeyMetricsSummary';
import StockNewsPanel from '../components/StockNewsPanel';
import GroupSelect from '../components/GroupSelect';
import PeerValuationSummary from '../components/PeerValuationSummary';
import TechStrip from '../components/TechStrip';
import { Link } from 'wouter';

// ── Auto-scrape panel shown when no valuation data exists yet ─────────────────
function ScrapePanel({ ticker, onDone }) {
    const [jobId, setJobId]     = useState(null);
    const [steps, setSteps]     = useState([]);
    const [status, setStatus]   = useState('idle'); // idle | running | completed | failed
    const [error, setError]     = useState(null);
    const pollRef               = useRef(null);

    const startScrape = async () => {
        setStatus('running');
        setSteps([]);
        setError(null);
        try {
            const { data } = await axios.post('/api/scrape', { ticker, group: '其他' });
            setJobId(data.job_id);
        } catch (e) {
            setStatus('failed');
            setError(e?.response?.data?.detail ?? e.message);
        }
    };

    // Poll job until done
    useEffect(() => {
        if (!jobId) return;
        pollRef.current = setInterval(async () => {
            try {
                const { data } = await axios.get(`/api/scrape/${jobId}`);
                setSteps(data.steps ?? []);
                if (data.status === 'completed') {
                    clearInterval(pollRef.current);
                    setStatus('completed');
                    setTimeout(onDone, 800);
                } else if (data.status === 'failed') {
                    clearInterval(pollRef.current);
                    setStatus('failed');
                    setError(data.error);
                }
            } catch { clearInterval(pollRef.current); }
        }, 2000);
        return () => clearInterval(pollRef.current);
    }, [jobId]);

    // Auto-start on mount
    useEffect(() => { startScrape(); }, []);

    return (
        <div className="container py-16 max-w-lg mx-auto">
            <div className="rounded-2xl border p-6 space-y-4"
                style={{ background: 'var(--card-bg)', borderColor: 'var(--border)' }}>
                <div className="flex items-center gap-3">
                    <RefreshCw size={16} className={status === 'running' ? 'animate-spin text-blue-400' : status === 'completed' ? 'text-green-400' : 'text-red-400'} />
                    <div>
                        <p className="text-sm font-semibold text-[var(--text-primary)]">
                            {status === 'running' ? `Scraping ${ticker}…` : status === 'completed' ? 'Done — loading data' : `Scrape failed`}
                        </p>
                        <p className="text-xs text-[var(--text-muted)]">
                            {status === 'running' ? 'Fetching ratios, statistics, PE, forecasts…' : status === 'failed' ? error : ''}
                        </p>
                    </div>
                </div>

                {steps.length > 0 && (
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                        {steps.map((s, i) => (
                            <p key={i} className="text-[11px] text-[var(--text-secondary)] font-mono leading-relaxed">
                                {s}
                            </p>
                        ))}
                    </div>
                )}

                {status === 'failed' && (
                    <button
                        onClick={startScrape}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--text-secondary)] transition-colors"
                    >
                        Retry
                    </button>
                )}
            </div>
        </div>
    );
}

export default function StockDetail() {
    const [match, params] = useRoute("/stock/:ticker");
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [livePrice, setLivePrice] = useState(null);
    const [groups, setGroups] = useState([]);
    const [changingGroup, setChangingGroup] = useState(false);
    // 圖表元件載入完回傳的技術面資料（隨圖表區間切換更新），餵給上方的技術指標速覽
    const [tech, setTech] = useState(null);
    const onAnalytics = useCallback((payload) => setTech(payload), []);

    const ticker = params?.ticker;

    const loadData = () => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/valuations/${ticker}/history`)
            .then(res => { setData(res.data); setLoading(false); })
            .catch(err => { console.error(err); setLoading(false); });

        axios.get(`/api/stocks/${ticker}/price`)
            .then(res => setLivePrice(res.data))
            .catch(() => {});
    };

    // Reassign this ticker's sector/group, then reload so badges/valuations refresh.
    const changeGroup = (newGroup) => {
        const cur = data[data.length - 1]?.industry;
        if (!ticker || !newGroup || newGroup === cur) return;
        setChangingGroup(true);
        axios.patch(`/api/stocks/${ticker}/industry`, { industry: newGroup })
            .then(() => {
                setGroups(prev => prev.includes(newGroup) ? prev : [...prev, newGroup].sort());
                loadData();
            })
            .catch(err => console.error(err))
            .finally(() => setChangingGroup(false));
    };

    useEffect(() => { setTech(null); loadData(); }, [ticker]);

    // Available sector/group names for the inline "change group" dropdown.
    useEffect(() => {
        axios.get('/api/scrape').then(r => setGroups(r.data.groups || [])).catch(() => {});
    }, []);

    if (loading) {
        return (
            <div className="container py-20 flex justify-center">
                <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (!data.length) {
        return <ScrapePanel ticker={ticker} onDone={loadData} />;
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
                        <GroupSelect
                            value={latest.industry}
                            options={groups}
                            loading={changingGroup}
                            onChange={changeGroup}
                            size="badge"
                            align="left"
                            ariaLabel={`Change sector for ${latest.ticker}`}
                        />
                        {latest.market_cap_display && (
                            <span className="badge badge-neutral">{latest.market_cap_display} Cap</span>
                        )}
                    </div>
                    <nav className="mt-3 flex items-center gap-1.5 flex-wrap">
                        <Link
                            href={`/stock/${ticker}/ai`}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[13px] font-semibold text-[var(--accent-cyan)] bg-[rgba(0,82,255,0.08)] border border-[rgba(0,82,255,0.25)] hover:bg-[rgba(0,82,255,0.14)] transition-colors"
                        >
                            <Sparkles size={13} /> AI 分析
                        </Link>
                        {[
                            { href: `/stock/${ticker}/metrics`,    icon: BarChart3,        label: 'Metrics' },
                            { href: `/stock/${ticker}/statistics`, icon: TableProperties,  label: 'Statistics' },
                            { href: `/stock/${ticker}/ratios`,     icon: Ratio,            label: 'Ratios' },
                            { href: `/stock/${ticker}/forecast`,   icon: Target,           label: 'Forecast' },
                            { href: `/stock/${ticker}/dcf`,        icon: DollarSign,       label: 'DCF' },
                            { href: `/stock/${ticker}/pe`,         icon: Activity,         label: 'PE History' },
                            { href: `/stock/${ticker}/options`,    icon: CandlestickChart, label: 'Options / IV' },
                            { href: `/stock/${ticker}/peers`,      icon: Users,            label: '同業比較' },
                            { href: `/stock/${ticker}/metrics?tab=income`, icon: Sheet, label: '三大財報' },
                            { href: `/stock/${ticker}/segments`,   icon: PieChart,         label: '分部門' },
                            { href: `/stock/${ticker}/earnings-notes`, icon: FileText,     label: '財報筆記' },
                            { href: '#news',                       icon: Newspaper,        label: 'News', anchor: true },
                        ].map(({ href, icon: Icon, label, anchor }) => {
                            const cls = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[13px] font-medium text-[var(--text-secondary)] bg-[var(--bg-secondary)] border border-[var(--glass-border)] hover:text-[var(--accent-cyan)] hover:border-[rgba(0,82,255,0.35)] hover:bg-[rgba(0,82,255,0.06)] transition-colors';
                            return anchor
                                ? <a key={label} href={href} className={cls}><Icon size={13} /> {label}</a>
                                : <Link key={label} href={href} className={cls}><Icon size={13} /> {label}</Link>;
                        })}
                    </nav>
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

            {/* 技術面呈現沿用指數頁（MarketDetail）那一套：先一列八格的指標速覽，
                再走勢圖置頂、圖層與完整技術分析收進折疊區。個股頁原本是把四張分析卡
                全攤開擺在圖前面，第一眼看不到價格 */}
            <TechStrip tech={tech} />
            <PriceChartPanel ticker={ticker} chartFirst onAnalytics={onAnalytics} />

            {/* Key Metrics Summary */}
            <KeyMetricsSummary ticker={ticker} />

            {/* PE Valuation Comparison */}
            {(() => {
                const currentPrice = livePrice?.price ?? latest.current_price;
                const peFairValue = latest.pe_median_price;
                const nextYearFairValue = latest.next_year_fair_price;

                const peDiff = peFairValue ? ((currentPrice - peFairValue) / peFairValue * 100).toFixed(1) : null;
                const nextYearDiff = nextYearFairValue ? ((currentPrice - nextYearFairValue) / nextYearFairValue * 100).toFixed(1) : null;

                const isPeUndervalued = peDiff && parseFloat(peDiff) < 0;
                const isNextYearUndervalued = nextYearDiff && parseFloat(nextYearDiff) < 0;

                return (
                    <div className="card !p-6">
                        <h3 className="text-base font-semibold mb-4 text-[var(--text-secondary)]">
                            {livePrice ? 'Live Valuation Analysis' : 'Valuation Analysis'}
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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

                            {/* Next Year Fair Value Comparison */}
                            <div className="p-4 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                                <div className="flex justify-between items-start mb-3">
                                    <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider">vs Next Year Fair Value</div>
                                    {nextYearDiff != null && (
                                        <div className={`px-2 py-0.5 rounded text-xs font-semibold ${
                                            isNextYearUndervalued
                                                ? 'bg-[rgba(52,211,153,0.15)] text-[var(--accent-success)]'
                                                : 'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)]'
                                        }`}>
                                            {isNextYearUndervalued ? 'Undervalued' : 'Overvalued'}
                                        </div>
                                    )}
                                </div>
                                {nextYearDiff == null ? (
                                    <div className="text-sm text-[var(--text-muted)] py-2">
                                        無下一年度 EPS 預估資料
                                    </div>
                                ) : (
                                    <>
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
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                );
            })()}

            {/* 同業比較摘要 —— 上面兩張卡是「跟自己的歷史比」，這張是「跟同業比」。
                完整的十個指標與逐檔明細在 /stock/:ticker/peers，這裡只放結論與入口。 */}
            <PeerValuationSummary ticker={ticker} />

            {/* Stats Grid */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard
                    label="Fair Value (PE)"
                    value={`$${latest.pe_median_price}`}
                    sub={`Median PE: ${latest.pe_median_5y}`}
                    accent="var(--accent-success)"
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
                    sub={latest.next_year_eps && latest.current_year_eps
                        ? `YoY Growth: ${((latest.next_year_eps - latest.current_year_eps) / latest.current_year_eps * 100).toFixed(1)}%`
                        : null}
                    accent="var(--accent-amber)"
                />
            </div>

            {/* Analyst revisions — replaces the old price-vs-fair-value history chart,
                which only restated what the valuation cards above already show. */}
            <AnalystRevisionPanel key={ticker} ticker={ticker} />

            {/* Latest News (same source as Stock Digest — Google News RSS) */}
            <StockNewsPanel ticker={ticker} company={latest.company_name} />
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
