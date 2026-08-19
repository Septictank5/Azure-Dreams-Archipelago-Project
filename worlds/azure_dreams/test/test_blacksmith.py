"""The blacksmith (blacksmith.py): disc-pinned edits, the assembled natives run
in the simulator, the script's structure, and the record set.

Ground truth for the natives is `docs/systems/blacksmith.md` sections 4-5;
the disc bytes are pinned against `extracted/TOWN.BIN` when it is present.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from .. import blacksmith, patch, town_shop
from . import mips_sim

_TOWN_BIN = Path(__file__).resolve().parents[4] / "extracted" / "TOWN.BIN"


class TestBlacksmithDiscEdits(unittest.TestCase):
    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_overlay_originals_match_the_disc(self) -> None:
        town = _TOWN_BIN.read_bytes()

        def at(address: int, size: int) -> bytes:
            offset = town_shop.equipment_runtime_to_file_offset(address)
            return town[offset : offset + size]

        self.assertEqual(
            struct.unpack("<4I", at(blacksmith.GHOSH_ROW0_ADDRESS, 16)),
            blacksmith.GHOSH_ROW0_ORIGINAL,
        )
        self.assertEqual(at(blacksmith.GHOSH_TEMPLATE_ADDRESS, 20), blacksmith.GHOSH_TEMPLATE_ORIGINAL)
        self.assertEqual(
            at(blacksmith.GHOSH_TEMPLATE_ADDRESS + 20, 20), blacksmith.SPAWN_TERMINATOR_ORIGINAL
        )
        self.assertEqual(at(blacksmith.SMITH_GETTER_ADDRESS, 12), blacksmith.SMITH_GETTER_ORIGINAL_PREFIX)
        # return_zero really is `jr ra / addu v0,zero,zero`
        self.assertEqual(struct.unpack("<II", at(blacksmith.RETURN_ZERO_ADDRESS, 8)), (0x03E0_0008, 0x0000_1021))

    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_row0_is_the_only_reference_to_what_we_overwrite(self) -> None:
        town = _TOWN_BIN.read_bytes()
        overlay = town[town_shop.EQUIPMENT_OVERLAY_FILE_OFFSET : town_shop.EQUIPMENT_OVERLAY_FILE_OFFSET + 0x3000]
        for target in (blacksmith.SMITH_GETTER_ADDRESS, blacksmith.GHOSH_ROW0_ORIGINAL[1], blacksmith.GHOSH_ROW0_ORIGINAL[2]):
            hits = [
                0x8001_6000 + i
                for i in range(0, len(overlay) - 3, 4)
                if struct.unpack_from("<I", overlay, i)[0] == target
            ]
            self.assertEqual(hits, [blacksmith.GHOSH_ROW0_ADDRESS + {0x8001_64E0: 0, 0x8001_82E8: 4, 0x8001_8AB8: 8}[target]], hex(target))

    def test_spawn_record_keeps_everything_but_type_position_and_facing(self) -> None:
        record = blacksmith.build_spawn_record()
        self.assertEqual(len(record), 20)
        self.assertEqual(record[:4], blacksmith.GHOSH_TEMPLATE_ORIGINAL[:4])  # hdr 0x4000, flag 1
        self.assertEqual(record[4], blacksmith.SMITH_FACING)
        self.assertEqual(record[5:12], blacksmith.GHOSH_TEMPLATE_ORIGINAL[5:12])
        self.assertEqual(struct.unpack_from("<I", record, 12)[0], blacksmith.SMITH_ACTOR_TYPE)
        self.assertEqual(struct.unpack_from("<hh", record, 16), blacksmith.SMITH_POSITION)

    def test_getter_returns_the_script_address(self) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(blacksmith.SMITH_GETTER_ADDRESS, blacksmith.build_getter())
        cpu = mips_sim.Cpu(memory)
        self.assertEqual(cpu.run(blacksmith.SMITH_GETTER_ADDRESS, limit=10), blacksmith.SCRIPT_ADDRESS)

    def test_homes_are_inside_the_free_spans(self) -> None:
        natives = blacksmith.build_natives()
        pieces = [
            (blacksmith.NATIVE_BLOCK_ADDRESS, natives.code),
            (blacksmith.NATIVE_BLOCK_B_ADDRESS, natives.code_b),
            (blacksmith.HEADER_TEXT_ADDRESS, blacksmith.build_header_text()),
            (blacksmith.REFUSAL_TEXT_ADDRESS, blacksmith.build_refusal_text()),
            *blacksmith.build_script(natives),
        ]
        for address, body in pieces:
            span = next(
                (start, end)
                for start, end, _ in town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS
                if start <= address < end
            )
            self.assertLessEqual(address + len(body), span[1], hex(address))
        # no two pieces overlap, and none overlaps town_shop's own dialogue writes
        writes = {a for a, _, _ in town_shop.EQUIPMENT_DIALOGUE_RETARGETS} | set(
            town_shop.EQUIPMENT_GREETING_ENTRY_ADDRESSES
        )
        for index, (address, body) in enumerate(pieces):
            for other_address, other_body in pieces[index + 1 :]:
                self.assertTrue(
                    address + len(body) <= other_address or other_address + len(other_body) <= address,
                    (hex(address), hex(other_address)),
                )
            for write in writes:
                self.assertFalse(address <= write < address + len(body), hex(write))

    def test_slab_entry_points_are_the_jump_table(self) -> None:
        natives = blacksmith.build_natives()
        words = struct.unpack_from("<4I", natives.code)
        self.assertEqual(words[0], patch._j(0x02, natives.addresses["price"]))
        self.assertEqual(words[2], patch._j(0x02, natives.addresses["guard"]))
        self.assertEqual(blacksmith.NATIVE_BLOCK_ADDRESS, town_shop.SMITH_PRICE_ENTRY_ADDRESS)
        self.assertEqual(blacksmith.NATIVE_BLOCK_ADDRESS + 8, town_shop.SMITH_GUARD_ENTRY_ADDRESS)


class _Bench:
    """A RAM stand-in with a planted inventory, money and smith level, the
    game's routines the natives call stubbed, and a fake menu object."""

    MENU = 0x8010_0000          # menu+0x20 (what the A-press handler passes)
    LIST_WIDGET = 0x8010_1000
    SCRATCH = 0x8010_2000       # the row-builder stub records its a0 here

    def __init__(self, items, money: int, level: int, selected_row: int = 1, shield_level: int | None = None) -> None:
        self.memory = mips_sim.Memory()
        natives = blacksmith.build_natives()
        self.natives = natives
        self.memory.load_bytes(blacksmith.NATIVE_BLOCK_ADDRESS, natives.code)
        self.memory.load_bytes(blacksmith.NATIVE_BLOCK_B_ADDRESS, natives.code_b)
        # zero_bytes, the menu constructor and the row builder are the game's; stub them.
        self.memory.load_bytes(blacksmith.ZERO_BYTES_ADDRESS, struct.pack("<II", 0x03E0_0008, 0))
        self.memory.load_bytes(town_shop.MENU_CONSTRUCTOR_ADDRESS, struct.pack("<II", 0x03E0_0008, 0x2402_1234))  # v0 = 0x1234
        self.memory.load_bytes(
            blacksmith.ROW_BUILDER_ADDRESS,
            struct.pack("<4I", 0x3C01_8010, 0xAC24_2000, 0x03E0_0008, 0),  # lui at,0x8010 / sw a0,0x2000(at) / jr ra
        )
        for index, descriptor in enumerate(items):
            self.memory.load_bytes(0x8001_0248 + index * 4, bytes(descriptor))
            self.memory.write32(blacksmith.INVENTORY_ORDER_ADDRESS + index * 4, 0x8001_0248 + index * 4)
        self.memory.write32(blacksmith.INVENTORY_ORDER_ADDRESS + len(items) * 4, 0)
        self.memory.write32(blacksmith.TOWN_MONEY_ADDRESS, money)
        self.memory.write32(blacksmith.WEAPON_LEVEL_ADDRESS & ~3, 0)
        self.memory.write8(blacksmith.WEAPON_LEVEL_ADDRESS, level)
        self.memory.write8(blacksmith.SHIELD_LEVEL_ADDRESS, level if shield_level is None else shield_level)
        self.memory.write32(blacksmith.MENU_HEADER_POINTER_SLOT_ADDRESS, blacksmith.VANILLA_MENU_HEADER_TEXT_ADDRESS)
        self.memory.write32(blacksmith.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS, blacksmith.VANILLA_REFUSAL_MESSAGE_ADDRESS)
        # the fake menu: +4 selected row, +0x20 catalog, +0x28 list widget
        self.memory.write32(self.MENU + 4, selected_row)
        self.memory.write32(self.MENU + 0x20, blacksmith.CATALOG_BUFFER_ADDRESS)
        self.memory.write32(self.MENU + 0x28, self.LIST_WIDGET)

    def call(self, name: str, a0: int = 0) -> int:
        cpu = mips_sim.Cpu(self.memory)
        cpu.registers[4] = a0
        return cpu.run(self.natives.addresses[name], limit=200_000)

    def catalog(self) -> list[list[int]]:
        rows = []
        address = blacksmith.CATALOG_BUFFER_ADDRESS + 4
        while self.memory.read32(address):
            rows.append([self.memory.read8(address + i) for i in range(4)])
            address += 4
        return rows

    def descriptor(self, index: int) -> list[int]:
        return [self.memory.read8(0x8001_0248 + index * 4 + i) for i in range(4)]

    def slab_byte(self, offset: int) -> int:
        return self.memory.read8(town_shop.SHOP_CORE_ADDRESS + offset)


ITEMS = (
    (1, 0x0F, 3, 0x20),      # 0: sword +3, equipped
    (1, 0x11, 0xFF, 0x00),   # 1: shield -1
    (1, 0x10, 5, 0x00),      # 2: Wooden Wand +5: NOT temperable
    (2, 0x10, 44, 0x00),     # 3: Trained Wand +44 (over the cap already)
    (3, 0x02, 0, 0),         # 4: a herb: not equipment
    (1, 0x0F, 40, 0),        # 5: sword +40: at the cap
)


class TestBlacksmithNatives(unittest.TestCase):
    def test_is_upgradable(self) -> None:
        bench = _Bench(ITEMS, 0, 3)
        expected = (1, 1, 0, 1, 0, 1)
        for index, want in enumerate(expected):
            self.assertEqual(bench.call("is_upgradable", 0x8001_0248 + index * 4), want, index)

    def test_price_matches_the_formula(self) -> None:
        bench = _Bench((), 0, 3)
        for quality in (-3, -1, 0, 1, 2, 5, 9, 14, 19, 29, 39, 44):
            bench.memory.load_bytes(0x8010_3000, bytes((1, 0x0F, quality & 0xFF, 0)))
            self.assertEqual(bench.call("price", 0x8010_3000), blacksmith.temper_cost(quality), quality)
        self.assertEqual(blacksmith.temper_cost(39), 500)

    def test_caps_read_their_own_byte_clamp_and_use_the_table(self) -> None:
        for level, expected in ((0, 0), (1, 10), (2, 20), (3, 40), (9, 40)):
            bench = _Bench((), 0, level, shield_level=0)
            self.assertEqual(bench.call("cap_weapon"), expected, level)
            self.assertEqual(bench.call("cap_shield"), 0, level)
        bench = _Bench((), 0, 1, shield_level=3)
        self.assertEqual(bench.call("cap_weapon"), 10)
        self.assertEqual(bench.call("cap_shield"), 40)
        # cap_for picks by category: sword/wand -> weapon, shield -> shield
        bench.memory.load_bytes(0x8010_3000, bytes((1, 0x0F, 0, 0)))
        bench.memory.load_bytes(0x8010_3004, bytes((2, 0x10, 0, 0)))
        bench.memory.load_bytes(0x8010_3008, bytes((1, 0x11, 0, 0)))
        self.assertEqual(bench.call("cap_for", 0x8010_3000), 10)
        self.assertEqual(bench.call("cap_for", 0x8010_3004), 10)
        self.assertEqual(bench.call("cap_for", 0x8010_3008), 40)

    def test_has_equipment(self) -> None:
        self.assertEqual(_Bench(ITEMS, 0, 3).call("has_equipment"), 1)
        self.assertEqual(_Bench(((3, 0x02, 0, 0), (1, 0x10, 5, 0)), 0, 3).call("has_equipment"), 0)
        self.assertEqual(_Bench((), 0, 3).call("has_equipment"), 0)

    def test_open_menu_builds_the_catalog_and_dresses_the_menu(self) -> None:
        bench = _Bench(ITEMS, 1000, 3)
        self.assertEqual(bench.call("open"), 0x1234)
        self.assertEqual(bench.memory.read32(blacksmith.MENU_HANDLE_ADDRESS), 0x1234)
        self.assertEqual(bench.memory.read32(blacksmith.CATALOG_BUFFER_ADDRESS), blacksmith.CATALOG_LEADING_ENTRY)
        self.assertEqual(
            bench.catalog(),
            [[1, 0x0F, 3, 0], [1, 0x11, 0xFF, 0], [2, 0x10, 44, 0x80], [1, 0x0F, 40, 0x80]],
        )
        # separate caps: weapon level 0 greys every sword and the wand, the shield stays open
        bench = _Bench(ITEMS, 1000, 0, shield_level=1)
        bench.call("open")
        self.assertEqual(
            bench.catalog(),
            [[1, 0x0F, 3, 0x80], [1, 0x11, 0xFF, 0], [2, 0x10, 44, 0x80], [1, 0x0F, 40, 0x80]],
        )
        bench = _Bench(ITEMS, 1000, 3)
        bench.call("open")
        self.assertEqual(bench.slab_byte(town_shop.ACTIVE_SHOP_OFFSET), town_shop.SMITH_MENU_SHOP_MARKER)
        self.assertEqual(bench.slab_byte(town_shop.ARMED_MENU_OFFSET), 1)
        self.assertEqual(bench.memory.read32(blacksmith.MENU_HEADER_POINTER_SLOT_ADDRESS), blacksmith.HEADER_TEXT_ADDRESS)
        self.assertEqual(bench.memory.read32(blacksmith.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS), blacksmith.REFUSAL_TEXT_ADDRESS)
        # after_menu puts everything back
        self.assertEqual(bench.call("after_menu"), 0)
        self.assertEqual(bench.memory.read32(blacksmith.MENU_HEADER_POINTER_SLOT_ADDRESS), blacksmith.VANILLA_MENU_HEADER_TEXT_ADDRESS)
        self.assertEqual(bench.memory.read32(blacksmith.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS), blacksmith.VANILLA_REFUSAL_MESSAGE_ADDRESS)
        self.assertEqual(bench.slab_byte(town_shop.ACTIVE_SHOP_OFFSET), 0xFF)

    def test_guard_purchases_the_selected_row(self) -> None:
        bench = _Bench(ITEMS, 1000, 3, selected_row=1)   # catalog row 1 = the equipped sword +3
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.memory.read32(blacksmith.TOWN_MONEY_ADDRESS), 1000 - blacksmith.temper_cost(3))
        self.assertEqual(bench.descriptor(0), [1, 0x0F, 4, 0x20])       # +1, still equipped
        self.assertEqual(bench.catalog()[0], [1, 0x0F, 4, 0x20])       # row bumped, 0x20 pre-set for the toggle
        self.assertEqual(bench.memory.read32(_Bench.SCRATCH), _Bench.LIST_WIDGET + 0x20)  # rows rebuilt

    def test_guard_lockstep_skips_herbs_and_plain_wands(self) -> None:
        bench = _Bench(ITEMS, 1000, 3, selected_row=2)   # catalog row 2 = the shield -1 (index 1 in the bag)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.descriptor(1)[2], 0)                    # -1 -> 0
        self.assertEqual(bench.descriptor(2)[2], 5)                    # the Wooden Wand untouched
        self.assertEqual(bench.memory.read32(blacksmith.TOWN_MONEY_ADDRESS), 1000 - blacksmith.temper_cost(-1))

    def test_guard_marks_the_row_unselectable_at_the_cap(self) -> None:
        # 0xA0, not 0x80. The A-press handler XORs 0x20 after the guard returns
        # and recolours from the result (`recolour_shop_row`, 0x800B09EC): 0x20
        # set is CHECKED, which paints the row green and stamps the BUY tag on
        # it. X tempers on the spot, so a selection highlight is a lie - the
        # guard leaves 0x20 SET on every path so the toggle takes it off, and
        # the row that just hit its cap settles on 0x80: grey, unselectable.
        items = ((1, 0x0F, 39, 0),)
        bench = _Bench(items, 1000, 3, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.descriptor(0)[2], 40)
        self.assertEqual(bench.catalog()[0], [1, 0x0F, 40, 0xA0])
        self.assertEqual(bench.catalog()[0][3] ^ 0x20, 0x80)           # what vanilla leaves
        # a shield uses the SHIELD cap: level 1 -> +10
        items = ((1, 0x11, 9, 0),)
        bench = _Bench(items, 1000, 3, selected_row=1, shield_level=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.catalog()[0], [1, 0x11, 10, 0xA0])

    def test_the_row_never_ends_up_looking_selected(self) -> None:
        """Both ways out of a successful temper leave 0x20 set, so vanilla's
        toggle clears it and nothing goes green with a BUY sticker on it."""

        for quality, cap_level, what in ((10, 3, "an ordinary temper"), (39, 3, "the one that caps")):
            with self.subTest(what):
                bench = _Bench(((1, 0x0F, quality, 0),), 50_000, cap_level, selected_row=1)
                bench.call("open")
                self.assertEqual(bench.call("guard", _Bench.MENU), 1, what)
                flags = bench.catalog()[0][3]
                self.assertEqual(flags & 0x20, 0x20, f"{what}: 0x20 was not left set")
                self.assertEqual((flags ^ 0x20) & 0x20, 0, f"{what}: settles checked")

    def test_guard_refuses_without_gold_and_changes_nothing(self) -> None:
        bench = _Bench(ITEMS, 0, 3, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(bench.memory.read32(blacksmith.TOWN_MONEY_ADDRESS), 0)
        self.assertEqual(bench.descriptor(0), [1, 0x0F, 3, 0x20])
        self.assertEqual(bench.catalog()[0], [1, 0x0F, 3, 0])
        self.assertEqual(bench.memory.read32(_Bench.SCRATCH), 0)       # no rebuild


class TestBlacksmithScript(unittest.TestCase):
    def test_script_shape(self) -> None:
        natives = blacksmith.build_natives()
        segments = dict(blacksmith.build_script(natives))
        first = segments[blacksmith.SCRIPT_ADDRESS]
        # opens on the two native tests, then the smith's presentation byte; no clear before the first window
        self.assertEqual(first[0], 0x4C)
        self.assertEqual(struct.unpack_from("<I", first, 1)[0], natives.addresses["has_equipment"])
        self.assertEqual(first[5:7], b"\x3e\x0f")
        self.assertEqual(first[11:13], bytes((0x57, blacksmith.SMITH_ACTOR_SLOT)))
        # the caps line: two natives each followed by FD 0F
        for name in ("cap_weapon", "cap_shield"):
            call = first.find(b"\x4c" + struct.pack("<I", natives.addresses[name]))
            self.assertGreater(call, 0, name)
            self.assertEqual(first[call + 5 : call + 7], b"\xfd\x0f", name)
        # the menu page: clear, call the opener, after_menu, silent close
        pick = first.find(b"\x08\x15")
        self.assertGreater(pick, 0)
        opener = struct.unpack_from("<I", first, pick + 2)[0]
        self.assertEqual(first[pick + 6], 0x4C)
        self.assertEqual(struct.unpack_from("<I", first, pick + 7)[0], natives.addresses["after_menu"])
        self.assertEqual(first[pick + 11 : pick + 13], b"\x01\x01")
        # the opener stub is the six-opcode protocol, in the second segment
        second = segments[blacksmith.SCRIPT_B_ADDRESS]
        stub_offset = opener - blacksmith.SCRIPT_B_ADDRESS
        stub = second[stub_offset:]
        self.assertEqual(stub[0], 0x30)
        self.assertEqual(stub[1:3], b"\x34\x0e")
        self.assertEqual(stub[7], 0x4C)
        self.assertEqual(struct.unpack_from("<I", stub, 8)[0], natives.addresses["open"])
        self.assertEqual(stub[12:14], b"\x3e\x0f")
        self.assertEqual(struct.unpack_from("<I", stub, 14)[0], opener)                 # retry -> self
        self.assertEqual(struct.unpack_from("<I", stub, 3)[0], opener + len(stub) - 1)  # resume -> its own 0x16
        self.assertEqual(stub[-2:], b"\x23\x16")
        for body in segments.values():
            self.assertNotEqual(body[-1], 0)

    def test_records_cover_all_three_homes(self) -> None:
        offsets = {offset for offset, _ in blacksmith.iter_overlay_file_patches()}
        self.assertEqual(
            offsets,
            {
                town_shop.equipment_runtime_to_file_offset(a)
                for a in (blacksmith.GHOSH_ROW0_ADDRESS, blacksmith.GHOSH_TEMPLATE_ADDRESS, blacksmith.SMITH_GETTER_ADDRESS)
            },
        )
        dialogue = dict(blacksmith.iter_dialogue_file_patches())
        for address in (
            blacksmith.NATIVE_BLOCK_ADDRESS,
            blacksmith.NATIVE_BLOCK_B_ADDRESS,
            blacksmith.HEADER_TEXT_ADDRESS,
            blacksmith.REFUSAL_TEXT_ADDRESS,
            blacksmith.SCRIPT_ADDRESS,
            blacksmith.SCRIPT_B_ADDRESS,
        ):
            self.assertIn(town_shop.equipment_dialogue_runtime_to_file_offset(address), dialogue)
        ppf = bytearray()
        blacksmith.append_blacksmith_ppf_records(ppf)
        self.assertGreater(len(ppf), 600)

    def test_temper_level_bytes_sit_in_the_zeroed_intro_word(self) -> None:
        self.assertEqual(patch.PERSISTENT_WEAPON_TEMPER_LEVEL_OFFSET, 0x56)
        self.assertEqual(patch.PERSISTENT_SHIELD_TEMPER_LEVEL_OFFSET, 0x57)
        for offset in (patch.PERSISTENT_WEAPON_TEMPER_LEVEL_OFFSET, patch.PERSISTENT_SHIELD_TEMPER_LEVEL_OFFSET):
            self.assertIn(offset & ~3, patch.PERSISTENT_ZEROED_WORD_OFFSETS)
            self.assertLess(offset, patch.PERSISTENT_STATE_SIZE)
            self.assertNotIn(offset, (patch.PERSISTENT_INTRO_RESTORE_MARKER_OFFSET, patch.PERSISTENT_INTRO_FIRST_RUN_READY_OFFSET))


if __name__ == "__main__":
    unittest.main()
