"""The forced-trap stub: a mailbox byte plants and springs a trap on Koh.

The stub is interposed on the receive dispatcher's hook site, so every test
here also pins the site's contract: `s1` is Koh's actor, the no-request path
hands control to the receive dispatcher with the caller's `ra` intact, and
every other path returns the displaced load `lhu v1,0xA2(s1)` itself.

The planted trap must be a REAL one - the roller's exact field recipe -
because the handlers read it back through the slot argument (the bomb's
deferred explosion object reads x/y/height frames after the handler
returned, which is why the first fake-and-scrub build detonated silently at
tile (7, 0x15): the record's +0x6/+0x7 are coordinates, not id/category).
"""

import struct
import unittest
from pathlib import Path

from .. import patch
from . import mips_sim


KOH_ACTOR = 0x8009_C000
KOH_OBJECT = 0x8009_D000
KOH_X = 22
KOH_Y = 37
GROUND_HEIGHT = 0x0123
# The pending-event halfword `+0xA2`. The default carries a bit that is not
# an event the stub cares about, so the displaced load stays meaningfully
# asserted while nothing is pending.
IDLE_EVENTS = 0x0004
PICKUP_PENDING = 0x0004 | patch.ACTOR_EVENT_PICKUP

# Real table values from the live dump: poison is a plain sprite handle,
# bomb a negative animation pointer, frog carries the 0x20000000 modifier,
# the dud has no sprite at all.
SPRITE_TABLE = {
    6: 0x0000_0000,
    7: 0x800D_F740,
    11: 0x000D_FB98,
    13: 0xA00D_F820,
}


def _descriptor(memory: mips_sim.Memory, slot: int) -> bytes:
    base = patch.TRAP_DESCRIPTOR_ARRAY_ADDRESS + slot * 4
    return bytes(memory.read8(base + index) for index in range(4))


def _record(memory: mips_sim.Memory, slot: int) -> mips_sim.Memory:
    return patch.TRAP_RECORD_ARRAY_ADDRESS + slot * patch.TRAP_RECORD_SIZE


class _Run:
    def __init__(
        self,
        request: int,
        *,
        action_state: int = patch.ACTOR_ACTION_STATE_IDLE,
        bonus_active: int = 0,
        occupied_slots: int = 0,
        trigger_result: int = 1,
        events: int = IDLE_EVENTS,
        trigger_arms_bump: bool = False,
    ) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.FORCED_TRAP_STUB_ADDRESS, patch._build_forced_trap_stub()
        )
        memory.write8(patch.FORCED_TRAP_REQUEST_ADDRESS, request)
        memory.write8(patch.BONUS_ACTIVE_FLAG_ADDRESS, bonus_active)
        memory.write8(KOH_ACTOR + patch.ACTOR_ACTION_STATE_OFFSET, action_state)
        memory.write32(KOH_ACTOR + patch.ACTOR_LINKED_OBJECT_OFFSET, KOH_OBJECT)
        memory.write8(KOH_OBJECT + patch.LINKED_OBJECT_X_OFFSET, KOH_X)
        memory.write8(KOH_OBJECT + patch.LINKED_OBJECT_Y_OFFSET, KOH_Y)
        memory.write8(
            KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET, events & 0xFF
        )
        memory.write8(
            KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET + 1, events >> 8
        )
        for slot in range(occupied_slots):
            memory.write8(
                patch.TRAP_DESCRIPTOR_ARRAY_ADDRESS + slot * 4 + 1, 0x15
            )
        for trap_id, value in SPRITE_TABLE.items():
            memory.write32(
                patch.TRAP_SPRITE_TABLE_ADDRESS + trap_id * 4, value
            )

        self.height_calls: list[tuple[int, int]] = []
        self.mark_calls: list[tuple[int, int, int]] = []
        self.attach_calls: list[tuple[int, int, int]] = []
        self.trigger_calls: list[dict[str, int]] = []
        self.dispatcher_entries = 0

        def ground_height(cpu: mips_sim.Cpu) -> None:
            self.height_calls.append(
                (cpu.registers[4], cpu.registers[5], cpu.registers[6])
            )
            cpu.registers[2] = GROUND_HEIGHT

        def tile_mark(cpu: mips_sim.Cpu) -> None:
            self.mark_calls.append(
                (cpu.registers[4], cpu.registers[5], cpu.registers[6])
            )

        def sprite_attach(cpu: mips_sim.Cpu) -> None:
            self.attach_calls.append(
                (cpu.registers[4], cpu.registers[5], cpu.registers[6])
            )

        def trigger_trap(cpu: mips_sim.Cpu) -> None:
            self.trigger_calls.append(
                {
                    "id": cpu.registers[4],
                    "actor": cpu.registers[5],
                    "slot": cpu.registers[6],
                    "forced": cpu.registers[7],
                }
            )
            if trigger_arms_bump:
                # What the real bump handler does: arm the deferred event
                # and increment the outstanding-animation counter.
                address = KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET
                armed = memory.read16(address) | patch.ACTOR_EVENT_BUMP
                memory.write8(address, armed & 0xFF)
                memory.write8(address + 1, armed >> 8)
            cpu.registers[2] = trigger_result

        def receive_dispatcher(cpu: mips_sim.Cpu) -> None:
            self.dispatcher_entries += 1
            cpu.registers[2] = 0

        self.memory = memory
        self.cpu = mips_sim.Cpu(
            memory,
            {
                patch.TRAP_GROUND_HEIGHT_ADDRESS: ground_height,
                patch.TRAP_TILE_MARK_ADDRESS: tile_mark,
                patch.TRAP_SPRITE_ATTACH_ADDRESS: sprite_attach,
                patch.TRAP_DISPATCH_ADDRESS: trigger_trap,
                patch.RECEIVE_ITEM_DISPATCHER_ADDRESS: receive_dispatcher,
            },
        )
        self.cpu.registers[17] = KOH_ACTOR  # s1, the site's contract
        self.cpu.run(patch.FORCED_TRAP_STUB_ADDRESS)

    @property
    def request_byte(self) -> int:
        return self.memory.read8(patch.FORCED_TRAP_REQUEST_ADDRESS)

    @property
    def displaced_load(self) -> int:
        return self.cpu.registers[3]  # v1


class TestForcedTrapStub(unittest.TestCase):
    def test_no_request_tail_jumps_to_the_receive_dispatcher(self) -> None:
        run = _Run(0)
        self.assertEqual(run.dispatcher_entries, 1)
        self.assertEqual(run.trigger_calls, [])

    def test_a_request_plants_a_native_trap_and_springs_it(self) -> None:
        run = _Run(11)  # poison, a plain-handle sprite
        # The descriptor: id, category 0x15, quality, revealed.
        self.assertEqual(
            _descriptor(run.memory, 0),
            bytes((11, 0x15, patch.FORCED_TRAP_QUALITY, 0)),
        )
        # The record, exactly as the roller writes it.
        record = _record(run.memory, 0)
        self.assertEqual(run.memory.read8(record + 0x6), KOH_X)
        self.assertEqual(run.memory.read8(record + 0x7), KOH_Y)
        self.assertEqual(run.memory.read16(record + 0x10), GROUND_HEIGHT)
        self.assertEqual(run.memory.read16(record + 0x12), GROUND_HEIGHT)
        # Plain handle: +0x8 = value | 0x80000000, flags 0 (visible now).
        self.assertEqual(run.memory.read32(record + 0x8), 0x800D_FB98)
        self.assertEqual(run.memory.read16(record + 0x14), 0)
        self.assertEqual(run.attach_calls, [])
        # The helpers saw the roller's own arguments - INCLUDING the height
        # probe's third one. a2 is the Z reference; leaving it to whatever
        # the hook site held is what fired the bomb into nowhere twice, and
        # only the bomb reads the height back, so nothing else caught it.
        self.assertEqual(
            run.height_calls,
            [(
                KOH_X * 64 + 0x20,
                KOH_Y * 64 + 0x20,
                patch.TRAP_GROUND_HEIGHT_PROBE_Z & 0xFFFF_FFFF,
            )],
        )
        self.assertEqual(
            run.mark_calls, [(KOH_X, KOH_Y, patch.TRAP_TILE_MARK_BIT)]
        )
        # Sprung forced, on the planted slot, on Koh.
        self.assertEqual(
            run.trigger_calls,
            [{"id": 11, "actor": KOH_ACTOR, "slot": 0, "forced": 1}],
        )
        # Consumed; delivery skipped this frame; displaced load honoured.
        self.assertEqual(run.request_byte, 0)
        self.assertEqual(run.dispatcher_entries, 0)
        self.assertEqual(run.displaced_load, IDLE_EVENTS)

    def test_the_trap_is_not_scrubbed(self) -> None:
        # The persistence IS the bomb fix: its explosion object reads the
        # record on later frames. The trap stays armed on the floor.
        run = _Run(11)
        self.assertEqual(_descriptor(run.memory, 0)[1], 0x15)

    def test_an_animated_trap_is_attached_and_planted_hidden(self) -> None:
        run = _Run(7)  # bomb
        record = _record(run.memory, 0)
        self.assertEqual(
            run.attach_calls, [(record, SPRITE_TABLE[7], 0)]
        )
        self.assertEqual(run.memory.read16(record + 0x14), 0x0840)
        self.assertEqual(run.memory.read32(record + 0x8), 0)
        self.assertEqual(len(run.trigger_calls), 1)

    def test_the_20000000_animation_modifier_sets_flag_0x100(self) -> None:
        run = _Run(13)  # frog
        record = _record(run.memory, 0)
        self.assertEqual(run.memory.read16(record + 0x14), 0x0940)

    def test_a_spriteless_trap_plants_with_no_sprite(self) -> None:
        run = _Run(6)  # dud
        record = _record(run.memory, 0)
        self.assertEqual(run.attach_calls, [])
        self.assertEqual(run.memory.read32(record + 0x8), 0)
        self.assertEqual(run.memory.read16(record + 0x14), 0)
        self.assertEqual(len(run.trigger_calls), 1)

    def test_occupied_slots_are_skipped(self) -> None:
        run = _Run(11, occupied_slots=5)
        self.assertEqual(_descriptor(run.memory, 5)[0], 11)
        self.assertEqual(run.trigger_calls[0]["slot"], 5)

    def test_a_full_trap_table_defers(self) -> None:
        run = _Run(11, occupied_slots=patch.TRAP_SLOT_COUNT)
        self.assertEqual(run.trigger_calls, [])
        self.assertEqual(run.mark_calls, [])
        self.assertEqual(run.request_byte, 11)
        self.assertEqual(run.displaced_load, IDLE_EVENTS)

    def test_a_refused_trigger_still_consumes_the_request(self) -> None:
        # Once planted, the trap is armed underfoot either way - that is the
        # delivery. No retry loop that would plant a second trap.
        run = _Run(11, trigger_result=0)
        self.assertEqual(len(run.trigger_calls), 1)
        self.assertEqual(run.request_byte, 0)
        self.assertEqual(_descriptor(run.memory, 0)[1], 0x15)

    def test_koh_not_in_ordinary_idle_defers_untouched(self) -> None:
        # 0x17 (post-fidget idle) is deliberately excluded, mirroring the
        # receive dispatcher's own delivery guard.
        run = _Run(11, action_state=0x17)
        self.assertEqual(run.trigger_calls, [])
        self.assertEqual(run.mark_calls, [])
        self.assertEqual(_descriptor(run.memory, 0), bytes(4))
        self.assertEqual(run.request_byte, 11)
        self.assertEqual(run.dispatcher_entries, 0)
        self.assertEqual(run.displaced_load, IDLE_EVENTS)

    def test_a_pending_pickup_defers(self) -> None:
        """No trap on a frame that already has an actor event queued.

        The marker's own pickup presentation is queued in `+0xA2` bit 0x80
        by the payload and consumed by the hook site immediately after this
        stub returns. Springing into that window puts two events in one
        halfword and lets one clobber the other.
        """

        run = _Run(11, events=PICKUP_PENDING)
        self.assertEqual(run.trigger_calls, [])
        self.assertEqual(run.mark_calls, [])
        self.assertEqual(run.request_byte, 11)
        self.assertEqual(run.displaced_load, PICKUP_PENDING)

    def test_a_deferred_bump_event_gets_koh_out_of_idle(self) -> None:
        """The bump semi-softlock, fixed.

        The neutral handler only dispatches `+0xA2` events when the action
        state is neither 0x0E nor 0x17 - and 0x0E is what this stub demands
        before firing. A bump armed from idle would sit there with the
        outstanding-animation counter already incremented and nothing ever
        starting the animation.
        """

        run = _Run(14, trigger_arms_bump=True)
        self.assertEqual(len(run.trigger_calls), 1)
        self.assertEqual(
            run.memory.read8(KOH_ACTOR + patch.ACTOR_ACTION_STATE_OFFSET),
            patch.ACTOR_ACTION_STATE_BUMPED,
        )

    def test_a_trap_that_arms_nothing_leaves_the_action_state_alone(self) -> None:
        run = _Run(11)  # poison acts immediately; nothing deferred
        self.assertEqual(len(run.trigger_calls), 1)
        self.assertEqual(
            run.memory.read8(KOH_ACTOR + patch.ACTOR_ACTION_STATE_OFFSET),
            patch.ACTOR_ACTION_STATE_IDLE,
        )

    def test_the_bonus_floor_defers(self) -> None:
        run = _Run(11, bonus_active=1)
        self.assertEqual(run.trigger_calls, [])
        self.assertEqual(run.mark_calls, [])
        self.assertEqual(run.request_byte, 11)

    def test_an_out_of_range_id_is_dropped_without_planting(self) -> None:
        run = _Run(20)
        self.assertEqual(run.trigger_calls, [])
        self.assertEqual(run.mark_calls, [])
        self.assertEqual(_descriptor(run.memory, 0), bytes(4))
        self.assertEqual(run.request_byte, 0)
        self.assertEqual(run.dispatcher_entries, 0)

    def test_the_highest_real_id_is_accepted(self) -> None:
        run = _Run(19)  # monster den (quality byte = spawn count)
        self.assertEqual(len(run.trigger_calls), 1)
        self.assertEqual(run.trigger_calls[0]["id"], 19)


class TestPickupMessageGate(unittest.TestCase):
    """Suppress the "Found ..." box for a disguised trap, and only that.

    The pickup message is the one dialogue that appears after the player
    has committed to taking the item, so it is the only one cut. The
    step-on message, the at-feet name, the description box and the
    `Strange...` shop presentation are different code entirely and cannot
    be reached from here - what these tests pin is that the gate fires on
    exactly one shape of placement and leaves every other pickup alone.
    """

    MARKER = 0x8001_0298   # where the payload's collect hook copies it

    def _run(
        self,
        *,
        form: int,
        status: int = patch.MARKER_STATUS,
        slot: int = 0,
        page_floor: int = 3,
        current_floor: int = 3,
        magic: int = patch.FLOOR_PAGE_MAGIC,
        descriptor: int | None = None,
        events: int = IDLE_EVENTS | patch.ACTOR_EVENT_PICKUP,
    ) -> mips_sim.Memory:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.FORCED_TRAP_STUB_ADDRESS, patch._build_forced_trap_stub()
        )
        memory.write8(KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET, events & 0xFF)
        memory.write8(
            KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET + 1, events >> 8
        )
        memory.write32(
            KOH_ACTOR + patch.ACTOR_PICKUP_DESCRIPTOR_OFFSET,
            self.MARKER if descriptor is None else descriptor,
        )
        memory.write8(self.MARKER + 2, slot)
        memory.write8(self.MARKER + 3, status)
        memory.write32(patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_HEADER_OFFSET, magic)
        memory.write8(
            patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_HEADER_OFFSET + 4,
            page_floor,
        )
        memory.write8(0x8008_146C, current_floor)
        memory.write8(
            patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_RECORDS_OFFSET + slot * 3 + 2,
            form,
        )
        # No trap request: the gate must work before the client has had any
        # chance to write one, so this exercises it on its own.
        memory.write8(patch.FORCED_TRAP_REQUEST_ADDRESS, 0)
        cpu = mips_sim.Cpu(
            memory,
            {patch.RECEIVE_ITEM_DISPATCHER_ADDRESS: lambda c: None},
        )
        cpu.registers[17] = KOH_ACTOR
        cpu.run(patch.FORCED_TRAP_STUB_ADDRESS)
        return memory

    def _pickup_still_queued(self, memory: mips_sim.Memory) -> bool:
        events = memory.read16(KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET)
        return bool(events & patch.ACTOR_EVENT_PICKUP)

    def test_a_disguised_trap_pickup_is_silenced(self) -> None:
        memory = self._run(form=patch.FLOOR_PAGE_FORM_TRAP)
        self.assertFalse(self._pickup_still_queued(memory))

    def test_an_honest_local_placement_keeps_its_message(self) -> None:
        memory = self._run(form=0)
        self.assertTrue(self._pickup_still_queued(memory))

    def test_a_remote_placement_keeps_its_message(self) -> None:
        memory = self._run(form=patch.FLOOR_PAGE_FORM_REMOTE)
        self.assertTrue(self._pickup_still_queued(memory))

    def test_the_second_slot_is_read_independently(self) -> None:
        self.assertFalse(
            self._pickup_still_queued(
                self._run(form=patch.FLOOR_PAGE_FORM_TRAP, slot=1)
            )
        )
        self.assertTrue(self._pickup_still_queued(self._run(form=0, slot=1)))

    def test_a_non_marker_pickup_is_never_touched(self) -> None:
        # An ordinary floor item picked up while a trap sits elsewhere on
        # the page: its descriptor is not a marker, so the gate must not
        # even look at the record.
        memory = self._run(form=patch.FLOOR_PAGE_FORM_TRAP, status=0x00)
        self.assertTrue(self._pickup_still_queued(memory))

    def test_a_stale_page_falls_through_to_the_vanilla_message(self) -> None:
        for kwargs in (
            {"page_floor": 2, "current_floor": 3},
            {"magic": 0xDEAD_BEEF},
            {"slot": patch.MARKER_SLOT_COUNT},
            {"descriptor": 0},
        ):
            with self.subTest(**kwargs):
                memory = self._run(form=patch.FLOOR_PAGE_FORM_TRAP, **kwargs)
                self.assertTrue(self._pickup_still_queued(memory))

    def test_no_queued_pickup_means_nothing_to_do(self) -> None:
        memory = self._run(form=patch.FLOOR_PAGE_FORM_TRAP, events=IDLE_EVENTS)
        self.assertEqual(
            memory.read16(KOH_ACTOR + patch.ACTOR_PICKUP_FLAG_OFFSET),
            IDLE_EVENTS,
        )


class TestForcedTrapPlacement(unittest.TestCase):
    def test_the_stub_fits_and_rides_every_floor_page_sector(self) -> None:
        stub = patch._build_forced_trap_stub()
        self.assertLessEqual(len(stub), patch.FORCED_TRAP_STUB_CAPACITY)
        placements = [
            patch.LocationPlacement("Gold", "Koh", False)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        start = patch.FORCED_TRAP_STUB_OFFSET
        self.assertEqual(block[start : start + len(stub)], stub)
        window_start = start - patch.FLOOR_PAGE_WINDOW_OFFSET
        for sector in patch.build_floor_page_sectors(block, placements):
            self.assertEqual(sector[window_start : window_start + len(stub)], stub)

    def test_build_player_ppf_retargets_the_receive_hook_in_place(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        placements = [
            patch.LocationPlacement("Gold", "Koh", False)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        player = patch.build_player_ppf(base_patch, block, "test")
        covering = []
        cursor = patch.PPF_HEADER_SIZE
        while cursor < len(player):
            offset, length = struct.unpack_from("<IB", player, cursor)
            body = cursor + 5
            if offset <= patch.RECEIVE_HOOK_RAW_OFFSET < offset + length:
                covering.append(
                    player[
                        body
                        + patch.RECEIVE_HOOK_RAW_OFFSET
                        - offset : body
                        + patch.RECEIVE_HOOK_RAW_OFFSET
                        - offset
                        + 4
                    ]
                )
            cursor = body + length
        # Edited in place: one record, already the base patch's, now carrying
        # the stub's jal - never a second record for covered ground.
        self.assertEqual(len(covering), 1)
        expected = struct.pack(
            "<I", patch._j(0x03, patch.FORCED_TRAP_STUB_ADDRESS)
        )
        self.assertEqual(covering[0], expected)

    def test_the_base_patch_still_carries_the_expected_hook(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        self.assertEqual(
            patch._read_ppf_word(base_patch, patch.RECEIVE_HOOK_RAW_OFFSET),
            patch.RECEIVE_HOOK_ORIGINAL_WORD,
        )


if __name__ == "__main__":
    unittest.main()
