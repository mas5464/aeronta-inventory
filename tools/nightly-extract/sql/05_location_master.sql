-- Domain: location_master  |  Windowed: no  |  Binds: none
SELECT a.location as HostLocID,
				a.location as LocName,
				a.category as HostLocTypeID,
				a.location_description as LocDescription,
				a.RELATED_MAIN_WAREHOUSE as HostParentLocID,
				a.INVENTORY_PROVIDER as RecAssignedTo,
				a.STATION_CODE as CausalLocation,
				a.RELATED_MAIN_WAREHOUSE as HostReplSourceLocID
				FROM LOCATION_MASTER a ;
