import { Link, useLocation } from 'wouter';
import { TrendingUp, PlusCircle, Radar, Briefcase } from 'lucide-react';
import GlobalSearch from './GlobalSearch';

export default function Navbar() {
  const [location] = useLocation();
  const isActive = (path) => location === path;

  return (
    <nav className="glass sticky top-0 z-50 border-b border-[var(--glass-border)]">
      <div className="container mx-auto h-14 flex items-center gap-4">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 cursor-pointer group shrink-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg shadow-purple-500/20">
            <TrendingUp size={16} className="text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight">
            PE<span className="text-gradient">Valuation</span>
          </span>
        </Link>

        {/* Search — grows to fill space */}
        <div className="flex-1 flex justify-center">
          <GlobalSearch />
        </div>

        {/* Nav links */}
        <div className="flex items-center gap-1 shrink-0">
          <NavLink href="/" active={isActive('/')}>Home</NavLink>
          <NavLink href="/valuations" active={isActive('/valuations')}>Valuations</NavLink>
          <NavLink href="/signals" active={isActive('/signals')}>
            <span className="flex items-center gap-1.5">
              <Radar size={13} />
              Signals
            </span>
          </NavLink>
          <NavLink href="/portfolio/gsheet" active={location.startsWith('/portfolio')}>
            <span className="flex items-center gap-1.5">
              <Briefcase size={13} />
              Portfolio
            </span>
          </NavLink>
          <NavLink href="/add-stock" active={isActive('/add-stock')}>
            <span className="flex items-center gap-1.5">
              <PlusCircle size={13} />
              Add Stock
            </span>
          </NavLink>
        </div>
      </div>
    </nav>
  );
}

function NavLink({ href, active, children }) {
  return (
    <Link
      href={href}
      className={`px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all duration-200 ${
        active
          ? 'bg-[rgba(139,92,246,0.12)] text-[var(--accent-purple)]'
          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
      }`}
    >
      {children}
    </Link>
  );
}
