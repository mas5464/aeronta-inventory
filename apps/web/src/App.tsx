import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { cn } from "@/lib/utils";
import { AiRecommendations } from "@/features/recommendations/AiRecommendations";
import { DataConnections } from "@/features/feeds/DataConnections";
import { ForecastServiceLevels } from "@/features/forecast/ForecastServiceLevels";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import { Scenarios } from "@/features/scenarios/Scenarios";
import { Workbench } from "@/features/workbench/Workbench";
import { Overview } from "@/pages/Overview";

const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/workbench", label: "Workbench" },
  { to: "/recommendations", label: "AI Recommendations" },
  { to: "/forecast", label: "Forecast & Service Levels" },
  { to: "/scenarios", label: "What-If Scenarios" },
  { to: "/data", label: "Data & Connections" },
];

function AppNav() {
  return (
    <nav className="flex gap-1 px-6" aria-label="Primary">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cn(
              "rounded-t-control px-3 py-2 text-sm font-medium text-ink-2 hover:text-ink",
              isActive && "bg-panel text-ink",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <HashRouter>
      <div className="min-h-screen bg-bg text-ink">
        <header className="border-b border-line">
          <div className="px-6 py-4">
            <h1 className="text-lg font-semibold">Trax Inventory Optimizer</h1>
          </div>
          <AppNav />
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/workbench" element={<Workbench />} />
            <Route path="/recommendations" element={<AiRecommendations />} />
            <Route path="/forecast" element={<ForecastServiceLevels />} />
            <Route path="/scenarios" element={<Scenarios />} />
            <Route path="/data" element={<DataConnections />} />
            <Route path="/parts/:pn/:location" element={<PartDrillDown />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
