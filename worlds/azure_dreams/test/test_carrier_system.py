"""The `carrier_system` yaml option: what a room looks like without the
monster-carried third check.

The switch is deliberately lopsided. AP-side it is real - 39 fewer locations,
39 fewer items, and a fortune teller who stops counting the slot that is not
there. Disc-side it is ONE WORD: the seed page is always laid out for three
slots a floor because the base patch bakes that in, and dropping the forced
spawn is enough to mean the third one never appears.
"""

from __future__ import annotations

import struct
import unittest

from .. import fortune_teller as ft
from .. import items, locations, patch
from .bases import AzureDreamsTestBase


class TestCarrierOff(AzureDreamsTestBase):
    options = {"carrier_system": False}

    def test_two_checks_a_floor(self) -> None:
        self.assertEqual(locations.tower_slots_for(self.world), 2)
        tower = [
            location
            for location in self.world.get_locations()
            if location.address is not None
            and location.name.startswith("Tower Floor")
        ]
        self.assertEqual(len(tower), locations.TOWER_FLOOR_COUNT * 2)
        # Slot 3 is not a location; its NAME and ID stay reserved, because the
        # id is the game's own journal bit index and re-numbering per option
        # would make two rooms disagree about what an id means.
        self.assertFalse(any(name.endswith("Slot 3") for name in (l.name for l in tower)))
        self.assertIn("Tower Floor 01 - Slot 3", locations.LOCATION_NAME_TO_ID)
        self.assertEqual(
            locations.LOCATION_NAME_TO_ID["Tower Floor 02 - Slot 1"],
            locations.LOCATION_ID_BASE + 3,
        )

    def test_the_pool_loses_exactly_those_draws(self) -> None:
        self.assertEqual(items.carrier_reward_loss(self.world), 39)
        regular = [
            location
            for location in self.world.get_locations()
            if location.address is not None
        ]
        self.assertEqual(len(self.multiworld.itempool), len(regular))
        self.assertEqual(
            len(self.get_items_by_name(items.PROGRESSIVE_KEYCARD)),
            items.PROGRESSIVE_KEYCARD_COUNT,
        )
        self.assertEqual(
            len(self.get_items_by_name(items.GOLD_PACKAGE)), items.GOLD_PACKAGE_COUNT
        )

    def test_slot_data_tells_the_client_two(self) -> None:
        slot_data = self.world.fill_slot_data()
        self.assertIs(slot_data["carrier_system"], False)
        self.assertEqual(slot_data["tower_slots_per_floor"], 2)
        self.assertEqual(slot_data["tower_location_count"], 78)
        self.assertEqual(slot_data["apworld_version"], 19)

    def test_the_seed_page_still_gets_three_records_a_floor(self) -> None:
        # The page's layout, the render resolver's bounds and the journal's bit
        # per slot are baked into the base patch at three.
        from Fill import distribute_items_restrictive

        distribute_items_restrictive(self.multiworld)
        placements = self.world._tower_placements()
        self.assertEqual(len(placements), locations.TOWER_LOCATION_COUNT)
        self.assertEqual(len(placements), 117)
        # The filler repeats the floor's first placement, so it costs the page
        # a three-byte reference and no message bytes.
        for floor_index in range(locations.TOWER_FLOOR_COUNT):
            base = floor_index * 3
            self.assertIs(placements[base + 2], placements[base + 1])
        classes = self.world._tower_hint_classes()
        self.assertEqual(len(classes), 117)
        self.assertTrue(all(classes[index * 3 + 2] == 0 for index in range(39)))


class TestCarrierOn(AzureDreamsTestBase):
    def test_three_checks_a_floor(self) -> None:
        self.assertEqual(locations.tower_slots_for(self.world), 3)
        self.assertEqual(items.carrier_reward_loss(self.world), 0)
        slot_data = self.world.fill_slot_data()
        self.assertIs(slot_data["carrier_system"], True)
        self.assertEqual(slot_data["tower_slots_per_floor"], 3)
        self.assertEqual(slot_data["tower_location_count"], 117)


class TestTheOffSwitchIsOneWord(unittest.TestCase):
    """`build_player_ppf` differs by exactly the forced-spawn retarget."""

    def _records(self, carrier: bool) -> dict[int, bytes]:
        base = patch.SEED_BLOCK_SIZE
        ppf = patch.build_player_ppf(
            _base_ppf(),
            bytes(base),
            "carrier test",
            carrier_system=carrier,
        )
        records: dict[int, bytes] = {}
        cursor = patch.PPF_HEADER_SIZE
        while cursor < len(ppf):
            offset, length = struct.unpack_from("<IB", ppf, cursor)
            cursor += 5
            records[offset] = ppf[cursor : cursor + length]
            cursor += length
        return records

    def test_only_the_forced_spawn_hook_differs(self) -> None:
        on = self._records(True)
        off = self._records(False)
        missing = set(on) - set(off)
        self.assertEqual(set(off) - set(on), set())
        self.assertEqual(len(missing), 1)
        offset = missing.pop()
        sector, within = divmod(patch.CARRIER_SPAWN_HOOK_DUNGEON_OFFSET, patch.FORM1_USER_SIZE)
        self.assertEqual(
            offset,
            (patch.DUNGEON_BIN_BASE_LBA + sector) * patch.RAW_SECTOR_SIZE + 24 + within,
        )
        self.assertEqual(
            struct.unpack("<I", on[offset])[0],
            patch._j(0x03, patch.CARRIER_FORCED_STUB_ADDRESS),
        )
        # and every other record is byte-identical
        for shared in on.keys() & off.keys():
            self.assertEqual(on[shared], off[shared], hex(shared))


class TestFortuneTellerMask(unittest.TestCase):
    """Her "is this floor finished?" mask is the floor's slot bits, and it has
    to shrink with them - otherwise every floor looks unfinished forever and
    she sells a reading for a check that does not exist."""

    def _mask_immediates(self, slots: int) -> list[int]:
        layout = ft.build_layout(slots_per_floor=slots)
        code = layout.natives
        words = struct.unpack(f"<{len(code) // 4}I", code)
        # andi/addiu immediates equal to the all-collected mask
        return [word & 0xFFFF for word in words if (word >> 26) in (0x09, 0x0C)]

    def test_the_mask_follows_the_slot_count(self) -> None:
        self.assertIn(0b111, self._mask_immediates(3))
        self.assertNotIn(0b111, self._mask_immediates(2))
        self.assertIn(0b11, self._mask_immediates(2))

    def test_the_layout_is_the_same_size_either_way(self) -> None:
        three = ft.build_layout(slots_per_floor=3)
        two = ft.build_layout(slots_per_floor=2)
        self.assertEqual(len(three.natives), len(two.natives))
        self.assertEqual(three.script_address, two.script_address)
        self.assertEqual(three.charger_end, two.charger_end)

    def test_a_nonsense_slot_count_is_refused(self) -> None:
        for slots in (0, 1, 4):
            with self.assertRaises(ValueError):
                ft.build_layout(slots_per_floor=slots)


def _base_ppf() -> bytes:
    from importlib import resources

    return (
        resources.files("worlds.azure_dreams")
        .joinpath("data", "azure_dreams_base.ppf")
        .read_bytes()
    )
