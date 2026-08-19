"""The fortune teller (fortune_teller.py): the scene pinned against the disc,
the natives run in the simulator over a planted journal, the script's shape,
the class mapping, and the record set.

Ground truth is `docs/systems/fortune-teller.md`; the disc bytes are pinned
against `extracted/TOWN.BIN` when it is present.
"""

from __future__ import annotations

import struct
import unittest
from dataclasses import dataclass
from pathlib import Path

from .. import fortune_teller as ft
from .. import item_manifest, items, patch
from . import mips_sim

_TOWN_BIN = Path(__file__).resolve().parents[4] / "extracted" / "TOWN.BIN"


class TestFortuneTellerScene(unittest.TestCase):
    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_her_entry_point_holds_the_vanilla_greeting(self) -> None:
        town = _TOWN_BIN.read_bytes()
        offset = ft.dialogue_runtime_to_file_offset(ft.SCRIPT_ENTRY_ADDRESS)
        expected = ft._text(ft.VANILLA_ENTRY_TEXT)
        self.assertEqual(town[offset : offset + len(expected)], expected)

    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_the_overlay_routes_her_slot_to_the_entry_point(self) -> None:
        town = _TOWN_BIN.read_bytes()
        overlay = 0x62_9000
        # dialogue table row 0 at overlay 0x80017408: (key 0x4F, state 0, script)
        key, state, script = struct.unpack_from("<HHI", town, overlay + 0x1408)
        self.assertEqual((key, state, script), (ft.TELLER_ACTOR_SLOT, 0, ft.SCRIPT_ENTRY_ADDRESS))
        # and only that one row: the next word pair is the variant row array
        self.assertEqual(struct.unpack_from("<I", town, overlay + 0x1410)[0], 0x8001_60F8)
        # her template: type 0x6A at (512, 280), facing 1, presence 1
        record = town[overlay + (ft.TELLER_TEMPLATE_ADDRESS - 0x8001_6000) :][:20]
        self.assertEqual(record[:4], bytes.fromhex("00600100"))
        self.assertEqual(record[4], 1)
        self.assertEqual(struct.unpack_from("<I", record, 12)[0], ft.TELLER_ACTOR_TYPE)
        self.assertEqual(struct.unpack_from("<hh", record, 16), ft.TELLER_POSITION)
        # the prop after her carries her slot in its +4 word, then a terminator, then a spare terminator
        prop = town[overlay + (ft.TELLER_TEMPLATE_ADDRESS + 20 - 0x8001_6000) :][:20]
        self.assertEqual(struct.unpack_from("<I", prop, 4)[0] >> 24, ft.TELLER_ACTOR_SLOT)
        for extra in (40, 60):
            terminator = town[overlay + (ft.TELLER_TEMPLATE_ADDRESS + extra - 0x8001_6000) :][:20]
            self.assertEqual(terminator[:2], b"\x00\x80", extra)

    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_the_scratch_tail_of_the_image_is_zero(self) -> None:
        town = _TOWN_BIN.read_bytes()
        start = ft.dialogue_runtime_to_file_offset(ft.DIALOGUE_SCRATCH_ADDRESS)
        end = ft.DIALOGUE_FILE_OFFSET + (ft.DIALOGUE_RUNTIME_END - ft.DIALOGUE_RUNTIME_ADDRESS)
        self.assertEqual(town[start:end], bytes(end - start))

    def test_layout_fits_and_is_ordered(self) -> None:
        layout = ft.build_layout()
        self.assertEqual(layout.entry_stub_address, ft.SCRIPT_ENTRY_ADDRESS)
        self.assertLess(layout.entry_stub_address + 5, layout.state_address)
        self.assertEqual(layout.state_address % 16, 0)
        self.assertEqual(layout.natives_address, layout.state_address + ft.STATE_SIZE)
        self.assertGreaterEqual(layout.page_table_address, layout.natives_address + len(layout.natives))
        self.assertEqual(layout.page_table_address % 4, 0)
        self.assertEqual(layout.class_table_address, layout.page_table_address + ft.HINT_CLASS_COUNT * 4)
        self.assertGreaterEqual(layout.script_address, layout.class_table_address + ft.LOCATION_COUNT)
        self.assertLessEqual(layout.end, ft.REGION_END)
        self.assertLess(layout.end - ft.REGION_START, 8 * 1024)   # a fraction of the 43 KB image

    def test_every_line_fits_the_window(self) -> None:
        for lines in (ft.GREETING, ft.PRICE_PAGE, ft.READ_PAGE, ft.DONE_PAGE, ft.POOR_PAGE,
                      ft.NO_MORE_PAGE, ft.NOTHING_PAGE, ft.BYE_PAGE, (ft.WHERE_GROUND, ft.WHERE_CARRIER)):
            self.assertLessEqual(len(lines), 3)
            for line in lines:
                self.assertLessEqual(ft._cells(line), ft.LINE_LIMIT, line)
        for hint_class, lines in ft.CLASS_LINES.items():
            self.assertEqual(len(lines), 2, hint_class)
            for line in lines:
                self.assertLessEqual(ft._cells(line), ft.LINE_LIMIT, line)
        # the offer row with a two-digit floor
        self.assertLessEqual(ft._cells(ft.OFFER_LINE + "39" + ft.OFFER_TAIL), ft.LINE_LIMIT)
        self.assertLessEqual(ft._cells(ft.READ_PAGE[1] + "39" + ft.READ_TAIL), ft.LINE_LIMIT)

    def test_text_uses_the_games_apostrophe_and_ellipsis(self) -> None:
        self.assertEqual(ft._text("'"), b"\x81\x66")
        self.assertEqual(ft._text("..."), b"\x81\x63")
        self.assertEqual(ft._text("a b"), b"\x82\x81\x81\x40\x82\x82")
        self.assertEqual(ft._cells("it's..."), 5)


class _Bench:
    """RAM with a planted ADSV journal, money and class table, the natives and
    the page table loaded where the layout puts them."""

    def __init__(self, journal: bytes = b"", money: int = 0, classes: bytes | None = None, magic: bool = True) -> None:
        # The layout is built over a valid table; the planted one may carry an
        # out-of-range byte on purpose (the generator refuses those, the native
        # must not trust the disc anyway).
        self.classes = classes or bytes(ft.LOCATION_COUNT)
        self.layout = ft.build_layout(bytes(min(value, ft.HINT_CLASS_COUNT - 1) for value in self.classes))
        self.memory = mips_sim.Memory()
        self.memory.load_bytes(self.layout.natives_address, self.layout.natives)
        self.memory.load_bytes(self.layout.page_table_address, ft.build_page_table(self.layout))
        self.memory.load_bytes(self.layout.class_table_address, self.classes)
        self.memory.load_bytes(self.layout.state_address, bytes(ft.STATE_SIZE))
        if magic:
            self.memory.write32(ft.ADSV_MAGIC_ADDRESS, ft.ADSV_MAGIC)
        self.memory.load_bytes(ft.JOURNAL_ADDRESS, journal.ljust(patch.PERSISTENT_TOWER_MASK_BYTES, b"\0"))
        self.memory.write32(ft.TOWN_MONEY_ADDRESS, money)

    def call(self, name: str) -> int:
        cpu = mips_sim.Cpu(self.memory)
        return cpu.run(self.layout.native_addresses[name], limit=50_000)

    def state(self, offset: int) -> int:
        return self.memory.read8(self.layout.state_address + offset)

    def candidates(self) -> list[int]:
        out = []
        for index in range(ft.HINT_FLOOR_CHOICES + 1):
            value = self.state(ft.STATE_CANDIDATES + index)
            if not value:
                break
            out.append(value)
        return out

    def money(self) -> int:
        return self.memory.read32(ft.TOWN_MONEY_ADDRESS)

    def page(self, name: str) -> int:
        return self.layout.page_addresses[name]


class TestFortuneTellerNatives(unittest.TestCase):
    def test_scan_offers_the_lowest_floors_with_anything_left(self) -> None:
        bench = _Bench()
        self.assertEqual(bench.call("scan"), 3)
        self.assertEqual(bench.candidates(), [1, 2, 3])
        self.assertEqual(bench.state(ft.STATE_CURSOR), 0)
        # floors 1 and 2 done, 3 partly, 4 done, 5 untouched -> 3, 5, 6
        bench = _Bench(bytes((7, 7, 1, 7, 0, 0)))
        self.assertEqual(bench.call("scan"), 3)
        self.assertEqual(bench.candidates(), [3, 5, 6])
        # only two floors left in the whole tower
        journal = bytearray(b"\x07" * 39)
        journal[10] = 6
        journal[38] = 3
        bench = _Bench(bytes(journal))
        self.assertEqual(bench.call("scan"), 2)
        self.assertEqual(bench.candidates(), [11, 39])
        # everything collected -> nothing; the spare byte 39 never counts
        journal = bytearray(b"\x07" * 39) + b"\x00"
        bench = _Bench(bytes(journal))
        self.assertEqual(bench.call("scan"), 0)
        self.assertEqual(bench.candidates(), [])
        # bits above the slot count do not make a floor "done" or "open"
        bench = _Bench(bytes((0xF8, 0xFF)))
        self.assertEqual(bench.call("scan"), 3)
        self.assertEqual(bench.candidates(), [1, 3, 4])

    def test_scan_refuses_without_the_journal_magic(self) -> None:
        bench = _Bench(magic=False)
        self.assertEqual(bench.call("scan"), 0)

    def test_next_walks_the_candidates_then_returns_zero(self) -> None:
        bench = _Bench(bytes((7, 7)))
        bench.call("scan")
        self.assertEqual(bench.call("next"), 3)
        self.assertEqual(bench.state(ft.STATE_CHOSEN), 3)
        self.assertEqual(bench.call("next"), 4)
        self.assertEqual(bench.call("next"), 5)
        self.assertEqual(bench.call("next"), 0)
        self.assertEqual(bench.call("next"), 0)
        self.assertEqual(bench.state(ft.STATE_CHOSEN), 5)      # the last real offer stays chosen
        # a short list stops at its terminator
        journal = bytearray(b"\x07" * 39)
        journal[20] = 0
        bench = _Bench(bytes(journal))
        self.assertEqual(bench.call("scan"), 1)
        self.assertEqual(bench.call("next"), 21)
        self.assertEqual(bench.call("next"), 0)

    def test_pay_debits_exactly_the_price_or_refuses(self) -> None:
        bench = _Bench(money=ft.HINT_PRICE - 1)
        bench.call("scan")
        bench.call("next")
        self.assertEqual(bench.call("pay"), 0)
        self.assertEqual(bench.money(), ft.HINT_PRICE - 1)
        bench = _Bench(money=ft.HINT_PRICE + 5)
        bench.call("scan")
        bench.call("next")
        bench.call("next")
        self.assertEqual(bench.call("pay"), 2)                     # returns the chosen floor
        self.assertEqual(bench.money(), 5)
        self.assertEqual(bench.state(ft.STATE_SLOT_CURSOR), 0)
        # exact change is enough
        bench = _Bench(money=ft.HINT_PRICE)
        bench.call("scan")
        bench.call("next")
        self.assertEqual(bench.call("pay"), 1)
        self.assertEqual(bench.money(), 0)

    def test_where_walks_the_open_slots_and_tells_the_carrier_apart(self) -> None:
        # floor 1: nothing collected -> ground, ground, carrier, done
        bench = _Bench(money=ft.HINT_PRICE)
        bench.call("scan")
        bench.call("next")
        bench.call("pay")
        self.assertEqual(bench.call("where"), bench.page("where_ground"))
        self.assertEqual(bench.state(ft.STATE_CURRENT_SLOT), 0)
        self.assertEqual(bench.call("where"), bench.page("where_ground"))
        self.assertEqual(bench.state(ft.STATE_CURRENT_SLOT), 1)
        self.assertEqual(bench.call("where"), bench.page("where_carrier"))
        self.assertEqual(bench.state(ft.STATE_CURRENT_SLOT), 2)
        self.assertEqual(bench.call("where"), bench.page("done"))
        self.assertEqual(bench.call("where"), bench.page("done"))
        # floor 2 with slots 0 and 2 collected -> only slot 1
        bench = _Bench(bytes((7, 5)), money=ft.HINT_PRICE)
        bench.call("scan")
        bench.call("next")
        bench.call("pay")
        self.assertEqual(bench.state(ft.STATE_CHOSEN), 2)
        self.assertEqual(bench.call("where"), bench.page("where_ground"))
        self.assertEqual(bench.state(ft.STATE_CURRENT_SLOT), 1)
        self.assertEqual(bench.call("where"), bench.page("done"))
        # floor 39 (the last journal byte) with only the carrier left
        journal = bytearray(b"\x07" * 39)
        journal[38] = 3
        bench = _Bench(bytes(journal), money=ft.HINT_PRICE)
        bench.call("scan")
        bench.call("next")
        bench.call("pay")
        self.assertEqual(bench.state(ft.STATE_CHOSEN), 39)
        self.assertEqual(bench.call("where"), bench.page("where_carrier"))
        self.assertEqual(bench.call("where"), bench.page("done"))

    def test_what_reads_the_class_table_by_location_index(self) -> None:
        classes = bytearray(ft.LOCATION_COUNT)
        classes[0] = ft.HINT_CLASS_HERB              # floor 1 slot 0
        classes[1] = ft.HINT_CLASS_BALL              # floor 1 slot 1
        classes[2] = ft.HINT_CLASS_REMOTE            # floor 1 slot 2
        classes[(12 - 1) * 3 + 1] = ft.HINT_CLASS_KEYCARD
        classes[(39 - 1) * 3 + 2] = ft.HINT_CLASS_EGG
        classes[(2 - 1) * 3 + 0] = 0xFF              # out of range -> unknown
        bench = _Bench(money=ft.HINT_PRICE * 3, classes=bytes(classes))
        bench.call("scan")
        bench.call("next")
        bench.call("pay")
        for expected in ("class_1", "class_4", "class_22"):
            bench.call("where")
            self.assertEqual(bench.call("what"), bench.page(expected))
        # floor 2 slot 0 is the out-of-range byte
        bench.call("next")
        bench.call("pay")
        bench.call("where")
        self.assertEqual(bench.call("what"), bench.page("class_0"))
        # direct: floor 12 slot 1 and floor 39 slot 2 through the state block
        bench.memory.write8(bench.layout.state_address + ft.STATE_CHOSEN, 12)
        bench.memory.write8(bench.layout.state_address + ft.STATE_CURRENT_SLOT, 1)
        self.assertEqual(bench.call("what"), bench.page("class_18"))
        bench.memory.write8(bench.layout.state_address + ft.STATE_CHOSEN, 39)
        bench.memory.write8(bench.layout.state_address + ft.STATE_CURRENT_SLOT, 2)
        self.assertEqual(bench.call("what"), bench.page("class_17"))


class TestFortuneTellerScript(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = ft.build_layout()
        self.script = self.layout.script
        self.pages = self.layout.page_addresses
        self.natives = self.layout.native_addresses

    def at(self, name: str) -> int:
        return self.pages[name] - self.layout.script_address

    def test_entry_stub_jumps_to_main(self) -> None:
        stub = ft.build_entry_stub(self.layout)
        self.assertEqual(stub[0], 0x17)
        self.assertEqual(struct.unpack_from("<I", stub, 1)[0], self.pages["main"])

    def test_main_opens_with_text_scans_and_prices(self) -> None:
        main = self.script[self.at("main") :]
        self.assertGreaterEqual(main[0], 0x80)                    # text first, no clear before the first window
        wait = main.find(b"\x11")
        self.assertEqual(main[wait + 1], 0x4C)
        self.assertEqual(struct.unpack_from("<I", main, wait + 2)[0], self.natives["scan"])
        self.assertEqual(main[wait + 6 : wait + 8], b"\x3e\x0f")
        self.assertEqual(struct.unpack_from("<I", main, wait + 8)[0], self.pages["nothing"])
        self.assertEqual(main[wait + 12 : wait + 15], bytes((0x08, 0x57, ft.TELLER_ACTOR_SLOT)))

    def test_offer_page_shape(self) -> None:
        offer = self.script[self.at("offer") : self.at("read")]
        self.assertEqual(offer[0:2], b"\x08\x4c")
        self.assertEqual(struct.unpack_from("<I", offer, 2)[0], self.natives["next"])
        self.assertEqual(offer[6:8], b"\x3e\x0f")
        self.assertEqual(struct.unpack_from("<I", offer, 8)[0], self.pages["no_more"])
        line = ft._text(ft.OFFER_LINE) + b"\xfd\x0f" + ft._text(ft.OFFER_TAIL) + b"\x0a"
        self.assertEqual(offer[12 : 12 + len(line)], line)
        rows = offer[12 + len(line) :]
        expected_rows = (
            b"\x0b\x81\x6d" + ft._text(ft.CHOICE_LOOK) + b"\x81\x6e"
            + ft._choice_gap(ft.CHOICE_LOOK)
            + b"\x81\x6d" + ft._text(ft.CHOICE_HIGHER) + b"\x81\x6e"
            + b"\x0a\x0b\x81\x6d" + ft._text(ft.CHOICE_NOT_NOW) + b"\x81\x6e"
            + b"\x2c\x03\x1a"
        )
        self.assertEqual(rows[: len(expected_rows)], expected_rows)
        targets = struct.unpack_from("<3I", rows, len(expected_rows))
        self.assertEqual(targets, (self.pages["read"], self.pages["offer"], self.pages["bye"]))
        self.assertEqual(len(rows), len(expected_rows) + 12)
        # [Look.] is 7 cells: nine full-width spaces of gap
        self.assertEqual(ft._choice_gap(ft.CHOICE_LOOK), b"\x81\x40" * 9)

    def test_read_pays_first_then_loops_through_where_and_what(self) -> None:
        read = self.script[self.at("read") : self.at("loop")]
        self.assertEqual(read[0], 0x4C)
        self.assertEqual(struct.unpack_from("<I", read, 1)[0], self.natives["pay"])
        self.assertEqual(read[5:7], b"\x3e\x0f")
        self.assertEqual(struct.unpack_from("<I", read, 7)[0], self.pages["poor"])
        self.assertEqual(read[11:14], bytes((0x08, 0x57, ft.TELLER_ACTOR_SLOT)))
        self.assertIn(b"\xfd\x0f", read)
        self.assertEqual(read[-1], 0x11)
        loop = self.script[self.at("loop") : self.at("where_ground")]
        self.assertEqual(loop, b"\x4c" + struct.pack("<I", self.natives["where"]) + b"\x48\x0f")
        for name, line in (("where_ground", ft.WHERE_GROUND), ("where_carrier", ft.WHERE_CARRIER)):
            page = self.script[self.at(name) :]
            head = bytes((0x08, 0x57, ft.TELLER_ACTOR_SLOT)) + ft._text(line) + b"\x0a\x4c"
            self.assertEqual(page[: len(head)], head)
            self.assertEqual(struct.unpack_from("<I", page, len(head))[0], self.natives["what"])
            self.assertEqual(page[len(head) + 4 : len(head) + 6], b"\x48\x0f")

    def test_class_pages_continue_the_where_page_and_return_to_the_loop(self) -> None:
        for hint_class in range(ft.HINT_CLASS_COUNT):
            page = self.script[self.at(f"class_{hint_class}") :]
            first, second = ft.CLASS_LINES[hint_class]
            body = ft._text(first) + b"\x0a" + ft._text(second) + b"\x11\x17"
            self.assertEqual(page[: len(body)], body, hint_class)
            self.assertEqual(struct.unpack_from("<I", page, len(body))[0], self.pages["loop"])
            self.assertNotEqual(page[0], 0x08)      # no clear: it shares the where page
        table = ft.build_page_table(self.layout)
        self.assertEqual(len(table), ft.HINT_CLASS_COUNT * 4)
        self.assertEqual(struct.unpack_from("<I", table, 4 * ft.HINT_CLASS_BALL)[0], self.pages["class_4"])

    def test_closing_pages_clear_wait_and_end_twice(self) -> None:
        for name in ("done", "poor", "no_more", "nothing", "bye"):
            page = self.script[self.at(name) :]
            self.assertEqual(page[:3], bytes((0x08, 0x57, ft.TELLER_ACTOR_SLOT)), name)
            end = page.find(b"\x11\x01\x01")
            self.assertGreater(end, 3, name)
        self.assertEqual(self.script[-3:], b"\x11\x01\x01")
        self.assertNotEqual(self.script[-1], 0)

    def test_every_reference_lands_inside_the_region(self) -> None:
        for name, address in self.pages.items():
            self.assertTrue(self.layout.script_address <= address < self.layout.end, name)
        for name, address in self.natives.items():
            self.assertTrue(self.layout.natives_address <= address < self.layout.page_table_address, name)


@dataclass
class _FakeItem:
    name: str
    player: int
    advancement: bool = False
    trap: bool = False


class TestHintClasses(unittest.TestCase):
    def test_own_items_by_name(self) -> None:
        cases = {
            "Antidote Herb": ft.HINT_CLASS_HERB,
            items.PROGRESSIVE_KEYCARD: ft.HINT_CLASS_KEYCARD,
            items.GOLD_PACKAGE: ft.HINT_CLASS_GOLD,
            items.SEND_TOKEN: ft.HINT_CLASS_SEND_TOKEN,
            items.RED_SAND: ft.HINT_CLASS_RED_SAND,
            items.BLUE_SAND: ft.HINT_CLASS_BLUE_SAND,
            "White Sand": ft.HINT_CLASS_WHITE_SAND,
            "Bomb Trap": ft.HINT_CLASS_TRAP,
            "Not An Item": ft.HINT_CLASS_UNKNOWN,
        }
        for name, expected in cases.items():
            if name != "Not An Item":
                self.assertTrue(name in item_manifest.REWARD_BY_NAME or name in items.ITEM_NAME_TO_ID, name)
            self.assertEqual(ft.hint_class_for_item(_FakeItem(name, 1), 1), expected, name)
        # every native reward maps to a real class, never UNKNOWN
        by_category: dict[int, set[int]] = {}
        for reward in item_manifest.NATIVE_REWARDS:
            hint_class = ft.hint_class_for_own_item_name(reward.name)
            self.assertNotEqual(hint_class, ft.HINT_CLASS_UNKNOWN, reward.name)
            by_category.setdefault(reward.category, set()).add(hint_class)
        for category, classes in by_category.items():
            if category == item_manifest.SAND_CATEGORY:
                self.assertEqual(classes, {ft.HINT_CLASS_RED_SAND, ft.HINT_CLASS_BLUE_SAND, ft.HINT_CLASS_WHITE_SAND})
            else:
                self.assertEqual(len(classes), 1, category)
        self.assertEqual(ft.hint_class_for_own_item_name("Fire Ball (10)"), ft.HINT_CLASS_BALL)

    def test_other_players_items_by_classification(self) -> None:
        self.assertEqual(ft.hint_class_for_item(_FakeItem("Sword", 2), 1), ft.HINT_CLASS_REMOTE)
        self.assertEqual(ft.hint_class_for_item(_FakeItem("Sword", 2, advancement=True), 1), ft.HINT_CLASS_REMOTE_PROGRESSION)
        self.assertEqual(ft.hint_class_for_item(_FakeItem("Sword", 2, trap=True), 1), ft.HINT_CLASS_REMOTE_TRAP)
        # another player's item that happens to share our name is still theirs
        self.assertEqual(ft.hint_class_for_item(_FakeItem(items.PROGRESSIVE_KEYCARD, 2, advancement=True), 1), ft.HINT_CLASS_REMOTE_PROGRESSION)


class TestFortuneTellerRecords(unittest.TestCase):
    def test_records_cover_every_piece_inside_the_image(self) -> None:
        classes = [index % ft.HINT_CLASS_COUNT for index in range(ft.LOCATION_COUNT)]
        pieces = ft.iter_dialogue_file_patches(classes)
        self.assertEqual(len(pieces), 14)      # 7 hers, 7 the ball charger's (the strip lives in the town image now)
        for offset, body in pieces:
            self.assertGreaterEqual(offset, ft.dialogue_runtime_to_file_offset(ft.REGION_START))
            self.assertLessEqual(offset + len(body), ft.dialogue_runtime_to_file_offset(ft.REGION_END))
        # the class table is the bytes we passed
        layout = ft.build_layout(bytes(classes))
        table_offset = ft.dialogue_runtime_to_file_offset(layout.class_table_address)
        self.assertIn((table_offset, bytes(classes)), pieces)
        # no two pieces overlap
        ordered = sorted(pieces)
        for (a, body), (b, _) in zip(ordered, ordered[1:]):
            self.assertLessEqual(a + len(body), b)
        ppf = bytearray()
        ft.append_fortune_teller_ppf_records(ppf, classes)
        self.assertGreater(len(ppf), 4000)

    def test_bad_tables_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            ft.build_class_table([0] * (ft.LOCATION_COUNT - 1))
        with self.assertRaises(ValueError):
            ft.build_layout(bytes([ft.HINT_CLASS_COUNT] * ft.LOCATION_COUNT))


if __name__ == "__main__":
    unittest.main()


class TestYamlOptionGating(unittest.TestCase):
    """`hint_system` / `temper_system` (options.py): what each combination writes."""

    CLASSES = [0] * ft.LOCATION_COUNT

    def test_both_off_writes_nothing_at_all(self) -> None:
        self.assertEqual(ft.iter_dialogue_file_patches(self.CLASSES, hints=False, charger=False), ())
        self.assertEqual(ft.iter_overlay_file_patches(charger=False), ())
        self.assertEqual(ft.iter_fortune_teller_raw_patches(self.CLASSES, hints=False, charger=False), ())

    def test_hints_only_leaves_the_overlay_and_the_charger_alone(self) -> None:
        pieces = dict(ft.iter_dialogue_file_patches(self.CLASSES, hints=True, charger=False))
        layout = ft.build_layout()
        self.assertIn(ft.dialogue_runtime_to_file_offset(layout.script_address), pieces)
        self.assertIn(ft.dialogue_runtime_to_file_offset(layout.class_table_address), pieces)
        self.assertNotIn(ft.dialogue_runtime_to_file_offset(layout.dialogue_table_address), pieces)
        self.assertNotIn(ft.dialogue_runtime_to_file_offset(layout.charger_jump_table_address), pieces)
        self.assertEqual(ft.iter_overlay_file_patches(charger=False), ())
        # the vanilla table row still routes her slot to the entry stub
        self.assertIn(ft.dialogue_runtime_to_file_offset(ft.SCRIPT_ENTRY_ADDRESS), pieces)

    def test_charger_only_closes_her_quiz_with_one_page(self) -> None:
        pieces = dict(ft.iter_dialogue_file_patches(self.CLASSES, hints=False, charger=True))
        layout = ft.build_layout(hints=False)
        script = pieces[ft.dialogue_runtime_to_file_offset(layout.script_address)]
        self.assertGreaterEqual(script[0], 0x80)                # text first, no clear
        self.assertEqual(script[-3:], b"\x11\x01\x01")
        self.assertIn(ft._text(ft.CLOSED_PAGE[0]), script)
        self.assertLess(len(script), 120)
        # the entry stub jumps to it, the dialogue table names it, the charger is present
        stub = pieces[ft.dialogue_runtime_to_file_offset(ft.SCRIPT_ENTRY_ADDRESS)]
        self.assertEqual(struct.unpack_from("<I", stub, 1)[0], layout.script_address)
        table = pieces[ft.dialogue_runtime_to_file_offset(layout.dialogue_table_address)]
        self.assertEqual(struct.unpack_from("<HHI", table, 0), (ft.TELLER_ACTOR_SLOT, 0, layout.script_address))
        self.assertIn(ft.dialogue_runtime_to_file_offset(layout.charger_natives_address), pieces)
        self.assertNotIn(ft.dialogue_runtime_to_file_offset(layout.natives_address), pieces)
        self.assertEqual(len(ft.iter_overlay_file_patches(charger=True)), 2)
        # nothing else moved: the charger's addresses are the same either way
        full = ft.build_layout()
        self.assertEqual(full.charger_script_address, layout.charger_script_address)
        self.assertEqual(full.charger_natives.code, layout.charger_natives.code)

    def test_defaults_are_on(self) -> None:
        from .. import options
        self.assertEqual(options.HintSystem.default, 1)
        self.assertEqual(options.TemperSystem.default, 1)
