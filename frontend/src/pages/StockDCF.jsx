import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts';
import {
    ArrowLeft, TrendingUp, TrendingDown, AlertTriangle, ArrowRight, Info,
} from 'lucide-react';
import SensitivityMatrix from '../components/SensitivityMatrix';
import { CHART_GRID, CHART_AXIS } from '../components/chartTheme';

/* ═══════════════════════════════════════════════════════════════════════════
   現金流折現（DCF）估值頁

   版面順序照「看的人想知道的順序」排，不是照計算順序排：
     1. 結論 —— 三情境的每股價值擺在最上面，跟現價畫在同一條軸上
     2. 假設 —— 三情境各自假設了什麼，並排比較
     3. 過程 —— 五個步驟，公式旁邊直接放代進去的數字
     4. 檢驗 —— 敏感度、反推、終值交叉驗證、資料可信度

   專有名詞一律中文優先，英文縮寫放在後面的小字，因為查資料時還是要用英文。
   ═══════════════════════════════════════════════════════════════════════════ */

function fmt(val, type) {
    if (val == null) return '—';
    if (type === 'pct') return `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    if (type === 'pct0') return `${val > 0 ? '+' : ''}${val.toFixed(0)}%`;
    if (type === 'dollar') return `${val < 0 ? '-' : ''}$${Math.abs(val).toFixed(2)}`;
    if (type === 'big') {
        const abs = Math.abs(val);
        const sign = val < 0 ? '-' : '';
        if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)} 兆`;
        if (abs >= 1e8) return `${sign}$${(abs / 1e8).toFixed(1)} 億`;
        if (abs >= 1e4) return `${sign}$${(abs / 1e4).toFixed(1)} 萬`;
        return `${sign}$${abs.toFixed(0)}`;
    }
    return String(val);
}

/** 依整組數值挑一個單位，讓同一欄／同一條座標軸的刻度單位一致。
 *  單獨用 fmt(v,'big') 會讓 $9000 億 跟 $1.8 兆 出現在同一欄，掃讀時很容易誤判量級。 */
function moneyScale(values) {
    const max = Math.max(...values.map((v) => Math.abs(v ?? 0)), 0);
    const [div, unit] = max >= 1e12 ? [1e12, ' 兆'] : max >= 1e8 ? [1e8, ' 億'] : max >= 1e4 ? [1e4, ' 萬'] : [1, ''];
    const dp = max / div >= 100 ? 0 : max / div >= 10 ? 1 : 2;
    return (v) => (v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v / div).toFixed(dp)}${unit}`);
}

/** 股數專用格式 —— 用「億股」而不是把金額格式的錢字號拔掉。 */
function shares(v) {
    if (v == null) return '—';
    if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 億股`;
    if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(1)} 萬股`;
    return `${v.toFixed(0)} 股`;
}

const BRIDGE_COLORS = ['var(--accent-purple)', 'var(--accent-cyan)', 'var(--accent-amber)', 'var(--accent-success)'];

/** 情境卡的折現率說明。折現率被 6–16% 上下限夾住時會跟中性情境相同，
 *  這時要說「已到下限」，而不是印一個「比中性低 0.00 個百分點」。 */
function waccNote(a, base, dir) {
    const d = dir === 'up' ? a.effective_wacc - base.effective_wacc : base.effective_wacc - a.effective_wacc;
    if (Math.abs(d) < 0.005) return `折現率 ${a.effective_wacc}%（已達${dir === 'up' ? '上' : '下'}限，與中性相同）`;
    return `折現率 ${a.effective_wacc}%（比中性${dir === 'up' ? '高' : '低'} ${d.toFixed(2)} 個百分點）`;
}

const SCENARIO_META = {
    pessimistic: {
        label: '悲觀', en: 'Pessimistic',
        color: 'var(--accent-danger)', text: 'var(--accent-danger-text)',
        bg: 'rgba(220,38,38,0.06)', border: 'rgba(220,38,38,0.22)',
        premise: (a, base) => `分析師預估區間的低標、${waccNote(a, base, 'up')}、第 5 年利潤率減一個標準差`,
    },
    moderate: {
        label: '中性', en: 'Moderate',
        color: 'var(--accent-cyan)', text: 'var(--accent-cyan-text)',
        bg: 'rgba(0,82,255,0.06)', border: 'rgba(0,82,255,0.22)',
        premise: (a) => `分析師預估共識、折現率 ${a.effective_wacc}%、第 5 年利潤率用五年常態化水準`,
    },
    optimistic: {
        label: '樂觀', en: 'Optimistic',
        color: 'var(--accent-success)', text: 'var(--accent-success-text)',
        bg: 'rgba(5,150,105,0.06)', border: 'rgba(5,150,105,0.22)',
        premise: (a, base) => `分析師預估區間的高標、${waccNote(a, base, 'down')}、第 5 年利潤率加一個標準差`,
    },
};
const ORDER = ['pessimistic', 'moderate', 'optimistic'];

/** 估值區間軸 —— 三個情境的每股價值與現價畫在同一條線上。
 *  數值全部以文字標出，不靠顏色單獨傳達訊息（無障礙要求）。 */
function ValuationRangeBar({ scenarios, price, fair }) {
    const lo = scenarios.pessimistic.intrinsic_value;
    const hi = scenarios.optimistic.intrinsic_value;
    const pts = [lo, hi, price, fair].filter((v) => v != null);
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const pad = (max - min) * 0.12 || Math.abs(max) * 0.12 || 1;
    const a = min - pad;
    const b = max + pad;
    // 三個值完全相等時 b - a 會是 0，除下去整條軸都是 NaN
    const span = b - a || 1;
    const pos = (v) => Math.max(0, Math.min(100, ((v - a) / span) * 100));

    const priceInside = price >= lo && price <= hi;

    return (
        <div className="pt-2">
            <div className="relative h-[86px]">
                {/* 底軸 */}
                <div className="absolute left-0 right-0 top-[44px] h-[3px] rounded-full bg-[var(--bg-tertiary)]" />
                {/* 悲觀→樂觀 區間 */}
                <div
                    className="absolute top-[41px] h-[9px] rounded-full"
                    style={{
                        left: `${pos(lo)}%`,
                        width: `${Math.max(pos(hi) - pos(lo), 0.6)}%`,
                        background: 'linear-gradient(90deg, rgba(220,38,38,0.30), rgba(0,82,255,0.30), rgba(5,150,105,0.30))',
                    }}
                />
                {/* 加權合理價 */}
                {fair != null && (
                    <div className="absolute top-[36px]" style={{ left: `${pos(fair)}%`, transform: 'translateX(-50%)' }}>
                        <div className="w-[3px] h-[19px] rounded-full bg-[var(--accent-cyan)]" />
                        <div className="absolute top-[22px] left-1/2 -translate-x-1/2 whitespace-nowrap text-[11px] font-semibold text-[var(--accent-cyan-text)]">
                            加權 {fmt(fair, 'dollar')}
                        </div>
                    </div>
                )}
                {/* 現價 */}
                {price != null && (
                    <div className="absolute top-[14px]" style={{ left: `${pos(price)}%`, transform: 'translateX(-50%)' }}>
                        <div className="whitespace-nowrap text-[11px] font-bold text-[var(--text-primary)] mb-1 text-center">
                            現價 {fmt(price, 'dollar')}
                        </div>
                        <div className="w-[2px] h-[24px] mx-auto bg-[var(--text-primary)]" />
                    </div>
                )}
                {/* 兩端標籤 */}
                <div className="absolute top-[56px] text-[11px] text-[var(--accent-danger-text)] font-semibold"
                     style={{ left: `${pos(lo)}%`, transform: 'translateX(-50%)' }}>
                    悲觀 {fmt(lo, 'dollar')}
                </div>
                <div className="absolute top-[56px] text-[11px] text-[var(--accent-success-text)] font-semibold"
                     style={{ left: `${pos(hi)}%`, transform: 'translateX(-50%)' }}>
                    樂觀 {fmt(hi, 'dollar')}
                </div>
            </div>
            <p className="text-[12px] text-[var(--text-meta)] mt-1">
                {priceInside
                    ? '現價落在三情境估算的區間之內 —— 市場的預期跟模型沒有明顯分歧。'
                    : price > hi
                        ? '現價高於最樂觀的情境 —— 市場價入的東西比這個模型任何一組假設都多。'
                        : '現價低於最悲觀的情境 —— 市場價入的東西比這個模型任何一組假設都少。'}
            </p>
        </div>
    );
}

/** 計算過程的一個步驟。左邊是編號與標題，右邊是公式與代入的數字。 */
function Step({ n, title, en, formula, children }) {
    return (
        <div className="flex gap-4">
            <div className="shrink-0 flex flex-col items-center">
                <div className="w-7 h-7 rounded-full bg-[var(--accent-cyan)] text-white text-[13px] font-bold
                                flex items-center justify-center">{n}</div>
                {n < 5 && <div className="w-px flex-1 bg-[var(--glass-border)] mt-2" />}
            </div>
            <div className="flex-1 pb-7 min-w-0">
                <h3 className="text-[15px] font-bold text-[var(--text-primary)] mb-0.5">
                    {title} <span className="text-[11px] font-normal text-[var(--text-muted)] ml-1">{en}</span>
                </h3>
                {formula && (
                    <div className="text-[12px] font-mono text-[var(--text-meta)] bg-[var(--bg-secondary)]
                                    border border-[var(--glass-border)] rounded-lg px-3 py-2 my-2 overflow-x-auto styled-scrollbar">
                        {formula}
                    </div>
                )}
                {children}
            </div>
        </div>
    );
}

function Field({ label, en, value, tone }) {
    return (
        <div>
            <div className="text-[11px] text-[var(--text-meta)] mb-0.5">
                {label}{en && <span className="ml-1 opacity-60">{en}</span>}
            </div>
            <div className="text-[14px] font-semibold font-mono text-[var(--text-primary)]"
                 style={tone ? { color: tone } : undefined}>
                {value}
            </div>
        </div>
    );
}

function Section({ title, en, hint, children }) {
    return (
        <section className="space-y-3">
            <div>
                <h2 className="text-[17px] font-bold text-[var(--text-primary)]">
                    {title}
                    {en && <span className="text-[12px] font-normal text-[var(--text-muted)] ml-2">{en}</span>}
                </h2>
                {hint && <p className="text-[12.5px] text-[var(--text-meta)] mt-1 leading-relaxed">{hint}</p>}
            </div>
            {children}
        </section>
    );
}

export default function StockDCF() {
    const [, params] = useRoute('/stock/:ticker/dcf');
    const ticker = params?.ticker?.toUpperCase();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [activeScenario, setActiveScenario] = useState('moderate');

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        axios.get(`/api/stocks/${ticker}/dcf`)
            .then(res => setData(res.data))
            .catch(err => {
                console.error(err);
                setError(err.response?.data?.detail || '載入估值資料失敗');
            })
            .finally(() => setLoading(false));
    }, [ticker]);

    if (loading) {
        return (
            <div className="container py-20 flex justify-center">
                <div className="w-6 h-6 border-2 border-[var(--accent-purple)] border-t-transparent rounded-full animate-spin"
                     role="status" aria-label="載入中" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="container py-20 text-center">
                <p className="text-[var(--text-meta)] text-base">{error}</p>
                <Link href={`/stock/${ticker}`} className="text-sm text-[var(--accent-purple-text)] hover:underline mt-4 inline-block">
                    返回 {ticker}
                </Link>
            </div>
        );
    }

    if (!data || !data.scenarios) return null;

    const sc = data.scenarios[activeScenario];
    if (!sc) return null;

    const { assumptions, summary, projections, value_bridge, warnings } = sc;
    const meta = SCENARIO_META[activeScenario];
    const price = data.current_price;
    const {
        applicability, confidence, fair_value: fairValue, reverse_dcf: reverseDcf,
        data_sources: dataSources, capex_cycle: capexCycle, benchmarks,
    } = data;
    // 敏感度與終值交叉驗證只在中性情境計算 —— 兩者本來就是「繞著基準情境」的
    // 分析，跟著分頁切換而整段消失反而像壞掉。固定讀中性，標題註明。
    const baseScenario = data.scenarios.moderate;
    const sensitivity_ = baseScenario?.sensitivity;
    const terminalCheck = baseScenario?.terminal_cross_check;
    const marginGrowth = sensitivity_?.margin_growth;
    const wc = assumptions.wacc_components;
    const marginReadings = data.margin_readings;

    // 五年預估與價值橋接放在同一個量級尺標下
    const projMoney = moneyScale([
        ...projections.map((p) => p.revenue),
        ...projections.map((p) => p.fcf),
        ...projections.map((p) => p.present_value),
        summary.total_pv_fcfs,
    ]);
    const bridgeMoney = moneyScale((value_bridge || []).map((b) => b.value));

    const bridgeData = (value_bridge || []).map((item, i) => ({
        name: { 'PV of FCFs': '五年現金流現值', 'PV of Terminal Value': '終值現值',
                'Net Cash': '淨現金', 'Equity Value': '股權價值' }[item.label] || item.label,
        value: item.value,
        fill: BRIDGE_COLORS[i % BRIDGE_COLORS.length],
    }));

    const CONF_TONE = (s) => s >= 65 ? 'var(--accent-success)' : s >= 45 ? 'var(--accent-amber)' : 'var(--accent-danger)';

    // 有些區塊只在有資料時出現（資本支出正常化、終值交叉驗證……），
    // 所以編號要按這次實際會渲染的清單算，不能寫死。
    const visibleSections = [
        'summary', 'hypotheses', 'steps',
        wc && 'wacc',
        sensitivity_ && 'sensitivity',
        reverseDcf && 'reverse',
        capexCycle && 'capex',
        terminalCheck && 'terminal',
        (confidence || dataSources) && 'confidence',
    ].filter(Boolean);
    const no = (key) => `${'一二三四五六七八九十'[visibleSections.indexOf(key)] ?? ''}、`;

    return (
        <div className="container py-8 space-y-9 animate-fade-in">
            <Link
                href={`/stock/${ticker}`}
                className="inline-flex items-center gap-2 text-sm text-[var(--text-meta)] hover:text-[var(--text-primary)] transition-colors duration-200"
            >
                <ArrowLeft size={14} /> 返回 {ticker}
            </Link>

            {/* ══ 標題 ══ */}
            <header className="pb-5 border-b border-[var(--glass-border)]">
                <div className="flex flex-wrap items-center gap-3 mb-1.5">
                    <h1 className="text-3xl font-extrabold tracking-tight">{ticker}</h1>
                    <span className="badge badge-neutral">現金流折現估值 DCF</span>
                    {confidence && (
                        <span
                            className="badge"
                            style={{
                                background: 'var(--bg-tertiary)',
                                color: CONF_TONE(confidence.score),
                                border: `1px solid ${CONF_TONE(confidence.score)}33`,
                            }}
                            title={confidence.summary}
                        >
                            資料可信度 {confidence.grade}（{confidence.score}/100）
                        </span>
                    )}
                </div>
                <p className="text-[13.5px] text-[var(--text-meta)]">
                    財報資料截至 {data.data_date} · 本次計算 {data.computed_date}
                </p>
            </header>

            {/* ══ 適用性警示 ══ */}
            {applicability && !applicability.meaningful && (
                <div className="card card-flat !p-5 !border-[rgba(220,38,38,0.35)]" role="alert">
                    <div className="flex items-center gap-2 mb-2">
                        <AlertTriangle size={16} className="text-[var(--accent-danger)]" />
                        <span className="text-[14px] font-bold text-[var(--accent-danger-text)]">
                            這家公司不適合用現金流折現估值
                        </span>
                    </div>
                    <ul className="space-y-1">
                        {applicability.reasons.map((r, i) => (
                            <li key={i} className="text-[13px] text-[var(--text-meta)] flex items-start gap-2">
                                <span className="text-[var(--accent-danger)] mt-0.5 shrink-0">•</span>{r}
                            </li>
                        ))}
                    </ul>
                    <p className="text-[12px] text-[var(--text-meta)] mt-3">
                        下面的數字在算術上成立，但不構成估值結論 —— 請改看本益比、股價淨值比或同業比較。
                    </p>
                </div>
            )}

            {/* ══ 估值結論 ══ */}
            <Section
                title={`${no('summary')}估值結論`}
                en="Valuation Summary"
                hint="三組假設各自算出一個每股價值，跟現價畫在同一條軸上。三者不是預測，是「如果假設成立，這家公司值多少」。"
            >
                <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,320px)_1fr] gap-7 items-start">
                        {/* 加權合理價 */}
                        <div>
                            <div className="text-[11px] text-[var(--text-meta)] mb-1">
                                加權合理價 <span className="opacity-60">Weighted Fair Value</span>
                            </div>
                            <div className="flex items-baseline gap-3 flex-wrap">
                                <span className="text-[38px] leading-none font-extrabold font-mono tabular-nums">
                                    {fmt(fairValue?.weighted, 'dollar')}
                                </span>
                                {fairValue?.upside_pct != null && (
                                    <span
                                        className="text-[19px] font-bold font-mono"
                                        style={{ color: fairValue.upside_pct >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}
                                    >
                                        {fairValue.upside_pct >= 0 ? <TrendingUp size={16} className="inline mr-1 -mt-1" />
                                                                   : <TrendingDown size={16} className="inline mr-1 -mt-1" />}
                                        {fmt(fairValue.upside_pct, 'pct0')}
                                    </span>
                                )}
                            </div>
                            <div className="text-[13px] text-[var(--text-meta)] mt-2">
                                相對現價 <span className="font-mono font-semibold text-[var(--text-primary)]">{fmt(price, 'dollar')}</span>
                                {fairValue?.upside_pct != null && (
                                    <> · 判定
                                        <span className="font-semibold ml-1"
                                              style={{ color: fairValue.upside_pct >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                                            {fairValue.upside_pct >= 0 ? '低估' : '高估'}
                                        </span>
                                    </>
                                )}
                            </div>
                            {fairValue?.weights && (
                                <div className="text-[11.5px] text-[var(--text-meta)] mt-3 leading-relaxed">
                                    三情境權重 悲觀 {(fairValue.weights.pessimistic * 100).toFixed(0)}%
                                    · 中性 {(fairValue.weights.moderate * 100).toFixed(0)}%
                                    · 樂觀 {(fairValue.weights.optimistic * 100).toFixed(0)}%
                                    <br />資料可信度越低，權重越往中性情境集中。
                                </div>
                            )}
                        </div>

                        {/* 區間軸 */}
                        <ValuationRangeBar scenarios={data.scenarios} price={price} fair={fairValue?.weighted} />
                    </div>
                </div>

                {/* 回測結論的誠實揭露：這個模型的排序能力被一行比率涵蓋，
                    所以把基準擺在旁邊，而不是只秀自己的數字。 */}
                {benchmarks && (benchmarks.fcf_yield != null || benchmarks.earnings_yield != null) && (
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 rounded-xl
                                    border border-[var(--glass-border)] bg-[var(--bg-secondary)]">
                        <span className="text-[11px] font-semibold text-[var(--text-secondary)]">同場對照</span>
                        {benchmarks.fcf_yield != null && (
                            <span className="text-[12.5px] text-[var(--text-meta)]">
                                自由現金流殖利率
                                <span className="font-mono font-bold text-[var(--text-primary)] ml-1.5">
                                    {benchmarks.fcf_yield.toFixed(2)}%
                                </span>
                            </span>
                        )}
                        {benchmarks.earnings_yield != null && (
                            <span className="text-[12.5px] text-[var(--text-meta)]">
                                盈餘殖利率
                                <span className="font-mono font-bold text-[var(--text-primary)] ml-1.5">
                                    {benchmarks.earnings_yield.toFixed(2)}%
                                </span>
                            </span>
                        )}
                        <span className="text-[11.5px] text-[var(--text-meta)] leading-snug basis-full sm:basis-auto sm:flex-1">
                            {benchmarks.note}
                        </span>
                    </div>
                )}

                {/* 資本支出高峰的即時提示：headline 掉 95% 的原因不能等到第七節才講 */}
                {capexCycle && (
                    <div className="card card-flat !p-4 !border-[rgba(217,119,6,0.32)]"
                         style={{ transform: 'none', background: 'rgba(217,119,6,0.04)' }}>
                        <div className="flex items-start gap-2.5">
                            <Info size={15} className="text-[var(--accent-amber)] mt-0.5 shrink-0" />
                            <div className="min-w-0">
                                <div className="text-[13.5px] font-bold text-[var(--accent-amber-text)] mb-1">
                                    這家公司正在資本支出高峰，上面的數字被壓低了
                                </div>
                                <p className="text-[12.5px] text-[var(--text-meta)] leading-relaxed">
                                    自由現金流會把每一塊資本支出當成永久流出。這家公司資本支出佔營收
                                    <span className="font-mono font-semibold text-[var(--text-primary)]"> {capexCycle.capex_pct}%</span>，
                                    折舊只有
                                    <span className="font-mono font-semibold text-[var(--text-primary)]"> {capexCycle.depreciation_pct}% </span>
                                    —— 中間
                                    <span className="font-mono font-semibold text-[var(--text-primary)]"> {capexCycle.growth_capex_pct} 個百分點</span>
                                    是在蓋產能，不是在虧錢。若資本支出回到折舊水準，每股為
                                    <span className="font-mono font-bold text-[var(--accent-amber-text)]"> {fmt(capexCycle.intrinsic_value, 'dollar')}</span>
                                    （{fmt(capexCycle.upside_pct, 'pct0')}）。詳見下方對照。
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* 三情境卡 */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    {ORDER.map((key) => {
                        const s = data.scenarios[key];
                        const m = SCENARIO_META[key];
                        const active = activeScenario === key;
                        const up = s.upside_pct;
                        const diff = price != null ? s.intrinsic_value - price : null;
                        return (
                            <button
                                key={key}
                                onClick={() => setActiveScenario(key)}
                                aria-pressed={active}
                                className="card !p-4 text-left cursor-pointer transition-all duration-200"
                                style={{
                                    transform: 'none',
                                    borderColor: active ? m.border : undefined,
                                    background: active ? m.bg : undefined,
                                    outline: active ? `2px solid ${m.color}` : 'none',
                                    outlineOffset: '-1px',
                                }}
                            >
                                <div className="flex items-center justify-between mb-2.5">
                                    <span className="text-[13px] font-bold" style={{ color: m.text }}>
                                        {m.label}情境
                                        <span className="text-[10px] font-normal opacity-60 ml-1">{m.en}</span>
                                    </span>
                                    {up != null && (
                                        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                                            up >= 0 ? 'badge-success' : 'badge-danger'}`}>
                                            {up >= 0 ? '低估' : '高估'}
                                        </span>
                                    )}
                                </div>
                                <div className="text-[26px] leading-none font-extrabold font-mono tabular-nums mb-2">
                                    {fmt(s.intrinsic_value, 'dollar')}
                                </div>
                                <div className="flex items-baseline gap-2 mb-2.5">
                                    {up != null && (
                                        <span className="text-[14px] font-bold font-mono"
                                              style={{ color: up >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                                            {fmt(up, 'pct0')}
                                        </span>
                                    )}
                                    {diff != null && (
                                        <span className="text-[11.5px] text-[var(--text-meta)] font-mono">
                                            較現價 {diff >= 0 ? '+' : '−'}{fmt(Math.abs(diff), 'dollar')}
                                        </span>
                                    )}
                                </div>
                                <p className="text-[11px] text-[var(--text-meta)] leading-relaxed border-t border-[var(--glass-border)] pt-2">
                                    {m.premise(s.assumptions, data.scenarios.moderate.assumptions)}
                                </p>
                            </button>
                        );
                    })}
                </div>
            </Section>

            {/* ══ 三情境假設對照 ══ */}
            <Section
                title={`${no('hypotheses')}三情境各自假設了什麼`}
                en="Scenario Hypotheses"
                hint="估值的差別全部來自這張表。區間寬度不是拍腦袋定的：利潤率的上下限用這家公司自己五年的波動度算，營收上下限用分析師實際給的預估區間。"
            >
                <div className="metrics-section">
                    <div className="overflow-x-auto styled-scrollbar">
                        <table className="metrics-table">
                            <thead>
                                <tr>
                                    <th className="metrics-th-sticky" style={{ minWidth: '190px' }}>假設項目</th>
                                    {ORDER.map((k) => (
                                        <th key={k} className="metrics-th" style={{ color: SCENARIO_META[k].text, minWidth: '150px' }}>
                                            {SCENARIO_META[k].label}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {[
                                    { label: '第 1 年營收成長率', en: 'Yr1 growth', get: (s) => `${s.assumptions.growth_yr1.toFixed(1)}%` },
                                    { label: '第 2 年營收成長率', en: 'Yr2 growth', get: (s) => `${s.assumptions.growth_yr2.toFixed(1)}%` },
                                    { label: '第 3 年營收成長率', en: 'Yr3 growth', get: (s) => s.assumptions.growth_yr3 != null ? `${s.assumptions.growth_yr3.toFixed(1)}%` : '—' },
                                    { label: '第 1 年自由現金流利潤率', en: 'Yr1 FCF margin', get: (s) => `${s.assumptions.fcf_margin.toFixed(1)}%` },
                                    { label: '第 5 年自由現金流利潤率', en: 'Yr5 FCF margin', get: (s) => { const v = s.assumptions.fcf_margin_path?.slice(-1)[0]; return v != null ? `${v.toFixed(1)}%` : '—'; } },
                                    { label: '折現率（加權平均資金成本）', en: 'WACC', get: (s) => `${s.assumptions.effective_wacc.toFixed(2)}%` },
                                    { label: '永續成長率', en: 'Terminal growth', get: (s) => `${s.assumptions.terminal_growth.toFixed(1)}%` },
                                    { label: '隱含終值倍數（對第 5 年現金流）', en: 'Implied exit multiple', get: (s) => s.summary.implied_terminal_fcf_multiple != null ? `${s.summary.implied_terminal_fcf_multiple}x` : '—' },
                                    { label: '終值佔企業價值比重', en: 'TV % of EV', get: (s) => `${s.summary.terminal_value_pct}%` },
                                    { label: '五年後流通股數', en: 'Diluted shares (Yr5)', get: (s) => shares(s.summary.shares_diluted_yr5) },
                                    { label: '每股內在價值', en: 'Intrinsic value', get: (s) => fmt(s.intrinsic_value, 'dollar'), strong: true },
                                    { label: '相對現價漲跌空間', en: 'Upside', get: (s) => s.upside_pct != null ? fmt(s.upside_pct, 'pct0') : '—', strong: true },
                                ].map((row) => (
                                    <tr key={row.label} className="metrics-tr">
                                        <td className="metrics-td-sticky">
                                            <span className="text-[13px] font-semibold text-[var(--text-primary)]">{row.label}</span>
                                            <span className="block text-[10.5px] text-[var(--text-muted)]">{row.en}</span>
                                        </td>
                                        {ORDER.map((k) => (
                                            <td key={k} className="metrics-td"
                                                style={{
                                                    fontWeight: row.strong ? 700 : undefined,
                                                    color: row.strong ? SCENARIO_META[k].text : undefined,
                                                    background: activeScenario === k ? SCENARIO_META[k].bg : undefined,
                                                }}>
                                                {row.get(data.scenarios[k])}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-[12px] text-[var(--text-meta)] px-1">
                    <span>營收預估基準：<span className="text-[var(--text-primary)] font-medium">{assumptions.revenue_source}</span></span>
                    <span>基期：<span className="text-[var(--text-primary)] font-medium">{assumptions.revenue_base_period}</span></span>
                    <span>股數年變動：<span className="text-[var(--text-primary)] font-medium">{assumptions.share_drift_pct}%</span>（負數代表回購）</span>
                </div>
            </Section>

            {/* ══ 計算過程 ══ */}
            <Section
                title={`${no('steps')}計算過程`}
                en="Step-by-step"
                hint={`以下是「${meta.label}情境」的完整計算。點上面的情境卡可以切換。`}
            >
                <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                    {/* 步驟 1 */}
                    <Step
                        n={1} title="預測未來五年營收" en="Revenue projection"
                        formula={`第 1–3 年 = 分析師預估的營收「絕對金額」（非成長率套用）　第 4–5 年 = 第 3 年成長率逐步收斂至永續成長率 ${assumptions.terminal_growth}%`}
                    >
                        <p className="text-[12.5px] text-[var(--text-meta)] mb-3 leading-relaxed">
                            分析師給的成長率是「會計年度對會計年度」，直接乘在最近四季（TTM）營收上會重複計算已經發生的成長。
                            所以前三年直接採用分析師的營收金額，基期用最後一個實際會計年度（{assumptions.revenue_base_period}）。
                        </p>
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">年度</th>
                                        <th className="metrics-th">預估營收</th>
                                        <th className="metrics-th">年增率</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {projections.map((p) => (
                                        <tr key={p.year} className="metrics-tr">
                                            <td className="metrics-td-sticky text-[13px] font-semibold">第 {p.year} 年</td>
                                            <td className="metrics-td font-mono">{projMoney(p.revenue)}</td>
                                            <td className="metrics-td font-mono font-semibold"
                                                style={{ color: p.growth_rate >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                                                {fmt(p.growth_rate, 'pct')}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </Step>

                    {/* 步驟 2 */}
                    <Step
                        n={2} title="換算成自由現金流" en="Free cash flow"
                        formula={`自由現金流 = 預估營收 × 自由現金流利潤率　（利潤率由 ${assumptions.fcf_margin.toFixed(1)}% 五年線性收斂至 ${assumptions.fcf_margin_terminal}%）`}
                    >
                        <p className="text-[12.5px] text-[var(--text-meta)] mb-3 leading-relaxed">
                            利潤率不是固定一個數壓五年。起點是目前的實際利潤率，終點是三種讀法的等權平均（見下），中間線性過渡。{dataSources?.fcf_basis?.includes('未扣息') && (
                                <>另外把稅後利息加回來 —— 後面的價值橋接已經扣過淨負債，用扣息後的現金流會把槓桿扣兩次。</>
                            )}
                        </p>
                        {marginReadings && (
                            <div className="rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-3.5 mb-3">
                                <div className="text-[11.5px] font-semibold text-[var(--text-secondary)] mb-2.5">
                                    終期利潤率怎麼定的 &mdash; 五、六個年度資料點分不出「循環低谷」和「結構性轉折」，
                                    所以三種讀法等權平均，不押單一種
                                </div>
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                    <Field label="近期加權" en="往哪走" value={`${marginReadings.normalised_pct}%`} />
                                    <Field label="近三年中位" en="穩在哪" value={`${marginReadings.recent_level_pct}%`} />
                                    <Field label="六年中位" en="常態是多少" value={`${marginReadings.through_cycle_pct}%`} />
                                    <Field label="採用終期" en="＋目前值 15%"
                                           value={`${marginReadings.terminal_pct}%`} tone="var(--accent-cyan-text)" />
                                </div>
                                <div className="text-[11px] text-[var(--text-meta)] mt-2.5 pt-2.5 border-t border-[var(--glass-border)]">
                                    歷史（新→舊）
                                    <span className="font-mono ml-1">
                                        {marginReadings.history.map((h) => `${h}%`).join(' · ')}
                                    </span>
                                    {marginReadings.outliers_trimmed > 0 && (
                                        <span>
                                            {'\u3000'}（其中 {marginReadings.outliers_trimmed} 個極端年度已修剪，避免單一崩壞年決定永續假設）
                                        </span>
                                    )}
                                    {marginReadings.estimator_spread_pp > 8 && (
                                        <span className="text-[var(--accent-amber-text)] block mt-1">
                                            三種讀法落差 {marginReadings.estimator_spread_pp} 個百分點 &mdash; 終期是判斷題，不是計算題
                                        </span>
                                    )}
                                </div>
                            </div>
                        )}
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">年度</th>
                                        <th className="metrics-th">預估營收</th>
                                        <th className="metrics-th">自由現金流利潤率</th>
                                        <th className="metrics-th">自由現金流</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {projections.map((p) => (
                                        <tr key={p.year} className="metrics-tr">
                                            <td className="metrics-td-sticky text-[13px] font-semibold">第 {p.year} 年</td>
                                            <td className="metrics-td font-mono text-[var(--text-meta)]">{projMoney(p.revenue)}</td>
                                            <td className="metrics-td font-mono">{p.fcf_margin.toFixed(2)}%</td>
                                            <td className="metrics-td font-mono font-semibold">{projMoney(p.fcf)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </Step>

                    {/* 步驟 3 */}
                    <Step
                        n={3} title="把未來的錢折現回今天" en="Discount to present value"
                        formula={`折現因子 = 1 ÷ (1 + ${assumptions.effective_wacc}%)^年數　　現值 = 自由現金流 × 折現因子`}
                    >
                        <div className="overflow-x-auto styled-scrollbar">
                            <table className="metrics-table">
                                <thead>
                                    <tr>
                                        <th className="metrics-th-sticky">年度</th>
                                        <th className="metrics-th">自由現金流</th>
                                        <th className="metrics-th">折現因子</th>
                                        <th className="metrics-th">現值</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {projections.map((p) => (
                                        <tr key={p.year} className="metrics-tr">
                                            <td className="metrics-td-sticky text-[13px] font-semibold">第 {p.year} 年</td>
                                            <td className="metrics-td font-mono text-[var(--text-meta)]">{projMoney(p.fcf)}</td>
                                            <td className="metrics-td font-mono text-[var(--text-meta)]">{p.discount_factor.toFixed(4)}</td>
                                            <td className="metrics-td font-mono font-semibold">{projMoney(p.present_value)}</td>
                                        </tr>
                                    ))}
                                    <tr className="metrics-tr" style={{ background: 'var(--bg-secondary)' }}>
                                        <td className="metrics-td-sticky text-[13px] font-bold">五年現值合計</td>
                                        <td className="metrics-td" /><td className="metrics-td" />
                                        <td className="metrics-td font-mono font-bold text-[var(--accent-cyan-text)]">
                                            {projMoney(summary.total_pv_fcfs)}
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </Step>

                    {/* 步驟 4 */}
                    <Step
                        n={4} title="估算第五年以後的價值（終值）" en="Terminal value"
                        formula={`終值 = 第 5 年現金流 × (1 + 永續成長率) ÷ (折現率 − 永續成長率)`}
                    >
                        <div className="text-[12.5px] font-mono text-[var(--text-primary)] leading-loose mb-3 overflow-x-auto styled-scrollbar">
                            <div>終值 ＝ {fmt(projections[projections.length - 1].fcf, 'big')} × (1 + {assumptions.terminal_growth}%) ÷ ({assumptions.effective_wacc}% − {assumptions.terminal_growth}%) ＝ <span className="font-bold">{fmt(summary.terminal_value, 'big')}</span></div>
                            <div>終值現值 ＝ {fmt(summary.terminal_value, 'big')} × {projections[projections.length - 1].discount_factor.toFixed(4)} ＝ <span className="font-bold text-[var(--accent-cyan-text)]">{fmt(summary.pv_terminal, 'big')}</span></div>
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                            <Field label="終值佔企業價值" en="TV % of EV" value={`${summary.terminal_value_pct}%`}
                                   tone={summary.terminal_value_pct > 85 ? 'var(--accent-danger-text)' : undefined} />
                            <Field label="等於幾倍第 5 年現金流" en="Implied multiple"
                                   value={summary.implied_terminal_fcf_multiple != null ? `${summary.implied_terminal_fcf_multiple}x` : '—'} />
                            <Field label="永續成長率" en="Terminal growth" value={`${assumptions.terminal_growth}%`} />
                        </div>
                        {summary.terminal_value_pct > 85 && (
                            <p className="text-[12px] text-[var(--accent-amber-text)] mt-3 flex items-start gap-1.5">
                                <Info size={13} className="mt-0.5 shrink-0" />
                                終值佔了企業價值的 {summary.terminal_value_pct}%，代表這個估值幾乎完全建立在「第六年以後長什麼樣」的假設上。
                            </p>
                        )}
                    </Step>

                    {/* 步驟 5 */}
                    <Step
                        n={5} title="加總並換算成每股價值" en="Value bridge"
                        formula={`企業價值 = 五年現值 + 終值現值　→　股權價值 = 企業價值 + 淨現金　→　每股價值 = 股權價值 ÷ 五年後流通股數`}
                    >
                        <div className="text-[12.5px] font-mono text-[var(--text-primary)] leading-loose overflow-x-auto styled-scrollbar">
                            <div>企業價值 ＝ {fmt(summary.total_pv_fcfs, 'big')} ＋ {fmt(summary.pv_terminal, 'big')} ＝ <span className="font-bold">{fmt(summary.enterprise_value, 'big')}</span></div>
                            <div>股權價值 ＝ {fmt(summary.enterprise_value, 'big')} ＋ {fmt(summary.net_cash, 'big')}（淨現金）＝ <span className="font-bold">{fmt(summary.equity_value, 'big')}</span></div>
                            <div>五年後股數 ＝ {shares(summary.shares_outstanding)} × (1 {assumptions.share_drift_pct >= 0 ? '+' : '−'} {Math.abs(assumptions.share_drift_pct)}%)⁵ ＝ <span className="font-bold">{shares(summary.shares_diluted_yr5)}</span></div>
                            <div className="pt-1.5 mt-1.5 border-t border-[var(--glass-border)]">
                                每股內在價值 ＝ <span className="font-extrabold text-[15px]" style={{ color: meta.text }}>{fmt(sc.intrinsic_value, 'dollar')}</span>
                                <ArrowRight size={12} className="inline mx-2 text-[var(--text-muted)]" />
                                相對現價 {fmt(price, 'dollar')}
                                <span className="font-bold ml-2"
                                      style={{ color: sc.upside_pct >= 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)' }}>
                                    {fmt(sc.upside_pct, 'pct')}
                                </span>
                            </div>
                        </div>
                    </Step>

                    {/* 價值橋接圖 */}
                    {bridgeData.length > 0 && (
                        <div className="pl-11">
                            <div className="h-[220px] w-full">
                                <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0} debounce={1}>
                                    <BarChart data={bridgeData} margin={{ top: 5, right: 12, bottom: 5, left: 12 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                                        <XAxis dataKey="name" stroke="var(--text-muted)" fontSize={11}
                                               tick={{ fill: 'var(--text-meta)' }} axisLine={{ stroke: CHART_AXIS }} tickLine={false} />
                                        <YAxis stroke="var(--text-muted)" fontSize={10}
                                               tick={{ fill: 'var(--text-meta)' }} axisLine={{ stroke: CHART_AXIS }} tickLine={false}
                                               tickFormatter={bridgeMoney} />
                                        <Tooltip
                                            contentStyle={{
                                                backgroundColor: 'var(--bg-primary)', border: '1px solid var(--glass-border)',
                                                borderRadius: '10px', fontSize: '12px', boxShadow: '0 8px 32px rgba(10,11,13,0.12)',
                                            }}
                                            formatter={(val) => [bridgeMoney(val), '金額']}
                                            labelStyle={{ color: 'var(--text-meta)', marginBottom: '4px' }}
                                        />
                                        {/* 圓角要跟著正負號翻面，否則負值長條在 recharts 會畫不出來
                                            （ORCL 的股權價值是負的，整張圖曾經是空白的） */}
                                        <Bar dataKey="value">
                                            {bridgeData.map((entry, i) => (
                                                <Cell
                                                    key={i}
                                                    fill={entry.fill}
                                                    radius={entry.value >= 0 ? [6, 6, 0, 0] : [0, 0, 6, 6]}
                                                />
                                            ))}
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </div>
                    )}
                </div>
            </Section>

            {/* ══ 折現率怎麼來的 ══ */}
            {wc && (
                <Section
                    title={`${no('wacc')}折現率是怎麼算出來的`}
                    en="WACC Build-up"
                    hint="折現率是整個估值裡影響最大的單一數字。這裡不直接採用資料源給的加權平均資金成本，而是自己拆解重算，再與其取平均。"
                >
                    <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-5 gap-y-4 mb-5">
                            <Field label="貝他係數（原始）" en="Beta 5Y" value={wc.beta_raw ?? '—'} />
                            <Field label="貝他係數（收斂後）" en="Adjusted" value={wc.beta_adjusted} />
                            <Field label="無風險利率" en="Risk-free" value={`${wc.risk_free_rate}%`} />
                            <Field label="股權風險溢酬" en="ERP" value={`${wc.equity_risk_premium}%`} />
                            <Field label="規模溢酬" en="Size premium" value={`${wc.size_premium}%`} />
                            <Field label="槓桿溢酬" en="Leverage premium" value={`${wc.leverage_premium}%`} />
                            <Field label="股權成本" en="Cost of equity" value={`${wc.cost_of_equity}%`} />
                            <Field label="稅後負債成本" en="After-tax CoD" value={`${wc.after_tax_cost_of_debt}%`} />
                        </div>
                        <div className="text-[12.5px] font-mono text-[var(--text-primary)] leading-loose bg-[var(--bg-secondary)]
                                        border border-[var(--glass-border)] rounded-lg px-4 py-3 overflow-x-auto styled-scrollbar">
                            <div>股權成本 ＝ {wc.risk_free_rate}% ＋ {wc.beta_adjusted} × {wc.equity_risk_premium}% ＋ {wc.size_premium}%（規模）＋ {wc.leverage_premium}%（槓桿）＝ {wc.cost_of_equity}%</div>
                            <div>自行推算 ＝ {wc.weight_equity}% × {wc.cost_of_equity}% ＋ {wc.weight_debt}% × {wc.after_tax_cost_of_debt}% ＝ {wc.wacc_build_up}%</div>
                            {wc.wacc_scraped != null && <div>資料源數值 ＝ {wc.wacc_scraped}%</div>}
                            <div className="pt-1.5 mt-1.5 border-t border-[var(--glass-border)]">
                                採用折現率 ＝ <span className="font-bold text-[var(--accent-cyan-text)]">{assumptions.wacc}%</span>
                                <span className="text-[var(--text-meta)] ml-2">（{assumptions.wacc_source}）</span>
                            </div>
                        </div>
                        <p className="text-[12px] text-[var(--text-meta)] mt-3 leading-relaxed">
                            貝他係數會往 1 收斂（Blume 調整）—— 五年原始貝他對高波動個股會失真，直接拿去算會把折現率推到 20% 以上，估值等於自動歸零。
                            負債成本用實際利息支出除以總負債推算，不是猜的。
                        </p>
                    </div>
                </Section>
            )}

            {/* ══ 敏感度分析 ══ */}
            {sensitivity_ && (
                <Section
                    title={`${no('sensitivity')}敏感度分析`}
                    en="Sensitivity"
                    hint="假設變動一點，每股價值會變多少。以中性情境為基準，中間有青色外框的那格就是中性情境採用的假設。"
                >
                    <div>
                        <h3 className="text-[14px] font-semibold text-[var(--text-secondary)] mb-2">
                            折現率 × 永續成長率
                        </h3>
                        <SensitivityMatrix matrix={sensitivity_} currentPrice={price} corner="折現率 \ 永續成長" />
                    </div>
                    {marginGrowth && (
                        <div className="pt-2">
                            <h3 className="text-[14px] font-semibold text-[var(--text-secondary)] mb-2">
                                {marginGrowth.margin_axis || '自由現金流利潤率'} × 營收成長（相對分析師共識）
                            </h3>
                            <SensitivityMatrix
                                matrix={{
                                    wacc_labels: marginGrowth.margin_labels,
                                    tg_labels: marginGrowth.growth_labels.map((l) => {
                                        const m = l.match(/^([+-]?\d+)%/);
                                        if (!m) return l;
                                        const v = Number(m[1]);
                                        return v === 0 ? '分析師共識' : `共識 ${v > 0 ? '+' : ''}${v}%`;
                                    }),
                                    matrix: marginGrowth.matrix,
                                    base_wacc_idx: marginGrowth.base_margin_idx,
                                    base_tg_idx: marginGrowth.base_growth_idx,
                                }}
                                currentPrice={price}
                                corner="第 5 年利潤率 \ 營收成長"
                            />
                        </div>
                    )}
                </Section>
            )}

            {/* ══ 反推估值 ══ */}
            {reverseDcf && (
                <Section
                    title={`${no('reverse')}反推估值：現價已經假設了什麼`}
                    en="Reverse DCF"
                    hint="把模型倒過來跑 —— 固定利潤率與折現率，問「營收要成長多快，才會剛好等於現在的股價」。"
                >
                    <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-4">
                            <Field label="現價隱含的五年營收年複合成長率" en="Implied CAGR"
                                   value={`${reverseDcf.implied_revenue_cagr_5y}%`} tone="var(--accent-purple-text)" />
                            <Field label="分析師實際預估" en="Consensus"
                                   value={`${reverseDcf.consensus_revenue_cagr_5y}%`} />
                            <Field label="兩者差距" en="Gap"
                                   value={`${reverseDcf.gap_pp > 0 ? '+' : ''}${reverseDcf.gap_pp} 個百分點`}
                                   tone={reverseDcf.gap_pp > 0 ? 'var(--accent-success-text)' : 'var(--accent-danger-text)'} />
                            <Field label="現價隱含的終期利潤率" en="Implied terminal margin"
                                   value={`${reverseDcf.implied_terminal_fcf_margin}%${reverseDcf.implied_terminal_fcf_margin_capped ? ' 以上' : ''}（模型用 ${reverseDcf.model_terminal_fcf_margin}%）`} />
                        </div>
                        <p className="text-[13px] text-[var(--text-primary)] font-medium border-t border-[var(--glass-border)] pt-3">
                            {reverseDcf.gap_pp > 5 ? '市場價入的成長明顯低於分析師預估 —— 如果分析師是對的，這裡有預期差。'
                                : reverseDcf.gap_pp < -5 ? '市場價入的成長已經高於分析師預估 —— 現價需要超預期才撐得住。'
                                : '市場大致按分析師共識定價，沒有明顯的預期落差。'}
                        </p>
                        <p className="text-[11.5px] text-[var(--text-meta)] mt-1.5">
                            計算條件：折現率 {reverseDcf.at_wacc}%、永續成長率 {reverseDcf.at_terminal_growth}%
                        </p>
                    </div>
                </Section>
            )}

            {/* ══ 資本支出正常化對照 ══ */}
            {capexCycle && (
                <Section
                    title={`${no('capex')}資本支出正常化對照`}
                    en="Normalised Capex"
                    hint="自由現金流扣掉全部資本支出，所以蓋產能的公司看起來像在燒錢。這裡把資本支出換成維持性水準（以折舊代理），看本業真正的現金產出值多少。"
                >
                    <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-5">
                            <Field label="營業現金流佔營收" en="OCF margin" value={`${capexCycle.operating_cash_flow_pct}%`} />
                            <Field label="資本支出佔營收" en="Capex" value={`${capexCycle.capex_pct}%`}
                                   tone="var(--accent-danger-text)" />
                            <Field label="折舊攤提佔營收（維持性代理）" en="D&A" value={`${capexCycle.depreciation_pct}%`} />
                            <Field label="擴產投資（差額）" en="Growth capex"
                                   value={`${capexCycle.growth_capex_pct} 個百分點`} tone="var(--accent-amber-text)" />
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-4 border-t border-[var(--glass-border)]">
                            <div>
                                <div className="text-[11px] text-[var(--text-meta)] mb-1">
                                    模型採用（扣全部資本支出）<span className="opacity-60 ml-1">As modelled</span>
                                </div>
                                <div className="text-[22px] font-extrabold font-mono">{fmt(sc.intrinsic_value, 'dollar')}</div>
                                <div className="text-[11.5px] text-[var(--text-meta)] mt-0.5">
                                    終期利潤率 {capexCycle.model_terminal_fcf_margin}%
                                </div>
                            </div>
                            <div>
                                <div className="text-[11px] text-[var(--text-meta)] mb-1">
                                    資本支出回到折舊水準<span className="opacity-60 ml-1">Maintenance capex</span>
                                </div>
                                <div className="text-[22px] font-extrabold font-mono text-[var(--accent-amber-text)]">
                                    {fmt(capexCycle.intrinsic_value, 'dollar')}
                                    <span className="text-[14px] ml-2">{fmt(capexCycle.upside_pct, 'pct0')}</span>
                                </div>
                                <div className="text-[11.5px] text-[var(--text-meta)] mt-0.5">
                                    終期利潤率 {capexCycle.normalised_fcf_margin}%（營業現金流 − 折舊）
                                </div>
                            </div>
                        </div>
                        <p className="text-[12px] text-[var(--text-meta)] mt-4 leading-relaxed">
                            折舊只是維持性資本支出的粗略代理 —— 對資產基礎還在擴張的公司會低估維持成本，
                            所以正常化後的數字是上緣，不是目標價。真正的問題是「這輪投資什麼時候結束、投出來的產能報酬率多少」，
                            那不是折現模型能回答的。
                        </p>
                    </div>
                </Section>
            )}

            {/* ══ 終值交叉驗證 ══ */}
            {terminalCheck && (
                <Section
                    title={`${no('terminal')}終值交叉驗證`}
                    en="Terminal Cross-check"
                    hint="用永續成長模型算出的終值，換算成「幾倍現金流」，跟這家公司自己過去五年的實際交易倍數比對（以中性情境為基準）。"
                >
                    <div className="card card-flat !p-6" style={{ transform: 'none' }}>
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-4">
                            <Field label="永續成長模型算出" en="Gordon growth" value={fmt(baseScenario.intrinsic_value, 'dollar')} />
                            <Field label="改用歷史倍數算出" en="Exit multiple" value={fmt(terminalCheck.intrinsic_value, 'dollar')} />
                            <Field label="永續成長模型隱含的倍數" en="Implied multiple"
                                   value={terminalCheck.implied_gordon_multiple != null ? `${terminalCheck.implied_gordon_multiple}x` : '—'} />
                            <Field label="公司自己五年中位數 EV/FCF" en="Historical median"
                                   value={`${terminalCheck.historical_ev_fcf_median}x（實際採用 ${terminalCheck.applied_multiple}x）`} />
                        </div>
                        <p className="text-[13px] text-[var(--text-primary)] font-medium border-t border-[var(--glass-border)] pt-3">
                            {terminalCheck.divergence_vs_gordon_pct != null && Math.abs(terminalCheck.divergence_vs_gordon_pct) > 40
                                ? `兩種算法差了 ${terminalCheck.divergence_vs_gordon_pct > 0 ? '+' : ''}${terminalCheck.divergence_vs_gordon_pct}% —— 終值假設就是這檔估值的最大變數。`
                                : '兩種算法結果接近，終值假設沒有明顯偏誤。'}
                        </p>
                    </div>
                </Section>
            )}

            {/* ══ 資料可信度與來源 ══ */}
            {(confidence || dataSources) && (
                <Section
                    title={`${no('confidence')}這個估值有多可信`}
                    en="Confidence & Provenance"
                    hint="模型的每個輸入，是實際量到的資料，還是假設出來的。"
                >
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {confidence && (
                            <div className="card card-flat !p-5" style={{ transform: 'none' }}>
                                <div className="flex items-baseline justify-between mb-1">
                                    <span className="text-[13px] font-semibold text-[var(--text-secondary)]">綜合評分</span>
                                    <span className="text-[26px] font-extrabold font-mono" style={{ color: CONF_TONE(confidence.score) }}>
                                        {confidence.score}
                                        <span className="text-[15px] ml-1">{confidence.grade}</span>
                                    </span>
                                </div>
                                <p className="text-[12.5px] text-[var(--text-meta)] mb-4">{confidence.summary}</p>
                                <div className="space-y-2.5">
                                    {confidence.factors.map((f) => (
                                        <div key={f.name}>
                                            <div className="flex justify-between items-baseline text-[11.5px] mb-1">
                                                <span className="text-[var(--text-secondary)] font-medium">{f.name}</span>
                                                <span className="font-mono text-[var(--text-meta)]">{f.score}</span>
                                            </div>
                                            <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
                                                <div className="h-full rounded-full transition-all duration-300"
                                                     style={{ width: `${Math.max(0, Math.min(100, f.score))}%`, background: CONF_TONE(f.score) }} />
                                            </div>
                                            <div className="text-[10.5px] text-[var(--text-muted)] mt-0.5">{f.note}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                        {dataSources && (
                            <div className="card card-flat !p-5 space-y-4" style={{ transform: 'none' }}>
                                <span className="text-[13px] font-semibold text-[var(--text-secondary)] block">資料來源</span>
                                <Field label="營收預估錨定" en="Revenue anchor" value={dataSources.revenue_anchor} />
                                <Field label="現金流利潤率基準" en="Margin basis" value={dataSources.margin_basis} />
                                <Field label="現金流口徑" en="FCF basis" value={dataSources.fcf_basis} />
                                <Field label="折現率來源" en="WACC basis" value={dataSources.wacc_basis} />
                                <Field label="股價來源" en="Price source" value={dataSources.price_source} />
                                <div>
                                    <div className="text-[11px] text-[var(--text-meta)] mb-1">各資料源距今天數 <span className="opacity-60">Source age</span></div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {Object.entries(dataSources.age_days || {}).map(([k, v]) => (
                                            <span key={k} className="text-[11px] font-mono px-2 py-0.5 rounded-md border"
                                                  style={{
                                                      background: 'var(--bg-secondary)',
                                                      borderColor: v == null || v > 120 ? 'rgba(217,119,6,0.35)' : 'var(--glass-border)',
                                                      color: v == null || v > 120 ? 'var(--accent-amber-text)' : 'var(--text-meta)',
                                                  }}>
                                                {{ statistics: '統計快照', forecast: '分析師預估', income: '損益表',
                                                   cashflow: '現金流量表', ratios: '財務比率' }[k] || k} {v == null ? '缺' : `${v}天`}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                </Section>
            )}

            {/* ══ 計算備註 ══ */}
            {warnings && warnings.length > 0 && (
                <Section title="計算備註" en="Model Notes">
                    <div className="card card-flat !p-5 !border-[rgba(217,119,6,0.28)]" style={{ transform: 'none' }}>
                        <div className="flex items-center gap-2 mb-3">
                            <AlertTriangle size={15} className="text-[var(--accent-amber)]" />
                            <span className="text-[13px] font-semibold text-[var(--accent-amber-text)]">
                                模型在計算過程中標記了 {warnings.length} 項
                            </span>
                        </div>
                        <ul className="space-y-1.5">
                            {warnings.map((w, i) => (
                                <li key={i} className="text-[12.5px] text-[var(--text-meta)] flex items-start gap-2 leading-relaxed">
                                    <span className="text-[var(--accent-amber)] mt-0.5 shrink-0">•</span>{w}
                                </li>
                            ))}
                        </ul>
                    </div>
                </Section>
            )}
        </div>
    );
}
