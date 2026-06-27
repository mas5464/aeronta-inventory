"""Trax IO Agent Spine — deterministic orchestration core.

Sequences the real #2 Feature Store + #11 Recommendation Engine, enforces the autonomy
tier #11 only suggests, routes approvals, and writes back. Protocol-first so the
Strands/AgentCore LLM topology and Cedar slot in later (see
docs/superpowers/specs/2026-06-27-agent-spine-v1-design.md).
"""

__version__ = "0.1.0"
