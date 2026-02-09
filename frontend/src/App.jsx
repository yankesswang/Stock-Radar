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
        <Route>
          <div className="container py-20 text-center text-[var(--text-muted)]">404 - Not Found</div>
        </Route>
      </Switch>
    </div>
  );
}

export default App;
