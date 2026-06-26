-- Domain: order_plan_closed_orders  |  Windowed: no  |  Binds: none
select d.order_type || '_' ||  d.ORDER_NUMBER || '_' || d.ORDER_LINE as HostOrderID ,
			 d.order_type || '_' ||  d.ORDER_NUMBER || '_' || d.ORDER_LINE as OrderID ,
			 d.PN as HostPartID , h.REQUESTER_LOCATION as HostLocID , h.RELATION_CODE as HostVendorLocID ,
			 'c' as OrderStatus , h.CREATED_DATE as PlanOrderDate,
			  d.DELIVERY_DATE as PlanRcvDate , d.DELIVERY_DATE as PlanAvailDate ,
			  (select max( pn_inventory_history.CREATED_DATE )  from pn_inventory_history
			  WHERE ( PN_INVENTORY_HISTORY.ORDER_TYPE = d.order_type ) AND
			   ( PN_INVENTORY_HISTORY.ORDER_NO = d.order_number ) AND
			   ( PN_INVENTORY_HISTORY.ORDER_LINE = d.order_line ) AND
			   ( PN_INVENTORY_HISTORY.TRANSACTION_TYPE like '%RECEIVING%' ))
			  as ActualRcvDate from ORDER_DETAIL d , ORDER_HEADER h where
			 d.ORDER_NUMBER = h.ORDER_NUMBER
			   and  d.STATUS = 'CLOSED' and
			  d.ORDER_TYPE  in ('PO' , 'RO') ;
