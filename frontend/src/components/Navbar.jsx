import { Link, useLocation } from 'wouter';
import { TrendingUp, PlusCircle, Radar, Briefcase, CalendarDays, BarChart3, Bell, BookMarked } from 'lucide-react';
import GlobalSearch from './GlobalSearch';

export default function Navbar() {
  const [location] = useLocation();
  const isActive = (path) => location === path;

  return (
    <nav className="glass sticky top-0 z-50 border-b border-[rgba(91,97,110,0.18)] shadow-sm">
      <div className="container mx-auto h-14 flex items-center gap-4">

        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 cursor-pointer shrink-0 group">
          <div className="w-8 h-8 rounded-lg bg-[#0052ff] flex items-center justify-center shadow-lg shadow-[rgba(0,82,255,0.35)]">
            <TrendingUp size={15} className="text-white" />
          </div>
          <span className="text-[15px] font-bold tracking-tight">
            Stock<span className="text-gradient">Radar</span>
          </span>
        </Link>

        {/* Search */}
        <div className="flex-1 flex justify-center">
          <GlobalSearch />
        </div>

        {/* Nav links */}
        <div className="flex items-center gap-0.5 shrink-0">
          <NavLink href="/"              active={isActive('/')}>Home</NavLink>
          <NavLink href="/valuations"    active={isActive('/valuations')}>Valuations</NavLink>
          <NavLink href="/compare"       active={location.startsWith('/compare')}>
            <BarChart3 size={13} />Compare
          </NavLink>
          <NavLink href="/earnings"      active={isActive('/earnings')}>
            <CalendarDays size={13} />Earnings
          </NavLink>
          <NavLink href="/watchlists"    active={location.startsWith('/watchlists')}>
            <BookMarked size={13} />Watchlists
          </NavLink>
          <NavLink href="/alerts"        active={isActive('/alerts')}>
            <Bell size={13} />Alerts
          </NavLink>
          <NavLink href="/signals"       active={isActive('/signals')}>
            <Radar size={13} />Signals
          </NavLink>
          <NavLink href="/portfolio/gsheet" active={location.startsWith('/portfolio')}>
            <Briefcase size={13} />Portfolio
          </NavLink>

          {/* Add Stock — pill CTA */}
          <Link
            href="/add-stock"
            className={`ml-2 flex items-center gap-1.5 px-3.5 py-1.5 rounded-[56px] text-sm font-semibold transition-all duration-200 ${
              isActive('/add-stock')
                ? 'bg-[#578bfa] text-white shadow-[0_2px_12px_rgba(0,82,255,0.4)]'
                : 'bg-[#0052ff] text-white hover:bg-[#578bfa] shadow-[0_2px_8px_rgba(0,82,255,0.25)] hover:shadow-[0_4px_16px_rgba(0,82,255,0.4)]'
            }`}
          >
            <PlusCircle size={13} />
            Add
          </Link>
        </div>
      </div>
    </nav>
  );
}

function NavLink({ href, active, children }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-150 ${
        active
          ? 'bg-[rgba(0,82,255,0.1)] text-[#578bfa]'
          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(91,97,110,0.08)]'
      }`}
    >
      {children}
    </Link>
  );
}
