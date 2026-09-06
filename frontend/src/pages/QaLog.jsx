import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
    MessageSquare, Search, Star, ChevronRight, Filter, X, Clock,
    ExternalLink, AlertTriangle, Trash2, User, Bot,
} from 'lucide-react';
import Markdown from '../components/Markdown';

// 統一問答紀錄 —— 個股頁問過的每一題都落在這裡（後端 chat_sessions / chat_messages）。
//
// 一列 = 一段對話，不是一個問題：追問（「那停損呢」）離開它的上下文根本讀不懂，
// 拆開列反而找不回當時在想什麼。卡片預設收起、標題是那段對話的第一個問題，
// 展開才看完整往返 —— 作法沿用 FlightClaw AI 助理的對話紀錄。
//
// 篩選全部走後端（整段對話命中就進來），前端不做二次過濾，免得分頁後只篩到當頁。

const API = '/api/stock-chat';
const PAGE = 20;
const MARKET = 'MARKET';
// 大盤那條線在 DB 裡的 scope 就叫 MARKET，畫面上一律顯示「大盤」
const scopeName = (t) => (t === MARKET ? '大盤' : t);

export default function QaLog() {
    const [items, setItems]     = useState([]);
    const [total, setTotal]     = useState(0);
    const [offset, setOffset]   = useState(0);
    const [loading, setLoading] = useState(true);
    const [facets, setFacets]   = useState(null);
    const [reload, setReload]   = useState(0);

    const [search, setSearch]   = useState('');
    const [query, setQuery]     = useState('');
    const [ticker, setTicker]   = useState('');
    const [topic, setTopic]     = useState('');
    const [starred, setStarred] = useState(false);
    const [openIds, setOpenIds] = useState(() => new Set());

    useEffect(() => {
        const t = setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 350);
        return () => clearTimeout(t);
    }, [search]);

    useEffect(() => { setOffset(0); }, [ticker, topic, starred]);

    useEffect(() => {
        setLoading(true);
        const params = { limit: PAGE, offset };
        if (query) params.q = query;
        if (ticker) params.ticker = ticker;
        if (topic) params.topic = topic;
        if (starred) params.starred = true;
        axios.get(`${API}/log`, { params })
            .then(r => {
                const list = r.data.items || [];
                setItems(list);
                setTotal(r.data.total || 0);
                // 最新那段預設展開（多半就是剛剛問完那段），其餘收起
                setOpenIds(new Set(list.length ? [list[0].id] : []));
            })
            .catch(() => { setItems([]); setTotal(0); })
            .finally(() => setLoading(false));
    }, [query, ticker, topic, starred, offset, reload]);

    useEffect(() => {
        axios.get(`${API}/log/facets`).then(r => setFacets(r.data)).catch(() => {});
    }, [reload, items.length]);

    const toggle = (id) => setOpenIds(prev => {
        const next = new Set(prev);
        next.has(id) ? next.delete(id) : next.add(id);
        return next;
    });

    const starMessage = async (sessionId, mid, next) => {
        setItems(list => list.map(s => (s.id !== sessionId ? s : {
            ...s,
            starred: next || s.messages.some(m => m.id !== mid && m.starred),
            messages: s.messages.map(m => (m.id === mid ? { ...m, starred: next } : m)),
        })));
        await axios.patch(`${API}/messages/${mid}`, { starred: next }).catch(() => {});
    };

    const removeSession = async (sid, e) => {
        e.stopPropagation();
        if (!window.confirm('刪掉整段對話？')) return;
        await axios.delete(`${API}/sessions/${sid}`).catch(() => {});
        setReload(n => n + 1);
    };

    const hasFilter = query || ticker || topic || starred;
    const pages = Math.ceil(total / PAGE);
    const page = Math.floor(offset / PAGE) + 1;

    return (
        <div className="container py-10 space-y-6 animate-fade-in">
            <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-6 border-b border-[var(--glass-border)]">
                <div>
                    <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-2.5">
                        <MessageSquare size={26} className="text-[var(--accent-cyan)]" /> 問答紀錄
                    </h1>
                    <p className="text-sm text-[var(--text-secondary)] mt-1.5">
                        個股頁問過的每一段對話 —— 問題、當時的回答、踩了哪些資料。點標題展開。
                    </p>
                </div>
                {facets && (
                    <div className="flex items-center gap-5 text-sm">
                        <Stat label="對話" value={facets.total_sessions ?? 0} />
                        <Stat label="問題" value={facets.total_questions} />
                        <Stat label="標記" value={facets.starred} />
                        <Stat label="個股" value={facets.by_ticker?.length ?? 0} />
                    </div>
                )}
            </header>

            {/* 篩選 */}
            <div className="space-y-3">
                <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
                        <input
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            placeholder="搜尋對話內容（問題或回答）…"
                            className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm bg-[var(--bg-secondary)] border border-[var(--glass-border)] outline-none focus:border-[rgba(0,82,255,0.45)]"
                        />
                    </div>
                    <button
                        onClick={() => setStarred(v => !v)}
                        className={`px-3 py-2.5 rounded-xl text-sm font-medium border transition-colors flex items-center gap-1.5 ${
                            starred
                                ? 'bg-[rgba(251,191,36,0.12)] border-[rgba(251,191,36,0.4)] text-[var(--accent-amber)]'
                                : 'bg-[var(--bg-secondary)] border-[var(--glass-border)] text-[var(--text-secondary)]'}`}
                    >
                        <Star size={14} fill={starred ? 'currentColor' : 'none'} /> 只看標記
                    </button>
                    {hasFilter && (
                        <button
                            onClick={() => { setSearch(''); setTicker(''); setTopic(''); setStarred(false); }}
                            className="px-3 py-2.5 rounded-xl text-sm bg-[var(--bg-secondary)] border border-[var(--glass-border)] text-[var(--text-muted)] flex items-center gap-1.5"
                        >
                            <X size={14} /> 清除
                        </button>
                    )}
                </div>

                {facets && (
                    <div className="flex flex-wrap items-center gap-1.5">
                        <Filter size={13} className="text-[var(--text-muted)]" />
                        {(facets.by_topic || []).map(t => (
                            <Chip key={t.topic} active={topic === t.topic}
                                onClick={() => setTopic(topic === t.topic ? '' : t.topic)}>
                                {t.topic} <span className="opacity-60">{t.count}</span>
                            </Chip>
                        ))}
                        {(facets.by_ticker || []).length > 0 && (
                            <span className="w-px h-4 bg-[var(--glass-border)] mx-1" />
                        )}
                        {(facets.by_ticker || []).slice(0, 14).map(t => (
                            <Chip key={t.ticker} active={ticker === t.ticker}
                                onClick={() => setTicker(ticker === t.ticker ? '' : t.ticker)}>
                                {scopeName(t.ticker)} <span className="opacity-60">{t.count}</span>
                            </Chip>
                        ))}
                    </div>
                )}
            </div>

            {loading ? (
                <div className="py-20 flex justify-center">
                    <div className="w-6 h-6 border-2 border-[#0052ff] border-t-transparent rounded-full animate-spin" />
                </div>
            ) : items.length === 0 ? (
                <div className="card text-center py-16 space-y-3">
                    <p className="text-[var(--text-secondary)]">
                        {hasFilter ? '沒有符合條件的對話。' : '還沒有任何問答。'}
                    </p>
                    {!hasFilter && (
                        <p className="text-sm text-[var(--text-muted)]">
                            到任一個股頁，用右下角的「問 XXXX」開始第一段對話。
                        </p>
                    )}
                </div>
            ) : (
                <div className="space-y-2">
                    {items.map(s => (
                        <SessionCard key={s.id} session={s} open={openIds.has(s.id)}
                            onToggle={() => toggle(s.id)} onStar={starMessage}
                            onDelete={removeSession} />
                    ))}
                </div>
            )}

            {pages > 1 && (
                <div className="flex items-center justify-center gap-3 text-sm">
                    <button disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - PAGE))}
                        className="px-3 py-1.5 rounded-lg bg-[var(--bg-secondary)] border border-[var(--glass-border)] disabled:opacity-40">
                        上一頁
                    </button>
                    <span className="text-[var(--text-muted)]">{page} / {pages}</span>
                    <button disabled={page >= pages} onClick={() => setOffset(o => o + PAGE)}
                        className="px-3 py-1.5 rounded-lg bg-[var(--bg-secondary)] border border-[var(--glass-border)] disabled:opacity-40">
                        下一頁
                    </button>
                </div>
            )}
        </div>
    );
}

function Stat({ label, value }) {
    return (
        <div className="text-right">
            <div className="text-xl font-bold font-mono tabular-nums leading-none">{value}</div>
            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider mt-1">{label}</div>
        </div>
    );
}

function Chip({ children, active, onClick }) {
    return (
        <button onClick={onClick}
            className={`px-2.5 py-1 rounded-full text-[12px] font-medium border transition-colors ${
                active
                    ? 'bg-[rgba(0,82,255,0.12)] border-[rgba(0,82,255,0.35)] text-[var(--accent-cyan)]'
                    : 'bg-[var(--bg-secondary)] border-[var(--glass-border)] text-[var(--text-secondary)] hover:text-[var(--accent-cyan)]'}`}>
            {children}
        </button>
    );
}

const day = (iso) => (iso || '').slice(0, 10);
const time = (iso) => (iso || '').slice(11, 16);

function SessionCard({ session: s, open, onToggle, onStar, onDelete }) {
    return (
        <div className="card !p-0 overflow-hidden">
            <button onClick={onToggle}
                className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-white/[0.03] transition-colors">
                <ChevronRight size={15}
                    className={`shrink-0 text-[var(--text-muted)] transition-transform ${open ? 'rotate-90' : ''}`} />
                <span className="shrink-0 px-2 py-0.5 rounded-md text-[11px] font-bold font-mono bg-[rgba(0,82,255,0.1)] text-[var(--accent-cyan)] border border-[rgba(0,82,255,0.2)]">
                    {/* 大盤對話裡點名過的股票也標出來 —— 「NVDA 跟 AMD 誰便宜」開在大盤範圍，
                        但要能一眼看出那段在談哪幾檔 */}
                    {s.ticker === MARKET && s.tickers?.length ? s.tickers.join(' · ') : scopeName(s.ticker)}
                </span>
                <span className="flex-1 min-w-0 font-semibold text-sm truncate">{s.title}</span>
                {s.starred && <Star size={13} className="shrink-0 text-[var(--accent-amber)]" fill="currentColor" />}
                {s.has_error && <AlertTriangle size={13} className="shrink-0 text-[var(--accent-danger)]" />}
                <span className="hidden sm:flex shrink-0 items-center gap-1.5">
                    {s.topics?.slice(0, 3).map(t => (
                        <span key={t} className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--bg-tertiary)] text-[var(--text-muted)]">{t}</span>
                    ))}
                </span>
                <span className="shrink-0 text-[11px] text-[var(--text-muted)] whitespace-nowrap">
                    {day(s.updated_at)} · {s.question_count} 問
                </span>
            </button>

            {open && (
                <div className="border-t border-[var(--glass-border)] px-4 py-3 space-y-3">
                    <Thread messages={s.messages} sessionId={s.id} onStar={onStar} />
                    <div className="flex items-center gap-4 pt-1 text-[11px] text-[var(--text-muted)]">
                        <Link href={s.ticker === MARKET ? '/macro' : `/stock/${s.ticker}`}
                            className="flex items-center gap-1 text-[var(--accent-cyan)] hover:underline">
                            <ExternalLink size={11} />
                            {s.ticker === MARKET ? '回大盤頁接著問' : `回 ${s.ticker} 個股頁接著問`}
                        </Link>
                        <button onClick={(e) => onDelete(s.id, e)}
                            className="flex items-center gap-1 hover:text-[var(--accent-danger)]">
                            <Trash2 size={11} /> 刪除這段對話
                        </button>
                        <span className="ml-auto">開始於 {day(s.created_at)} {time(s.created_at)}</span>
                    </div>
                </div>
            )}
        </div>
    );
}

function Thread({ messages, sessionId, onStar }) {
    let lastDay = day(messages[0]?.created_at);
    return (
        <div className="space-y-3">
            {messages.map((m, i) => {
                const d = day(m.created_at);
                // 同一段對話跨天才插分隔線 —— 一天內問完的不需要
                const sep = i > 0 && d && d !== lastDay ? d : null;
                if (sep) lastDay = d;
                return (
                    <div key={m.id} className="space-y-3">
                        {sep && (
                            <div className="flex items-center gap-3 text-[10px] text-[var(--text-muted)] tracking-widest">
                                <span className="flex-1 h-px bg-[var(--glass-border)]" />
                                {sep}
                                <span className="flex-1 h-px bg-[var(--glass-border)]" />
                            </div>
                        )}
                        <div className="flex gap-2.5">
                            <div className={`w-6 h-6 shrink-0 rounded-full flex items-center justify-center border ${
                                m.role === 'user'
                                    ? 'bg-[rgba(0,82,255,0.1)] border-[rgba(0,82,255,0.3)] text-[var(--accent-cyan)]'
                                    : 'bg-[var(--bg-tertiary)] border-[var(--glass-border)] text-[var(--text-muted)]'}`}>
                                {m.role === 'user' ? <User size={12} /> : <Bot size={12} />}
                            </div>
                            <div className="flex-1 min-w-0">
                                {m.role === 'user' ? (
                                    <p className="text-sm font-semibold text-[var(--text-primary)] leading-snug">{m.content}</p>
                                ) : m.content ? (
                                    <Markdown compact>{m.content}</Markdown>
                                ) : (
                                    <p className="text-sm text-[var(--accent-danger)]">
                                        {m.error || '（沒有回答）'}
                                    </p>
                                )}
                                <div className="flex items-center gap-2.5 mt-1 text-[10px] text-[var(--text-muted)] flex-wrap">
                                    <span className="flex items-center gap-1"><Clock size={9} />{time(m.created_at)}</span>
                                    {m.role === 'assistant' && m.sections?.length > 0 && (
                                        <span>依據：{m.sections.join('、')}</span>
                                    )}
                                    {m.duration_ms != null && <span>{(m.duration_ms / 1000).toFixed(1)}s</span>}
                                    {m.role === 'assistant' && (
                                        <button onClick={() => onStar(sessionId, m.id, !m.starred)}
                                            className={`flex items-center gap-1 hover:text-[var(--accent-amber)] ${m.starred ? 'text-[var(--accent-amber)]' : ''}`}>
                                            <Star size={10} fill={m.starred ? 'currentColor' : 'none'} />
                                            {m.starred ? '已標記' : '標記'}
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
