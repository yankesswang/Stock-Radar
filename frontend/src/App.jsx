import { lazy, Suspense } from 'react';
import { Route, Switch } from 'wouter';
import Navbar from './components/Navbar';
import MacNotifier from './components/MacNotifier';
import Home from './pages/Home';

const ValuationList = lazy(() => import('./pages/ValuationList'));
const StockDetail = lazy(() => import('./pages/StockDetail'));
const StockMetrics = lazy(() => import('./pages/StockMetrics'));
const StockStatistics = lazy(() => import('./pages/StockStatistics'));
const StockRatios = lazy(() => import('./pages/StockRatios'));
const StockForecast = lazy(() => import('./pages/StockForecast'));
const StockDCF = lazy(() => import('./pages/StockDCF'));
const AddStock = lazy(() => import('./pages/AddStock'));
const StockPE = lazy(() => import('./pages/StockPE'));
const SignalWatchlist = lazy(() => import('./pages/SignalWatchlist'));
const PortfolioList = lazy(() => import('./pages/PortfolioList'));
const PortfolioDetail = lazy(() => import('./pages/PortfolioDetail'));
const GSheetPortfolio = lazy(() => import('./pages/GSheetPortfolio'));
const EarningsCalendar = lazy(() => import('./pages/EarningsCalendar'));
const Compare = lazy(() => import('./pages/Compare'));
const PriceAlerts = lazy(() => import('./pages/PriceAlerts'));
const Watchlists = lazy(() => import('./pages/Watchlists'));
const MarketDetail = lazy(() => import('./pages/MarketDetail'));
const MimiWatchlist = lazy(() => import('./pages/MimiWatchlist'));
const Macro = lazy(() => import('./pages/Macro'));
const Sectors = lazy(() => import('./pages/Sectors'));
const FinanceDigest = lazy(() => import('./pages/FinanceDigest'));
const MarketDigest = lazy(() => import('./pages/MarketDigest'));
const StockOptions = lazy(() => import('./pages/StockOptions'));
const StockPeers = lazy(() => import('./pages/StockPeers'));
const StockAIAnalysis = lazy(() => import('./pages/StockAIAnalysis'));
import StockEarningsNotes from './pages/StockEarningsNotes';
const AIPortfolioReport = lazy(() => import('./pages/AIPortfolioReport'));
const WarRoom = lazy(() => import('./pages/WarRoom'));

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-32 text-[var(--text-muted)]">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent" />
    </div>
  );
}

function App() {
  return (
    <div className="relative min-h-screen bg-[var(--bg-primary)] text-[var(--text-primary)]">
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -top-24 left-1/4 h-72 w-72 rounded-full bg-[rgba(0,82,255,0.14)] blur-3xl" />
        <div className="absolute bottom-0 right-1/4 h-72 w-72 rounded-full bg-[rgba(87,139,250,0.12)] blur-3xl" />
      </div>
      <Navbar />
      <MacNotifier />
      <main className="pt-20 pb-8 lg:pt-24">
        <Suspense fallback={<PageLoader />}>
          <Switch>
            <Route path="/" component={Home} />
            <Route path="/valuations" component={ValuationList} />
            <Route path="/stock/:ticker" component={StockDetail} />
            <Route path="/stock/:ticker/metrics" component={StockMetrics} />
            <Route path="/stock/:ticker/statistics" component={StockStatistics} />
            <Route path="/stock/:ticker/ratios" component={StockRatios} />
            <Route path="/stock/:ticker/forecast" component={StockForecast} />
            <Route path="/stock/:ticker/dcf" component={StockDCF} />
            <Route path="/stock/:ticker/pe" component={StockPE} />
            <Route path="/stock/:ticker/options" component={StockOptions} />
            <Route path="/stock/:ticker/peers" component={StockPeers} />
            <Route path="/stock/:ticker/ai" component={StockAIAnalysis} />
            <Route path="/stock/:ticker/earnings-notes" component={StockEarningsNotes} />
            <Route path="/macro" component={Macro} />
            <Route path="/sectors" component={Sectors} />
            <Route path="/earnings" component={EarningsCalendar} />
            <Route path="/compare" component={Compare} />
            <Route path="/alerts" component={PriceAlerts} />
            <Route path="/watchlists" component={Watchlists} />
            <Route path="/market/:symbol" component={MarketDetail} />
            <Route path="/signals" component={SignalWatchlist} />
            <Route path="/portfolio/gsheet" component={GSheetPortfolio} />
            <Route path="/portfolio/:id" component={PortfolioDetail} />
            <Route path="/portfolio" component={PortfolioList} />
            <Route path="/market-digest" component={MarketDigest} />
            <Route path="/finance-digest" component={FinanceDigest} />
            <Route path="/ai-report" component={AIPortfolioReport} />
            <Route path="/war-room" component={WarRoom} />
            <Route path="/mimi-watchlist" component={MimiWatchlist} />
            <Route path="/add-stock" component={AddStock} />
            <Route>
              <div className="container py-20 text-center text-[var(--text-muted)]">404 - Not Found</div>
            </Route>
          </Switch>
        </Suspense>
      </main>
    </div>
  );
}

export default App;
