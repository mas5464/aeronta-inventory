# Trax IO Inventory Optimizer — Web Application User Guide

**Audience:** Airline Operations, Planners, Inventory Managers, Supply Chain Analysts  
**Last Updated:** 2026-07-07  
**Focus:** How to use the UI, interpret recommendations, approve changes, troubleshoot

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard Overview](#dashboard-overview)
3. [Views & Navigation](#views--navigation)
4. [Workbench: The Approval Loop](#workbench-the-approval-loop)
5. [Part Drill-Down: Deep Analysis](#part-drill-down-deep-analysis)
6. [AI Recommendations: Explainable Cards](#ai-recommendations-explainable-cards)
7. [Forecast & Service Levels](#forecast--service-levels)
8. [What-If Scenarios: Test Changes](#what-if-scenarios-test-changes)
9. [Reports: Business Value Report](#reports-business-value-report)
10. [Data & Connections: Health Check](#data--connections-health-check)
11. [Settings & Theme](#settings--theme)
12. [Troubleshooting & FAQ](#troubleshooting--faq)

---

## Getting Started

### 1. Accessing the Application

**URL:** `http://localhost:8089` (local dev) or `https://trax-io.company.com` (production)

**Login:**
- **Local Dev:** No authentication required. App accepts any bearer token in the `Authorization` header.
- **Production:** Log in with your company SSO (OAuth 2.0). Your tenant is automatically determined by your domain.

### 2. Home Screen

On first load, you'll see:

```
┌─────────────────────────────────────────────────────────────┐
│  TRAX IO INVENTORY OPTIMIZER                           👤 🌙 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Welcome, [Your Name]                                        │
│  You are managing: Air Canada (847 parts)                    │
│  Last optimization: Jun 29, 2026 @ 2:30 PM                  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PENDING ACTIONS        🔴 26 needing your approval   │   │
│  │                                                      │   │
│  │ • 12 parts with cost impact >$5K                    │   │
│  │ • 8 AOG-critical increases                          │   │
│  │ • 6 parts with low forecast confidence              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Go to Workbench ➜                                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Dashboard Overview

Click **"Overview"** (first nav item) to see the portfolio snapshot.

### Portfolio Health Metrics

```
┌────────────────────────────────────────────────────────┐
│                 PORTFOLIO AT A GLANCE                  │
├────────────────────────────────────────────────────────┤
│                                                        │
│  57,605 Keys Under Management                         │
│  (aircraft × part-location combinations tracked)      │
│                                                        │
│  On-Hand Inventory        Open Pipeline               │
│  $847 Million             $419 Million                │
│  (current stock value)    (pending orders)            │
│                                                        │
│  Service Level: 95.2%     Shortage Risk: 2.8%         │
│  (% time stock available) (% likely stockout next 30d)│
│                                                        │
│  AOG-Critical Parts: 156  Red Flags: 12               │
│  (flight-grounding only)  (need immediate attention)  │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### Breakdowns (Click to Drill)

The dashboard displays five interactive cards. Click any to see details:

1. **By Tier**
   - Tier A (AOG): 156 parts, 12% portfolio value
   - Tier B (Normal): 512 parts, 68% portfolio value
   - Tier C (Consumable): 179 parts, 20% portfolio value

2. **By ATA Chapter** (Maintenance category)
   - 32-Hydraulic Power
   - 38-Water/Waste
   - 49-Auxiliary Power Unit
   - (Click row to see all parts in that category)

3. **By Part Class**
   - Engine: 45 parts, avg lead time 60 days
   - Hydraulic: 123 parts, avg lead time 45 days
   - Avionics: 89 parts, avg lead time 30 days
   - Consumable: 590 parts, avg lead time 10 days

4. **On-Hand Stock Distribution**
   - Stock: $400-600M (too much, risk of aging)
   - Stock: $300-400M (ideal range)
   - Stock: $100-300M (OK)
   - Stock: <$100M (watch for stockouts)

5. **Top 10 Shortages** (Current risk)
   - **A380-HYDRAULIC-PUMP@YYZ** — On-hand: 1 unit, Lead time: 45 days
   - **A380-FUEL-FILTER@YVR** — On-hand: 2 units, Lead time: 30 days
   - (Click to open Part Drill-Down)

### Next Steps from Dashboard

- **"Approve pending recommendations"** → Go to Workbench (see recommendations needing your OK)
- **"Drill into top shortage"** → Open Part Drill-Down for that part
- **"View forecast trend"** → Go to Forecast view to see 24-month demand projection

---

## Views & Navigation

### Navigation Menu (Left Sidebar)

```
TRAX IO
├─ 📊 Overview
│  └─ Portfolio summary, KPIs, drill cards
│
├─ 🎯 Workbench
│  └─ Approval loop; pending recommendations
│
├─ 💡 AI Recommendations
│  └─ Explainable recommendation cards
│
├─ 📈 Forecast & Service Levels
│  └─ 24-month demand projection + SL targets
│
├─ ⚡ What-If Scenarios
│  └─ Test "What if lead time increases?" etc.
│
├─ 📋 Reports
│  └─ Business Value Report (ROI, savings projection)
│
├─ 🔌 Data & Connections
│  └─ Connection health, data freshness
│
└─ ⚙️ Settings
   └─ Theme (dark/light), notifications, preferences
```

---

## Workbench: The Approval Loop

**The Workbench is your main operational view.** It displays all pending recommendations waiting for approval.

### View: Pending Recommendations Table

```
┌──────────────────────────────────────────────────────────────────────────┐
│ WORKBENCH: Pending Recommendations                                  ⟲ 🔽 │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ Showing 1–50 of 847 recommendations                                      │
│                                                                          │
│ Filter: [Tier ▼] [Status ▼] [ATA ▼] [Sort: By Tier ▼]                  │
│                                                                          │
│ ┌─────┬──────────────┬──────────┬─────────┬────────┬────────┬──────┐   │
│ │ Part│ Location     │ Tier     │ Current │ New    │ Impact │ …    │   │
│ │ No. │ (Airport)    │ A/B/C    │ ROP/EOQ │ ROP/EOQ│ $      │      │   │
│ ├─────┼──────────────┼─────────┼─────────┼────────┼────────┼──────┤   │
│ │[✓]  │A380-TIRE    │ Tier B   │ 10/40   │ 15/45  │ +$120  │ ••• │   │
│ │[ ]  │ YYZ          │ Normal   │         │        │        │      │   │
│ ├─────┼──────────────┼─────────┼─────────┼────────┼────────┼──────┤   │
│ │[ ]  │A380-OIL     │ Tier A   │ 8/50    │ 12/60  │ +$540  │ ⚠️  │   │
│ │[ ]  │ YYZ          │ AOG-only │         │        │        │      │   │
│ ├─────┼──────────────┼─────────┼─────────┼────────┼────────┼──────┤   │
│ │[ ]  │A380-FILTER  │ Tier C   │ 20/100  │ 25/110 │ +$60   │      │   │
│ │[ ]  │ YVR          │          │         │        │        │      │   │
│ └─────┴──────────────┴─────────┴─────────┴────────┴────────┴──────┘   │
│                                                                          │
│ Legend: [✓] = Approved  ⚠️ = Requires escalation  ••• = View details  │
│                                                                          │
│ Actions:                                                                │
│ [✓ Approve Visible] [⊘ Reject All] [📋 Select & Bulk Approve] [⚙️ More] │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Workflow: Approving a Single Recommendation

**Scenario:** You see "A380-TIRE@YYZ" — increase ROP from 10 to 15.

**Steps:**

1. **Click the row** to expand:
   ```
   A380-TIRE @ YYZ (Tier B)
   
   Current Levels:
   • ROP (reorder point): 10 units
   • EOQ (order qty):     40 units
   • Safety Stock:        6 units
   • Max:                 60 units
   
   Recommended:
   • ROP: 15 units (+50%)
   • EOQ: 45 units (+13%)
   • SS:  8 units
   • Max: 68 units
   
   Reason: "Seasonal demand increase; Q2 peak detected. 
            1.8 units/month avg, lead time 30 days (std dev 5d).
            Confidence: 92%"
   
   History of this part:
   • Jun 29: ROP 10 → 10 (no change)
   • May 15: ROP 9 → 10 (increased for summer season)
   • Apr 10: ROP 8 → 9 (adjusted forecast)
   
   Cost impact: +$120/year (holding cost for extra 5 units)
   ```

2. **Review the reasoning.** Does it make sense?
   - Green checkmark 🟢 = High confidence (>85%)
   - Yellow caution ⚠️ = Medium confidence (70–85%)
   - Red flag 🔴 = Low confidence (<70%, requires override)

3. **Choose an action:**
   - **Approve** ✓ — Accept the recommendation. Write to eMRO.
   - **Reject** ✗ — Dismiss; do not apply.
   - **Defer** ⏸ — Revisit later; stays in queue.
   - **History** 📜 — See past changes to this part; view rollback option.

4. **Confirm:**
   ```
   Dialog: "Approve recommendation for A380-TIRE @ YYZ?"
   
   New values: ROP 15, EOQ 45, SS 8, Max 68
   Impact: +$120/year holding cost
   Reason for change: [text input] "Increase for Q2 seasonal peak"
   
   [Approve] [Reject] [Cancel]
   ```

5. **Result:**
   - Recommendation moves to "Approved" tab (grayed out in pending)
   - eMRO database updated: A380-TIRE @ YYZ now has ROP=15
   - Notification: "✓ Applied: A380-TIRE @ YYZ (ROP 10→15)"

### Bulk Approval: "Approve All in Tier C"

**For non-critical consumables, you can batch-approve:**

```
[Select All] [Select Tier C Only] [Deselect Rejected]
[✓ Bulk Approve (847 selected)]

Dialog:
"Approve 847 recommendations?"
• 156 Tier A (require escalation)
• 512 Tier B (within guardrails)
• 179 Tier C (auto-approved)

By tier:
○ Approve Tier A manually; auto-approve B & C
○ Approve all (recommended for weekly rebalancing)
○ Cancel

[Proceed] [Cancel]
```

**Result:** 512 + 179 = 691 recommendations written to eMRO. 156 Tier A stay pending.

---

## Part Drill-Down: Deep Analysis

**To understand one part deeply, go to a specific part's detail page.**

### Two Ways to Open

1. Click a row in Workbench → expands the detail panel
2. Click "Overview" → drill into a shortage card → opens full Part Drill-Down

### Part Detail Page Layout

```
┌──────────────────────────────────────────────────────────────┐
│ A380-HYDRAULIC-PUMP @ Toronto (YYZ)                    🔍 ≡  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ CURRENT LEVELS & RECOMMENDATION                             │
│ ┌────────────────┬──────────────┬──────────────┐             │
│ │ Metric         │ Current      │ Recommended  │             │
│ ├────────────────┼──────────────┼──────────────┤             │
│ │ On-Hand        │ 3 units      │ (no change)  │             │
│ │ On-Order       │ 4 units      │ (no change)  │             │
│ │ ROP            │ 8 units      │ 12 units ↑   │ ⚠️ Escalate│
│ │ EOQ            │ 50 units     │ 60 units ↑   │             │
│ │ Safety Stock   │ 5 units      │ 7 units      │             │
│ │ Max            │ 68 units     │ 79 units     │             │
│ └────────────────┴──────────────┴──────────────┘             │
│                                                              │
│ DEMAND FORECAST (24-Month Projection)                      │
│ ┌──────────────────────────────────────────────┐            │
│ │ Chart: Demand over time                      │            │
│ │                                              │            │
│ │     3.5│        ╭─╮                          │            │
│ │     3.0│   ╭─╮ ╱ ╲ ╭─╮ (peaks show seasonality)│         │
│ │     2.5│  ╱ ╲╱   ╲╱ ╲                       │            │
│ │     2.0│ ╱                                   │            │
│ │     1.5│                                     │            │
│ │  units ├─────────────────────────────────────│            │
│ │        └──────────────────────────────────────┘            │
│ │        Jun   Sep   Dec   Mar   Jun (next year)            │
│ │                                                          │
│ │ Summary: Avg 2.1 units/month; seasonal peak +40% in     │
│ │ summer (June–August); baseline lead-time 45 days        │
│ └──────────────────────────────────────────────┘            │
│                                                              │
│ OPEN ORDERS & LEAD TIMES                                   │
│ ┌────────────────────────────────┐                          │
│ │ Order  Qty   Ordered   Due      Status      │              │
│ ├────────────────────────────────┤              │
│ │ PO-123 4u    Jun 20    Jul 05   In Transit   │              │
│ │ PO-124 2u    Jun 28    Jul 15   Pending      │              │
│ │ PO-125 6u    (new)     Jul 20   To Create    │              │
│ └────────────────────────────────┘              │
│                                                              │
│ WRITETHROUGH HISTORY (Versions & Rollback)               │
│ ┌─────────────────────────────────────────────┐            │
│ │ Date        Action     ROP     By    Status  │            │
│ ├─────────────────────────────────────────────┤            │
│ │ Jun 29, 2pm Recommend  12 units planner ⏳   │ (pending)  │
│ │ Jun 22, 4pm Approved   10 units mgr001  ✓    │            │
│ │ Jun 15, 9am Approved   8 units  mgr001  ✓    │            │
│ │ [View Full History] [Roll Back Last Change] │            │
│ └─────────────────────────────────────────────┘            │
│                                                              │
│ ACTIONS:                                                    │
│ [✓ Approve] [✗ Reject] [⏸ Defer] [📋 CSV Export] [🔗 Share]│
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Key Sections

#### 1. Stock Breakdown

See the current on-hand vs. on-order:

```
On-Hand: 3 units
  └─ Serviceable (ready to use): 3
  
On-Order: 4 units
  └─ PO-123 (arrives Jul 5): 4
  
Days of Supply: 
  (3 on-hand + 4 on-order) / 2.1 avg monthly demand = 3.3 months
  
Shortage Risk (next 30 days): 12% chance we'll need an emergency order
```

#### 2. Lead-Time View

Click **"Lead Times"** tab:

```
Supplier: Parker Hydraulics
Last 10 Orders:
  Average lead time: 42 days
  Std deviation:     ±5 days
  On-time rate:      95%
  Fastest delivery:  30 days
  Slowest delivery:  52 days
  
Backup supplier: Hydac Intl
  Lead time: 60 days
  On-time rate: 88% (less reliable)
  
Decision: Use primary; safety stock of 7 units covers ±5 day variance
```

#### 3. Write-Back History & Rollback

The **HISTORY** section shows the audit trail:

```
Proposal on Jun 29, 2pm:
  ROP: 8 → 12 units
  EOQ: 50 → 60 units
  SS:  5 → 7 units
  
  Status: ⏳ Pending (awaiting your approval)
  Reason: "Seasonal demand increase Q2"
  Confidence: 92%

[APPROVE] [REJECT]

──────────────────────

Applied on Jun 22, 4pm:
  ROP: 8 → 10 units
  Status: ✓ Approved by Mgr001
  
  [🔄 ROLL BACK THIS CHANGE] ← Reverts to ROP=8

─────────────────────

Applied on Jun 15, 9am:
  ROP: 7 → 8 units
  Status: ✓ Approved by Mgr001
  
  [🔄 ROLL BACK THIS CHANGE] (expired, >90 days)
```

**To roll back:** Click `[🔄 ROLL BACK]` on the most-recent written entry:

```
Dialog: "Roll Back: A380-HYDRAULIC-PUMP @ YYZ"

This will revert:
  ROP: 10 → 8 units (old value)
  EOQ: 52 → 50 units
  SS:  6 → 5 units

New version will be created as a "rollback" entry for audit.

Reason (required): [________________________]  "Supplier shortages easing"

[Roll Back] [Cancel]
```

---

## AI Recommendations: Explainable Cards

**Go to "AI Recommendations" view to see explanation cards for pending recommendations.**

### Card Layout

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│  A380-TIRE-MAIN @ YYZ                               │
│  Recommendation: ROP 10 → 15 units                  │
│                                                      │
│  ╔════════════════════════════════════════════════╗ │
│  ║ CONFIDENCE: ████████░░ 92%                     ║ │
│  ║ Seasonal pattern detected; historical fit good ║ │
│  ╚════════════════════════════════════════════════╝ │
│                                                      │
│  Why this recommendation?                           │
│  ├─ Demand spike detected (Q2 peak, +40% vs avg)  │
│  ├─ Lead time 30 days; forecast mean 1.8 units/mo │
│  ├─ Current ROP=10 covers 5.5 day supply          │
│  ├─ Increasing to ROP=15 covers 8.3 days (safer) │
│  └─ Annual holding cost impact: +$120 (0.3%)      │
│                                                      │
│  Model Used: Statistical Projector (Exponential)   │
│  Data: 24-month history (100% available)           │
│                                                      │
│  [✓ Approve]  [✗ Reject]  [ℹ️ Details]  [📊 Forecast]
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Explanation Sections

**"Why this recommendation?"** — Shows the reasoning chain:

1. **Demand Analysis:** "Seasonal spike Q2 (+40%)"
2. **Lead-Time Impact:** "30-day lead time means we need 1.5 units buffer"
3. **Safety Stock Logic:** "95% SL = Z-score 1.65; SS = 1.65 × √(variance)"
4. **Cost Trade-off:** "+$120/year holding vs. lower stockout risk"

**"Model Used"** — Which forecasting method:
- ✓ Statistical Projector (fast, interpretable, 18% MAPE)
- ✓ Gradient-Boosted (more accurate, 8% MAPE for high-frequency parts)
- ✓ Empirical Bayes (for ultra-rare parts, principled priors)

**"Forecast Details"** — Click to see:
- 24-month demand curve
- 80% + 95% confidence intervals
- Seasonality indices
- Anomaly scores

---

## Forecast & Service Levels

**Go to "Forecast & Service Levels" to explore demand projections and tune SL targets.**

### Demand Forecast Chart

```
┌─────────────────────────────────────────────────────┐
│ Part: A380-ENGINE-OIL-FILTER                        │
│ Forecast for next 24 months                         │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Demand    ╭╮                                       │
│  (units)   ││      ╭╮        ╭╮                    │
│  3.0  ─────┼┼──╭─╮─┼┼────╭─╮─┼┼────                 │
│  2.5  ─ ╱‾‾╰─╰─┘ ╰─╰╰─╭─╮╰─╰─╰╰─╭───               │
│  2.0  ╱  (high confidence)  (low confidence)       │
│  1.5  historical           (extrapolation)        │
│  1.0  ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔         │
│  0.5  (historical data ↑)  (forecast →)           │
│     └─────────────────────────────────────────────│
│        Jun    Sep   Dec   Mar   Jun   Sep (2027)  │
│                                                     │
│ Summary:                                            │
│ • Historical avg: 1.9 units/month (24 months)     │
│ • Forecast avg: 1.8 units/month (next 24 months) │
│ • Seasonality: +35% in summer, -25% in winter     │
│ • Trend: Flat (no growth/decline detected)        │
│ • Anomalies: 2 spikes (May spike, Jan spike)      │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Service Level Slider

Adjust your **target service level** to see impact on ROP:

```
Adjust Service Level Target

        Conservative (80% SL)
        ├─ ROP: 8 units
        ├─ Stockout risk: 20% (1 in 5 months stockout likely)
        └─ Holding cost: Minimal
        
        Moderate (95% SL) ← Default
        ├─ ROP: 13 units ← Recommended
        ├─ Stockout risk: 5% (very unlikely to run out)
        └─ Holding cost: Moderate
        
        Aggressive (99% SL)
        ├─ ROP: 18 units
        ├─ Stockout risk: 1% (nearly never runs out)
        └─ Holding cost: High
        
        ◀────────●────────▶  Drag to adjust
         80%    95%    99%

Result: "Increasing SL to 99% would increase ROP by +38%, 
         adding $2.1M annually across portfolio."
```

---

## What-If Scenarios: Test Changes

**Explore "What if lead-time increases by 30%?" or "What if we halve the holding cost?"**

### Scenario Builder

```
┌──────────────────────────────────────────────────────┐
│ WHAT-IF SCENARIOS                                    │
├──────────────────────────────────────────────────────┤
│                                                      │
│ Scenario 1: Lead-Time Increase (Supplier Crisis)    │
│ ┌────────────────────────────────────────────────┐  │
│ │ Change: Increase all lead times by 30%         │  │
│ │ Reason: Supplier shortage, delayed shipments   │  │
│ │ Duration: 6 months                             │  │
│ │                                                │  │
│ │ [Calculate Impact]                             │  │
│ │                                                │  │
│ │ Result:                                        │  │
│ │ • ROP increases by 18% (avg) to cover delay   │  │
│ │ • Safety stock +22% ($1.3M annual cost)        │  │
│ │ • Service level drops 95% → 92% (if no change)│  │
│ │ • Recommendation: Expedite orders now          │  │
│ └────────────────────────────────────────────────┘  │
│                                                      │
│ Scenario 2: Budget Ceiling                          │
│ ┌────────────────────────────────────────────────┐  │
│ │ Constraint: Inventory holding cost ≤ $2.8M    │  │
│ │ Current: $3.1M (over budget)                   │  │
│ │ Target: Reduce by $300K                        │  │
│ │                                                │  │
│ │ [Calculate Impact]                             │  │
│ │                                                │  │
│ │ Result:                                        │  │
│ │ • Service level must drop 95% → 91% (tradeoff)│  │
│ │ • ROP decreases 12% (avg)                      │  │
│ │ • Stockout risk increases 5% → 8%              │  │
│ │ • Parts affected: 348 Tier C (consumables OK) │  │
│ │ • Red flag: 23 AOG parts under-stocked        │  │
│ └────────────────────────────────────────────────┘  │
│                                                      │
│ Scenario 3: Custom (Build Your Own)                 │
│ ┌────────────────────────────────────────────────┐  │
│ │ Filters:                                       │  │
│ │ • Apply to: All parts / Tier [  ] / ATA [ ]  │  │
│ │ • Change: [Dropdown: Lead time / Demand / SL] │  │
│ │ • Value: [________] %                          │  │
│ │                                                │  │
│ │ [Calculate] [Save as Scenario] [Reset]        │  │
│ └────────────────────────────────────────────────┘  │
│                                                      │
└──────────────────────────────────────────────────────┘

After calculating a scenario:

[Compare to Current] [Apply This Scenario] [Save & Schedule] [Share Link]
```

---

## Reports: Business Value Report

**Go to "Reports" to see the official Business Value Report (BVR).**

### BVR Overview

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│  BUSINESS VALUE REPORT                              │
│  Trax IO Recommendations Summary                    │
│  Period: Week 27 (Jun 24 – Jun 30, 2026)           │
│                                                      │
│  Prepared for: Air Canada Supply Chain              │
│  Generated: Jun 30, 2026 @ 2:15 PM                 │
│                                                      │
│ ══════════════════════════════════════════════════ │
│                                                      │
│  KEY METRICS                                        │
│                                                      │
│  ┌─────────────────────────────────────────────┐   │
│  │ Total Projected Savings          $1.24 Million│  │
│  │ Changes Applied / Shadowed       847 / 156   │  │
│  │ Keys Under Management            57,605      │  │
│  │ Open Pipeline                    $419M       │  │
│  └─────────────────────────────────────────────┘   │
│                                                      │
│  SAVINGS BREAKDOWN                                  │
│                                                      │
│  Holding Cost Reduction        ████████░░ 45%      │
│  (inventory turned over faster)   $558K / year     │
│                                                      │
│  Ordering Cost Reduction       ██████░░░░ 30%      │
│  (optimized batch sizes)          $372K / year     │
│                                                      │
│  Shortage Avoidance            ████░░░░░░ 20%      │
│  (fewer emergency expedites)      $248K / year     │
│                                                      │
│  Obsolescence Prevention       ██░░░░░░░░ 5%       │
│  (less aging inventory)            $62K / year     │
│                                                      │
│ ══════════════════════════════════════════════════ │
│                                                      │
│  GOVERNANCE STRIP                                   │
│                                                      │
│  Recommendations Approved:    97.0%  (821 / 847)  │
│  Tier A Overrides:            2.9%   (8 / 847)    │
│  Escalations Approved:        0.1%   (1 / 847)    │
│  Kill Switch Status:          ✓ OFF (normal)      │
│                                                      │
│ ══════════════════════════════════════════════════ │
│                                                      │
│  [📄 Print] [📥 Download PDF] [📊 Full Report]    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Download & Print

- **PDF:** Includes charts, narrative, methodology
- **CSV:** Raw recommendation data (suitable for audit)
- **Email:** Send report to stakeholders

---

## Data & Connections: Health Check

**Go to "Data & Connections" to verify all integrations are healthy.**

### Connection Status Table

```
┌────────────────────────────────────────────────────────────┐
│ DATA & CONNECTIONS                                         │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Feed                  Status        Last Updated  Lag     │
│ ─────────────────────────────────────────────────────────│
│ eMRO Oracle DB        ✓ Connected   Jun 30, 2pm   <5min  │
│                                                            │
│ Demand History        ✓ Synced      Jun 30, 1pm   <1hr   │
│ (24-month extracts)                                        │
│                                                            │
│ Part Master Data      ✓ Fresh       Jun 30, 12am  Today  │
│ (class, supplier)                                          │
│                                                            │
│ Supplier Performance  ✓ Synced      Jun 28, 3pm   2 days │
│ (lead time stats)                                          │
│                                                            │
│ Order History         ⚠️  Stale      Jun 24, 4pm   6 days │
│ (last 5 years)                      ⚠️ Refresh needed     │
│                                                            │
│ Requisitions (Kafka)  ✓ Flowing     Jun 30, 2:13p <5sec  │
│                                                            │
│ Transfer Orders (Kafka) ✓ Flowing     Jun 30, 2:14p <5sec  │
│                                                            │
│ Write-Back Ledger     ✓ Audited     Jun 30, 2:14p <1sec  │
│                                                            │
│ [🔄 Refresh All] [📋 Details] [⚙️ Settings]              │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Connection Details (Click a Row)

```
eMRO Oracle DB
├─ URL: jdbc:oracle:thin:@customer-oracle.internal:1521/PROD
├─ Status: Connected
├─ Ping time: 47 ms
├─ Queries/min: 12
├─ Last error: None
├─ Uptime: 99.97% (this week)
└─ [Test Connection] [Troubleshoot]

Order History (STALE ⚠️)
├─ Source: eMRO ORDER_HEADER/DETAIL tables
├─ Last sync: Jun 24, 4 PM (6 days ago)
├─ Expected: Daily (12 AM UTC)
├─ Status: Data available but outdated
├─ Impact: Demand forecast may underweight recent orders
└─ [Refresh Now] [Schedule Refresh] [Troubleshoot]
```

---

## Settings & Theme

### Theme Toggle

```
┌──────────────────────────────────┐
│ Settings → Theme                 │
├──────────────────────────────────┤
│                                  │
│ Appearance:                      │
│ ○ Light Mode  ● Dark Mode (default) │
│   (white bg)  (dark bg, OLED-friendly)│
│                                  │
│ [Save]                           │
│                                  │
└──────────────────────────────────┘
```

---

## Troubleshooting & FAQ

### "I see '⏳ Pending' but no recommendations yet"

**Cause:** First optimization run is still computing.

**Fix:**
- Wait 2–5 minutes for the initial batch to complete
- Refresh the page: `Ctrl+R` (Windows) or `Cmd+R` (Mac)
- Check **Data & Connections** to ensure all feeds are synced

### "Why does my ROincrease conflict with Tier A guardrails?"

**Cause:** The recommendation exceeds the +50% threshold for AOG-critical parts.

**Fix:**
- Click the recommendation to see the reason
- You can **override** the guardrail if you approve the increase (requires escalation approval)
- Or **defer** to revisit after consulting with your supply chain manager

### "I rejected a recommendation, but it reappeared"

**Cause:** Rejections are per-week. A new optimization run (next week) may generate the same recommendation again.

**Fix:**
- Rejections only suppress for the current week
- To prevent future recommendations, adjust the part's **Max** level in settings (if allowed)

### "How do I roll back a change that's already live?"

**Steps:**
1. Open the part's detail page
2. Scroll to **History** section
3. Find the entry you want to roll back
4. Click `[🔄 Roll Back This Change]`
5. Enter a reason (required for audit)
6. Confirm
7. A new ledger entry is created with the old values

**Limitation:** Only available for changes within the last 90 days.

### "Why is there a 🔴 red flag on my recommendation?"

**Cause:** Confidence is <70% (low data quality or missing context).

**Fix:**
- Click the card to see the explanation
- Data quality issues: historical data incomplete, supplier changed, etc.
- Typically safe to approve anyway (guardrails still apply)
- If unsure, **defer** until more data is available

### "Can I export the data for Excel?"

**Yes:**
- Go to **Workbench** or **AI Recommendations**
- Click `[📋 CSV Export]` button
- Downloads all filtered recommendations as CSV
- Open in Excel for further analysis

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+/` (Windows) `Cmd+/` (Mac) | Show shortcuts help |
| `Esc` | Close dialog or drill panel |
| `Enter` | Approve selected recommendation |
| `Shift+A` | Approve all visible (with confirmation) |
| `Shift+R` | Reject all visible (with confirmation) |
| `Ctrl+F` | Search parts by PN or location |
| `Ctrl+K` | Command palette (navigate views) |

---

## Getting Help

- **In-app Help:** Click `?` icon (top right) for tooltips
- **Video Tutorials:** See [company.com/trax-io/help](https://company.com/trax-io/help)
- **Contact Support:** `support@trax.com` or Slack `#trax-io-support`
- **Escalations:** Contact your regional manager or supply chain director

---

## Next Steps

1. **Complete your first approval session** — Spend 15 min reviewing & approving 10–20 recommendations
2. **Explore Forecast view** — Understand the 24-month projections behind each recommendation
3. **Set up a What-If scenario** — Model a supplier crisis or budget constraint
4. **Review the Business Value Report** — Understand the financial impact of your approvals
5. **Bookmark Data & Connections** — Check it weekly to ensure data freshness

