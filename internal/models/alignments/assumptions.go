package alignments

// futureContext declares which fields are hindsight. Everything else is
// causal, so a live variant could emit aligned events and divergence starts
// at their own tick.
const futureContext = "only last_tick, end_tick, end, end_source, and truth_changes of a divergence read records after its start_tick, " +
	"up to the end of coverage. Its start_tick, observed and truth contents, and co_presence use only records at or before start_tick, " +
	"and every aligned event uses only its own record and earlier records of the same stream."

// assumptions lists every rule the alignment depends on. Each entry is a
// stable token followed by an explanation.
func assumptions() []string {
	return []string{
		"observation_from_container_views: an actor observes a container when it is sent a CONTENTS view of a menu backed by that container " +
			"(its complete contents) or a SLOT view of one of its slots; OPENED, CARRIED, and CLOSED views and menus without a container source observe nothing",
		"own_click_confirms_world_contents: an applied container_click while a container-backed menu is open sets the actor's observation of " +
			"that container to the world contents at the end of the click's tick. Vanilla adopts the client's predicted slots and sends only slots " +
			"that still differ (AbstractContainerMenu.broadcastChanges), so a correctly predicted own click produces no SLOT view",
		"menu_slot_mapping: menu slot i below container_slot_count is container slot i; for a double chest the first half of those slots belongs " +
			"to source.block_pos and the second half to source.secondary_block_pos; slots at or above the world container_size are not compared",
		"stack_equality: two stacks are equal when item_id, count, and damage match, and components_snbt matches when both records carry it",
		"end_of_tick_comparison: world snapshots describe the end of their tick, so observation and world contents are compared at the end of " +
			"each tick, after the tick's world records and then the actor's records in stream order",
		"truth_from_world_stream: world contents are the latest container_snapshot at or before the tick. They are unknown before the first " +
			"snapshot, after container_removed, and for LOOT_UNGENERATED contents; no divergence starts or continues without known contents",
		"divergence_requires_observation: a divergence needs at least one earlier observation of the container by the same actor; containers " +
			"an actor never observed are only counted in unobserved_container_count",
		"actor_is_play: each Play is one actor; observations never carry across connections of the same player",
		"coverage_bounds: a tick is evaluated only inside both the actor's Play and the world stream; an interval still open at the earlier " +
			"coverage end is cut there without end_tick, and its end names the coverage that ended",
		"co_presence_sample: co-presence uses the observer's latest perception sample at or before the divergence start, if it is less than one " +
			"sampling interval earlier and not after the last sample; participant menus are checked at the start tick itself",
		"index_bounds: the event index holds world records and block entity visibility only for containers some participant observed, entity " +
			"visibility only for minecraft:player entities, and container views only for container-backed menus, without CARRIED views",
	}
}

func knownLimitations() []string {
	return []string{
		"click_attributed_to_open_menu: captured container_click records carry no container id, so a click is attributed to the menu open " +
			"when it was applied; a click the server ignored for a stale container id still confirms the observation",
		"entity_inventories_not_compared: the world stream has no entity inventories (chest boats, minecarts, donkeys) and ender chests have no " +
			"container source, so they are never observed or compared",
		"slot_view_tick_lag: a SLOT view sent one tick after the world change it reports produces a one-tick divergence that ends REOBSERVED",
		"co_presence_is_reconstructed: container and entity visibility are copied from perception.jsonl and carry its assumptions and limitations",
		"whole_coverage_only: the processor has no tick selection; it covers the whole world stream and every named Play",
	}
}
