-- Domain: order_plan  |  Windowed: no  |  Binds: none
select     A.order_type || '_' ||  A.ORDER_NUMBER || '_' || B.ORDER_LINE as HostOrderID,
					        A.order_type || '_' ||  A.ORDER_NUMBER || '_' || B.ORDER_LINE as OrderID,
					        A.requester_location as HostLocID,
					        A.RELATION_CODE as HostVendorLocID,
					        A.SHIPPED_FROM_LOCATION as HostReplSourceLocID,
					        A.CREATED_DATE as PlanOrderDate,
					        A.SHIP_VIA_DATE as PlanShipDate,
					        A.CREATED_DATE as ActualOrderDate,
					        A.ORDER_TYPE as OrderTypeID,
					        B.PN as HostPartID,
					        B.STATUS as OrderStatus,
					        B.DELIVERY_DATE as PlanRcvDate,
					        B.DELIVERY_DATE as PlanAvailDate,
					        B.QTY_REQUIRE as PlanQuantity,
					        B.QTY_RECEIVED as ReceivedQuantity,
					        (  decode ( B.QTY_RECEIVED , 0  , null ,   ( select max(h.CREATED_DATE)  from pn_inventory_history h
                            where h.ORDER_NO = B.order_number and h.order_line = B.order_line and h.transaction_type in ( 'PO/RECEIVING'  ) ) ) ) as ActualRcvDate,
                            B.UNIT_COST as UnitCost
					        from ORDER_HEADER A,  ORDER_DETAIL B
					        WHERE B.STATUS = 'OPEN'   AND  A.ORDER_NUMBER = B.ORDER_NUMBER  AND A.ORDER_TYPE = B.ORDER_TYPE
					        ORDER BY A.CREATED_DATE DESC ;
