import { HashRouter, Route, Routes } from "react-router-dom";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import { Overview } from "@/pages/Overview";

export default function App() {
  return (
    <HashRouter>
      <div className="min-h-screen bg-bg text-ink">
        <header className="border-b border-line px-6 py-4">
          <h1 className="text-lg font-semibold">Trax Inventory Optimizer</h1>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/parts/:pn/:location" element={<PartDrillDown />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
