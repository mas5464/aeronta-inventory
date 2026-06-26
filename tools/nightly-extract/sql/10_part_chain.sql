-- Domain: part_chain  |  Windowed: no  |  Binds: none
select p.pn_interchangeable as HostPartChainID,
							p.pn_interchangeable as PartChainName, notes_text	as PartChainNote
			 		from pn_interchangeable p , note_pad n   where p.conditional_notes = n.notes (+) ;
