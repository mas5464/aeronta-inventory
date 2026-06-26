-- Domain: causal_values  |  Windowed: yes  |  Binds: start_date, end_date
SELECT  am.ac_type  AS HostProductID ,
				 act.destination  as HostLocID ,
				 SUM(act.flight_hours * 60) + SUM(act.flight_MINUTES) as HostCausalMinutes ,
				 sum(act.CYCLES) AS CausalCycles ,
					 :start_date AS StartDate,
					 :end_date AS EndDate
				 FROM AC_ACTUAL_FLIGHTS act , AC_MASTER am
				 WHERE  ( am.AC = act.AC )
					  AND ( act."FLIGHT_DATE" >= :start_date )
					  AND ( act."FLIGHT_DATE"   <= :end_date )
				  GROUP  BY  am.ac_type ,  act.destination ;
