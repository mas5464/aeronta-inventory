-- Domain: events  |  Windowed: yes  |  Binds: as_of_date, transaction
SELECT DISTINCT PLANNING.ROWID    AS HostEventID ,
						 ( select status
		    from WO_ENGINEERING_ORDER
		    where wo  = planning.wo and
		          eo  = planning.EO and
		          pn = PLANNING.PN and
		          SN = PLANNING.PN_SN)     AS WOEOSTATUS,
		  PLANNING.EO                     AS EVENTNAME,
		  PLANNING.DESCRIPTION            AS EVENTDESC,
		  PLANNING.WO_LOCATION            AS HOSTLOCID,
		  PLANNING.ac_type                AS HOSTPRODUCTID,
		  PLANNING.AC                     AS HOSTTAIL,
		  PLANNING.PN                     AS HOSTPARTID,
		  PLANNING.PN_SN                  AS HOSTPARTSN ,
		  PLANNING.DUE_DATE               AS DUE_DATE,
		  PLANNING.WO_SCHEDULE_START_DATE AS WO_SCHEDULE_START_DATE,
		  CASE
		    WHEN SCHEDULE_START_DATE IS NULL
		    THEN PLANNING.DUE_DATE
		    ELSE
		      CASE
		        WHEN PLANNING.DUE_DATE < SCHEDULE_START_DATE
		        THEN PLANNING.DUE_DATE
		        ELSE SCHEDULE_START_DATE
		      END
		  END AS SCHEDDATE,
		  CASE
		    WHEN PLANNING.RECORD_TYPE IN ('E/C','P/N E/C')
		    THEN PLANNING.CATEGORY
		    ELSE 'PNC'
		  END AS ECCATEGORY,
		  CASE
		    WHEN TRIM(PLANNING.PN_SN)IS NOT NULL
		    THEN 1
		    ELSE 0
		  END         AS SchedOccur,
		  WO.STATUS   AS woStatus ,
		  PLANNING.WO AS WO,
		  '' as RESERVE
		 FROM PLANNING,
		  WO
		 WHERE TRANSACTION                           = :transaction  AND planning.wo    = wo.wo(+)
		 AND (TRUNC(PLANNING.DUE_DATE)              <= TRUNC(:as_of_date)
		 OR (TRUNC(PLANNING.WO_SCHEDULE_START_DATE) <= TRUNC(:as_of_date))) ;
