-- Domain: trans_code  |  Windowed: no  |  Binds: none
select SYSTEM_CODE as HostPartTypeID, SYSTEM_CODE_DESCRIPTION as PartTypeName from SYSTEM_TRAN_CODE where SYSTEM_TRANSACTION = 'PNCATEGORY' ;
