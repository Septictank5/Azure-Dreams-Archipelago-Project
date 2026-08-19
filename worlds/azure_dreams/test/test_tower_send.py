"""Tests for the tower players-menu Send row (M1 dispatch skeleton)."""

import struct
import unittest

from .. import alternate_pickup, patch, save_removal, tower_send


class TowerSendLayoutTests(unittest.TestCase):
    def test_handler_table_is_vanilla_rows_plus_send(self) -> None:
        table = tower_send.build_handler_table()
        self.assertEqual(len(table), 28)
        handlers = struct.unpack("<7I", table)
        # Items and Feet route through the flag-clearing stubs; the middle
        # four rows stay vanilla; Send sits ABOVE Feet (2026-08-08 swap) so
        # up-from-the-top still wraps to Feet for the speedrun habit.
        self.assertEqual(handlers[0], tower_send.ITEMS_STUB_ADDRESS)
        self.assertEqual(handlers[1:5], tower_send.VANILLA_HANDLERS[1:5])
        self.assertEqual(handlers[5], tower_send.SEND_HANDLER_ROUTINE_ADDRESS)
        self.assertEqual(handlers[6], tower_send.FEET_STUB_ADDRESS)

    def test_clear_flag_stubs_fit_and_clear_the_flag(self) -> None:
        for stub_address, handler in (
            (tower_send.ITEMS_STUB_ADDRESS, tower_send.ITEMS_HANDLER_ADDRESS),
            (tower_send.FEET_STUB_ADDRESS, tower_send.FEET_HANDLER_ADDRESS),
        ):
            stub = tower_send._build_clear_flag_stub(handler)
            self.assertEqual(len(stub), 16)
            words = struct.unpack("<4I", stub)
            # lui t0 / sw zero,imm(t0) reconstructing the flag address.
            high = words[0] & 0xFFFF
            low = words[1] & 0xFFFF
            address = (high << 16) + (low - 0x10000 if low >= 0x8000 else low)
            self.assertEqual(address, tower_send.SEND_MODE_FLAG_ADDRESS)
            self.assertEqual(
                words[2],
                0x08000000 | ((handler >> 2) & 0x03FFFFFF),
            )
        self.assertLessEqual(
            tower_send.FEET_STUB_ADDRESS + 16,
            tower_send.RELOCATED_BLOCK_END_ADDRESS,
        )

    def test_table_fits_the_dead_quit_helper(self) -> None:
        self.assertLessEqual(
            tower_send.HANDLER_TABLE_ADDRESS + len(
                tower_send.build_handler_table()
            ),
            tower_send.RELOCATED_BLOCK_END_ADDRESS,
        )

    def test_relocated_block_does_not_touch_the_pickup_cave(self) -> None:
        # alternate_pickup owns the retired card driver at 0x8004EB68-0x8004EE4F;
        # the relocated table lives in the dead QUIT helper below it.
        self.assertLess(
            tower_send.RELOCATED_BLOCK_END_ADDRESS,
            alternate_pickup.BLOCK_ADDRESS,
        )

    def test_instruction_words(self) -> None:
        # lui v0,0x8005 / addiu v0,v0,0x8D60 -> 0x80048D60 after sign extension.
        self.assertEqual(tower_send._TABLE_LUI_WORD, 0x3C028005)
        self.assertEqual(tower_send._TABLE_ADDIU_WORD, 0x24428D60)
        # addiu a2,zero,7 - the stepper's row count.
        self.assertEqual(tower_send._ROW_COUNT_WORD, 0x24060007)
        # The widened loop bounds keep their vanilla opcode/register bits.
        self.assertEqual(tower_send._FADE_BOUND_WORD, 0x28C20008)
        self.assertEqual(tower_send._POSITION_BOUND_WORD, 0x28A20011)
        self.assertEqual(tower_send._POPOUT_BOUND_WORD, 0x29220007)
        # The intro's done-test moves from slot 6 to slot 7 so the slide-in
        # finishes for OUR row; leaving it on slot 6 froze the label mid-slide.
        self.assertEqual(tower_send._FADE_DONE_TEST_WORD, 0x8C42001C)

    def test_both_routes_to_the_table_addiu_get_the_relocated_upper_half(
        self,
    ) -> None:
        # REGRESSION, 2026-08-18. FUN_8004FF20 feeds the addiu at 0x8004FF78
        # from TWO `lui v0,0x8007` instructions: the fall-through copy at
        # 0x8004FF70 and the branch-delay copy at 0x8004FF5C, taken when the
        # players menu is paged to a familiar and the row is Line up / Fuse /
        # Command. Patching only the first left the second computing
        # 0x80068D60 and `jalr`ing garbage - a hard crash, reproduced in play.
        # Every `lui` that can reach the addiu must carry the relocated half.
        patches = dict(tower_send.iter_tower_send_file_patches(["Other"]))
        for address in (
            tower_send.TABLE_LUI_ADDRESS,
            tower_send.TABLE_LUI_DELAY_SLOT_ADDRESS,
        ):
            offset = save_removal.slus_runtime_to_file_offset(address)
            self.assertIn(offset, patches)
            self.assertEqual(
                patches[offset], struct.pack("<I", tower_send._TABLE_LUI_WORD)
            )
        # The two lui sites must be distinct, and the delay-slot one must sit
        # in the branch shadow immediately before the skipped fall-through.
        self.assertLess(
            tower_send.TABLE_LUI_DELAY_SLOT_ADDRESS,
            tower_send.TABLE_LUI_ADDRESS,
        )
        self.assertLess(
            tower_send.TABLE_LUI_ADDRESS, tower_send.TABLE_ADDIU_ADDRESS
        )

    def test_assigner_stub_shape(self) -> None:
        stub = patch.build_send_row_assigner_stub()
        words = struct.unpack(f"<{len(stub)//4}I", stub)
        # lw t0,0x1C(s0) / lw t2,0x20(s0): slot 7 and the cursor slot 8.
        self.assertEqual(words[0], 0x8E08001C)
        self.assertEqual(words[1], 0x8E0A0020)
        # Chain insert plus the animation-byte zero, then the displaced call.
        self.assertIn(0x8D4B000C, words)   # lw t3,0xC(t2)
        self.assertIn(0xAD48000C, words)   # sw t0,0xC(t2)
        self.assertIn(0xAD0B000C, words)   # sw t3,0xC(t0)
        self.assertIn(0xA2200000 | patch.SEND_ROW_ANIMATION_BYTE_OFFSET, words)
        self.assertIn(
            0x08000000
            | ((patch.SEND_ROW_ASSIGNER_CONTINUATION >> 2) & 0x03FFFFFF),
            words,
        )

    def test_the_animation_byte_is_row_sixs(self) -> None:
        # The drive loop reads state+0xA4+row; row 6 is the one Konami never
        # initialised, and an uninitialised byte is a wild label X.
        self.assertEqual(patch.SEND_ROW_ANIMATION_BYTE_OFFSET, 0xAA)

    def test_block_holds_only_the_table_now(self) -> None:
        self.assertEqual(
            tower_send.HANDLER_TABLE_ADDRESS + len(tower_send.build_handler_table()),
            0x80048D7C,
        )
        self.assertLess(0x80048D7C, tower_send.RELOCATED_BLOCK_END_ADDRESS)

    def test_record_sits_above_alternate_pickup(self) -> None:
        self.assertEqual(tower_send.SEND_ROW_RECORD_ADDRESS, 0x8004EE38)
        pickup_end = (
            alternate_pickup.BLOCK_ADDRESS
            + alternate_pickup.resident_block_size()
        )
        self.assertGreaterEqual(tower_send.SEND_ROW_RECORD_ADDRESS, pickup_end)
        self.assertLessEqual(
            tower_send.SEND_ROW_RECORD_ADDRESS + 12,
            alternate_pickup.BLOCK_END_ADDRESS,
        )

    def test_row_six_flag_byte_is_untouched_vanilla_zero(self) -> None:
        # The flag table is deliberately NOT relocated: the dispatcher reads
        # 0x800717B4 + 6 = 0x800717BA for the Send row, which is vanilla 0x00.
        # Nothing in this module may write the vanilla flag or handler tables.
        table_span = range(
            tower_send.VANILLA_HANDLER_TABLE_ADDRESS,
            tower_send.VANILLA_FLAG_TABLE_ADDRESS + 8,
        )
        for file_offset, payload in tower_send.iter_tower_send_file_patches(["Sandknight"]):
            for address in table_span:
                self.assertNotEqual(
                    file_offset,
                    save_removal.slus_runtime_to_file_offset(address),
                    f"patch writes vanilla menu table at 0x{address:08x}",
                )


class SendRowStripTests(unittest.TestCase):
    def test_record_points_at_the_uploaded_strip(self) -> None:
        r = tower_send.SEND_ROW_RECORD
        self.assertEqual(len(r), 12)
        self.assertEqual(struct.unpack_from("<H", r, 4)[0], 0x0017)  # sheet page
        self.assertEqual(struct.unpack_from("<H", r, 6)[0], 0x7CC6)  # sheet CLUT
        self.assertEqual((r[2], r[3]), (0x04, 0x20))                 # row 5's line
        self.assertEqual((r[8], r[9]), (0x90, 0x60))                 # texture UV
        self.assertEqual((r[10], r[11]), (56, 16))
        # The Feet/Hand records take the bottom line the Send row vacated.
        self.assertEqual(
            tower_send.FEET_HAND_POSITION_PATCHES,
            (
                (patch.FEET_LABEL_RECORD_ADDRESS + 3, 0x30),
                (patch.HAND_LABEL_RECORD_ADDRESS + 3, 0x30),
            ),
        )

    def test_upload_targets_match_the_record_uvs(self) -> None:
        # The record's texture pair (+8/+9) must be painted, or the row
        # renders someone else's pixels. Its +2/+3 pair is the row's SCREEN
        # offset, not a texture coordinate (renderer 0x800453E0 sign-extends
        # it into vertex X/Y) - painting VRAM there distorts the item menu's
        # targeting reticule, whose cells own those texels. Exactly one
        # upload target, and never one at the position pair.
        r = tower_send.SEND_ROW_RECORD
        self.assertEqual(
            patch.SEND_ROW_VRAM_TARGETS,
            ((448 + r[8] // 4, 256 + r[9]),),
        )
        self.assertNotIn(
            (448 + r[2] // 4, 256 + r[3]), patch.SEND_ROW_VRAM_TARGETS
        )
        self.assertEqual(r[3], 0x20)
        self.assertEqual(r[9], 0x60)

    def test_rail_blit_is_idempotent(self) -> None:
        # Every blit source must be a row nothing writes, or a second menu
        # open copies what the first one wrote. The cap comes from RAM.
        written = {patch.RAIL_CAP_VRAM_Y}
        for source_y, destination_y in patch.RAIL_BLITS:
            written.add(destination_y)
        for source_y, _ in patch.RAIL_BLITS:
            self.assertNotIn(source_y, written)
        self.assertEqual(
            len(patch.load_send_rail_cap()), patch.SEND_RAIL_CAP_SIZE
        )

    def test_name_slot_holds_a_full_length_slot_name(self) -> None:
        # Archipelago slot names run to sixteen characters; full-width CP932
        # is two bytes each plus a terminator.
        self.assertGreaterEqual(patch.SEND_ROW_NAME_SLOT_SIZE, 16 * 2 + 1)
        table = tower_send.build_target_names(["Christopherjames"])
        self.assertEqual(len(table), patch.SEND_ROW_NAME_SLOT_SIZE)
        self.assertEqual(table.count(0), patch.SEND_ROW_NAME_SLOT_SIZE - 32)

    def test_strip_asset_is_the_expected_size(self) -> None:
        self.assertEqual(
            len(patch.load_send_row_strip()), patch.SEND_ROW_STRIP_SIZE
        )

    def test_seed_page_blocks_do_not_overlap_their_neighbours(self) -> None:
        upload = patch.build_send_row_upload()
        self.assertLessEqual(
            patch.SEND_ROW_UPLOAD_OFFSET + len(upload),
            patch.SEND_ROW_STRIP_OFFSET,
        )
        self.assertLessEqual(
            patch.SEND_ROW_STRIP_OFFSET + patch.SEND_ROW_STRIP_SIZE,
            patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET,
        )
        # The floor-page loader must stop before the Send-row code block.
        self.assertLessEqual(
            patch.FLOOR_PAGE_LOADER_OFFSET + patch.FLOOR_PAGE_LOADER_CAPACITY,
            patch.SEND_ROW_CODE_OFFSET,
        )

    def test_strip_lands_in_the_seed_block(self) -> None:
        block = patch.build_seed_block(
            b"12345678",
            [patch.LocationPlacement("Gold", "Koh", False)] * patch.LOCATION_COUNT,
            ["Sandknight"],
        )
        strip = patch.load_send_row_strip()
        self.assertEqual(
            block[
                patch.SEND_ROW_STRIP_OFFSET :
                patch.SEND_ROW_STRIP_OFFSET + len(strip)
            ],
            strip,
        )


class SendCommitTests(unittest.TestCase):
    def test_commit_fits_its_slot(self) -> None:
        for count in (1, 2, 3):
            self.assertLessEqual(
                len(tower_send.build_send_commit(count)),
                patch.SEND_ROW_CODE_CAPACITY - tower_send._COMMIT_OFFSET,
            )

    def test_commit_shape(self) -> None:
        words = struct.unpack_from(
            f"<{len(tower_send.build_send_commit(3)) // 4}I",
            tower_send.build_send_commit(3),
        )
        jal = lambda a: 0x0C000000 | ((a >> 2) & 0x03FFFFFF)
        j = lambda a: 0x08000000 | ((a >> 2) & 0x03FFFFFF)
        # The displaced source test is re-issued first: beq s1,v0,...
        self.assertEqual(words[0] >> 16, 0x1222)
        # A ground descriptor rejoins the vanilla hook chain; bails leave
        # through the epilogue; a send chimes.
        self.assertIn(j(alternate_pickup.PUT_IN_HOOK_ADDRESS), words)
        self.assertIn(j(alternate_pickup.PUT_IN_EPILOGUE_ADDRESS), words)
        self.assertIn(jal(alternate_pickup.PLAY_SOUND_ADDRESS), words)
        # The vanilla bail's v0 = s1 is reproduced in a delay slot.
        self.assertIn(0x02201021, words)
        # The magic is loaded for the compare and stored on publish.
        self.assertIn(
            0x3C090000 | (patch.TOWER_GIFT_MAILBOX_MAGIC >> 16), words
        )
        # The slot is freed by the game's own occupancy rule: sb zero,1(s1).
        self.assertIn(0xA2200001, words)
        # The commit never touches the marker guard or its test: hand, feet
        # and ground items take their vanilla paths before this code runs.
        self.assertNotIn(jal(alternate_pickup.MARKER_TEST_ADDRESS), words)
        self.assertNotIn(j(alternate_pickup.PUT_IN_GUARD_ADDRESS), words)

    def test_mailbox_stays_inside_the_adap_structure_tail(self) -> None:
        self.assertEqual(patch.TOWER_GIFT_MAILBOX_ADDRESS, 0x801DA5F0)
        self.assertLessEqual(
            patch.TOWER_GIFT_MAILBOX_ADDRESS + patch.TOWER_GIFT_MAILBOX_SIZE,
            patch.HIGH_MAILBOX_ADDRESS + 0x100,
        )
        self.assertEqual(
            patch.TOWER_GIFT_MAILBOX_MAGIC.to_bytes(4, "little"), b"ADGT"
        )

    def test_source_test_is_overridden_only_with_targets(self) -> None:
        self.assertEqual(
            tower_send.iter_tower_send_dungeon_file_patches([]), ()
        )
        patches = tower_send.iter_tower_send_dungeon_file_patches(["Koh"])
        self.assertEqual(len(patches), 1)
        offset, payload = patches[0]
        self.assertEqual(
            offset,
            alternate_pickup.dungeon_runtime_to_file_offset(
                tower_send.PUT_IN_SOURCE_TEST_ADDRESS
            ),
        )
        words = struct.unpack("<2I", payload)
        self.assertEqual(
            words,
            (
                0x08000000
                | ((tower_send.SEND_COMMIT_ROUTINE_ADDRESS >> 2) & 0x03FFFFFF),
                0,
            ),
        )
        # The seam sits just above the allocator hook, whose jal stays
        # alternate_pickup's - the two records may never overlap.
        self.assertEqual(
            tower_send.PUT_IN_SOURCE_TEST_ADDRESS + 8,
            alternate_pickup.PUT_IN_HOOK_ADDRESS,
        )


class TowerSendPatchRecordTests(unittest.TestCase):
    def _records(self) -> dict[int, bytes]:
        ppf = bytearray()
        tower_send.append_tower_send_ppf_records(ppf, ["Sandknight"])
        records: dict[int, bytes] = {}
        offset = 0
        while offset < len(ppf):
            raw, length = struct.unpack_from("<IB", ppf, offset)
            records[raw] = bytes(ppf[offset + 5:offset + 5 + length])
            offset += 5 + length
        return records

    def test_all_placements_emit(self) -> None:
        records = self._records()
        # table 28 + two 16-byte stubs + record 12 + the two Feet/Hand
        # position bytes + TWELVE SLUS instruction words (eleven, plus the
        # branch-delay `lui` at 0x8004FF5C added 2026-08-18 - see
        # test_both_routes_to_the_table_addiu_get_the_relocated_upper_half)
        # + the two-word dungeon source-test jump + the rail's one-byte
        # height + the eight-byte send state.
        self.assertEqual(
            sum(len(data) for data in records.values()),
            28 + 32 + 12 + 2 + 52 + 8 + 1 + 8,
        )
        expected_offsets = {
            file_offset
            for file_offset, _ in tower_send.iter_tower_send_file_patches(["Sandknight"])
        }
        self.assertEqual(len(expected_offsets), 20)

    def test_rail_grows_by_exactly_one_row(self) -> None:
        self.assertEqual(
            patch.RAIL_EXTENDED_HEIGHT - patch.RAIL_VANILLA_HEIGHT, 16
        )
        # One blit only: the shaft slice into the row the cap vacated. The cap
        # itself is uploaded from RAM, because blitting it was not idempotent.
        self.assertEqual(len(patch.RAIL_BLITS), 1)
        shaft_source, shaft_destination = patch.RAIL_BLITS[0]
        self.assertEqual(shaft_destination - shaft_source, 16)
        self.assertEqual(patch.RAIL_CAP_VRAM_Y - shaft_destination, 16)
        # The rail sits on a whole-halfword boundary, or MoveImage cannot
        # address it exactly.
        self.assertEqual((patch.RAIL_VRAM_X - 448) * 4, 0x80)

    def test_table_record_content(self) -> None:
        records = self._records()
        table_record = None
        for raw, data in records.items():
            if len(data) == 28:
                table_record = data
        self.assertIsNotNone(table_record)
        handlers = struct.unpack("<7I", table_record)
        self.assertEqual(handlers[0], tower_send.ITEMS_STUB_ADDRESS)
        self.assertEqual(handlers[1:5], tower_send.VANILLA_HANDLERS[1:5])
        self.assertEqual(handlers[5], tower_send.SEND_HANDLER_ROUTINE_ADDRESS)
        self.assertEqual(handlers[6], tower_send.FEET_STUB_ADDRESS)


if __name__ == "__main__":
    unittest.main()


class NoTargetsTests(unittest.TestCase):
    """With no other Azure Dreams player the feature must vanish entirely."""

    def test_no_slus_records_at_all(self) -> None:
        ppf = bytearray()
        tower_send.append_tower_send_ppf_records(ppf, [])
        self.assertEqual(len(ppf), 0)
        self.assertEqual(tower_send.iter_tower_send_file_patches([]), ())

    def test_seed_page_keeps_the_rented_bytes_zeroed(self) -> None:
        placements = [
            patch.LocationPlacement("Gold", "Koh", False)
        ] * patch.LOCATION_COUNT
        block = patch.build_seed_block(b"12345678", placements, [])
        rented = block[
            patch.SEND_ROW_UPLOAD_OFFSET :
            patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET
        ]
        self.assertEqual(rented, bytes(len(rented)))

    def test_targets_are_capped(self) -> None:
        many = ["A", "B", "C", "D", "E"]
        ppf = bytearray()
        tower_send.append_tower_send_ppf_records(ppf, many)
        self.assertGreater(len(ppf), 0)
        self.assertEqual(tower_send.MAX_TARGETS, 3)
