-- Domain: order_plan  |  Windowed: no  |  Binds: none
SELECT
    A.ORDER_TYPE || '_' || A.ORDER_NUMBER AS HostOrderID,
    A.ORDER_TYPE || '_' || A.ORDER_NUMBER || '_' || B.ORDER_LINE AS OrderID,
    B.ORDER_LINE AS OrderLineID,
    A.REQUESTER_LOCATION AS HostLocID,
    A.RELATION_CODE AS HostVendorLocID,
    A.RELATION_CODE AS HostShopID,
    A.SHIPPED_FROM_LOCATION AS HostReplSourceLocID,
    A.CREATED_DATE AS PlanOrderDate,
    A.SHIP_VIA_DATE AS PlanShipDate,
    A.CREATED_DATE AS ActualOrderDate,
    A.ORDER_TYPE AS OrderTypeID,
    B.PN AS HostPartID,
    B.STATUS AS OrderStatus,
    B.DELIVERY_DATE AS PlanRcvDate,
    B.DELIVERY_DATE AS PlanAvailDate,
    B.QTY_REQUIRE AS PlanQuantity,
    B.QTY_RECEIVED AS ReceivedQuantity,
    DECODE(
        B.QTY_RECEIVED,
        0,
        NULL,
        (
            SELECT MAX(H.CREATED_DATE)
            FROM PN_INVENTORY_HISTORY H
            WHERE H.ORDER_NO = B.ORDER_NUMBER
              AND H.ORDER_LINE = B.ORDER_LINE
              AND H.TRANSACTION_TYPE IN ('PO/RECEIVING')
        )
    ) AS ActualRcvDate,
    B.UNIT_COST AS UnitCost
FROM ORDER_HEADER A
JOIN ORDER_DETAIL B
  ON A.ORDER_NUMBER = B.ORDER_NUMBER
 AND A.ORDER_TYPE = B.ORDER_TYPE
WHERE (
        UPPER(TRIM(A.ORDER_TYPE)) = 'PO'
        AND UPPER(TRIM(B.STATUS)) = 'OPEN'
    )
   OR (
        UPPER(TRIM(A.ORDER_TYPE)) = 'RO'
        AND NVL(B.QTY_REQUIRE, 0) > NVL(B.QTY_RECEIVED, 0)
    )
ORDER BY
    A.CREATED_DATE DESC,
    A.ORDER_TYPE,
    A.ORDER_NUMBER,
    B.ORDER_LINE;
