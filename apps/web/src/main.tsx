import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "@fontsource-variable/instrument-sans";
import "./styles/globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // `retry: 1`'s scheduled retry attempt got stuck indefinitely in
      // `fetchStatus: 'paused'` in some browser contexts and never actually
      // re-fired — the query never reached `isError`, so <QueryError>'s
      // Retry button never appeared (every view already has that button;
      // an automatic retry was never load-bearing). `networkMode: 'always'`
      // is kept as defense-in-depth against the same online/offline-signal
      // issue if retries are ever reintroduced.
      retry: false,
      networkMode: "always",
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
