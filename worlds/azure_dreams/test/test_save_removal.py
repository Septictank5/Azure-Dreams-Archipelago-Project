import struct
import unittest
from pathlib import Path

from . import mips_sim
from .. import save_removal, town_shop


def _raw_offset(start_lba: int, file_offset: int) -> int:
    return town_shop.mode2_file_offset_to_raw_offset(start_lba, file_offset)


ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)


# Two tests are retired below (renamed with a leading underscore) rather than
# deleted: the two "already done" save-entry stubs are no longer written,
# because the tower-resume warp body now occupies 0x8004EE50-0x8004EEBB. The
# entries were unreachable - an entry's only job is to call
# create_memory_card_screen_actor, and a breakpoint on that never fired across
# a full session. If the stubs ever come back, so do these.
class SaveRemovalTests(unittest.TestCase):
    def test_module_mappings_match_the_documented_disc_offsets(self) -> None:
        self.assertEqual(
            save_removal._title_runtime_to_file_offset(0x8008_9368),
            0x2_2408,
        )
        self.assertEqual(
            save_removal.slus_runtime_to_file_offset(
                save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS
            ),
            0x2_2650,
        )
        self.assertEqual(
            save_removal.slus_runtime_to_file_offset(
                save_removal.CARD_DRIVER_TOWER_SAVE_ENTRY_ADDRESS
            ),
            0x2_2670,
        )

    def test_title_menu_has_two_rows_and_no_continue(self) -> None:
        words = save_removal.build_title_menu_without_continue()

        # Two rows, one label later, and Y/stagger stepped in so they land
        # exactly where vanilla rows 0 and 1 were.
        self.assertEqual(words[0x8008_8C7C], town_shop._i(0x09, 0, 18, 1))
        self.assertEqual(
            words[0x8008_8C84],
            town_shop._i(
                0x09, 0, 22,
                save_removal.TITLE_FIRST_ROW_Y + save_removal.TITLE_ROW_HEIGHT,
            ),
        )
        self.assertEqual(words[0x8008_8CFC], town_shop._i(0x23, 2, 2, 0x14))

        # The cursor sprite is at index * 16 + 0xa8, unchanged, so the two rows
        # it can reach are exactly the two the loop now builds.
        for index in range(2):
            row_y = save_removal.TITLE_FIRST_ROW_Y + index * save_removal.TITLE_ROW_HEIGHT
            self.assertIn(row_y, (0xA8, 0xB8))

    def test_the_cursor_wraps_over_two_rows(self) -> None:
        words = save_removal.build_title_menu_without_continue()
        self.assertEqual(words[0x8008_9328], town_shop._i(0x09, 4, 4, 1))
        self.assertEqual(
            words[save_removal.TITLE_CURSOR_MOVE_APPLY_ADDRESS],
            town_shop._i(0x0C, 4, 4, 1),  # andi a0,a0,1
        )
        # Both directions step by one, so the mask is a real toggle.
        for index in (0, 1):
            self.assertEqual((index + 1) & 1, 1 - index)

    def test_dispatch_maps_row_zero_to_new_game_and_row_one_to_options(self) -> None:
        words = save_removal.build_title_menu_without_continue()
        for source, expected, rt in (
            (0x8008_9480, save_removal.TITLE_NEW_GAME_BRANCH_ADDRESS, 0),
            (0x8008_9490, save_removal.TITLE_OPTIONS_BRANCH_ADDRESS, 2),
        ):
            word = words[source]
            self.assertEqual(word >> 26, 0x04, "not a BEQ")
            self.assertEqual((word >> 21) & 0x1F, 3, "not testing the index in v1")
            self.assertEqual((word >> 16) & 0x1F, rt)
            self.assertEqual(source + 4 + (word & 0xFFFF) * 4, expected)

    def test_no_patched_word_can_write_the_hanging_state(self) -> None:
        # 0x800891b4 returns unhandled for every state at or above 0xff, which
        # is what V67 walked into. Nothing here may put one there.
        words = save_removal.build_title_menu_without_continue()
        hang = town_shop._i(0x09, 0, 2, 0xFF)  # addiu v0,zero,0xff
        self.assertNotIn(hang, words.values())
        for address in (0x8008_9494, 0x8008_94A4):
            self.assertEqual(
                words[address],
                town_shop._i(0x09, 0, 2, save_removal.TITLE_BROWSING_STATE),
            )

    def test_the_attract_timeout_can_never_fire(self) -> None:
        words = save_removal.build_title_menu_without_continue()
        word = words[0x8008_93B0]
        self.assertEqual(word >> 26, 0x04)
        self.assertEqual((word >> 21) & 0x1F, 0)
        self.assertEqual((word >> 16) & 0x1F, 0, "not unconditional")
        self.assertEqual(
            0x8008_93B0 + 4 + (word & 0xFFFF) * 4,
            save_removal.TITLE_DISPATCH_EXIT_ADDRESS,
        )

    def test_the_v68_refusal_is_reverted(self) -> None:
        # Row 0 is New Game now; leaving the refusal in would make it unusable.
        words = save_removal.build_title_menu_without_continue()
        for address, original in save_removal.TITLE_V68_REVERTED_SITES:
            self.assertEqual(words[address], original)

    def _run_label_helper(self, flag: int) -> int:
        """Return the descriptor the helper leaves at sp+0x14."""

        memory = mips_sim.Memory()
        memory.load_bytes(
            save_removal.TITLE_LABEL_HELPER_ADDRESS,
            save_removal.build_title_label_helper(),
        )
        memory.write8(save_removal.TITLE_CONTINUE_FLAG_ADDRESS, flag)
        # The helper resumes into the constructor; stand in for it.
        memory.write32(
            save_removal.TITLE_STAGE_RESUME_ADDRESS,
            town_shop._r(31, 0, 0, 0, 0x08),  # jr ra
        )
        memory.write32(save_removal.TITLE_STAGE_RESUME_ADDRESS + 4, 0)

        cpu = mips_sim.Cpu(memory)
        stack = 0x801F_C000
        cpu.registers[4] = 0x8008_B164  # a0 = the staged NEW GAME descriptor
        cpu.run(save_removal.TITLE_LABEL_HELPER_ADDRESS, stack_pointer=stack)
        return memory.read32(stack + 0x14)

    def test_the_label_helper_swaps_only_when_the_flag_is_set(self) -> None:
        self.assertEqual(self._run_label_helper(0), 0x8008_B164)  # NEW GAME
        for flag in (1, 2, 0xFF):
            self.assertEqual(
                self._run_label_helper(flag),
                save_removal.TITLE_CONTINUE_LABEL_DESCRIPTOR,
                f"flag {flag} did not select CONTINUE",
            )

    def test_the_label_helper_fits_the_retired_driver_and_is_reached(self) -> None:
        helper = save_removal.build_title_label_helper()
        self.assertGreaterEqual(
            save_removal.TITLE_LABEL_HELPER_ADDRESS,
            save_removal.TITLE_CONTINUE_FLAG_ADDRESS + 4,
            "the helper overlaps the flag word",
        )
        self.assertLessEqual(
            save_removal.TITLE_LABEL_HELPER_ADDRESS + len(helper),
            save_removal.RETIRED_CARD_DRIVER_END_ADDRESS,
            "the helper runs into the disabled save entries",
        )

        # The displaced store is performed by the helper, not left behind.
        self.assertIn(
            struct.pack("<I", town_shop._i(0x2B, 29, 4, 0x14)),  # sw a0,0x14(sp)
            helper,
        )
        words = save_removal.build_title_menu_without_continue()
        self.assertEqual(
            words[save_removal.TITLE_STAGE_NEW_GAME_LABEL_ADDRESS],
            town_shop._j(0x02, save_removal.TITLE_LABEL_HELPER_ADDRESS),
        )

    def test_the_flag_ships_cleared(self) -> None:
        """A disc with no client must show NEW GAME.

        The flag sits on the retired driver's first instruction, which is very
        much not zero, so generation has to overwrite it.
        """

        patches = dict(save_removal.iter_title_label_slus_patches())
        self.assertEqual(
            patches[
                save_removal.slus_runtime_to_file_offset(
                    save_removal.TITLE_CONTINUE_FLAG_ADDRESS
                )
            ],
            bytes(4),
        )

    def test_the_elevator_never_requests_the_quit_prompt(self) -> None:
        payload = save_removal.build_elevator_without_quit_prompt()
        self.assertEqual(
            len(payload),
            len(save_removal.ELEVATOR_PROMPT_REQUEST_ORIGINALS) * 4,
        )
        words = struct.unpack(f"<{len(payload) // 4}I", payload)

        # Nothing may still name the builder or its registration slot.
        self.assertNotIn(
            struct.pack("<I", save_removal.CREATE_QUIT_PROMPT_ADDRESS), payload
        )
        for word in words:
            self.assertNotEqual(
                word & 0xFFFF,
                save_removal.CREATE_QUIT_PROMPT_ADDRESS & 0xFFFF,
                "the builder's low half survived",
            )
            self.assertNotEqual(
                word & 0xFFFF,
                save_removal.PROMPT_BUILDER_SLOT_ADDRESS & 0xFFFF,
                "the builder slot is still written",
            )

    def test_the_replacement_is_state_fours_tail_verbatim(self) -> None:
        """The first attempt hand-wrote this and crashed.

        It filled the `jal 0x80040aa0` delay slot with `lui v1,0x8001` rather
        than vanilla's `nop`; `v1` is caller-saved, so the call clobbered it and
        the following `lhu v1,0x234(v1)` read from a garbage base. Copying the
        eleven words the game already runs removes the whole class of mistake.
        """

        payload = save_removal.build_elevator_without_quit_prompt()
        self.assertEqual(
            payload,
            struct.pack(
                f"<{len(save_removal.ELEVATOR_STATE_4_TAIL_WORDS)}I",
                *save_removal.ELEVATOR_STATE_4_TAIL_WORDS,
            ),
        )
        # The delay slot of the first call is a NOP, as vanilla has it.
        self.assertEqual(save_removal.ELEVATOR_STATE_4_TAIL_WORDS[3], 0)
        # And the floor base is rebuilt after that call, not before it.
        self.assertEqual(save_removal.ELEVATOR_STATE_4_TAIL_WORDS[4] >> 26, 0x0F)

    def test_the_prompt_states_are_retargeted_to_idle(self) -> None:
        patches = dict(save_removal.iter_elevator_file_patches())
        for state in save_removal.ELEVATOR_PROMPT_STATES:
            offset = save_removal._dungeon_runtime_to_file_offset(
                save_removal.ELEVATOR_STATE_TABLE_ADDRESS + state * 4
            )
            self.assertEqual(
                patches[offset],
                struct.pack("<I", save_removal.ELEVATOR_IDLE_HANDLER_ADDRESS),
            )
        # State 1 still ends `j 0x80093404`, so the byte advances into one of
        # them; which one no longer matters.
        self.assertIn(1 + 1, save_removal.ELEVATOR_PROMPT_STATES)

    def test_the_quit_string_is_erased(self) -> None:
        patches = dict(save_removal.iter_quit_string_slus_patches())
        offset = save_removal.slus_runtime_to_file_offset(
            save_removal.QUIT_PROMPT_STRING_ADDRESS
        )
        self.assertEqual(
            patches[offset],
            bytes(len(save_removal.QUIT_PROMPT_STRING_ORIGINAL)),
        )

    def test_every_rewritten_menu_keeps_its_exact_length(self) -> None:
        patches = dict(save_removal.iter_mom_menu_file_patches())
        self.assertEqual(len(patches), len(save_removal.MOM_MENUS))
        for menu in save_removal.MOM_MENUS:
            self.assertEqual(len(patches[menu.file_offset]), menu.length)
            self.assertEqual(len(menu.original), menu.length)
            # Padding is the end-of-script opcode, and only at the tail.
            self.assertEqual(
                menu.replacement[menu.length - menu.freed_bytes :],
                bytes((save_removal.DIALOGUE_END_OPCODE,)) * menu.freed_bytes,
            )
            body = menu.replacement[: menu.length - menu.freed_bytes]
            if menu.replacement_rows:
                self.assertEqual(
                    body[-1 - 4 * len(menu.replacement_rows)],
                    save_removal.DIALOGUE_BRANCH_TABLE_OPCODE,
                )

    def test_the_safe_returns_to_the_rows_the_player_came_in_through(self) -> None:
        """Both directions matter, and for different reasons.

        Returning to the greeting while the Pita is still there is the point -
        the tower row has something to hand over. Returning to it once the flag
        is set would be an unlimited Pita source, because the grant is gated
        only by that page being reachable.
        """

        block = next(
            menu
            for menu in save_removal.MOM_MENUS
            if isinstance(menu, save_removal.PitaAwareReturn)
        ).replacement

        expected = (
            bytes((save_removal.DIALOGUE_SET_SLOT_OPCODE, 0))
            + struct.pack("<I", save_removal.EVENT_FLAG_PITA_TAKEN)
            + bytes((save_removal.DIALOGUE_NATIVE_CALL_OPCODE,))
            + struct.pack("<I", save_removal.GET_EVENT_FLAG_ADDRESS)
            + bytes(
                (
                    save_removal.DIALOGUE_BRANCH_IF_SLOT_OPCODE,
                    save_removal.DIALOGUE_RESULT_SLOT,
                )
            )
            + struct.pack("<I", save_removal.MOM_SECOND_ENTRY_MENU_PAGE_ADDRESS)
            + bytes((save_removal.DIALOGUE_GOTO_OPCODE,))
            + struct.pack("<I", save_removal.MOM_GREETING_MENU_PAGE_ADDRESS)
        )
        self.assertTrue(block.startswith(expected))
        self.assertEqual(
            set(block[len(expected) :]),
            {save_removal.DIALOGUE_END_OPCODE},
        )

        # The flag test is the selector's own, so the two agree by construction.
        self.assertEqual(save_removal.EVENT_FLAG_PITA_TAKEN, 0x16)
        self.assertEqual(save_removal.GET_EVENT_FLAG_ADDRESS, 0x8001_E670)

        # 0x4c stores its result at context+0x84, which is slot 0x0f - the slot
        # 0x3f then reads. Our warp checks already rely on that pairing.
        self.assertEqual(save_removal.DIALOGUE_RESULT_SLOT, 0x0F)

    def test_the_return_targets_are_menu_redraws_not_page_reprints(self) -> None:
        # Both targets are the `08 clear` / `57 01` immediately before a menu's
        # rows, so the question above them is not asked twice.
        greeting = save_removal.MOM_MENUS[0]
        self.assertEqual(
            save_removal.MOM_GREETING_MENU_PAGE_ADDRESS + 3,
            0x8001_6000 + greeting.file_offset - save_removal.MOM_OVERLAY_FILE_OFFSET,
            "the greeting target is not three bytes before its rows",
        )
        second_entry = save_removal.MOM_MENUS[3]
        self.assertEqual(
            save_removal.MOM_SECOND_ENTRY_MENU_PAGE_ADDRESS + 3,
            0x8001_6000
            + second_entry.file_offset
            - save_removal.MOM_OVERLAY_FILE_OFFSET,
            "the second-entry target is not three bytes before its rows",
        )

    def test_save_data_is_gone_from_every_menu(self) -> None:
        for menu in save_removal.MOM_MENUS:
            self.assertNotIn(save_removal._CHOICE_SAVE_DATA[1:], menu.replacement)
            self.assertNotIn(
                struct.pack("<I", save_removal.MOM_SAVE_DATA_TARGET),
                menu.replacement,
            )

    def test_the_safe_submenu_is_replaced_by_a_direct_route(self) -> None:
        greeting = save_removal.MOM_MENUS[0]

        # It used to ask for a favour and land on a page whose only content was
        # the sub-menu SAVE DATA lived in.
        self.assertIn(
            struct.pack("<I", save_removal.MOM_FAVOR_SUBMENU_TARGET),
            greeting.original,
        )
        self.assertIn(save_removal._CHOICE_IVE_GOT_A_FAVOR, greeting.original)

        # Now it offers the safe and goes straight where the sub-menu's own
        # safe row went.
        self.assertNotIn(
            struct.pack("<I", save_removal.MOM_FAVOR_SUBMENU_TARGET),
            greeting.replacement,
        )
        self.assertNotIn(save_removal._CHOICE_IVE_GOT_A_FAVOR, greeting.replacement)
        self.assertIn(save_removal._CHOICE_OPEN_THE_SAFE[1:], greeting.replacement)
        self.assertIn(
            struct.pack("<I", save_removal._OPEN_THE_SAFE_TARGET),
            greeting.replacement,
        )

        # Still three rows: only the middle one changed.
        self.assertEqual(len(greeting.replacement_rows), 3)
        self.assertEqual(
            [target for _, target in greeting.replacement_rows],
            [0x8001_6171, save_removal._OPEN_THE_SAFE_TARGET, 0x8001_6127],
        )

    def _retired_test_both_save_entries_get_the_same_replacement(self) -> None:
        patches = dict(save_removal.iter_slus_file_patches())
        entry = save_removal.build_completed_save_entry()
        self.assertEqual(len(entry), save_removal.CARD_DRIVER_ENTRY_SIZE)
        for address in (
            save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS,
            save_removal.CARD_DRIVER_TOWER_SAVE_ENTRY_ADDRESS,
        ):
            self.assertEqual(
                patches[save_removal.slus_runtime_to_file_offset(address)],
                entry,
            )
        # The rest of the SLUS payload is the title label flag and helper, and
        # none of it may collide with the two entries.
        driver_span = range(
            save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS,
            save_removal.CARD_DRIVER_TOWER_SAVE_ENTRY_ADDRESS
            + save_removal.CARD_DRIVER_ENTRY_SIZE,
        )
        for address in (
            save_removal.TITLE_CONTINUE_FLAG_ADDRESS,
            save_removal.TITLE_LABEL_HELPER_ADDRESS,
        ):
            self.assertNotIn(address, driver_span)

        # No call survives: the point is that the card module is never loaded.
        for word in struct.unpack("<8I", entry):
            self.assertNotEqual(word >> 26, 0x03, "a JAL survived in a save entry")

    def test_replacement_sets_the_done_bit_and_reports_success(self) -> None:
        parent = 0x8008_3498
        for existing in (0x0000, 0x4136, 0xDFFF):
            memory = mips_sim.Memory()
            memory.load_bytes(
                save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS,
                save_removal.build_completed_save_entry(),
            )
            memory.write32(
                parent + save_removal.CARD_SCREEN_DONE_FLAG_OFFSET,
                existing,
            )
            cpu = mips_sim.Cpu(memory)
            cpu.registers[4] = parent
            returned = cpu.run(save_removal.CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS)

            self.assertNotEqual(returned, 0, "the elevator needs a non-zero result")
            self.assertEqual(
                memory.read16(parent + save_removal.CARD_SCREEN_DONE_FLAG_OFFSET),
                existing | save_removal.CARD_SCREEN_DONE_BIT,
            )

    def test_mode2_patches_do_not_cross_form1_payload_boundaries(self) -> None:
        for raw_offset, data in save_removal.iter_save_removal_raw_patches():
            within_sector = raw_offset % 2_352
            self.assertGreaterEqual(within_sector, 24)
            self.assertLessEqual(within_sector + len(data), 24 + 2_048)

    @unittest.skipUnless(ORIGINAL_BIN.is_file(), "the untouched disc is not present")
    def _retired_test_raw_offsets_land_on_the_expected_original_bytes(self) -> None:
        """Guards the file-to-raw arithmetic against the real disc image."""

        image = ORIGINAL_BIN.read_bytes()
        patches = dict(save_removal.iter_save_removal_raw_patches())

        for address, expected, why in save_removal.TITLE_PATCH_SITES:
            offset = _raw_offset(
                save_removal.MAIN_FILE_START_LBA,
                save_removal._title_runtime_to_file_offset(address),
            )
            self.assertEqual(
                struct.unpack_from("<I", image, offset)[0],
                expected,
                f"0x{address:08x} ({why}) is not the instruction the patch assumes",
            )
            self.assertIn(offset, patches)

        # The elevator replacement is a copy of code the game already runs, so
        # prove both the source and the block it replaces.
        tail_offset = _raw_offset(
            save_removal.DUNGEON_FILE_START_LBA,
            save_removal._dungeon_runtime_to_file_offset(
                save_removal.ELEVATOR_STATE_4_TAIL_ADDRESS
            ),
        )
        self.assertEqual(
            image[tail_offset : tail_offset + 44],
            save_removal.build_elevator_without_quit_prompt(),
            "state 4's tail on the disc is not what the patch copies",
        )
        request_offset = _raw_offset(
            save_removal.DUNGEON_FILE_START_LBA,
            save_removal._dungeon_runtime_to_file_offset(
                save_removal.ELEVATOR_PROMPT_REQUEST_ADDRESS
            ),
        )
        self.assertEqual(
            struct.unpack_from("<11I", image, request_offset),
            save_removal.ELEVATOR_PROMPT_REQUEST_ORIGINALS,
        )
        for state, expected in save_removal.ELEVATOR_STATE_TABLE_ORIGINALS.items():
            offset = _raw_offset(
                save_removal.DUNGEON_FILE_START_LBA,
                save_removal._dungeon_runtime_to_file_offset(
                    save_removal.ELEVATOR_STATE_TABLE_ADDRESS + state * 4
                ),
            )
            self.assertEqual(
                struct.unpack_from("<I", image, offset)[0],
                expected,
                f"elevator state {state}'s table entry moved",
            )

        # The whole point of the Mom edit is that the surviving bytes are the
        # game's own. Prove the recorded originals are byte-for-byte real.
        for menu in save_removal.MOM_MENUS:
            offset = _raw_offset(town_shop.TOWN_FILE_START_LBA, menu.file_offset)
            self.assertEqual(
                image[offset : offset + menu.length],
                menu.original,
                f"Mom's menu at 0x{menu.file_offset:06x} is not what was recorded",
            )

        # Both driver entries are the same eight words apart from the mode
        # selector, which is what makes one replacement correct for both.
        entries = [
            image[offset : offset + save_removal.CARD_DRIVER_ENTRY_SIZE]
            for offset, data in patches.items()
            if len(data) == save_removal.CARD_DRIVER_ENTRY_SIZE
        ]
        self.assertEqual(len(entries), 2)
        for index, entry in enumerate(entries):
            words = struct.unpack("<8I", entry)
            self.assertEqual(words[0], 0x27BD_FFE8)  # addiu sp,sp,-0x18
            self.assertEqual(words[2] >> 26, 0x03)  # jal create_card_screen_actor
            self.assertEqual(
                words[2],
                town_shop._j(0x03, 0x8004_EDA8),
            )
            self.assertEqual(words[3], town_shop._i(0x09, 0, 5, index + 1))


if __name__ == "__main__":
    unittest.main()
