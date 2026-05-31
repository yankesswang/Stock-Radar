import { useState, useEffect } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  BookOpen, RefreshCw, TrendingUp, TrendingDown,
  AlertTriangle, Target, Clock, ChevronDown, ChevronUp,
  Activity, BarChart2, Shield, Zap, ArrowUpRight, CalendarDays,
} from 'lucide-react';

const ACTION_COLOR = {
  '布局':     'text-[var(--accent-cyan)]   bg-[rgba(6,182,212,0.1)]',
  '長線持有': 'text-[var(--accent-green)]  bg-[rgba(16,185,129,0.1)]',
  '主推':     'text-[var(--accent-cyan)]   bg-[rgba(6,182,212,0.1)]',
  '持有':     'text-[var(--accent-green)]  bg-[rgba(16,185,129,0.1)]',
  '逢高調節（第二目標到）': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  '先不買，等止跌訊號再進（2026-05-18 更新）': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  '純短線事件交易，訪中消息落地即出清': 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
  '事件交易進場': 'text-[var(--accent-cyan)] bg-[rgba(6,182,212,0.1)]',
  '減碼一半，保留底倉 + 移動止盈': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  '小買慢買，等站穩確認後加碼': 'text-[var(--accent-cyan)] bg-[rgba(6,182,212,0.1)]',
  '財報前出清（⚠️ 6/2前必須全出）': 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
  '財報前出清（⚠️ 6/3前必須全出）': 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
  '佈局，倉位上限30%，設停損': 'text-[var(--accent-cyan)] bg-[rgba(6,182,212,0.1)]',
  '佈局，優先序低於GLXY/CRCL': 'text-[var(--accent-cyan)] bg-[rgba(6,182,212,0.1)]',
};

function ActionBadge({ action }) {
  const cls = Object.entries(ACTION_COLOR).find(([k]) => action.includes(k.split('，')[0]) || action.includes(k))?.[1]
    ?? 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]';
  const short = action.split('（')[0].split('，')[0];
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${cls}`}>{short}</span>
  );
}

function PriceChange({ current, target }) {
  if (!current || !target) return null;
  const pct = ((target - current) / current * 100).toFixed(1);
  const up = parseFloat(pct) >= 0;
  return (
    <span className={`flex items-center gap-0.5 text-xs font-semibold ${up ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
      {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
      {up ? '+' : ''}{pct}%
    </span>
  );
}

function formatMcap(n) {
  if (!n) return null;
  if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(1)}B`;
  return `$${(n / 1e6).toFixed(0)}M`;
}

function FundBar({ fundData, livePrice }) {
  if (!fundData) return null;
  const { ytd_pct, fwd_pe, beta, week52_high, market_cap } = fundData;
  const pctFrom52w = week52_high && livePrice
    ? ((livePrice - week52_high) / week52_high * 100).toFixed(1)
    : null;
  const from52wColor = pctFrom52w == null ? '' :
    parseFloat(pctFrom52w) >= -5  ? 'text-[var(--accent-green)]' :
    parseFloat(pctFrom52w) >= -15 ? 'text-[var(--accent-yellow)]' :
                                    'text-[var(--accent-danger)]';
  return (
    <div className="flex flex-wrap gap-1.5 px-4 pb-2">
      {ytd_pct != null && (
        <span className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-semibold ${ytd_pct >= 0 ? 'text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]' : 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]'}`}>
          YTD {ytd_pct >= 0 ? '+' : ''}{ytd_pct}%
        </span>
      )}
      {fwd_pe != null && (
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
          Fwd PE&nbsp;{fwd_pe}x
        </span>
      )}
      {pctFrom52w != null && (
        <span className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)] ${from52wColor}`}>
          52W高 {parseFloat(pctFrom52w) >= 0 ? '+' : ''}{pctFrom52w}%
        </span>
      )}
      {beta != null && (
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
          β&nbsp;{beta}
        </span>
      )}
      {market_cap != null && (
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold text-[var(--text-muted)] bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
          {formatMcap(market_cap)}
        </span>
      )}
    </div>
  );
}

function StockCard({ stock, livePrice, fundData }) {
  const [expanded, setExpanded] = useState(false);
  const price = livePrice ?? stock.entry_price;

  return (
    <div className="card overflow-hidden hover:shadow-[0_4px_20px_rgba(0,82,255,0.12)] transition-shadow">
      {/* Header row */}
      <div
        className="flex items-start justify-between px-4 py-3 cursor-pointer select-none"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-start gap-3 min-w-0">
          <Link
            href={`/stock/${stock.ticker}`}
            onClick={e => e.stopPropagation()}
            className="shrink-0 w-10 h-10 rounded-xl bg-[rgba(0,82,255,0.08)] border border-[rgba(0,82,255,0.15)] flex items-center justify-center hover:bg-[rgba(0,82,255,0.15)] transition-colors cursor-pointer"
          >
            <span className="text-[10px] font-black text-[var(--accent-cyan)] leading-none text-center px-0.5">{stock.ticker}</span>
          </Link>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-sm">{stock.ticker}</span>
              <ActionBadge action={stock.action} />
              {stock.rec_date && (
                <span className="flex items-center gap-0.5 text-[10px] text-[var(--text-muted)]">
                  <CalendarDays size={9} />
                  {stock.rec_date}
                </span>
              )}
              {stock.earnings_date && (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)] border border-[rgba(248,113,113,0.2)]">
                  ⚠ 財報 {stock.earnings_date}
                </span>
              )}
            </div>
            <p className="text-xs text-[var(--text-muted)] mt-0.5 truncate">{stock.theme}</p>
          </div>
        </div>

        <div className="flex items-center gap-4 shrink-0 ml-2">
          {/* Live price */}
          <div className="text-right">
            {livePrice != null ? (
              <p className="text-sm font-bold">${livePrice.toLocaleString()}</p>
            ) : stock.entry_price != null ? (
              <p className="text-sm font-bold text-[var(--text-muted)]">${stock.entry_price}</p>
            ) : null}
            {stock.target_1 && price && (
              <PriceChange current={price} target={stock.target_1} />
            )}
          </div>
          {/* Target */}
          {stock.target_1 && (
            <div className="text-right hidden sm:block">
              <p className="text-[10px] text-[var(--text-muted)]">目標</p>
              <p className="text-xs font-semibold text-[var(--accent-yellow)]">${stock.target_1}</p>
            </div>
          )}
          <button className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all">
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {/* Tags */}
      {stock.tags && stock.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2">
          {stock.tags.map(t => (
            <span key={t} className="px-2 py-0.5 rounded-full text-[10px] bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)] text-[var(--text-muted)]">
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Fundamentals row */}
      <FundBar fundData={fundData} livePrice={livePrice} />

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-[var(--glass-border)] px-4 py-3 space-y-3">
          {/* Key stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {stock.timeframe && (
              <div className="bg-[rgba(255,255,255,0.03)] rounded-lg p-2">
                <p className="text-[10px] text-[var(--text-muted)] flex items-center gap-1"><Clock size={10} />時間框架</p>
                <p className="text-xs font-medium mt-0.5">{stock.timeframe.split('（')[0]}</p>
              </div>
            )}
            {stock.target_label && (
              <div className="bg-[rgba(255,255,255,0.03)] rounded-lg p-2">
                <p className="text-[10px] text-[var(--text-muted)] flex items-center gap-1"><Target size={10} />目標</p>
                <p className="text-xs font-medium mt-0.5">{stock.target_label}</p>
              </div>
            )}
            {stock.nav && (
              <div className="bg-[rgba(255,255,255,0.03)] rounded-lg p-2">
                <p className="text-[10px] text-[var(--text-muted)]">NAV</p>
                <p className="text-xs font-medium mt-0.5">${stock.nav}</p>
              </div>
            )}
            {stock.premium_pct && (
              <div className="bg-[rgba(248,113,113,0.06)] rounded-lg p-2 border border-[rgba(248,113,113,0.15)]">
                <p className="text-[10px] text-[var(--accent-danger)]">溢價幅度</p>
                <p className="text-xs font-bold text-[var(--accent-danger)] mt-0.5">{stock.premium_pct}%</p>
              </div>
            )}
          </div>

          {/* Key trigger */}
          {stock.key_trigger && (
            <div className="bg-[rgba(0,82,255,0.04)] border border-[rgba(0,82,255,0.12)] rounded-lg p-3">
              <p className="text-[10px] font-semibold text-[var(--accent-cyan)] flex items-center gap-1 mb-1"><Zap size={10} />關鍵催化劑</p>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{stock.key_trigger}</p>
            </div>
          )}

          {/* Logic */}
          {stock.logic && (
            <div>
              <p className="text-[10px] font-semibold text-[var(--text-muted)] flex items-center gap-1 mb-1.5"><BarChart2 size={10} />操作邏輯</p>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{stock.logic}</p>
            </div>
          )}

          {/* Risks */}
          {stock.risks && stock.risks.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-[var(--accent-danger)] flex items-center gap-1 mb-1.5"><AlertTriangle size={10} />核心風險</p>
              <ul className="space-y-1">
                {stock.risks.map((r, i) => (
                  <li key={i} className="text-xs text-[var(--text-muted)] flex items-start gap-1.5">
                    <span className="text-[var(--accent-danger)] mt-0.5 shrink-0">·</span>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Stop loss & position limit */}
          <div className="flex flex-wrap gap-2 pt-1">
            {stock.stop_loss && (
              <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[rgba(248,113,113,0.06)] border border-[rgba(248,113,113,0.15)]">
                <Shield size={10} className="text-[var(--accent-danger)]" />
                <span className="text-[10px] text-[var(--accent-danger)]">停損：{stock.stop_loss}</span>
              </div>
            )}
            {stock.position_limit && (
              <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[rgba(245,158,11,0.06)] border border-[rgba(245,158,11,0.15)]">
                <Activity size={10} className="text-[var(--accent-yellow)]" />
                <span className="text-[10px] text-[var(--accent-yellow)]">倉位上限：{stock.position_limit}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SectionBlock({ section, prices, fundamentals }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-6">
      <button
        className="w-full flex items-center justify-between mb-3 group"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-5 rounded-full bg-gradient-to-b from-[#0052ff] to-[#578bfa]" />
          <h2 className="text-base font-bold tracking-tight group-hover:text-[var(--accent-cyan)] transition-colors">{section.title}</h2>
          <span className="text-xs text-[var(--text-muted)]">({section.stocks.length})</span>
        </div>
        {open ? <ChevronUp size={14} className="text-[var(--text-muted)]" /> : <ChevronDown size={14} className="text-[var(--text-muted)]" />}
      </button>
      {section.meta && (
        <p className="text-xs text-[var(--text-muted)] mb-3 ml-3.5 px-3 py-2 rounded-lg bg-[rgba(245,158,11,0.06)] border border-[rgba(245,158,11,0.1)]">
          {section.meta}
        </p>
      )}
      {open && (
        <div className="space-y-2">
          {section.stocks.map(s => (
            <StockCard
              key={s.ticker}
              stock={s}
              livePrice={prices[s.ticker] ?? null}
              fundData={fundamentals[s.ticker] ?? null}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function MarketIndicatorBlock({ data }) {
  const [open, setOpen] = useState(false);
  if (!data) return null;
  return (
    <div className="mb-6">
      <button
        className="w-full flex items-center justify-between mb-3 group"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-5 rounded-full bg-gradient-to-b from-[var(--accent-purple)] to-[#578bfa]" />
          <h2 className="text-base font-bold tracking-tight group-hover:text-[var(--accent-cyan)] transition-colors">
            {data.title}
          </h2>
          <span className="text-[10px] text-[var(--text-muted)] px-2 py-0.5 rounded-full bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
            更新 {data.updated}
          </span>
        </div>
        {open ? <ChevronUp size={14} className="text-[var(--text-muted)]" /> : <ChevronDown size={14} className="text-[var(--text-muted)]" />}
      </button>
      {open && (
        <div className="card p-4 space-y-4">
          <div>
            <p className="text-xs font-semibold text-[var(--accent-green)] mb-2">真反彈確認條件（全部達標才可加倉）</p>
            <ul className="space-y-1">
              {data.confirm_conditions.map((c, i) => (
                <li key={i} className="text-xs text-[var(--text-secondary)] flex items-start gap-1.5">
                  <span className="text-[var(--accent-green)] mt-0.5">✓</span>{c}
                </li>
              ))}
            </ul>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {data.indicators.map(ind => (
              <div key={ind.name} className="bg-[rgba(255,255,255,0.03)] rounded-lg p-2">
                <p className="text-[10px] font-bold text-[var(--text-muted)]">{ind.name}</p>
                <p className="text-xs text-[var(--text-secondary)] mt-0.5">{ind.status}</p>
                {ind.note && <p className="text-[10px] text-[var(--text-muted)] mt-0.5">{ind.note}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function MimiPlaybook() {
  const [playbook,      setPlaybook]      = useState(null);
  const [prices,        setPrices]        = useState({});
  const [fundamentals,  setFundamentals]  = useState({});
  const [loading,       setLoading]       = useState(true);
  const [priceLoading,  setPriceLoading]  = useState(false);
  const [fundLoading,   setFundLoading]   = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get('/api/mimi');
      setPlaybook(r.data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const loadPrices = async () => {
    setPriceLoading(true);
    try {
      const r = await axios.get('/api/mimi/prices');
      setPrices(r.data.prices ?? {});
    } catch {
      // ignore
    } finally {
      setPriceLoading(false);
    }
  };

  const loadFundamentals = async () => {
    setFundLoading(true);
    try {
      const r = await axios.get('/api/mimi/fundamentals');
      setFundamentals(r.data.fundamentals ?? {});
    } catch {
      // ignore
    } finally {
      setFundLoading(false);
    }
  };

  useEffect(() => {
    load();
    loadPrices();
    loadFundamentals();
  }, []);

  return (
    <div className="container py-10 animate-fade-in">
      {/* Header */}
      <div className="flex items-start justify-between mb-8 gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#0052ff] to-[var(--accent-purple)] flex items-center justify-center shadow-lg shadow-[rgba(0,82,255,0.25)]">
            <BookOpen size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Mimi Playbook</h1>
            <p className="text-xs text-[var(--text-muted)]">
              彙整自 MimiVsJames Substack · 即時價格覆蓋
              <a
                href="https://mimivsjames2.substack.com"
                target="_blank"
                rel="noopener noreferrer"
                className="ml-2 text-[var(--accent-cyan)] hover:underline inline-flex items-center gap-0.5"
              >
                原文來源 <ArrowUpRight size={10} />
              </a>
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadFundamentals}
            disabled={fundLoading}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-sm font-medium border border-[var(--glass-border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all disabled:opacity-50"
          >
            <RefreshCw size={13} className={fundLoading ? 'animate-spin' : ''} />
            更新基本面
          </button>
          <button
            onClick={loadPrices}
            disabled={priceLoading}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-sm font-medium border border-[var(--glass-border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all disabled:opacity-50"
          >
            <RefreshCw size={13} className={priceLoading ? 'animate-spin' : ''} />
            更新價格
          </button>
        </div>
      </div>

      {/* Summary ticker bar */}
      {!priceLoading && Object.keys(prices).length > 0 && (
        <div className="mb-6 flex flex-wrap gap-2">
          {Object.entries(prices).filter(([, v]) => v != null).map(([ticker, price]) => (
            <Link key={ticker} href={`/stock/${ticker}`}>
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-[rgba(0,82,255,0.06)] border border-[rgba(0,82,255,0.12)] text-[var(--text-secondary)] hover:text-[var(--accent-cyan)] hover:border-[rgba(0,82,255,0.3)] transition-all cursor-pointer">
                {ticker} <span className="text-[var(--text-muted)]">${price}</span>
              </span>
            </Link>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20 gap-2 text-sm text-[var(--text-muted)]">
          <RefreshCw size={16} className="animate-spin text-[#0052ff]" />
          載入中…
        </div>
      ) : !playbook ? (
        <div className="card p-16 text-center text-sm text-[var(--text-muted)]">
          無法載入 Playbook 資料
        </div>
      ) : (
        <div>
          {playbook.sections?.map(sec => (
            <SectionBlock key={sec.id} section={sec} prices={prices} fundamentals={fundamentals} />
          ))}
          <MarketIndicatorBlock data={playbook.market_indicators} />
        </div>
      )}
    </div>
  );
}
