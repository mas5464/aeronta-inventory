# ADR-0001: Strands vs LangGraph for the Trax IO Supervisor

**Status:** Accepted
**Date:** 2026-04-14
**Deciders:** Miguel Sosa (VP Innovation), AI Platform team
**Supersedes:** none
**Superseded by:** none

---

## Context

The Trax IO Supervisor agent orchestrates six specialist subagents on AWS Bedrock AgentCore Runtime. The Supervisor needs LLM reasoning (to handle multi-step delegation, recover from partial failures, and explain decisions), tool-calling (to invoke specialists), session and tenant context propagation, deterministic orchestration paths for the bulk of the work, and tight integration with the AgentCore platform (Memory, Identity, Gateway, Observability).

Three viable framework choices were on the table at design time: **AWS Strands Agents SDK**, **LangGraph**, and **CrewAI**. CrewAI was ruled out at the design stage (peer-network swarm semantics are a poor fit for governed industrial decisions). The remaining choice — Strands vs LangGraph — is the load-bearing one that locks in the Supervisor's surface area for years.

---

## Decision

We adopt **AWS Strands Agents SDK** as the framework for the Supervisor and as the standard pattern for any LLM-reasoning subagent that lands in subsequent phases (Causal Demand Forecaster in v2, AOG Risk in v3, etc.). The deterministic orchestration graph that Strands tools invoke remains a plain Python class (`SupervisorOrchestrator`) so it is fully testable without an LLM in the loop.

---

## Considered options

### Option A — AWS Strands Agents SDK (chosen)

Strands is a model-driven agent framework where the model decides which tools to call, in what order, and when to stop. AWS-native, with first-class integrations into Bedrock, AgentCore Runtime, AgentCore Memory, AgentCore Gateway, and AgentCore Observability. Anthropic Claude is a first-class model. Tools are typed Python functions; MCP servers are first-class.

**Pros**
- Native AWS Bedrock and AgentCore integration: traces, identity, memory, and Gateway tools wire up with minimal glue.
- Anthropic Claude is the canonical reasoning model for Strands. Sonnet 4.6 / Haiku 4.5 selection is straightforward.
- Designed for production (multi-tenant context propagation, session management, telemetry) rather than research-prototype ergonomics.
- MCP-first tool model matches our "specialist as MCP tool" topology one-to-one.
- AWS owns long-term maintenance; Strands ships in lockstep with AgentCore.

**Cons**
- Younger framework; less community content than LangGraph.
- Tighter AWS coupling — using Strands signals long-term commitment to Bedrock as the model substrate.
- Less explicit graph control than LangGraph; the model decides flow more than the engineer does.

### Option B — LangGraph

LangGraph is a graph-based agent framework where the engineer defines an explicit state machine (nodes = steps, edges = conditional transitions). LangChain ecosystem; rich community; strong observability via LangSmith.

**Pros**
- Explicit graph control: every state transition is engineer-authored, which is auditable.
- Mature ecosystem; many production case studies; large community.
- Model-agnostic (Claude, GPT, Gemini, open-source) — easier to swap.
- LangSmith offers out-of-the-box agent observability.

**Cons**
- Not native to AWS or AgentCore; integration with AgentCore Memory, Identity, Gateway requires custom adapters that we would own and maintain.
- Graph nodes that need LLM reasoning still need a model client — typically `langchain-anthropic` — but no special integration with Bedrock-only deployments.
- The explicit-graph model becomes verbose at the Supervisor layer where most paths are "let the LLM figure out which specialist to call." Either we encode every path (verbose) or we wrap an LLM-router node (then we are reinventing Strands).
- LangSmith adds a second observability surface alongside AgentCore Observability, splitting our trace data.
- LangChain ecosystem churn rate is higher than AWS-native SDKs.

### Option C — Roll our own thin Bedrock client + handcrafted orchestrator

Skip the framework. Use the raw `bedrock-runtime` API for model calls, write our own tool-calling loop, build orchestration in plain Python.

**Pros**
- Maximum control. No framework risk.
- Easiest to reason about; no abstractions to debug through.
- Smallest dependency surface — easy SOC 2 supply-chain story.

**Cons**
- We would re-implement what Strands gives for free (tool registration, session management, retry on tool error, multi-turn conversation state, AgentCore wiring).
- Every new specialist would need bespoke wiring. Phases 2–6 each cost more.
- No leverage when AWS adds new AgentCore features — we have to integrate manually.
- The "control" benefit is mostly illusory: by the time we have a clean abstraction, we have built a worse Strands.

---

## Decision rationale

Three factors drove the choice of Strands.

**Strands is on the AgentCore roadmap; LangGraph is not.** Trax IO is a multi-tenant SaaS on AWS Bedrock AgentCore. AgentCore Memory namespacing, AgentCore Identity propagation, AgentCore Gateway tool registration, and AgentCore Observability tracing all come "for free" with Strands and require glue code with LangGraph. That glue code is exactly the kind of accidental complexity we don't want a small platform team carrying for years.

**The orchestration we actually need is mostly LLM-driven dispatch.** The Supervisor's job is to look at a `(tenant, pn, location)` request, decide whether to fetch fresh data or trust the cache, decide whether the recommendation needs human approval, and explain the result. Most of that is LLM judgment — LangGraph's explicit-graph control is paying for a feature we don't need. Where determinism matters (the actual Data → Regime → Forecast → Policy → Guardrail → Writeback pipeline), we keep it as a plain Python class (`SupervisorOrchestrator`) that Strands invokes as a single tool. Best of both worlds.

**Anthropic Claude is the strategic model.** Strands treats Claude Sonnet 4.6 and Haiku 4.5 as first-class. LangGraph treats every model as equally important, which means fewer Claude-specific affordances (cached input handling, tool-call streaming semantics, prompt-cache best practices). For a product whose reasoning quality compounds with model quality, Strands is the right home.

The downside — long-term coupling to Bedrock — is acceptable. Trax IO is a multi-tenant SaaS in Trax's AWS account by design (per the design's Q3 decision). Optionality on the model substrate is theoretical; in practice we will run on Bedrock for the lifetime of v1–v6.

---

## Consequences

### Positive

- The Agent Spine Phase 7 (Supervisor) work item is small (one Strands `Agent` definition + a handful of typed tools) instead of an entire orchestration engine.
- AgentCore Memory, Identity, Gateway, Observability all "just work" — no bespoke adapters.
- Adding a new specialist in v2 (Causal Demand Forecaster) is a single new Strands `Agent` plus a tool registration; no Supervisor refactor.
- Anthropic Claude prompt-cache and tool-call semantics are exploited optimally.

### Negative

- Strands SDK version drift is now a recurring maintenance burden. Pin the version and upgrade deliberately.
- Talent pool with Strands experience is small (early 2026); LangGraph engineers will need to ramp. Mitigate with a 1-week internal Strands bootcamp at team kickoff.
- If AWS deprecates Strands or pivots the framework architecture, we own a migration. Mitigate by keeping the deterministic orchestrator (`SupervisorOrchestrator`) framework-free — replacing Strands then becomes a single-file rewrite of the entry point.

### Neutral

- We commit to one observability surface (AgentCore Observability + AWS X-Ray) instead of LangSmith. Acceptable given AgentCore's tracing maturity in 2026.

---

## Open questions deferred

- **Should the AOG Risk Agent (v3) use Strands or a custom RL loop?** Deferred to v3 design. Likely Strands for the LLM-reasoning surface (planner-facing explanation, scenario synthesis) with a separate non-LLM scoring engine, mirroring v1's Forecasting + Policy split.
- **Is Strands' multi-agent collaboration model rich enough for the v6 multi-echelon Rotable Pool work?** Probably not — METRIC simulation is a discrete-event simulator, not an agent. Plan to integrate it as a SageMaker job invoked by the Rotable Pool specialist's tool surface, not as another Strands agent.

---

## Verification

The Agent Spine plan implements Supervisor in `src/trax_io/supervisor/agent.py` using Strands. The `SupervisorOrchestrator` in `src/trax_io/supervisor/orchestration.py` is framework-free and tested in isolation. The integration test suite in `tests/integration/` exercises the orchestrator end-to-end against `fake_emro` without invoking any LLM, validating that the deterministic core can be evolved independently of Strands version changes.
