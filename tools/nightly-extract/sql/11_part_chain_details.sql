-- Domain: part_chain_details  |  Windowed: no  |  Binds: none
select pn_interchangeable as HostPartID ,
			 pn  as HostChainParentID ,
			'0' as RelationType ,
			to_char(DBMS_LOB.SUBSTR( ( notes_text ),255,1 )) AS PartChainNote
			from pn_interchangeable ,
			 note_pad
			where interchangeable_type = 'B' and pn_interchangeable.NOTES = note_pad.notes(+)
			 union select pn as HostPartID , pn_interchangeable as HostChainParentID , '1' as RelationType ,
			 to_char(DBMS_LOB.SUBSTR( ( notes_text ),255,1 )) AS PartChainNote
				from pn_interchg_one_way, note_pad where pn_interchg_one_way.conditional_notes = note_pad.notes(+) ;
