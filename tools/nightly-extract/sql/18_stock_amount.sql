-- Domain: stock_amount  |  Windowed: no  |  Binds: none
SELECT  PN  AS HostPartID ,
						 location_master.LOCATION      AS HostLocID,
						 SUM (QTY_IN_REPAIR)    AS InRepair ,
						 SUM (QTY_AVAILABLE + QTY_PENDING_RI + QTY_RESERVED + DECODE (PENDING , 'TECHNICAL' , 1 , 0 ) )    AS OnHandNew ,
						 SUM (QTY_US        + DECODE (PENDING , 'INVENTORY' , 1 , 0 ) )   AS OnHandBad ,
						 SUM (QTY_RESERVED )  AS Allocated ,
						 SUM ( QTY_IN_RENTAL )   AS RentalQty ,
						 SUM (DECODE(LOAN_CATEGORY, NULL , 0 , QTY_AVAILABLE + QTY_IN_RENTAL + QTY_IN_REPAIR + QTY_IN_TRANSFER + QTY_PENDING_RI + QTY_RESERVED + QTY_US )) AS LoanQty
						 FROM PN_INVENTORY_DETAIL ,  location_master	WHERE PN_INVENTORY_DETAIL."LOCATION"  = LOCATION_MASTER."LOCATION"  (+)
						 AND NVL (location_master.INVENTORY_QUARANTINE , 'N' ) = 'N'
						 and PN_INVENTORY_DETAIL.KIT_NO is null
						 GROUP BY pn ,  location_master.LOCATION ;
