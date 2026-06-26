-- Domain: vendor  |  Windowed: no  |  Binds: none
SELECT v.relation_code as HostVendorID, v.name as VendorName, v.status as VendorStatus, v.approval_description as Description,
				(SELECT to_char(DBMS_LOB.SUBSTR( ( notes_text ),255,1 )) FROM NOTE_PAD WHERE NOTES = v.NOTES  ORDER BY MODIFIED_DATE DESC FETCH FIRST 1 ROW ONLY) as Notes
				FROM RELATION_MASTER v WHERE v.relation_transaction = 'VENDOR' ;
