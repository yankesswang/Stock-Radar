/**
 * WarRoomTeaser — Portfolio 頁的一張「今日戰情」摘要卡（淺色主題）。
 *
 * 原本這個位置掛的是每日報告的總覽面板（2,300 行的 DailyDigestPanel）；2026-09-07
 * 每日報告併進戰情室後，Portfolio 頁只留一眼看得完的數字與入口：本場日期／市況、
 * 今日指令要動幾檔、新聞利多利空幾則，細節全部在 War Room 家族三頁。
 */
import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import axios from '../lib/apiClient';
import { Radar, ClipboardCheck, Newspaper, Radio, ArrowRight } from 'lucide-react';
import { actionMeta } from './opsTheme';

const REGIME = {
  risk_on: { label: '風險偏好', cls: 'text-[var(--accent-success-text)] bg-[rgba(16,185,129,0.12)]' },
  'risk-on': { label: '風險偏好', cls: 'text-[var(--accent-success-text)] bg-[rgba(16,185,129,0.12)]' },
  risk_off: { label: '避險', cls: 'text-[var(--accent-danger-text)] bg-[rgba(239,68,68,0.12)]' },
  'risk-off': { label: '避險', cls: 'text-[var(--accent-danger-text)] bg-[rgba(239,68,68,0.12)]' },
  mixed: { label: '多空分歧', cls: 'text-[var(--accent-amber-text)] bg-[rgba(245,158,11,0.12)]' },
};

// 決策動作歸成四桶：要減／要加／持有／觀察
const BUCKET = {
  stop_loss: 'cut', exit: 'cut', sell: 'cut', trim: 'cut', reduce: 'cut',
  open: 'add', add: 'add', buy: 'add', accumulate: 'add',
  watch: 'watch', hold: 'hold',
};
const BUCKETS = [
  { key: 'cut', label: '停損／減碼', cls: 'text-[var(--accent-danger-text)]' },
  { key: 'add', label: '加倉／建倉', cls: 'text-[var(--accent-success-text)]' },
  { key: 'watch', label: '觀察', cls: 'text-[var(--accent-amber-text)]' },
  { key: 'hold', label: '持有', cls: 'text-[var(--text-secondary)]' },
];

const LINKS = [
  { href: '/war-room', label: '戰情室', icon: Radar, hint: '今日指令・持倉矩陣・新聞線・市場日報' },
  { href: '/review', label: '盤後復盤', icon: ClipboardCheck, hint: '90 秒晨報・決策全表・機會掃描・完整報告' },
  { href: '/war-room?panel=news', label: '新聞線', icon: Newspaper, hint: '今日主線與持倉必讀新聞' },
  { href: '/signals', label: '訊號雷達', icon: Radio, hint: '全部技術訊號' },
];

export default function WarRoomTeaser() {
  const [journal, setJournal] = useState(null);
  const [news, setNews] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([axios.get('/api/war-room/journal/latest'), axios.get('/api/market-news/latest')])
      .then(([j, n]) => {
        if (!alive) return;
        if (j.status === 'fulfilled') setJournal(j.value.data);
        if (n.status === 'fulfilled') setNews(n.value.data);
        if (j.status !== 'fulfilled' && n.status !== 'fulfilled') setFailed(true);
      });
    return () => { alive = false; };
  }, []);

  const decisions = journal?.decisions || [];
  const counts = decisions.reduce((m, d) => {
    const b = BUCKET[d.action] || 'hold';
    m[b] = (m[b] || 0) + 1;
    return m;
  }, {});
  const regime = REGIME[journal?.market_regime];
  const impact = (news?.items || []).reduce((m, it) => { m[it.impact] = (m[it.impact] || 0) + 1; return m; }, {});
  const date = journal?.date || news?.date;

  return (
    <div className="card p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Radar size={15} className="text-[var(--accent-cyan)]" />
        <p className="text-sm font-semibold text-[var(--text-secondary)]">今日戰情 · War Room</p>
        {date && <span className="font-mono text-xs text-[var(--text-meta)]">本場 {date}</span>}
        {regime && <span className={`rounded px-2 py-0.5 text-[11px] font-bold ${regime.cls}`}>{regime.label}</span>}
        <Link href="/war-room" className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[var(--accent-cyan-text)] hover:underline">
          進入戰情室 <ArrowRight size={13} />
        </Link>
      </div>

      {failed ? (
        <p className="text-xs text-[var(--text-meta)]">戰情資料載入失敗 —— 直接進戰情室看。</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              今日指令 {decisions.length ? `· ${decisions.length} 檔` : ''}
            </p>
            {decisions.length ? (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                {BUCKETS.filter((b) => counts[b.key]).map((b) => (
                  <span key={b.key} className={`inline-flex items-baseline gap-1 ${b.cls}`}>
                    <span className="font-mono text-base font-bold tabular-nums">{counts[b.key]}</span>
                    <span className="text-xs">{b.label}</span>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-[var(--text-meta)]">{journal ? '這場沒有決策' : '載入中…'}</p>
            )}
            {decisions.length > 0 && (
              <p className="mt-1 truncate text-xs text-[var(--text-meta)]" title={decisions.map((d) => `${d.ticker} ${actionMeta(d.action).label}`).join('、')}>
                {decisions.filter((d) => BUCKET[d.action] === 'cut' || BUCKET[d.action] === 'add').slice(0, 6)
                  .map((d) => `${d.ticker} ${actionMeta(d.action).label}`).join('、') || '全部持有／觀察，沒有要動的'}
              </p>
            )}
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              市場新聞 {news ? `· ${news.count ?? (news.items || []).length} 則` : ''}
            </p>
            {news ? (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                <span className="inline-flex items-baseline gap-1 text-[var(--accent-success-text)]">
                  <span className="font-mono text-base font-bold tabular-nums">{impact['利多'] || 0}</span><span className="text-xs">利多</span>
                </span>
                <span className="inline-flex items-baseline gap-1 text-[var(--accent-danger-text)]">
                  <span className="font-mono text-base font-bold tabular-nums">{impact['利空'] || 0}</span><span className="text-xs">利空</span>
                </span>
                <span className="inline-flex items-baseline gap-1 text-[var(--text-secondary)]">
                  <span className="font-mono text-base font-bold tabular-nums">{impact['中性'] || 0}</span><span className="text-xs">中性</span>
                </span>
              </div>
            ) : (
              <p className="text-xs text-[var(--text-meta)]">尚無市場新聞</p>
            )}
            {news?.market_summary && (
              <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[var(--text-meta)]" title={news.market_summary}>
                {news.market_summary}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-t border-[var(--glass-border)] pt-3">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            title={l.hint}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-3 py-1.5 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
          >
            <l.icon size={13} /> {l.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
