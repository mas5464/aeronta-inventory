// Trax IO autonomy bands (design §6.1). delta_bps = round(delta_pct * 10000); Cedar has no float type.

// Tier C — autonomous: routine/consumable parts (essentiality 4-5), single-write delta within +/-40%.
permit(principal, action == Action::"autonomous_write", resource is PartLocation)
when { resource.criticality_tier >= 4 && resource.delta_bps <= 4000 };

// Tier B — bounded: non-flight-safety parts (essentiality 2-3 and below), delta within +/-15%.
permit(principal, action == Action::"bounded_write", resource is PartLocation)
when { resource.criticality_tier >= 2 && resource.delta_bps <= 1500 };

// §6.2 hard floor (declarative mirror of the code-level cap): never auto-write a delta over 100%.
forbid(principal, action, resource is PartLocation)
when { resource.delta_bps > 10000 };
