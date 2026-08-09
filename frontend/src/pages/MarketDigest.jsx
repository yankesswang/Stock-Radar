import { useState, useEffect } from 'react';
import axios from 'axios';
import { LineChart, Calendar, Loader2 } from 'lucide-react';
import Markdown from '../components/Markdown';

export default function MarketDigest() {
  const [dates, setDates] = useState([]);
  const [active, setActive] = useState(null);
  const [report, setReport] = useState(null);
  const [tab, setTab] = useState('market');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    axios.get('/api/stock-digest')
      .then((r) => {
        const ds = r.data.dates || [];
        setDates(ds);
        if (ds.length) setActive(ds[0]);
        else setLoading(false);
      })
      .catch(() => { setError('無法載入市場日報清單'); setLoading(false); });
  }, []);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    axios.get(`/api/stock-digest/${active}`)
      .then((r) => {
        setReport(r.data);
        setTab(r.data.sections?.[0]?.key || 'market');
        setError('');
      })
      .catch(() => setError('無法載入該日市場日報'))
      .finally(() => setLoading(false));
  }, [active]);

  const current = report?.sections?.find((s) => s.key === tab);

  return (
    <div className="container max-w-4xl">
      <div className="flex items-center gap-2.5 mb-1">
        <LineChart className="text-[var(--accent-cyan)]" size={22} />
        <h1 className="text-xl font-bold text-[var(--text-primary)]">市場日報</h1>
      </div>
      <p className="text-sm text-[var(--text-muted)] mb-5">
        每日股市盤勢報告：Macro · 財報 · AI/半導體 · 盤面 · Options · 持倉（同步推送 Telegram）
      </p>

      {dates.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {dates.map((d) => (
            <button key={d} onClick={() => setActive(d)}
              className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium transition
                ${d === active ? 'bg-[var(--accent-cyan)] text-black'
                               : 'bg-white/5 text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
              <Calendar size={12} /> {d}
            </button>
          ))}
        </div>
      )}

      {report?.sections?.length > 1 && (
        <div className="flex flex-wrap gap-1 mb-5 border-b border-white/10">
          {report.sections.map((s) => (
            <button key={s.key} onClick={() => setTab(s.key)}
              className={`px-3 py-2 text-sm font-medium transition border-b-2 -mb-px
                ${s.key === tab ? 'border-[var(--accent-cyan)] text-[var(--text-primary)]'
                                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
              {s.label}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-[var(--text-muted)] py-10 justify-center">
          <Loader2 className="animate-spin" size={18} /> 載入中…
        </div>
      )}

      {!loading && error && <div className="text-[var(--accent-danger)] py-10 text-center">{error}</div>}

      {!loading && !error && dates.length === 0 && (
        <div className="text-[var(--text-muted)] py-10 text-center">
          尚無市場日報。每天 08:00 stock digest 執行後會自動產生。
        </div>
      )}

      {!loading && !error && current && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-5 text-sm">
          <Markdown>{current.content}</Markdown>
        </div>
      )}
    </div>
  );
}
