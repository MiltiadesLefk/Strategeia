import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthGate } from './components/AuthGate';
import { DashboardLayout } from './layout/DashboardLayout';
import {
  AnalysisPage,
  BacktestsPage,
  CalendarPage,
  DashboardPage,
  MarketScanPage,
  MarketTerminalPage,
  PortfolioPage,
  SettingsPage,
  SmartMoneyPage,
  TradePlansPage,
} from './routePages';

// Research was folded into Analysis (one page per symbol: chart + technicals
// pinned above, fundamentals/news/earnings as tabs below) — this keeps any
// old /research?symbol=X links/bookmarks working instead of 404ing.
function ResearchRedirect() {
  const location = useLocation();
  return <Navigate to={`/analysis${location.search}`} replace />;
}

export default function App() {
  return (
    <AuthGate>
      <Routes>
        <Route element={<DashboardLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="scan" element={<MarketScanPage />} />
          <Route path="analysis" element={<AnalysisPage />} />
          <Route path="research" element={<ResearchRedirect />} />
          <Route path="trade-plans" element={<TradePlansPage />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="terminal" element={<MarketTerminalPage />} />
          <Route path="smart-money" element={<SmartMoneyPage />} />
          <Route path="backtests" element={<BacktestsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </AuthGate>
  );
}
