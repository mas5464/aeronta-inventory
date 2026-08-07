-- Domain: demand_history_expendables  |  Windowed: yes  |  Binds: from_date, to_date
-- NOTE: pkg_settings_pn_master.getPNCategory(pn) is inlined as a direct lookup
-- into pn_master.category, removing the PL/SQL package dependency.
select to_char(apn.transaction_no ) as HostDmdDetail , apn.pn as HostPartID ,
				 apn.LOCATION as HostLocID , apn.CREATED_DATE as HistoryBegDate ,
				 apn.QTY as HistoryAmount ,  '' as AirCraftType ,
				 apn.WO as WorkOrderNumber , apn.ac as TailNumber ,
				 apn.TASK_CARD as TaskCard , '' as DemandNote ,  'ISSUED' as TransactionType ,
				 'UN/SCHEDULE' as RemovalCategory ,
				 to_char( apn.TRANSACTION_NO ) asTransaction,   apn.LOCATION as Station ,  '' as ReasonCategory ,
				 apn.BATCH as Batch ,  apn.ORDER_NO as OrderNo ,  apn.ORDER_TYPE as OrderType ,
				 apn.STATUS as Status,(DECODE(NVL(WO,0),0, (SELECT DEFECT_REPORT.RESOLVED_LOCATION
				 FROM
				 DEFECT_REPORT,
				 DEFECT_REPORT_PN
				 WHERE
				 DEFECT_REPORT.DEFECT                    = DEFECT_REPORT_PN.DEFECT
				 AND DEFECT_REPORT.DEFECT_ITEM             = DEFECT_REPORT_PN.DEFECT_ITEM
				 AND TRUNC ( DEFECT_REPORT.RESOLVED_DATE ) = TRUNC ( apn.CREATED_DATE )
				 AND DEFECT_REPORT.AC (+)                  = apn.AC
				 AND DEFECT_REPORT_PN.PN(+)                 = apn.PN AND rownum = 1), (SELECT WO.LOCATION FROM WO WHERE WO.WO = apn.WO)) ) as resolved_station
				 from PN_INVENTORY_HISTORY  apn where apn.transaction_type = 'ISSUED'
				 and (select  PN_TRANSACTION from SYSTEM_TRAN_CODE where SYSTEM_TRANSACTION = 'PNCATEGORY' and SYSTEM_CODE = (select category from pn_master where pn = apn.pn)) in ('C','K')
				 AND apn.CREATED_DATE >= :from_date and apn.CREATED_DATE < :to_date + 1 ;
