import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import axios from 'axios';
import {
    MessageSquare, Send, History, Plus, Star, Trash2, X, AlertTriangle,
    Database, ScrollText, Minus, Target, Globe2, Layers, ArrowRight,
} from 'lucide-react';
import Markdown from './Markdown';

// 投資問答的懸浮面板（貼右緣正中的圓鈕 + 側邊視窗），設計沿用 FlightClaw 的 AI 助理：
// 面板浮在頁面之上而不是插在版面裡，這樣「看著圖表／財報表格問問題」時兩邊都在，
// 不必為了問一句話把頁面捲走。
//
// 只掛一次（App.jsx），**範圍由路由決定**：
//   /stock/:ticker/*  → 這檔（個股的每個分頁都跟著帶，換股票自動重置對話）
//   其他頁            → 大盤 / 總經（指數、利率、板塊、市場新聞、我的組合曝險）
// 跨標的問答不必切頁：問句裡點名的代號後端會自己認出來一起撈（「NVDA 跟 AMD 誰便宜」）。
//
// 回答走 SSE（EventSource 只能 GET，所以問題走 query string）。每一問一答都存進
// 後端，統一在 /qa 依對話收合檢視 —— 這個面板不自己保存任何東西。

const API = '/api/stock-chat';

function tickerFromPath(path) {
    const m = /^\/stock\/([A-Za-z0-9.\-]{1,12})(\/|$)/.exec(path);
    return m ? m[1].toUpperCase() : null;
}

export default function StockChatDock() {
    const [location]  = useLocation();
    const ticker      = tickerFromPath(location);          // null = 大盤 / 總經
    const scopeLabel  = ticker || '大盤';

    const [open, setOpen]           = useState(false);
    const [messages, setMessages]   = useState([]);
    const [input, setInput]         = useState('');
    const [streaming, setStreaming] = useState(false);
    const [live, setLive]           = useState('');
    const [meta, setMeta]           = useState(null);
    const [error, setError]         = useState('');
    const [sessionId, setSessionId] = useState(null);
    const [sessions, setSessions]   = useState([]);
    const [showHistory, setShowHistory] = useState(false);
    const [suggestions, setSuggestions] = useState([]);
    const [dataChips, setDataChips]     = useState([]);   // 這個範圍撈得到哪些資料

    const esRef     = useRef(null);
    const bottomRef = useRef(null);
    const inputRef  = useRef(null);

    const closeStream = () => { esRef.current?.close(); esRef.current = null; };

    // 換股票 = 換脈絡。上一檔的對話帶到下一檔會問出錯的答案，所以整個重置。
    useEffect(() => {
        closeStream();
        setMessages([]); setLive(''); setMeta(null); setError('');
        setSessionId(null); setInput(''); setStreaming(false); setShowHistory(false);
        axios.get(`${API}/suggestions`, { params: ticker ? { ticker } : {} })
            .then(r => {
                setSuggestions(r.data.groups || []);
                // 只列這個範圍真的會用到的區塊：在個股頁列逐檔的，在別頁列大盤的
                const want = ticker ? 'stock' : 'market';
                setDataChips((r.data.sections || []).filter(x => x.scope === want));
            })
            .catch(() => { setSuggestions([]); setDataChips([]); });
        loadSessions();
        return closeStream;
    }, [ticker]);

    // 個股頁的「問答」鈕、或紀錄頁的「接著問」都用這個事件打開面板
    useEffect(() => {
        const openDock = () => setOpen(true);
        window.addEventListener('stockchat:open', openDock);
        return () => window.removeEventListener('stockchat:open', openDock);
    }, []);

    useEffect(() => {
        if (open) setTimeout(() => inputRef.current?.focus(), 60);
    }, [open]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, [messages.length, live, open]);

    const loadSessions = () => {
        axios.get(`${API}/sessions`, { params: ticker ? { ticker } : {} })
            .then(r => setSessions(r.data.sessions || [])).catch(() => setSessions([]));
    };

    const openSession = async (sid) => {
        try {
            const { data } = await axios.get(`${API}/sessions/${sid}`);
            setSessionId(sid);
            setMessages(data.messages || []);
            setShowHistory(false);
            setError(''); setLive(''); setMeta(null);
        } catch { setError('讀不到這條對話'); }
    };

    const deleteSession = async (sid, e) => {
        e.stopPropagation();
        await axios.delete(`${API}/sessions/${sid}`).catch(() => {});
        if (sid === sessionId) { setSessionId(null); setMessages([]); }
        loadSessions();
    };

    const newChat = () => {
        closeStream();
        setSessionId(null); setMessages([]); setLive(''); setMeta(null);
        setError(''); setStreaming(false); setShowHistory(false);
        inputRef.current?.focus();
    };

    const toggleStar = async (mid, next) => {
        setMessages(ms => ms.map(m => (m.id === mid ? { ...m, starred: next } : m)));
        await axios.patch(`${API}/messages/${mid}`, { starred: next }).catch(() => {});
    };

    const ask = (question) => {
        const q = (question ?? input).trim();
        if (!q || streaming) return;

        setInput(''); setError(''); setLive(''); setMeta(null); setStreaming(true);
        setMessages(ms => [...ms, { id: `tmp-${Date.now()}`, role: 'user', content: q }]);

        const params = new URLSearchParams({ q });
        if (ticker) params.set('ticker', ticker);
        if (sessionId) params.set('session_id', String(sessionId));
        const es = new EventSource(`${API}/stream?${params}`);
        esRef.current = es;

        let buffer = '';
        let used = [];    // meta 事件的內容留在 closure：done 讀 state 會讀到舊值
        es.addEventListener('meta', (e) => {
            const d = JSON.parse(e.data);
            used = d.used || [];
            setSessionId(d.session_id);
            setMeta(d);
        });
        es.addEventListener('delta', (e) => {
            buffer += JSON.parse(e.data).text;
            setLive(buffer);
        });
        es.addEventListener('done', (e) => {
            const d = JSON.parse(e.data);
            setMessages(ms => [...ms, {
                id: d.message_id, role: 'assistant', content: buffer,
                sections: used, duration_ms: d.duration_ms, starred: false,
            }]);
            setLive(''); setStreaming(false); closeStream(); loadSessions();
        });
        es.addEventListener('error', (e) => {
            let detail = '連線中斷';
            try { detail = JSON.parse(e.data).detail; } catch { /* 連線層錯誤沒有 data */ }
            setError(detail);
            if (buffer) setMessages(ms => [...ms, { id: `err-${Date.now()}`, role: 'assistant', content: buffer }]);
            setLive(''); setStreaming(false); closeStream();
        });
        es.onerror = () => {
            if (!esRef.current) return;      // done 之後的正常關閉
            setError(prev => prev || '連線中斷');
            setLive(''); setStreaming(false); closeStream();
        };
    };

    const onKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
    };

    return (
        <>
            {/* 貼在右緣正中的圓鈕，只有圖示 —— 它跟著你捲頁，不占版面也不搶字 */}
            {!open && (
                <button
                    onClick={() => setOpen(true)}
                    title={`問 ${scopeLabel}`}
                    aria-label={`問 ${scopeLabel}`}
                    className="ui-focus cursor-pointer fixed right-4 top-1/2 -translate-y-1/2 z-[400] w-12 h-12 rounded-full flex items-center justify-center bg-[#0052ff] text-white shadow-[0_10px_30px_rgba(0,82,255,0.4)] hover:bg-[#578bfa] hover:scale-[1.06] transition-all"
                >
                    <MessageSquare size={19} />
                </button>
            )}

            {/* 面板同樣靠右垂直置中，展開時就在圓鈕原本的位置長出來 */}
            {open && (
                <div className="fixed z-[401] flex flex-col overflow-hidden glass
                                right-0 bottom-0 w-screen h-[100dvh] rounded-none border-0
                                sm:right-4 sm:bottom-auto sm:top-1/2 sm:-translate-y-1/2
                                sm:w-[420px] sm:h-[min(78vh,680px)] sm:rounded-2xl
                                sm:border sm:border-[var(--glass-border)]
                                shadow-[0_20px_60px_rgba(10,11,13,0.35)]">
                    {/* Header */}
                    <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--glass-border)] bg-[var(--bg-secondary)]">
                        <div className="w-7 h-7 rounded-lg bg-[rgba(0,82,255,0.1)] flex items-center justify-center shrink-0">
                            <MessageSquare size={14} className="text-[var(--accent-cyan)]" />
                        </div>
                        <span className="font-semibold text-sm">問 {scopeLabel}</span>
                        <span className="text-[10px] text-[var(--text-muted)] px-1.5 py-0.5 rounded-full border border-[var(--glass-border)]">
                            {messages.length > 0 ? `第 ${Math.ceil(messages.length / 2)} 問` : '新對話'}
                        </span>
                        <div className="flex-1" />
                        <IconBtn title="新對話" onClick={newChat}><Plus size={14} /></IconBtn>
                        <IconBtn title="這檔的歷史對話" active={showHistory}
                            onClick={() => { setShowHistory(v => !v); loadSessions(); }}>
                            <History size={14} />
                        </IconBtn>
                        <Link href="/qa" title="全部問答紀錄">
                            <IconBtn title="全部問答紀錄"><ScrollText size={14} /></IconBtn>
                        </Link>
                        <IconBtn title="收起" onClick={() => setOpen(false)}>
                            <Minus size={14} className="hidden sm:block" />
                            <X size={14} className="sm:hidden" />
                        </IconBtn>
                    </div>

                    {/* 這檔的歷史對話 */}
                    {showHistory && (
                        <div className="px-4 py-3 border-b border-[var(--glass-border)] bg-[var(--bg-tertiary)]">
                            {sessions.length === 0 ? (
                                <p className="text-xs text-[var(--text-muted)]">還沒問過{ticker ? ` ${ticker}` : '大盤'}。</p>
                            ) : (
                                <div className="space-y-1 max-h-44 overflow-y-auto">
                                    {sessions.map(s => (
                                        <button key={s.id} onClick={() => openSession(s.id)}
                                            className={`ui-focus tap cursor-pointer w-full text-left px-2.5 py-2 rounded-lg text-xs flex items-center gap-2 transition-colors duration-200 ${
                                                s.id === sessionId ? 'bg-[rgba(0,82,255,0.1)]' : 'hover:bg-white/5'}`}>
                                            <span className="flex-1 truncate text-[var(--text-secondary)]">{s.title}</span>
                                            <span className="text-[10px] text-[var(--text-muted)] shrink-0">
                                                {(s.updated_at || '').slice(5, 16).replace('T', ' ')}
                                            </span>
                                            <span onClick={(e) => deleteSession(s.id, e)}
                                                className="cursor-pointer text-[var(--text-meta)] hover:text-[var(--accent-danger)] shrink-0">
                                                <Trash2 size={12} />
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* 對話 */}
                    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3.5">
                        {messages.length === 0 && !live && (
                            <EmptyState scopeLabel={scopeLabel} isStock={!!ticker}
                                groups={suggestions} chips={dataChips} onPick={ask}
                                onDraft={(t) => { setInput(t); inputRef.current?.focus(); }}
                                disabled={streaming} />
                        )}
                        {messages.map(m => <Bubble key={m.id} msg={m} onStar={toggleStar} />)}
                        {live && <Bubble msg={{ role: 'assistant', content: live }} streaming />}
                        {streaming && !live && (
                            <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                                <div className="w-3.5 h-3.5 border-2 border-[#0052ff] border-t-transparent rounded-full animate-spin" />
                                {meta?.used?.length ? `讀取 ${meta.used.join('、')}…` : '整理資料中…'}
                            </div>
                        )}
                        {error && (
                            <div className="flex items-start gap-2 text-xs text-[var(--accent-danger)] bg-[rgba(248,113,113,0.08)] rounded-lg px-3 py-2">
                                <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error}
                            </div>
                        )}
                        <div ref={bottomRef} />
                    </div>

                    {/* 這輪踩了哪些資料 —— 回答可不可信看這排 */}
                    {meta?.used?.length > 0 && !streaming && (
                        <div className="px-4 pb-1.5 flex items-center gap-1 flex-wrap text-[10px] text-[var(--text-meta)]">
                            {meta.tickers?.length > 1 && (
                                <span className="px-1.5 py-0.5 rounded bg-[rgba(0,82,255,0.1)] text-[var(--accent-cyan)] font-semibold">
                                    {meta.tickers.join(' · ')}
                                </span>
                            )}
                            <Database size={10} />
                            {meta.used.map(s => (
                                <span key={s} className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)]">{s}</span>
                            ))}
                            {meta.missing?.map(s => (
                                <span key={s} className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] line-through opacity-60">{s}</span>
                            ))}
                        </div>
                    )}

                    {/* 輸入 */}
                    <div className="px-3 py-3 border-t border-[var(--glass-border)] bg-[var(--bg-secondary)]">
                        <div className="flex items-end gap-2">
                            <textarea
                                ref={inputRef}
                                value={input}
                                onChange={e => setInput(e.target.value)}
                                onKeyDown={onKeyDown}
                                rows={1}
                                placeholder={ticker ? `問 ${ticker}：技術面、財報、估值…` : '問大盤、總經、板塊，或直接點名幾檔比較…'}
                                className="flex-1 resize-none max-h-28 rounded-xl px-3 py-2.5 text-[13px] bg-[var(--bg-primary)] border border-[var(--glass-border)] outline-none focus:border-[rgba(0,82,255,0.45)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
                            />
                            <button
                                onClick={() => ask()}
                                disabled={streaming || !input.trim()}
                                className="ui-focus cursor-pointer disabled:cursor-default h-[42px] px-3.5 rounded-xl text-sm font-semibold bg-[#0052ff] text-white disabled:opacity-40 hover:bg-[#578bfa] transition-colors duration-200 flex items-center gap-1.5"
                            >
                                <Send size={14} />
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

function IconBtn({ children, onClick, title, active }) {
    return (
        <button title={title} onClick={onClick}
            className={`ui-focus cursor-pointer w-7 h-7 rounded-lg flex items-center justify-center border transition-colors duration-200 ${
                active
                    ? 'bg-[rgba(0,82,255,0.12)] border-[rgba(0,82,255,0.35)] text-[var(--accent-cyan)]'
                    : 'bg-[var(--bg-primary)] border-[var(--glass-border)] text-[var(--text-secondary)] hover:text-[var(--accent-cyan)]'}`}>
            {children}
        </button>
    );
}

function EmptyState({ scopeLabel, isStock, groups, chips, onPick, onDraft, disabled }) {
    // 建議問題按主題分頁，不是一次攤十一顆 —— 攤開來是一面看起來都一樣的藥丸牆，
    // 反而看不出「這裡能問哪幾類問題」。一次只露一類，主題本身就是答案。
    const [tab, setTab] = useState(0);
    useEffect(() => { setTab(0); }, [scopeLabel]);
    const active = groups[tab] || groups[0];

    return (
        <div className="space-y-4">
            {/* 範圍：面板最該一眼看懂的事，所以給它自己的一張卡而不是塞在句子裡 */}
            <div className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-tertiary)] px-3 py-2.5">
                <div className="flex items-center gap-2">
                    {isStock
                        ? <Target size={14} className="text-[var(--accent-cyan)] shrink-0" />
                        : <Globe2 size={14} className="text-[var(--accent-cyan)] shrink-0" />}
                    <span className="text-[11px] text-[var(--text-meta)]">問答範圍</span>
                    <span className="text-sm font-bold">{isStock ? scopeLabel : '大盤・總經'}</span>
                </div>
                <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text-body)]">
                    答案只用下面這些資料，沒有的會直說沒有。
                </p>
                {chips.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                        {chips.map(c => (
                            <span key={c.key}
                                className="px-1.5 py-0.5 rounded text-[10px] text-[var(--text-meta)] bg-[var(--bg-secondary)] border border-[var(--glass-border)]">
                                {c.label}
                            </span>
                        ))}
                    </div>
                )}
            </div>

            {/* 建議問題：主題分頁 + 該主題的題目 */}
            {groups.length > 0 && (
                <div>
                    <div role="tablist" aria-label="建議問題主題"
                        className="flex items-center gap-1 flex-wrap mb-2">
                        {groups.map((g, i) => (
                            <button key={g.topic} role="tab" aria-selected={i === tab}
                                onClick={() => setTab(i)}
                                className={`ui-focus cursor-pointer px-2.5 py-1 rounded-full text-[12px] font-medium border transition-colors duration-200 ${
                                    i === tab
                                        ? 'bg-[rgba(0,82,255,0.1)] border-[rgba(0,82,255,0.35)] text-[var(--accent-cyan-text)]'
                                        : 'bg-transparent border-[var(--glass-border)] text-[var(--text-meta)] hover:text-[var(--accent-cyan-text)] hover:border-[rgba(0,82,255,0.35)]'}`}>
                                {g.topic}
                            </button>
                        ))}
                    </div>
                    <div className="flex flex-col gap-1.5">
                        {(active?.items || []).map(q => (
                            <button key={q} onClick={() => onPick(q)} disabled={disabled}
                                className="ui-focus tap cursor-pointer group flex items-center gap-2 text-left px-2.5 py-2 rounded-lg text-[12.5px] leading-snug text-[var(--text-body)] bg-[var(--bg-tertiary)] border border-[var(--glass-border)] hover:border-[rgba(0,82,255,0.4)] hover:bg-[rgba(0,82,255,0.06)] transition-colors duration-200 disabled:opacity-40 disabled:cursor-default">
                                <span className="flex-1">{q}</span>
                                <ArrowRight size={13}
                                    className="shrink-0 text-[var(--text-muted)] group-hover:text-[var(--accent-cyan-text)]" />
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* 跨標的：這是最不容易被發現的能力，給它一行明講 + 一鍵套用範例 */}
            <button onClick={() => onDraft(isStock ? `${scopeLabel} 跟 AMD 現在誰的估值比較合理？`
                                                   : 'NVDA 跟 AMD 現在誰的估值比較合理？')}
                className="ui-focus tap cursor-pointer w-full flex items-start gap-2 text-left px-2.5 py-2 rounded-lg border border-dashed border-[var(--glass-border)] hover:border-[rgba(0,82,255,0.4)] transition-colors duration-200">
                <Layers size={13} className="mt-0.5 shrink-0 text-[var(--text-meta)]" />
                <span className="text-[11.5px] leading-relaxed text-[var(--text-meta)]">
                    句子裡直接打上代號就能<span className="font-semibold text-[var(--text-body)]">跨標的比較</span>
                    （最多四檔）—— 點這裡看範例
                </span>
            </button>
        </div>
    );
}


function Bubble({ msg, streaming, onStar }) {
    if (msg.role === 'user') {
        return (
            <div className="flex justify-end">
                <div className="max-w-[88%] rounded-2xl rounded-br-sm px-3 py-2 text-[13px] leading-relaxed bg-[rgba(0,82,255,0.1)] border border-[rgba(0,82,255,0.2)] whitespace-pre-wrap">
                    {msg.content}
                </div>
            </div>
        );
    }
    return (
        <div className="space-y-1">
            <div className="rounded-2xl rounded-bl-sm px-3 py-2.5 bg-[var(--bg-tertiary)] border border-[var(--glass-border)]">
                <Markdown compact>{msg.content}</Markdown>
                {streaming && <span className="inline-block w-1.5 h-3.5 bg-[#0052ff] animate-pulse align-middle ml-0.5" />}
            </div>
            {!streaming && msg.id && (
                <div className="flex items-center gap-2.5 pl-1 text-[10px] text-[var(--text-meta)]">
                    <button onClick={() => onStar?.(msg.id, !msg.starred)}
                        className={`ui-focus cursor-pointer flex items-center gap-1 hover:text-[var(--accent-amber-text)] ${msg.starred ? 'text-[var(--accent-amber-text)]' : ''}`}>
                        <Star size={10} fill={msg.starred ? 'currentColor' : 'none'} />
                        {msg.starred ? '已標記' : '標記'}
                    </button>
                    {msg.duration_ms != null && <span>{(msg.duration_ms / 1000).toFixed(1)}s</span>}
                    {msg.sections?.length > 0 && <span className="truncate">依據：{msg.sections.join('、')}</span>}
                </div>
            )}
        </div>
    );
}
