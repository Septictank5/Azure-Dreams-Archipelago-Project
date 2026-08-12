import struct
import unittest
from pathlib import Path

from . import mips_sim
from .. import patch, town_shop, town_warp

ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)


class TownWarpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patches = dict(town_warp.iter_town_warp_file_patches())

    def test_uncle_resource_mapping_matches_live_capture(self) -> None:
        self.assertEqual(town_warp.UNCLE_DIALOGUE_FILE_OFFSET, 0x2D9800)
        self.assertEqual(
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.UNCLE_VANILLA_DIALOGUE_ENTRY_ADDRESS
            ),
            0x2DA399,
        )
        # 0x2DA618 is the verified live-capture anchor, and it is the `jr ra`
        # delay slot at 0x8001eae8 - an instruction the console executes.  The
        # space we own starts one word later; writing over that slot is what
        # crashed the tower-entrance Kewne event.
        self.assertEqual(
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.UNCLE_ZERO_GAP_ORIGINAL_START_ADDRESS
            ),
            0x2DA618,
        )
        self.assertEqual(
            town_warp.UNCLE_ZERO_GAP_START_ADDRESS,
            town_warp.UNCLE_ZERO_GAP_ORIGINAL_START_ADDRESS + 4,
        )
        self.assertEqual(
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.UNCLE_ZERO_GAP_START_ADDRESS
            ),
            0x2DA61C,
        )
        # Nothing may be written into the delay slot.
        self.assertNotIn(0x2DA618, self.patches)
        for offset, data in self.patches.items():
            self.assertFalse(
                offset <= 0x2DA618 < offset + len(data),
                f"write at 0x{offset:X} covers the jr ra delay slot",
            )

    def test_rebuilt_dialogue_home_and_shape(self) -> None:
        """The v110 rebuilt script: right home, right span, no flaky calls."""

        rebuilt_offset = town_warp._dialogue_runtime_to_file_offset(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS
        )
        # Window +0x870: Nada's region plus her 512-byte spare tail.
        self.assertEqual(rebuilt_offset, 0x2DA070)
        self.assertEqual(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS - 0x8001_DCD0, 0x870
        )
        rebuilt = self.patches[rebuilt_offset]
        self.assertLessEqual(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS + len(rebuilt),
            town_warp.UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
        )
        # The still-live checks and the item-limit refusal are called at
        # their existing retired-region addresses...
        for address in (
            town_warp.WARP_LEVEL_2_CHECK_ADDRESS,
            town_warp.WARP_LEVEL_4_CHECK_ADDRESS,
            town_warp.WARP_LEVEL_6_CHECK_ADDRESS,
            town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS,
        ):
            self.assertIn(struct.pack("<I", address), rebuilt)
        # ...but the flaky floor-warp callbacks are NOT: floor picks end
        # silently until the tower-load rebuild replaces them.
        for address in (
            town_warp.WARP_FLOOR_10_ADDRESS,
            town_warp.WARP_FLOOR_20_ADDRESS,
            town_warp.WARP_FLOOR_30_ADDRESS,
        ):
            self.assertNotIn(struct.pack("<I", address), rebuilt)
        # Every internal label lands inside the rebuilt span, not the old
        # script's address range.
        self.assertNotIn(
            struct.pack("<I", town_warp.WARP_SCRIPT_ADDRESS), rebuilt
        )

    def test_two_column_gaps_pad_to_sixteen_cells(self) -> None:
        """Column 0 plus its gap is a fixed 16 cells, like the native menus.

        `[Floor 10]` is 10 cells, so the gap is six full-width spaces; the
        old hardcoded four made the selection highlight bleed into the next
        option's bracket (observed live in v110).
        """

        rebuilt = self.patches[
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS
            )
        ]
        six = b"\x81\x6e" + b"\x81\x40" * 6 + b"\x81\x6d"
        four = b"\x81\x6e" + b"\x81\x40" * 4 + b"\x81\x6d"
        self.assertIn(six, rebuilt)
        self.assertNotIn(four, rebuilt)

    def test_native_menus_pin_the_sixteen_cell_rule(self) -> None:
        """Two independent vanilla two-column menus establish the rule."""

        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        disc = ORIGINAL_BIN.read_bytes()
        # `[Yes]` (5 cells) + 11 spaces near the menu at TOWN+0x62C99A,
        # and `[I'll buy.]` (11 cells) + 5 spaces near TOWN+0x40D553.
        # Raw-disc search: the patterns are long enough to be unique
        # within their sectors.
        for file_offset, gap_cells in ((0x62_C900, 11), (0x40_D500, 5)):
            raw = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA, file_offset
            )
            window = disc[raw:raw + 0x100]
            pattern = b"\x81\x6e" + b"\x81\x40" * gap_cells + b"\x81\x6d"
            self.assertIn(
                pattern,
                window,
                f"native {gap_cells}-space menu near TOWN+0x{file_offset:X} moved",
            )

    def test_uncle_variant_guard_and_getter_are_redirected(self) -> None:
        guard_offset = town_warp._overlay_runtime_to_file_offset(
            town_warp.UNCLE_SELECTION_FUNCTION_POINTER_ADDRESS
        )
        self.assertEqual(guard_offset, 0x2B0AF4)
        self.assertEqual(
            self.patches[guard_offset],
            bytes.fromhex("14A50180"),  # 0x8001a514 return_zero
        )

        getter_offset = town_warp._overlay_runtime_to_file_offset(
            town_warp.UNCLE_DIALOGUE_GETTER_RETURN_INSTRUCTION_ADDRESS
        )
        self.assertEqual(getter_offset, 0x2ABF24)
        self.assertEqual(
            self.patches[getter_offset],
            bytes.fromhex("40E54224"),  # addiu v0,v0,-0x1AC0
        )
        self.assertEqual(
            struct.unpack("<I", self.patches[getter_offset])[0],
            town_shop._i(
                0x09,
                2,
                2,
                town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS - 0x8002_0000,
            ),
        )

        for address in (
            town_warp.UNCLE_DIALOGUE_FUNCTION_POINTER_ADDRESS,
            town_warp.UNCLE_PLACEMENT_POINTER_ADDRESS,
            town_warp.UNCLE_EVENT_FLAG_ID_ADDRESS,
        ):
            self.assertNotIn(
                town_warp._overlay_runtime_to_file_offset(address),
                self.patches,
            )

    def test_uncle_spawn_coordinates_are_relocated(self) -> None:
        """Uncle stands at the spot captured live in Koh_position.bin.

        Spawn coordinates are SIGNED LOCAL offsets from the owning scene
        section's base, while the player's motion block is global.  Uncle's
        base (3200, 2816) is measured end to end, not inferred: his
        template local (864, 800) put his live actor at exactly
        (4064, 3616) in koh_next_to_uncle_position.bin, with the player
        standing beside him for scale.  Three attempts failed before this:
        reading the dialogue-reused RAM copy, writing the global pair as
        the offset, and rebasing on a neighboring section's origin.
        """

        offset = town_warp._overlay_runtime_to_file_offset(
            town_warp.UNCLE_PLACEMENT_COORDINATE_ADDRESS
        )
        self.assertEqual(
            self.patches[offset],
            struct.pack("<2H", *town_warp.UNCLE_RELOCATED_COORDINATES),
        )
        self.assertEqual(town_warp.UNCLE_TARGET_GLOBAL_COORDINATES, (3937, 1702))
        # (737, -1114) as unsigned halfwords.
        self.assertEqual(town_warp.UNCLE_RELOCATED_COORDINATES, (0x02E1, 0xFBA6))
        # The measured chain must stay consistent: base + vanilla local
        # equals the observed live position.
        self.assertEqual(
            (
                town_warp.UNCLE_SECTION_ORIGIN[0]
                + town_warp.UNCLE_VANILLA_COORDINATES[0],
                town_warp.UNCLE_SECTION_ORIGIN[1]
                + town_warp.UNCLE_VANILLA_COORDINATES[1],
            ),
            town_warp.UNCLE_VANILLA_GLOBAL_COORDINATES,
        )
        self.assertEqual(
            town_warp.UNCLE_VANILLA_GLOBAL_COORDINATES, (4064, 3616)
        )

    def test_nada_is_relocated_and_the_child_is_not(self) -> None:
        """Nada moves to the koh_position_for_Nada.bin spot; the child stays.

        Same measured recipe as Uncle: her base (3584, 4096) comes from her
        live actor at (4064, 4160) against her template local (480, 64) in
        koh_next_to_nada.bin.  The child's template is 20 bytes after hers
        and is deliberately untouched.
        """

        offset = town_warp._overlay_runtime_to_file_offset(
            town_warp.NADA_PLACEMENT_COORDINATE_ADDRESS
        )
        self.assertEqual(
            self.patches[offset],
            struct.pack("<2H", *town_warp.NADA_RELOCATED_COORDINATES),
        )
        # (700, -1122) as unsigned halfwords.
        self.assertEqual(town_warp.NADA_RELOCATED_COORDINATES, (0x02BC, 0xFB9E))
        self.assertEqual(
            (
                town_warp.NADA_SECTION_ORIGIN[0]
                + town_warp.NADA_VANILLA_COORDINATES[0],
                town_warp.NADA_SECTION_ORIGIN[1]
                + town_warp.NADA_VANILLA_COORDINATES[1],
            ),
            town_warp.NADA_VANILLA_GLOBAL_COORDINATES,
        )
        self.assertNotIn(
            town_warp._overlay_runtime_to_file_offset(
                town_warp.NADA_CHILD_COORDINATE_ADDRESS
            ),
            self.patches,
            "the child stays where he is, singing his tutorial hymns",
        )
        if ORIGINAL_BIN.exists():
            disc = ORIGINAL_BIN.read_bytes()
            for address, model, coords in (
                (town_warp.NADA_TEMPLATE_ADDRESS, town_warp.NADA_MODEL,
                 town_warp.NADA_VANILLA_COORDINATES),
                (town_warp.NADA_CHILD_TEMPLATE_ADDRESS, town_warp.NADA_CHILD_MODEL,
                 (0x0160, 0x0060)),
            ):
                raw = town_shop.mode2_file_offset_to_raw_offset(
                    town_shop.TOWN_FILE_START_LBA,
                    town_warp._overlay_runtime_to_file_offset(address),
                )
                template = disc[raw:raw + 0x14]
                self.assertEqual(
                    struct.unpack('<H', template[0xC:0xE])[0], model,
                    f"model moved at 0x{address:08x}; re-derive the template",
                )
                self.assertEqual(
                    struct.unpack('<2H', template[0x10:0x14]), coords,
                    f"coordinates moved at 0x{address:08x}; re-derive",
                )

    def test_uncle_vanilla_descriptor_and_position_are_documented(self) -> None:
        self.assertEqual(
            town_warp.UNCLE_VANILLA_DESCRIPTOR,
            {
                0x8001_B2F4: 0x8001_6728,
                0x8001_B2F8: 0x8001_671C,
                0x8001_B2FC: 0x8001_DDCC,
                0x8001_B300: 0x0000_0DB5,
            },
        )
        self.assertEqual(town_warp.UNCLE_VANILLA_COORDINATES, (0x0360, 0x0320))
        if ORIGINAL_BIN.exists():
            disc = ORIGINAL_BIN.read_bytes()
            raw = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA,
                town_warp._overlay_runtime_to_file_offset(
                    town_warp.UNCLE_PLACEMENT_COORDINATE_ADDRESS
                ),
            )
            self.assertEqual(
                struct.unpack("<2H", disc[raw:raw + 4]),
                town_warp.UNCLE_VANILLA_COORDINATES,
                "the placement coordinates moved on the disc; re-derive them",
            )

    def test_payload_uses_only_retired_uncle_script_and_audited_gap(self) -> None:
        expected_file_offsets = {
            town_warp._overlay_runtime_to_file_offset(
                town_warp.UNCLE_SELECTION_FUNCTION_POINTER_ADDRESS
            ),
            town_warp._overlay_runtime_to_file_offset(
                town_warp.UNCLE_DIALOGUE_GETTER_RETURN_INSTRUCTION_ADDRESS
            ),
            town_warp._overlay_runtime_to_file_offset(
                town_warp.UNCLE_PLACEMENT_COORDINATE_ADDRESS
            ),
            town_warp._overlay_runtime_to_file_offset(
                town_warp.NADA_PLACEMENT_COORDINATE_ADDRESS
            ),
            *(
                town_warp._dialogue_runtime_to_file_offset(address)
                for address in (
                    town_warp.WARP_LEVEL_2_CHECK_ADDRESS,
                    town_warp.WARP_LEVEL_4_CHECK_ADDRESS,
                    town_warp.WARP_LEVEL_6_CHECK_ADDRESS,
                    town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS,
                    town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS,
                    town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS,
                    town_warp.UNCLE_WARP_TRIGGER_ADDRESS,
                    *(
                        town_warp.UNCLE_REBUILT_FLOOR_CB_ADDRESS
                        + town_warp.UNCLE_REBUILT_FLOOR_CB_STRIDE * i
                        for i in range(3)
                    ),
                )
            ),
            # The resume trampoline over the town scene-transition FNO handler
            # at 0x800C24C8, which the angel's own hand-off calls. Its body is
            # a SLUS patch, not one of these.
            town_warp.town_mode_overlay_file_offset(
                town_warp.TOWN_SCENE_FNO_HANDLER_ADDRESS
            ),
        }
        self.assertEqual(set(self.patches), expected_file_offsets)

        script = self.patches[
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS
            )
        ]
        self.assertLessEqual(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS + len(script),
            town_warp.UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
        )

        for protected_address in (
            town_warp.UNCLE_VANILLA_DIALOGUE_ENTRY_ADDRESS,
            town_warp.UNCLE_RETIRED_SCRIPT_END_ADDRESS - 4,
            town_warp.UNCLE_NATIVE_CODE_START_ADDRESS,
            0x8001_EAE4,
            town_warp.UNCLE_NEXT_DIALOGUE_START_ADDRESS,
        ):
            self.assertNotIn(
                town_warp._dialogue_runtime_to_file_offset(protected_address),
                self.patches,
            )

    def test_retired_body_is_packed_without_gaps_or_overrun(self) -> None:
        blocks = (
            (town_warp.WARP_FLOOR_10_ADDRESS, town_warp._build_floor_warp(2, 10)),
            (town_warp.WARP_FLOOR_20_ADDRESS, town_warp._build_floor_warp(4, 20)),
            (town_warp.WARP_FLOOR_30_ADDRESS, town_warp._build_floor_warp(6, 30)),
            (town_warp.WARP_LEVEL_2_CHECK_ADDRESS, town_warp._build_level_check(2)),
            (town_warp.WARP_LEVEL_4_CHECK_ADDRESS, town_warp._build_level_check(4)),
            (town_warp.WARP_LEVEL_6_CHECK_ADDRESS, town_warp._build_level_check(6)),
            (
                town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS,
                town_warp._build_item_limit_check(),
            ),
            (
                town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS,
                town_warp._build_item_limit_page(),
            ),
        )
        cursor = town_warp.UNCLE_RETIRED_SCRIPT_CODE_START_ADDRESS
        for address, payload in blocks[:-1]:
            self.assertEqual(address, cursor)
            cursor = address + len(payload)
        # Only the last block may leave slack, and it must stay in the body.
        self.assertEqual(town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS, cursor)
        self.assertLessEqual(
            town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS + len(blocks[-1][1]),
            town_warp.UNCLE_RETIRED_SCRIPT_END_ADDRESS,
        )

    def test_compact_threshold_checks_and_floor_callbacks_fit(self) -> None:
        for required in (2, 4, 6):
            self.assertEqual(len(town_warp._build_level_check(required)), 20)
        for required, floor in ((2, 10), (4, 20), (6, 30)):
            self.assertEqual(len(town_warp._build_floor_warp(required, floor)), 0x20)
        # Assert against the slot rather than a constant: the routine grew to
        # 48 bytes when it learned to ignore -1, and the refusal page moved up
        # to make room. A hard-coded size hides which of the two is wrong.
        self.assertLessEqual(
            town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS
            + len(town_warp._build_item_limit_check()),
            town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS,
        )
        self.assertLessEqual(
            town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS
            + len(town_warp._build_item_limit_page()),
            town_warp.UNCLE_RETIRED_SCRIPT_END_ADDRESS,
        )

    def test_menu_uses_gated_two_three_and_four_choice_forms(self) -> None:
        script = town_warp._build_warp_script(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS,
            town_warp.UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
            floor_callbacks=tuple(
                town_warp.UNCLE_REBUILT_FLOOR_CB_ADDRESS
                + town_warp.UNCLE_REBUILT_FLOOR_CB_STRIDE * i
                for i in range(3)
            ),
        )
        self.assertIn(bytes((0x2C, 0x02, 0x1A)), script)
        self.assertIn(bytes((0x2C, 0x03, 0x1A)), script)
        self.assertIn(bytes((0x2C, 0x04, 0x1A)), script)
        self.assertNotIn(bytes((0x2C, 0x05, 0x1A)), script)
        for callback in (
            town_warp.WARP_LEVEL_2_CHECK_ADDRESS,
            town_warp.WARP_LEVEL_4_CHECK_ADDRESS,
            town_warp.WARP_LEVEL_6_CHECK_ADDRESS,
            town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS,
        ):
            self.assertIn(bytes((0x4C,)) + struct.pack("<I", callback), script)
        # v110: floor picks end silently - no floor-warp callbacks.
        for callback in (
            town_warp.WARP_FLOOR_10_ADDRESS,
            town_warp.WARP_FLOOR_20_ADDRESS,
            town_warp.WARP_FLOOR_30_ADDRESS,
        ):
            self.assertNotIn(
                bytes((0x4C,)) + struct.pack("<I", callback), script
            )

    def test_prompt_shares_the_page_with_the_choices(self) -> None:
        script = town_warp._build_warp_script(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS,
            town_warp.UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
            floor_callbacks=tuple(
                town_warp.UNCLE_REBUILT_FLOOR_CB_ADDRESS
                + town_warp.UNCLE_REBUILT_FLOOR_CB_STRIDE * i
                for i in range(3)
            ),
        )
        prompt = town_shop._encode_shop_name(
            town_warp.WARP_DESTINATION_PROMPT,
            max_characters=None,
        )[:-1]

        # Printed once, and V32's three separate prompt pages stay retired.
        self.assertEqual(script.count(prompt), 1)
        start = script.index(prompt)
        tail = script[start + len(prompt) :]

        # A row break, then the first menu's own row-start opcode and its first
        # bracketed choice: no page terminator in between, so the prompt and
        # the choices render together.
        self.assertEqual(tail[0], 0x0A)
        first_choice = tail.index(bytes((0x81, 0x6D)))
        self.assertNotIn(bytes((0x11, 0x01)), tail[:first_choice])

        # The only page that still ends before a menu is the locked message.
        self.assertEqual(script.count(bytes((0x11, 0x01))), 1)

    def test_script_refuses_an_oversized_inventory_before_the_menu(self) -> None:
        script = town_warp._build_warp_script(
            town_warp.UNCLE_REBUILT_SCRIPT_ADDRESS,
            town_warp.UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
            floor_callbacks=tuple(
                town_warp.UNCLE_REBUILT_FLOOR_CB_ADDRESS
                + town_warp.UNCLE_REBUILT_FLOOR_CB_STRIDE * i
                for i in range(3)
            ),
        )
        branch = (
            bytes((0x4C,))
            + struct.pack("<I", town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS)
            + bytes((0x3F, 0x0F))
            + struct.pack("<I", town_warp.WARP_ITEM_LIMIT_PAGE_ADDRESS)
        )
        self.assertIn(branch, script)
        prompt = town_shop._encode_shop_name(
            town_warp.WARP_DESTINATION_PROMPT,
            max_characters=None,
        )[:-1]
        self.assertLess(script.index(branch), script.index(prompt))

    def test_item_limit_page_is_a_terminated_message(self) -> None:
        page = town_warp._build_item_limit_page()
        self.assertTrue(page.endswith(bytes((0x11, 0x01))))
        self.assertEqual(
            page[:-2],
            town_shop._encode_shop_name(
                town_warp.WARP_ITEM_LIMIT_MESSAGE,
                max_characters=None,
            )[:-1],
        )

    def test_each_warp_rechecks_its_keycard_threshold(self) -> None:
        for required, floor in ((2, 10), (4, 20), (6, 30)):
            payload = town_warp._build_floor_warp(required, floor)
            self.assertIn(
                struct.pack("<I", town_shop._i(0x0B, 9, 10, required)),
                payload,
            )
            self.assertIn(
                struct.pack(
                    "<I",
                    town_shop._i(
                        0x09,
                        0,
                        4,
                        town_warp.TOWER_FLOOR_MARKER | (floor - 1),
                    ),
                ),
                payload,
            )
            self.assertIn(
                struct.pack(
                    "<I",
                    town_shop._j(0x02, town_warp.UNCLE_WARP_TRIGGER_ADDRESS),
                ),
                payload,
            )

    def test_actor_scavenging_helper_is_retired(self) -> None:
        # The V35 helper (scan the actor list, retarget a door actor's
        # descriptor) was the shortcut flake's home: a missed scan was a
        # silent no-op.  v114 replaces it with the deterministic trigger;
        # the builder survives for reference but is never emitted.
        self.assertNotIn(
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.WARP_ACTIVATE_TOWER_ENTRANCE_ADDRESS
            ),
            self.patches,
        )
        self.assertTrue(len(town_warp._build_activate_tower_entrance()) > 0)

    def test_native_town_floor_initializer_is_no_longer_patched(self) -> None:
        self.assertNotIn(
            town_shop.town_runtime_to_file_offset(0x800A_0EE0),
            self.patches,
        )

    def test_floor_overlay_bootstrap_routes_new_run_to_marker_helper(self) -> None:
        patches = dict(town_warp.iter_tower_floor_bootstrap_file_patches())
        expected_hook = struct.pack(
            "<I",
            town_shop._j(
                0x02,
                patch.TOWER_FLOOR_BOOTSTRAP_HELPER_ADDRESS,
            ),
        )
        expected_offsets = {
            copy_offset + town_warp.TOWER_FLOOR_BOOTSTRAP_HOOK_OFFSET
            for copy_offset in town_warp.FLOOR_GENERATION_FILE_OFFSETS
        }

        self.assertEqual(set(patches), expected_offsets)
        self.assertEqual(
            expected_offsets,
            {0x2A_3448, 0x51_EC48},
        )
        for copy_offset in town_warp.FLOOR_GENERATION_FILE_OFFSETS:
            self.assertEqual(
                patches[
                    copy_offset
                    + town_warp.TOWER_FLOOR_BOOTSTRAP_HOOK_OFFSET
                ],
                expected_hook,
            )

    def test_retired_wrapper_allocation_now_contains_actor_helper(self) -> None:
        # v110: the activate-entrance helper (and the old script and floor
        # callbacks) are no longer emitted - the rebuilt dialogue never
        # calls them, and the Kewne-sensitive zero-gap region stays
        # untouched vanilla.  The builder survives for the tower-load
        # rebuild to reference.
        self.assertNotIn(
            town_warp._dialogue_runtime_to_file_offset(
                town_warp.WARP_ACTIVATE_TOWER_ENTRANCE_ADDRESS
            ),
            self.patches,
        )
        self.assertTrue(len(town_warp._build_activate_tower_entrance()) > 0)

    BEGIN_SCRATCH = 0x801F_0000  # records begin_scene_transition's a0
    INIT_SCRATCH = 0x801F_0004   # records that initialize_new_run ran

    def _callback_address(self, index: int) -> int:
        return (
            town_warp.UNCLE_REBUILT_FLOOR_CB_ADDRESS
            + town_warp.UNCLE_REBUILT_FLOOR_CB_STRIDE * index
        )

    def _load_warp_code(self, memory) -> None:
        for index, (level, floor) in enumerate(((2, 10), (4, 20), (6, 30))):
            memory.load_bytes(
                self._callback_address(index),
                town_warp._build_floor_warp(level, floor),
            )
        for address, payload in (
            (town_warp.WARP_LEVEL_2_CHECK_ADDRESS, town_warp._build_level_check(2)),
            (town_warp.WARP_LEVEL_4_CHECK_ADDRESS, town_warp._build_level_check(4)),
            (town_warp.WARP_LEVEL_6_CHECK_ADDRESS, town_warp._build_level_check(6)),
            (
                town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS,
                town_warp._build_item_limit_check(),
            ),
            (town_warp.UNCLE_WARP_TRIGGER_ADDRESS, town_warp._build_warp_trigger()),
        ):
            memory.load_bytes(address, payload)
        # Native stubs: begin_scene_transition_from_descriptor records its
        # a0; initialize_new_tower_run_state records that it ran.
        memory.load_bytes(
            town_warp.BEGIN_SCENE_TRANSITION_FROM_DESCRIPTOR_ADDRESS,
            struct.pack(
                "<4I",
                town_shop._i(0x0F, 0, 8, 0x801F),   # lui t0,0x801F
                town_shop._i(0x2B, 8, 4, 0),        # sw a0,0(t0)
                town_shop._r(31, 0, 0, 0, 0x08),    # jr ra
                0,
            ),
        )
        memory.load_bytes(
            town_warp.INITIALIZE_NEW_TOWER_RUN_STATE_ADDRESS,
            struct.pack(
                "<5I",
                town_shop._i(0x0F, 0, 8, 0x801F),   # lui t0,0x801F
                town_shop._i(0x09, 0, 9, 1),        # li t1,1
                town_shop._i(0x2B, 8, 9, 4),        # sw t1,4(t0)
                town_shop._r(31, 0, 0, 0, 0x08),    # jr ra
                0,
            ),
        )

    def test_threshold_checks_report_the_real_keycard_level(self) -> None:
        for required, address in (
            (2, town_warp.WARP_LEVEL_2_CHECK_ADDRESS),
            (4, town_warp.WARP_LEVEL_4_CHECK_ADDRESS),
            (6, town_warp.WARP_LEVEL_6_CHECK_ADDRESS),
        ):
            for level in range(0, 9):
                memory = mips_sim.Memory()
                self._load_warp_code(memory)
                memory.write32(town_warp.KEYCARD_LEVEL_ADDRESS, level)
                cpu = mips_sim.Cpu(memory)
                self.assertEqual(
                    cpu.run(address),
                    1 if level >= required else 0,
                    f"keycard {level} against threshold {required}",
                )

    def _run_item_limit(
        self, occupied_slots, deleted_slots=(), stale_slots=()
    ) -> int:
        """Held items live in the DESCRIPTORS; the order table is noise.

        `occupied_slots` fills descriptors with a non-zero category, which is
        the game's own definition of an occupied slot.  `deleted_slots` and
        `stale_slots` write junk into the order table - deletion markers and
        leftover pointers past a compaction - and the count must ignore both.
        """

        memory = mips_sim.Memory()
        self._load_warp_code(memory)
        for slot in occupied_slots:
            descriptor = (
                town_warp.INVENTORY_DESCRIPTOR_TABLE_ADDRESS
                + slot * town_warp.INVENTORY_DESCRIPTOR_SIZE
            )
            # [id, category, quality, status]; category is what marks it live.
            memory.write32(descriptor, 0x00AD_0B01)
            memory.write8(
                descriptor + town_warp.INVENTORY_DESCRIPTOR_CATEGORY_OFFSET,
                0x0B,
            )
            memory.write32(
                town_warp.INVENTORY_ORDER_TABLE_ADDRESS + slot * 4, descriptor
            )
        for slot in deleted_slots:
            memory.write32(
                town_warp.INVENTORY_ORDER_TABLE_ADDRESS + slot * 4, 0xFFFF_FFFF
            )
        for slot in stale_slots:
            # A real, in-range pointer left behind past the zero terminator.
            memory.write32(
                town_warp.INVENTORY_ORDER_TABLE_ADDRESS + slot * 4,
                town_warp.INVENTORY_DESCRIPTOR_TABLE_ADDRESS + slot * 4,
            )
        return mips_sim.Cpu(memory).run(town_warp.WARP_ITEM_LIMIT_CHECK_ADDRESS)

    def test_item_limit_check_counts_every_occupied_descriptor(self) -> None:
        for held in range(0, town_warp.INVENTORY_DESCRIPTOR_SLOT_COUNT + 1):
            self.assertEqual(
                self._run_item_limit(range(held)),
                1 if held > town_warp.TOWER_ENTRY_ITEM_LIMIT else 0,
                f"{held} items held",
            )

    def test_item_limit_check_survives_holes_and_a_full_array(self) -> None:
        # Descriptors are allocated by index, not packed, so holes are normal.
        self.assertEqual(self._run_item_limit((0, 1, 3, 5, 7, 9)), 1)
        self.assertEqual(self._run_item_limit((0, 2, 4, 6)), 0)
        self.assertEqual(
            self._run_item_limit(
                range(town_warp.INVENTORY_DESCRIPTOR_SLOT_COUNT)
            ),
            1,
        )

    def test_item_limit_check_ignores_deletion_markers(self) -> None:
        # Live capture, empty bag: seven consecutive 0xFFFFFFFF entries in the
        # order table with real pointers on both sides.
        self.assertEqual(self._run_item_limit((), deleted_slots=range(7)), 0)
        self.assertEqual(
            self._run_item_limit(
                (), deleted_slots=range(town_warp.INVENTORY_DESCRIPTOR_SLOT_COUNT)
            ),
            0,
        )
        self.assertEqual(
            self._run_item_limit(range(5), deleted_slots=range(8, 15)), 0
        )
        self.assertEqual(
            self._run_item_limit(range(6), deleted_slots=range(8, 15)), 1
        )

    def test_item_limit_check_ignores_stale_pointers_after_compaction(
        self,
    ) -> None:
        """The Monster Shop regression: the tail past the terminator is live-
        looking pointers, not markers, so no value test on the order table can
        reject them.  Counting descriptors does not have to.
        """

        # An empty bag whose order table is entirely stale pointers.
        self.assertEqual(
            self._run_item_limit(
                (),
                stale_slots=range(town_warp.INVENTORY_DESCRIPTOR_SLOT_COUNT),
            ),
            0,
        )
        # A shop compaction leaving markers AND a stale tail behind.
        self.assertEqual(
            self._run_item_limit(
                (), deleted_slots=range(3, 8), stale_slots=range(8, 20)
            ),
            0,
        )
        # Five real items with a filthy order table still boards.
        self.assertEqual(
            self._run_item_limit(
                range(5), deleted_slots=range(6, 9), stale_slots=range(9, 20)
            ),
            0,
        )
        # Six real items is still refused - the limit itself must keep working.
        self.assertEqual(
            self._run_item_limit(
                range(6), deleted_slots=range(6, 9), stale_slots=range(9, 20)
            ),
            1,
        )

    def _run_floor_warp(self, floor: int, level: int):
        memory = mips_sim.Memory()
        self._load_warp_code(memory)
        memory.write32(town_warp.KEYCARD_LEVEL_ADDRESS, level)
        index = {10: 0, 20: 1, 30: 2}[floor]
        mips_sim.Cpu(memory).run(self._callback_address(index))
        return memory

    def test_floor_callbacks_stage_a_marked_floor_and_start_the_transition(
        self,
    ) -> None:
        for floor, required in ((10, 2), (20, 4), (30, 6)):
            memory = self._run_floor_warp(floor, required)
            self.assertEqual(
                memory.read16(town_warp.PERSISTENT_TOWER_FLOOR_ADDRESS),
                town_warp.TOWER_FLOOR_MARKER | floor,
                f"floor {floor}",
            )
            self.assertEqual(
                memory.read32(self.BEGIN_SCRATCH),
                town_warp.TOWER_ROAD_TRANSITION_DESCRIPTOR_ADDRESS,
                "begin_scene_transition did not get the tower descriptor",
            )
            self.assertEqual(
                memory.read32(self.INIT_SCRATCH), 1,
                "initialize_new_tower_run_state did not run",
            )

    def test_a_failed_recheck_stages_nothing_and_never_starts_a_transition(
        self,
    ) -> None:
        for floor, required in ((10, 2), (20, 4), (30, 6)):
            memory = self._run_floor_warp(floor, required - 1)
            self.assertEqual(
                memory.read16(town_warp.PERSISTENT_TOWER_FLOOR_ADDRESS), 0
            )
            self.assertEqual(memory.read32(self.BEGIN_SCRATCH), 0)
            self.assertEqual(memory.read32(self.INIT_SCRATCH), 0)

    def test_trigger_refuses_a_zero_request(self) -> None:
        memory = mips_sim.Memory()
        self._load_warp_code(memory)
        cpu = mips_sim.Cpu(memory)
        cpu.registers[4] = 0  # a0: a refused request
        cpu.run(town_warp.UNCLE_WARP_TRIGGER_ADDRESS)
        self.assertEqual(
            memory.read16(town_warp.PERSISTENT_TOWER_FLOOR_ADDRESS), 0
        )
        self.assertEqual(memory.read32(self.BEGIN_SCRATCH), 0)
        self.assertEqual(memory.read32(self.INIT_SCRATCH), 0)

    def test_mode2_patches_do_not_cross_form1_payload_boundaries(self) -> None:
        for raw_offset, data in town_warp.iter_town_warp_raw_patches():
            within_sector = raw_offset % 2_352
            self.assertGreaterEqual(within_sector, 24)
            self.assertLessEqual(within_sector + len(data), 24 + 2_048)


if __name__ == "__main__":
    unittest.main()
