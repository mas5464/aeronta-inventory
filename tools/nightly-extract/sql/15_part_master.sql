-- Domain: part_master  |  Windowed: no  |  Binds: none
-- NOTE: PKG_TRAX_PTC calls are inlined. See ~/Downloads/PTC Project Files/PKG_TRAX_PTC.sql
-- for the original package body that this file no longer depends on.
select
						  pi.PN_INTERCHANGEABLE as HostPartID,
						  pi.PN_INTERCHANGEABLE as PartNumber,
						  pm.PN_DESCRIPTION as PartName,
						  pm.CATEGORY as HostPartTypeID,

						   Decode((select count(*)from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn = pi.PN_INTERCHANGEABLE),0,
						   DECODE((select count(*) from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn = pi.PN),0,'0',1,
						  (select AC_TYPE from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn = pi.PN),'Fleet'),1,(select ac_type
						  from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn = pi.PN_INTERCHANGEABLE),'Fleet') as HostPartFamilyID,


						  pm.PN_DESCRIPTION as PartDescription,
						  NVL(pm.ESSENTIALITY_CODE, '0') as HostPartCriticalID,
						  ROUND( (( (select count(*)  from PN_INVENTORY_HISTORY  ih where ih.PN = pi.PN_INTERCHANGEABLE and ih.TRANSACTION_TYPE = 'RO/CREATE') - (select count(*)
						  from PN_INVENTORY_HISTORY ih where ih.PN = pi.PN_INTERCHANGEABLE and ih.TRANSACTION_TYPE = 'RO/RECEIVING') ) / DECODE((select count(*)
						  from PN_INVENTORY_HISTORY  ih where ih.TRANSACTION_TYPE = 'RO/CREATE' and ih.PN = pi.PN_INTERCHANGEABLE),0,1,(select count(*)
						  from PN_INVENTORY_HISTORY  ih where ih.TRANSACTION_TYPE = 'RO/CREATE' and ih.PN = pi.PN_INTERCHANGEABLE ) )),2) as WashRate,
						  (select ROUND(AVG(oi.INVOICE_AMOUNT),2)
						  from ORDER_INVOICE oi , order_detail od where
						  od.ORDER_NUMBER = oi.ORDER_NUMBER and od.ORDER_LINE = oi.ORDER_LINE and oi.ORDER_TYPE = od.ORDER_TYPE
						  and od.order_Type = 'RO'  and od.status = 'CLOSED' and od.pn = pi.pn_interchangeable
						  and  oi.CREATED_DATE BETWEEN add_months(trunc(sysdate,'mm'),-24) and (trunc(sysdate,'mm'))) as RepairCost,
						  DECODE((SELECT pn_transaction FROM system_tran_code  WHERE system_transaction = 'PNCATEGORY' AND system_code  =  pm.category ),'P','Y','R','Y','C','N','K','N'  ) AS PartRepairable,
						  decode(pm.STATUS,'ACTIVE','Y','N') as PartActive,
						  DECODE((SELECT pn_transaction FROM system_tran_code  WHERE system_transaction = 'PNCATEGORY' AND system_code  =  pm.category ),'R','Y','N') as PartSerializable,
						  NVL( (select MINIMUM_ORDER_QTY as MinOQ from PN_VENDOR_PRICE where PN_INTERCHANGEABLE = pi.PN_INTERCHANGEABLE and Condition = 'NEW' and PREFER = 'Y' and rownum = 1),'0') as MinOQ,
						  (DECODE( (select PN_TRANSACTION from system_tran_code where system_transaction = 'PNCATEGORY' and system_code = pm.CATEGORY) ,'K','Y','N')) as IsPartKit,
						  ( select  decode ( (select count(*) from PN_EFFECTIVITY_HEADER , pn_interchangeable intr where PN_EFFECTIVITY_HEADER.pn   = intr.PN_INTERCHANGEABLE and intr.pn = pi.pn  and intr.pn <> intr.PN_INTERCHANGEABLE )  , 0 ,
						  (select (listagg(PN_EFFECTIVITY_HEADER.ac_type,'_') WITHIN GROUP  (ORDER BY "PN_EFFECTIVITY_HEADER"."AC_TYPE")  ) from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn =  pi.pn )   ,
						  (select (listagg(PN_EFFECTIVITY_HEADER.ac_type,'_') WITHIN GROUP  (ORDER BY "PN_EFFECTIVITY_HEADER"."AC_TYPE")  ) from PN_EFFECTIVITY_HEADER where PN_EFFECTIVITY_HEADER.pn =  pi.pn_interchangeable )  )
						  from dual ) as ACType ,
						  pm.CHAPTER as ATAChapter,
						  pm.SECTION as ATASubChapter,
						  DECODE((SELECT COUNT(*) FROM  pn_effectivity_distribution ped WHERE ped.pn = pi.PN_INTERCHANGEABLE),0,(SELECT COUNT(*) FROM  pn_effectivity_distribution ped WHERE ped.pn = pi.PN),
						  (SELECT COUNT(*) FROM  pn_effectivity_distribution ped WHERE ped.pn = pi.PN_INTERCHANGEABLE)) as NoOfTails,
						  pm.SHELF_LIFE_DAYS as ShelfLife,
						  pm. HAZARDOUS_MATERIAL as Hazmat,
						  TOOL_CONTROL_ITEM as Tool,
						  pm.BIN_CAT as BulkPartFlag,
						  (select Vendor as HostProcureNewPriVendLocID from PN_VENDOR_PRICE where CONDITION = 'NEW' and PN_INTERCHANGEABLE = pi.PN_INTERCHANGEABLE AND PREFER = 'Y' AND rownum = 1) as HostProcureNewPriVendLocID,
						  (select Vendor as HostRepairPriVendLocID from PN_VENDOR_PRICE where PN_INTERCHANGEABLE = pi.PN_INTERCHANGEABLE AND PREFER = 'Y' AND rownum = 1) as HostRepairPriVendLocID,

						    -- Inlined PKG_TRAX_PTC.getKitCost: returns pm.standard_cost EXCEPT
						    -- when the part's category maps to PN_TRANSACTION='K' (kit) AND
						    -- standard_cost is null/0 — then fall back to latest PO unit_cost,
						    -- and if that is null/0, sum the avg NLA PO vendor prices.
						    NVL(
						      CASE
						        WHEN (SELECT PN_TRANSACTION FROM system_tran_code
						               WHERE system_transaction = 'PNCATEGORY'
						                 AND system_code = pm.category) = 'K'
						         AND NVL(pm.standard_cost, 0) = 0
						        THEN NVL(
						          (SELECT unit_cost FROM (
						             SELECT unit_cost FROM ORDER_DETAIL
						              WHERE order_type = 'PO' AND pn = pm.pn
						              ORDER BY CREATED_DATE DESC)
						           WHERE ROWNUM = 1),
						          (SELECT SUM(nlaPrice) FROM (
						             SELECT ROUND(AVG(PN_VENDOR_PRICE.PRICE), 2) AS nlaPrice,
						                    PN_VENDOR_PRICE.PN_INTERCHANGEABLE
						               FROM PN_VENDOR_PRICE
						              WHERE PN_VENDOR_PRICE.PN_VENDOR_PRICE_CATEGORY = 'PO'
						                AND PN_VENDOR_PRICE.PN_INTERCHANGEABLE IN
						                    (SELECT PN_NEXT_LOWER_ASSEMBLY.NLA_PN
						                       FROM PN_NEXT_LOWER_ASSEMBLY
						                      WHERE PN_NEXT_LOWER_ASSEMBLY.NHA_PN = pm.pn)
						              GROUP BY PN_VENDOR_PRICE.PN_INTERCHANGEABLE))
						        )
						        ELSE pm.standard_cost
						      END,
						      0
						    )  AS StandardCost ,

						  pi.CREATED_DATE as PartPhaseInDate,
						  NVL(pm.MARKET_VALUE_UNIT_COST,'0') as MarketUnitCost,
						  ROUND(pm.AVERAGE_COST,2) as AverageCost ,
						  pm.sub_category as SubCategory ,
						  pi.INTERCHANGEABLE_TYPE as InterchangeableType  ,

						  -- Inlined PKG_TRAX_PTC.getRecordsType: space-joined DISTINCT
						  -- RECORDS_TYPE values from PN_NEXT_LOWER_ASSEMBLY for this PN.
						  (SELECT LISTAGG(RECORDS_TYPE, ' ') WITHIN GROUP (ORDER BY RECORDS_TYPE)
						     FROM (SELECT DISTINCT RECORDS_TYPE
						             FROM PN_NEXT_LOWER_ASSEMBLY
						            WHERE NLA_PN = pm.pn))   AS RecordType,
						  pm.APU,pm.CAT_RATING,pm.DISK,pm.ENGINE,pm.ETOPS,pm.ETOPS_FLAG,pm.MEL,pm.REFERENCE_DOCUMENT,pm.REFERENCE_DOCUMENT_REVISION,pm.RVSM_CODE,pm.RVSM_FLAG
						  from PN_INTERCHANGEABLE pi ,PN_Master pm
						  where pi.PN = pm.PN ;
