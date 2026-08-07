-- Domain: demand_history_rotables  |  Windowed: yes  |  Binds: from_date, to_date
select apn.transaction as HostDmdDetail  ,
				 apn.TRANSACTION_ITEM as HostDmdDetailItem ,
         apn.pn as HostPartID ,
        apn.SN as HostPartIDSn ,

         apn.transaction_date as HistoryBegDate ,
                                                        1 as HistoryAmount , ( select ac_type from ac_master where ac = apn.ac ) as AirCraftType ,
                                                        apn.WO as WorkOrderNumber , apn.ac as TailNumber , apn.TASK_CARD as TaskCard ,
                                                        translate( apn.REMOVAL_REASON, chr(10)||chr(11)||chr(13), '    ') as DemandNote ,  'REMOVE' as TransactionType ,
                                                        apn.SCHEDULE_CATEGORY as RemovalCategory ,  apn.TRANSACTION as Transaction ,
                                                         apn.station as Station ,  apn.REASON_CATEGORY as ReasonCategory ,  apn.BATCH as Batch ,
                                                        apn.ORDER_NO as OrderNo ,  apn.ORDER_TYPE as OrderType ,
                                                        apn.STATUS as Status,

          decode((SELECT dr.STATION  FROM  DEFECT_REPORT dr WHERE  apn.DEFECT = dr.defect (+) and apn.DEFECT_TYPE = dr.defect_type (+) and apn.DEFECT_ITEM = dr.DEFECT_ITEM(+)
           ),null,apn.STATION,(SELECT dr.STATION  FROM DEFECT_REPORT dr  WHERE  apn.DEFECT = dr.defect (+) and apn.DEFECT_TYPE = dr.defect_type (+) and apn.DEFECT_ITEM = dr.DEFECT_ITEM(+)  ))  as HostLocID,

          (SELECT WO.LOCATION FROM WO WHERE WO.WO = apn.WO) as woStation ,  (SELECT dr.RESOLVED_LOCATION   FROM
                                                        DEFECT_REPORT            dr
           WHERE              apn.DEFECT = dr.defect (+) and apn.DEFECT_TYPE = dr.defect_type (+) and apn.DEFECT_ITEM = dr.DEFECT_ITEM(+) ) as resolved_station
           from ac_pn_transaction_history  apn where apn.transaction_type = 'REMOVE' and apn.SCHEDULE_CATEGORY in ('SCHEDULE' , 'UN/SCHEDULE')
            AND apn.transaction_date >= :from_date and apn.transaction_date < :to_date + 1 ;
