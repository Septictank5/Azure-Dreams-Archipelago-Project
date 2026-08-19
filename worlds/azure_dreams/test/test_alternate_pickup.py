import struct
import unittest

from . import mips_sim
from .. import alternate_pickup, patch, save_removal, town_shop


MARKER = bytes(
    (
        alternate_pickup.MARKER_ID,
        alternate_pickup.MARKER_CATEGORY,
        0x00,
        alternate_pickup.MARKER_STATUS,
    )
)
DESCRIPTOR_SCRATCH = 0x801F_A000
OUT_FLAG_SCRATCH = 0x801F_A010
GROUND_DESCRIPTOR_SCRATCH = 0x801F_A020
PLAYER_ACTOR = 0x801F_B000
FLOOR_CONTEXT = 0x801F_B400


def marker(slot: int = 0) -> bytes:
    return bytes(
        (
            alternate_pickup.MARKER_ID,
            alternate_pickup.MARKER_CATEGORY,
            slot,
            alternate_pickup.MARKER_STATUS,
        )
    )


class AlternatePickupLayoutTests(unittest.TestCase):
    def test_the_resident_block_fits_the_retired_card_driver(self) -> None:
        # 744 bytes, and the title-label helper before it must not be clipped.
        self.assertEqual(alternate_pickup.BLOCK_ADDRESS, 0x8004_EB68)
        self.assertEqual(
            alternate_pickup.BLOCK_ADDRESS,
            save_removal.TITLE_LABEL_HELPER_ADDRESS
            + len(save_removal.build_title_label_helper()),
        )
        self.assertLessEqual(
            alternate_pickup.resident_block_size(),
            alternate_pickup.BLOCK_END_ADDRESS - alternate_pickup.BLOCK_ADDRESS,
        )

    def test_every_code_block_is_word_aligned_and_ordered(self) -> None:
        previous_end = alternate_pickup.CODE_ADDRESS
        for address, payload in alternate_pickup._resolve_layout():
            self.assertEqual(address % 4, 0)
            self.assertEqual(address, previous_end)
            previous_end = address + len(payload)

    def test_the_name_lookup_hook_replaces_the_displaced_store(self) -> None:
        # `sw zero,0x0(s1)`, the first instruction after 0x8004AC3C's frame is
        # complete.  The guard replays it; if this moves, it stops being safe
        # to hook here at all.
        patches = dict(alternate_pickup.iter_slus_file_patches())
        offset = save_removal.slus_runtime_to_file_offset(
            alternate_pickup.NAME_LOOKUP_HOOK_ADDRESS
        )
        self.assertEqual(
            patches[offset],
            struct.pack(
                "<I", patch._j(0x02, alternate_pickup.NAME_GUARD_ADDRESS)
            ),
        )

    def test_the_asset_calls_point_at_the_seed_render_resolver(self) -> None:
        # The ground entity's own call at 0x800A8690 was redirected long ago;
        # these two were missed, which is why a lifted marker rendered as a
        # Wind Crystal.
        expected = struct.pack(
            "<I", patch._j(0x03, patch.RESOLVE_LOCATION_RENDER_ADDRESS)
        )
        patches = dict(alternate_pickup.iter_dungeon_file_patches())
        for address in (
            alternate_pickup.HELD_ITEM_ASSET_CALL_ADDRESS,
            alternate_pickup.GROUND_ITEM_ASSET_CALL_ADDRESS,
        ):
            offset = alternate_pickup.dungeon_runtime_to_file_offset(address)
            self.assertEqual(patches[offset], expected, hex(address))

    def test_the_overlay_offset_mapping_matches_the_known_seam(self) -> None:
        # DUNGEON.BIN+0xC2F30 is the already-hooked ground asset call; the two
        # new ones are derived with the same arithmetic.
        self.assertEqual(
            alternate_pickup.dungeon_runtime_to_file_offset(0x800A_8690), 0xC2F30
        )
        self.assertEqual(
            alternate_pickup.dungeon_runtime_to_file_offset(
                alternate_pickup.HELD_ITEM_ASSET_CALL_ADDRESS
            ),
            0xC3840,
        )

    def test_the_marker_is_a_gift_identity(self) -> None:
        # Category 0x0B has no entry in the per-category name-suffix table at
        # 0x800DD784, where 0x06 had `　crystal`, and it draws the gift icon.
        # Both were visible bugs before this changed.
        self.assertEqual(patch.MARKER_CATEGORY, 0x0B)
        self.assertEqual(patch.MARKER_ID, 0x01)
        # 0x8D, not the original 0xAD: bit 0x20 is the equipped bit, and the
        # death drop refuses to drop an equipped carried item - the first
        # carrier ride held its check and never dropped it (2026-08-15).
        self.assertEqual(patch.MARKER_STATUS, 0x8D)
        self.assertEqual(patch.MARKER_STATUS & 0x20, 0)
        self.assertEqual(patch.MARKER_STATUS & 0x80, 0x80)

    def test_the_display_name_borrows_the_shop_wording(self) -> None:
        # The name stays uninformative and the description carries the detail,
        # which is what the town shop already does for an item this game has no
        # room to lay out.
        self.assertEqual(
            patch.MARKER_DISPLAY_NAME, town_shop.REMOTE_ITEM_DISPLAY_NAME
        )
        for text in (patch.MARKER_DISPLAY_NAME, patch.MARKER_SEND_LABEL):
            self.assertLessEqual(
                len(patch._full_width_cp932(text)) + 1,
                patch.MARKER_TEXT_SLOT_SIZE,
                text,
            )

    def test_the_payload_side_code_is_packed_and_fits(self) -> None:
        previous_end = patch.MARKER_CODE_ADDRESS
        for address, payload in patch.resolve_marker_code_layout():
            self.assertEqual(address, previous_end, hex(address))
            previous_end = address + len(payload)
        self.assertLessEqual(previous_end, patch.MARKER_PRESENTATION_END_ADDRESS)

    def test_the_description_hook_replaces_the_displaced_load(self) -> None:
        patches = dict(alternate_pickup.iter_slus_file_patches())
        offset = save_removal.slus_runtime_to_file_offset(
            alternate_pickup.DESCRIPTION_HOOK_ADDRESS
        )
        self.assertEqual(
            patches[offset],
            struct.pack(
                "<I",
                patch._j(0x02, alternate_pickup.DESCRIPTION_TRAMPOLINE_ADDRESS),
            ),
        )

    def test_the_front_menu_edits_are_single_words_in_stream_one(self) -> None:
        edits = dict(alternate_pickup.iter_front_menu_stream_patches())
        self.assertEqual(
            edits[
                alternate_pickup.VERB_LIST_BUILDER_TAIL_ADDRESS
                - alternate_pickup.FRONT_MENU_STREAM_BASE_ADDRESS
            ],
            struct.pack(
                "<I", patch._j(0x02, alternate_pickup.VERB_LIST_HOOK_ADDRESS)
            ),
        )
        self.assertEqual(
            edits[
                alternate_pickup.VERB_LABEL_LOOKUP_ADDRESS
                - alternate_pickup.FRONT_MENU_STREAM_BASE_ADDRESS
            ],
            struct.pack(
                "<I", patch._j(0x02, alternate_pickup.VERB_LABEL_HOOK_ADDRESS)
            ),
        )
        for offset in edits:
            self.assertLess(offset, 0x7590, "stream 1 decodes to 0x7590 bytes")


STOP_TRAMPOLINE = 0x801F_9000


class _Harness:
    """Loads the resident block and the seed page into simulated memory.

    Every hook here leaves through `j <vanilla address>` rather than `jr ra`,
    so the exits are modelled as stubs.  `mips_sim` resumes at `ra` after a
    stub, which is right for a `jal` and useless for a jump, so an exit stub
    records the machine state and then points `ra` at two words that load the
    simulator's own sentinel and jump to it.
    """

    def __init__(self, seed_block: bytes | None = None) -> None:
        self.memory = mips_sim.Memory()
        for address, payload in alternate_pickup._resolve_layout():
            self.memory.load_bytes(address, payload)
        for address, payload in patch.resolve_marker_code_layout():
            self.memory.load_bytes(address, payload)
        self.memory.load_bytes(
            patch.MARKER_DISPLAY_NAME_ADDRESS,
            patch._full_width_cp932(patch.MARKER_DISPLAY_NAME) + b"\0",
        )
        if seed_block is not None:
            self.memory.load_bytes(patch.SEED_BLOCK_ADDRESS, seed_block)
        self.memory.write32(patch.SEED_BLOCK_ADDRESS, patch.SEED_MAGIC)
        self.memory.write32(STOP_TRAMPOLINE, 0x3C1F_DEAD)  # lui ra,0xdead
        self.memory.write32(STOP_TRAMPOLINE + 4, 0x03E0_0008)  # jr ra
        self.memory.write32(STOP_TRAMPOLINE + 8, 0)
        self.exits: list[int] = []
        self.exit_state: dict[str, int] = {}
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def exit_stub(self, address: int):
        def stub(cpu: mips_sim.Cpu) -> None:
            self.exits.append(address)
            self.exit_state = {
                "ra": cpu.registers[31],
                "sp": cpu.registers[29],
                "v0": cpu.registers[2],
            }
            cpu.registers[31] = STOP_TRAMPOLINE

        return stub

    def call_stub(self, name: str, arguments: int = 0, result: int | None = None):
        def stub(cpu: mips_sim.Cpu) -> None:
            self.calls.append(
                (name, tuple(cpu.registers[4 + index] for index in range(arguments)))
            )
            if result is not None:
                cpu.registers[2] = result

        return stub


class MarkerTestTests(unittest.TestCase):
    def _run(self, descriptor: bytes, seed: bool = True) -> int:
        harness = _Harness()
        if not seed:
            harness.memory.write32(patch.SEED_BLOCK_ADDRESS, 0)
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, descriptor)
        cpu = mips_sim.Cpu(harness.memory)
        cpu.registers[4] = DESCRIPTOR_SCRATCH
        return cpu.run(alternate_pickup.MARKER_TEST_ADDRESS)

    def test_a_real_marker_is_recognised_in_both_slots(self) -> None:
        for slot in range(alternate_pickup.MARKER_SLOT_COUNT):
            self.assertEqual(self._run(marker(slot)), 1, f"slot {slot}")

    def test_every_wrong_byte_is_rejected(self) -> None:
        # A real Cream must survive this, and so must the town shop's display
        # proxy `(1, 11, 0, 0)`, which differs only in its status byte.
        for index, replacement in (
            (0, 0x02),  # Roses
            (1, 0x06),  # a crystal
            (2, alternate_pickup.MARKER_SLOT_COUNT),  # slot out of range
            (3, 0x00),  # an ordinary status byte - the shop's proxy
        ):
            descriptor = bytearray(marker())
            descriptor[index] = replacement
            self.assertEqual(self._run(bytes(descriptor)), 0, f"byte {index}")

    def test_a_disc_with_no_seed_page_recognises_nothing(self) -> None:
        self.assertEqual(self._run(marker(), seed=False), 0)

    def test_it_leaves_the_descriptor_pointer_alone(self) -> None:
        harness = _Harness()
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, marker())
        cpu = mips_sim.Cpu(harness.memory)
        cpu.registers[4] = DESCRIPTOR_SCRATCH
        cpu.run(alternate_pickup.MARKER_TEST_ADDRESS)
        self.assertEqual(cpu.registers[4], DESCRIPTOR_SCRATCH)


class NameGuardTests(unittest.TestCase):
    """The name lookup answers two different questions through one routine.

    `You're on <item>.` has a whole message box, so it gets the real multiworld
    name. The item-name field is one narrow line shared with every native item,
    so it gets `Strange...`. They are told apart by the return address, which
    means the guard has to capture `ra` before its own `jal` destroys it - and
    that is what these check.
    """

    STACK = 0x801F_C000
    ENTRY = 0x801F_8000

    def _run(self, descriptor: bytes, caller: int):
        placements = [
            patch.LocationPlacement("Master Sword", "Sandknight", True)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        harness = _Harness(block)
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, descriptor)
        harness.memory.write32(alternate_pickup.CURRENT_FLOOR_ADDRESS, 3)
        # The floor-page loader's effect: floor 3's page over the window.
        harness.memory.load_bytes(
            patch.FLOOR_PAGE_WINDOW_ADDRESS,
            patch.build_floor_page_sectors(block, placements)[2],
        )

        # `mips_sim.Cpu.run` owns `ra`, so the caller under test is staged by a
        # two-instruction prologue that sets it and jumps into the guard.
        harness.memory.write32(self.ENTRY, patch._i(0x0F, 0, 31, caller >> 16))
        harness.memory.write32(
            self.ENTRY + 4, patch._i(0x0D, 31, 31, caller & 0xFFFF)
        )
        harness.memory.write32(
            self.ENTRY + 8, patch._j(0x02, alternate_pickup.NAME_GUARD_ADDRESS)
        )
        harness.memory.write32(self.ENTRY + 12, 0)

        appended: list[int] = []

        def append_encoded_text(cpu: mips_sim.Cpu) -> None:
            appended.append(cpu.registers[4])
            cpu.registers[2] = cpu.registers[5] + 1

        stubs = {
            0x8009_9194: append_encoded_text,
            alternate_pickup.NAME_LOOKUP_RESUME_ADDRESS: harness.exit_stub(
                alternate_pickup.NAME_LOOKUP_RESUME_ADDRESS
            ),
        }
        cpu = mips_sim.Cpu(harness.memory, stubs=stubs)
        harness.memory.write32(self.STACK + 0x10, 0x1111_1111)
        harness.memory.write32(self.STACK + 0x14, 0x2222_2222)
        harness.memory.write32(self.STACK + 0x18, 0xDEAD_0000)
        cpu.registers[4] = DESCRIPTOR_SCRATCH
        cpu.registers[17] = OUT_FLAG_SCRATCH
        harness.memory.write32(OUT_FLAG_SCRATCH, 0xFFFF_FFFF)
        cpu.run(self.ENTRY, stack_pointer=self.STACK)
        return cpu, harness, appended

    def test_the_message_system_gets_the_real_item_name(self) -> None:
        cpu, _, appended = self._run(
            marker(), patch.MESSAGE_NAME_CALLER_RETURN_ADDRESS
        )
        self.assertEqual(cpu.registers[2], patch.MARKER_DESCRIPTION_BUFFER_ADDRESS)
        # The item name only - never the owner line, which belongs to the
        # description box.
        self.assertEqual(len(appended), 1)

    def test_the_item_name_field_gets_the_placeholder(self) -> None:
        for caller in (0x8001_7164, 0x8001_A224, 0xDEAD_0000):
            with self.subTest(caller=hex(caller)):
                cpu, _, appended = self._run(marker(), caller)
                self.assertEqual(
                    cpu.registers[2], patch.MARKER_DISPLAY_NAME_ADDRESS
                )
                self.assertEqual(appended, [])

    def test_a_marker_unwinds_the_frame_it_was_planted_in(self) -> None:
        cpu, _, _ = self._run(marker(), 0xDEAD_0000)
        self.assertEqual(cpu.registers[29], self.STACK + 0x20)
        self.assertEqual(cpu.registers[16], 0x1111_1111)
        self.assertEqual(cpu.registers[17], 0x2222_2222)

    def test_an_ordinary_item_continues_into_the_vanilla_lookup(self) -> None:
        _, harness, _ = self._run(b"\x01\x0b\x00\x00", 0xDEAD_0000)
        self.assertEqual(
            harness.exits, [alternate_pickup.NAME_LOOKUP_RESUME_ADDRESS]
        )

    def test_the_displaced_store_runs_on_both_paths(self) -> None:
        for descriptor in (marker(), b"\x01\x0b\x00\x00"):
            _, harness, _ = self._run(descriptor, 0xDEAD_0000)
            self.assertEqual(
                harness.memory.read32(OUT_FLAG_SCRATCH), 0, descriptor.hex()
            )


class PutInGuardTests(unittest.TestCase):
    FLOOR = 7
    STACK = 0x801F_C000

    def _harness(self) -> _Harness:
        harness = _Harness()
        harness.memory.write32(
            alternate_pickup.PLAYER_ACTOR_POINTER_ADDRESS, PLAYER_ACTOR
        )
        harness.memory.write32(
            alternate_pickup.FLOOR_CONTEXT_POINTER_ADDRESS, FLOOR_CONTEXT
        )
        harness.memory.write32(
            alternate_pickup.CURRENT_FLOOR_ADDRESS, self.FLOOR
        )
        return harness

    def _stubs(self, harness: _Harness, found_index: int = -1) -> dict:
        return {
            alternate_pickup.ALLOCATE_MESSAGE_BUFFER_ADDRESS: harness.call_stub(
                "allocate", 0, result=0x801F_D000
            ),
            alternate_pickup.APPEND_CONTROL_CODE_ADDRESS: harness.call_stub(
                "control", 2, result=0x801F_D004
            ),
            patch.APPEND_LOCATION_MESSAGE_ADDRESS: harness.call_stub(
                "compose", 2, result=0x801F_D040
            ),
            alternate_pickup.TERMINATE_MESSAGE_ADDRESS: harness.call_stub(
                "terminate", 1
            ),
            alternate_pickup.DISPLAY_MESSAGE_ADDRESS: harness.call_stub("display", 1),
            alternate_pickup.PLAY_SOUND_ADDRESS: harness.call_stub("sound", 1),
            alternate_pickup.FIND_ARRAY_INDEX_ADDRESS: harness.call_stub(
                "find", 4, result=found_index & 0xFFFF_FFFF
            ),
            alternate_pickup.CLEAR_TILE_FLAGS_ADDRESS: harness.call_stub("tile", 3),
            alternate_pickup.PUT_IN_ALLOCATOR_ADDRESS: harness.exit_stub(
                alternate_pickup.PUT_IN_ALLOCATOR_ADDRESS
            ),
            alternate_pickup.PUT_IN_EPILOGUE_ADDRESS: harness.exit_stub(
                alternate_pickup.PUT_IN_EPILOGUE_ADDRESS
            ),
        }

    def _run(
        self,
        descriptor_address: int,
        descriptor: bytes,
        show_message: int = 1,
        found_index: int = -1,
        harness: _Harness | None = None,
    ) -> tuple[mips_sim.Cpu, _Harness]:
        harness = harness or self._harness()
        harness.memory.load_bytes(descriptor_address, descriptor)
        cpu = mips_sim.Cpu(harness.memory, stubs=self._stubs(harness, found_index))
        cpu.registers[17] = descriptor_address  # s1 - the descriptor
        cpu.registers[19] = show_message  # s3 - show_message
        cpu.registers[31] = 0x8009_7FF8  # the instruction after the hook
        cpu.run(alternate_pickup.PUT_IN_GUARD_ADDRESS, stack_pointer=self.STACK)
        return cpu, harness

    def test_an_ordinary_item_reaches_the_vanilla_allocator_untouched(self) -> None:
        cpu, harness = self._run(DESCRIPTOR_SCRATCH, b"\x01\x01\x00\x00")
        self.assertEqual(harness.exits, [alternate_pickup.PUT_IN_ALLOCATOR_ADDRESS])
        self.assertEqual(harness.calls, [])
        # This is the whole reason the guard pushes a frame.  It was entered by
        # `jal`, so `ra` points one instruction past the hook and the vanilla
        # allocator's own `jr ra` is what returns there - but the marker test is
        # also a `jal`, which destroys it.  `mips_sim.Cpu.run` owns `ra`, so the
        # check is that the value it installed survived.
        self.assertEqual(harness.exit_state["ra"], 0xDEAD_0000)
        self.assertEqual(harness.exit_state["sp"], self.STACK)

    def test_a_marker_leaves_through_the_epilogue_with_nothing_inserted(self) -> None:
        cpu, harness = self._run(
            alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker()
        )
        self.assertEqual(harness.exits, [alternate_pickup.PUT_IN_EPILOGUE_ADDRESS])
        self.assertEqual(harness.exit_state["v0"], 0)
        self.assertEqual(harness.exit_state["sp"], self.STACK)

    def test_a_marker_records_its_location_in_the_journal(self) -> None:
        for slot in range(alternate_pickup.MARKER_SLOT_COUNT):
            with self.subTest(slot=slot):
                _, harness = self._run(
                    alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker(slot)
                )
                # One byte per floor, bit = slot (ADSV v4). The journal is
                # ADSV +0x10 - not a hard-coded address of its own.
                self.assertEqual(
                    alternate_pickup.COLLECTION_JOURNAL_ADDRESS,
                    patch.PERSISTENT_STATE_ADDRESS
                    + patch.PERSISTENT_LOCATION_MASK_OFFSET,
                )
                byte = harness.memory.read8(
                    alternate_pickup.COLLECTION_JOURNAL_ADDRESS + self.FLOOR - 1
                )
                self.assertEqual(byte, 1 << slot)
                # And nothing else in the journal moved.
                for other in range(patch.PERSISTENT_TOWER_MASK_BYTES):
                    if other != self.FLOOR - 1:
                        self.assertEqual(
                            harness.memory.read8(
                                alternate_pickup.COLLECTION_JOURNAL_ADDRESS + other
                            ),
                            0,
                        )

    def test_it_refuses_a_floor_that_would_index_past_the_journal(self) -> None:
        harness = self._harness()
        harness.memory.write32(alternate_pickup.CURRENT_FLOOR_ADDRESS, 40)
        _, harness = self._run(
            alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker(), harness=harness
        )
        self.assertEqual(harness.exits, [alternate_pickup.PUT_IN_ALLOCATOR_ADDRESS])

    def test_it_composes_the_placement_text_from_the_descriptor(self) -> None:
        _, harness = self._run(
            alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker()
        )
        names = [name for name, _ in harness.calls]
        self.assertEqual(
            names,
            ["allocate", "control", "compose", "terminate", "display", "sound"],
        )
        compose = dict(
            (name, arguments) for name, arguments in harness.calls
        )["compose"]
        self.assertEqual(compose[0], alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS)

    def test_show_message_zero_suppresses_only_the_display(self) -> None:
        _, harness = self._run(
            alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker(), show_message=0
        )
        names = [name for name, _ in harness.calls]
        self.assertNotIn("display", names)
        self.assertIn("compose", names)

    def test_a_marker_in_hand_clears_the_hand_and_the_holding_bit(self) -> None:
        harness = self._harness()
        harness.memory.write32(PLAYER_ACTOR + 0x1C, 0xFFFF_FFFF)
        harness.memory.write32(PLAYER_ACTOR + 0x124, 0x1234_5678)
        _, harness = self._run(
            alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS, marker(), harness=harness
        )
        self.assertEqual(
            harness.memory.read32(alternate_pickup.IN_HAND_DESCRIPTOR_ADDRESS), 0
        )
        self.assertEqual(harness.memory.read32(PLAYER_ACTOR + 0x124), 0)
        self.assertEqual(
            harness.memory.read32(PLAYER_ACTOR + 0x1C),
            0xFFFF_FFFF & ~alternate_pickup.ACTOR_HOLDING_ITEM_BIT,
        )
        self.assertEqual(
            [name for name, _ in harness.calls if name in ("find", "tile")], []
        )

    def test_a_marker_at_feet_clears_its_tile_and_ground_descriptor(self) -> None:
        harness = self._harness()
        harness.memory.write32(FLOOR_CONTEXT + 0xF0, GROUND_DESCRIPTOR_SCRATCH)
        harness.memory.write32(PLAYER_ACTOR + 0xF0, GROUND_DESCRIPTOR_SCRATCH)
        harness.memory.write32(GROUND_DESCRIPTOR_SCRATCH, 0xDEAD_BEEF)
        # Entity 3's coordinates, twelve bytes apart.
        harness.memory.write8(
            alternate_pickup.GROUND_ITEM_ENTITIES_ADDRESS + 3 * 12, 9
        )
        harness.memory.write8(
            alternate_pickup.GROUND_ITEM_ENTITIES_ADDRESS + 3 * 12 + 1, 11
        )
        _, harness = self._run(
            DESCRIPTOR_SCRATCH, marker(), found_index=3, harness=harness
        )
        tile = dict((name, arguments) for name, arguments in harness.calls)["tile"]
        self.assertEqual(
            tile, (9, 11, alternate_pickup.TILE_OCCUPIED_BY_ITEM)
        )
        self.assertEqual(harness.memory.read32(GROUND_DESCRIPTOR_SCRATCH), 0)
        self.assertEqual(harness.memory.read32(DESCRIPTOR_SCRATCH), 0)

    def test_a_missing_ground_slot_still_clears_the_descriptor(self) -> None:
        # `find_array_index` returning -1 must not turn into a wild tile clear.
        harness = self._harness()
        harness.memory.write32(FLOOR_CONTEXT + 0xF0, GROUND_DESCRIPTOR_SCRATCH)
        harness.memory.write32(PLAYER_ACTOR + 0xF0, GROUND_DESCRIPTOR_SCRATCH)
        _, harness = self._run(
            DESCRIPTOR_SCRATCH, marker(), found_index=-1, harness=harness
        )
        self.assertNotIn("tile", [name for name, _ in harness.calls])
        self.assertEqual(harness.exits, [alternate_pickup.PUT_IN_EPILOGUE_ADDRESS])


class VerbMenuTests(unittest.TestCase):
    CONTROLLER = 0x801F_B800

    def _run_list(self, descriptor: bytes) -> tuple[mips_sim.Cpu, _Harness]:
        harness = _Harness()
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, descriptor)
        harness.memory.write32(self.CONTROLLER + 0x68, DESCRIPTOR_SCRATCH)
        harness.memory.write32(alternate_pickup.MENU_MARKER_FLAG_ADDRESS, 0xFF)
        cpu = mips_sim.Cpu(
            harness.memory,
            stubs={
                alternate_pickup.VERB_LIST_RESUME_ADDRESS: harness.exit_stub(
                    alternate_pickup.VERB_LIST_RESUME_ADDRESS
                ),
            },
        )
        cpu.registers[18] = self.CONTROLLER  # s2
        cpu.registers[16] = 4  # s0 - the vanilla row count
        cpu.run(alternate_pickup.VERB_LIST_HOOK_ADDRESS)
        return cpu, harness

    def test_a_marker_gets_exactly_one_row(self) -> None:
        _, harness = self._run_list(marker())
        self.assertEqual(
            harness.memory.read8(self.CONTROLLER + 0x54),
            alternate_pickup.SEND_VERB_ID,
        )
        self.assertEqual(harness.memory.read32(self.CONTROLLER + 0x28), 1)
        self.assertEqual(
            harness.memory.read32(alternate_pickup.MENU_MARKER_FLAG_ADDRESS), 1
        )

    def test_an_ordinary_item_keeps_the_vanilla_list(self) -> None:
        _, harness = self._run_list(b"\x01\x01\x00\x00")
        # The displaced store still has to happen, or the count is never written.
        self.assertEqual(harness.memory.read32(self.CONTROLLER + 0x28), 4)
        self.assertEqual(
            harness.memory.read32(alternate_pickup.MENU_MARKER_FLAG_ADDRESS), 0
        )
        self.assertEqual(
            harness.exits, [alternate_pickup.VERB_LIST_RESUME_ADDRESS]
        )

    def _run_label(self, verb: int, flag: int) -> tuple[mips_sim.Cpu, _Harness]:
        harness = _Harness()
        harness.memory.write32(alternate_pickup.MENU_MARKER_FLAG_ADDRESS, flag)
        resume = alternate_pickup.VERB_LABEL_LOOKUP_ADDRESS + 8
        cpu = mips_sim.Cpu(
            harness.memory, stubs={resume: harness.exit_stub(resume)}
        )
        cpu.registers[4] = verb
        cpu.run(alternate_pickup.VERB_LABEL_HOOK_ADDRESS)
        return cpu, harness

    def test_the_put_in_row_reads_send_while_the_flag_is_set(self) -> None:
        cpu, harness = self._run_label(alternate_pickup.SEND_VERB_ID, 1)
        self.assertEqual(cpu.registers[2], alternate_pickup.SEND_LABEL_ADDRESS)
        self.assertEqual(harness.exits, [])

    def test_the_put_in_row_reads_put_in_for_every_ordinary_item(self) -> None:
        _, harness = self._run_label(alternate_pickup.SEND_VERB_ID, 0)
        self.assertEqual(
            harness.exits, [alternate_pickup.VERB_LABEL_LOOKUP_ADDRESS + 8]
        )

    def test_every_other_verb_rejoins_the_vanilla_table_read(self) -> None:
        for verb in (0x01, 0x02, 0x0E, 0x12):
            with self.subTest(verb=verb):
                cpu, harness = self._run_label(verb, 1)
                self.assertEqual(
                    harness.exits, [alternate_pickup.VERB_LABEL_LOOKUP_ADDRESS + 8]
                )
                # The `lui` the hook replaced supplied the table base; the
                # vanilla tail indexes off it, so it has to arrive in v0.
                self.assertEqual(
                    cpu.registers[2], alternate_pickup.VERB_LABEL_TABLE_ADDRESS
                )


if __name__ == "__main__":
    unittest.main()


class DescriptionTests(unittest.TestCase):
    """The description box, built from the pooled placement text.

    The routine this replaces returns **void** - it resolves a pointer and
    calls `show_item_description` itself. V80 handed a string back instead and
    the box vanished entirely, which is what the last assertion here is for.
    """

    STACK = 0x801F_C000

    def _run(self, placements, floor, slot):
        block = patch.build_seed_block(b"12345678", placements)
        harness = _Harness(block)
        harness.memory.write32(alternate_pickup.CURRENT_FLOOR_ADDRESS, floor)
        # What the floor-page loader does during the floor build: land the
        # floor's page sector over the seed page's window. Floors without a
        # page (40+) leave whatever was resident, exactly like the runtime.
        if 1 <= floor <= patch.FLOOR_PAGE_FLOOR_COUNT:
            pages = patch.build_floor_page_sectors(block, placements)
            harness.memory.load_bytes(
                patch.FLOOR_PAGE_WINDOW_ADDRESS, pages[floor - 1]
            )
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, marker(slot))
        harness.memory.write32(self.STACK + 0x10, 0x1111_1111)
        harness.memory.write32(self.STACK + 0x14, 0x2222_2222)
        harness.memory.write32(self.STACK + 0x18, 0xDEAD_0000)

        appended: list[int] = []
        shown: list[int] = []

        def append_encoded_text(cpu: mips_sim.Cpu) -> None:
            appended.append(cpu.registers[4])
            cursor = cpu.registers[5]
            cpu.memory.write8(cursor, 0xEE)
            cpu.registers[2] = cursor + 1

        def show_item_description(cpu: mips_sim.Cpu) -> None:
            shown.append(cpu.registers[4])

        cpu = mips_sim.Cpu(
            harness.memory,
            stubs={
                0x8009_9194: append_encoded_text,
                patch.SHOW_ITEM_DESCRIPTION_ADDRESS: show_item_description,
            },
        )
        cpu.registers[4] = DESCRIPTOR_SCRATCH
        cpu.run(patch.MARKER_DESCRIBE_ENTRY_ADDRESS, stack_pointer=self.STACK)
        return appended, shown, cpu, harness.memory, block

    def test_it_shows_what_it_built_rather_than_returning_it(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(patch.LOCATION_COUNT)]
        _, shown, _, _, _ = self._run(placements, floor=1, slot=0)
        self.assertEqual(shown, [patch.MARKER_DESCRIPTION_BUFFER_ADDRESS])

    def test_a_local_placement_still_says_who_it_is_for(self) -> None:
        # "for <you>" is still the answer to who this is for, and the shop says
        # the same thing about its own local slots.
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(patch.LOCATION_COUNT)]
        appended, _, _, _, block = self._run(placements, floor=1, slot=0)
        self.assertEqual(len(appended), 3)
        self.assertEqual(
            appended[1], patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_FRAGMENT_FOR
        )
        # ...which means a local placement carries its recipient too. The
        # record's player-slot byte selects the fixed name slot.
        recipient = (
            patch.SEED_BLOCK_ADDRESS
            + patch.FLOOR_PAGE_PLAYER_SLOTS_OFFSET
            + block[patch.FLOOR_PAGE_RECORDS_OFFSET + 1]
            * patch.FLOOR_PAGE_PLAYER_SLOT_SIZE
        )
        self.assertEqual(appended[2], recipient)

    def test_the_item_name_alone_never_gains_the_owner_line(self) -> None:
        # The name path passes with_owner clear, and that is what keeps
        # `You're on ...` to one line.
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(patch.LOCATION_COUNT)]
        harness = _Harness(patch.build_seed_block(b"12345678", placements))
        harness.memory.write32(alternate_pickup.CURRENT_FLOOR_ADDRESS, 1)
        harness.memory.load_bytes(DESCRIPTOR_SCRATCH, marker(0))
        appended: list[int] = []

        def append_encoded_text(cpu: mips_sim.Cpu) -> None:
            appended.append(cpu.registers[4])
            cpu.registers[2] = cpu.registers[5] + 1

        cpu = mips_sim.Cpu(harness.memory, stubs={0x8009_9194: append_encoded_text})
        cpu.registers[4] = DESCRIPTOR_SCRATCH
        cpu.registers[5] = 0
        cpu.run(patch.MARKER_TEXT_BUILDER_ADDRESS)
        self.assertEqual(len(appended), 1)

    def test_a_remote_placement_adds_the_shop_second_line(self) -> None:
        placements = [
            patch.LocationPlacement("Master Sword", "Sandknight", True)
            for _ in range(patch.LOCATION_COUNT)
        ]
        appended, _, _, _, _ = self._run(placements, floor=3, slot=1)
        self.assertEqual(len(appended), 3)
        self.assertEqual(
            appended[1], patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_FRAGMENT_FOR
        )
        self.assertNotEqual(appended[0], appended[2])

    def test_it_terminates_what_it_built(self) -> None:
        placements = [
            patch.LocationPlacement("Master Sword", "Sandknight", True)
            for _ in range(patch.LOCATION_COUNT)
        ]
        appended, _, _, memory, _ = self._run(placements, floor=3, slot=1)
        self.assertEqual(
            memory.read8(patch.MARKER_DESCRIPTION_BUFFER_ADDRESS + len(appended)), 0
        )

    def test_it_reads_the_placement_the_floor_and_slot_select(self) -> None:
        placements = [
            patch.LocationPlacement(f"Item{index}", "Koh", False) for index in range(patch.LOCATION_COUNT)
        ]
        for floor, slot in ((1, 0), (1, 1), (20, 1), (39, 1)):
            with self.subTest(floor=floor, slot=slot):
                appended, _, _, _, block = self._run(placements, floor=floor, slot=slot)
                # The loaded page's slot k holds placement (floor-1)*2+k, so
                # the appended address is the fixed slot for `slot`.
                expected = (
                    patch.SEED_BLOCK_ADDRESS
                    + patch.FLOOR_PAGE_ITEM_SLOTS_OFFSET
                    + slot * patch.FLOOR_PAGE_ITEM_SLOT_SIZE
                )
                self.assertEqual(appended[0], expected)

    def test_a_floor_past_the_tower_falls_back_to_the_display_name(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(patch.LOCATION_COUNT)]
        appended, shown, _, _, _ = self._run(placements, floor=40, slot=0)
        self.assertEqual(appended, [])
        self.assertEqual(shown, [patch.MARKER_DISPLAY_NAME_ADDRESS])

    def test_it_unwinds_the_frame_it_inherited(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(patch.LOCATION_COUNT)]
        _, _, cpu, _, _ = self._run(placements, floor=1, slot=0)
        self.assertEqual(cpu.registers[29], self.STACK + 0x20)
        self.assertEqual(cpu.registers[16], 0x1111_1111)
        self.assertEqual(cpu.registers[17], 0x2222_2222)
