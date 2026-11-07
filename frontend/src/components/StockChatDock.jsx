import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import axios from '../lib/apiClient';
import {
    MessageSquare, Send, History, Plus, Star, Trash2, X, AlertTriangle,
    Database, ScrollText, Minus, Target, Globe2, Layers, ArrowRight, GripVertical,
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

// ── 面板位置 ─────────────────────────────────────────────────────────────
// 預設貼右緣置中，但看圖表時它常剛好蓋住要看的那一塊 → 桌機上可以拖標題列搬走、
// 拉右下角改大小，記在 localStorage（只是個人偏好，讀不到就回預設）。
// 手機維持全螢幕，不吃這組設定。雙擊標題列回到預設位置。
const LAYOUT_KEY = 'stockchat:layout';
const MIN_W = 340, MIN_H = 360, EDGE = 8;

function loadLayout() {
    try {
        const v = JSON.parse(localStorage.getItem(LAYOUT_KEY));
        return v && [v.x, v.y, v.w, v.h].every(Number.isFinite) ? v : null;
    } catch { return null; }
}

function saveLayout(v) {
    try {
        if (v) localStorage.setItem(LAYOUT_KEY, JSON.stringify(v));
        else localStorage.removeItem(LAYOUT_KEY);
    } catch { /* 無痕模式之類，當次有效就好 */ }
}

// 視窗縮小後面板不能跑到畫面外：先收尺寸、再收位置
function clampLayout({ x, y, w, h }) {
    const vw = window.innerWidth, vh = window.innerHeight;
    w = Math.min(Math.max(w, MIN_W), vw - EDGE * 2);
    h = Math.min(Math.max(h, MIN_H), vh - EDGE * 2);
    x = Math.min(Math.max(x, EDGE), vw - w - EDGE);
    y = Math.min(Math.max(y, EDGE), vh - h - EDGE);
    return { x, y, w, h };
}

function useIsDesktop() {
    const query = '(min-width: 640px)';   // 對齊 Tailwind 的 sm:
    const [ok, setOk] = useState(() => window.matchMedia(query).matches);
    useEffect(() => {
        const mq = window.matchMedia(query);
        const on = () => setOk(mq.matches);
        mq.addEventListener('change', on);
        return () => mq.removeEventListener('change', on);
    }, []);
    return ok;
}

function tickerFromPath(path) {
    const m = /^\/stock\/([A-Za-z0-9.-]{1,12})(\/|$)/.exec(path);
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
    const panelRef  = useRef(null);
    const gestureRef = useRef(null);   // 拖曳／縮放進行中的起點

    const isDesktop = useIsDesktop();
    const [layout, setLayout] = useState(loadLayout);   // null = 預設貼右緣置中

    useEffect(() => {
        const onResize = () => setLayout(l => (l ? clampLayout(l) : l));
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);

    // mode = 'move'（標題列）| 'resize'（右下角）。起點用當下的實際位置，
    // 所以從預設的置中狀態第一次拖也不會跳。
    const startGesture = (mode) => (e) => {
        if (!isDesktop || e.button !== 0) return;
        if (mode === 'move' && e.target.closest('button, a, input, textarea')) return;
        const r = panelRef.current.getBoundingClientRect();
        gestureRef.current = { mode, px: e.clientX, py: e.clientY, x: r.left, y: r.top, w: r.width, h: r.height };
        e.currentTarget.setPointerCapture(e.pointerId);
        e.preventDefault();   // 連同 pointer capture，拖曳中不會順手反白頁面文字
    };

    const moveGesture = (e) => {
        const g = gestureRef.current;
        if (!g) return;
        const dx = e.clientX - g.px, dy = e.clientY - g.py;
        g.last = g.mode === 'move'
            ? clampLayout({ x: g.x + dx, y: g.y + dy, w: g.w, h: g.h })
            // 縮放時左上角不動：撞到畫面邊緣就停，不是把整個面板往回推
            : clampLayout({ x: g.x, y: g.y,
                w: Math.min(g.w + dx, window.innerWidth - g.x - EDGE),
                h: Math.min(g.h + dy, window.innerHeight - g.y - EDGE) });
        setLayout(g.last);
    };

    const endGesture = () => {
        const g = gestureRef.current;
        gestureRef.current = null;
        if (g?.last) saveLayout(g.last);   // 只點一下沒動就不寫
    };

    const resetLayout = (e) => {
        if (e.target.closest('button, a')) return;
        setLayout(null); saveLayout(null);
    };

    const floating = isDesktop && layout;
    const gestureHandlers = { onPointerMove: moveGesture, onPointerUp: endGesture, onPointerCancel: endGesture };

    const closeStream = () => { esRef.current?.close(); esRef.current = null; };

    const loadSessions = useCallback(() => {
        axios.get(`${API}/sessions`, { params: ticker ? { ticker } : {} })
            .then(r => setSessions(r.data.sessions || [])).catch(() => setSessions([]));
    }, [ticker]);

    // 換股票 = 換脈絡。上一檔的對話帶到下一檔會問出錯的答案，所以整個重置。
    useEffect(() => {
        closeStream();
        setMessages([]); setLive(''); setMeta(null); setError('');
        setSessionId(null); setInput(''); setStreaming(false); setShowHistory(false);
        axios.get(`${API}/suggestions`, { params: ticker ? { ticker } : {} })
            .then(r => {
                setSuggestions(r.data.groups || []);
                // 個股頁也能問大盤（後端看問句主詞決定撈哪邊），所以逐檔＋大盤兩組都列；
                // 別頁只列大盤的。族群區塊（scope=theme）是問句點名族群才自動帶，不列
                const want = ticker ? ['stock', 'market'] : ['market'];
                setDataChips((r.data.sections || []).filter(x => want.includes(x.scope)));
            })
            .catch(() => { setSuggestions([]); setDataChips([]); });
        loadSessions();
        return closeStream;
    }, [ticker, loadSessions]);

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
            {/* 貼在右緣正中的圓鈕，只有圖示 —— 它跟著你捲頁，不占版面也不搶字。
                開跟收是同一顆：打開後它留在原地變成「－」，面板讓出它的位置。
                手機上面板是全螢幕，圓鈕會蓋住內容 → 打開時藏起來，改用標題列的 ✕。 */}
            <button
                onClick={() => setOpen(v => !v)}
                title={open ? '收起' : `問 ${scopeLabel}`}
                aria-label={open ? '收起問答' : `問 ${scopeLabel}`}
                aria-expanded={open}
                className={`ui-focus cursor-pointer fixed right-4 top-1/2 -translate-y-1/2 z-[402] w-12 h-12 rounded-full items-center justify-center bg-[#0052ff] text-white shadow-[0_10px_30px_rgba(0,82,255,0.4)] hover:bg-[#578bfa] hover:scale-[1.06] transition-all ${
                    open ? 'hidden sm:flex' : 'flex'}`}
            >
                {open ? <Minus size={19} /> : <MessageSquare size={19} />}
            </button>

            {/* 面板預設靠右垂直置中，貼在圓鈕左邊長出來；拖過之後改用記住的位置 */}
            {open && (
                <div ref={panelRef}
                    style={floating ? { left: layout.x, top: layout.y, width: layout.w, height: layout.h, right: 'auto', bottom: 'auto', transform: 'none' } : undefined}
                    className="fixed z-[401] flex flex-col overflow-hidden glass
                                right-0 bottom-0 w-screen h-[100dvh] rounded-none border-0
                                sm:right-20 sm:bottom-auto sm:top-1/2 sm:-translate-y-1/2
                                sm:w-[420px] sm:h-[min(78vh,680px)] sm:rounded-2xl
                                sm:border sm:border-[var(--glass-border)]
                                shadow-[0_20px_60px_rgba(10,11,13,0.35)]">
                    {/* Header */}
                    <div onPointerDown={startGesture('move')} {...gestureHandlers} onDoubleClick={resetLayout}
                        title={isDesktop ? '拖曳移動・雙擊回到預設位置' : undefined}
                        className="flex items-center gap-2 px-4 py-3 border-b border-[var(--glass-border)] bg-[var(--bg-secondary)] sm:cursor-grab sm:active:cursor-grabbing touch-none select-none">
                        <GripVertical size={14} className="hidden sm:block -ml-2 -mr-1 text-[var(--text-muted)] shrink-0" />
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
                        {/* 桌機收起靠右緣那顆圓鈕；手機它藏起來了，這裡補一顆 ✕ */}
                        <span className="sm:hidden">
                            <IconBtn title="收起" onClick={() => setOpen(false)}><X size={14} /></IconBtn>
                        </span>
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
                                recent={sessions} onOpenSession={openSession}
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
                            {/* 在個股頁問大盤時明講「這題沒用這檔的資料」，不然會以為答案漏看了這檔 */}
                            {ticker && meta.intent && meta.intent !== 'stock' && (
                                <span className="px-1.5 py-0.5 rounded bg-[rgba(0,82,255,0.1)] text-[var(--accent-cyan)] font-semibold">
                                    {meta.intent === 'market' ? '改問大盤' : `大盤 × ${ticker}`}
                                </span>
                            )}
                            {meta.themes?.map(t => (
                                <span key={t} className="px-1.5 py-0.5 rounded bg-[rgba(0,82,255,0.1)] text-[var(--accent-cyan)] font-semibold">
                                    {t} 族群
                                </span>
                            ))}
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

                    {/* 右下角拉把：改大小（桌機才有） */}
                    <div onPointerDown={startGesture('resize')} {...gestureHandlers}
                        title="拖曳調整大小"
                        className="hidden sm:block absolute right-0 bottom-0 w-4 h-4 cursor-nwse-resize touch-none"
                        style={{ background: 'linear-gradient(135deg, transparent 50%, var(--text-muted) 50%, var(--text-muted) 58%, transparent 58%, transparent 70%, var(--text-muted) 70%, var(--text-muted) 78%, transparent 78%)', opacity: 0.6 }} />
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

const RECENT_SHOWN = 5;

function EmptyState({ scopeLabel, isStock, recent, onOpenSession, groups, chips, onPick, onDraft, disabled }) {
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
                    {isStock && ' 在這頁也能直接問大盤、總經或族群（「今天大盤為什麼跌」「族群今天怎麼了」），會自動換成大盤資料。'}
                </p>
                {[['stock', isStock ? scopeLabel : null], ['market', '大盤']].map(([scope, title]) => {
                    const list = chips.filter(c => c.scope === scope);
                    if (!list.length) return null;
                    return (
                        <div key={scope} className="mt-2 flex flex-wrap items-center gap-1">
                            {isStock && title && (
                                <span className="text-[10px] font-semibold text-[var(--text-meta)] mr-0.5">{title}</span>
                            )}
                            {list.map(c => (
                                <span key={c.key}
                                    className="px-1.5 py-0.5 rounded text-[10px] text-[var(--text-meta)] bg-[var(--bg-secondary)] border border-[var(--glass-border)]">
                                    {c.label}
                                </span>
                            ))}
                        </div>
                    );
                })}
            </div>

            {/* 問過的：回來看同一檔時，最常要的是「上次那題答了什麼」，不是再問一次。
                直接列在空白頁，不必先找到標題列的時鐘鈕。 */}
            {recent?.length > 0 && (
                <div>
                    <div className="flex items-center gap-1.5 mb-2 text-[11px] text-[var(--text-meta)]">
                        <History size={12} />
                        <span>{isStock ? `問過 ${scopeLabel}` : '問過的'}</span>
                        {recent.length > RECENT_SHOWN && <span>・最近 {RECENT_SHOWN} 條</span>}
                    </div>
                    <div className="flex flex-col gap-1.5">
                        {recent.slice(0, RECENT_SHOWN).map(s => (
                            <button key={s.id} onClick={() => onOpenSession(s.id)}
                                className="ui-focus tap cursor-pointer group flex items-center gap-2 text-left px-2.5 py-2 rounded-lg text-[12.5px] leading-snug text-[var(--text-body)] bg-[var(--bg-tertiary)] border border-[var(--glass-border)] hover:border-[rgba(0,82,255,0.4)] hover:bg-[rgba(0,82,255,0.06)] transition-colors duration-200">
                                <span className="flex-1 truncate">{s.title}</span>
                                <span className="shrink-0 text-[10px] text-[var(--text-muted)]">
                                    {s.message_count > 2 && `${Math.ceil(s.message_count / 2)} 問・`}
                                    {(s.updated_at || '').slice(5, 10).replace('-', '/')}
                                </span>
                                <ArrowRight size={13}
                                    className="shrink-0 text-[var(--text-muted)] group-hover:text-[var(--accent-cyan-text)]" />
                            </button>
                        ))}
                    </div>
                </div>
            )}

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
