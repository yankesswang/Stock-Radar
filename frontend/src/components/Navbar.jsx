import { Link, useLocation } from 'wouter';
import {
  TrendingUp,
  PlusCircle,
  Radar,
  Briefcase,
  CalendarDays,
  BarChart3,
  Bell,
  BookMarked,
  BookOpen,
} from 'lucide-react';
import GlobalSearch from './GlobalSearch';

const LINKS = [
  { href: '/', label: 'Home' },
  { href: '/portfolio/gsheet', label: 'Portfolio', icon: Briefcase, matchPrefix: '/portfolio' },
  { href: '/valuations', label: 'Valuations' },
  { href: '/compare', label: 'Compare', icon: BarChart3 },
  { href: '/earnings', label: 'Earnings', icon: CalendarDays },
  { href: '/watchlists', label: 'Watchlists', icon: BookMarked },
  { href: '/alerts', label: 'Alerts', icon: Bell },
  { href: '/signals', label: 'Signals', icon: Radar },
  { href: '/mimi', label: 'Mimi', icon: BookOpen },
];

export default function Navbar() {
  const [location] = useLocation();

  const isActive = (href, matchPrefix) => {
    if (matchPrefix) return location.startsWith(matchPrefix);
    return location === href || (href !== '/' && location.startsWith(href));
  };

  return (
    <header className="fixed top-4 left-0 right-0 z-50 px-3 lg:px-6">
      <nav className="glass mx-auto w-full max-w-[1400px] rounded-2xl shadow-[0_16px_32px_rgba(10,11,13,0.08)]">
        <div className="px-3 py-3 lg:px-5">
          <div className="flex items-center gap-3 lg:gap-5">
            <Link href="/" className="flex items-center gap-2.5 cursor-pointer shrink-0 group">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#0052ff] to-[#578bfa] flex items-center justify-center shadow-lg shadow-[rgba(0,82,255,0.35)]">
                <TrendingUp size={16} className="text-white" />
              </div>
              <span className="hidden sm:block text-[15px] font-bold tracking-tight">
                Stock<span className="text-gradient">Radar</span>
              </span>
            </Link>

            <div className="min-w-0 flex-1">
              <GlobalSearch />
            </div>

            <div className="hidden 2xl:flex items-center gap-1 shrink-0">
              {LINKS.map((item) => (
                <NavLink
                  key={item.href}
                  href={item.href}
                  active={isActive(item.href, item.matchPrefix)}
                  icon={item.icon}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>

            <Link
              href="/add-stock"
              className={`hidden sm:inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-sm font-semibold cursor-pointer transition-all duration-200 ${
                isActive('/add-stock')
                  ? 'bg-[#578bfa] text-white shadow-[0_8px_20px_rgba(0,82,255,0.28)]'
                  : 'bg-[#0052ff] text-white hover:bg-[#578bfa] shadow-[0_8px_18px_rgba(0,82,255,0.2)] hover:shadow-[0_10px_22px_rgba(0,82,255,0.3)]'
              }`}
            >
              <PlusCircle size={14} />
              Add
            </Link>
          </div>

          <div className="2xl:hidden mt-3 flex items-center gap-1 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {LINKS.map((item) => (
              <NavLink
                key={`mobile-${item.href}`}
                href={item.href}
                active={isActive(item.href, item.matchPrefix)}
                icon={item.icon}
                compact
              >
                {item.label}
              </NavLink>
            ))}
            <Link
              href="/add-stock"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border border-[rgba(0,82,255,0.2)] text-[var(--accent-cyan)] bg-white/85 whitespace-nowrap cursor-pointer transition-colors duration-200 hover:bg-white"
            >
              <PlusCircle size={12} />
              Add
            </Link>
          </div>
        </div>
      </nav>
    </header>
  );
}

function NavLink({ href, active, children, icon: Icon, compact = false }) {
  return (
    <Link
      href={href}
      className={`inline-flex items-center gap-1.5 rounded-xl font-medium cursor-pointer transition-all duration-200 whitespace-nowrap ${
        compact ? 'px-3 py-1.5 text-xs' : 'px-3 py-2 text-sm'
      } ${
        active
          ? 'bg-[rgba(0,82,255,0.11)] text-[var(--accent-cyan)] border border-[rgba(0,82,255,0.2)]'
          : 'text-[var(--text-secondary)] border border-transparent hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.06)]'
      }`}
    >
      {Icon && <Icon size={compact ? 12 : 13} />}
      {children}
    </Link>
  );
}
