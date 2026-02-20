import { useState, useEffect } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import { Briefcase, Plus, Trash2, ChevronRight, TrendingUp, RefreshCw } from 'lucide-react';

export default function PortfolioList() {
  const [portfolios, setPortfolios] = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [creating,   setCreating]   = useState(false);
  const [name,       setName]       = useState('');
  const [desc,       setDesc]       = useState('');
  const [saving,     setSaving]     = useState(false);

  const load = () => {
    setLoading(true);
    axios.get('/api/portfolio')
      .then(r => setPortfolios(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!name.trim()) return;
    setSaving(true);
    await axios.post('/api/portfolio', { name: name.trim(), description: desc.trim() });
    setName(''); setDesc(''); setCreating(false);
    load();
    setSaving(false);
  };

  const remove = async (e, id) => {
    e.preventDefault();
    if (!confirm('Delete this portfolio?')) return;
    await axios.delete(`/api/portfolio/${id}`);
    setPortfolios(p => p.filter(x => x.id !== id));
  };

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Briefcase size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Portfolios</h1>
            <p className="text-xs text-[var(--text-muted)]">Track your holdings with live prices &amp; valuation data</p>
          </div>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 transition-all shadow-lg shadow-purple-500/20"
        >
          <Plus size={15} /> New Portfolio
        </button>
      </div>

      {/* Create form */}
      {creating && (
        <div className="card p-5 border-[rgba(139,92,246,0.3)] space-y-3">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)]">New Portfolio</h3>
          <input
            autoFocus
            type="text"
            placeholder="Portfolio name *"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && create()}
            className="w-full px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-sm outline-none focus:border-[var(--accent-purple)] transition-colors"
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={desc}
            onChange={e => setDesc(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && create()}
            className="w-full px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-sm outline-none focus:border-[var(--accent-purple)] transition-colors"
          />
          <div className="flex gap-2">
            <button onClick={create} disabled={!name.trim() || saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-50 transition-all">
              {saving ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => { setCreating(false); setName(''); setDesc(''); }}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-24">
          <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
        </div>
      )}

      {/* Empty */}
      {!loading && portfolios.length === 0 && (
        <div className="card p-16 text-center">
          <Briefcase size={40} className="mx-auto mb-3 text-[var(--text-muted)] opacity-20" />
          <p className="text-[var(--text-muted)] text-sm mb-4">No portfolios yet.</p>
          <button onClick={() => setCreating(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
            <Plus size={14} /> Create your first portfolio
          </button>
        </div>
      )}

      {/* List */}
      {!loading && portfolios.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {portfolios.map(p => (
            <Link key={p.id} href={`/portfolio/${p.id}`}>
              <div className="card p-5 cursor-pointer hover:border-[rgba(139,92,246,0.3)] hover:shadow-lg hover:shadow-purple-500/5 transition-all group">
                <div className="flex items-start justify-between mb-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-md">
                    <Briefcase size={16} className="text-white" />
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={e => remove(e, p.id)}
                      className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.08)] transition-all opacity-0 group-hover:opacity-100"
                    >
                      <Trash2 size={14} />
                    </button>
                    <ChevronRight size={16} className="text-[var(--text-muted)] group-hover:text-[var(--accent-purple)] transition-colors" />
                  </div>
                </div>
                <h3 className="font-bold text-base mb-0.5">{p.name}</h3>
                {p.description && <p className="text-xs text-[var(--text-muted)] mb-3 line-clamp-1">{p.description}</p>}
                <div className="flex items-center gap-3 text-xs mt-2 pt-2 border-t border-[var(--glass-border)]">
                  <span className="flex items-center gap-1 text-[var(--text-muted)]">
                    <TrendingUp size={11} />
                    {p.holding_count} holding{p.holding_count !== 1 ? 's' : ''}
                  </span>
                  {p.tickers.length > 0 && (
                    <span className="text-[var(--text-muted)] truncate">{p.tickers.slice(0, 4).join(' · ')}{p.tickers.length > 4 ? ` +${p.tickers.length - 4}` : ''}</span>
                  )}
                  <span className="ml-auto text-[var(--text-muted)]">{p.created_at}</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
