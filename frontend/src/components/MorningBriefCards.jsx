/**
 * MorningBriefCards — 盤後復盤「第一層：90 秒晨報」的卡片版。
 *
 * 原本整段 markdown 直出：16 條編號項目、40 行重複的「已回應，待對帳」、風險條列，
 * 一面牆 3800px 高，跟下面「異常持倉」的逐檔卡片放在一起讀起來是兩套語言。
 * 這裡改成跟異常持倉同一套視覺：
 *   1. 戰情官一句話置頂（這才是 90 秒的重點），旁邊市場狀態＋資料品質徽章
 *   2. 今天要做 —— 一檔一張卡：代號／動作／狀態徽章、目標倉位・停損・現價、觸發條件、風險一行
 *      沒有動作、只是貼近停損的，收成緊湊列
 *   3. 等待條件 —— 去重：同一檔同一動作只留一顆晶片（附最早日期與出現次數）；距觸發列依距離上色
 *   4. 最大風險 —— 曝險／全停損兩張數字卡 ＋ 逐檔風險列
 *   5. 其餘 N 檔維持 —— 一列晶片
 * 認不出格式（舊報告）就退回 markdown（由呼叫端決定）。
 */
import { useMemo } from 'react';
import { OctagonX, TriangleAlert, Hourglass, ShieldAlert, Quote, ListChecks } from 'lucide-react';
import { C, alpha, actionMeta, fmt, mmdd } from './opsTheme';
import { Badge } from './opsUi';
import { sortTodo } from './morningBrief';

const REGIME_COLOR = (label) => {
  if (!label) return C.muted;
  if (/風險偏好|偏多|多頭/.test(label)) return C.long;
  if (/避險|偏空|空頭|風險趨避/.test(label)) return C.short;
  return C.amber;
};
const distColor = (d) => (d == null ? C.dim : d < 3 ? C.short : d < 8 ? C.amber : C.dim);

function SectionHead({ icon, color, title, count, hint }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span style={{ color }} aria-hidden>{icon}</span>
      <span className="font-mono text-[12px] font-bold uppercase tracking-[0.14em]" style={{ color }}>{title}</span>
      {count != null && <span className="font-mono text-[12px] tabular-nums" style={{ color: C.dim }}>{count}</span>}
      {hint && <span className="text-[12px]" style={{ color: C.muted }}>{hint}</span>}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <span className="inline-flex items-baseline gap-1 whitespace-nowrap">
      <span className="text-[12.5px]" style={{ color: C.dim }}>{label}</span>
      <span className="font-mono text-[15px] font-semibold tabular-nums" style={{ color: color || C.text }}>{value}</span>
    </span>
  );
}

function TodoCard({ it }) {
  const am = it.action ? actionMeta(it.action) : { label: it.actionLabel || '—', color: C.muted };
  const edge = it.belowStop ? C.short : am.color;
  const statusColor = it.statusKind === 'unconfirmed' ? C.short : it.statusKind === 'pending' ? C.amber : C.long;
  return (
    <li className="flex flex-col gap-2 border p-3" style={{ borderColor: C.border, borderLeft: `4px solid ${edge}`, background: alpha(C.ink, 0.015) }}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-[20px] font-bold tracking-wide" style={{ color: C.text }}>{it.ticker}</span>
        <Badge color={am.color}>{am.label}</Badge>
        {it.belowStop && <Badge color={C.short}>破停損</Badge>}
        {it.status && (
          <span className="ml-auto font-mono text-[11.5px] font-semibold" style={{ color: statusColor }} title={it.status}>
            {it.statusKind === 'unconfirmed' ? '尚未確認執行' : it.statusKind === 'pending' ? '已回應・待對帳' : it.status}
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
        {it.target != null && <Stat label="目標倉位" value={`${fmt(it.target, 1)}%`} />}
        <Stat label="停損" value={it.stop != null ? fmt(it.stop) : '—'} color={it.belowStop ? C.short : undefined} />
        {it.price != null && <Stat label="現價" value={fmt(it.price)} color={it.belowStop ? C.short : undefined} />}
        {it.maxRisk && <Stat label="最大風險" value={it.maxRisk} color={C.amber} />}
      </div>
      {it.trigger && (
        <div className="text-[14px] leading-relaxed" style={{ color: C.sub }}>
          <span className="mr-1.5" style={{ color: C.dim }}>觸發</span>{it.trigger}
        </div>
      )}
    </li>
  );
}

function NearRow({ it }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 py-2" style={{ borderTop: `1px solid ${C.border}` }}>
      <span className="w-20 font-mono text-[15px] font-bold" style={{ color: C.text }}>{it.ticker}</span>
      <Stat label="現價" value={it.price != null ? fmt(it.price) : '—'} />
      <Stat label="停損" value={it.stop != null ? fmt(it.stop) : '—'} />
      <Stat label="距停損" value={it.distPct != null ? `${fmt(it.distPct, 1)}%` : '—'} color={distColor(it.distPct)} />
      {it.maxRisk && <Stat label="最大風險" value={it.maxRisk} color={C.amber} />}
    </li>
  );
}

export default function MorningBriefCards({ brief }) {
  const todo = useMemo(() => sortTodo(brief.todo.filter((t) => t.kind === 'action')), [brief.todo]);
  const near = useMemo(() => brief.todo.filter((t) => t.kind === 'near').sort((a, b) => (a.distPct ?? 99) - (b.distPct ?? 99)), [brief.todo]);
  const { triggered, pending, more } = brief.waiting;
  const { exposure, totalStop, tickers: riskTickers, other } = brief.risks;
  const regimeColor = REGIME_COLOR(brief.regime);
  const totalStopBad = totalStop && String(totalStop.amount).includes('-');

  return (
    <div className="space-y-5">
      {/* 1. 一句話 + 狀態列 */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          {brief.regime && <Badge color={regimeColor}>{brief.regime}</Badge>}
          {brief.meta?.交易日 && <span className="font-mono text-[12px]" style={{ color: C.dim }}>交易日 {brief.meta.交易日}</span>}
          {brief.meta?.狀態 && <span className="font-mono text-[12px]" style={{ color: C.dim }}>{brief.meta.狀態.replace(/（.*?）/g, '').trim()}</span>}
          {brief.quality?.[0] && <span className="font-mono text-[12px]" style={{ color: C.dim }}>{brief.quality[0]}</span>}
          {brief.meta?.verify && (
            <span className="font-mono text-[12px]" style={{ color: brief.meta.verify.ok ? C.long : C.amber }} title={brief.meta.verify.text}>
              {brief.meta.verify.ok ? '✓ 產出驗證通過' : '⚠ 產出驗證有異'}
            </span>
          )}
        </div>
        {brief.oneLiner && (
          <blockquote
            className="mt-2.5 flex gap-3 border-l-4 py-2.5 pl-4 pr-3"
            style={{ borderColor: C.accent, background: `${alpha(C.accent, 0.051)}` }}
          >
            <Quote size={18} className="mt-1 shrink-0" style={{ color: C.accent }} aria-hidden />
            <p className="text-[17px] font-medium leading-[1.7]" style={{ color: C.text }}>{brief.oneLiner}</p>
          </blockquote>
        )}
      </div>

      {/* 2. 今天要做 */}
      <div>
        <SectionHead icon={<ListChecks size={15} />} color={C.short} title="今天要做" count={todo.length} hint="破停損 → 停損 → 減碼 → 加碼／建倉；尚未確認執行的排前" />
        {todo.length ? (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {todo.map((it) => <TodoCard key={it.ticker + it.action} it={it} />)}
          </ul>
        ) : (
          <div className="py-3 font-mono text-[13px]" style={{ color: C.muted }}>今天沒有需要執行的動作</div>
        )}
        {near.length > 0 && (
          <div className="mt-3 border px-3 pb-1" style={{ borderColor: C.border, background: alpha(C.ink, 0.015) }}>
            <div className="py-2 font-mono text-[11.5px] uppercase tracking-[0.14em]" style={{ color: C.amber }}>
              貼近停損・尚無動作 <span style={{ color: C.dim }}>{near.length}</span>
            </div>
            <ul>{near.map((it) => <NearRow key={it.ticker} it={it} />)}</ul>
          </div>
        )}
      </div>

      {/* 3. 等待條件 */}
      {(triggered.length > 0 || pending.length > 0) && (
        <div>
          <SectionHead icon={<Hourglass size={15} />} color={C.amber} title="等待條件" count={triggered.length + pending.length} hint="已觸發的同一檔同一動作只列一次" />
          {triggered.length > 0 && (
            <div className="mb-2.5">
              <div className="mb-1.5 text-[12.5px]" style={{ color: C.dim }}>已觸發・已回應、待對帳 —— 需人工確認是否成交</div>
              <div className="flex flex-wrap gap-1.5">
                {triggered.map((t) => {
                  const am = t.action ? actionMeta(t.action) : { label: t.actionLabel, color: C.muted };
                  return (
                    <span
                      key={`${t.ticker}-${t.actionLabel}`}
                      className="inline-flex items-center gap-1.5 border px-2 py-1 font-mono text-[12.5px]"
                      style={{ borderColor: `${alpha(am.color, 0.333)}`, background: `${alpha(am.color, 0.071)}`, color: C.text }}
                      title={`${t.status}${t.times > 1 ? `（報告內重複 ${t.times} 次）` : ''}`}
                    >
                      <span className="font-bold">{t.ticker}</span>
                      <span style={{ color: am.color }}>{am.label}</span>
                      <span style={{ color: C.dim }}>{t.date ? mmdd(t.date) : '近期'}</span>
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          {pending.length > 0 && (
            <ul className="border" style={{ borderColor: C.border, background: alpha(C.ink, 0.015) }}>
              {pending.map((p, i) => (
                <li key={p.ticker + i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2" style={{ borderTop: i ? `1px solid ${C.border}` : 'none' }}>
                  <span className="w-20 font-mono text-[15px] font-bold" style={{ color: C.text }}>{p.ticker}</span>
                  <span className="w-16 font-mono text-[13px] font-semibold tabular-nums" style={{ color: distColor(p.distPct) }}>
                    距 {fmt(p.distPct, 1)}%
                  </span>
                  <span className="min-w-0 flex-1 text-[14px]" style={{ color: C.sub }}>{p.condition}</span>
                </li>
              ))}
              {more != null && (
                <li className="px-3 py-2 font-mono text-[12px]" style={{ borderTop: `1px solid ${C.border}`, color: C.muted }}>
                  另有 {more} 條距觸發價較遠的條件單，見決策帳本
                </li>
              )}
            </ul>
          )}
        </div>
      )}

      {/* 4. 最大風險 */}
      {(exposure || totalStop || riskTickers.length > 0 || other.length > 0) && (
        <div>
          <SectionHead icon={<ShieldAlert size={15} />} color={C.short} title="最大風險" />
          {(exposure || totalStop) && (
            <div className="mb-2.5 grid grid-cols-1 gap-3 md:grid-cols-2">
              {exposure && (
                <div className="border p-3" style={{ borderColor: C.border, borderLeft: `4px solid ${exposure.level === 'act' ? C.short : C.amber}`, background: alpha(C.ink, 0.015) }}>
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-[26px] font-bold tabular-nums" style={{ color: C.amber }}>{fmt(exposure.pct, 1)}%</span>
                    <span className="text-[14px] font-semibold" style={{ color: C.text }}>{exposure.group} 曝險</span>
                    <span className="font-mono text-[12px]" style={{ color: C.dim }}>{exposure.count} 檔・{exposure.note}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {exposure.tickers.map((t) => (
                      <span key={t} className="border px-1.5 py-0.5 font-mono text-[11.5px]" style={{ borderColor: C.border, color: C.sub }}>{t}</span>
                    ))}
                  </div>
                </div>
              )}
              {totalStop && (
                <div className="border p-3" style={{ borderColor: C.border, borderLeft: `4px solid ${totalStopBad ? C.short : C.amber}`, background: alpha(C.ink, 0.015) }}>
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-[26px] font-bold tabular-nums" style={{ color: totalStopBad ? C.short : C.text }}>{totalStop.amount}</span>
                    <span className="font-mono text-[13px] tabular-nums" style={{ color: totalStopBad ? C.short : C.dim }}>組合 {fmt(totalStop.pct, 2)}%</span>
                  </div>
                  <div className="mt-1 text-[13px]" style={{ color: C.dim }}>全部停損同時觸發的損失上限</div>
                </div>
              )}
            </div>
          )}
          {riskTickers.length > 0 && (
            <ul className="border" style={{ borderColor: C.border, background: alpha(C.ink, 0.015) }}>
              {riskTickers.map((r, i) => {
                const color = r.level === 'act' ? C.short : C.amber;
                return (
                  <li key={r.ticker + i} className="flex items-start gap-3 px-3 py-2.5" style={{ borderTop: i ? `1px solid ${C.border}` : 'none' }}>
                    {r.level === 'act'
                      ? <OctagonX size={16} className="mt-0.5 shrink-0" style={{ color }} aria-label={`風險 ${r.risk}`} />
                      : <TriangleAlert size={16} className="mt-0.5 shrink-0" style={{ color }} aria-label={`風險 ${r.risk}`} />}
                    <span className="w-20 shrink-0 font-mono text-[15px] font-bold" style={{ color: C.text }}>{r.ticker}</span>
                    <span className="min-w-0 flex-1 text-[14px] leading-relaxed" style={{ color: C.sub }}>{r.reason}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {other.length > 0 && (
            <ul className="mt-2 space-y-1 text-[13.5px]" style={{ color: C.sub }}>
              {other.map((o, i) => <li key={i}>・{o}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* 5. 其餘維持 */}
      {brief.rest.tickers.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 font-mono text-[11.5px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
            其餘 {brief.rest.count ?? brief.rest.tickers.length} 檔{brief.rest.label ? `・${brief.rest.label}` : ''}
          </span>
          {brief.rest.tickers.map((t) => (
            <span key={t} className="border px-1.5 py-0.5 font-mono text-[11.5px]" style={{ borderColor: C.border, color: C.dim }}>{t}</span>
          ))}
        </div>
      )}
    </div>
  );
}
