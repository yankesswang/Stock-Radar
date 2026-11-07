import { useState, useEffect, useMemo, useRef } from 'react';
import { useSearch, useLocation } from 'wouter';
import axios from '../lib/apiClient';
import { Landmark } from 'lucide-react';

/* SEC 申報的財務資料，一個區塊四個切面：
     基本面指標 ｜ 損益表 ｜ 資產負債表 ｜ 現金流量表
   以前「基本面指標」和「三大表」是兩頁，而且指標表裡一半的列（營收、淨利、
   資產總計…）就是三大表的科目原封不動再抄一次。現在合在同一個區塊，
   指標分頁只留「算出來的」（利潤率、ROE、FCF、負債比…），申報原數一律去
   三大表分頁看——同一個數字只出現在一個地方。
   年度/單季與期數是四個切面共用的，切分頁不會重設。 */
const VIEWS = [
    { k: 'metrics',  label: '基本面指標' },
    { k: 'income',   label: '損益表' },
    { k: 'balance',  label: '資產負債表' },
    { k: 'cashflow', label: '現金流量表' },
];
const VIEW_KEYS = new Set(VIEWS.map((v) => v.k));
const FREQ = [{ k: 'annual', label: 'Annually' }, { k: 'quarterly', label: 'Quarterly' }];
const LIMITS = [5, 10, 15, 20, 40];

/* ── 格式 ─────────────────────────────────────────────────────────────── */
function compactMoney(a, unit) {
    const suffix = unit === 'shares' ? '股' : '';
    return a >= 1e9 ? `${(a / 1e9).toFixed(2)}B${suffix}`
        : a >= 1e6 ? `${(a / 1e6).toFixed(1)}M${suffix}`
        : a >= 1e3 ? `${(a / 1e3).toFixed(0)}K${suffix}`
        : a.toFixed(unit === 'shares' ? 0 : 2);
}

// 三大表：同一張損益表裡三種單位並存（金額、元/股、股數），縮寫要看單位，
// 不然 EPS $7.49 會被縮成 0.0。負數用括號是財報慣例。
function fmtStatement(v, unit) {
    if (v == null) return '—';
    if (unit === 'USD/shares' || unit?.endsWith('/shares')) {
        return v < 0 ? `(${Math.abs(v).toFixed(2)})` : v.toFixed(2);
    }
    const s = compactMoney(Math.abs(v), unit);
    return v < 0 ? `(${s})` : s;
}

// 指標：後端每列帶 format，直接照著排
function fmtMetric(v, format) {
    if (v == null) return '—';
    if (format === 'pct') return `${(v * 100).toFixed(1)}%`;
    if (format === 'ratio' || format === 'per_share') return v.toFixed(2);
    const a = Math.abs(v);
    const s = a >= 1e9 ? `${(a / 1e9).toFixed(1)}B`
        : a >= 1e6 ? `${(a / 1e6).toFixed(0)}M`
        : a >= 1e3 ? `${(a / 1e3).toFixed(0)}K` : a.toFixed(0);
    return v < 0 ? `(${s})` : s;
}

/* ── 變化率 ─────────────────────────────────────────────────────────────
   每一欄都跟它右邊那一欄（前一期）比，也就是環比：年度視圖是比上一年、
   單季視圖是比上一季。以前只在表頭左邊放一組變化欄，那組永遠只算最新一期，
   其餘期間看不出走勢——十欄數字裡只有一欄有變化率，等於沒有。

   單季的環比會被淡旺季干擾（零售業 Q4 永遠暴衝、Q1 永遠掉），所以單季視圖
   額外把同比（較去年同季、四季前）放進滑鼠提示，跟這個檔案處理會計季、
   公式的方式一致：主線只留一個數字，第二層資訊走 title。

   比率類指標（毛利率、ROE）的變化用百分點 pp，金額變化用 %。 */
const CHANGE_LABEL = { annual: '較上一年', quarterly: '較上一季' };
const YOY_STEP = 4;                       // 單季視圖的同比：往回四季

function change(cur, prev, format) {
    if (cur == null || prev == null) return null;
    if (format === 'pct') {
        const pp = (cur - prev) * 100;
        return { v: pp, text: `${pp > 0 ? '+' : ''}${pp.toFixed(1)}pp` };
    }
    if (prev === 0) return null;
    // 跨過零的變化率沒有意義：營業外收支從 −1.16B 到 21.0M 會算出 +82452%，
    // 那個數字只反映分母多接近零，不反映變化多大。直接講「轉正／轉負」。
    if ((cur < 0) !== (prev < 0)) {
        return { v: cur >= 0 ? 1 : -1, text: cur >= 0 ? '轉正' : '轉負' };
    }
    const pct = ((cur - prev) / Math.abs(prev)) * 100;
    // 基期接近零時變化率會噴到四五位數（AMZN 2025 營業外收支 21.0M → 17.34B
    // 是 +82452%），那個位數只反映分母多小。封頂讓欄寬穩定，真正的倍數看上面
    // 兩個原數就好。
    if (Math.abs(pct) > 999) return { v: pct, text: pct > 0 ? '>+999%' : '<−999%' };
    return { v: pct, text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` };
}

/* 一格數值＋它的環比。periods 是新到舊，所以「前一期」是 i + 1。 */
function ValueCell({ values, periods, i, freq, format, unit, showDelta }) {
    const p = periods[i];
    const text = format === undefined ? fmtStatement(values[p], unit) : fmtMetric(values[p], format);
    const d = showDelta ? change(values[p], values[periods[i + 1]], format) : null;
    // 單季才給同比：年度視圖的環比本來就是同比，再放一次是重複
    const yoy = freq === 'quarterly' ? change(values[p], values[periods[i + YOY_STEP]], format) : null;
    return (
        <td className="num py-1.5 px-3 text-right whitespace-nowrap align-top text-[var(--text-primary)]"
            title={yoy ? `較去年同季 ${yoy.text}` : undefined}>
            <div>{text}</div>
            {d && (
                <div className="text-[10.5px] leading-tight tabular-nums"
                     style={{ color: d.v >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                    {d.text}
                </div>
            )}
        </td>
    );
}

const PILL_ON  = 'text-[var(--accent-cyan-text)] bg-[rgba(0,82,255,0.10)] border-[rgba(0,82,255,0.35)]';
const PILL_OFF = 'text-[var(--text-secondary)] bg-[var(--bg-secondary)] border-[var(--glass-border)] hover:border-[var(--glass-border-hover)]';
const pill = (on, big = false) =>
    `ui-focus tap cursor-pointer px-3 py-2 rounded-full ${big ? 'text-[13px]' : 'text-[12px]'} font-medium border transition-colors duration-200 ${on ? PILL_ON : PILL_OFF}`;

/* ── 期間表頭：日曆季讓跨公司可比，會計季放滑鼠提示；第四季推算值標出來 ── */
function PeriodHeaders({ data }) {
    return data.periods.map((p) => {
        const derived = data.derived_periods?.includes(p);
        const fiscal = data.fiscal_labels?.[p];
        return (
            <th key={p} scope="col"
                title={`${p}${fiscal ? ` · 會計季 ${fiscal}` : ''}`
                       + (derived ? ' · 第四季：全年減前三季推算，非申報原數' : '')}
                className="num py-2 px-3 font-medium text-right whitespace-nowrap">
                {data.period_labels?.[p] || p}
                {derived && (
                    <span className="ml-1 text-[10px] font-normal text-[var(--accent-amber-text)]">推算</span>
                )}
            </th>
        );
    });
}

/* ── 基本面指標：只列計算值 ─────────────────────────────────────────── */
function MetricsTable({ ticker, data, freq, showDelta }) {
    // 申報原數（營收、淨利、資產總計…）已經在三大表分頁，這裡不再抄一次
    const groups = useMemo(() => data.groups
        .map((g) => ({ ...g, items: g.items.filter((it) => it.source === 'computed') }))
        .filter((g) => g.items.length), [data]);

    if (!groups.length) {
        return <p className="py-6 text-center text-[13px] text-[var(--text-meta)]">這檔算不出任何衍生指標。</p>;
    }
    return (
        <table className="data-table w-full text-[12.5px] border-collapse">
            <caption className="sr-only">{ticker} 由 SEC 三大表推算的基本面指標</caption>
            <thead>
                <tr className="text-[var(--text-meta)]">
                    <th scope="col" className="py-2 px-3 text-left font-medium sticky-col">指標</th>
                    <PeriodHeaders data={data} />
                </tr>
            </thead>
            <tbody>
                {groups.map((g) => [
                    <tr key={g.group} className="border-t border-[var(--glass-border)]">
                        <td colSpan={data.periods.length + 1}
                            className="sticky-col py-1.5 px-3 text-[11px] uppercase tracking-wide text-[var(--text-meta)]">
                            {g.group}
                        </td>
                    </tr>,
                    ...g.items.map((it) => (
                        <tr key={it.key} className="border-t border-[var(--glass-border)]">
                            <th scope="row"
                                className="sticky-col py-1.5 px-3 text-left font-normal text-[var(--text-primary)]"
                                title={it.formula ? `＝ ${it.formula}` : undefined}>
                                {it.label}
                                {/* 公式直接寫在列上：這一頁沒有原數可對照，
                                    不寫的話「ROIC」是怎麼除出來的沒人知道。
                                    「（非期間平均）」這種說明性括號拿掉，運算子後面的
                                    分母括號「÷（權益＋負債）」要留 */}
                                {it.formula && (
                                    <span className="ml-2 text-[10.5px] text-[var(--text-meta)] hidden md:inline">
                                        ＝ {it.formula.replace(/(?<![÷−＋+×\s])（[^）]*）/g, '')}
                                    </span>
                                )}
                            </th>
                            {data.periods.map((p, i) => (
                                <ValueCell key={p} values={it.values} periods={data.periods} i={i}
                                           freq={freq} format={it.format} showDelta={showDelta} />
                            ))}
                        </tr>
                    )),
                ])}
            </tbody>
        </table>
    );
}

/* ── 三大表 ─────────────────────────────────────────────────────────── */
function StatementTable({ ticker, data, freq, showDelta }) {
    const periods = data.periods ?? [];
    // 段落標頭底下沒有任何科目（例如損益表尾巴掛著的「Products」軸）就不畫
    const items = useMemo(() => {
        const src = data.line_items ?? [];
        return src.filter((li, i) => !li.is_header || (src[i + 1] && !src[i + 1].is_header));
    }, [data]);

    return (
        <table className="data-table w-full text-[12.5px] border-collapse">
            <caption className="sr-only">
                {ticker} {data.label}，{data.freq === 'instant' ? '各期末時點' : freq === 'annual' ? '年度' : '單季'}
            </caption>
            <thead>
                <tr className="text-[var(--text-meta)]">
                    <th scope="col" className="py-2 px-3 font-medium text-left sticky-col">科目</th>
                    <PeriodHeaders data={data} />
                </tr>
            </thead>
            <tbody>
                {items.map((li) => {
                    if (li.is_header) {
                        return (
                            <tr key={li.concept} className="border-t border-[var(--glass-border)]">
                                <td colSpan={periods.length + 1}
                                    className="sticky-col py-1.5 px-3 text-[11px] uppercase tracking-wide text-[var(--text-meta)]">
                                    {li.label}
                                </td>
                            </tr>
                        );
                    }
                    return (
                        <tr key={li.concept}
                            className={`border-t border-[var(--glass-border)] ${li.is_total ? 'font-semibold bg-[rgba(0,82,255,0.03)]' : ''}`}>
                            <th scope="row"
                                className="sticky-col py-1.5 px-3 text-left font-normal text-[var(--text-primary)]"
                                style={{ paddingLeft: `${12 + (li.depth ?? 0) * 12}px` }}
                                title={li.label_en}>
                                {li.label}
                                {/* 沒有中文對照的科目沿用申報原文，標一下 */}
                                {!li.translated && <span className="ml-1 text-[10px] text-[var(--text-meta)]">EN</span>}
                            </th>
                            {periods.map((p, i) => (
                                <ValueCell key={p} values={li.values} periods={periods} i={i}
                                           freq={freq} unit={li.unit} showDelta={showDelta} />
                            ))}
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}

/* ═════════════════════════════════════════════════════════════════════════ */
export default function SecFinancials({ ticker }) {
    // 切面記在 ?tab=：個股主頁的「三大財報」入口與舊 /financials 書籤直接落到對的分頁
    const search = useSearch();
    const [location, navigate] = useLocation();
    const urlTab = useMemo(() => {
        const t = new URLSearchParams(search || '').get('tab');
        return VIEW_KEYS.has(t) ? t : null;
    }, [search]);

    const [view, setViewState] = useState(urlTab ?? 'metrics');
    const [freq, setFreq]      = useState('annual');
    const [limit, setLimit]    = useState(10);
    // 變化率佔掉每一列第二行，四十期的表會變兩倍高，給個開關收起來
    const [showDelta, setShowDelta] = useState(true);
    // 回應都以 key 存成字典，畫面直接用當下的 key 去查：查不到就是載入中。
    // 這樣換 ticker / 切分頁不需要在 effect 裡同步重設狀態，切回去過的分頁也是秒開。
    const [ovMap, setOvMap]    = useState({});     // ticker → 總覽；null = 沒 SEC 資料
    const [resMap, setResMap]  = useState({});     // `${ticker}|${view}|${freq}|${limit}` → { status, data }
    const sectionRef = useRef(null);

    const overview = ovMap[ticker];                 // undefined = 查詢中
    const key = `${ticker}|${view}|${freq}|${limit}`;
    const res = resMap[key];
    const state = res?.status ?? 'loading';
    const data = res?.data ?? null;

    const setView = (k) => {
        setViewState(k);
        const q = new URLSearchParams(search || '');
        if (k === 'metrics') q.delete('tab'); else q.set('tab', k);
        const qs = q.toString();
        navigate(`${location}${qs ? `?${qs}` : ''}`, { replace: true });
    };

    // 有沒有 SEC 資料用總覽判斷一次；沒有就整段不出現，不占版面
    useEffect(() => {
        if (!ticker || ticker in ovMap) return;
        let alive = true;
        axios.get(`/api/financials/${ticker}`)
            .then(({ data: d }) => { if (alive) setOvMap((m) => ({ ...m, [ticker]: d })); })
            .catch(() => { if (alive) setOvMap((m) => ({ ...m, [ticker]: null })); });
        return () => { alive = false; };
    }, [ticker, ovMap]);

    useEffect(() => {
        if (!ticker || !overview || key in resMap) return;
        let alive = true;
        axios.get(`/api/financials/${ticker}/${view}`, { params: { freq, limit } })
            .then(({ data: d }) => {
                if (alive) setResMap((m) => ({ ...m, [key]: { status: 'ok', data: d } }));
            })
            .catch((e) => {
                if (!alive) return;
                const status = e?.response?.status === 404 ? 'empty' : 'error';
                setResMap((m) => ({ ...m, [key]: { status, data: null } }));
            });
        return () => { alive = false; };
    }, [ticker, overview, view, freq, limit, key, resMap]);

    // 從別頁帶 ?tab= 進來的，把這個區塊捲到視野裡（只在第一次掛載時）
    const scrolled = useRef(false);
    useEffect(() => {
        if (scrolled.current || !urlTab || !overview || !sectionRef.current) return;
        scrolled.current = true;
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        sectionRef.current.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    }, [urlTab, overview]);

    if (overview === null) return null;

    const cov = overview?.coverage;
    const isMetrics = view === 'metrics';

    return (
        <section ref={sectionRef} id="financials"
                 className="rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4 scroll-mt-24">
            <div className="flex items-center gap-2 flex-wrap mb-3">
                <h2 className="text-[15px] font-bold text-[var(--text-primary)] inline-flex items-center gap-2">
                    <Landmark size={16} aria-hidden="true" /> 財務報表與基本面
                </h2>
                <span className="num text-[11.5px] text-[var(--text-meta)]">
                    來源：SEC 申報（結構）＋ XBRL companyfacts（數值）
                    {cov?.mn && ` · ${cov.mn.slice(0, 4)} 起共 ${cov.np} 個期間`}
                </span>
                <div className="ml-auto flex gap-2 items-center flex-wrap" role="group" aria-label="期間">
                    {FREQ.map((f) => (
                        <button key={f.k} onClick={() => setFreq(f.k)} aria-pressed={freq === f.k}
                                className={pill(freq === f.k)}>
                            {f.label}
                        </button>
                    ))}
                    <button onClick={() => setShowDelta((v) => !v)} aria-pressed={showDelta}
                            title={`每一欄顯示${CHANGE_LABEL[freq]}的變化率`}
                            className={pill(showDelta)}>
                        變化率
                    </button>
                    <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
                            aria-label="顯示幾個期間"
                            className="ui-focus tap num rounded-lg border border-[var(--glass-border)]
                                       bg-[var(--bg-secondary)] px-2 py-1.5 text-[12px] cursor-pointer
                                       text-[var(--text-primary)]">
                        {LIMITS.map((n) => <option key={n} value={n}>{n} 期</option>)}
                    </select>
                </div>
            </div>

            {/* 切面：同一個區塊裡切，不跳頁 */}
            <div className="flex items-center gap-1.5 flex-wrap mb-3" role="tablist" aria-label="財務資料切面">
                {VIEWS.map((v) => {
                    const on = view === v.k;
                    return (
                        <button key={v.k} role="tab" aria-selected={on} id={`fin-tab-${v.k}`}
                                aria-controls="fin-panel" onClick={() => setView(v.k)}
                                className={pill(on, true)}>
                            {v.label}
                        </button>
                    );
                })}
                <span className="text-[11.5px] text-[var(--text-meta)] w-full sm:w-auto sm:ml-2">
                    {isMetrics
                        ? '這裡全是由三大表算出的比率與衍生值；營收、淨利這類申報原數請看三大表分頁'
                        : data?.derived_periods?.length > 0
                            ? '標「推算」的是第四季：10-K 只報全年，該季由全年減前三季得出'
                            : '負數以括號表示'}
                    {showDelta && `　·　數字下方是${CHANGE_LABEL[freq]}`}
                    {showDelta && freq === 'quarterly' && '，滑鼠移上去看較去年同季'}
                </span>
            </div>

            <div id="fin-panel" role="tabpanel" aria-labelledby={`fin-tab-${view}`}>
                {state === 'loading' && (
                    <div className="space-y-1" aria-busy="true">
                        {Array.from({ length: 10 }).map((_, i) => (
                            <div key={i} className="h-7 rounded bg-[var(--bg-tertiary)] animate-pulse opacity-70" />
                        ))}
                    </div>
                )}

                {state === 'empty' && (
                    <p className="py-8 text-center text-[13px] text-[var(--text-meta)]">
                        {isMetrics ? '這檔還沒有算好的基本面指標' : '這檔還沒有這張表的資料'}
                        （跑 fetch_financials.py 後才會出現）
                    </p>
                )}
                {state === 'error' && (
                    <p className="py-8 text-center text-[13px] text-[var(--accent-danger-text)]">讀取失敗，稍後再試。</p>
                )}

                {state === 'ok' && data && (
                    <div className="overflow-auto max-h-[70vh]">
                        {isMetrics
                            ? <MetricsTable ticker={ticker} data={data} freq={freq} showDelta={showDelta} />
                            : <StatementTable ticker={ticker} data={data} freq={freq} showDelta={showDelta} />}
                    </div>
                )}
            </div>
        </section>
    );
}
