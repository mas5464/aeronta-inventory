-- Domain: order_plan_data_requisition  |  Windowed: no  |  Binds: none
select 'REQ' ||'_'|| RH.requisition ||'_'|| RD.requisition_line as HostOrderID,
	       'REQ' ||'_'|| RH.requisition ||'_'|| RD.requisition_line as OrderID,
	       RH.REQUESTER_LOCATION as HostLocID,
	       '' as HostVendorLocID,
	       RD.ASSIGN_TO as HostReplSourceLocID,
	       RD.CREATED_DATE as PlanOrderDate,
	       '' as PlanShipDate,
	       RH.CREATED_DATE as ActualOrderDate,
	       'REQ' as OrderTypeID,
	       RD.PN as HostPartID,
	       RD.STATUS as OrderStatus,
	       RD.REQUIRE_DATE as PlanRcvDate,
	       RD.REQUIRE_DATE as PlanAvailDate,
	       RD.QTY_REQUIRE as PlanQuantity,
	       RD.QTY_RECEIVED as ReceivedQuantity,
	       '' as ActualRcvDate
			from Requisition_Header RH, requisition_detail RD
			where RH.requisition = RD.REQUISITION and RH.status = 'OPEN'
			and ( RH.REQUISITION ,  RD.REQUISITION_LINE ) not in(select REQUISITION, REQUISITION_LINE from ORDER_DETAIL
			where requisition_line is not null and requisition > 0  )
			ORDER BY RH.CREATED_DATE DESC ;
