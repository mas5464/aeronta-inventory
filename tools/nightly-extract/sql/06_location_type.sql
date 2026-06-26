-- Domain: location_type  |  Windowed: no  |  Binds: none
select SYSTEM_CODE as HostLocTypeID, SYSTEM_CODE_DESCRIPTION as LocTypeName from SYSTEM_TRAN_CODE where SYSTEM_TRANSACTION = 'LOC/CATEGORY' ;
