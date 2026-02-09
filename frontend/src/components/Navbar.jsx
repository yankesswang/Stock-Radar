import { Link, useLocation } from 'wouter';
import { TrendingUp } from 'lucide-react';

export default function Navbar() {
  const [location] = useLocation();

  const isActive = (path) => location === path;

  return (
    <nav className="glass sticky top-0 z-50 border-b border-[var(--glass-border)]">
      <div className="container mx-auto h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5 cursor-pointer group">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg shadow-purple-500/20">
            <TrendingUp size={16} className="text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight">
            PE<span className="text-gradient">Valuation</span>
          </span>
        </Link>

        <div className="flex items-center gap-1">
          <NavLink href="/" active={isActive('/')}>Home</NavLink>
          <NavLink href="/valuations" active={isActive('/valuations')}>Valuations</NavLink>
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
