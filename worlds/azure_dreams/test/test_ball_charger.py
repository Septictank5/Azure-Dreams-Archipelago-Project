"""The ball charger (ball_charger.py, laid out by fortune_teller.build_layout):
the natives run in the simulator over a planted bag, the script's shape, the
overlay edits pinned against the disc, and the record set.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from .. import ball_charger as bc
from .. import fortune_teller as ft
from .. import item_manifest, items, patch, town_shop
from . import mips_sim

_TOWN_BIN = Path(__file__).resolve().parents[4] / "extracted" / "TOWN.BIN"


class TestBallChargerCosts(unittest.TestCase):
    def test_cost_curve(self) -> None:
        self.assertEqual(bc.CHARGE_COST, (500, 531, 623, 778, 994, 1272, 1611, 2012, 2475, 3000))
        self.assertEqual(bc.charge_cost(-3), 500)
        self.assertEqual(bc.charge_cost(9), 3000)
        self.assertEqual(bc.charge_cost(40), 3000)
        self.assertEqual(sum(bc.CHARGE_COST), 13796)      # a full 0 -> 10 climb

    def test_allowance_and_ceiling(self) -> None:
        # The sands buy CHARGES PER TOWN VISIT, not a per-ball ceiling. The
        # ceiling is ten at every level, because ten is what teaches mix magic.
        self.assertEqual(bc.USES_BY_LEVEL, (0, 1, 2, 3))
        self.assertEqual(bc.MAX_USES, 3)
        self.assertEqual(bc.MAX_CHARGES, item_manifest.TEACHING_CHARGES)
        self.assertEqual(len(bc.CHARGE_COST), bc.MAX_CHARGES)
        self.assertEqual(bc.ACID_RAIN_BALL_ID, item_manifest.ACID_RAIN_BALL_ID)

    def test_the_spend_counter_sits_beside_the_level(self) -> None:
        self.assertEqual(bc.USED_ADDRESS, bc.LEVEL_ADDRESS + 1)
        self.assertLess(bc.USED_ADDRESS, patch.PERSISTENT_STATE_ADDRESS)


class TestBallChargerSceneEdits(unittest.TestCase):
    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_overlay_originals_match_the_disc(self) -> None:
        town = _TOWN_BIN.read_bytes()
        offset = ft.overlay_runtime_to_file_offset(ft.OVERLAY_TABLE_ADDIU_ADDRESS)
        self.assertEqual(struct.unpack_from("<I", town, offset)[0], ft.OVERLAY_TABLE_ADDIU_ORIGINAL)
        # the getter's lui a0,0x8001 the word before that immediate's partner
        self.assertEqual(struct.unpack_from("<I", town, offset - 8)[0], 0x3C04_8001)
        # the record we overwrite is the run's terminator, and another terminator follows it
        record = ft.overlay_runtime_to_file_offset(ft.CHARGER_RECORD_ADDRESS)
        self.assertEqual(town[record : record + 20], bytes.fromhex("00800000" + "00" * 16))
        self.assertEqual(town[record + 20 : record + 40], bytes.fromhex("00800000" + "00" * 16))
        # the vanilla dialogue table row we relocate
        table = ft.overlay_runtime_to_file_offset(ft.VANILLA_DIALOGUE_TABLE_ADDRESS)
        self.assertEqual(struct.unpack_from("<HHI", town, table), (ft.TELLER_ACTOR_SLOT, 0, ft.SCRIPT_ENTRY_ADDRESS))

    def test_overlay_patches(self) -> None:
        patches = dict(ft.iter_overlay_file_patches())
        layout = ft.build_layout()
        addiu = patches[ft.overlay_runtime_to_file_offset(ft.OVERLAY_TABLE_ADDIU_ADDRESS)]
        self.assertEqual(struct.unpack("<I", addiu)[0], patch._i(0x09, 4, 4, layout.dialogue_table_address & 0xFFFF))
        self.assertLess(layout.dialogue_table_address & 0xFFFF, 0x8000)      # positive immediate under lui 0x8001
        self.assertEqual(layout.dialogue_table_address >> 16, 0x8001)
        record = patches[ft.overlay_runtime_to_file_offset(ft.CHARGER_RECORD_ADDRESS)]
        self.assertEqual(len(record), 20)
        self.assertEqual(record[:4], bytes.fromhex("00600100"))
        self.assertEqual(record[4], bc.CHARGER_FACING)
        self.assertEqual(struct.unpack_from("<I", record, 12)[0], bc.CHARGER_ACTOR_TYPE)
        self.assertEqual(struct.unpack_from("<hh", record, 16), bc.CHARGER_POSITION)
        # she stands to Shiela's right, on the same grid
        self.assertGreater(bc.CHARGER_POSITION[0], ft.TELLER_POSITION[0])
        self.assertEqual(bc.CHARGER_POSITION[0] % 8, 0)

    def test_dialogue_table(self) -> None:
        layout = ft.build_layout()
        table = ft.build_dialogue_table(layout)
        self.assertEqual(len(table), ft.DIALOGUE_TABLE_SIZE)
        rows = [struct.unpack_from("<HHI", table, i * 8) for i in range(3)]
        self.assertEqual(rows[0], (ft.TELLER_ACTOR_SLOT, 0, layout.page_addresses["main"]))
        self.assertEqual(rows[1], (bc.CHARGER_ACTOR_SLOT, 0, layout.charger_labels["start"]))
        self.assertEqual(rows[2], (0, 0, 0))
        self.assertNotEqual(bc.CHARGER_ACTOR_SLOT, ft.TELLER_ACTOR_SLOT)

    def test_layout_places_the_charger_over_the_slab_entry_table(self) -> None:
        layout = ft.build_layout()
        self.assertEqual(layout.charger_jump_table_address, town_shop.SMITH_NATIVE_TABLE_ADDRESS)
        self.assertLessEqual(layout.end, layout.charger_jump_table_address)
        self.assertLessEqual(layout.charger_end, ft.REGION_END)
        words = struct.unpack("<4I", bc.build_jump_table(layout.charger_natives))
        self.assertEqual(words[0], patch._j(0x02, layout.charger_natives.addresses["price"]))
        self.assertEqual(words[2], patch._j(0x02, layout.charger_natives.addresses["guard"]))
        self.assertEqual(town_shop.SMITH_PRICE_ENTRY_ADDRESS, layout.charger_jump_table_address)
        self.assertEqual(town_shop.SMITH_GUARD_ENTRY_ADDRESS, layout.charger_jump_table_address + 8)


class _Bench:
    MENU = 0x8010_0000
    LIST_WIDGET = 0x8010_1000
    SCRATCH = 0x8010_2000

    def __init__(
        self, items, money: int, level: int, selected_row: int = 1, used: int = 0
    ) -> None:
        self.layout = ft.build_layout()
        self.natives = self.layout.charger_natives
        self.memory = mips_sim.Memory()
        self.memory.load_bytes(self.layout.charger_natives_address, self.natives.code)
        self.memory.load_bytes(self.layout.charger_jump_table_address, bc.build_jump_table(self.natives))
        self.memory.load_bytes(self.layout.charger_catalog_address, b"\xEE" * (bc.CATALOG_BUFFER_SIZE + 4))
        self.memory.load_bytes(town_shop.MENU_CONSTRUCTOR_ADDRESS, struct.pack("<II", 0x03E0_0008, 0x2402_1234))
        self.memory.load_bytes(
            bc.ROW_BUILDER_ADDRESS,
            struct.pack("<4I", 0x3C01_8010, 0xAC24_2000, 0x03E0_0008, 0),
        )
        for index, descriptor in enumerate(items):
            self.memory.load_bytes(0x8001_0248 + index * 4, bytes(descriptor))
            self.memory.write32(bc.INVENTORY_ORDER_ADDRESS + index * 4, 0x8001_0248 + index * 4)
        self.memory.write32(bc.INVENTORY_ORDER_ADDRESS + len(items) * 4, 0)
        self.memory.write32(bc.TOWN_MONEY_ADDRESS, money)
        # One word: the level byte then the spend counter, exactly as the ADSV
        # initializer zeroes them.
        self.memory.write32(bc.LEVEL_ADDRESS, level)
        self.memory.write8(bc.USED_ADDRESS, used)
        self.memory.write32(bc.MENU_HEADER_POINTER_SLOT_ADDRESS, bc.VANILLA_MENU_HEADER_TEXT_ADDRESS)
        self.memory.write32(bc.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS, bc.VANILLA_REFUSAL_MESSAGE_ADDRESS)
        self.memory.write32(self.MENU + 4, selected_row)
        self.memory.write32(self.MENU + 0x20, self.layout.charger_catalog_address)
        self.memory.write32(self.MENU + 0x28, self.LIST_WIDGET)

    def call(self, name: str, a0: int = 0) -> int:
        cpu = mips_sim.Cpu(self.memory)
        cpu.registers[4] = a0
        return cpu.run(self.natives.addresses[name], limit=200_000)

    def call_at(self, address: int, a0: int = 0) -> int:
        cpu = mips_sim.Cpu(self.memory)
        cpu.registers[4] = a0
        return cpu.run(address, limit=200_000)

    def catalog(self) -> list[list[int]]:
        rows = []
        address = self.layout.charger_catalog_address + 4
        while self.memory.read32(address):
            rows.append([self.memory.read8(address + i) for i in range(4)])
            address += 4
        return rows

    def descriptor(self, index: int) -> list[int]:
        return [self.memory.read8(0x8001_0248 + index * 4 + i) for i in range(4)]

    def slab_byte(self, offset: int) -> int:
        return self.memory.read8(town_shop.SHOP_CORE_ADDRESS + offset)


ITEMS = (
    (1, 0x04, 4, 0x00),      # 0: Fire Ball, 4 charges
    (6, 0x04, 9, 0x00),      # 1: Water Ball, 9 charges
    (17, 0x04, 1, 0x00),     # 2: Acid Rain Ball: never
    (1, 0x0F, 3, 0x20),      # 3: a sword: not a ball
    (2, 0x04, 10, 0x00),     # 4: Blaze Ball, 10 charges: at the max
    (3, 0x04, 0, 0x00),      # 5: Flame Ball, empty
)


class TestBallChargerNatives(unittest.TestCase):
    def test_is_chargeable(self) -> None:
        bench = _Bench(ITEMS, 0, 3)
        for index, want in enumerate((1, 1, 0, 0, 1, 1)):
            self.assertEqual(bench.call("is_chargeable", 0x8001_0248 + index * 4), want, index)

    def test_price_reads_the_table(self) -> None:
        bench = _Bench((), 0, 3)
        for charges in (-2, 0, 1, 4, 9, 10, 44):
            bench.memory.load_bytes(0x8010_3000, bytes((1, 0x04, charges & 0xFF, 0)))
            self.assertEqual(bench.call("price", 0x8010_3000), bc.charge_cost(charges), charges)
        # through the slab's entry table too
        bench.memory.load_bytes(0x8010_3000, bytes((1, 0x04, 3, 0)))
        self.assertEqual(bench.call_at(town_shop.SMITH_PRICE_ENTRY_ADDRESS, 0x8010_3000), 778)

    def test_allowance_reads_the_level_byte(self) -> None:
        for level, expected in ((0, 0), (1, 1), (2, 2), (3, 3), (7, 3)):
            self.assertEqual(_Bench((), 0, level).call("allowance"), expected, level)
        # only the low byte of the word is the level
        bench = _Bench((), 0, 0)
        bench.memory.write32(bc.LEVEL_ADDRESS, 0x0000_0002)
        self.assertEqual(bench.call("allowance"), 2)

    def test_uses_left_subtracts_the_spend_counter(self) -> None:
        for level, used, expected in (
            (3, 0, 3), (3, 1, 2), (3, 3, 0), (3, 9, 0),
            (1, 0, 1), (1, 1, 0), (0, 0, 0),
        ):
            self.assertEqual(
                _Bench((), 0, level, used=used).call("uses_left"), expected, (level, used)
            )

    def test_has_balls(self) -> None:
        self.assertEqual(_Bench(ITEMS, 0, 3).call("has_balls"), 1)
        self.assertEqual(_Bench(((17, 0x04, 1, 0), (1, 0x0F, 3, 0)), 0, 3).call("has_balls"), 0)
        self.assertEqual(_Bench((), 0, 3).call("has_balls"), 0)

    def test_open_builds_the_catalog_and_dresses_the_menu(self) -> None:
        bench = _Bench(ITEMS, 5000, 3)
        self.assertEqual(bench.call("open"), 0x1234)
        self.assertEqual(bench.memory.read32(bench.layout.charger_handle_address), 0x1234)
        self.assertEqual(bench.memory.read32(bench.layout.charger_catalog_address), bc.CATALOG_LEADING_ENTRY)
        self.assertEqual(
            bench.catalog(),
            [[1, 0x04, 4, 0], [6, 0x04, 9, 0], [2, 0x04, 10, 0x80], [3, 0x04, 0, 0]],
        )
        # the whole buffer was zeroed first (it was 0xEE-filled)
        tail = bench.layout.charger_catalog_address + 4 * 5
        self.assertEqual(bench.memory.read32(tail), 0)
        self.assertEqual(bench.memory.read8(bench.layout.charger_catalog_address + bc.CATALOG_BUFFER_SIZE - 1), 0)
        # level 1: only the ball that is already full is greyed - the ceiling
        # is ten at every level now, and the level only says how many charges
        # she will hand out this visit
        bench = _Bench(ITEMS, 5000, 1)
        bench.call("open")
        self.assertEqual(
            bench.catalog(),
            [[1, 0x04, 4, 0], [6, 0x04, 9, 0], [2, 0x04, 10, 0x80], [3, 0x04, 0, 0]],
        )
        # level 0: no allowance at all, so every row is greyed
        bench = _Bench(ITEMS, 5000, 0)
        bench.call("open")
        self.assertTrue(all(row[3] == 0x80 for row in bench.catalog()))
        # level 3 with the visit's three charges already spent: the same
        bench = _Bench(ITEMS, 5000, 3, used=3)
        bench.call("open")
        self.assertTrue(all(row[3] == 0x80 for row in bench.catalog()))
        bench = _Bench(ITEMS, 5000, 3)
        bench.call("open")
        self.assertEqual(bench.slab_byte(town_shop.ACTIVE_SHOP_OFFSET), bc.MENU_SHOP_MARKER)
        self.assertEqual(bench.slab_byte(town_shop.ARMED_MENU_OFFSET), 1)
        self.assertEqual(bench.memory.read32(bc.MENU_HEADER_POINTER_SLOT_ADDRESS), bench.layout.charger_header_text_address)
        self.assertEqual(bench.memory.read32(bc.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS), bench.layout.charger_refusal_text_address)
        self.assertEqual(bench.call("after_menu"), 0)
        self.assertEqual(bench.memory.read32(bc.MENU_HEADER_POINTER_SLOT_ADDRESS), bc.VANILLA_MENU_HEADER_TEXT_ADDRESS)
        self.assertEqual(bench.memory.read32(bc.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS), bc.VANILLA_REFUSAL_MESSAGE_ADDRESS)
        self.assertEqual(bench.slab_byte(town_shop.ACTIVE_SHOP_OFFSET), 0xFF)

    def test_guard_charges_the_selected_row(self) -> None:
        bench = _Bench(ITEMS, 5000, 3, selected_row=1)     # the Fire Ball at 4
        bench.call("open")
        self.assertEqual(bench.call_at(town_shop.SMITH_GUARD_ENTRY_ADDRESS, _Bench.MENU), 1)
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 5000 - bc.charge_cost(4))
        self.assertEqual(bench.descriptor(0), [1, 0x04, 5, 0])
        self.assertEqual(bench.catalog()[0], [1, 0x04, 5, 0x20])
        self.assertEqual(bench.memory.read32(_Bench.SCRATCH), _Bench.LIST_WIDGET + 0x20)
        # the Acid Rain and the sword are skipped when counting rows
        bench = _Bench(ITEMS, 5000, 3, selected_row=4)     # catalog row 4 = the empty Flame Ball (bag index 5)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.descriptor(5), [3, 0x04, 1, 0])
        self.assertEqual(bench.descriptor(2), [17, 0x04, 1, 0])
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 5000 - bc.charge_cost(0))

    def test_guard_greys_the_row_at_the_ceiling(self) -> None:
        bench = _Bench(ITEMS, 5000, 3, selected_row=2)     # the Water Ball at 9 -> 10
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.descriptor(1)[2], 10)
        # 0xA0, because vanilla is not finished with this byte yet - see
        # test_the_row_never_ends_up_looking_selected.
        self.assertEqual(bench.catalog()[1], [6, 0x04, 10, 0xA0])
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 5000 - 3000)
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 1)

    def test_the_row_never_ends_up_looking_selected(self) -> None:
        """After a charge, vanilla's A-press handler XORs 0x20 on the row and
        then recolours from the result (`recolour_shop_row`, 0x800B09EC): 0x20
        set is CHECKED, which paints the row green and stamps the mode's BUY
        tag on it. X applies the charge on the spot here, so a selection
        highlight is a lie - every path out of the guard has to leave 0x20 SET
        so the toggle takes it off again.
        """

        def after_vanilla_toggle(flags: int) -> int:
            return flags ^ 0x20

        cases = (
            # (bag, level, spent, row, what the press means)
            (((3, 0x04, 0, 0),), 3, 0, 1, "an ordinary charge"),
            (((6, 0x04, 9, 0),), 3, 0, 1, "the charge that reaches ten"),
            (((3, 0x04, 0, 0),), 1, 0, 1, "the visit's last charge"),
            (((6, 0x04, 9, 0),), 1, 0, 1, "last charge AND tenth at once"),
        )
        for bag, level, spent, row, what in cases:
            with self.subTest(what):
                bench = _Bench(bag, 50_000, level, selected_row=row, used=spent)
                bench.call("open")
                self.assertEqual(bench.call("guard", _Bench.MENU), 1, what)
                flags = bench.catalog()[row - 1][3]
                self.assertEqual(flags & 0x20, 0x20, f"{what}: 0x20 was not left set")
                settled = after_vanilla_toggle(flags)
                self.assertEqual(
                    settled & 0x20, 0, f"{what}: the row settles green with a BUY tag"
                )
                # and a row that is done is grey rather than merely uncoloured
                done = level == 1 or bag[0][2] + 1 >= bc.MAX_CHARGES
                self.assertEqual(
                    settled & 0x80, 0x80 if done else 0, f"{what}: wrong grey state"
                )

    def test_guard_spends_the_visit_allowance(self) -> None:
        # Level 2 = two charges a visit, and they may go into the same ball:
        # 0 -> 1 -> 2 on one sphere, then the whole list greys out.
        bench = _Bench(((3, 0x04, 0, 0),), 5000, 2, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        self.assertEqual(bench.catalog()[0], [3, 0x04, 1, 0x20])
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 1)
        # the A-press handler's toggle XORs the 0x20 back off before the next press
        row = bench.layout.charger_catalog_address + 4
        bench.memory.write8(row + 3, bench.memory.read8(row + 3) ^ 0x20)
        self.assertEqual(bench.call("guard", _Bench.MENU), 1)
        # two charges in, nowhere near the ten-charge ceiling, but the visit is
        # over - so the row is greyed by the allowance rather than by the ball
        self.assertEqual(bench.catalog()[0], [3, 0x04, 2, 0xA0])
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 2)
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 5000 - 500 - 531)
        # and a third press is refused, with the money and the ball untouched
        bench.memory.write8(row + 3, bench.memory.read8(row + 3) ^ 0x20)
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(bench.descriptor(0), [3, 0x04, 2, 0])
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 5000 - 500 - 531)
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 2)

    def test_the_allowance_is_global_not_per_ball(self) -> None:
        # Three charges at level 3 spread over three different balls, and the
        # fourth press is refused however much gold is left.
        items = ((1, 0x04, 0, 0), (6, 0x04, 0, 0), (3, 0x04, 0, 0), (2, 0x04, 0, 0))
        bench = _Bench(items, 50_000, 3, selected_row=1)
        bench.call("open")
        for row_index in (1, 2, 3):
            bench.memory.write32(_Bench.MENU + 4, row_index)
            self.assertEqual(bench.call("guard", _Bench.MENU), 1, row_index)
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 3)
        self.assertEqual([bench.descriptor(i)[2] for i in range(4)], [1, 1, 1, 0])
        bench.memory.write32(_Bench.MENU + 4, 4)
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(bench.descriptor(3)[2], 0)

    def test_guard_names_the_reason_it_refused(self) -> None:
        layout = ft.build_layout()
        # gold short, allowance intact: the gold string
        bench = _Bench(ITEMS, 100, 3, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(
            bench.memory.read32(bc.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS),
            layout.charger_refusal_text_address,
        )
        # gold in hand, allowance gone: the second string
        bench = _Bench(ITEMS, 50_000, 3, selected_row=1, used=3)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(
            bench.memory.read32(bc.REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS),
            layout.charger_refusal_spent_text_address,
        )
        self.assertNotEqual(
            layout.charger_refusal_spent_text_address, layout.charger_refusal_text_address
        )
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 50_000)

    def test_guard_refuses_a_ball_already_at_the_ceiling(self) -> None:
        bench = _Bench(((2, 0x04, 10, 0),), 50_000, 3, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(bench.descriptor(0)[2], 10)
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 50_000)
        self.assertEqual(bench.memory.read8(bc.USED_ADDRESS), 0)

    def test_guard_refuses_without_gold(self) -> None:
        bench = _Bench(ITEMS, 100, 3, selected_row=1)
        bench.call("open")
        self.assertEqual(bench.call("guard", _Bench.MENU), 0)
        self.assertEqual(bench.memory.read32(bc.TOWN_MONEY_ADDRESS), 100)
        self.assertEqual(bench.descriptor(0), [1, 0x04, 4, 0])
        self.assertEqual(bench.memory.read32(_Bench.SCRATCH), 0)


class TestBallChargerScript(unittest.TestCase):
    def test_script_shape(self) -> None:
        layout = ft.build_layout()
        script = layout.charger_script
        natives = layout.charger_natives.addresses
        labels = layout.charger_labels
        base = layout.charger_script_address
        self.assertEqual(labels["start"], base)
        self.assertEqual(script[0], 0x4C)
        self.assertEqual(struct.unpack_from("<I", script, 1)[0], natives["has_balls"])
        self.assertEqual(script[5:7], b"\x3e\x0f")
        self.assertEqual(struct.unpack_from("<I", script, 7)[0], labels["no_balls"])
        self.assertEqual(script[11], 0x4C)
        self.assertEqual(struct.unpack_from("<I", script, 12)[0], natives["allowance"])
        self.assertEqual(struct.unpack_from("<I", script, 18)[0], labels["asleep"])
        # then the second, distinct test: the allowance exists but is spent
        self.assertEqual(script[22], 0x4C)
        self.assertEqual(struct.unpack_from("<I", script, 23)[0], natives["uses_left"])
        self.assertEqual(struct.unpack_from("<I", script, 29)[0], labels["spent"])
        self.assertEqual(script[33:35], bytes((0x57, bc.CHARGER_ACTOR_SLOT)))
        # the greeting prints what is LEFT of this visit, not the level's total
        uses_call = script.find(b"\x4c" + struct.pack("<I", natives["uses_left"]), 35)
        self.assertGreater(uses_call, 35)
        self.assertEqual(script[uses_call + 5 : uses_call + 7], b"\xfd\x0f")
        pick = labels["pick"] - base
        self.assertEqual(script[pick : pick + 2], b"\x08\x15")
        self.assertEqual(struct.unpack_from("<I", script, pick + 2)[0], labels["opener"])
        self.assertEqual(struct.unpack_from("<I", script, pick + 7)[0], natives["after_menu"])
        self.assertEqual(script[pick + 11 : pick + 13], b"\x01\x01")
        stub = script[labels["opener"] - base :]
        self.assertEqual(stub[0:3], b"\x30\x34\x0e")
        self.assertEqual(struct.unpack_from("<I", stub, 3)[0], labels["opener_return"])
        self.assertEqual(struct.unpack_from("<I", stub, 8)[0], natives["open"])
        self.assertEqual(struct.unpack_from("<I", stub, 14)[0], labels["opener"])
        self.assertEqual(stub[-2:], b"\x23\x16")
        self.assertNotEqual(script[-1], 0)
        for line in (
            bc.GREETING,
            bc.CAP_LINE_HEAD + "3" + bc.CAP_LINE_TAIL,
            *bc.BYE_PAGE,
            *bc.NO_BALLS_PAGE,
            *bc.ASLEEP_PAGE,
            *bc.SPENT_PAGE,
        ):
            self.assertLessEqual(len(bc._text(line)) // 2, ft.LINE_LIMIT, line)

    def test_menu_strings(self) -> None:
        self.assertIn(bytes((0x81, 0x7E, 0x20)), bc.build_header_text())
        self.assertEqual(bc.build_refusal_text()[-1], 0)
        self.assertEqual(bc.build_spent_refusal_text()[-1], 0)
        self.assertNotEqual(bc.build_spent_refusal_text(), bc.build_refusal_text())


class TestWhiteSandInThePool(unittest.TestCase):
    def test_white_sand_is_a_fixed_useful_unlock(self) -> None:
        self.assertIn(items.WHITE_SAND, items.ITEM_CLASSIFICATIONS)
        self.assertIn(item_manifest.WHITE_SAND_NAME, item_manifest.TEMPER_SAND_NAMES)
        self.assertFalse(any(r.name == item_manifest.WHITE_SAND_NAME for r in item_manifest.ORDINARY_REWARDS))
        reward = item_manifest.REWARD_BY_NAME[item_manifest.WHITE_SAND_NAME]
        self.assertEqual((reward.category, reward.native_item_id), (item_manifest.SAND_CATEGORY, 3))


class TestBallChargerRecords(unittest.TestCase):
    def test_records_include_both_homes(self) -> None:
        classes = [0] * ft.LOCATION_COUNT
        dialogue = dict(ft.iter_dialogue_file_patches(classes))
        layout = ft.build_layout()
        for address in (
            layout.dialogue_table_address,
            layout.charger_jump_table_address,
            layout.charger_natives_address,
            layout.charger_catalog_address,
            layout.charger_header_text_address,
            layout.charger_refusal_text_address,
            layout.charger_script_address,
        ):
            self.assertIn(ft.dialogue_runtime_to_file_offset(address), dialogue)
        overlay = dict(ft.iter_overlay_file_patches())
        self.assertEqual(len(overlay), 2)
        raw = ft.iter_fortune_teller_raw_patches(classes)
        self.assertGreater(len(raw), 8)


if __name__ == "__main__":
    unittest.main()


from .bases import AzureDreamsTestBase  # noqa: E402


class TestTemperSystemOff(AzureDreamsTestBase):
    options = {"temper_system": False, "hint_system": False}

    def test_no_sands_and_the_pool_size_holds(self) -> None:
        names = [item.name for item in self.multiworld.itempool]
        self.assertEqual(len(names), 137)
        for sand in (items.RED_SAND, items.BLUE_SAND, items.WHITE_SAND):
            self.assertNotIn(sand, names)
        self.assertFalse(self.world.options.temper_system)
        self.assertFalse(self.world.options.hint_system)


class TestTemperSystemOn(AzureDreamsTestBase):
    def test_three_of_each_sand_by_default(self) -> None:
        names = [item.name for item in self.multiworld.itempool]
        self.assertEqual(len(names), 137)
        for sand in (items.RED_SAND, items.BLUE_SAND, items.WHITE_SAND):
            self.assertEqual(names.count(sand), items.TEMPER_SAND_COUNT, sand)
