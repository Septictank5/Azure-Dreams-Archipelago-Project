"""The town status strip (town_status_strip.py): the natives in the simulator
against stubbed resident routines, the text template and frame chain, the
town-image words pinned to the disc, and the record set."""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from .. import ball_charger, blacksmith, town_shop
from .. import town_status_strip as ts
from . import mips_sim

_TOWN_BIN = Path(__file__).resolve().parents[4] / "extracted" / "TOWN.BIN"


class TestPieces(unittest.TestCase):
    def test_template_shape(self) -> None:
        template = ts.build_text_template()
        self.assertEqual(template, b"WEAPON:+0 \x0c8SHIELD:+0 \x0c8BALL:+0\x00")
        self.assertEqual(len(template), ts.TEXT_TEMPLATE_SIZE)
        # each slot starts life holding its own level-0 string
        for offset, width, texts in zip(
            ts.TEXT_SLOT_OFFSETS, ts.GROUP_VALUE_WIDTHS, ts.GROUP_VALUE_TEXTS
        ):
            self.assertEqual(template[offset : offset + width].decode(), texts[0])

    def test_the_strip_prints_what_a_level_buys(self) -> None:
        # the point of the display: the smith's ceiling and the charger's
        # per-visit allowance, not the sand count the tracker already shows
        self.assertEqual(ts.GEAR_VALUE_TEXTS, ("+0 ", "+10", "+20", "+40"))
        self.assertEqual(ts.BALL_VALUE_TEXTS, ("+0", "+1", "+2", "+3"))
        self.assertEqual(
            [text.strip() for text in ts.GEAR_VALUE_TEXTS],
            [f"+{cap}" for cap in blacksmith.CAP_BY_LEVEL],
        )
        self.assertEqual(
            list(ts.BALL_VALUE_TEXTS), [f"+{uses}" for uses in ball_charger.USES_BY_LEVEL]
        )
        # every value slot is one fixed width, so nothing shifts as levels rise
        for texts in ts.GROUP_VALUE_TEXTS:
            self.assertEqual(len({len(text) for text in texts}), 1)
        # upper case only: the 8x8 atlas maps lower-case rows to garbage (measured)
        self.assertTrue(all(not text.islower() for text in ts.GROUP_TEXTS))
        self.assertEqual(ts.TEXT_WIDTH, 232)
        self.assertEqual(ts.TEXT_LEFT + ts.TEXT_WIDTH, ts.FRAME_LEFT + ts.FRAME_WIDTH - 12)

    def test_frame_chain(self) -> None:
        chain = ts.build_frame_chain()
        self.assertEqual(len(chain), 18 * 12)
        records = [chain[i : i + 12] for i in range(0, len(chain), 12)]
        self.assertTrue(all(r[1] == 0x2C and struct.unpack_from("<HH", r, 4) == (0x17, 0x7CC2) for r in records))
        self.assertEqual(records[-1][0], 0xC0)                       # last
        self.assertEqual(sum(1 for r in records[:-1] if r[0] & 0x80), 0)
        xs = [struct.unpack_from("<b", r, 2)[0] for r in records]
        self.assertEqual(min(xs), -128)
        self.assertEqual(max(x + w for x, w in zip(xs, (r[10] for r in records))), 128)
        self.assertEqual((records[4][0], records[5][0]), (0x41, 0x41))   # the mirrored right rail slices
        ys = [struct.unpack_from("<b", r, 3)[0] + r[11] for r in records]
        self.assertEqual(max(ys), ts.FRAME_HEIGHT)
        self.assertLess(ts.FRAME_TOP + ts.FRAME_HEIGHT, 32)          # clears the inventory's MONEY box
        self.assertEqual((ts.BOX_X, ts.BOX_W), (ts.FRAME_LEFT + 7, ts.FRAME_WIDTH - 14))
        # the text sits between the bars
        self.assertGreaterEqual(ts.TEXT_TOP, ts.FRAME_TOP + 8)
        self.assertLessEqual(ts.TEXT_TOP + 8, ts.FRAME_TOP + ts.FRAME_BAR_Y)

    def test_layout_fits_the_canvas(self) -> None:
        layout = ts.build_layout()
        self.assertEqual(layout.state_address, ts.CANVAS_ADDRESS + 16)
        self.assertLess(layout.end, ts.CANVAS_ADDRESS + 0x800)      # under 2 KB of the 8 KB canvas
        self.assertLessEqual(layout.end, 0x800F_EE00)               # memory-map.md: next tenant claims from here
        self.assertLessEqual(layout.end, ts.CANVAS_END)
        for name in ("rebuild", "update", "chain_actor", "spawn", "trampoline"):
            self.assertIn(name, layout.natives.addresses)

    @unittest.skipUnless(_TOWN_BIN.exists(), "extracted TOWN.BIN not present")
    def test_town_image_originals(self) -> None:
        town = _TOWN_BIN.read_bytes()
        jal = town_shop.town_runtime_to_file_offset(ts.SCENE_RUNNER_TAIL_JAL_ADDRESS)
        self.assertEqual(struct.unpack_from("<I", town, jal)[0], ts.SCENE_RUNNER_TAIL_JAL_ORIGINAL)
        # the words around it: `lw a1,0x88(a0)` before, `nop` / `lw ra,0x1c(sp)` after
        self.assertEqual(struct.unpack_from("<I", town, jal - 4)[0], 0x8C85_0088)
        self.assertEqual(struct.unpack_from("<II", town, jal + 4), (0, 0x8FBF_001C))
        # the canvas is empty on disc for the span we take
        layout = ts.build_layout()
        start = town_shop.town_runtime_to_file_offset(ts.CANVAS_ADDRESS)
        end = town_shop.town_runtime_to_file_offset(layout.end)
        self.assertEqual(set(town[start:end]) - {0x00, 0xFF}, set())

    def test_scene_runner_jal_targets_the_trampoline(self) -> None:
        layout = ts.build_layout()
        word = struct.unpack("<I", ts.build_scene_runner_jal(layout))[0]
        self.assertEqual(word >> 26, 0x03)
        self.assertEqual((word & 0x03FF_FFFF) << 2 | 0x8000_0000, layout.natives.addresses["trampoline"])


class _Bench:
    ACTOR = 0x8010_2000

    def __init__(self, weapon=0, shield=0, ball=0) -> None:
        self.layout = ts.build_layout()
        self.natives = self.layout.natives
        self.memory = mips_sim.Memory()
        self.memory.load_bytes(self.layout.natives_address, self.natives.code)
        self.memory.load_bytes(self.layout.text_address, ts.build_text_template())
        self.memory.load_bytes(self.layout.chain_address, b"\xEE" * ts.CHAIN_BUFFER_SIZE)
        self.memory.load_bytes(self.layout.frame_chain_address, ts.build_frame_chain())
        self.memory.load_bytes(self.layout.state_address, bytes(ts.STATE_SIZE))
        self.memory.write32(ts.WEAPON_LEVEL_ADDRESS & ~3, 0)
        self.memory.write8(ts.WEAPON_LEVEL_ADDRESS, weapon)
        self.memory.write8(ts.SHIELD_LEVEL_ADDRESS, shield)
        self.memory.write32(ts.BALL_LEVEL_ADDRESS, ball)
        self.calls: list[tuple] = []
        # fake actors the allocator hands out in turn, shaped like allocate_actor(flags):
        # zeroed, obj08/obj0C set, +0x1E = flags | 0x4000
        self.allocated = 0
        for index in range(6):
            actor = self.ACTOR + index * 0x200
            self.memory.load_bytes(actor, bytes(0x124))
            self.memory.write32(actor + 8, actor + 0xDC)
            self.memory.write32(actor + 0xC, actor + 0xF4)

        def stub(name):
            def run(cpu):
                self.calls.append((name, cpu.registers[4], cpu.registers[5], cpu.registers[6], cpu.registers[7]))
                if name == "allocate":
                    actor = self.ACTOR + self.allocated * 0x200
                    flags = (cpu.registers[4] | 0x4000) & 0xFFFF
                    self.memory.write8(actor + 0x1E, flags & 0xFF)
                    self.memory.write8(actor + 0x1F, flags >> 8)
                    cpu.registers[2] = actor
                    self.allocated += 1
                elif name == "build_chain":
                    cpu.registers[2] = cpu.registers[4]
            return run

        self.stubs = {
            ts.ALLOCATE_ACTOR_ADDRESS: stub("allocate"),
            ts.BUILD_ASCII_CHAIN_ADDRESS: stub("build_chain"),
            ts.REGISTER_RENDER_ROOT_ADDRESS: stub("register"),
            ts.REGISTER_ACTOR_ROOT_ADDRESS: stub("register_actor"),
            ts.RUN_SCRIPT_ADDRESS: stub("run_script"),
        }

    def call(self, name: str, a0=0, a1=0, a2=0) -> int:
        cpu = mips_sim.Cpu(self.memory, self.stubs)
        cpu.registers[4], cpu.registers[5], cpu.registers[6] = a0, a1, a2
        return cpu.run(self.natives.addresses[name], limit=100_000)

    def slot(self, index: int) -> str:
        start = self.layout.text_address + ts.TEXT_SLOT_OFFSETS[index]
        width = ts.GROUP_VALUE_WIDTHS[index]
        return bytes(self.memory.read8(start + i) for i in range(width)).decode()

    def state8(self, offset: int) -> int:
        return self.memory.read8(self.layout.state_address + offset)


class TestStripNatives(unittest.TestCase):
    def test_rebuild_writes_the_levels_and_builds_the_chain(self) -> None:
        bench = _Bench(weapon=1, shield=3, ball=2)
        bench.call("rebuild")
        self.assertEqual((bench.slot(0), bench.slot(1), bench.slot(2)), ("+10", "+40", "+2"))
        self.assertEqual(bench.calls[-1][:4], ("build_chain", bench.layout.chain_address, bench.layout.text_address, 0))
        # the whole line, so a wrong slot offset cannot pass by luck
        line = bytes(
            bench.memory.read8(bench.layout.text_address + i) for i in range(ts.TEXT_TEMPLATE_SIZE)
        )
        self.assertEqual(line, b"WEAPON:+10\x0c8SHIELD:+40\x0c8BALL:+2\x00")
        # a level past the table reads as the top entry rather than off the end
        bench = _Bench(weapon=12, shield=0, ball=9)
        bench.call("rebuild")
        self.assertEqual((bench.slot(0), bench.slot(1), bench.slot(2)), ("+40", "+0 ", "+3"))

    def test_update_builds_once_then_only_on_change(self) -> None:
        bench = _Bench(weapon=2, shield=1, ball=1)
        body, xf, spr = _Bench.ACTOR + 0x20, _Bench.ACTOR + 0xDC, _Bench.ACTOR + 0xF4
        bench.call("update", body, xf, spr)
        self.assertEqual([c[0] for c in bench.calls], ["build_chain"])
        self.assertEqual(bench.state8(ts.STATE_CACHE + 3), 1)
        self.assertEqual((bench.state8(ts.STATE_CACHE), bench.state8(ts.STATE_CACHE + 1), bench.state8(ts.STATE_CACHE + 2)), (2, 1, 1))
        bench.call("update", body, xf, spr)
        self.assertEqual(len(bench.calls), 1)                        # unchanged: no rebuild
        bench.memory.write8(ts.BALL_LEVEL_ADDRESS, 3)
        bench.call("update", body, xf, spr)
        self.assertEqual(len(bench.calls), 2)
        self.assertEqual(bench.slot(2), "+3")

    def test_update_hides_the_strip_while_a_window_is_open(self) -> None:
        bench = _Bench(weapon=1, shield=1, ball=1)
        bench.call("spawn")
        box, frame, text = (_Bench.ACTOR + i * 0x200 for i in range(3))
        state = bench.layout.state_address
        self.assertEqual(bench.memory.read32(state + ts.STATE_FRAME_ACTOR), frame)
        self.assertEqual(bench.memory.read32(state + ts.STATE_BOX_ACTOR), box)
        text_flags, frame_flags = text + 0xF4 + 0x14, frame + 0xF4 + 0x14
        bench.memory.write8(text_flags, 0x01)                       # an unrelated bit must survive
        update = lambda: bench.call("update", text + 0x20, text + 0xDC, text + 0xF4)  # noqa: E731
        # no window: shown
        update()
        self.assertEqual((bench.memory.read16(text_flags), bench.memory.read16(frame_flags)), (0x01, 0))
        self.assertEqual(bench.memory.read16(box + ts.BOX_WIDTH_OFFSET), ts.BOX_W)
        # a window opens (the list head is a pointer): everything hides, the levels still track
        bench.memory.write32(ts.WINDOW_LIST_HEAD_ADDRESS, 0x8008_20A8)
        bench.memory.write8(ts.BALL_LEVEL_ADDRESS, 2)
        update()
        self.assertEqual((bench.memory.read16(text_flags), bench.memory.read16(frame_flags)), (0x81, 0x80))
        self.assertEqual(bench.memory.read16(box + ts.BOX_WIDTH_OFFSET), 0)
        self.assertEqual(bench.slot(2), "+2")
        # the last window closes: everything shows again
        bench.memory.write32(ts.WINDOW_LIST_HEAD_ADDRESS, 0)
        update()
        self.assertEqual((bench.memory.read16(text_flags), bench.memory.read16(frame_flags)), (0x01, 0))
        self.assertEqual(bench.memory.read16(box + ts.BOX_WIDTH_OFFSET), ts.BOX_W)
        # frame / box never allocated: the text still hides, nothing else is touched
        bench.memory.write32(state + ts.STATE_FRAME_ACTOR, 0)
        bench.memory.write32(state + ts.STATE_BOX_ACTOR, 0)
        bench.memory.write32(ts.WINDOW_LIST_HEAD_ADDRESS, 0x8008_20A8)
        update()
        self.assertEqual((bench.memory.read16(text_flags), bench.memory.read16(frame_flags)), (0x81, 0))
        self.assertEqual(bench.memory.read16(box + ts.BOX_WIDTH_OFFSET), ts.BOX_W)
        self.assertEqual(bench.memory.read32(0xC), 0)               # no stray write through a null actor

    def test_spawn_makes_the_box_the_frame_and_the_text_once(self) -> None:
        bench = _Bench(weapon=1, shield=2, ball=3)
        self.assertEqual(bench.call("spawn"), 0)
        names = [c[0] for c in bench.calls]
        self.assertEqual(names, ["allocate", "register_actor", "allocate", "register", "build_chain", "allocate", "register"])
        box, frame, text = (_Bench.ACTOR + i * 0x200 for i in range(3))
        # the box: flags 1, fields, the tile class, no updater
        self.assertEqual(bench.calls[0][1], ts.BOX_ACTOR_FLAGS)
        self.assertEqual(bench.calls[1][1:3], (box, ts.TILE_CLASS_ADDRESS))
        self.assertEqual(bench.memory.read32(box + 0x10), 0)
        self.assertEqual((bench.memory.read16(box + 0x2C), bench.memory.read16(box + 0x2E)), (ts.BOX_X, ts.BOX_Y))
        self.assertEqual((bench.memory.read16(box + 0x30), bench.memory.read16(box + 0x32)), (ts.BOX_W, ts.BOX_H))
        self.assertEqual(bench.memory.read16(box + 0x36), 1)
        self.assertEqual(bench.memory.read32(box + 0x28), ts.BOX_RGB)
        # the frame: a chain actor at the frame anchor over the frame records, no updater
        self.assertEqual(bench.calls[2][1], ts.ACTOR_FLAGS)
        self.assertEqual(bench.calls[3][1:3], (frame + 0x20, ts.RENDER_2D_CLASS_ADDRESS))
        self.assertEqual(bench.memory.read32(frame + 0x10), 0)
        self.assertEqual((bench.memory.read16(frame + 0xDC + 2), bench.memory.read16(frame + 0xDC + 6)), (ts.FRAME_ANCHOR_X, ts.FRAME_ANCHOR_Y))
        self.assertEqual(bench.memory.read16(frame + 0xDC + 0xA), 0)
        self.assertEqual(bench.memory.read32(frame + 0xF4 + 8), bench.layout.frame_chain_address)
        # the text: built first (cache holds the levels), then its actor with the updater, remembered
        self.assertEqual(bench.calls[4][1:3], (bench.layout.chain_address, bench.layout.text_address))
        self.assertEqual((bench.slot(0), bench.slot(1), bench.slot(2)), ("+10", "+20", "+3"))
        self.assertEqual(bench.state8(ts.STATE_CACHE + 3), 1)
        self.assertEqual(bench.memory.read32(text + 0x10), bench.natives.addresses["update"])
        self.assertEqual((bench.memory.read16(text + 0xDC + 2), bench.memory.read16(text + 0xDC + 6)), (ts.TEXT_ANCHOR_X, ts.TEXT_ANCHOR_Y))
        self.assertEqual(bench.memory.read32(text + 0xF4 + 8), bench.layout.chain_address)
        self.assertEqual(bench.memory.read32(bench.layout.state_address + ts.STATE_ACTOR), text)
        for actor in (frame, text):
            spr = actor + 0xF4
            self.assertEqual((bench.memory.read16(spr + 0x1C), bench.memory.read16(spr + 0x1E)), (0x1000, 0x1000))
            self.assertEqual(bench.memory.read32(spr + 0xC), ts.STRIP_RGB)
            self.assertEqual((bench.memory.read16(spr + 0x10), bench.memory.read16(spr + 0x14)), (0, 0))
        # a second scene load with the text actor still alive: nothing new
        bench.calls.clear()
        self.assertEqual(bench.call("spawn"), 0)
        self.assertEqual(bench.calls, [])
        # the pool was rebuilt (the slot zeroed): everything respawns
        bench.memory.load_bytes(text, bytes(0x124))
        bench.calls.clear()
        bench.call("spawn")
        self.assertEqual([c[0] for c in bench.calls], ["allocate", "register_actor", "allocate", "register", "build_chain", "allocate", "register"])
        self.assertEqual(bench.memory.read32(bench.layout.state_address + ts.STATE_ACTOR), _Bench.ACTOR + 5 * 0x200)
        # a dry pool: nothing spawns, nothing crashes, and the next scene tries again
        bench = _Bench()
        bench.stubs[ts.ALLOCATE_ACTOR_ADDRESS] = lambda cpu: cpu.registers.__setitem__(2, 0)
        self.assertEqual(bench.call("spawn"), 0)
        self.assertEqual([c[0] for c in bench.calls], ["build_chain"])
        self.assertEqual(bench.memory.read32(bench.layout.state_address + ts.STATE_ACTOR), 0)
        self.assertEqual(bench.memory.read32(bench.layout.state_address + ts.STATE_FRAME_ACTOR), 0)
        self.assertEqual(bench.memory.read32(bench.layout.state_address + ts.STATE_BOX_ACTOR), 0)

    def test_trampoline_spawns_then_runs_the_script_with_the_arguments_intact(self) -> None:
        bench = _Bench(weapon=1)
        bench.call("trampoline", 0x8008_2A38, 0x8001_D07C)
        names = [c[0] for c in bench.calls]
        self.assertEqual(names[-1], "run_script")
        self.assertEqual(bench.calls[-1][1:3], (0x8008_2A38, 0x8001_D07C))
        self.assertIn("allocate", names)


class TestStripRecords(unittest.TestCase):
    def test_records(self) -> None:
        pieces = dict(ts.iter_town_file_patches())
        layout = ts.build_layout()
        for address in (ts.CANVAS_ADDRESS, layout.state_address, layout.text_address, layout.chain_address,
                        layout.frame_chain_address, layout.natives_address, ts.SCENE_RUNNER_TAIL_JAL_ADDRESS):
            self.assertIn(town_shop.town_runtime_to_file_offset(address), pieces)
        self.assertEqual(pieces[town_shop.town_runtime_to_file_offset(ts.CANVAS_ADDRESS)], bytes(4))
        raw = ts.iter_town_status_strip_raw_patches()
        self.assertGreater(len(raw), 6)
        ppf = bytearray()
        ts.append_town_status_strip_ppf_records(ppf)
        self.assertGreater(len(ppf), 1000)


if __name__ == "__main__":
    unittest.main()
