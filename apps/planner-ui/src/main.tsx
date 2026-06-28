import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { FakePlannerClient, HttpPlannerClient, type PlannerClient } from "./api/client";
import { SAMPLE_HISTORY, SAMPLE_SEED } from "./api/sample";
import "./styles/tokens.css";

const tenant = import.meta.env.VITE_TENANT ?? "acme";

// VITE_FAKE=1 runs fully offline against the in-memory sample; otherwise point
// VITE_BFF_URL at a running `create_planner_app` (uvicorn).
const client: PlannerClient = import.meta.env.VITE_FAKE
  ? new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })), SAMPLE_HISTORY)
  : new HttpPlannerClient(import.meta.env.VITE_BFF_URL ?? "http://localhost:8000");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App client={client} tenant={tenant} />
  </StrictMode>,
);
