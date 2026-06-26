-- Domain: part_criticality  |  Windowed: no  |  Binds: none
select a.system_code as HostPartCriticalID , a.system_code_description as PartCriticalDesc
			 from system_tran_code a  where a.system_transaction = 'ESSENTIALITY' ;
