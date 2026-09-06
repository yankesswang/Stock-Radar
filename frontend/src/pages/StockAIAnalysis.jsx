import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    ArrowLeft, Loader2, RefreshCw, Sparkles, TrendingUp, TrendingDown,
    Target, ShieldAlert, Zap, AlertTriangle,
} from 'lucide-react';

const GREEN = 'var(--accent-success)';
const RED   = 'var(--accent-danger)';
const AMBER = 'var(--accent-amber)';
const CYAN  = 'var(--accent-cyan)';

// 評級 / 動作 → 顏色
const RATING_COLOR = {
    '強烈看多': GREEN, '偏多': GREEN, '中性': CYAN, '偏空': RED, '強烈看空': RED,
};
const ACTION_COLOR = {
    '分批買入': GREEN, '買入': GREEN, '持有': CYAN, '觀望': 'var(--text-muted)', '減碼': AMBER, '賣出': RED,
};

function scoreColor(score) {
    if (score == null) return 'var(--text-muted)';
    if (score >= 70) return GREEN;
    if (score >= 55) return '#6ee7b7';
    if (score >= 45) return CYAN;
    if (score >= 30) return AMBER;
    return RED;
}

// 把 analysis markdown 粗略渲染成段落（標題 / 條列 / 文字）
function renderMarkdown(md) {
    if (!md) return null;
    return md.split('\n').map((line, i) => {
        const h = line.match(/^(#{1,4})\s+(.*)/);
        if (h) return <h4 key={i} className="text-sm font-bold text-[var(--text-primary)] mt-4 mb-1">{h[2]}</h4>;
        const li = line.match(/^\s*[-*]\s+(.*)/);
        if (li) return <li key={i} className="text-sm text-[var(--text-secondary)] ml-5 list-disc leading-relaxed">{renderInline(li[1])}</li>;
        if (!line.trim()) return null;
        return <p key={i} className="text-sm text-[var(--text-secondary)] leading-relaxed mb-1">{renderInline(line)}</p>;
    });
}
// **粗體** 與 [文字](網址) —— 條列末端的來源標註就是靠這裡變成可點連結
function renderInline(text) {
    const parts = String(text).split(/(\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g);
    return parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) {
            return <strong key={i} className="text-[var(--text-primary)] font-semibold">{p.slice(2, -2)}</strong>;
        }
        const link = p.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
        if (link) {
            return (
                <a key={i} href={link[2]} target="_blank" rel="noreferrer"
                   className="text-[var(--accent-cyan)] hover:opacity-70 underline decoration-dotted">
                    {link[1]}
                </a>
            );
        }
        return p;
    });
}

export default function StockAIAnalysis() {
    const [, params] = useRoute('/stock/:ticker/ai');
    const ticker = params?.ticker;

    const [card, setCard]       = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError]     = useState('');

    const load = (refresh = false) => {
        if (!ticker) return;
        refresh ? setRefreshing(true) : setLoading(true);
        setError('');
        axios.get(`/api/stocks/${ticker}/ai-analysis`, { params: refresh ? { refresh: 1 } : {}, timeout: 320000 })
            .then(res => setCard(res.data))
            .catch(err => setError(err.response?.data?.detail || '產生 AI 分析失敗，請稍後重試。'))
            .finally(() => { setLoading(false); setRefreshing(false); });
    };

    useEffect(() => { load(false); }, [ticker]);

    if (loading) return (
        <div className="container py-20 flex flex-col items-center gap-3">
            <Loader2 className="w-7 h-7 animate-spin text-[var(--accent-purple)]" />
            <p className="text-sm text-[var(--text-muted)]">AI 正在分析 {ticker}…（首次產生約需 20–90 秒）</p>
        </div>
    );

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            {/* Back + refresh */}
            <div className="flex items-center justify-between">
                <Link href={`/stock/${ticker}`}
                    className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
                    <ArrowLeft size={14} /> Back to {ticker}
                </Link>
                <button
                    onClick={() => load(true)}
                    disabled={refreshing}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--text-secondary)] transition-colors disabled:opacity-50"
                >
                    <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
                    {refreshing ? '重新分析中…' : '重新分析'}
                </button>
            </div>

            {/* Header */}
            <div className="flex items-end justify-between pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-2">
                        <Sparkles size={22} className="text-[var(--accent-purple)]" />
                        <span className="font-mono text-[var(--accent-cyan)]">{ticker}</span>
                        <span className="text-[var(--text-secondary)] font-normal text-xl ml-1">AI 決策分析</span>
                    </h1>
                    {card && (
                        <p className="text-xs text-[var(--text-muted)] mt-1">
                            {card.company_name} · 產生於 {card.generated_at?.slice(0, 16).replace('T', ' ')}
                            {card.cached && <span className="ml-2 text-[var(--accent-amber)]">（當日快取）</span>}
                        </p>
                    )}
                </div>
            </div>

            {error && (
                <div className="card !p-6 border border-[rgba(248,113,113,0.2)] bg-[rgba(248,113,113,0.04)] flex items-start gap-3">
                    <AlertTriangle size={18} className="text-[var(--accent-danger)] mt-0.5" />
                    <div>
                        <p className="text-sm text-[var(--text-primary)] font-medium">{error}</p>
                        <button onClick={() => load(true)} className="text-xs text-[var(--accent-cyan)] mt-2 hover:underline">重試</button>
                    </div>
                </div>
            )}

            {card && !error && (
                <>
                    {/* 決策摘要卡 */}
                    <div className="card !p-6">
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-5 items-center">
                            {/* 評分 */}
                            <div className="text-center md:border-r border-[var(--glass-border)]">
                                <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">綜合評分</p>
                                <p className="text-5xl font-extrabold font-mono tabular-nums leading-none"
                                    style={{ color: scoreColor(card.score) }}>
                                    {card.score ?? '—'}
                                </p>
                                <p className="text-[10px] text-[var(--text-muted)] mt-1">/ 100</p>
                            </div>
                            {/* 評級 + 動作 */}
                            <div className="text-center space-y-2">
                                <div>
                                    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">多空評級</p>
                                    <span className="inline-block px-3 py-1 rounded-full text-sm font-bold"
                                        style={{ color: RATING_COLOR[card.rating] || CYAN, background: 'var(--bg-tertiary)' }}>
                                        {card.rating || '—'}
                                    </span>
                                </div>
                                <div>
                                    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">操作建議</p>
                                    <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm font-bold"
                                        style={{ color: ACTION_COLOR[card.action] || CYAN, background: 'var(--bg-tertiary)' }}>
                                        {(ACTION_COLOR[card.action] === GREEN) ? <TrendingUp size={13} /> : (ACTION_COLOR[card.action] === RED) ? <TrendingDown size={13} /> : null}
                                        {card.action || '—'}
                                    </span>
                                </div>
                            </div>
                            {/* 核心結論 */}
                            <div className="md:col-span-2 md:border-l border-[var(--glass-border)] md:pl-5">
                                <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">核心結論</p>
                                <p className="text-base font-semibold text-[var(--text-primary)] leading-snug">{card.summary || '—'}</p>
                                <p className="text-xs text-[var(--text-secondary)] mt-2">{card.trend}</p>
                            </div>
                        </div>
                    </div>

                    {/* 買賣點位 */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <PriceCard label="建議進場區" value={card.buy_zone} color={GREEN} icon={<Target size={14} />} />
                        <PriceCard label="目標價" value={card.target} color={CYAN} icon={<TrendingUp size={14} />} />
                        <PriceCard label="停損參考" value={card.stop_loss} color={RED} icon={<TrendingDown size={14} />} />
                    </div>

                    {/* 風險 + 催化 */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <ListCard title="風險警報" items={card.risks} color={RED} icon={<ShieldAlert size={15} />} />
                        <ListCard title="催化因素" items={card.catalysts} color={GREEN} icon={<Zap size={15} />} />
                    </div>

                    {/* 完整分析 */}
                    <div className="card !p-6">
                        <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-3 uppercase tracking-wider">完整分析</h3>
                        <div className="space-y-0.5">{renderMarkdown(card.analysis)}</div>
                    </div>

                    <p className="text-[11px] text-[var(--text-muted)] text-center">
                        ⚠️ 由 claude 依 stock-radar 即時資料產生，僅供參考，非投資建議。
                    </p>
                </>
            )}
        </div>
    );
}

function PriceCard({ label, value, color, icon }) {
    return (
        <div className="card !p-4">
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1 flex items-center gap-1.5"
                style={{ color }}>{icon} {label}</p>
            <p className="text-2xl font-extrabold font-mono tabular-nums" style={{ color }}>{value || '—'}</p>
        </div>
    );
}

function ListCard({ title, items, color, icon }) {
    return (
        <div className="card !p-5">
            <h3 className="text-sm font-semibold mb-3 flex items-center gap-1.5" style={{ color }}>{icon} {title}</h3>
            {items?.length ? (
                <ul className="space-y-2">
                    {items.map((it, i) => (
                        <li key={i} className="text-sm text-[var(--text-secondary)] leading-relaxed flex gap-2">
                            <span style={{ color }}>•</span><span>{it}</span>
                        </li>
                    ))}
                </ul>
            ) : <p className="text-sm text-[var(--text-muted)]">—</p>}
        </div>
    );
}
