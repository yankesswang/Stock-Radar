/**
 * DailyWire — 戰情室的日報層兩個面板（ops 暗色系）：
 *   NewsWirePanel：市場新聞（今日主線＋持倉／大盤／觀察名單必讀新聞，AI 策展、每則附出處）
 *   DigestPanel：市場日報（個股與產業／Portfolio／今日市場報告的全文閱讀）
 *
 * 這兩份原本住在獨立的「每日報告」頁；2026-09-07 併進戰情室後跟著「本場」日期走，
 * 持倉相關的新聞依目前範圍（哪個組合）排到最前。買賣結論仍只由盤後復盤（/review）給。
 */
import { useMemo, useState } from 'react';
import { Link } from 'wouter';
import {
  Newspaper, ExternalLink, RefreshCw, ChevronDown, ChevronUp, FileText,
  ClipboardCheck, AlertTriangle, Info,
} from 'lucide-react';
import { C, alpha } from './opsTheme';
import { Panel, Badge, Empty, FilterChip } from './opsUi';
import OpsProse from './OpsMarkdown';
import { clean as cleanMd } from './markdownText';
import {
  NEWS_SCOPES, IMPACT_META, normScope, sortNews, splitSentences,
  digestSections, splitFrontMatter, summaryChips, charCount,
} from './warRoomDaily';

function Skeleton({ n = 6 }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="wr-skel h-4 w-full" style={{ background: C.border, opacity: 0.5 }} />
      ))}
    </div>
  );
}

// 「本場 09/05」但報告是 09/04 的 → 寫明沿用哪一天，不能讓人以為是同一天的東西
function DateChip({ date, activeDate }) {
  if (!date) return null;
  const same = !activeDate || date === activeDate;
  return (
    <span
      className="font-mono text-[11px] tabular-nums"
      style={{ color: same ? C.muted : C.amber }}
      title={same ? date : `本場 ${activeDate} 沒有這份，沿用 ${date}`}
    >
      {same ? date : `沿用 ${date}`}
    </span>
  );
}

const fmtTime = (iso) => (iso ? String(iso).slice(5, 16).replace('T', ' ').replace('-', '/') : '');

// ── 市場新聞 ────────────────────────────────────────────────────────────────
function NewsRow({ item, index, heldTickers }) {
  const [open, setOpen] = useState(false);
  const impact = IMPACT_META[item.impact];
  const edge = impact ? impact.color : C.border;
  const points = splitSentences(item.tldr);
  const sources = item.sources || [];
  const extra = sources.slice(1);
  const primary = sources[0] || (item.url ? { title: item.source, url: item.url } : null);
  const held = (item.tickers || []).some((t) => heldTickers[t]);

  return (
    <li
      className="border-l-2 py-2 pl-3 pr-2 transition-colors duration-200"
      style={{ borderColor: edge, background: held ? `${alpha(C.accent, 0.031)}` : 'transparent' }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="w-4 shrink-0 text-right font-mono text-[12px] tabular-nums" style={{ color: C.muted }}>
          {index}
        </span>
        <span className="min-w-0 max-w-[76ch] text-[15.5px] font-semibold leading-snug" style={{ color: C.text }}>
          {item.title}
        </span>
        {(item.tickers || []).map((t) => {
          const isHeld = !!heldTickers[t];
          return (
            <Link
              key={t}
              href={`/stock/${encodeURIComponent(t)}`}
              className="border px-1.5 py-0.5 font-mono text-[11px] font-bold tracking-wider transition-colors duration-200 hover:bg-[var(--ops-deep)]"
              style={{
                borderColor: isHeld ? C.accent : C.border,
                color: isHeld ? C.accent : C.sub,
                background: isHeld ? `${alpha(C.accent, 0.071)}` : 'transparent',
              }}
              title={isHeld ? `${t}：本場範圍內持有` : t}
            >
              {t}
            </Link>
          );
        })}
        {impact && (
          <span className="ml-auto shrink-0">
            <Badge color={impact.color}>{impact.label}</Badge>
          </span>
        )}
      </div>

      {/* 摘要全文直接攤開 —— 使用者要的是掃一眼就讀完，不是每則再點一次「更多」；
          只有「另外 N 則出處」才收著 */}
      {points.length > 0 && (
        <ul className="mt-1 space-y-0.5 pl-6">
          {points.map((t, k) => (
            <li key={k} className="flex gap-1.5 text-[14px] leading-relaxed" style={{ color: C.sub }}>
              <span className="shrink-0" style={{ color: C.muted }}>•</span>
              <span className="max-w-[76ch]">{t}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 pl-6 font-mono text-[11px]" style={{ color: C.muted }}>
        {/* 出處要一眼看得出是可以點出去的連結：做成青色邊框晶片（同 War Room 其他外連按鈕），
            不再是一行灰字 */}
        {primary?.url && (
          <a
            href={primary.url}
            target="_blank"
            rel="noreferrer"
            title={primary.title || item.title}
            className="wr-news-link inline-flex items-center gap-1.5 border px-2 py-1 font-bold uppercase tracking-wider transition-all duration-200"
            style={{ borderColor: `${alpha(C.accent, 0.4)}`, color: C.accent, background: `${alpha(C.accent, 0.063)}` }}
          >
            <ExternalLink size={12} /> 原文 · {primary.source || item.source || '連結'}
          </a>
        )}
        {item.published_at && <span>{fmtTime(item.published_at)}</span>}
        {extra.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex cursor-pointer items-center gap-1"
            style={{ color: C.muted }}
          >
            {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            另外 {extra.length} 則出處
          </button>
        )}
      </div>

      {open && extra.length > 0 && (
        <ul className="mt-1.5 space-y-1 border-t pt-1.5 pl-6 text-[12px]" style={{ borderColor: C.border, color: C.dim }}>
          {extra.map((src, i) => (
            <li key={i} className="flex gap-1.5 leading-snug">
              <span className="shrink-0">•</span>
              <span>
                {src.title}{' '}
                {src.url && (
                  <a
                    href={src.url}
                    target="_blank"
                    rel="noreferrer"
                    className="wr-news-link ml-1 inline-flex items-center gap-1 border px-1.5 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider transition-all duration-200"
                    style={{ borderColor: `${alpha(C.accent, 0.4)}`, color: C.accent, background: `${alpha(C.accent, 0.063)}` }}
                  >
                    <ExternalLink size={10} /> {src.source || '原文'}
                  </a>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function NewsWirePanel({ news, heldTickers = {}, activeDate }) {
  const { data, date, loading, ready, error, status, runError, regenerate } = news;
  const [scope, setScope] = useState('all');
  const [dirOnly, setDirOnly] = useState(false);

  const items = useMemo(() => sortNews(data?.items || [], heldTickers), [data, heldTickers]);
  const counts = useMemo(() => {
    const c = { all: items.length, 利多: 0, 利空: 0 };
    for (const s of NEWS_SCOPES) c[s.key] = 0;
    for (const it of items) {
      c[normScope(it.scope)] += 1;
      if (it.impact in c) c[it.impact] += 1;
    }
    return c;
  }, [items]);
  const shown = useMemo(
    () => items.filter((it) => (scope === 'all' || normScope(it.scope) === scope) && (!dirOnly || it.impact === '利多' || it.impact === '利空')),
    [items, scope, dirOnly]
  );
  const busy = status === 'running';
  const summary = useMemo(() => splitSentences(data?.market_summary), [data]);

  return (
    <Panel
      label="新聞線 ／ 今日主線與必讀新聞"
      status={
        <span className="flex flex-wrap items-center gap-3">
          <DateChip date={date} activeDate={activeDate} />
          {data && (
            <span className="font-mono text-[11px]" style={{ color: C.muted }} title={`從 ${data.total} 則頭條策展出 ${data.count} 則`}>
              策展 {data.count}/{data.total} 則{data.generated_at ? ` · ${fmtTime(data.generated_at)}` : ''}
            </span>
          )}
          <button
            type="button"
            onClick={regenerate}
            disabled={busy}
            title="重新抓持倉與觀察名單的當日新聞並由 AI 策展（約 2–5 分鐘）"
            className="inline-flex cursor-pointer items-center gap-1.5 border px-2 py-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-colors duration-200 hover:bg-[var(--ops-deep)] disabled:cursor-not-allowed disabled:opacity-40"
            style={{ borderColor: C.border, color: C.sub }}
          >
            <RefreshCw size={12} className={busy ? 'wr-spin' : ''} />
            {busy ? '策展中…' : '立即產生今日'}
          </button>
          <Newspaper size={14} style={{ color: C.muted }} />
        </span>
      }
    >
      {runError && (
        <div className="mb-3 flex items-center gap-2 font-mono text-[12px]" style={{ color: C.short }}>
          <AlertTriangle size={13} /> {runError}
        </div>
      )}
      {!ready || loading ? (
        <Skeleton n={8} />
      ) : error ? (
        <Empty>{error}</Empty>
      ) : !date || !data ? (
        <Empty>尚無市場新聞 —— 右上「立即產生今日」可現在策展一份。</Empty>
      ) : (
        <div className="space-y-3">
          {summary.length > 0 && (
            <div className="border-l-2 py-1 pl-3" style={{ borderColor: C.accent }}>
              <div className="mb-1 font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.accent }}>
                今日主線
              </div>
              <ul className="space-y-1">
                {summary.map((t, k) => (
                  <li key={k} className="flex gap-1.5 text-[15px] leading-relaxed" style={{ color: C.sub }}>
                    <span className="shrink-0" style={{ color: C.accent }}>•</span>
                    <span className="max-w-[80ch]">{t}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.curated === false && (
            <div className="flex items-center gap-2 font-mono text-[12px]" style={{ color: C.amber }}>
              <AlertTriangle size={13} /> AI 策展未執行，以下為未整理的原始頭條，沒有影響判定。
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip active={scope === 'all'} onClick={() => setScope('all')}>全部 {counts.all}</FilterChip>
            {NEWS_SCOPES.map((s) => (
              <FilterChip key={s.key} active={scope === s.key} onClick={() => setScope(s.key)}>
                {s.label} {counts[s.key]}
              </FilterChip>
            ))}
            <span className="mx-1 h-4 border-l" style={{ borderColor: C.border }} />
            <FilterChip
              active={dirOnly}
              color={C.amber}
              onClick={() => setDirOnly((v) => !v)}
              title="只看判為利多／利空的，中性的先收起"
            >
              有方向 {counts.利多 + counts.利空}
            </FilterChip>
          </div>

          {shown.length ? (
            <ol className="space-y-1">
              {shown.map((item, i) => (
                <NewsRow key={`${date}-${i}-${item.title}`} item={item} index={i + 1} heldTickers={heldTickers} />
              ))}
            </ol>
          ) : (
            <Empty>這個篩選下沒有新聞</Empty>
          )}

          <div className="flex flex-wrap items-start gap-x-4 gap-y-1 font-mono text-[11px]" style={{ color: C.muted }}>
            <span className="inline-flex items-center gap-1">
              <Info size={11} /> {data.role_note || '純事實與來源；買賣結論只由盤後復盤提供。'}
            </span>
            {data.skipped && <span className="max-w-[90ch]">已濾掉：{data.skipped}</span>}
          </div>
        </div>
      )}
    </Panel>
  );
}

// ── 市場日報 ────────────────────────────────────────────────────────────────
const SECTION_LS_KEY = 'stockradar.warroom.digestSection';
const COLLAPSED_PX = 560;

export function DigestPanel({ digest, activeDate }) {
  const { data, date, loading, ready, error } = digest;
  const sections = useMemo(() => digestSections(data), [data]);
  const [key, setKey] = useState(() => {
    try {
      return localStorage.getItem(SECTION_LS_KEY) || '';
    } catch {
      return '';
    }
  });
  // 展開狀態綁在「哪一天的哪一段」上：換段落或換日期自然回到收合，不必靠 effect 重設
  const [expandedKey, setExpandedKey] = useState(null);
  const [detail, setDetail] = useState(false);

  // 換日期後段落可能不存在時退回預設：個股與產業 → 今日市場報告 → Portfolio
  //（Portfolio 表跟上面的持倉矩陣是同一批數字，排最後）
  const fallback = ['stocks', 'market', 'portfolio'].map((k) => sections.find((s) => s.key === k)).find(Boolean) || sections[0] || null;
  const current = sections.find((s) => s.key === key) || fallback;
  const curKey = current ? `${date}-${current.key}` : null;
  const expanded = expandedKey === curKey;
  const setExpanded = (on) => setExpandedKey(on ? curKey : null);

  const pick = (k) => {
    setKey(k);
    try {
      localStorage.setItem(SECTION_LS_KEY, k);
    } catch {
      /* private mode */
    }
  };

  const doc = useMemo(() => splitFrontMatter(cleanMd(current?.content)), [current]);
  const chips = useMemo(() => summaryChips(doc.front), [doc.front]);
  const chars = charCount(doc.body);
  const missing = useMemo(() => {
    // 只報這個面板該有的那幾份；復盤與掃描缺席在 /review 自己會說
    const own = new Set(['今日市場報告', 'Portfolio', '個股與產業']);
    return (data?.completeness?.missing_labels || []).filter((l) => [...own].some((o) => l.includes(o)));
  }, [data]);
  const needsFold = chars > 1200;

  return (
    <Panel
      label="市場日報 ／ 個股與產業・Portfolio・今日市場報告"
      status={
        <span className="flex flex-wrap items-center gap-3">
          <DateChip date={date} activeDate={activeDate} />
          {missing.length > 0 && (
            <span className="font-mono text-[11px]" style={{ color: C.amber }} title={missing.join('、')}>
              今日缺 {missing.length} 份
            </span>
          )}
          <Link
            href="/review"
            className="inline-flex items-center gap-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-colors hover:underline"
            style={{ color: C.accent }}
            title="盤後復盤與機會掃描全文在盤後復盤頁"
          >
            <ClipboardCheck size={12} /> 復盤・掃描
          </Link>
          <FileText size={14} style={{ color: C.muted }} />
        </span>
      }
    >
      {!ready || loading ? (
        <Skeleton n={10} />
      ) : error ? (
        <Empty>{error}</Empty>
      ) : !date || !data ? (
        <Empty>尚無市場日報</Empty>
      ) : !current ? (
        <Empty>這天只有盤後復盤與機會掃描 —— 在「復盤・掃描」讀。</Empty>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {sections.length > 1 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {sections.map((s) => (
                  <FilterChip key={s.key} active={s.key === current.key} onClick={() => pick(s.key)}>
                    {s.label}
                  </FilterChip>
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px]" style={{ color: C.muted }}>
              {sections.length <= 1 && <span style={{ color: C.sub }}>{doc.front?.title || current.label}</span>}
              {chips.map((c, n) => (
                <span key={n} className="inline-flex items-baseline gap-1.5">
                  {c.k && <span>{c.k}</span>}
                  <span style={{ color: c.tone || C.sub, fontWeight: c.tone ? 700 : 400 }}>{c.v}</span>
                </span>
              ))}
              <span>約 {chars.toLocaleString()} 字</span>
              {(doc.front?.meta?.length || doc.front?.notes?.length || missing.length) ? (
                <button
                  type="button"
                  onClick={() => setDetail((v) => !v)}
                  aria-expanded={detail}
                  className="inline-flex cursor-pointer items-center gap-0.5"
                  style={{ color: C.muted }}
                >
                  明細 {detail ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </button>
              ) : null}
            </div>
          </div>

          {detail && (
            <dl className="grid gap-x-6 gap-y-1 border p-3 font-mono text-[12px] [grid-template-columns:repeat(auto-fill,minmax(min(100%,20rem),1fr))]" style={{ borderColor: C.border, color: C.sub }}>
              {doc.front?.title && (
                <div className="flex gap-2"><dt className="w-16 shrink-0" style={{ color: C.muted }}>報告</dt><dd className="min-w-0">{doc.front.title}</dd></div>
              )}
              {(doc.front?.meta || []).map((m, n) => (
                <div key={n} className="flex gap-2"><dt className="w-16 shrink-0" style={{ color: C.muted }}>{m.k}</dt><dd className="min-w-0">{m.v}</dd></div>
              ))}
              {missing.length > 0 && (
                <div className="flex gap-2"><dt className="w-16 shrink-0" style={{ color: C.muted }}>缺少</dt><dd className="min-w-0">{missing.join('、')}</dd></div>
              )}
              {(doc.front?.notes || []).map((n, k) => (
                <p key={`n${k}`} className="col-span-full leading-relaxed">{n}</p>
              ))}
            </dl>
          )}

          <div className="relative">
            <div
              className="overflow-hidden transition-[max-height] duration-300"
              style={{ maxHeight: needsFold && !expanded ? COLLAPSED_PX : 'none' }}
            >
              <OpsProse key={`${date}-${current.key}`} maxWidth={1180}>{doc.body}</OpsProse>
            </div>
            {needsFold && !expanded && (
              <div
                className="pointer-events-none absolute inset-x-0 bottom-0 h-28"
                style={{ background: `linear-gradient(to bottom, transparent, ${C.panel})` }}
              />
            )}
          </div>
          {needsFold && (
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className="inline-flex cursor-pointer items-center gap-1.5 border px-3 py-1.5 font-mono text-[12px] font-bold uppercase tracking-wider transition-colors duration-200 hover:bg-[var(--ops-deep)]"
              style={{ borderColor: C.accent, color: C.accent, background: `${alpha(C.accent, 0.063)}` }}
            >
              {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              {expanded ? '收合全文' : `展開全文（約 ${chars.toLocaleString()} 字）`}
            </button>
          )}
        </div>
      )}
    </Panel>
  );
}
