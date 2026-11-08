import { useState, useEffect } from 'react';
import axios from '../lib/apiClient';
import { Newspaper, ExternalLink, RefreshCw } from 'lucide-react';

/** Relative "x hours/days ago" from an ISO timestamp */
function timeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const diff = Date.now() - then;
    const m = Math.round(diff / 60000);
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.round(h / 24);
    return `${d}d ago`;
}

/**
 * 個股新聞 — 來源與 Stock Digest 的 horizon 流程相同（Google News RSS）。
 * 後端 /api/stocks/{ticker}/news 即時抓取並回傳。
 */
export default function StockNewsPanel({ ticker, company }) {
    const [items, setItems]   = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError]   = useState(null);

    const load = () => {
        if (!ticker) return;
        setLoading(true);
        setError(null);
        const params = company ? { company } : {};
        axios.get(`/api/stocks/${ticker}/news`, { params })
            .then(res => { setItems(res.data.items ?? []); setLoading(false); })
            .catch(err => { setError(err?.response?.data?.detail ?? err.message); setLoading(false); });
    };

    useEffect(() => { load(); }, [ticker]);

    return (
        <div id="news" className="card !p-6 scroll-mt-20">
            <div className="flex items-center justify-between mb-5">
                <h3 className="text-base font-semibold text-[var(--text-secondary)] flex items-center gap-2">
                    <Newspaper size={16} className="text-[var(--accent-amber)]" />
                    Latest News
                    <span className="text-xs font-normal text-[var(--text-muted)]">· Google News</span>
                </h3>
                <button
                    onClick={load}
                    disabled={loading}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--text-secondary)] transition-colors disabled:opacity-50"
                >
                    <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
                </button>
            </div>

            {loading ? (
                <div className="py-10 flex justify-center">
                    <div className="w-5 h-5 border-2 border-[var(--accent-amber)] border-t-transparent rounded-full animate-spin" />
                </div>
            ) : error ? (
                <p className="text-sm text-[var(--accent-danger)] py-4">無法載入新聞：{error}</p>
            ) : items.length === 0 ? (
                <p className="text-sm text-[var(--text-muted)] py-4">目前沒有 {ticker} 的相關新聞。</p>
            ) : (
                <ul className="divide-y divide-[var(--glass-border)]">
                    {items.map((n, i) => (
                        <li key={i}>
                            <a
                                href={n.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="group flex items-start gap-3 py-3 hover:bg-white/[0.02] -mx-2 px-2 rounded-lg transition-colors"
                            >
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm text-[var(--text-primary)] leading-snug group-hover:text-[var(--accent-amber)] transition-colors">
                                        {n.title}
                                    </p>
                                    <div className="flex items-center gap-2 mt-1 text-xs text-[var(--text-muted)]">
                                        {n.source && <span className="font-medium">{n.source}</span>}
                                        {n.source && n.published_at && <span>·</span>}
                                        {n.published_at && <span>{timeAgo(n.published_at)}</span>}
                                    </div>
                                </div>
                                <ExternalLink size={13} className="shrink-0 mt-1 text-[var(--text-muted)] opacity-0 group-hover:opacity-100 transition-opacity" />
                            </a>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
