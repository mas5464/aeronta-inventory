-- Domain: stock_level_upload  |  Windowed: no  |  Binds: none
SELECT s.PN as hostlocid, s.LOCATION as hostpartid, s.Reorder_Level as rop,
			s.Eoq_Level as eoq,s.Replenishment_Lead_Time as slreplenishmentlength,
			s.Minimum_Stock as safetylevel, s.Maximum_Stock as stockmax, s.Company as company FROM PN_INVENTORY_LEVEL s ;
