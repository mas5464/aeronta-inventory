import { Overview } from "@/pages/Overview";

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line px-6 py-4">
        <h1 className="text-lg font-semibold">Trax Inventory Optimizer</h1>
      </header>
      <main>
        <Overview />
      </main>
    </div>
  );
}
