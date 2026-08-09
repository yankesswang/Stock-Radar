import { useState, useEffect } from 'react';
import axios from 'axios';
import {
  Newspaper, ChevronDown, ChevronUp, ExternalLink, Calendar, Loader2,
} from 'lucide-react';
import Markdown from '../components/Markdown';

function DigestCard({ item }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-5">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold text-[var(--text-primary)] leading-snug">{item.title}</h3>
        {item.sector && (
          <span className="shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold text-[var(--accent-cyan)] bg-[rgba(6,182,212,0.12)]">
            {item.sector}
          </span>
        )}
      </div>

      {item.tickers?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.tickers.map((t) => (
            <span key={t} className="px-1.5 py-0.5 rounded text-[11px] font-mono font-semibold text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]">
              ${t}
            </span>
          ))}
        </div>
      )}

      {item.tldr && (
        <div className="mt-3 text-sm">
          <Markdown>{item.tldr}</Markdown>
        </div>
      )}

      <div className="mt-3 flex items-center gap-4">
        {item.url && (
          <a href={item.url} target="_blank" rel="noreferrer"
             className="inline-flex items-center gap-1 text-xs text-[var(--accent-cyan)] hover:underline">
            <ExternalLink size={13} /> 原文
          </a>
        )}
        {item.content && (
          <button onClick={() => setOpen((o) => !o)}
                  className="inline-flex items-center gap-1 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]">
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {open ? '收合全文' : '展開全文'}
          </button>
        )}
      </div>

      {open && item.content && (
        <div className="mt-4 pt-4 border-t border-white/10 text-sm">
          <Markdown>{item.content}</Markdown>
        </div>
      )}
    </div>
  );
}

export default function FinanceDigest() {
  const [dates, setDates] = useState([]);
  const [active, setActive] = useState(null);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    axios.get('/api/finance-digest')
      .then((r) => {
        const ds = r.data.dates || [];
        setDates(ds);
        if (ds.length) setActive(ds[0]);
        else { setLoading(false); }
      })
      .catch(() => { setError('無法載入日報清單'); setLoading(false); });
  }, []);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    axios.get(`/api/finance-digest/${active}`)
      .then((r) => { setReport(r.data); setError(''); })
      .catch(() => setError('無法載入該日日報'))
      .finally(() => setLoading(false));
  }, [active]);

  return (
    <div className="container max-w-4xl">
      <div className="flex items-center gap-2.5 mb-1">
        <Newspaper className="text-[var(--accent-cyan)]" size={22} />
        <h1 className="text-xl font-bold text-[var(--text-primary)]">投資日報</h1>
      </div>
      <p className="text-sm text-[var(--text-muted)] mb-5">
        每日 Substack 財經電子報 → AI 整理投資筆記（同步推送 Telegram）
      </p>

      {dates.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-5">
          {dates.map((d) => (
            <button key={d} onClick={() => setActive(d)}
              className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium transition
                ${d === active
                  ? 'bg-[var(--accent-cyan)] text-black'
                  : 'bg-white/5 text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
              <Calendar size={12} /> {d}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-[var(--text-muted)] py-10 justify-center">
          <Loader2 className="animate-spin" size={18} /> 載入中…
        </div>
      )}

      {!loading && error && (
        <div className="text-[var(--accent-danger)] py-10 text-center">{error}</div>
      )}

      {!loading && !error && dates.length === 0 && (
        <div className="text-[var(--text-muted)] py-10 text-center">
          尚無日報。每天 08:30 finance digest 執行後會自動產生。
        </div>
      )}

      {!loading && !error && report && (
        <>
          <div className="text-xs text-[var(--text-muted)] mb-4">
            {report.date} · 整理 {report.count}/{report.total} 篇
          </div>
          <div className="space-y-4">
            {report.items.map((item, i) => <DigestCard key={i} item={item} />)}
            {report.items.length === 0 && (
              <div className="text-[var(--text-muted)] text-center py-6">本日無筆記</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
