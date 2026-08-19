from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from random import Random
from typing import Any

from BaseClasses import Item
from worlds.AutoWorld import World

from . import (
    alternate_pickup,
    blacksmith,
    bonus_floor,
    floor_item_pool,
    fortune_teller,
    intro_skip,
    item_manifest,
    items,
    locations,
    monster_shop,
    monster_spawns,
    nada_send,
    native_bugfixes,
    options,
    patch,
    pool_house,
    regions,
    rules,
    save_removal,
    shop_prices,
    species_move_fix,
    tower_send,
    town_receive,
    town_shop,
    town_status_strip,
    town_warp,
    web_world,
)


# Release-channel split (2026-08-09): seeds generated from the SOURCE world -
# the dev install - emit `.adpatch-dev`, which only the dev client opens.
# Seeds generated from a promoted `.apworld` - the stable install, or any
# player's - emit `.adpatch`, which only the stable client opens. Loader
# inspection rather than a constant so promotion needs no code rewrite: being
# inside an apworld zip IS being the release artifact, by construction
# (tools/Promote-AdapStable.py is the only thing that builds one).
def _running_from_apworld() -> bool:
    import zipimport

    spec = globals().get("__spec__")
    loader = getattr(spec, "loader", None) or globals().get("__loader__")
    return isinstance(loader, zipimport.zipimporter)


PATCH_EXTENSION = ".adpatch" if _running_from_apworld() else ".adpatch-dev"


class AzureDreamsWorld(World):
    """
    Climb the 40-floor Monster Tower, finding multiworld checks on each floor
    and progressive keycards that unlock each successive five-floor band.
    Reach floor 40 and acquire the Ultimate Egg to complete the goal.
    """

    game = items.GAME_NAME
    web = web_world.AzureDreamsWebWorld()
    options_dataclass = options.AzureDreamsOptions
    options: options.AzureDreamsOptions
    topology_present = True
    origin_region_name = regions.TOWN_REGION

    item_name_to_id = items.ITEM_NAME_TO_ID
    location_name_to_id = locations.LOCATION_NAME_TO_ID
    # The great/good/junk groups are gone with the tiers they named. These
    # describe what an item IS, which is the only thing left to group by once
    # the pool is flat.
    item_name_groups = {
        "Keycards": {items.PROGRESSIVE_KEYCARD},
        "Traps": set(items.TRAP_NAMES),
        "Balls": {
            reward.name for reward in items.item_manifest.BALL_REWARDS
        },
        "Eggs": {reward.name for reward in items.item_manifest.EGG_REWARDS},
        "Equipment": {
            reward.name
            for reward in items.item_manifest.NATIVE_REWARDS
            if reward.category in items.item_manifest.EQUIPMENT_CATEGORIES
        },
        "Familiar Items": set(items.item_manifest.FAMILIAR_REWARD_NAME_SET),
    }

    def generate_early(self) -> None:
        # Traps must never leave this world: the machinery that springs one
        # exists only in this player's tower (and the disguise only in this
        # player's dialogue). local_items is the fill-level enforcement; the
        # shop item rule (rules.py) then narrows "own world" to "own tower".
        if self.options.traps:
            self.options.local_items.value.update(items.TRAP_NAMES)

    def create_regions(self) -> None:
        regions.create_and_connect_regions(self)
        locations.create_all_locations(self)

    def create_items(self) -> None:
        items.create_all_items(self)

    def set_rules(self) -> None:
        rules.set_all_rules(self)

    def create_item(self, name: str) -> items.AzureDreamsItem:
        return items.create_item(self, name)

    def get_filler_item_name(self) -> str:
        # Flat pool, so there is no junk list to draw from any more; anything
        # in the manifest is as valid a filler as anything else.
        return self.random.choice(items.item_manifest.NATIVE_REWARDS).name

    def post_fill(self) -> None:
        monster_shop.shape_monster_shop(self)

    def _seed_signature(self) -> bytes:
        return patch.make_seed_signature(
            str(self.multiworld.seed_name),
            self.player,
            self.multiworld.player_name[self.player],
        )

    def _is_own_keycard(self, item: Item) -> bool:
        """Whether a tower placement raises *this* player's clearance.

        Only these floors keep their Return to Town withheld: the elevator
        offers no way down at a clearance ceiling when the floor still holds
        progress, on the grounds that the player can pick it up and climb on.

        The test has to be ownership, not game. Another Azure Dreams player's
        keycard is named Progressive Keycard and belongs to this same game, but
        collecting it raises nobody's clearance here - counting it stranded a
        player at their ceiling with no elevator down and no way to earn one.
        """

        return item.player == self.player and item.name == items.PROGRESSIVE_KEYCARD

    def _is_own_trap(self, item: Item) -> bool:
        return (
            item.player == self.player
            and item.game == self.game
            and items.is_trap_name(item.name)
        )

    def _tower_placements(self) -> list[patch.LocationPlacement]:
        """One record per SEED-PAGE slot, which is always three a floor.

        The page's layout, the render resolver's bounds and the journal's bit
        per slot are all baked into the base patch at three, so a carrier-less
        seed still emits 117 records - it simply never spawns the third. The
        filler copies the floor's first placement rather than inventing a name,
        because the page pools its text: a repeat costs the three-byte
        reference and no message bytes at all, and the message region is the
        thing that runs out first in a busy room.
        """

        placements: list[patch.LocationPlacement] = []
        placed_slots = locations.tower_slots_for(self)
        for floor in range(1, locations.TOWER_FLOOR_COUNT + 1):
            for slot in range(locations.TOWER_SLOTS_PER_FLOOR):
                if slot >= placed_slots:
                    placements.append(placements[-1])
                    continue
                location = self.get_location(locations.tower_location_name(floor, slot))
                if location.item is None:
                    raise ValueError(f"Azure Dreams location {location.name!r} was not filled.")
                # A trap of our own wears a Progressive Keycard's face in
                # every dialogue the game renders from this placement - the
                # floor message, the at-feet menu, the description box. The
                # AP name (spoiler, server log, hints) stays truthful; only
                # the GAME lies. progressive_keycard stays False, and must:
                # that mask withholds the elevator's Return to Town at a
                # clearance ceiling on the promise that grabbing the item
                # raises clearance - a trap breaks the promise and would
                # strand the player on the ceiling floor.
                placements.append(
                    patch.LocationPlacement(
                        # The name the GAME may use, which drops the quality of
                        # anything unidentified - the floor message and the
                        # at-feet menu both render from this, and either one
                        # naming `Vital Sword (-1)` gives away what the
                        # inventory is deliberately hiding.
                        item_name=(
                            items.PROGRESSIVE_KEYCARD
                            if self._is_own_trap(location.item)
                            else item_manifest.display_name_for(location.item.name)
                        ),
                        recipient_name=self.multiworld.player_name[location.item.player],
                        remote=location.item.player != self.player,
                        progressive_keycard=self._is_own_keycard(location.item),
                        # Suppresses the "Found ..." box for this placement
                        # only - the fakeout dialogues (step-on, at-feet
                        # name, description) all stay, because the player
                        # has not committed until the pickup.
                        trap=self._is_own_trap(location.item),
                    )
                )
        return placements

    def _tower_hint_classes(self) -> list[int]:
        """One fortune-teller hint class per tower location, in location-index
        order (`(floor - 1) * slots + slot`, the journal's own order). What her
        crystal says a check holds - by kind, never by name."""

        classes: list[int] = []
        placed_slots = locations.tower_slots_for(self)
        for floor in range(1, locations.TOWER_FLOOR_COUNT + 1):
            for slot in range(locations.TOWER_SLOTS_PER_FLOOR):
                if slot >= placed_slots:
                    # Class 0 reads as "something the mist will not let me
                    # name", and she never reaches it anyway: her scan mask
                    # drops the carrier bit with the carrier.
                    classes.append(0)
                    continue
                location = self.get_location(locations.tower_location_name(floor, slot))
                if location.item is None:
                    raise ValueError(f"Azure Dreams location {location.name!r} was not filled.")
                classes.append(fortune_teller.hint_class_for_item(location.item, self.player))
        return classes

    def generate_output(self, output_directory: str) -> None:
        placements = self._tower_placements()

        # Every OTHER Azure Dreams player in the room, capped: the tower Send
        # row's targets. With none, the row drops out entirely rather than
        # offering a send with nowhere to go. (Nada used to share this list;
        # she no longer sends at all.)
        tower_send_targets = [
            self.multiworld.player_name[other]
            for other in self.multiworld.get_game_players(self.game)
            if other != self.player
        ][:tower_send.MAX_TARGETS]
        # The carrier's colour: one of the species' own variant palettes,
        # drawn per seed (patch.CARRIER_PALETTE_CHOICES owns the account).
        carrier_palette = self.random.choice(patch.CARRIER_PALETTE_CHOICES)
        seed_block = bytearray(
            patch.build_seed_block(
                self._seed_signature(),
                placements,
                tower_send_targets,
                carrier_palette=carrier_palette,
            )
        )
        # The send-mode routines and the target-name table. They live in the
        # seed page but are built here because they reference both patch.py's
        # layout and alternate_pickup's hook addresses.
        tower_send.place_seed_page_blocks(seed_block, tower_send_targets)
        # Per-floor monster rosters: one native type traded for this floor's
        # location-check carrier, its slots handed to the survivors. Rolled from
        # the world's own random, so it is part of the seed.
        #
        # With the carrier system off none of it applies: no roster is rewritten
        # (every floor keeps its vanilla population), the species table stays
        # zero, and the forced-spawn hook below is not written at all - so the
        # game's own floor population runs exactly as it shipped.
        spawn_tables: list[bytes] = []
        if self.options.carrier_system:
            spawn_tables, carrier_species = monster_spawns.plan_floor_spawns(self.random)
            monster_spawns.place_carrier_table(seed_block, carrier_species)
        seed_block = bytes(seed_block)
        # The per-floor text bank: one sector per floor, cut from the FINAL
        # seed block so every sector's static window content is byte-identical
        # to what boots resident (the loader lands sectors over live memory).
        floor_pages = patch.build_floor_page_sectors(seed_block, placements)
        base_patch = (
            resources.files(__package__)
            .joinpath("data", "azure_dreams_base.ppf")
            .read_bytes()
        )
        description = f"ADAP {self.multiworld.seed_name} P{self.player}"
        player_patch = bytearray(
            patch.build_player_ppf(
                base_patch,
                seed_block,
                description,
                floor_pages,
                spawn_tables,
                carrier_system=bool(self.options.carrier_system),
            )
        )
        shop_slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        for slot in range(locations.SHOP_LOCATION_COUNT):
            location = self.get_location(locations.shop_location_name(slot))
            if location.item is None:
                raise ValueError(f"Azure Dreams location {location.name!r} was not filled.")

            native_reward = item_manifest.REWARD_BY_NAME.get(location.item.name)
            use_native_descriptor = (
                location.item.game == self.game and native_reward is not None
            )
            shop_slots[slot] = town_shop.ShopSlot(
                descriptor=(
                    native_reward.descriptor
                    if use_native_descriptor
                    else town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR
                ),
                price=shop_prices.shop_slot_price(self.random, slot, location.item),
                display_name=(
                    None
                    if use_native_descriptor
                    else town_shop.REMOTE_ITEM_DISPLAY_NAME
                ),
                # Described the same way whatever it is. A native reward keeps
                # its own descriptor and name - those fit and are useful - but
                # its description says what the slot actually holds and who for,
                # which its vanilla flavour text cannot.
                description=town_shop.format_shop_description(
                    item_manifest.display_name_for(location.item.name),
                    self.multiworld.player_name[location.item.player],
                ),
            )

        shop_payload = town_shop.build_town_shop_payload(
            shop_slots,
            self._seed_signature(),
        )
        town_payload = town_receive.build_town_receive_payload(shop_payload)
        town_receive.append_town_receive_ppf_records(player_patch, town_payload)
        town_shop.append_town_shop_hook_ppf_records(player_patch)
        town_warp.append_town_warp_ppf_records(player_patch)
        # Nada is the town receive NPC and nothing else since 0.9.109 - her
        # send menu is gone, sending is the tower's token-priced feature - so
        # her bytes no longer depend on who else is in the room.
        nada_send.append_nada_ppf_records(player_patch)
        save_removal.append_save_removal_ppf_records(player_patch)
        # Repairs to bugs that shipped in the retail game, not ADAP behaviour.
        # Order-independent: they touch vanilla combat code nothing else in this
        # generator writes, which is asserted by the disc builder refusing any
        # two records that disagree about a byte.
        native_bugfixes.append_native_bugfix_ppf_records(player_patch)
        # The group-V move-routine fix: two words in each of the 35 disc copies
        # of the five species packages whose walk re-aims at Koh and turns a
        # fleeing carrier into an attacker. Nothing else writes the species
        # archive (docs/game/carrier-attack-theories.md §1).
        species_move_fix.append_species_move_fix_ppf_records(player_patch)
        # Wotta's pool house with every girl present (no-op when
        # pool_house.POOL_HOUSE_OPEN is off). Seven halfwords in the interior
        # overlay's spawn templates; nothing else writes TOWN.BIN+0x51E000.
        pool_house.append_pool_house_ppf_records(player_patch)
        # The blacksmith: takes over Ghosh's scene entry in the equipment shop
        # (three overlay edits) and lives in dialogue spans town_shop's trims
        # freed - nothing else writes either (docs/systems/blacksmith.md).
        if self.options.temper_system:
            blacksmith.append_blacksmith_ppf_records(player_patch)
        # The fortune teller and the ball charger: their conversations, the
        # natives and this seed's hint-class table, all inside her own dialogue
        # image (TOWN.BIN+0x62BFC0, loaded only in her building) plus two
        # overlay words for the charger - nothing else writes either
        # (docs/systems/fortune-teller.md). Each behind its yaml option.
        fortune_teller.append_fortune_teller_ppf_records(
            player_patch,
            self._tower_hint_classes(),
            hints=bool(self.options.hint_system),
            charger=bool(self.options.temper_system),
            # Her "is this floor done?" test is a mask of the floor's journal
            # bits. Leaving the carrier bit in it on a carrier-less seed would
            # make every floor look unfinished forever, and she would sell a
            # reading for a check that does not exist.
            slots_per_floor=locations.tower_slots_for(self),
        )
        # The town status strip (WEAPON/SHIELD/BALL levels along the top of
        # the screen, everywhere in town): the town image's free canvas plus
        # one retargeted jal in the scene runner. Meaningless without the
        # temper system, so it rides with it (docs/systems/fortune-teller.md 6).
        if self.options.temper_system and town_status_strip.TOWN_STATUS_STRIP:
            town_status_strip.append_town_status_strip_ppf_records(player_patch)
        # The floor spawn-pool rebalance: deliberate behaviour change, same for
        # every seed - un-gates the short-run-critical mode-2 items and retunes
        # the rarity-class weights (128/85/32/1 -> 108/85/32/6). Resident flag
        # halfwords plus four instruction words in both floor-generation
        # package copies; nothing else writes either region.
        floor_item_pool.append_floor_item_pool_ppf_records(
            player_patch, temper_system=bool(self.options.temper_system)
        )
        alternate_pickup.append_alternate_pickup_ppf_records(player_patch)
        # The tower players-menu Send row. Skipped entirely - no row, no
        # widened loops, no strip - when the room has no other Azure Dreams
        # player to send to.
        tower_send.append_tower_send_ppf_records(player_patch, tower_send_targets)
        # Strictly after alternate_pickup: the bonus floor REWRITES that
        # module's item-name guard in place to veil our loot, so it has to land
        # on top of it. Appending earlier is silently undone.
        bonus_floor.append_bonus_floor_ppf_records(
            player_patch, Random(int.from_bytes(seed_block[:8], "little"))
        )
        for shop_index in range(town_shop.IMPLEMENTED_SHOP_COUNT):
            shop_text_sector = town_shop.build_shop_text_sector(
                shop_slots,
                shop_index,
                self._seed_signature(),
            )
            patch.append_mode2_form1_sector_ppf_records(
                player_patch,
                town_shop.SHOP_TEXT_SECTOR_LBAS[shop_index],
                shop_text_sector,
            )
        # Keep this last: the intro-skip appender audits its two trusted
        # ProGrammar ranges against every other generated PPF record.
        intro_skip.append_intro_skip_ppf_records(player_patch)
        # The container is still PPF1; only the extension is ours. A distinct
        # extension lets the client register a file association without taking
        # .ppf away from every other PSX patching tool on the machine.
        output_path = (
            Path(output_directory)
            / f"{self.multiworld.get_out_file_name_base(self.player)}{PATCH_EXTENSION}"
        )
        output_path.write_bytes(bytes(player_patch))

    def fill_slot_data(self) -> Mapping[str, Any]:
        # Every own tower location holding one of this world's traps, with
        # the GAME trap id to poke into the forced-trap request byte. The
        # client springs a trap at the moment it reports that location's
        # check to the server - the server's checked-location set is the
        # durable exactly-once anchor, so no journal growth was needed.
        # JSON keys are strings by the time slot data reaches the client.
        trap_locations: dict[str, int] = {}
        for floor in range(1, locations.TOWER_FLOOR_COUNT + 1):
            for slot in range(locations.tower_slots_for(self)):
                location = self.get_location(locations.tower_location_name(floor, slot))
                if (
                    location.item is not None
                    and self._is_own_trap(location.item)
                    and location.address is not None
                ):
                    trap_locations[str(location.address)] = (
                        items.TRAP_GAME_ID_BY_NAME[location.item.name]
                    )

        return {
            # 13: the 5000-gold packages exist. A client that does not know
            # the gold item id would wedge its receive queue on the first
            # package, so the version gate does the refusing instead.
            # 14: ADSV grew to v3/0x2C for the gold-granted counter; a v2
            # client cannot read the journal at all.
            # 15: trap items exist. A pre-trap client would deliver a trap
            # item into inventory as a Strange... gift instead of springing
            # it, so the gate refuses again.
            # 16: `Send Token` items exist. A client that does not know the
            # id would try to deliver one as a native reward, wedging the
            # queue on an item with no descriptor - and would never bank it,
            # so the player would collect tokens that never arrive.
            # 17: three tower checks per floor (117), ADSV v4 (moved base, a
            # one-byte-per-floor tower journal, the town half of one unified
            # mask, the intro flags in a field of their own), and the third
            # check rides a monster.
            # 18: White Sand cuts the line (the ball charger's level byte
            # beside ADSV); the two yaml toggles below are informational.
            # 19: `carrier_system` can be OFF, and then a floor has two checks
            # rather than three. The client has to be told: it draws a chest
            # per slot and reads the journal a bit per slot, and a client that
            # assumed three would show 39 checks that can never be collected.
            "apworld_version": 19,
            "hint_system": bool(self.options.hint_system),
            "temper_system": bool(self.options.temper_system),
            "carrier_system": bool(self.options.carrier_system),
            "trap_locations": trap_locations,
            "tower_location_id_base": locations.LOCATION_ID_BASE,
            "tower_floor_count": locations.TOWER_FLOOR_COUNT,
            # What this room actually placed - two without the carrier - not
            # the three the seed page is always laid out for.
            "tower_slots_per_floor": locations.tower_slots_for(self),
            "tower_location_count": (
                locations.TOWER_FLOOR_COUNT * locations.tower_slots_for(self)
            ),
            "shop_location_id_base": locations.SHOP_LOCATION_ID_BASE,
            "shop_location_count": locations.SHOP_LOCATION_COUNT,
            "progressive_keycard_count": items.PROGRESSIVE_KEYCARD_COUNT,
            "goal_floor": 40,
            "seed_signature": self._seed_signature().hex(),
            "seed_block_address": patch.SEED_BLOCK_ADDRESS,
            "persistent_state_address": patch.PERSISTENT_STATE_ADDRESS,
            "persistent_state_size": patch.PERSISTENT_STATE_SIZE,
            "persistent_location_mask_address": (
                patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_LOCATION_MASK_OFFSET
            ),
            "persistent_received_item_count_address": (
                patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_RECEIVED_ITEM_COUNT_OFFSET
            ),
            "persistent_keycard_level_address": (
                patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_KEYCARD_LEVEL_OFFSET
            ),
            "persistent_shop_mask_address": (
                patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_SHOP_MASK_OFFSET
            ),
        }
