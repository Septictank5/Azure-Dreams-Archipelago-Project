"""Trap items in the multiworld pool.

Own-world only (local_items), tower only (the shop item rule), disguised as
Progressive Keycards in every placement the GAME renders, truthful
everywhere Archipelago speaks for itself (spoiler, slot data, item ids).
`docs/systems/forced-trap.md` owns the design.
"""

import unittest
from random import Random

from BaseClasses import ItemClassification
from Fill import distribute_items_restrictive
from test.general import setup_multiworld
from worlds.generic.Rules import locality_rules

from .. import items, locations, options, patch
from ..world import AzureDreamsWorld
from .bases import AzureDreamsTestBase


class TestTrapsDisabledByDefault(AzureDreamsTestBase):
    def test_no_trap_items_without_the_option(self) -> None:
        self.assertFalse(
            any(items.is_trap_name(item.name) for item in self.multiworld.itempool)
        )

    def test_the_default_chance_is_low(self) -> None:
        """The client's Create YAML dialog opens on this same number.

        A trap spends a whole tower check on a setback and they compound,
        so the default is deliberately a couple per seed rather than a
        handful. If this moves, move `AzureDreamsPlayerYaml`'s
        `DefaultTrapChance` with it or the dialog quietly disagrees with
        an omitted option.
        """

        self.assertEqual(options.TrapChance.default, 3)
        self.assertEqual(options.TrapChance.range_end, 100)


class TestTrapPool(AzureDreamsTestBase):
    options = {"traps": True, "trap_chance": 60}

    def test_traps_replace_filler_without_changing_the_pool_size(self) -> None:
        self.assertEqual(len(self.multiworld.itempool), 98)
        traps = [
            item for item in self.multiworld.itempool if items.is_trap_name(item.name)
        ]
        self.assertGreater(len(traps), 0)
        self.assertLessEqual(len(traps), items.TRAP_COUNT_CAP)
        for trap in traps:
            self.assertEqual(trap.classification, ItemClassification.trap)
            game_id = items.TRAP_GAME_ID_BY_NAME[trap.name]
            self.assertEqual(trap.code, items.TRAP_ITEM_ID_BASE + game_id)

    def test_traps_are_forced_local(self) -> None:
        self.assertTrue(
            items.TRAP_NAMES <= self.world.options.local_items.value
        )


class TestTrapTypeRoll(unittest.TestCase):
    def test_monster_den_is_pinned_to_one_percent(self) -> None:
        # 20,000 rolls: the den share estimator lands near 0.01 and every
        # other id in the set appears. Seeded, so this cannot flake.
        random = Random(0xADA9)
        rolled = [items.roll_trap_name(random) for _ in range(20_000)]
        dens = sum(name == "Monster Den Trap" for name in rolled)
        self.assertGreater(dens, 100)   # ~200 expected
        self.assertLess(dens, 320)
        self.assertEqual(set(rolled), set(items.TRAP_NAME_BY_GAME_ID.values()))

    def test_the_pool_excludes_the_dangerous_and_pointless_ids(self) -> None:
        # go-up (4) is the bonus-floor entry, the dud (6) does nothing, and
        # crack/upheaval (15/16) are gated on per-floor support the forced
        # spring ignores.
        for game_id in (4, 6, 15, 16):
            self.assertNotIn(game_id, items.TRAP_NAME_BY_GAME_ID)


class TestTrapPlacement(unittest.TestCase):
    """A filled two-player room at maximum trap chance."""

    def setUp(self) -> None:
        self.multiworld = setup_multiworld(
            [AzureDreamsWorld, AzureDreamsWorld],
            options=[
                {"traps": True, "trap_chance": 100},
                {},
            ],
        )
        # Real generation applies locality between item creation and the
        # fill (main.py); the test flow must too, or local_items is inert.
        locality_rules(self.multiworld)
        distribute_items_restrictive(self.multiworld)
        self.mine: AzureDreamsWorld = self.multiworld.worlds[1]
        self.theirs: AzureDreamsWorld = self.multiworld.worlds[2]

    def _trap_locations(self):
        return [
            location
            for location in self.multiworld.get_locations()
            if location.item is not None
            and location.item.game == items.GAME_NAME
            and items.is_trap_name(location.item.name)
        ]

    def test_every_trap_lands_in_its_own_tower(self) -> None:
        placed = self._trap_locations()
        self.assertGreater(len(placed), 0)
        tower_names = {
            locations.tower_location_name(floor, slot)
            for floor in range(1, locations.TOWER_FLOOR_COUNT + 1)
            for slot in range(locations.TOWER_SLOTS_PER_FLOOR)
        }
        for location in placed:
            self.assertEqual(location.item.player, 1)
            self.assertEqual(location.player, 1)
            self.assertIn(location.name, tower_names)

    def test_trap_placements_wear_the_keycard_disguise(self) -> None:
        placements = self.mine._tower_placements()
        placed_addresses = {
            location.address for location in self._trap_locations()
        }
        self.assertGreater(len(placed_addresses), 0)
        index_by_address = {}
        index = 0
        for floor in range(1, locations.TOWER_FLOOR_COUNT + 1):
            for slot in range(locations.TOWER_SLOTS_PER_FLOOR):
                location = self.mine.get_location(
                    locations.tower_location_name(floor, slot)
                )
                index_by_address[location.address] = index
                index += 1
        for address in placed_addresses:
            placement = placements[index_by_address[address]]
            self.assertEqual(placement.item_name, items.PROGRESSIVE_KEYCARD)
            self.assertFalse(placement.remote)
            # NEVER the keycard mask: it withholds the elevator's Return to
            # Town at a clearance ceiling on the promise that grabbing the
            # item raises clearance. A trap with the bit set would strand
            # the player on the ceiling floor.
            self.assertFalse(placement.progressive_keycard)

    def test_slot_data_maps_every_trap_and_gates_the_client(self) -> None:
        slot_data = self.mine.fill_slot_data()
        self.assertEqual(slot_data["apworld_version"], 16)
        trap_map = slot_data["trap_locations"]
        placed = self._trap_locations()
        self.assertEqual(len(trap_map), len(placed))
        for location in placed:
            self.assertEqual(
                trap_map[str(location.address)],
                items.TRAP_GAME_ID_BY_NAME[location.item.name],
            )
        # The trap-free player's map is empty, not absent - the client reads
        # the key unconditionally.
        self.assertEqual(self.theirs.fill_slot_data()["trap_locations"], {})

    def test_the_seed_page_carries_the_disguise(self) -> None:
        """End of the chain: the bytes the game renders dialogue from."""

        placements = self.mine._tower_placements()
        block = patch.build_seed_block(b"12345678", placements)
        keycard_text = patch.encode_item_slot_text(items.PROGRESSIVE_KEYCARD)
        placed_addresses = {
            location.address for location in self._trap_locations()
        }
        base = locations.LOCATION_ID_BASE
        checked = 0
        for address in placed_addresses:
            index = address - base
            floor = index // locations.TOWER_SLOTS_PER_FLOOR + 1
            slot = index % locations.TOWER_SLOTS_PER_FLOOR
            pages = patch.build_floor_page_sectors(block, placements)
            page = pages[floor - 1]
            slot_offset = (
                patch.FLOOR_PAGE_ITEM_SLOTS_OFFSET
                - patch.FLOOR_PAGE_WINDOW_OFFSET
                + slot * patch.FLOOR_PAGE_ITEM_SLOT_SIZE
            )
            self.assertEqual(
                page[slot_offset : slot_offset + len(keycard_text)],
                keycard_text,
            )
            checked += 1
            if checked >= 4:
                break
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
