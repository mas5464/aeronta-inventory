-- Domain: part_location  |  Windowed: no  |  Binds: none
SELECT 	pi.pn as HostPartID,
					pil.location as HostLocID,
					pvp.Price  as Price,
					lm.RELATED_MAIN_WAREHOUSE  as HostReplSourceLocID,
					LOWER(lm.inventory) as ProcAllowed,  -- IF LOCATION IS to procure this part locally.  Valid values are (y)  and (n) .
					LOWER(lm.maintenance_facility) as RepaAllowed, -- Allows this location to repair this part locally. Valid values are (y)  and (n) .
					pvp.Vendor as HostProcureNewPriVendLocID, -- opt
					pvp.MINIMUM_ORDER_QTY as MinOQ,
					(select system_code_description from system_tran_code where system_transaction = 'UOM' and system_code = pm.STOCK_UOM) as UnitMeasure,
					pm.STOCK_UOM as UOM
					FROM PN_INTERCHANGEABLE pi ,PN_Master pm ,pn_inventory_level pil , LOCATION_MASTER lm, PN_VENDOR_PRICE pvp
				        where pi.PN = pm.PN and pil.PN = pi.PN_INTERCHANGEABLE
				        and pil.location = lm.location and pvp.PN_INTERCHANGEABLE = pi.PN_INTERCHANGEABLE ;
