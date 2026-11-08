import { useState, useEffect, useRef, useCallback } from 'react';
import { useRoute } from 'wouter';
import axios from '../lib/apiClient';
import { TrendingUp, TrendingDown, RefreshCw } from 'lucide-react';
import StockPageHeader from '../components/StockPageHeader';
import StockConclusions from '../components/StockConclusions';
import { useStockTabs } from '../components/useStockHeader';
import AnalystRevisionPanel from '../components/AnalystRevisionPanel';
import PriceChartPanel from '../components/PriceChartPanel';
import KeyMetricsSummary from '../components/KeyMetricsSummary';
import StockNewsPanel from '../components/StockNewsPanel';
import GroupSelect from '../components/GroupSelect';
import TechStrip from '../components/TechStrip';

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
    const [, params] = useRoute("/stock/:ticker");
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [livePrice, setLivePrice] = useState(null);
    const [groups, setGroups] = useState([]);
    const [changingGroup, setChangingGroup] = useState(false);
    // 圖表元件載入完回傳的技術面資料（隨圖表區間切換更新），餵給上方的技術指標速覽
    const [tech, setTech] = useState(null);
    const onAnalytics = useCallback((payload) => setTech(payload), []);
    // 各切面的結論與有無資料（跟分頁列共用同一份快取）
    const tabsInfo = useStockTabs(params?.ticker);

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

    // 子頁分頁列的「新聞」連到 /stock/T#news：這頁載完後把新聞區塊捲進視野。
    // 上面的走勢圖、指標、分析師面板各自非同步載入，新聞區塊會一路被往下推，
    // 所以捲一次不夠，之後幾秒內再對齊幾次，直到版面穩定
    useEffect(() => {
        if (loading || !data.length || window.location.hash !== '#news') return;
        const go = (smooth) => document.getElementById('news')
            ?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
        go(true);
        const timers = [600, 1500, 3000].map((ms) => setTimeout(() => go(false), ms));
        return () => timers.forEach(clearTimeout);
    }, [loading, data.length, ticker]);

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
    // 還沒拿到即時報價前，先用估值快照裡的收盤價把標頭填起來
    const headerPrice = livePrice ?? (latest.current_price != null ? { price: latest.current_price } : null);

    return (
        <div className="container py-10 space-y-8 animate-fade-in">
            {/* 標頭與分頁列跟 11 個子頁共用；這頁自己抓了估值歷史與現價，直接餵進去 */}
            <StockPageHeader
                ticker={ticker}
                info={latest}
                price={headerPrice}
                industryNode={
                    <GroupSelect
                        value={latest.industry}
                        options={groups}
                        loading={changingGroup}
                        onChange={changeGroup}
                        size="badge"
                        align="left"
                        ariaLabel={`Change sector for ${latest.ticker}`}
                    />
                }
            />

            {/* 技術面呈現沿用指數頁（MarketDetail）那一套：先一列八格的指標速覽，
                再走勢圖置頂、圖層與完整技術分析收進折疊區。個股頁原本是把四張分析卡
                全攤開擺在圖前面，第一眼看不到價格 */}
            <TechStrip tech={tech} />
            <PriceChartPanel ticker={ticker} chartFirst onAnalytics={onAnalytics} />

            {/* 各切面的一句話結論：四個合理價來源對現價、AI 立場、最新財報筆記。證據在各自的分頁；
                以前這裡的兩張估值卡、三張 StatCard、同業摘要卡講的都是其中一部分，而且互相重複 */}
            <StockConclusions ticker={ticker} tabs={tabsInfo} price={livePrice?.price ?? latest.current_price} />

            {/* Key Metrics Summary */}
            <KeyMetricsSummary ticker={ticker} />

            {/* Analyst revisions — replaces the old price-vs-fair-value history chart,
                which only restated what the valuation cards above already show. */}
            <AnalystRevisionPanel key={ticker} ticker={ticker} />

            {/* Latest News (same source as Stock Digest — Google News RSS) */}
            <StockNewsPanel ticker={ticker} company={latest.company_name} />
        </div>
    );
}
