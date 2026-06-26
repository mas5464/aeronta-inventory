-- Domain: sales_order  |  Windowed: no  |  Binds: none
select d.ORDER_NUMBER || d.ORDER_LINE as HostSalesOrderID ,
			d.PN as HostPartID ,
			h.LOCATION as HostLocID ,
			d.REQUIRE_DATE as SalesOrderDtDue ,
			d.QTY_REQUIRE as OrderedQty ,
			d.QTY_SHIPPED as ShippedQty ,
			d.QTY_REQUIRE - d.QTY_SHIPPED as OpenQty  ,
			h.PRIORITY as Priority
			from CUSTOMER_ORDER_DETAIL d , CUSTOMER_ORDER_HEADER h
			where d.STATUS = 'OPEN'
			 and d.ORDER_NUMBER = h.ORDER_NUMBER ;
