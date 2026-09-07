import { Route, Routes } from 'react-router-dom';
import { DashboardLayout } from './layout/DashboardLayout';
import { DashboardPage } from './pages/DashboardPage';
import { MarketScanPage } from './pages/MarketScanPage';
import { AnalysisPage } from './pages/AnalysisPage';
import { ResearchPage } from './pages/ResearchPage';
import { RiskManagerPage } from './pages/RiskManagerPage';
import { TradePlansPage } from './pages/TradePlansPage';
import { PortfolioPage } from './pages/PortfolioPage';
import { SettingsPage } from './pages/SettingsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<DashboardLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="scan" element={<MarketScanPage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="research" element={<ResearchPage />} />
        <Route path="risk" element={<RiskManagerPage />} />
        <Route path="trade-plans" element={<TradePlansPage />} />
        <Route path="portfolio" element={<PortfolioPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
