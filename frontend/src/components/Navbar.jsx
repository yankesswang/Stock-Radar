import { useState, useEffect } from 'react';
import { Link, useLocation } from 'wouter';
import {
  TrendingUp,
  PlusCircle,
  Radar,
  Briefcase,
  CalendarDays,
  BarChart3,
  Bell,
  Target,
  Globe2,
  ChevronDown,
  LayoutList,
  Menu,
  X,
  MessageSquare,
} from 'lucide-react';
import GlobalSearch from './GlobalSearch';
import ThemeToggle from './ThemeToggle';

// Most-used links stay flat on the bar.
const PRIMARY = [
  { href: '/', label: 'Home' },
  { href: '/portfolio/gsheet', label: 'Portfolio', icon: Briefcase, matchPrefix: '/portfolio' },
  { href: '/valuations', label: 'Valuations', icon: BarChart3 },
];

// Everything else is grouped into dropdowns — each item appears exactly once.
//
// 「分析」原本平攤 11 個項目，其中三組是同一件事的不同切面（市場／戰情／財報）。
// 現在一個切面群只放一個入口，同群的其他頁走頁內分頁（components/ClusterTabs.jsx），
// 順序就是看盤的順序：大盤 → 今天發生什麼 → 我要做什麼 → 為什麼 → 橫向比較。
const GROUPS = [
  {
    label: '分析',
    icon: Radar,
    items: [
      { href: '/macro', label: '市場', icon: Globe2, hint: 'Macro · 板塊 RS',
        match: ['/macro', '/sectors'] },
      { href: '/war-room', label: 'War Room', icon: Radar, hint: '戰情室 · 盤後復盤 · 訊號雷達 · 新聞線／市場日報',
        match: ['/war-room', '/review', '/signals'] },
      { href: '/earnings', label: '財報', icon: CalendarDays, hint: '行事曆 · 結論 · 論點追蹤',
        match: ['/earnings'] },
      { href: '/compare', label: 'Compare', icon: BarChart3, hint: '同業橫向比較' },
    ],
  },
  {
    // 自己維護的清單與通知 —— 是輸入，不是分析結果，所以不掛在「分析」下面。
    label: '監控',
    icon: Bell,
    items: [
      { href: '/alerts', label: '價格警報', icon: Bell },
      { href: '/watchlists', label: '觀察清單', icon: LayoutList },
      { href: '/mimi-watchlist', label: 'Mimi 清單', icon: Target },
      { href: '/qa', label: '問答紀錄', icon: MessageSquare, hint: '個股問答的統一紀錄' },
    ],
  },
];

export default function Navbar() {
  const [location] = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (href, matchPrefix) => {
    if (matchPrefix) return location.startsWith(matchPrefix);
    return location === href || (href !== '/' && location.startsWith(href));
  };
  // 群組項目代表整個切面群（市場 = /macro + /sectors …），在群內任一頁都要亮著
  const itemActive = (it) =>
    (it.match || []).some((m) => location.startsWith(m)) || isActive(it.href, it.matchPrefix);

  // Close the mobile drawer whenever the route changes (a link was tapped).
  useEffect(() => {
    setMobileOpen(false);
  }, [location]);

  return (
    <header className="fixed top-4 left-0 right-0 z-50 px-3 lg:px-6">
      <nav className="glass mx-auto w-full max-w-[1400px] rounded-2xl shadow-[0_16px_32px_rgba(10,11,13,0.08)]">
        <div className="px-3 py-3 lg:px-5">
          <div className="flex items-center gap-3 lg:gap-4">
            <Link href="/" className="flex items-center gap-2.5 cursor-pointer shrink-0 group">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#0052ff] to-[#578bfa] flex items-center justify-center shadow-lg shadow-[rgba(0,82,255,0.35)]">
                <TrendingUp size={16} className="text-white" />
              </div>
              <span className="hidden sm:block text-[15px] font-bold tracking-tight">
                Stock<span className="text-gradient">Radar</span>
              </span>
            </Link>

            <div className="flex-1 min-w-0 xl:flex-initial xl:shrink-0">
              <GlobalSearch />
            </div>

            {/* Desktop inline nav — only when wide enough to not overlap the
                search box (needs ~1280px for all 7 items + search + Add). */}
            <div className="hidden xl:flex flex-1 min-w-0 items-center justify-end gap-1">
              {PRIMARY.map((item) => (
                <NavLink
                  key={item.href}
                  href={item.href}
                  active={isActive(item.href, item.matchPrefix)}
                  icon={item.icon}
                >
                  {item.label}
                </NavLink>
              ))}

              {GROUPS.map((group) => (
                <NavGroup key={group.label} group={group} itemActive={itemActive} />
              ))}
            </div>

            <ThemeToggle className="hidden xl:inline-flex h-9 w-9" size={15} />

            <Link
              href="/add-stock"
              className={`hidden xl:inline-flex shrink-0 items-center gap-1.5 px-3.5 py-2 rounded-full text-sm font-semibold cursor-pointer transition-all duration-200 ${
                isActive('/add-stock')
                  ? 'bg-[#578bfa] text-white shadow-[0_8px_20px_rgba(0,82,255,0.28)]'
                  : 'bg-[#0052ff] text-white hover:bg-[#578bfa] shadow-[0_8px_18px_rgba(0,82,255,0.2)] hover:shadow-[0_10px_22px_rgba(0,82,255,0.3)]'
              }`}
            >
              <PlusCircle size={14} />
              Add
            </Link>

            {/* Mobile: theme toggle + hamburger (hidden on desktop) */}
            <ThemeToggle className="xl:hidden h-10 w-10" size={17} />
            <button
              type="button"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label={mobileOpen ? '關閉選單' : '開啟選單'}
              aria-expanded={mobileOpen}
              className="xl:hidden shrink-0 inline-flex items-center justify-center w-10 h-10 rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] transition-colors active:bg-[rgba(0,82,255,0.06)]"
            >
              {mobileOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>

          {/* Mobile drawer */}
          {mobileOpen && (
            <div className="xl:hidden mt-3 pt-3 border-t border-[var(--glass-border)] flex flex-col gap-1 animate-fade-in">
              {PRIMARY.map((item) => (
                <MobileLink
                  key={item.href}
                  href={item.href}
                  active={isActive(item.href, item.matchPrefix)}
                  icon={item.icon}
                >
                  {item.label}
                </MobileLink>
              ))}

              {GROUPS.map((group) => {
                const GroupIcon = group.icon;
                return (
                  <div key={group.label} className="mt-2">
                    <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                      {GroupIcon && <GroupIcon size={12} />}
                      {group.label}
                    </div>
                    {group.items.map((it) => (
                      <MobileLink
                        key={it.href}
                        href={it.href}
                        active={itemActive(it)}
                        icon={it.icon}
                      >
                        {it.label}
                      </MobileLink>
                    ))}
                  </div>
                );
              })}

              <Link
                href="/add-stock"
                className={`mt-3 inline-flex items-center justify-center gap-1.5 px-3.5 py-2.5 rounded-full text-sm font-semibold cursor-pointer transition-all duration-200 ${
                  isActive('/add-stock')
                    ? 'bg-[#578bfa] text-white'
                    : 'bg-[#0052ff] text-white active:bg-[#578bfa]'
                }`}
              >
                <PlusCircle size={14} />
                Add Stock
              </Link>
            </div>
          )}
        </div>
      </nav>
    </header>
  );
}

// Full-width tap target used inside the mobile drawer.
function MobileLink({ href, active, children, icon: Icon }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm font-medium cursor-pointer transition-colors ${
        active
          ? 'bg-[rgba(0,82,255,0.11)] text-[var(--accent-cyan)]'
          : 'text-[var(--text-secondary)] active:bg-[rgba(0,82,255,0.06)]'
      }`}
    >
      {Icon && <Icon size={15} />}
      {children}
    </Link>
  );
}

function NavLink({ href, active, children, icon: Icon }) {
  return (
    <Link
      href={href}
      className={`inline-flex items-center gap-1.5 rounded-xl font-medium cursor-pointer transition-all duration-200 whitespace-nowrap px-3 py-1.5 text-xs ${
        active
          ? 'bg-[rgba(0,82,255,0.11)] text-[var(--accent-cyan)] border border-[rgba(0,82,255,0.2)]'
          : 'text-[var(--text-secondary)] border border-transparent hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.06)]'
      }`}
    >
      {Icon && <Icon size={12} />}
      {children}
    </Link>
  );
}

// Hover-opened dropdown (the inner padding bridges button→panel so it stays open).
function NavGroup({ group, itemActive }) {
  const { label, icon: Icon, items } = group;
  const anyActive = items.some(itemActive);
  return (
    <div className="relative group shrink-0">
      <button
        className={`inline-flex items-center gap-1.5 rounded-xl font-medium cursor-pointer transition-all duration-200 whitespace-nowrap px-3 py-1.5 text-xs ${
          anyActive
            ? 'bg-[rgba(0,82,255,0.11)] text-[var(--accent-cyan)] border border-[rgba(0,82,255,0.2)]'
            : 'text-[var(--text-secondary)] border border-transparent hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.06)]'
        }`}
      >
        {Icon && <Icon size={12} />}
        {label}
        <ChevronDown size={12} className="opacity-60 transition-transform duration-200 group-hover:rotate-180" />
      </button>
      <div className="absolute right-0 top-full pt-2 hidden group-hover:block z-[60]">
        <div className="min-w-[190px] rounded-xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] shadow-2xl shadow-[rgba(10,11,13,0.22)] p-1.5">
          {items.map((it) => {
            const active = itemActive(it);
            const ItemIcon = it.icon;
            return (
              <Link
                key={it.href}
                href={it.href}
                className={`flex items-start gap-2.5 rounded-lg px-3 py-2 text-xs font-medium cursor-pointer transition-colors ${
                  active
                    ? 'bg-[rgba(0,82,255,0.11)] text-[var(--accent-cyan)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.06)]'
                }`}
              >
                {ItemIcon && <ItemIcon size={13} className="mt-0.5 shrink-0" />}
                <span className="min-w-0">
                  {it.label}
                  {/* 群內有哪幾個分頁，在下拉就說清楚 —— 不然「市場」看不出含板塊 RS */}
                  {it.hint && (
                    <span className="block text-[10.5px] font-normal text-[var(--text-muted)]">{it.hint}</span>
                  )}
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
