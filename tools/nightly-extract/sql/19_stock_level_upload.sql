-- Domain: stock_level_upload  |  Windowed: no  |  Binds: none
-- NOTE: the canonical eMRO Data SQLs.sql transposes these aliases (PN->hostlocid,
-- LOCATION->hostpartid). Corrected here so PN keys to HostPartID and LOCATION to
-- HostLocID, consistent with every other domain. See .claude/memory/lessons.md.
SELECT s.PN as hostpartid, s.LOCATION as hostlocid, s.Reorder_Level as rop,
			s.Eoq_Level as eoq,s.Replenishment_Lead_Time as slreplenishmentlength,
			s.Minimum_Stock as safetylevel, s.Maximum_Stock as stockmax, s.Company as company FROM PN_INVENTORY_LEVEL s ;
