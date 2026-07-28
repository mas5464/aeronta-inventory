# Sync Strategy: Shared Services Between Trax & Aeronta

## Overview

`trax-io-aws` and `aeronta-inventory` are independent repositories that **duplicate** shared Python services:
- `services/feature-store/`
- `services/recommendation-engine/`
- `services/forecasting/`
- `services/event-publisher/`

This doc tracks manual cherry-pick syncs across both repos.

## Source of Truth

**Aeronta is primary** (live production, catches bugs first).
**Trax is secondary** (internal, lower mutation rate).

When a bug fix is needed in a shared service:
1. Fix in Aeronta first, test thoroughly
2. Manually cherry-pick to Trax
3. Document the cherry-pick below

## Last Sync

- **Date:** 2026-07-27 (fork created; no cherry-picks yet)
- **Services synced:** N/A
- **Aeronta commits picked:** N/A

## Sync Log

| Date | Aeronta Commit | Service | Trax Commit | Status |
|------|----------------|---------|------------|--------|
| 2026-07-27 | (fork) | (initial) | (fork) | ✅ Repos created |

## Known Divergences

None yet. Both repos start from identical shared code.

## Next Sync Review

2026-08-10 (weekly cadence recommended)

## How to Cherry-Pick

When you need to sync a fix from Aeronta to Trax:

```bash
# In trax-io-aws working tree
git log --oneline origin/main..aeronta/main -- services/recommendation-engine
# Identify the Aeronta commit(s) to pick

# Manually re-implement the fix in Trax (don't copy files directly)
# Files already exist; update the logic only

git add services/recommendation-engine/
git commit -m "cherry-pick: fix recommendation-engine bug from aeronta-inventory (Aeronta PR #NNN)"
git push origin main

# Then update this doc:
# - Add row to Sync Log
# - Update "Last Sync" section
```

## Shared Service Inventory

| Service | Location (both repos) | Responsibility |
|---------|----------------------|-----------------|
| feature-store | `services/feature-store/` | Feature engineering, offline/online stores |
| recommendation-engine | `services/recommendation-engine/` | Core ML logic (ROP, EOQ, Safety Stock, Max) |
| forecasting | `services/forecasting/` | Demand forecasting models |
| event-publisher | `services/event-publisher/` | Event schema & publishing |

All others are repo-specific or non-shared.
