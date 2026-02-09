import SearchBar from '../components/SearchBar';
import { TrendingUp, BarChart3, Zap, ArrowRight } from 'lucide-react';
import { Link } from 'wouter';

export default function Home() {
    return (
        <div className="relative min-h-[calc(100vh-56px)] flex flex-col items-center justify-center p-6 overflow-hidden">
            {/* Background effects */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-[-20%] left-[10%] w-[500px] h-[500px] rounded-full bg-[var(--accent-purple)] opacity-[0.04] blur-[120px]" />
                <div className="absolute bottom-[-10%] right-[15%] w-[400px] h-[400px] rounded-full bg-[var(--accent-cyan)] opacity-[0.03] blur-[100px]" />
            </div>

            <div className="relative max-w-3xl w-full space-y-10 animate-fade-in-up text-center">
                {/* Hero */}
                <div className="space-y-5">
                    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[var(--glass-border)] bg-[var(--glass-bg)] text-xs font-medium text-[var(--text-secondary)] mb-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-success)] animate-pulse" />
                        Tracking 150+ stocks
                    </div>
                    <h1 className="text-5xl md:text-7xl font-extrabold tracking-tight leading-[1.05]">
                        Fair Value<br />
                        <span className="text-gradient">Decoded</span>
                    </h1>
                    <p className="text-lg text-[var(--text-secondary)] max-w-lg mx-auto leading-relaxed">
                        Institutional-grade valuation analysis using historical PE medians and analyst growth forecasts.
                    </p>
                </div>

                {/* Search */}
                <SearchBar />

                {/* CTA */}
                <div>
                    <Link
                        href="/valuations"
                        className="inline-flex items-center gap-2 btn btn-primary text-sm px-6 py-3"
                    >
                        Browse All Valuations <ArrowRight size={16} />
                    </Link>
                </div>

                {/* Feature cards */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-8 text-left">
                    <FeatureCard
                        icon={<TrendingUp size={20} />}
                        color="text-[var(--accent-success)]"
                        bg="bg-[rgba(52,211,153,0.08)]"
                        title="Growth Forecasts"
                        desc="5-year EPS & Revenue projections from analyst consensus."
                    />
                    <FeatureCard
                        icon={<BarChart3 size={20} />}
                        color="text-[var(--accent-purple)]"
                        bg="bg-[rgba(139,92,246,0.08)]"
                        title="Historical PE"
                        desc="5-year median PE ratios to benchmark current valuations."
                    />
                    <FeatureCard
                        icon={<Zap size={20} />}
                        color="text-[var(--accent-cyan)]"
                        bg="bg-[rgba(0,212,255,0.08)]"
                        title="Real-time Data"
                        desc="Live prices with automatically updated valuation metrics."
                    />
                </div>
            </div>
        </div>
    );
}

function FeatureCard({ icon, color, bg, title, desc }) {
    return (
        <div className="card group hover:border-[rgba(255,255,255,0.12)] p-5">
            <div className={`h-10 w-10 rounded-xl ${bg} flex items-center justify-center mb-4 ${color}`}>
                {icon}
            </div>
            <h3 className="font-semibold text-[15px] mb-1.5 text-[var(--text-primary)]">{title}</h3>
            <p className="text-[var(--text-muted)] text-sm leading-relaxed">{desc}</p>
        </div>
    );
}
