import { lazy, Suspense } from 'react';
import { Route, Switch, Redirect } from 'wouter';
import Navbar from './components/Navbar';
import MacNotifier from './components/MacNotifier';
import StockChatDock from './components/StockChatDock';
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
const EarningsConclusions = lazy(() => import('./pages/EarningsConclusions'));
const EarningsTheses = lazy(() => import('./pages/EarningsTheses'));
const StockSegments = lazy(() => import('./pages/StockSegments'));
const Compare = lazy(() => import('./pages/Compare'));
const PriceAlerts = lazy(() => import('./pages/PriceAlerts'));
const Watchlists = lazy(() => import('./pages/Watchlists'));
const MarketDetail = lazy(() => import('./pages/MarketDetail'));
const MimiWatchlist = lazy(() => import('./pages/MimiWatchlist'));
const Macro = lazy(() => import('./pages/Macro'));
const Sectors = lazy(() => import('./pages/Sectors'));
const StockOptions = lazy(() => import('./pages/StockOptions'));
const StockAIAnalysis = lazy(() => import('./pages/StockAIAnalysis'));
const StockPeers = lazy(() => import('./pages/StockPeers'));
import StockEarningsNotes from './pages/StockEarningsNotes';
const WarRoom = lazy(() => import('./pages/WarRoom'));
const PostMarketReview = lazy(() => import('./pages/PostMarketReview'));
const Briefing = lazy(() => import('./pages/Briefing'));
const QaLog = lazy(() => import('./pages/QaLog'));

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
      <StockChatDock />
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
            <Route path="/stock/:ticker/segments" component={StockSegments} />
            {/* 三大表已併進 metrics 頁的「財務報表與基本面」區塊，舊書籤直接落到損益表分頁 */}
            <Route path="/stock/:ticker/financials">
                {(p) => <Redirect to={`/stock/${p.ticker}/metrics?tab=income`} replace />}
            </Route>
            <Route path="/qa" component={QaLog} />
            <Route path="/macro" component={Macro} />
            <Route path="/sectors" component={Sectors} />
            <Route path="/earnings" component={EarningsCalendar} />
            <Route path="/earnings-conclusions" component={EarningsConclusions} />
            <Route path="/earnings-theses" component={EarningsTheses} />
            <Route path="/compare" component={Compare} />
            <Route path="/alerts" component={PriceAlerts} />
            <Route path="/watchlists" component={Watchlists} />
            <Route path="/market/:symbol" component={MarketDetail} />
            <Route path="/signals" component={SignalWatchlist} />
            <Route path="/portfolio/gsheet" component={GSheetPortfolio} />
            <Route path="/portfolio/:id" component={PortfolioDetail} />
            <Route path="/portfolio" component={PortfolioList} />
            <Route path="/briefing" component={Briefing} />
            {/* 舊書籤：直接進完整版的對應分頁，不再繞經 Portfolio 頁 */}
            <Route path="/market-digest"><Redirect to="/briefing?tab=market" /></Route>
            <Route path="/finance-digest"><Redirect to="/briefing?tab=notes" /></Route>
            {/* 復盤的完整畫面只有一份，在 /review —— 每日報告不再重畫一次 */}
            <Route path="/ai-report"><Redirect to="/review" /></Route>
            <Route path="/war-room" component={WarRoom} />
            <Route path="/review" component={PostMarketReview} />
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
