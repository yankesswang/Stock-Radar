import { useState, useEffect, useRef } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import { Sparkles, Loader2, RefreshCw, AlertTriangle, ExternalLink } from 'lucide-react';

const GREEN = 'var(--accent-success)';
const RED   = 'var(--accent-danger)';
const AMBER = 'var(--accent-amber)';
const CYAN  = 'var(--accent-cyan)';

const RATING_COLOR = { '強烈看多': GREEN, '偏多': GREEN, '中性': CYAN, '偏空': RED, '強烈看空': RED };
const ACTION_COLOR = { '分批買入': GREEN, '買入': GREEN, '持有': CYAN, '觀望': 'var(--text-muted)', '減碼': AMBER, '賣出': RED };

function scoreColor(s) {
    if (s == null) return 'var(--text-muted)';
    if (s >= 70) return GREEN;
    if (s >= 55) return '#6ee7b7';
    if (s >= 45) return CYAN;
    if (s >= 30) return AMBER;
    return RED;
}

export default function AIPortfolioReport() {
    const [dates, setDates]   = useState([]);
    const [date, setDate]     = useState(null);
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError]   = useState('');
    const [genStatus, setGenStatus] = useState('idle'); // idle | running
    const pollRef = useRef(null);

    // 第一次：抓日期清單，載入最新
    useEffect(() => {
        axios.get('/api/ai-report')
            .then(res => {
                const ds = res.data.dates || [];
                setDates(ds);
                if (ds.length) { setDate(ds[0]); }
                else { setLoading(false); }
            })
            .catch(() => { setError('無法讀取日報清單'); setLoading(false); });
    }, []);

    // 切換日期 → 載入該份
    useEffect(() => {
        if (!date) return;
        setLoading(true);
        axios.get(`/api/ai-report/${date}`)
            .then(res => { setReport(res.data); setError(''); })
            .catch(() => setError(`無法載入 ${date} 的日報`))
            .finally(() => setLoading(false));
    }, [date]);

    const regenerate = async () => {
        setGenStatus('running');
        setError('');
        try {
            await axios.post('/api/scheduler/ai-report/run');
            // 輪詢 scheduler 狀態，跑完後重新載入
            pollRef.current = setInterval(async () => {
                try {
                    const { data } = await axios.get('/api/scheduler/status');
                    if (data.ai_report?.status !== 'running') {
                        clearInterval(pollRef.current);
                        setGenStatus('idle');
                        const ds = (await axios.get('/api/ai-report')).data.dates || [];
                        setDates(ds);
                        const today = ds[0];
                        if (today) {
                            if (today === date) {
                                const r = await axios.get(`/api/ai-report/${today}`);
                                setReport(r.data);
                            } else { setDate(today); }
                        }
                    }
                } catch { clearInterval(pollRef.current); setGenStatus('idle'); }
            }, 5000);
        } catch (e) {
            setGenStatus('idle');
            setError(e.response?.data?.detail || '觸發失敗');
        }
    };
    useEffect(() => () => clearInterval(pollRef.current), []);

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-2">
                        <Sparkles size={22} className="text-[var(--accent-purple)]" />
                        AI 持倉決策日報
                    </h1>
                    <p className="text-xs text-[var(--text-muted)] mt-1">
                        收盤分析前（台北 5:00）自動預產的當日持倉 AI 決策卡，供 5:30 收盤分析參考 · 非獨立推播
                        {report && <span className="ml-2">· 產生於 {report.generated_at?.slice(0, 16).replace('T', ' ')}</span>}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {dates.length > 0 && (
                        <select
                            value={date || ''}
                            onChange={e => setDate(e.target.value)}
                            className="px-3 py-1.5 rounded-lg text-sm bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-[var(--text-secondary)]"
                        >
                            {dates.map(d => <option key={d} value={d}>{d}</option>)}
                        </select>
                    )}
                    <button
                        onClick={regenerate}
                        disabled={genStatus === 'running'}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--text-secondary)] transition-colors disabled:opacity-50"
                    >
                        <RefreshCw size={13} className={genStatus === 'running' ? 'animate-spin' : ''} />
                        {genStatus === 'running' ? '產生中…（數分鐘）' : '立即產生今日'}
                    </button>
                </div>
            </div>

            {error && (
                <div className="card !p-4 border border-[rgba(248,113,113,0.2)] bg-[rgba(248,113,113,0.04)] flex items-center gap-2">
                    <AlertTriangle size={16} className="text-[var(--accent-danger)]" />
                    <p className="text-sm text-[var(--text-primary)]">{error}</p>
                </div>
            )}

            {loading && (
                <div className="py-20 flex justify-center">
                    <Loader2 className="w-6 h-6 animate-spin text-[var(--accent-purple)]" />
                </div>
            )}

            {!loading && !report && !error && (
                <div className="card !p-10 text-center space-y-3">
                    <p className="text-sm text-[var(--text-muted)]">尚無 AI 持倉日報。</p>
                    <p className="text-xs text-[var(--text-muted)]">點「立即產生今日」會對所有持倉批次跑 AI 分析（依持倉數量約數分鐘）。</p>
                </div>
            )}

            {!loading && report && (
                <>
                    {/* 摘要表 */}
                    <div className="card !p-0 overflow-hidden">
                        <div className="px-5 py-3 border-b border-[var(--glass-border)] flex items-center justify-between">
                            <h3 className="text-sm font-semibold text-[var(--text-secondary)]">
                                持倉決策摘要（{report.ok}/{report.total} 檔）
                            </h3>
                            {report.errors?.length > 0 && (
                                <span className="text-xs text-[var(--accent-amber)]">{report.errors.length} 檔失敗：{report.errors.join(', ')}</span>
                            )}
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)] border-b border-[var(--glass-border)]">
                                        <th className="px-4 py-2">持倉</th>
                                        <th className="px-4 py-2">權重</th>
                                        <th className="px-4 py-2 text-center">評分</th>
                                        <th className="px-4 py-2">評級</th>
                                        <th className="px-4 py-2">建議</th>
                                        <th className="px-4 py-2">核心結論</th>
                                        <th className="px-4 py-2"></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {report.cards.map(c => {
                                        const h = report.holdings.find(x => x.ticker === c.ticker) || {};
                                        return (
                                            <tr key={c.ticker} className="border-b border-[var(--glass-border)] hover:bg-white/[0.02]">
                                                <td className="px-4 py-2.5 font-mono font-bold text-[var(--accent-cyan)]">{c.ticker}</td>
                                                <td className="px-4 py-2.5 text-[var(--text-muted)]">{h.weight_pct != null ? `${h.weight_pct.toFixed(1)}%` : '—'}</td>
                                                <td className="px-4 py-2.5 text-center font-mono font-bold" style={{ color: scoreColor(c.score) }}>{c.score ?? '—'}</td>
                                                <td className="px-4 py-2.5 font-semibold" style={{ color: RATING_COLOR[c.rating] || CYAN }}>{c.rating || '—'}</td>
                                                <td className="px-4 py-2.5 font-semibold" style={{ color: ACTION_COLOR[c.action] || CYAN }}>{c.action || '—'}</td>
                                                <td className="px-4 py-2.5 text-[var(--text-secondary)] max-w-[320px]">{c.summary || '—'}</td>
                                                <td className="px-4 py-2.5">
                                                    <Link href={`/stock/${c.ticker}/ai`} className="text-[var(--accent-purple)] hover:opacity-70">
                                                        <ExternalLink size={14} />
                                                    </Link>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* 逐檔卡片 */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {report.cards.map(c => (
                            <div key={c.ticker} className="card !p-5 space-y-3">
                                <div className="flex items-start justify-between">
                                    <div>
                                        <Link href={`/stock/${c.ticker}/ai`} className="font-mono font-bold text-lg text-[var(--accent-cyan)] hover:opacity-70">{c.ticker}</Link>
                                        <span className="text-xs text-[var(--text-muted)] ml-2">{c.company_name}</span>
                                    </div>
                                    <div className="text-right">
                                        <span className="text-3xl font-extrabold font-mono" style={{ color: scoreColor(c.score) }}>{c.score ?? '—'}</span>
                                        <span className="text-[10px] text-[var(--text-muted)]">/100</span>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 text-xs">
                                    <span className="px-2 py-0.5 rounded-full font-semibold" style={{ color: RATING_COLOR[c.rating] || CYAN, background: 'var(--bg-tertiary)' }}>{c.rating || '—'}</span>
                                    <span className="px-2 py-0.5 rounded-full font-semibold" style={{ color: ACTION_COLOR[c.action] || CYAN, background: 'var(--bg-tertiary)' }}>{c.action || '—'}</span>
                                </div>
                                <p className="text-sm text-[var(--text-primary)] font-medium">{c.summary}</p>
                                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
                                    <span>進場 <b style={{ color: GREEN }}>{c.buy_zone || '—'}</b></span>
                                    <span>目標 <b style={{ color: CYAN }}>{c.target || '—'}</b></span>
                                    <span>停損 <b style={{ color: RED }}>{c.stop_loss || '—'}</b></span>
                                </div>
                                {c.risks?.length > 0 && (
                                    <p className="text-xs text-[var(--text-muted)]"><span style={{ color: RED }}>⚠️ 風險：</span>{c.risks.join('；')}</p>
                                )}
                                {c.catalysts?.length > 0 && (
                                    <p className="text-xs text-[var(--text-muted)]"><span style={{ color: GREEN }}>⚡ 催化：</span>{c.catalysts.join('；')}</p>
                                )}
                            </div>
                        ))}
                    </div>

                    <p className="text-[11px] text-[var(--text-muted)] text-center">
                        ⚠️ 由 claude 依 stock-radar 即時資料產生，僅供參考，非投資建議。點 ticker 可看完整單檔分析。
                    </p>
                </>
            )}
        </div>
    );
}
