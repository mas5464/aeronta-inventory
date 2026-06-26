-- Domain: pn_vendor_price  |  Windowed: no  |  Binds: none
select
			   vendor as HostVendorLocID  ,
			   pn_interchangeable as HostPartID   ,
			   location as HostLocID   ,
			   lead_days  as ProcessingLength   ,
			   pn_vendor_price_category as OrderTypeID,
			   PN_VENDOR_PRICE.PRICE as Price,
			   PN_VENDOR_PRICE.Price_2 as Price2,
			   PN_VENDOR_PRICE.Price_3 as Price3,
			   PN_VENDOR_PRICE.Price_4 as Price4,
			   CONDITION as Condition,
			   NVL(PREFER,'N') as Preferred,
			   MINIMUM_ORDER_QTY as MinOQ,
			   MINIMUM_ORDER_QTY_2 as MinOQ2,
			   MINIMUM_ORDER_QTY_3 as MinOQ3,
			   MINIMUM_ORDER_QTY_4 as MinOQ4
			   from pn_vendor_price where pn_vendor_price_category in ('RO' , 'PO')
			    and status  = 'ACTIVE' ;
