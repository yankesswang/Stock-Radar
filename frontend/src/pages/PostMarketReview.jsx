/**
 * PostMarketReview — 盤後復盤（圖表化）
 *
 * 資料全部來自 /api/war-room/review/*（純讀檔 API，零 yfinance）：
 *   journal（當日 DECISIONS）+ ledger sidecar（未結追蹤/30天戰績）
 *   + 完整六區塊報告 markdown（鏡像落地）+ 當日機會掃描
 *   + ai_cards（05:00 預產的逐檔 AI 卡 —— 復盤的證據層，決策列旁逐檔對照；
 *     2026-09-03 起「AI 持倉」不再是獨立分頁，全部併進復盤）。
 *
 * 視覺與 WarRoom.jsx 同家族（自帶 Palantir ops-center 暗色系，不吃 app 淺色主題）。
 * 色彩皆為語意色（多方=teal、防禦=red、警戒=amber），且一律搭配文字徽章／標籤，
 * 不做 color-alone 編碼（validate_palette：CVD 分離與對比 PASS）。
 *
 * 頁面分頁（總覽／決策全表／機會掃描／完整報告）取代單一長捲動，並把報告的
 * 第一層＋第二層（90 秒晨報＋異常持倉，即讀者最該先看的內容）拉到總覽最上方，
 * 第三層附錄（逐檔全文／帳本／稽核明細）收進「完整報告」分頁，不再預設全展開。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from '../lib/apiClient';
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Cell, ReferenceLine, LabelList,
} from 'recharts';
import {
  ClipboardCheck, ShieldCheck, ShieldAlert, Scale, RefreshCw,
  ChevronLeft, ChevronRight, ChevronDown, Telescope, FileText, Activity,
  LayoutDashboard, ListChecks, Crosshair, BookOpen, Search, Flame, Sparkles,
} from 'lucide-react';
import { C, alpha, actionMeta, RISK_META, fmt, signPct, mmdd } from '../components/opsTheme';
import { Panel, Badge, Empty, FilterChip } from '../components/opsUi';
import { clean as cleanMd } from '../components/markdownText';
import AttentionPositionsPanel from '../components/AttentionPositions';
import { splitBriefSections, normalizeTickerBlocks } from '../components/warRoomAttention';
import { mergeDecisionsWithCards } from '../components/reviewMerge';
import ClusterTabs from '../components/ClusterTabs';
import OpsProse from '../components/OpsMarkdown';
import OpsDatePicker from '../components/OpsDatePicker';
import MorningBriefCards from '../components/MorningBriefCards';
import { parseMorningBrief } from '../components/morningBrief';
import { OPS_TABS } from '../components/clusterTabsDef';

// key 是 API 的 enum，label 一律中文（同 scripts/zh_terms.py 的對照）
const REGIME = {
  'risk-on': { label: '風險偏好', color: C.long, Icon: ShieldCheck },
  risk_on: { label: '風險偏好', color: C.long, Icon: ShieldCheck },
  'risk-off': { label: '避險', color: C.short, Icon: ShieldAlert },
  risk_off: { label: '避險', color: C.short, Icon: ShieldAlert },
  mixed: { label: '多空分歧', color: C.amber, Icon: Scale },
};

const TOOLTIP_STYLE = {
  backgroundColor: C.bg,
  border: `1px solid ${C.border}`,
  borderRadius: 8,
  fontSize: 12,
  color: C.sub,
};

// 深色 markdown 閱讀器抽到 components/OpsMarkdown.jsx（戰情室的市場日報共用）
const Prose = OpsProse;

// ── 統計磚 ────────────────────────────────────────────────────────────────────
function StatTile({ label, value, sub, color = C.text }) {
  return (
    <div className="relative border p-3" style={{ background: C.panel, borderColor: C.border }}>
      <div className="font-mono text-[10px] uppercase tracking-widest" style={{ color: C.muted }}>{label}</div>
      <div className="mt-1 font-mono text-2xl font-bold" style={{ color }}>{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[11px]" style={{ color: C.muted }}>{sub}</div>}
    </div>
  );
}

// ── 分頁列 ────────────────────────────────────────────────────────────────────
const TABS = [
  { id: 'overview', label: '總覽', icon: LayoutDashboard },
  { id: 'decisions', label: '決策全表', icon: ListChecks },
  { id: 'opportunities', label: '機會掃描', icon: Crosshair },
  { id: 'report', label: '完整報告', icon: BookOpen },
];

function TabBar({ tab, setTab, counts }) {
  return (
    <div className="flex flex-wrap items-center gap-1 border-b" style={{ borderColor: C.border }}>
      {TABS.map((t) => {
        const Icon = t.icon;
        const active = tab === t.id;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className="flex cursor-pointer items-center gap-1.5 border-b-2 px-3 py-2 font-mono text-[12px] font-bold uppercase tracking-wider transition-colors duration-200"
            style={{ borderColor: active ? C.accent : 'transparent', color: active ? C.accent : C.muted }}
          >
            <Icon size={13} />
            {t.label}
            {counts?.[t.id] != null && (
              <span
                className="rounded-full px-1.5 py-0.5 font-mono text-[10px]"
                style={{ background: active ? `${alpha(C.accent, 0.133)}` : alpha(C.ink, 0.06), color: active ? C.accent : C.muted }}
              >
                {counts[t.id]}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── 今日焦點（結構化資料組出的「一眼看重點」卡，取代靠 markdown 硬讀）───────────
// 直接從 journal.decisions / ledger.open_decisions 分桶，而不是從報告全文裡抓
// 字串 —— 同樣的重點，用大字級＋色塊呈現，不必再讀一段 90 秒晨報才找得到。
const FOCUS_BUCKETS = [
  { key: 'triggered', title: '已觸發待回應', color: C.short },
  { key: 'stop', title: '停損／出場', color: C.short },
  { key: 'highRisk', title: '高風險持有', color: C.amber },
  { key: 'trim', title: '減碼', color: C.amber },
  { key: 'addNew', title: '加碼／新倉', color: C.long },
];

function TodayFocusPanel({ decisions, openRows }) {
  const buckets = useMemo(() => {
    const noteOf = (d) => d.trigger || d.thesis || '';
    const triggered = (openRows || [])
      .filter((r) => r.status === 'triggered')
      .map((r) => ({ ticker: r.ticker, note: `${r.direction === 'above' ? '漲破' : '跌破'} ${r.trigger_price}（收 ${r.last_close}）` }));
    const stop = decisions
      .filter((d) => d.action === 'stop_loss' || d.action === 'exit')
      .map((d) => ({ ticker: d.ticker, note: noteOf(d) }));
    const trim = decisions
      .filter((d) => d.action === 'trim')
      .map((d) => ({ ticker: d.ticker, note: noteOf(d) }));
    const addNew = decisions
      .filter((d) => ['add', 'open', 'buy', 'accumulate'].includes(d.action))
      .map((d) => ({ ticker: d.ticker, note: noteOf(d) }));
    const highRisk = decisions
      .filter((d) => d.risk_level === 'high' && !['stop_loss', 'exit', 'trim'].includes(d.action))
      .map((d) => ({ ticker: d.ticker, note: noteOf(d) }));
    return { triggered, stop, trim, addNew, highRisk };
  }, [decisions, openRows]);

  const cards = FOCUS_BUCKETS.map((b) => ({ ...b, items: buckets[b.key] || [] })).filter((c) => c.items.length);

  return (
    <Panel
      label="今日焦點"
      status={<Badge color={C.accent}><Flame size={11} className="mr-1 inline" />一眼看重點</Badge>}
    >
      {!cards.length ? (
        <Empty>今日沒有需要優先處理的操作（無停損／減碼／新倉／高風險項目）</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {cards.map((c) => (
            <div key={c.key} className="border-l-4 p-3" style={{ borderColor: c.color, background: `${alpha(c.color, 0.051)}`, borderTop: `1px solid ${C.border}`, borderRight: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border}` }}>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-3xl font-bold tabular-nums" style={{ color: c.color }}>{c.items.length}</span>
                <span className="font-mono text-[12px] font-bold uppercase tracking-wider" style={{ color: C.text }}>{c.title}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {c.items.map((it) => (
                  <span
                    key={it.ticker}
                    className="inline-flex items-center gap-1 border px-2 py-1 font-mono text-[12.5px] font-bold"
                    style={{ borderColor: `${alpha(c.color, 0.333)}`, color: C.text, background: `${alpha(c.color, 0.078)}` }}
                    title={it.note}
                  >
                    {it.ticker}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ── 90 秒晨報（報告標頭＋第一層；第二層「異常持倉」改由 AttentionPositionsPanel 結構化呈現）──
// 2026-09-07 起解析成卡片（components/morningBrief.js + MorningBriefCards.jsx），跟異常持倉同一套
// 視覺；舊格式解析不出「今天需要處理／等待條件／最大風險」任一段才退回 markdown。
function MorningBriefPanel({ md }) {
  const brief = useMemo(() => (md ? parseMorningBrief(md) : null), [md]);
  if (!md) return null;
  const cards = brief?.ok;
  return (
    <Panel
      label="90 秒晨報"
      status={
        <Badge color={C.accent}>
          <FileText size={11} className="mr-1 inline" />
          {cards ? '一句話・今天要做・等待條件・最大風險' : '今天要做・等待條件・最大風險'}
        </Badge>
      }
    >
      {cards ? (
        <>
          <MorningBriefCards brief={brief} />
          {brief.unparsed.length > 0 && (
            <div className="mt-4 border-t pt-3" style={{ borderColor: C.border }}>
              <Prose>{cleanMd(brief.unparsed.map((u) => `### ${u.title}\n${u.md}`).join('\n\n'))}</Prose>
            </div>
          )}
        </>
      ) : (
        <Prose>{cleanMd(md)}</Prose>
      )}
    </Panel>
  );
}

// ── 決策品質曲線（trend：觸發率 + 決策期間平均漲跌，兩張小圖，各一軸）──────────
function TrendPanel({ trend }) {
  const rows = useMemo(
    () => (trend || []).filter((r) => r.scored_n).map((r) => ({ ...r, d: mmdd(r.date) })),
    [trend],
  );
  if (rows.length < 2) return <Panel label="復盤品質曲線"><Empty>累積兩份以上計分卡後開始畫曲線</Empty></Panel>;
  return (
    <Panel label="復盤品質曲線" status={<Badge color={C.accent}>{rows.length} 份計分卡</Badge>}>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <div className="mb-1 font-mono text-[11px]" style={{ color: C.sub }}>
            觸發率 %　<span style={{ color: C.muted }}>— 決策附帶的價格條件隔日實際觸發比例</span>
          </div>
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
              <XAxis dataKey="d" tick={{ fill: C.muted, fontSize: 11 }} axisLine={{ stroke: C.border }} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fill: C.muted, fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${v}%`, '觸發率']} labelStyle={{ color: C.muted }} />
              <Line type="monotone" dataKey="trigger_rate" stroke={C.accent} strokeWidth={2} dot={{ r: 3, fill: C.accent, strokeWidth: 0 }} activeDot={{ r: 5 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="mb-1 font-mono text-[11px]" style={{ color: C.sub }}>
            決策期間平均漲跌 %　<span style={{ color: C.muted }}>— 上一份決策日 → 計分日，持倉標的平均</span>
          </div>
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
              <XAxis dataKey="d" tick={{ fill: C.muted, fontSize: 11 }} axisLine={{ stroke: C.border }} tickLine={false} />
              <YAxis tick={{ fill: C.muted, fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [signPct(v), '平均漲跌']} labelStyle={{ color: C.muted }} />
              <ReferenceLine y={0} stroke="rgba(148,163,184,0.4)" />
              <Bar dataKey="avg_change_pct" radius={[3, 3, 0, 0]} maxBarSize={22}>
                {rows.map((r) => (
                  <Cell key={r.date} fill={(r.avg_change_pct ?? 0) >= 0 ? C.long : C.short} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </Panel>
  );
}

// ── 30 天戰績（per-action 前瞻報酬 bar）───────────────────────────────────────
function TrackRecordPanel({ tr }) {
  const rows = useMemo(
    () => (tr?.per_action || []).map((r) => ({ ...r, name: actionMeta(r.action).label })),
    [tr],
  );
  if (!rows.length) {
    return <Panel label="30 天決策戰績"><Empty>帳本尚無足夠決策樣本</Empty></Panel>;
  }
  const best = tr.best_call;
  const worst = tr.worst_call;
  return (
    <Panel
      label={`${tr.window_days} 天決策戰績`}
      status={<Badge color={C.accent}>{tr.n_total} 筆・{tr.fwd_trading_days} 日前瞻</Badge>}
    >
      <ResponsiveContainer width="100%" height={190}>
        <BarChart data={rows} margin={{ top: 18, right: 12, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: C.sub, fontSize: 11 }} axisLine={{ stroke: C.border }} tickLine={false} />
          <YAxis tick={{ fill: C.muted, fontSize: 11 }} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: C.muted }}
            formatter={(v, _n, item) => [`${signPct(v)}（${item?.payload?.count} 筆）`, '標的 5 日平均']}
          />
          <ReferenceLine y={0} stroke="rgba(148,163,184,0.4)" />
          <Bar dataKey="avg_fwd_pct" radius={[3, 3, 0, 0]} maxBarSize={34}>
            <LabelList dataKey="count" position="top" formatter={(v) => `n=${v}`} style={{ fill: C.muted, fontSize: 10, fontFamily: 'monospace' }} />
            {rows.map((r) => (
              <Cell key={r.action} fill={(r.avg_fwd_pct ?? 0) >= 0 ? C.long : C.short} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-1 font-mono text-[11px] leading-relaxed" style={{ color: C.muted }}>
        看多類跑贏 SPY＝call 對；防禦類標的隨後跑輸＝躲對了
      </div>
      {(best || worst) && (
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {best && (
            <div className="border px-2.5 py-1.5 font-mono text-[11.5px]" style={{ borderColor: `${alpha(C.long, 0.267)}`, color: C.sub }}>
              🏆 <span style={{ color: C.long }}>{best.ticker} {actionMeta(best.action).label}</span>
              （{mmdd(best.date)}）5日 {signPct(best.fwd_pct)}，vs SPY {signPct(best.alpha_pct)}
            </div>
          )}
          {worst && (
            <div className="border px-2.5 py-1.5 font-mono text-[11.5px]" style={{ borderColor: `${alpha(C.short, 0.267)}`, color: C.sub }}>
              💀 <span style={{ color: C.short }}>{worst.ticker} {actionMeta(worst.action).label}</span>
              （{mmdd(worst.date)}）5日 {signPct(worst.fwd_pct)}，vs SPY {signPct(worst.alpha_pct)}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

// ── 未結決策追蹤（觸發/接近觸發優先展開；其餘收進可展開的密集清單）────────────
function OpenDecisionRow({ r }) {
  const am = actionMeta(r.action);
  const arrow = r.direction === 'above' ? '漲破' : '跌破';
  const near = r.distance_pct !== null && Math.abs(r.distance_pct) < 3;
  const barPct = r.distance_pct === null ? 0 : Math.min(Math.abs(r.distance_pct), 15) / 15 * 100;
  const barColor = near ? C.short : Math.abs(r.distance_pct ?? 99) < 8 ? C.amber : C.muted;
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border px-2.5 py-1.5"
      style={{ borderColor: r.status === 'triggered' ? `${alpha(C.short, 0.333)}` : C.border, background: alpha(C.ink, 0.02) }}
      title={r.thesis || ''}
    >
      <span className="w-14 font-mono text-[13px] font-bold" style={{ color: C.text }}>{r.ticker}</span>
      <Badge color={am.color}>{am.label}</Badge>
      <span className="font-mono text-[12px]" style={{ color: C.sub }}>
        {arrow} {r.trigger_price}
      </span>
      <span className="font-mono text-[11px]" style={{ color: C.muted }}>
        {mmdd(r.proposed_date)} 提出・{r.streak} 份日誌
      </span>
      <span className="ml-auto flex items-center gap-2">
        {r.status === 'triggered' && (
          <Badge color={C.short}>✅ {mmdd(r.hit_date)} 已觸發（收 {r.last_close}）</Badge>
        )}
        {r.status === 'pending' && r.distance_pct !== null && (
          <>
            <span className="hidden h-1.5 w-24 overflow-hidden rounded sm:block" style={{ background: alpha(C.ink, 0.06) }}>
              <span className="block h-full rounded" style={{ width: `${100 - barPct}%`, background: barColor, opacity: 0.85 }} />
            </span>
            <span className="font-mono text-[12px]" style={{ color: barColor }}>
              距 {signPct(r.distance_pct)}
            </span>
          </>
        )}
        {r.status === 'pending' && r.distance_pct === null && (
          <span className="font-mono text-[11px]" style={{ color: C.muted }}>⏳ 尚無後續交易日</span>
        )}
        {r.status === 'no_data' && (
          <span className="font-mono text-[11px]" style={{ color: C.muted }}>⚠️ 無價格資料</span>
        )}
      </span>
    </div>
  );
}

function OpenDecisionsPanel({ rows }) {
  const [showRest, setShowRest] = useState(false);
  const { urgent, rest } = useMemo(() => {
    const rank = (r) => (r.status === 'triggered' ? 0 : r.status === 'pending' && r.distance_pct !== null ? 1 : 2);
    const sorted = [...(rows || [])].sort((a, b) => {
      const d = rank(a) - rank(b);
      if (d) return d;
      return Math.abs(a.distance_pct ?? 99) - Math.abs(b.distance_pct ?? 99);
    });
    // Urgent = already triggered, or pending with a known distance under 8% —
    // everything else (no data yet / far away) is noise 90% of the time and
    // gets folded into a collapsed strip instead of one full-height row each.
    const isUrgent = (r) => r.status === 'triggered'
      || (r.status === 'pending' && r.distance_pct !== null && Math.abs(r.distance_pct) < 8);
    return { urgent: sorted.filter(isUrgent), rest: sorted.filter((r) => !isUrgent(r)) };
  }, [rows]);
  const nTriggered = urgent.filter((r) => r.status === 'triggered').length;

  return (
    <Panel
      label="未結決策追蹤"
      status={nTriggered
        ? <Badge color={C.short}>✅ {nTriggered} 筆已觸發待回應</Badge>
        : <Badge color={C.long}>無已觸發項目</Badge>}
    >
      {!rows?.length ? (
        <Empty>近 10 天沒有帶價格條件的未結決策</Empty>
      ) : (
        <div className="space-y-1.5">
          {urgent.length
            ? urgent.map((r) => <OpenDecisionRow key={`${r.ticker}-${r.proposed_date}`} r={r} />)
            : <Empty>目前沒有已觸發或接近觸發（距離 &lt;8%）的項目</Empty>}

          {rest.length > 0 && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setShowRest((v) => !v)}
                className="flex w-full cursor-pointer items-center gap-1.5 border px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors duration-200"
                style={{ borderColor: C.border, color: C.muted, background: alpha(C.ink, 0.015) }}
              >
                <ChevronDown size={12} style={{ transform: showRest ? 'rotate(180deg)' : 'none', transition: 'transform 200ms' }} />
                {showRest ? '收起' : `其餘 ${rest.length} 筆（距離較遠／尚無資料，可展開）`}
              </button>
              {showRest && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {rest.map((r) => {
                    const am = actionMeta(r.action);
                    return (
                      <span
                        key={`${r.ticker}-${r.proposed_date}`}
                        className="inline-flex items-center gap-1.5 border px-2 py-1 font-mono text-[11px]"
                        style={{ borderColor: C.border, color: C.sub }}
                        title={r.thesis || ''}
                      >
                        <span className="font-bold" style={{ color: C.text }}>{r.ticker}</span>
                        <Badge color={am.color}>{am.label}</Badge>
                        {r.distance_pct !== null
                          ? <span style={{ color: C.muted }}>距 {signPct(r.distance_pct)}</span>
                          : <span style={{ color: C.muted }}>{r.status === 'no_data' ? '無資料' : '尚無交易日'}</span>}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

// ── 當日決策（依組合分群＋可搜尋；論點不截斷，避免只能靠 hover 才看得到全文）──
// 每一列右側掛同一檔的 AI 卡（評分／評級／建議）；方向與 War Room 相反的標「反向」。
const AI_RATING_COLOR = { '強烈看多': C.long, '偏多': C.long, '中性': C.accent, '偏空': C.short, '強烈看空': C.short };

function AiCardChip({ row }) {
  const c = row?.card;
  if (!c) return null;
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px]" style={{ color: C.dim }} title="05:00 預產的 AI 卡（證據層，非決策）">
      <span className="uppercase tracking-wider">AI</span>
      <span className="font-bold" style={{ color: C.text }}>{c.score ?? '—'}</span>
      {c.rating && <span style={{ color: AI_RATING_COLOR[c.rating] || C.accent }}>{c.rating}</span>}
      {c.action && <span style={{ color: C.sub }}>{c.action}</span>}
      {row.divergence === 'opposite' && <Badge color={C.short}>反向</Badge>}
      {row.divergence === 'lean' && <Badge color={C.muted}>不同調</Badge>}
    </span>
  );
}

function DecisionsPanel({ journal, rows }) {
  const decisions = useMemo(() => journal?.decisions || [], [journal]);
  const rowBy = useMemo(() => new Map((rows || []).map((r) => [r.ticker, r])), [rows]);
  const [onlyDiffer, setOnlyDiffer] = useState(false);
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState('all');

  const groups = useMemo(() => {
    const seen = [];
    for (const d of decisions) {
      const p = d.portfolio || '其他';
      if (!seen.includes(p)) seen.push(p);
    }
    return seen;
  }, [decisions]);

  const counts = useMemo(() => {
    const m = {};
    for (const d of decisions) {
      const p = d.portfolio || '其他';
      m[p] = (m[p] || 0) + 1;
    }
    return m;
  }, [decisions]);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    return decisions.filter((d) => {
      if (group !== 'all' && (d.portfolio || '其他') !== group) return false;
      if (q && !(d.ticker || '').toUpperCase().includes(q)) return false;
      if (onlyDiffer && !rowBy.get(String(d.ticker || '').toUpperCase())?.divergence) return false;
      return true;
    });
  }, [decisions, query, group, onlyDiffer, rowBy]);
  const nDiffer = useMemo(
    () => decisions.filter((d) => rowBy.get(String(d.ticker || '').toUpperCase())?.divergence).length,
    [decisions, rowBy],
  );

  if (!decisions.length) {
    return <Panel label="當日決策"><Empty>該日 journal 無決策（可能為管線失敗日）</Empty></Panel>;
  }

  return (
    <Panel label="當日決策" status={<Badge color={C.accent}>{decisions.length} 筆</Badge>}>
      <div className="mb-3 flex flex-wrap items-center gap-1.5 border-b pb-3" style={{ borderColor: C.border }}>
        <FilterChip active={group === 'all'} onClick={() => setGroup('all')}>ALL {decisions.length}</FilterChip>
        {groups.map((g) => (
          <FilterChip key={g} active={group === g} onClick={() => setGroup(group === g ? 'all' : g)}>
            {g} {counts[g]}
          </FilterChip>
        ))}
        {nDiffer > 0 && (
          <FilterChip active={onlyDiffer} onClick={() => setOnlyDiffer((v) => !v)}>
            AI 反向／不同調 {nDiffer}
          </FilterChip>
        )}
        <div
          className="ml-auto flex items-center gap-1.5 border px-2 py-1"
          style={{ borderColor: C.border, background: C.deep }}
        >
          <Search size={11} style={{ color: C.muted }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="篩選代號…"
            spellCheck={false}
            className="w-32 bg-transparent font-mono text-[13px] uppercase tracking-wider outline-none"
            style={{ color: C.text }}
          />
        </div>
      </div>

      {!filtered.length ? (
        <Empty>無符合條件</Empty>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((d, i) => {
            const am = actionMeta(d.action);
            const rm = RISK_META[d.risk_level] || { label: '—', color: C.muted };
            return (
              <div
                key={`${d.ticker}-${i}`}
                className="border px-2.5 py-2"
                style={{ borderColor: C.border, background: alpha(C.ink, 0.015) }}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[13.5px] font-bold" style={{ color: C.text }}>{d.ticker}</span>
                  {d.portfolio && <Badge color={C.muted}>{d.portfolio}</Badge>}
                  <Badge color={am.color}>{am.label}</Badge>
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full" style={{ background: rm.color }} />
                    <span className="font-mono text-[11px]" style={{ color: rm.color }}>{rm.label}</span>
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="inline-block h-1.5 w-14 overflow-hidden rounded align-middle" style={{ background: alpha(C.ink, 0.08) }}>
                      <span className="block h-full rounded" style={{ width: `${Math.round((d.confidence ?? 0) * 100)}%`, background: C.accent }} />
                    </span>
                    <span className="font-mono text-[11px]" style={{ color: C.muted }}>{fmt((d.confidence ?? 0) * 100, 0)}%</span>
                  </span>
                  {(d.trigger || d.stop) && (
                    <span className="font-mono text-[11.5px] sm:ml-auto" style={{ color: C.sub }}>
                      {d.trigger && <>觸發 {d.trigger}</>}
                      {d.trigger && d.stop && '　'}
                      {d.stop && <>停損 {d.stop}</>}
                    </span>
                  )}
                  <AiCardChip row={rowBy.get(String(d.ticker || '').toUpperCase())} />
                </div>
                {d.thesis && (
                  <div className="mt-1 text-[12.5px] leading-relaxed" style={{ color: C.muted }}>{d.thesis}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

// ── 當日機會掃描 ──────────────────────────────────────────────────────────────
function OpportunitiesPanel({ opp }) {
  if (!opp) return null;
  const rows = opp.opportunities || [];
  const nNew = rows.filter((o) => o.is_new).length;
  return (
    <Panel
      label="機會掃描"
      status={nNew ? <Badge color={C.mimi}>🆕 {nNew} 檔首次入圍</Badge> : <Badge color={C.muted}>{rows.length} 檔入圍</Badge>}
    >
      {!rows.length ? (
        <Empty>今日掃描無入圍標的</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {rows.map((o) => {
            const am = actionMeta(o.action);
            const ez = Array.isArray(o.entry_zone) && o.entry_zone.length === 2 ? `$${o.entry_zone[0]}-${o.entry_zone[1]}` : '—';
            return (
              <div key={o.ticker} className="border px-2.5 py-2" style={{ borderColor: o.is_new ? `${alpha(C.mimi, 0.4)}` : C.border, background: alpha(C.ink, 0.02) }}>
                <div className="flex items-center gap-2">
                  {o.is_new && <Badge color={C.mimi}>🆕</Badge>}
                  <span className="font-mono text-[13px] font-bold" style={{ color: C.text }}>{o.ticker}</span>
                  <Badge color={am.color}>{am.label}</Badge>
                  <span className="ml-auto font-mono text-[11px]" style={{ color: C.muted }}>
                    conv {fmt(o.conviction, 2)}｜RR {fmt(o.rr, 1)}
                  </span>
                </div>
                <div className="mt-1 font-mono text-[11.5px]" style={{ color: C.sub }}>
                  進場 {ez}｜目標 {o.target ?? '—'}｜停損 {o.stop ?? '—'}
                </div>
                {o.thesis && <div className="mt-1 text-[12px]" style={{ color: C.muted }}>{o.thesis}</div>}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PostMarketReview() {
  const [dates, setDates] = useState([]);
  const [date, setDate] = useState(null);
  const [data, setData] = useState(null);
  const [trend, setTrend] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState('overview');
  // 05:00 預產的逐檔 AI 卡可以手動補跑；War Room 本體是 05:30 的管線，
  // 沒有手動端點（補跑要下 `uv run python scripts/war_room.py`）。
  const [genStatus, setGenStatus] = useState('idle');
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);

  useEffect(() => {
    axios.get('/api/war-room/review/dates')
      .then((r) => {
        const ds = r.data?.dates || [];
        setDates(ds);
        if (ds.length) setDate((cur) => cur || ds[0]);
        else { setLoading(false); setError('尚無任何復盤資料'); }
      })
      .catch((e) => { setLoading(false); setError(String(e?.message || e)); });
    axios.get('/api/war-room/review/trend')
      .then((r) => setTrend(r.data?.trend || []))
      .catch(() => {});
  }, []);

  const fetchReview = useCallback((d) => {
    if (!d) return;
    setLoading(true);
    setError(null);
    axios.get(`/api/war-room/review/${d}`)
      .then((r) => setData(r.data))
      .catch((e) => { setData(null); setError(e?.response?.status === 404 ? `${d} 沒有復盤資料` : String(e?.message || e)); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchReview(date); }, [date, fetchReview]);

  const regenerateCards = useCallback(async () => {
    setGenStatus('running');
    setError(null);
    try {
      await axios.post('/api/scheduler/ai-report/run');
      pollRef.current = setInterval(async () => {
        try {
          const { data: st } = await axios.get('/api/scheduler/status');
          if (st.ai_report?.status === 'running') return;
          clearInterval(pollRef.current);
          setGenStatus('idle');
          const ds = (await axios.get('/api/war-room/review/dates')).data?.dates || [];
          setDates(ds);
          // 卡片是掛在最新一天的；產完就把畫面帶過去，否則按了像沒反應
          if (ds[0] && ds[0] !== date) setDate(ds[0]);
          else fetchReview(date);
        } catch { clearInterval(pollRef.current); setGenStatus('idle'); }
      }, 5000);
    } catch (e) {
      setGenStatus('idle');
      setError(e.response?.data?.detail || '觸發失敗');
    }
  }, [date, fetchReview]);

  // Jump back to the overview tab whenever the user picks a different date —
  // folded into the date setter itself instead of a second effect.
  const selectDate = useCallback((d) => { setDate(d); setTab('overview'); }, []);

  const idx = dates.indexOf(date);
  const journal = data?.journal;
  const ledger = data?.ledger;
  const regime = REGIME[journal?.market_regime] || null;
  const decisions = journal?.decisions || [];
  const openRows = ledger?.open_decisions || [];
  const nTriggered = openRows.filter((r) => r.status === 'triggered').length;
  const nHighRisk = decisions.filter((d) => d.risk_level === 'high').length;
  const oppCount = (data?.opportunities?.opportunities || []).length;
  // 逐檔 AI 卡（證據層）與決策併成一列一檔；只有方向相反的才值得多看
  const aiCards = data?.ai_cards || null;
  const mergedRows = useMemo(
    () => mergeDecisionsWithCards(decisions, aiCards?.cards, aiCards?.holdings, null),
    [decisions, aiCards],
  );
  const nOpposite = mergedRows.filter((r) => r.divergence === 'opposite').length;

  // Report layer split: 1st+2nd layer (90 秒晨報＋異常持倉) is the human TL;DR
  // and goes on the Overview tab; everything from the "📚 附錄" marker onward
  // (full per-ticker writeups, scorecard, ledger, audit detail) is reference
  // material and lives behind its own tab instead of always being rendered.
  const { briefMd, morningMd, attentionMd, appendixMd } = useMemo(() => {
    const md = data?.report_md || '';
    if (!md) return { briefMd: '', morningMd: '', attentionMd: '', appendixMd: '' };
    const i = md.indexOf('# 📚 附錄');
    const brief = i >= 0 ? md.slice(0, i) : md;
    const split = splitBriefSections(brief);
    return { briefMd: brief, morningMd: split.briefMd, attentionMd: split.attentionMd, appendixMd: i >= 0 ? md.slice(i) : '' };
  }, [data]);

  return (
    <div className="-mt-4 min-h-screen px-3 pb-10 pt-4 sm:px-5" style={{ background: C.bg }}>
      <div className="mx-auto max-w-6xl space-y-4">
        {/* 決策層的三個切面 —— 導覽列只留一個 War Room 入口 */}
        <ClusterTabs items={OPS_TABS} active="/review" theme="ops" className="mt-2 w-fit" />

        {/* Header */}
        <div className="flex flex-wrap items-center gap-3">
          <ClipboardCheck size={20} style={{ color: C.accent }} />
          <h1 className="font-mono text-lg font-bold tracking-wide" style={{ color: C.text }}>
            盤後復盤 <span style={{ color: C.muted }}>／ 戰情中心當日報告</span>
          </h1>
          {regime && (
            <Badge color={regime.color}><regime.Icon size={12} className="mr-1 inline" />{regime.label}</Badge>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            <button
              className="border p-1.5 disabled:opacity-30"
              style={{ borderColor: C.border, color: C.sub }}
              disabled={idx < 0 || idx >= dates.length - 1}
              onClick={() => selectDate(dates[idx + 1])}
              title="前一天"
            >
              <ChevronLeft size={14} />
            </button>
            <OpsDatePicker value={date} available={dates} onChange={selectDate} />
            <button
              className="border p-1.5 disabled:opacity-30"
              style={{ borderColor: C.border, color: C.sub }}
              disabled={idx <= 0}
              onClick={() => selectDate(dates[idx - 1])}
              title="後一天"
            >
              <ChevronRight size={14} />
            </button>
            <button
              className="border p-1.5"
              style={{ borderColor: C.border, color: C.sub }}
              onClick={() => fetchReview(date)}
              title="重新整理"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              className="inline-flex items-center gap-1.5 border px-2.5 py-1.5 font-mono text-[11px] font-bold uppercase tracking-wider disabled:opacity-40"
              style={{ borderColor: C.border, color: C.sub }}
              onClick={regenerateCards}
              disabled={genStatus === 'running'}
              title="重跑 05:00 的逐檔 AI 卡（證據層），約數分鐘"
            >
              <Sparkles size={12} className={genStatus === 'running' ? 'animate-pulse' : ''} />
              {genStatus === 'running' ? '產卡中…' : '重產 AI 卡'}
            </button>
          </div>
        </div>

        {error && <Panel><Empty>{error}</Empty></Panel>}

        {!error && data && (
          <>
            {/* Stat tiles */}
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
              <StatTile label="當日決策" value={decisions.length} sub={journal?.source === 'war_room' ? 'via 戰情官' : journal ? 'via 舊管線' : '無 journal'} />
              <StatTile
                label="AI 卡（證據層）"
                value={aiCards ? `${aiCards.ok ?? (aiCards.cards || []).length}/${aiCards.total ?? '—'}` : '—'}
                color={nOpposite ? C.amber : C.text}
                sub={aiCards ? (nOpposite ? `${nOpposite} 檔與復盤反向` : '無方向相反') : '該日無 AI 卡'}
              />
              <StatTile
                label="已觸發待回應"
                value={nTriggered}
                color={nTriggered ? C.short : C.long}
                sub={openRows.length ? `未結追蹤 ${openRows.length} 筆` : '無帳本資料'}
              />
              <StatTile label="高風險持倉" value={nHighRisk} color={nHighRisk ? C.amber : C.long} sub="模型標為高風險" />
              <StatTile
                label="30天樣本"
                value={ledger?.track_record?.n_total ?? '—'}
                sub={ledger ? `帳本 ${mmdd(ledger.date)} 產出` : '該日無帳本 sidecar'}
              />
            </div>

            <TabBar
              tab={tab}
              setTab={setTab}
              counts={{ decisions: decisions.length || undefined, opportunities: oppCount || undefined }}
            />

            {tab === 'overview' && (
              <>
                <TodayFocusPanel decisions={decisions} openRows={openRows} />
                <MorningBriefPanel md={morningMd} />
                <AttentionPositionsPanel md={attentionMd} decisions={decisions} openRows={openRows} />
                <TrendPanel trend={trend} />
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <OpenDecisionsPanel rows={openRows} />
                  <TrackRecordPanel tr={ledger?.track_record} />
                </div>
              </>
            )}

            {tab === 'decisions' && <DecisionsPanel journal={journal} rows={mergedRows} />}

            {tab === 'opportunities' && (
              <>
                <OpportunitiesPanel opp={data.opportunities} />
                {data.opportunities === null && (
                  <div className="flex items-center gap-2 font-mono text-[11px]" style={{ color: C.muted }}>
                    <Telescope size={12} /> 該日無機會掃描（market_scan 每交易日 06:00 自動跑）
                  </div>
                )}
              </>
            )}

            {tab === 'report' && (
              <Panel
                label="完整報告附錄"
                status={appendixMd
                  ? <Badge color={C.accent}><FileText size={11} className="mr-1 inline" />鏡像已落地</Badge>
                  : <Badge color={C.muted}>該日無附錄內容</Badge>}
              >
                {appendixMd ? (
                  <Prose>{normalizeTickerBlocks(appendixMd.replace(/<<<([A-Z_]+)>>>/g, '\n## ▍$1\n'))}</Prose>
                ) : briefMd ? (
                  <Empty>該日報告只有第一層摘要，沒有第三層附錄（可回總覽分頁查看）</Empty>
                ) : (
                  <Empty>
                    <Activity size={14} className="mr-1 inline" />
                    該日只有 journal 決策（完整報告從 2026-08-23 改版後才開始鏡像落地）
                  </Empty>
                )}
              </Panel>
            )}
          </>
        )}
      </div>
    </div>
  );
}
