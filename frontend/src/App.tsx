import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthGate } from './components/AuthGate';
import { DashboardLayout } from './layout/DashboardLayout';
import {
  AnalysisPage,
  BacktestsPage,
  ForecastLabPage,
  CalendarPage,
  CommitteePage,
  DashboardPage,
  MarketScanPage,
  MarketTerminalPage,
  PortfolioPage,
  RatingsPage,
  ScreenerPage,
  SettingsPage,
  SmartMoneyPage,
} from './routePages';

// One page per company at /stock/:symbol. The old /analysis?symbol=X and
// /research?symbol=X links/bookmarks land here instead of 404ing.
function CompanyRedirect() {
  const symbol = new URLSearchParams(useLocation().search).get('symbol');
  return <Navigate to={symbol ? `/stock/${encodeURIComponent(symbol)}` : '/stock'} replace />;
}

export default function App() {
  return (
    <AuthGate>
      <Routes>
        <Route element={<DashboardLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="scan" element={<MarketScanPage />} />
          <Route path="screener" element={<ScreenerPage />} />
          <Route path="stock" element={<AnalysisPage />} />
          <Route path="stock/:symbol" element={<AnalysisPage />} />
          <Route path="analysis" element={<CompanyRedirect />} />
          <Route path="research" element={<CompanyRedirect />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="ratings" element={<RatingsPage />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="terminal" element={<MarketTerminalPage />} />
          <Route path="smart-money" element={<SmartMoneyPage />} />
          <Route path="committee" element={<CommitteePage />} />
          <Route path="backtests" element={<BacktestsPage />} />
          <Route path="forecast-lab" element={<ForecastLabPage />} />
          <Route path="settings"element={<SettingsPage />} />
        </Route>
      </Routes>
    </AuthGate>
  );
}
