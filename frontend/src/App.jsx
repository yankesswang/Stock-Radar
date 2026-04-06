import { Route, Switch } from 'wouter';
import Navbar from './components/Navbar';
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

function App() {
  return (
    <div className="min-h-screen bg-[var(--bg-primary)] text-[var(--text-primary)]">
      <Navbar />
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
        <Route path="/signals" component={SignalWatchlist} />
        <Route path="/portfolio/gsheet" component={GSheetPortfolio} />
        <Route path="/portfolio/:id" component={PortfolioDetail} />
        <Route path="/portfolio" component={PortfolioList} />
        <Route path="/add-stock" component={AddStock} />
        <Route>
          <div className="container py-20 text-center text-[var(--text-muted)]">404 - Not Found</div>
        </Route>
      </Switch>
    </div>
  );
}

export default App;
