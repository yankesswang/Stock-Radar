import { Route, Switch } from 'wouter';
import Navbar from './components/Navbar';
import MacNotifier from './components/MacNotifier';
import Home from './pages/Home';
import ValuationList from './pages/ValuationList';
import StockDetail from './pages/StockDetail';
import StockMetrics from './pages/StockMetrics';
import StockStatistics from './pages/StockStatistics';
import StockRatios from './pages/StockRatios';
import StockForecast from './pages/StockForecast';
import StockDCF from './pages/StockDCF';
import AddStock from './pages/AddStock';
import StockPE from './pages/StockPE';
import SignalWatchlist from './pages/SignalWatchlist';
import PortfolioList from './pages/PortfolioList';
import PortfolioDetail from './pages/PortfolioDetail';
import GSheetPortfolio from './pages/GSheetPortfolio';
import EarningsCalendar from './pages/EarningsCalendar';
import Compare from './pages/Compare';
import PriceAlerts from './pages/PriceAlerts';
import Watchlists from './pages/Watchlists';
import MarketDetail from './pages/MarketDetail';
import MimiPlaybook from './pages/MimiPlaybook';

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
          <Route path="/earnings" component={EarningsCalendar} />
          <Route path="/compare" component={Compare} />
          <Route path="/alerts" component={PriceAlerts} />
          <Route path="/watchlists" component={Watchlists} />
          <Route path="/market/:symbol" component={MarketDetail} />
          <Route path="/signals" component={SignalWatchlist} />
          <Route path="/portfolio/gsheet" component={GSheetPortfolio} />
          <Route path="/portfolio/:id" component={PortfolioDetail} />
          <Route path="/portfolio" component={PortfolioList} />
          <Route path="/mimi" component={MimiPlaybook} />
          <Route path="/add-stock" component={AddStock} />
          <Route>
            <div className="container py-20 text-center text-[var(--text-muted)]">404 - Not Found</div>
          </Route>
        </Switch>
      </main>
    </div>
  );
}

export default App;
