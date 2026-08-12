"""Tower resume, stage 1: putting a saved run back into Koh.

The client can already snapshot everything else a tower run needs - inventory,
the familiar (its collar bit is a flag on its inventory descriptor) and the
floor number are all inside the 24 KiB checkpoint block. Koh's stats are not,
and the game's own copy at `0x80012194` is write-only: it is refreshed from
the live struct on tower entry and on every floor change and never read back.

So a resume stages the record where the game does not write, and this copies
it into Koh at the one seam where he reliably exists. `docs/systems/
tower-continue.md` owns the measurements.
"""

import struct
import unittest

from .. import patch
from . import mips_sim


def _record(level: int, hp: int, max_hp: int) -> bytes:
    """A recognisable stand-in for a captured `UnitStats`."""

    record = bytearray(range(0x40, 0x40 + patch.TOWER_RESUME_RECORD_SIZE))
    record[patch.KOH_UNIT_STATS_LEVEL_OFFSET] = level
    record[0x28] = hp
    record[0x29] = max_hp
    return bytes(record)


class _WrapperRun:
    """Runs the monster-levelling wrapper with a carrier staged (or not)."""

    def __init__(self, record: bytes | None, *, pending_level: int = 0) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.SHORTCUT_LEVEL_GRANT_ADDRESS, patch._build_shortcut_level_grant()
        )
        if record is not None:
            memory.load_bytes(patch.TOWER_RESUME_CARRIER_ADDRESS, record)
            memory.write32(
                patch.TOWER_RESUME_CARRIER_ADDRESS + patch.TOWER_RESUME_MAGIC_OFFSET,
                patch.TOWER_RESUME_MAGIC,
            )
        # Koh as the floor build leaves him: level 1, and a struct that shares
        # no bytes with the staged record.
        memory.load_bytes(
            patch.KOH_UNIT_STATS_ADDRESS, bytes(patch.TOWER_RESUME_RECORD_SIZE)
        )
        memory.write8(
            patch.KOH_UNIT_STATS_ADDRESS + patch.KOH_UNIT_STATS_LEVEL_OFFSET, 1
        )
        memory.write8(patch.SHORTCUT_PENDING_LEVEL_ADDRESS, pending_level)

        self.memory = memory
        self.level_ups = 0
        self.levelled_monsters = 0
        self.cpu = mips_sim.Cpu(
            memory,
            {
                patch.LEVEL_MONSTERS_ADDRESS: self._level_monsters,
                patch.LEVEL_UP_ADDRESS: self._level_up,
            },
        )
        self.cpu.run(patch.SHORTCUT_LEVEL_GRANT_ADDRESS)

    def _level_monsters(self, cpu: mips_sim.Cpu) -> None:
        self.levelled_monsters += 1

    def _level_up(self, cpu: mips_sim.Cpu) -> None:
        # The real routine bumps +0x11 and applies the growth table.
        address = cpu.registers[4] + patch.KOH_UNIT_STATS_LEVEL_OFFSET
        self.memory.write8(address, self.memory.read8(address) + 1)
        self.level_ups += 1

    @property
    def koh(self) -> bytes:
        return bytes(
            self.memory.read8(patch.KOH_UNIT_STATS_ADDRESS + i)
            for i in range(patch.TOWER_RESUME_RECORD_SIZE)
        )

    @property
    def magic(self) -> int:
        return self.memory.read32(
            patch.TOWER_RESUME_CARRIER_ADDRESS + patch.TOWER_RESUME_MAGIC_OFFSET
        )


class TestTowerResumeApply(unittest.TestCase):
    def test_a_staged_record_lands_in_koh_whole(self) -> None:
        record = _record(level=33, hp=57, max_hp=120)
        run = _WrapperRun(record)
        self.assertEqual(run.koh, record)
        self.assertEqual(
            run.koh[patch.KOH_UNIT_STATS_LEVEL_OFFSET], 33, "level did not resume"
        )
        self.assertEqual((run.koh[0x28], run.koh[0x29]), (57, 120), "HP did not resume")

    def test_it_is_one_shot(self) -> None:
        """The wrapper runs many times per floor - both real call sites are
        loop bodies. A record applied on every pass would undo whatever the
        player did on the floor it resumed onto."""

        record = _record(level=33, hp=57, max_hp=120)
        run = _WrapperRun(record)
        self.assertEqual(run.magic, 0, "the carrier was not consumed")

        # A second pass over the same memory must change nothing.
        run.memory.write8(
            patch.KOH_UNIT_STATS_ADDRESS + patch.KOH_UNIT_STATS_LEVEL_OFFSET, 34
        )
        mips_sim.Cpu(
            run.memory,
            {
                patch.LEVEL_MONSTERS_ADDRESS: lambda cpu: None,
                patch.LEVEL_UP_ADDRESS: lambda cpu: None,
            },
        ).run(patch.SHORTCUT_LEVEL_GRANT_ADDRESS)
        self.assertEqual(
            run.memory.read8(
                patch.KOH_UNIT_STATS_ADDRESS + patch.KOH_UNIT_STATS_LEVEL_OFFSET
            ),
            34,
            "a spent record was applied a second time",
        )

    def test_no_carrier_leaves_koh_alone(self) -> None:
        """An unwitnessed carrier is the normal case: every ordinary floor."""

        run = _WrapperRun(None)
        self.assertEqual(
            run.koh[patch.KOH_UNIT_STATS_LEVEL_OFFSET], 1, "Koh was rewritten"
        )
        self.assertEqual(set(run.koh) - {0, 1}, set())

    def test_a_record_without_its_magic_is_ignored(self) -> None:
        """The magic is written AFTER the record, so a client that died
        mid-write leaves bytes with no witness. That has to read as "nothing
        pending", never as a half-applied Koh."""

        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.SHORTCUT_LEVEL_GRANT_ADDRESS, patch._build_shortcut_level_grant()
        )
        memory.load_bytes(
            patch.TOWER_RESUME_CARRIER_ADDRESS, _record(level=33, hp=57, max_hp=120)
        )  # no magic
        memory.write8(
            patch.KOH_UNIT_STATS_ADDRESS + patch.KOH_UNIT_STATS_LEVEL_OFFSET, 1
        )
        memory.write8(patch.SHORTCUT_PENDING_LEVEL_ADDRESS, 0)
        mips_sim.Cpu(
            memory,
            {
                patch.LEVEL_MONSTERS_ADDRESS: lambda cpu: None,
                patch.LEVEL_UP_ADDRESS: lambda cpu: None,
            },
        ).run(patch.SHORTCUT_LEVEL_GRANT_ADDRESS)
        self.assertEqual(
            memory.read8(
                patch.KOH_UNIT_STATS_ADDRESS + patch.KOH_UNIT_STATS_LEVEL_OFFSET
            ),
            1,
        )

    def test_the_displaced_call_still_runs(self) -> None:
        """This is a WRAPPER first. The monster-levelling call it was planted
        over has to happen whether or not a resume is pending, or every
        monster on the floor arrives at level 1."""

        for label, record in (("with a resume", _record(33, 57, 120)), ("without", None)):
            with self.subTest(case=label):
                self.assertEqual(_WrapperRun(record).levelled_monsters, 1)


class TestTowerResumeAndTheShortcutGrant(unittest.TestCase):
    """The two features share one routine and one seam."""

    def test_the_shortcut_grant_still_works(self) -> None:
        run = _WrapperRun(None, pending_level=10)
        self.assertEqual(run.level_ups, 9, "level 1 -> 10 needs nine level_ups")
        self.assertEqual(
            run.memory.read8(patch.SHORTCUT_PENDING_LEVEL_ADDRESS),
            0,
            "the pending-level byte was not consumed",
        )

    def test_a_resume_is_not_levelled_on_top_of(self) -> None:
        """Never both in practice, but if they ever collided the resumed level
        must win: the grant's own "already at or above the target" test sees
        the resumed value because the resume runs first."""

        run = _WrapperRun(_record(level=33, hp=57, max_hp=120), pending_level=10)
        self.assertEqual(run.level_ups, 0)
        self.assertEqual(run.koh[patch.KOH_UNIT_STATS_LEVEL_OFFSET], 33)


class TestTowerResumePlacement(unittest.TestCase):
    def test_the_carrier_is_in_certified_scratch_and_the_checkpoint(self) -> None:
        # Certified untouched by the per-bucket write watch, clear of the
        # marker description buffer at 0x80015E00, and below 0x80016000 so a
        # checkpoint carries it and a restore rolls it back with everything
        # else.
        self.assertGreaterEqual(patch.TOWER_RESUME_CARRIER_ADDRESS, 0x8001_57B8)
        self.assertLessEqual(
            patch.TOWER_RESUME_CARRIER_ADDRESS + patch.TOWER_RESUME_MAGIC_OFFSET + 4,
            0x8001_5E00,
        )

    def test_it_does_not_collide_with_the_other_save_tail_tenants(self) -> None:
        carrier = range(
            patch.TOWER_RESUME_CARRIER_ADDRESS,
            patch.TOWER_RESUME_CARRIER_ADDRESS + patch.TOWER_RESUME_MAGIC_OFFSET + 4,
        )
        for name, address, size in (
            ("ADSV", patch.PERSISTENT_STATE_ADDRESS, patch.PERSISTENT_STATE_SIZE),
            ("shortcut carrier", patch.SHORTCUT_PENDING_LEVEL_ADDRESS, 4),
            ("send tokens", patch.SEND_TOKEN_COUNT_ADDRESS, 8),
            ("send-token bank", patch.SEND_TOKEN_BANKED_ADDRESS, 4),
        ):
            with self.subTest(neighbour=name):
                self.assertFalse(
                    set(carrier) & set(range(address, address + size)),
                    f"the tower-resume carrier overlaps {name}.",
                )

    def test_the_record_matches_the_measured_extent(self) -> None:
        # 0x4C is where the live struct goes back to pointers; the game's own
        # mirror agrees with it byte for byte up to there and diverges after.
        self.assertEqual(patch.TOWER_RESUME_RECORD_SIZE, 0x4C)
        self.assertEqual(patch.TOWER_RESUME_RECORD_WORDS, 19)
        self.assertEqual(patch.KOH_UNIT_STATS_ADDRESS, 0x8008_34B8)

    def test_the_wrapper_fits_its_slot(self) -> None:
        grant = patch._build_shortcut_level_grant()
        capacity = patch.INVENTORY_HUD_CODE_OFFSET - patch.SHORTCUT_LEVEL_GRANT_OFFSET
        self.assertLessEqual(
            len(grant),
            capacity,
            "The grant and the resume apply share this slot and fill it "
            "exactly; anything added here needs a new home.",
        )

    def test_it_rides_the_built_seed_block(self) -> None:
        placements = [
            patch.LocationPlacement("Gold", "Koh", False)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        grant = patch._build_shortcut_level_grant()
        self.assertEqual(
            block[
                patch.SHORTCUT_LEVEL_GRANT_OFFSET :
                patch.SHORTCUT_LEVEL_GRANT_OFFSET + len(grant)
            ],
            grant,
        )
        # The carrier load is what the client has to agree with.
        self.assertIn(
            struct.pack("<I", patch._i(0x0F, 0, 11, patch._upper(
                patch.TOWER_RESUME_CARRIER_ADDRESS))),
            grant,
        )


if __name__ == "__main__":
    unittest.main()


class TestResumeWarpStub(unittest.TestCase):
    """Stage 2a: the angel's own scene transition, diverted.

    The first attempt at this crashed the game on NEW GAME, at the end of the
    angel's dialogue, because its 104-byte wrapper lived in the front-menu
    package - unreachable code, but NOT RESIDENT IN TOWN, where those addresses
    hold the outdoor town overlay. Residency and reachability are different
    questions and only the second one had been asked.
    """

    def test_it_fits_the_retired_driver_tail(self) -> None:
        from .. import town_warp

        stub = town_warp._build_resume_warp_stub()
        capacity = (
            town_warp.RESUME_WARP_STUB_END_ADDRESS
            - town_warp.RESUME_WARP_STUB_ADDRESS
        )
        # 108: the two disabled save entries plus the driver tail. The entries
        # are certified by the same breakpoint that cleared the tail - an
        # entry's only job is to call create_memory_card_screen_actor.
        self.assertEqual(capacity, 108)
        self.assertLessEqual(len(stub), capacity)

    def test_it_lives_in_resident_slus(self) -> None:
        """The whole point. SLUS is never covered by an overlay load."""

        from .. import save_removal, town_warp

        self.assertGreaterEqual(
            town_warp.RESUME_WARP_STUB_ADDRESS, save_removal.SLUS_LOAD_ADDRESS
        )
        self.assertLess(town_warp.RESUME_WARP_STUB_ADDRESS, 0x8008_1800)
        # It DELIBERATELY spans the two disabled save entries as well as the
        # driver tail - 108 bytes - and save_removal must therefore not write
        # its "already done" stubs over them any more. If those stubs come
        # back, they land on this code.
        from .. import save_removal

        # The resume body's own patch lands ON the town entry's address, so
        # compare PAYLOADS: the "already done" stub must not be written at all.
        stub_payload = save_removal.build_completed_save_entry()
        for name, address in (
            ("town save entry", save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS),
            ("tower save entry", save_removal.CARD_DRIVER_TOWER_SAVE_ENTRY_ADDRESS),
        ):
            with self.subTest(entry=name):
                self.assertGreaterEqual(
                    address, town_warp.RESUME_WARP_STUB_ADDRESS
                )
                self.assertLess(address, town_warp.RESUME_WARP_STUB_END_ADDRESS)
        self.assertNotIn(
            stub_payload,
            [payload for _, payload in save_removal.iter_slus_file_patches()],
            "save_removal still writes an 'already done' save-entry stub, "
            "which would land on the resume warp body.",
        )

    def test_the_trampoline_replays_the_words_it_displaces(self) -> None:
        """A wrong offset shows up here rather than as a crash on the disc."""

        from .. import town_warp

        stub = town_warp._build_resume_warp_stub()
        for word in town_warp.TOWN_SCENE_FNO_DISPLACED_WORDS:
            with self.subTest(word=hex(word)):
                self.assertIn(struct.pack("<I", word), stub)
        self.assertIn(
            struct.pack("<I", town_warp._j(
                0x02, town_warp.TOWN_SCENE_FNO_HANDLER_ADDRESS + 8)
            ) if hasattr(town_warp, "_j") else struct.pack(
                "<I", (2 << 26) | (((town_warp.TOWN_SCENE_FNO_HANDLER_ADDRESS + 8) >> 2) & 0x3FFFFFF)
            ),
            stub,
            "the not-pending path must rejoin the vanilla handler past the "
            "two words the trampoline took.",
        )

    def test_it_calls_neither_uncles_trigger_nor_a_hand_rolled_entry(self) -> None:
        """m4 died on the trigger's residency; m5 hand-rolled the entry and got
        a floor with the wrong inventory. m6 uses the load path instead, so
        NONE of the three should appear."""

        from .. import town_warp

        stub = town_warp._build_resume_warp_stub()
        for name, address in (
            ("Uncle's warp trigger", town_warp.UNCLE_WARP_TRIGGER_ADDRESS),
            ("begin_scene_transition", town_warp.BEGIN_SCENE_TRANSITION_FROM_DESCRIPTOR_ADDRESS),
            ("initialize_new_tower_run_state", town_warp.INITIALIZE_NEW_TOWER_RUN_STATE_ADDRESS),
        ):
            for op in (2, 3):
                with self.subTest(callee=name, op=op):
                    self.assertNotIn(
                        struct.pack("<I", (op << 26) | ((address >> 2) & 0x3FFFFFF)),
                        stub,
                    )
        for name, address in (
            ("the SLUS commit helper", town_warp.RESUME_COMMIT_PENDING_ADDRESS),
            ("request_game_mode_overlay", town_warp.REQUEST_GAME_MODE_OVERLAY_ADDRESS),
        ):
            with self.subTest(callee=name):
                self.assertIn(
                    struct.pack("<I", (3 << 26) | ((address >> 2) & 0x3FFFFFF)),
                    stub,
                )

    def test_no_front_menu_package_edits_remain(self) -> None:
        """The reverted wrong turn stays reverted."""

        from .. import town_warp

        self.assertEqual(town_warp.iter_resume_warp_stream_patches(), ())


class TestResumeStubPreservesTheHandlersArgument(unittest.TestCase):
    """The m3 crash, pinned. `a0` is the scene index the handler indexes with."""

    def test_a0_is_untouched_on_the_not_pending_path(self) -> None:
        from .. import town_warp
        from . import mips_sim

        stub = town_warp._build_resume_warp_stub()
        memory = mips_sim.Memory()
        memory.load_bytes(town_warp.RESUME_WARP_STUB_ADDRESS, stub)
        # The vanilla rejoin and Uncle's trigger both end the routine here.
        for address in (
            town_warp.TOWN_SCENE_FNO_HANDLER_ADDRESS + 8,
            town_warp.UNCLE_WARP_TRIGGER_ADDRESS,
        ):
            memory.load_bytes(address, struct.pack("<2I", 0x03E00008, 0))
        memory.write32(
            patch.TOWER_RESUME_CARRIER_ADDRESS
            + patch.TOWER_RESUME_FLOOR_REQUEST_OFFSET,
            0,
        )
        cpu = mips_sim.Cpu(memory)
        cpu.registers[4] = 0x1234_5678          # the scene index
        cpu.run(town_warp.RESUME_WARP_STUB_ADDRESS)
        self.assertEqual(
            cpu.registers[4],
            0x1234_5678,
            "the stub clobbered a0; the angel would transition to the wrong "
            "scene and loop, which is exactly what tower-resume-m3 did.",
        )

    def test_a_pending_request_runs_the_card_loads_own_resume(self) -> None:
        """m6: stop re-implementing the entry, do what the load path does.

        `0x800251A4` - the memory-card module's post-load routine - sets the
        tower floor-state flags, sets the transition bit, calls one self-gating
        SLUS helper and requests game-mode overlay 6. That is the whole tower
        resume, and everything else comes out of the restored block.
        """

        from .. import town_warp
        from . import mips_sim

        memory = mips_sim.Memory()
        memory.load_bytes(
            town_warp.RESUME_WARP_STUB_ADDRESS, town_warp._build_resume_warp_stub()
        )
        memory.write32(
            patch.TOWER_RESUME_CARRIER_ADDRESS
            + patch.TOWER_RESUME_FLOOR_REQUEST_OFFSET,
            1,
        )
        memory.write32(town_warp.RESUME_TRANSITION_BYTE_ADDRESS & ~3, 0)
        seen = []
        cpu = mips_sim.Cpu(
            memory,
            {
                town_warp.RESUME_COMMIT_PENDING_ADDRESS:
                    lambda c: seen.append(("commit", None)),
                town_warp.REQUEST_GAME_MODE_OVERLAY_ADDRESS:
                    lambda c: seen.append(("mode", c.registers[4])),
            },
        )
        cpu.run(town_warp.RESUME_WARP_STUB_ADDRESS)

        self.assertEqual(
            seen,
            [("commit", None), ("mode", town_warp.RESUME_TOWER_GAME_MODE)],
            "the resume must be the SLUS commit helper then game mode 6, in "
            "the load path's order",
        )
        self.assertEqual(
            memory.read8(town_warp.RESUME_TOWER_FLOOR_STATE_ADDRESS)
            | (memory.read8(town_warp.RESUME_TOWER_FLOOR_STATE_ADDRESS + 1) << 8),
            town_warp.RESUME_TOWER_FLOOR_STATE_VALUE,
            "the tower floor-state flags were not set",
        )
        self.assertTrue(
            memory.read8(town_warp.RESUME_TRANSITION_BYTE_ADDRESS)
            & town_warp.RESUME_TRANSITION_BIT,
            "the transition bit was not set",
        )
        self.assertEqual(
            memory.read32(
                patch.TOWER_RESUME_CARRIER_ADDRESS
                + patch.TOWER_RESUME_FLOOR_REQUEST_OFFSET
            ),
            0,
            "the trigger was not consumed",
        )

    def test_it_no_longer_reimplements_the_tower_entry(self) -> None:
        """The scene transition and run-state init belong to the load path now.

        m5 called them directly and reached the floor - but with the intro's
        Pita for an inventory and wrong maximums, because a hand-rolled entry
        only restores what someone remembered to restore.
        """

        from .. import town_warp

        stub = town_warp._build_resume_warp_stub()
        for name, address in (
            ("begin_scene_transition", town_warp.BEGIN_SCENE_TRANSITION_FROM_DESCRIPTOR_ADDRESS),
            ("initialize_new_tower_run_state", town_warp.INITIALIZE_NEW_TOWER_RUN_STATE_ADDRESS),
            ("Uncle's warp trigger", town_warp.UNCLE_WARP_TRIGGER_ADDRESS),
        ):
            with self.subTest(callee=name):
                for op in (2, 3):
                    self.assertNotIn(
                        struct.pack("<I", (op << 26) | ((address >> 2) & 0x3FFFFFF)),
                        stub,
                    )
