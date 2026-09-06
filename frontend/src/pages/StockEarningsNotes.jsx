import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import { ArrowLeft, Loader2, FileText, Paperclip, CalendarDays, ExternalLink, Link2 } from 'lucide-react';
import Markdown from '../components/Markdown';

function reactionColor(text) {
    if (!text) return 'var(--text-muted)';
    if (/[-−]\s*\d/.test(text) || text.includes('下跌')) return 'var(--accent-danger)';
    if (/\+\s*\d/.test(text) || text.includes('上漲')) return 'var(--accent-success)';
    return 'var(--text-secondary)';
}

export default function StockEarningsNotes() {
    const [, params] = useRoute('/stock/:ticker/earnings-notes');
    const ticker = params?.ticker;

    const [quarters, setQuarters] = useState([]);
    const [active, setActive]     = useState(null);
    const [note, setNote]         = useState(null);
    const [loading, setLoading]   = useState(true);
    const [error, setError]       = useState('');

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/earnings-notes/${ticker}`)
            .then(({ data }) => {
                setQuarters(data.quarters || []);
                setActive(data.quarters?.[0]?.quarter ?? null);
                if (!data.quarters?.length) setError('這檔還沒有財報筆記');
            })
            .catch(() => setError('讀取財報筆記失敗'))
            .finally(() => setLoading(false));
    }, [ticker]);

    useEffect(() => {
        if (!ticker || !active) return;
        setNote(null);
        axios.get(`/api/earnings-notes/${ticker}/${active}`)
            .then(({ data }) => setNote(data))
            .catch(() => setError('讀取筆記內容失敗'));
    }, [ticker, active]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-24 text-[var(--text-muted)]">
                <Loader2 className="animate-spin mr-2" size={18} /> 載入財報筆記…
            </div>
        );
    }

    return (
        <div className="max-w-4xl mx-auto px-4 py-6">
            <Link href={`/stock/${ticker}`}
                  className="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--accent-cyan)] mb-4">
                <ArrowLeft size={15} /> 回 {ticker}
            </Link>

            <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2 mb-1">
                <FileText size={20} /> {ticker} 財報筆記
            </h1>
            {quarters[0]?.industry && (
                <div className="text-[13px] text-[var(--text-muted)] mb-4">{quarters[0].industry}</div>
            )}

            {error && !quarters.length && (
                <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-6 text-center text-[var(--text-muted)]">
                    {error}
                    <div className="text-xs mt-2">財報公布隔日會由 /earnings-note 自動生成</div>
                </div>
            )}

            {quarters.length > 0 && (
                <div className="flex gap-2 flex-wrap mb-5">
                    {quarters.map((q) => {
                        const on = q.quarter === active;
                        return (
                            <button
                                key={q.quarter}
                                onClick={() => setActive(q.quarter)}
                                className={`px-3 py-1.5 rounded-full text-[13px] font-medium border transition-colors ${
                                    on
                                        ? 'text-[var(--accent-cyan)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]'
                                        : 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)] hover:border-[rgba(0,82,255,0.35)]'
                                }`}
                            >
                                {q.quarter.replace('_', ' ')}
                            </button>
                        );
                    })}
                </div>
            )}

            {note && (
                <article className="rounded-2xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-6">
                    <header className="mb-4 pb-4 border-b border-[var(--glass-border)]">
                        <h2 className="text-lg font-bold text-[var(--text-primary)]">{note.title}</h2>
                        <div className="flex items-center gap-4 mt-2 text-[13px] text-[var(--text-muted)] flex-wrap">
                            {note.report_date && (
                                <span className="inline-flex items-center gap-1">
                                    <CalendarDays size={13} /> {note.report_date}
                                </span>
                            )}
                            {note.price_after_earnings && (
                                <span style={{ color: reactionColor(note.price_after_earnings) }}>
                                    {note.price_after_earnings}
                                </span>
                            )}
                            <span className="inline-flex items-center gap-1">
                                <Paperclip size={13} /> {note.asset_count} 個附件
                            </span>
                        </div>

                        {note.links?.length > 0 && (
                            <div className="mt-3">
                                <div className="text-[12px] text-[var(--text-muted)] mb-1.5 inline-flex items-center gap-1">
                                    <Link2 size={12} /> 原始出處
                                </div>
                                <div className="flex flex-wrap gap-1.5">
                                    {note.links.map((l) => (
                                        <a
                                            key={l.key}
                                            href={l.url}
                                            target="_blank"
                                            rel="noreferrer"
                                            title={l.url}
                                            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[12px] border transition-colors ${
                                                l.kind === 'blog'
                                                    ? 'text-[var(--accent-amber)] border-[rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.08)] hover:bg-[rgba(245,158,11,0.14)]'
                                                    : 'text-[var(--text-secondary)] border-[var(--glass-border)] bg-[var(--bg-primary)] hover:text-[var(--accent-cyan)] hover:border-[rgba(0,82,255,0.35)]'
                                            }`}
                                        >
                                            {l.label}<ExternalLink size={11} />
                                        </a>
                                    ))}
                                </div>
                            </div>
                        )}
                    </header>
                    <div className="earnings-note-body">
                        <Markdown>{note.markdown}</Markdown>
                    </div>
                </article>
            )}

            {active && !note && !error && (
                <div className="flex items-center justify-center py-16 text-[var(--text-muted)]">
                    <Loader2 className="animate-spin mr-2" size={16} /> 載入內容…
                </div>
            )}
        </div>
    );
}
