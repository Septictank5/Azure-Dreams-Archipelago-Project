import hashlib
import struct


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value
import unittest
from pathlib import Path

from .. import patch, town_shop
from . import mips_sim


def _ppf_writes(ppf: bytes) -> dict[int, int]:
    writes: dict[int, int] = {}
    cursor = patch.PPF_HEADER_SIZE
    while cursor < len(ppf):
        raw_offset, length = struct.unpack_from("<IB", ppf, cursor)
        cursor += 5
        for index, value in enumerate(ppf[cursor : cursor + length]):
            writes[raw_offset + index] = value
        cursor += length
    return writes


def _contains_written_bytes(writes: dict[int, int], expected: bytes) -> bool:
    return any(
        all(writes.get(offset + index) == value for index, value in enumerate(expected))
        for offset in writes
    )


def _slus_raw_offset(address: int) -> int:
    logical_offset = 0x800 + address - 0x8002_D000
    sector, within_sector = divmod(logical_offset, 2048)
    return (24 + sector) * patch.RAW_SECTOR_SIZE + 24 + within_sector


def _mailbox_offset(field: int) -> int:
    """The signed immediate that reaches `HIGH_MAILBOX_ADDRESS + field` from
    the `lui` the payload actually uses.

    Computed rather than written down: these were `-0x100`/`-0xFC` against
    the old `lui 0x8020`, and the 2026-08-01 carve retraction moved the
    mailbox. A hard-coded immediate here pins the wrong thing - the point of
    the assertion is which mailbox field is touched, not what the encoding
    happened to be before the move.
    """

    return (
        patch.HIGH_MAILBOX_ADDRESS
        + field
        - (patch._upper(patch.HIGH_MAILBOX_ADDRESS) << 16)
    )


class TestAzureDreamsPatch(unittest.TestCase):
    def test_known_mixed_battle_message(self) -> None:
        # '5' is the only glyph outside the compact alphabet, so the cheapest
        # encoding runs compact up to it, leaves compact mode, and finishes the
        # tail full-width. Re-entering compact mode for the final '.' would cost
        # a byte more than emitting it wide, and the encoder picks the cheaper.
        self.assertEqual(
            patch.encode_battle_message("Sent Master Sword to Player5."),
            bytes.fromhex(
                "51 35 02 05 04 01 24 03 06 04 02 09 01 35 11 07 09 0a "
                "01 04 07 01 21 0d 03 1a 02 09 00 82 54 81 44 00"
            ),
        )

    def test_battle_message_interleaves_modes_when_that_is_cheaper(self) -> None:
        # A name with an unsupported glyph in the middle. The old encoder gave
        # up at '(' and doubled everything after it; the current one returns to
        # the compact alphabet for the long run between the parentheses.
        encoded = patch.encode_battle_message("Small Key (Palace of Darkness)")
        self.assertLess(len(encoded), 53)
        # Compact mode is entered more than once. The re-entry follows the
        # escaped character rather than the zero that left compact mode, so the
        # marker to count is 0x51 itself: 0x51 <compact> 0x00 <wide> 0x51 ...
        self.assertGreater(encoded.count(0x51), 1)

    def test_battle_message_never_exceeds_the_single_switch_encoding(self) -> None:
        # The single-switch scheme this replaced. Interleaving costs four bytes
        # to escape an isolated glyph, so it is not always a win - the encoder
        # must never be worse than the scheme it replaced.
        def single_switch(text: str) -> bytes:
            out = bytearray((0x51,))
            split_at = len(text)
            for index, character in enumerate(text):
                glyph = patch._BATTLE_GLYPHS.get(character)
                if glyph is None:
                    split_at = index
                    break
                out.append(glyph)
            out.append(0)
            out.extend(patch._full_width_cp932(text[split_at:]))
            out.append(0)
            return bytes(out)

        for text in (
            "Found Gold.",
            "Blinder Ball (10)",
            "Sent Master Sword to Player5.",
            "Sent Small Key (Palace of Darkness) to Sandknight.",
            "Sent Bombs (10) to Reven.",
            "(",
            "",
        ):
            with self.subTest(text=text):
                self.assertLessEqual(
                    len(patch.encode_battle_message(text)), len(single_switch(text))
                )

    def test_battle_message_decodes_to_the_same_text_as_a_single_switch(self) -> None:
        # Models append_encoded_text at 0x80099194: 0x51 enters compact mode, a
        # zero inside it leaves and continues, a zero outside it terminates, and
        # raw bytes are copied verbatim. Compact glyph N is the CP932 pair at
        # entry N-1 of the game's table, which is exactly _BATTLE_GLYPHS.
        code_to_pair = {
            glyph: patch._full_width_cp932(character)
            for character, glyph in patch._BATTLE_GLYPHS.items()
        }

        def decode(data: bytes) -> bytes:
            out = bytearray()
            index = 0
            compact = False
            while True:
                if not compact and data[index] == 0x51:
                    compact = True
                    index += 1
                    continue
                byte = data[index]
                if byte == 0:
                    if not compact:
                        return bytes(out)
                    compact = False
                    index += 1
                    continue
                if compact:
                    out.extend(code_to_pair[byte])
                    index += 1
                else:
                    out.extend(data[index:index + 2])
                    index += 2

        for text in (
            "Found Gold.",
            "Sent Master Sword to Player5.",
            "Sent Small Key (Palace of Darkness) to Sandknight.",
            "Blinder Ball (10)",
            "Progressive Keycard",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    decode(patch.encode_battle_message(text)),
                    patch._full_width_cp932(text),
                )

    def test_unrepresentable_player_name_glyph_uses_full_width_question_mark(self) -> None:
        encoded = patch.encode_battle_message("Sent Gold to P😀.")
        self.assertIn("？".encode("cp932"), encoded)
        self.assertTrue(encoded.endswith(b"\x81\x44\x00"))

    def _run_composer(self, block, floor, slot, placements=None):
        """Execute the generated composer against a seed block and return its text.

        append_encoded_text is stubbed rather than simulated: the routine under
        test is the composer, and what matters is that it calls the helper with
        the right fragment pointers, chains the returned cursor, and honours the
        record's form byte.
        """
        memory = mips_sim.Memory()
        memory.load_bytes(patch.SEED_BLOCK_ADDRESS, block)
        memory.write32(0x8008_146C, floor)          # g_current_floor_number
        # The floor-page loader's effect during the floor build: this floor's
        # page sector landed over the seed page's window. Out-of-range floors
        # keep whatever page was already resident, exactly like the runtime;
        # tests that pass no placements run against the baked floor-1 page.
        if placements is not None and 1 <= floor <= patch.FLOOR_PAGE_FLOOR_COUNT:
            memory.load_bytes(
                patch.FLOOR_PAGE_WINDOW_ADDRESS,
                patch.build_floor_page_sectors(block, placements)[floor - 1],
            )
        descriptor = 0x801F_0000
        memory.load_bytes(
            descriptor,
            bytes((patch.MARKER_ID, patch.MARKER_CATEGORY, slot, patch.MARKER_STATUS)),
        )
        destination = 0x801F_1000

        appended: list[bytes] = []

        def read_encoded(memory, address):
            # Termination follows the real decoder, not "first zero byte": a
            # zero inside compact mode only leaves compact mode, so a message
            # ending in compact mode carries two trailing zeroes.
            length = 0
            compact = False
            while True:
                byte = memory.read8(address + length)
                if byte == 0:
                    length += 1
                    if not compact:
                        return bytes(
                            memory.read8(address + index) for index in range(length)
                        )
                    compact = False
                    continue
                if not compact and byte == 0x51:
                    compact = True
                    length += 1
                    continue
                length += 1 if compact else 2

        def append_encoded_text(cpu):
            source = cpu.registers[4]
            cursor = cpu.registers[5]
            encoded = read_encoded(cpu.memory, source)
            # Mirrors the real helper: writes decoded output, returns the new
            # cursor in v0, and terminates nothing.
            appended.append(encoded)
            cpu.memory.write8(cursor, len(encoded))
            cpu.registers[2] = cursor + 1

        cpu = mips_sim.Cpu(memory, {0x8009_9194: append_encoded_text})
        cpu.registers[4] = descriptor
        cpu.registers[5] = destination
        returned = cpu.run(patch.APPEND_LOCATION_MESSAGE_ADDRESS)
        return appended, returned, destination

    def test_composer_renders_a_local_placement(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        block = patch.build_seed_block(b"12345678", placements)
        appended, returned, destination = self._run_composer(block, floor=1, slot=0)
        self.assertEqual(
            appended,
            [
                patch.encode_battle_message("Found "),
                patch.encode_battle_message("Gold"),
                patch.encode_battle_message("."),
            ],
        )
        self.assertEqual(returned, destination + len(appended))

    def test_composer_renders_a_remote_placement(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        placements[1] = patch.LocationPlacement("Master Sword", "Player5", True)
        block = patch.build_seed_block(b"12345678", placements)
        appended, returned, destination = self._run_composer(block, floor=1, slot=1)
        self.assertEqual(
            appended,
            [
                patch.encode_battle_message("Sent "),
                patch.encode_battle_message("Master Sword"),
                patch.encode_battle_message(" to "),
                patch.encode_battle_message("Player5"),
                patch.encode_battle_message("."),
            ],
        )
        self.assertEqual(returned, destination + len(appended))

    def test_composer_indexes_by_floor_and_slot(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        # Floor 20, slot 1 is index (20 - 1) * 2 + 1 = 39.
        placements[39] = patch.LocationPlacement("Wind Crystal", "Reven", True)
        block = patch.build_seed_block(b"12345678", placements)
        appended, _, _ = self._run_composer(block, floor=20, slot=1, placements=placements)
        self.assertIn(patch.encode_battle_message("Wind Crystal"), appended)
        self.assertIn(patch.encode_battle_message("Reven"), appended)

    def test_composer_appends_nothing_outside_the_tower(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        block = patch.build_seed_block(b"12345678", placements)
        # Floor 0 is not a tower floor; the cursor must come back untouched.
        appended, returned, destination = self._run_composer(block, floor=0, slot=0)
        self.assertEqual(appended, [])
        self.assertEqual(returned, destination)

    def test_seed_block_contains_messages_and_ownership_bits(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        placements[1] = patch.LocationPlacement("Master Sword", "Player5", True)
        placements[6] = patch.LocationPlacement(
            "Progressive Keycard",
            "Koh",
            False,
            progressive_keycard=True,
        )
        block = patch.build_seed_block(b"12345678", placements)

        self.assertEqual(len(block), patch.SEED_BLOCK_SIZE)
        self.assertEqual(
            struct.unpack_from("<IHH", block),
            (patch.SEED_MAGIC, patch.SEED_VERSION, 78),
        )
        self.assertEqual(block[8:16], b"12345678")
        self.assertEqual(block[0x10] & 0b11, 0b10)
        self.assertEqual(block[patch.FLOOR_KEYCARD_MASK_OFFSET] & 0b1111, 0b1000)
        self.assertEqual(
            block[
                patch.ELEVATOR_RETURN_DESCRIPTOR_OFFSET :
                patch.ELEVATOR_RETURN_DESCRIPTOR_OFFSET + 4
            ],
            b"\x03\x06\x00\x0D",
        )

        # The baked floor-1 page: header, records, and name slots. Floor 1 is
        # placements 0 (local Gold) and 1 (remote Master Sword to Player5).
        self.assertEqual(
            struct.unpack_from("<IHH", block, patch.FLOOR_PAGE_HEADER_OFFSET),
            (patch.FLOOR_PAGE_MAGIC, 1, patch.FLOOR_PAGE_VERSION),
        )
        first = block[patch.FLOOR_PAGE_RECORDS_OFFSET : patch.FLOOR_PAGE_RECORDS_OFFSET + 3]
        remote = block[
            patch.FLOOR_PAGE_RECORDS_OFFSET + 3 : patch.FLOOR_PAGE_RECORDS_OFFSET + 6
        ]
        self.assertEqual(tuple(first), (0, 0, 0))   # slot 0, recipient 0, local
        self.assertEqual(tuple(remote), (1, 1, 1))  # slot 1, recipient 1, remote
        item0 = patch.FLOOR_PAGE_ITEM_SLOTS_OFFSET
        item1 = item0 + patch.FLOOR_PAGE_ITEM_SLOT_SIZE
        self.assertTrue(block[item0:].startswith(patch.encode_battle_message("Gold")))
        self.assertTrue(
            block[item1:].startswith(patch.encode_battle_message("Master Sword"))
        )
        name0 = patch.FLOOR_PAGE_PLAYER_SLOTS_OFFSET
        name1 = name0 + patch.FLOOR_PAGE_PLAYER_SLOT_SIZE
        self.assertTrue(block[name0:].startswith(patch.encode_battle_message("Koh")))
        self.assertTrue(block[name1:].startswith(patch.encode_battle_message("Player5")))
        for slot, text in (
            (patch.FLOOR_PAGE_FRAGMENT_FOUND, "Found "),
            (patch.FLOOR_PAGE_FRAGMENT_SENT, "Sent "),
            (patch.FLOOR_PAGE_FRAGMENT_TO, " to "),
            (patch.FLOOR_PAGE_FRAGMENT_PERIOD, "."),
        ):
            self.assertTrue(block[slot:].startswith(patch.encode_battle_message(text)))

        # Only floor 1's text is resident; floor 2+ item names are in the bank.
        self.assertEqual(block.count(patch.encode_battle_message("Gold")), 1)

        # The per-floor bank: every sector's static content is byte-identical
        # to the resident window, and each carries its own floor's fields.
        pages = patch.build_floor_page_sectors(block, placements)
        self.assertEqual(len(pages), patch.FLOOR_PAGE_FLOOR_COUNT)
        window = block[patch.FLOOR_PAGE_WINDOW_OFFSET : patch.FLOOR_PAGE_WINDOW_END]
        self.assertEqual(pages[0], bytes(window))  # floor 1 == the baked page
        dynamic = set()
        for span_start, span_end in (
            (patch.FLOOR_PAGE_HEADER_OFFSET, patch.FLOOR_PAGE_RECORDS_OFFSET + 6),
            (
                patch.FLOOR_PAGE_PLAYER_SLOTS_OFFSET,
                patch.FLOOR_PAGE_PLAYER_SLOTS_OFFSET
                + patch.FLOOR_PAGE_PLAYER_SLOT_COUNT * patch.FLOOR_PAGE_PLAYER_SLOT_SIZE,
            ),
            (
                patch.FLOOR_PAGE_ITEM_SLOTS_OFFSET,
                patch.FLOOR_PAGE_ITEM_SLOTS_OFFSET
                + patch.MARKER_SLOT_COUNT * patch.FLOOR_PAGE_ITEM_SLOT_SIZE,
            ),
        ):
            dynamic.update(
                range(
                    span_start - patch.FLOOR_PAGE_WINDOW_OFFSET,
                    span_end - patch.FLOOR_PAGE_WINDOW_OFFSET,
                )
            )
        for floor_index, page in enumerate(pages):
            self.assertEqual(len(page), patch.FLOOR_PAGE_WINDOW_SIZE)
            self.assertEqual(
                struct.unpack_from("<IHH", page, 0),
                (patch.FLOOR_PAGE_MAGIC, floor_index + 1, patch.FLOOR_PAGE_VERSION),
            )
            for offset in range(len(page)):
                if offset not in dynamic:
                    self.assertEqual(
                        page[offset],
                        window[offset],
                        f"floor {floor_index + 1} page differs from the resident "
                        f"window at static offset 0x{offset:X}",
                    )

        # Truncation: applied to encoded bytes, never past the slot, and an
        # AP-max-length player name always fits its slot.
        long_name = "Small Key (Palace of Darkness) Extended Edition"
        truncated = patch.encode_item_slot_text(long_name)
        self.assertLessEqual(len(truncated), patch.FLOOR_PAGE_ITEM_SLOT_SIZE)
        self.assertEqual(truncated[-1], 0)
        wide = patch.encode_item_slot_text("玉" * 40)
        self.assertLessEqual(len(wide), patch.FLOOR_PAGE_ITEM_SLOT_SIZE)
        self.assertLessEqual(
            len(patch.encode_battle_message("W" * 16)),
            patch.FLOOR_PAGE_PLAYER_SLOT_SIZE,
        )
        self.assertLessEqual(
            len(patch.encode_battle_message("玉" * 16)),
            patch.FLOOR_PAGE_PLAYER_SLOT_SIZE,
        )

        initializer = patch._build_seed_state_initializer()
        self.assertLessEqual(
            len(initializer),
            patch.APPEND_LOCATION_MESSAGE_ADDRESS - patch.SEED_INIT_ADDRESS,
        )
        self.assertEqual(patch.PERSISTENT_STATE_ADDRESS, 0x8001_5FC0)
        self.assertEqual(patch.PERSISTENT_STATE_VERSION, 3)
        self.assertEqual(patch.PERSISTENT_STATE_SIZE, 0x2C)
        # The record must stay inside the reserved 0x40-byte save tail at
        # buffer offset 0x5FC0.
        self.assertLessEqual(patch.PERSISTENT_STATE_SIZE, 0x40)
        self.assertEqual(patch.PERSISTENT_GOLD_GRANTED_OFFSET, 0x28)
        self.assertEqual(patch.PERSISTENT_RECEIVED_ITEM_COUNT_OFFSET, 0x1C)
        self.assertEqual(patch.PERSISTENT_KEYCARD_LEVEL_OFFSET, 0x20)
        self.assertEqual(patch.PERSISTENT_SHOP_MASK_OFFSET, 0x24)
        self.assertIn(
            struct.pack("<II", patch._i(0x0F, 0, 11, 0x8001), patch._i(0x09, 11, 11, 0x5FC0)),
            initializer,
        )

        self.assertLessEqual(
            len(patch._build_elevator_prompt_callback()),
            patch.ELEVATOR_CHOICE_CALLBACK_ADDRESS - patch.ELEVATOR_PROMPT_CALLBACK_ADDRESS,
        )
        self.assertLessEqual(
            len(patch._build_elevator_choice_callback()),
            patch.ELEVATOR_GATE_HANDLER_ADDRESS - patch.ELEVATOR_CHOICE_CALLBACK_ADDRESS,
        )
        self.assertLessEqual(
            len(patch._build_elevator_gate_handler()),
            patch.ELEVATOR_RETURN_PROMPT_ADDRESS - patch.ELEVATOR_GATE_HANDLER_ADDRESS,
        )
        gate_handler = patch._build_elevator_gate_handler()
        self.assertIn(
            struct.pack("<I", patch._j(0x02, patch._WIND_CRYSTAL_USE_ADDRESS)),
            gate_handler,
        )
        self.assertNotIn(
            struct.pack("<I", patch._j(0x03, patch._WIND_CRYSTAL_USE_ADDRESS)),
            gate_handler,
        )
        prompt_offset = patch.ELEVATOR_RETURN_PROMPT_ADDRESS - patch.SEED_BLOCK_ADDRESS
        self.assertTrue(block[prompt_offset:].startswith(patch.encode_elevator_return_prompt()))

    def test_the_prompt_callback_never_returns_a_null_menu(self) -> None:
        """0.9.36 softlocked by suppressing the prompt with a null return.

        Whatever consumes the prompt request retries a null constructor every
        frame, so the locked message appended forever. The only null return
        left is the vanilla allocation-failure path, which is reached by a
        branch on the allocator's own result and not by any test of ours.
        """

        callback = patch._build_elevator_prompt_callback()
        # Every branch out of the floor tests goes to the native prompt or on
        # into the builder; none jumps to a routine of ours in the page.
        for index in range(len(callback) // 4):
            word = struct.unpack_from("<I", callback, index * 4)[0]
            if word >> 26 not in (0x04, 0x05):
                continue
            target = (
                patch.ELEVATOR_PROMPT_CALLBACK_ADDRESS
                + index * 4
                + 4
                + _signed16(word & 0xFFFF) * 4
            )
            self.assertTrue(
                patch.ELEVATOR_PROMPT_CALLBACK_ADDRESS
                <= target
                <= patch.ELEVATOR_CHOICE_CALLBACK_ADDRESS,
                f"branch at +{index * 4:#x} leaves the callback, to {target:#010x}",
            )

    def test_a_refused_ascent_has_a_message_to_show(self) -> None:
        """The locked feedback used to point at an address nobody wrote.

        `0x801fff40` is in the AP protocol mailbox and nothing ever populated
        it, so every refused ascent produced an empty dialogue box. The text
        now lives in the generated page, which needs no runtime writer.
        """

        block = patch.build_seed_block(
            b"12345678",
            [patch.LocationPlacement("Gold", "Koh", False)] * patch.LOCATION_COUNT,
        )
        encoded = patch.encode_battle_message(patch.ELEVATOR_LOCKED_MESSAGE_TEXT)
        offset = patch.ELEVATOR_LOCKED_MESSAGE_OFFSET
        self.assertEqual(block[offset : offset + len(encoded)], encoded)
        self.assertNotEqual(encoded[0], 0, "the message would render empty")

        # The gate handler must point at it, and never at the mailbox again.
        gate_handler = patch._build_elevator_gate_handler()
        self.assertIn(
            struct.pack(
                "<I", patch._i(0x09, 4, 4, patch._lower(patch.ELEVATOR_LOCKED_MESSAGE_ADDRESS))
            ),
            gate_handler,
        )
        self.assertNotIn(
            struct.pack("<I", patch._i(0x09, 4, 4, 0xFF40)), gate_handler
        )
        self.assertIn(
            struct.pack("<I", patch._j(0x03, patch._SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS)),
            gate_handler,
        )

        # It sits in the appender region's unused tail, clear of both
        # neighbours.
        self.assertGreaterEqual(
            offset, patch.APPEND_LOCATION_MESSAGE_ADDRESS - patch.SEED_BLOCK_ADDRESS
        )
        self.assertLessEqual(
            offset + len(encoded),
            patch.RESOLVE_LOCATION_RENDER_ADDRESS - patch.SEED_BLOCK_ADDRESS,
        )

    def test_seed_block_contains_floor_safe_compact_inventory_hud_code(self) -> None:
        placements = [patch.LocationPlacement("Gold", "Koh", False) for _ in range(78)]
        block = patch.build_seed_block(b"12345678", placements)
        post = patch._build_inventory_hud_post_registration_hook()
        refresh = patch._build_inventory_hud_refresh()
        post_offset = (
            patch.INVENTORY_HUD_POST_REGISTRATION_ADDRESS
            - patch.SEED_BLOCK_ADDRESS
        )
        refresh_offset = (
            patch.INVENTORY_HUD_REFRESH_ADDRESS - patch.SEED_BLOCK_ADDRESS
        )

        self.assertEqual(len(post), 0xB0)
        self.assertEqual(len(refresh), 0xA4)
        self.assertEqual(post_offset, patch.INVENTORY_HUD_CODE_OFFSET)
        self.assertEqual(post_offset + len(post), refresh_offset)
        self.assertEqual(refresh_offset + len(refresh), patch.SEED_BLOCK_SIZE)
        self.assertEqual(block[post_offset:refresh_offset], post)
        self.assertEqual(block[refresh_offset:], refresh)

        floor_bootstrap = patch._build_tower_floor_bootstrap_helper()
        self.assertEqual(patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET, 0x154C)
        self.assertEqual(
            patch.TOWER_FLOOR_BOOTSTRAP_HELPER_ADDRESS,
            patch.SEED_BLOCK_ADDRESS + 0x154C,
        )
        # The helper grew when it took on the shortcut's starting levels, so its
        # size is no longer pinned - what matters is that it still fits the
        # reservation carved out of the message region.
        self.assertLessEqual(
            patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET + len(floor_bootstrap),
            patch.INVENTORY_HUD_CODE_OFFSET,
        )
        self.assertEqual(
            block[
                patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET :
                patch.TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET + len(floor_bootstrap)
            ],
            floor_bootstrap,
        )
        # The marker test and both vanilla rejoin points are the contract with
        # the overlay hook and must not drift.
        words = struct.unpack(f"<{len(floor_bootstrap) // 4}I", floor_bootstrap)
        self.assertEqual(words[0], patch._i(0x21, 4, 8, 0x0234))
        self.assertEqual(words[1], patch._i(0x0F, 0, 5, 0x8008))
        self.assertIn(patch._j(0x02, 0x8001_6450), words)
        self.assertIn(patch._j(0x02, 0x8001_645C), words)
        # The grant moved out of the helper: it now only stages the level for
        # the wrapper installed over the monster-levelling call.
        self.assertNotIn(patch._j(0x03, patch.LEVEL_UP_ADDRESS), words)

        grant = patch._build_shortcut_level_grant()
        grant_words = struct.unpack(f"<{len(grant) // 4}I", grant)
        self.assertIn(patch._j(0x03, patch.LEVEL_UP_ADDRESS), grant_words)
        self.assertIn(patch._j(0x03, patch.LEVEL_MONSTERS_ADDRESS), grant_words)
        self.assertLessEqual(
            patch.SHORTCUT_LEVEL_GRANT_OFFSET + len(grant),
            patch.INVENTORY_HUD_CODE_OFFSET,
        )
        self.assertEqual(
            block[
                patch.SHORTCUT_LEVEL_GRANT_OFFSET :
                patch.SHORTCUT_LEVEL_GRANT_OFFSET + len(grant)
            ],
            grant,
        )

        self.assertEqual(
            len(patch.encode_inventory_hud_text("Keycard Lvl: 0")),
            0x90,
        )
        self.assertEqual(
            len(patch.encode_inventory_hud_text("Max Floor: 40")),
            0x84,
        )
        self.assertEqual(patch.INVENTORY_HUD_NODE_ADDRESS, 0x801D_9D50)
        self.assertEqual(patch.INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS + 0x84, 0x801D_9EE4)
        self.assertLessEqual(
            patch.INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS + 0x84,
            0x801D_9EF0,
        )

    def test_compact_inventory_hud_refresh_reads_save_backed_keycard_level(self) -> None:
        refresh = patch._build_inventory_hud_refresh()
        self.assertIn(
            struct.pack(
                "<III",
                patch._i(0x0F, 0, 8, 0x8001),
                patch._i(0x09, 8, 8, 0x5FE0),
                patch._i(0x24, 8, 11, 0),
            ),
            refresh,
        )
        self.assertIn(
            struct.pack("<I", patch._i(0x28, 8, 9, 140)),
            refresh,
        )
        self.assertIn(
            struct.pack("<I", patch._i(0x28, 8, 14, 116)),
            refresh,
        )
        self.assertIn(
            struct.pack("<I", patch._i(0x28, 8, 15, 128)),
            refresh,
        )

    def test_render_resolver_uses_gift_for_every_valid_location(self) -> None:
        resolver = patch._build_render_resolver()
        self.assertIn(
            struct.pack(
                "<9I",
                patch._i(0x24, 4, 9, 0),
                patch._i(0x09, 0, 10, patch.MARKER_ID),
                patch._i(0x05, 9, 10, 30),
                patch._i(0x24, 4, 9, 1),
                patch._i(0x09, 0, 10, patch.MARKER_CATEGORY),
                patch._i(0x05, 9, 10, 27),
                patch._i(0x24, 4, 9, 3),
                patch._i(0x09, 0, 10, patch.MARKER_STATUS),
                patch._i(0x05, 9, 10, 24),
            ),
            resolver,
        )
        self.assertIn(
            struct.pack(
                "<II",
                patch._i(0x0F, 0, 2, 0x8007),
                patch._i(0x0D, 2, 2, 0x7950),
            ),
            resolver,
        )
        self.assertNotIn(
            struct.pack("<I", patch._i(0x24, 11, 13, 0x10)),
            resolver,
        )
        self.assertTrue(
            resolver.endswith(struct.pack("<II", patch._j(0x02, 0x800A_7A38), 0)),
        )

    def test_base_patch_uses_seed_resolver_for_held_ap_markers(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        writes = _ppf_writes(base_patch)
        held_resolver_call_raw_offset = 0x1C7_C758
        self.assertEqual(
            bytes(writes[held_resolver_call_raw_offset + index] for index in range(4)),
            struct.pack("<I", patch._j(0x03, patch.RESOLVE_LOCATION_RENDER_ADDRESS)),
        )

    def test_base_patch_routes_seeded_elevators_through_return_handlers(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        writes = _ppf_writes(base_patch)

        # The seed-magic guard, addressed at the relocated suite. Derived
        # via _upper/_lower rather than assuming the lui carries upper+1 -
        # that only held while the page's low half was >= 0x8000, which
        # stopped being true when it moved to 0x801D7F00.
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<II",
                    patch._i(0x0F, 0, 8, patch._upper(patch.SEED_BLOCK_ADDRESS)),
                    patch._i(0x23, 8, 9, patch._lower(patch.SEED_BLOCK_ADDRESS)),
                ),
            )
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack("<I", patch._j(0x02, patch.ELEVATOR_GATE_HANDLER_ADDRESS)),
            )
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<I",
                    patch._i(
                        0x0F,
                        0,
                        7,
                        ((patch.ELEVATOR_PROMPT_CALLBACK_ADDRESS + 0x8000) >> 16) & 0xFFFF,
                    ),
                ),
            )
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<I",
                    patch._i(
                        0x09,
                        7,
                        7,
                        patch.ELEVATOR_PROMPT_CALLBACK_ADDRESS & 0xFFFF,
                    ),
                ),
            )
        )

    def test_base_patch_uses_save_tail_for_collection_journal(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        self.assertIn(struct.pack("<I", patch._i(0x09, 8, 8, 0x5FD0)), base_patch)
        self.assertIn(struct.pack("<I", patch._i(0x09, 11, 11, 0x5FD0)), base_patch)
        self.assertNotIn(struct.pack("<I", patch._i(0x09, 8, 8, 0x3710)), base_patch)
        self.assertNotIn(struct.pack("<I", patch._i(0x09, 11, 11, 0x3710)), base_patch)

    def test_base_patch_disables_the_earthquake_system(self) -> None:
        # The per-turn earthquake callback's prologue becomes
        # `jr ra / addu v0,zero,zero`: no warnings, no collapse, and no
        # forced ascent past the progressive-keycard elevator gate. Go-up
        # traps are a separate handler and stay untouched.
        self.assertEqual(
            patch.EARTHQUAKE_DISABLED_PROLOGUE, (0x03E0_0008, 0x0000_1021)
        )
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        writes = _ppf_writes(base_patch)
        expected = struct.pack("<II", *patch.EARTHQUAKE_DISABLED_PROLOGUE)
        self.assertTrue(
            all(
                writes.get(patch.EARTHQUAKE_UPDATE_RAW_OFFSET + index) == value
                for index, value in enumerate(expected)
            ),
            "The earthquake update callback is not disabled in the base patch.",
        )

    def test_base_patch_keeps_seeded_gameplay_without_resident_hud_hook(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        for address in (
            patch.FLOOR_LOCATION_HOOK_WRAPPER_ADDRESS,
            patch.COLLECT_FLOOR_LOCATION_ADDRESS,
            patch.RECEIVE_ITEM_DISPATCHER_ADDRESS,
        ):
            self.assertIn(struct.pack("<I", patch._j(0x03, address)), base_patch)
        self.assertIn(
            struct.pack("<I", patch._j(0x02, patch.SEED_RUNTIME_LOADER_ADDRESS)),
            base_patch,
        )
        self.assertNotIn(struct.pack("<I", patch._j(0x03, 0x801F_F580)), base_patch)
        self.assertNotIn(struct.pack("<I", patch._j(0x03, 0x801F_F7B0)), base_patch)
        self.assertNotIn(struct.pack("<I", patch._j(0x03, 0x801F_F970)), base_patch)
        self.assertNotIn(struct.pack("<I", patch._j(0x02, 0x801F_F900)), base_patch)
        registration_jump = patch._j(
            0x02,
            patch.RESTORED_INVENTORY_HUD_POST_REGISTRATION_ADDRESS,
        )
        self.assertNotIn(
            struct.pack(
                "<II",
                patch._i(0x0F, 0, 8, registration_jump >> 16),
                patch._i(0x0D, 8, 8, registration_jump),
            ),
            base_patch,
        )
        self.assertNotIn(
            struct.pack("<I", patch.RESTORED_INVENTORY_HUD_KEYCARD_LABEL_ADDRESS),
            base_patch,
        )
        self.assertNotIn(
            struct.pack("<I", patch.RESTORED_INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS),
            base_patch,
        )
        self.assertNotIn(b"ADAPHUD1", base_patch)

    def test_base_patch_guards_the_pre_workspace_gameplay_copy(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        writes = _ppf_writes(base_patch)

        self.assertEqual(patch.TOWER_GAMEPLAY_BASE_ADDRESS, 0x801D_9700)
        self.assertEqual(patch.TOWER_GAMEPLAY_END_ADDRESS, 0x801D_9EF0)
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<IIIII",
                    patch._i(0x0F, 0, 4, 0x801E),
                    patch._i(0x09, 4, 4, 0x9700),
                    patch._i(0x23, 4, 8, 0),
                    patch._i(0x0F, 0, 9, 0x3C08),
                    patch._i(0x0D, 9, 9, 0x801E),
                ),
            )
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<IIIII",
                    patch._i(0x0F, 0, 5, 0x800E),
                    patch._i(0x09, 5, 5, 0x4980),
                    patch._i(0x23, 5, 8, 0),
                    patch._i(0x0F, 0, 9, 0x3C08),
                    patch._i(0x0D, 9, 9, 0x801E),
                ),
            )
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<III",
                    patch._i(0x0F, 0, 3, 1),
                    patch._r(17, 0, 0, 0, 0x08),
                    patch._i(0x0D, 3, 3, 0x8800),
                ),
            )
        )

    def test_base_patch_enters_resident_bootstrap_after_first_floor(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        writes = _ppf_writes(base_patch)

        resident_address = 0x8007_BEF0
        delivery_installer_address = 0x800E_5170
        resident_words = (
            patch._i(0x0F, 0, 4, 0x801E),
            patch._i(0x09, 4, 4, 0x9700),
            patch._i(0x23, 4, 8, 0),
            patch._i(0x0F, 0, 9, 0x3C08),
            patch._i(0x0D, 9, 9, 0x801E),
            patch._i(0x05, 8, 9, 3),
            patch._r(31, 0, 17, 0, 0x21),
            patch._j(0x02, patch.SEED_RUNTIME_LOADER_ADDRESS),
            0,
            patch._j(0x02, delivery_installer_address),
            0,
            0,
        )
        resident_raw = _slus_raw_offset(resident_address)
        self.assertEqual(
            bytes(writes[resident_raw + index] for index in range(48)),
            struct.pack("<12I", *resident_words),
        )

        hook_raw = _slus_raw_offset(0x8004_0E8C)
        self.assertEqual(
            bytes(writes[hook_raw + index] for index in range(8)),
            struct.pack("<II", patch._j(0x03, resident_address), 0),
        )

        # Direct inventory-module HUD builds install no global render hook,
        # so the old mode-transition unhook seam remains vanilla.
        self.assertNotIn(_slus_raw_offset(0x8004_0CFC), writes)

    def test_base_patch_contains_exact_direct_inventory_module_streams(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        writes = _ppf_writes(base_patch)

        def read_written_form1_extent(logical_offset: int, length: int) -> bytes:
            result = bytearray()
            copied = 0
            while copied < length:
                current = logical_offset + copied
                chunk_length = min(length - copied, 2048 - current % 2048)
                raw_offset = town_shop.mode2_file_offset_to_raw_offset(
                    0x3016,
                    current,
                )
                result.extend(
                    writes[raw_offset + index] for index in range(chunk_length)
                )
                copied += chunk_length
            return bytes(result)

        # Stream 1 grew from 0x3585 to 0x3588 when the alternate-pickup pass
        # planted two jumps in it - the verb-list builder's tail at 0x8001D0D4
        # and the verb-label lookup at 0x8001CD5C.  Both are in-place
        # instruction rewrites, so the *decoded* module is the same size; only
        # the compressed form moved.  `tools/Rebuild-AdapFrontMenuStreams.py`
        # produced it and verified the round-trip, and the pre-change patch is
        # kept at `.logs/azure_dreams_base-pre-alternate-pickup.ppf`.
        #
        # **Those two jumps target the resident block, so this hash moves
        # whenever that block's layout does.** Re-run the tool after changing
        # anything in `alternate_pickup`; `tools/Verify-AdapDisc.py` on a built
        # disc is what catches a stale stream, because nothing in this suite can
        # decompress it.
        stream_1 = read_written_form1_extent(0x4A3014, 0x3588)
        stream_2 = read_written_form1_extent(0x4A7014, 0x37B0)
        self.assertEqual(
            hashlib.sha256(stream_1).hexdigest().upper(),
            "95248404171B7739287E29931DA77D9AE02B488032E6A5DEDD2D4BAEE8A56090",
        )
        # Stream 2 carries the direct inventory-module HUD at its tail, and its
        # refresh routine at 0x80024C00 reads the AP mailbox for the clearance
        # level.  The carve retraction of 2026-08-01 moved that mailbox and the
        # module kept reading 0x801FFF00, so the magic compare failed and the
        # branch delay slot handed the renderer a hard zero - `Keycard Lvl: 0`
        # and `Max Floor: 4` for every player, cosmetic but wrong.  It reads
        # HIGH_MAILBOX_ADDRESS now.  Only the three immediates moved (five bytes
        # of the decoded image); the compressed form is a full re-encode, so the
        # hash moves with it.  `tools/Rebuild-AdapInventoryHudMailbox.py`
        # produced it and verified the round-trip, and the pre-change patch is
        # kept at `.logs/azure_dreams_base-pre-hud-mailbox-20260802.ppf`.
        #
        # **2026-08-10, kept because it cost a crash:** the tower-resume warp
        # wrapper was briefly hosted in the retired card-screen span at
        # 0x80021160 and this hash moved with it. It was reverted - that span
        # is inside the FRONT-MENU package, which is not resident in town, so
        # the trampoline jumped into the outdoor town overlay instead. The
        # stream is byte-restored and this hash is the original again. Useful
        # fact that survived: stream 2 decodes to 0x7A60 from 0x8001D590,
        # ending exactly at DIRECT_INVENTORY_MODULE_END_ADDRESS.
        self.assertEqual(
            hashlib.sha256(stream_2).hexdigest().upper(),
            "43D3BA098A257348A544740AE43ADF88DFCB972CC11A75CEBE9F14C58E87DC11",
        )
        self.assertEqual(patch.DIRECT_INVENTORY_MODULE_BASE_ADDRESS, 0x8002_4B20)
        self.assertEqual(patch.DIRECT_INVENTORY_MODULE_END_ADDRESS, 0x8002_4FF0)
        self.assertEqual(
            patch.DIRECT_INVENTORY_HUD_KEYCARD_LABEL_ADDRESS,
            0x8002_4E10,
        )
        self.assertEqual(
            patch.DIRECT_INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS,
            0x8002_4EA0,
        )

    def test_base_patch_contains_native_receive_dispatcher_calls(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        writes = _ppf_writes(base_patch)
        for address in (
            0x8009_8FB0,
            0x8009_8FF8,
            0x8009_90FC,
            0x8009_9368,
            0x800A_5720,
        ):
            self.assertTrue(
                _contains_written_bytes(writes, struct.pack("<I", patch._j(0x03, address)))
            )

        hook_raw_offset = 0x1C5_A970
        self.assertEqual(
            bytes(writes[hook_raw_offset + index] for index in range(4)),
            struct.pack("<I", patch._j(0x03, patch.RECEIVE_ITEM_DISPATCHER_ADDRESS)),
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<II",
                    patch._i(0x2B, 8, 10, _mailbox_offset(0x00)),
                    patch._i(0x09, 0, 9, 3),
                ),
            )
        )

        self.assertTrue(
            _contains_written_bytes(writes, struct.pack("<I", patch._i(0x29, 8, 9, _mailbox_offset(0x04))))
        )
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<III",
                    patch._i(0x09, 0, 9, 3),
                    patch._i(0x2B, 8, 9, _mailbox_offset(0xA8)),
                    patch._i(0x2B, 8, 21, _mailbox_offset(0xA4)),
                ),
            )
        )
        # Presentation is read from its own mailbox word, not from bit 31 of
        # the descriptor. It used to be `srl s6,t7,31`, which shared bit 7 of
        # the flags byte with the game's "unidentified" flag and made it
        # impossible to deliver an unappraised item.
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<I",
                    patch._i(
                        0x23,
                        8,
                        22,
                        (
                            patch.HIGH_MAILBOX_ADDRESS
                            + patch.MAILBOX_RECEIVE_PRESENTATION_OFFSET
                        )
                        & 0xFFFF,
                    ),
                ),
            )
        )
        # The descriptor reaches the inventory unmasked. It used to pass through
        # `sll t1,t1,1 / srl t1,t1,1`, which cleared bit 31 - bit 7 of the flags
        # byte, the game's "unidentified" flag - because the protocol borrowed
        # that bit for its presentation request. Presentation has its own
        # mailbox word now, so the strip is gone and those two slots are nops.
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<IIIII",
                    patch._i(0x23, 8, 9, _mailbox_offset(0xA0)),   # lw t1,descriptor
                    patch._i(0x23, 29, 8, 0x14),                   # lw t0,0x14(sp)
                    0,
                    0,
                    patch._i(0x2B, 23, 9, 0),                      # sw t1,0(s7)
                ),
            )
        )
        self.assertFalse(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<II",
                    patch._r(0, 9, 9, 1, 0x00),
                    patch._r(0, 9, 9, 1, 0x02),
                ),
            )
        )

    def test_base_patch_pickup_classifier_accepts_protocol_v3(self) -> None:
        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        writes = _ppf_writes(base_patch)
        self.assertTrue(
            _contains_written_bytes(
                writes,
                struct.pack(
                    "<III",
                    patch._i(0x25, 8, 10, _mailbox_offset(0x04)),
                    patch._i(0x09, 0, 11, 3),
                    patch._i(0x05, 10, 11, 59),
                ),
            )
        )

    def test_base_patch_validates_the_relocated_seed_page(self) -> None:
        """Every base-patch reference to the seed page follows its address.

        The page has moved twice (0x801FD600 -> 0x801C8E40 -> 0x801D7F00),
        and every old home must have no survivors."""

        base_patch = (Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf").read_bytes()
        low = patch._lower(patch.SEED_BLOCK_ADDRESS)
        # Derived, not assumed. An earlier revision hard-coded `upper + 1`
        # because 0x801C8E40's low half was >= 0x8000 and the lui had to
        # compensate; 0x801D7F00's does not, so the assumption inverted.
        seed_magic_load = struct.pack(
            "<II",
            patch._i(0x0F, 0, 8, patch._upper(patch.SEED_BLOCK_ADDRESS)),
            patch._i(0x23, 8, 9, low),
        )
        seed_count_load = struct.pack("<I", patch._i(0x25, 8, 9, low + 6))
        self.assertIn(seed_magic_load, base_patch)
        self.assertIn(seed_count_load, base_patch)
        # The carve-era home and its predecessor must have no survivors.
        self.assertNotIn(
            struct.pack("<II", patch._i(0x0F, 0, 8, 0x8020), patch._i(0x23, 8, 9, 0xD600)),
            base_patch,
        )
        self.assertNotIn(struct.pack("<I", patch._i(0x25, 8, 9, 0xD606)), base_patch)
        # ...and the 07-30 home the floor arena overwrote (2026-08-02 crash).
        self.assertNotIn(
            struct.pack("<II", patch._i(0x0F, 0, 8, 0x801D), patch._i(0x23, 8, 9, 0x8E40)),
            base_patch,
        )
        self.assertNotIn(struct.pack("<I", patch._i(0x23, 8, 9, 0xD000)), base_patch)
        # The carve is retracted: the ceiling is vanilla 2 MB again, and
        # neither carved value may survive anywhere in the patch. The effect
        # pool filling to ceiling-8 at the mix-magic crash is the evidence
        # that the carve was the mechanism (docs/carve-retraction-plan.md).
        self.assertIn(struct.pack("<I", 0x0020_0000), base_patch)
        self.assertNotIn(struct.pack("<I", 0x001F_E600), base_patch)
        self.assertNotIn(struct.pack("<I", 0x001F_D600), base_patch)

    def test_generated_zero_sectors_match_original_disc_parity(self) -> None:
        # These hashes are from the untouched US disc's two selected zeroed
        # Form-1 dummy sectors. Equality proves header, EDC, P, and Q parity.
        expected = {
            31_448: "f16684a53c600485ff531db6e65d0f35ba9e4d8c31c308db3903dc6c86330068",
            31_449: "dda88497f04cf28e298212b2968945ae741acb9e7f712d26368f7c35214fff50",
            31_450: "d4e7907d49e894cfb1f22d04768afce71371c62b6513be49617bcbb990d5402e",
        }
        for lba, expected_hash in expected.items():
            sector = patch.build_mode2_form1_sector(lba, bytes(2048))
            self.assertEqual(hashlib.sha256(sector).hexdigest(), expected_hash)

    def test_player_ppf_appends_complete_seed_sectors(self) -> None:
        # The synthetic base still has to carry the receive hook's record:
        # build_player_ppf verifies it before retargeting it at the
        # forced-trap stub, and refuses a base patch without it.
        base = (
            b"PPF10\0"
            + bytes(50)
            + struct.pack(
                "<IBI",
                patch.RECEIVE_HOOK_RAW_OFFSET,
                4,
                patch.RECEIVE_HOOK_ORIGINAL_WORD,
            )
        )
        seed_block = bytes((index & 0xFF for index in range(patch.SEED_BLOCK_SIZE)))
        result = patch.build_player_ppf(base, seed_block, "ADAP unit test")
        self.assertEqual(result[6:21], b"ADAP unit test\0")

        reconstructed: dict[int, int] = {}
        cursor = patch.PPF_HEADER_SIZE
        while cursor < len(result):
            raw_offset, length = struct.unpack_from("<IB", result, cursor)
            cursor += 5
            for offset, value in enumerate(result[cursor : cursor + length]):
                reconstructed[raw_offset + offset] = value
            cursor += length

        for sector_index in range(2):
            lba = patch.SEED_SECTOR_LBA + sector_index
            observed = bytes(
                reconstructed[lba * patch.RAW_SECTOR_SIZE + offset]
                for offset in range(patch.RAW_SECTOR_SIZE)
            )
            user_data = seed_block[sector_index * 2048 : (sector_index + 1) * 2048]
            self.assertEqual(observed, patch.build_mode2_form1_sector(lba, user_data))


class FloorPageLoaderTests(unittest.TestCase):
    """The per-floor page loader, riding the elevator-orb animator's callback.

    It must always fall through into the real animator with the object
    pointer (a0) intact, and only in settled gameplay (mode 2) with a stale
    page may it read the floor's bank sector over the window first. The
    construction hook is off limits by hard-won rule - see the loader's
    docstring."""

    ANIMATOR_OBJECT = 0x801E_4000

    def _run(self, floor, page_floor=None, placements=None, mode=2):
        placements = placements or [
            patch.LocationPlacement("Gold", "Koh", False)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        memory = mips_sim.Memory()
        memory.load_bytes(patch.SEED_BLOCK_ADDRESS, block)
        memory.write32(0x8008_146C, floor)
        memory.load_bytes(0x8008_2E6A, bytes([mode]))
        if page_floor is not None:
            # Override the baked page header's floor to stage staleness.
            header = patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_HEADER_OFFSET
            memory.load_bytes(header + 4, struct.pack("<H", page_floor))

        calls: list[tuple[str, tuple[int, ...]]] = []
        pages = patch.build_floor_page_sectors(block, placements)

        def animator(cpu: mips_sim.Cpu) -> None:
            calls.append(("animator", (cpu.registers[4],)))  # a0 = object

        def build_descriptor(cpu: mips_sim.Cpu) -> None:
            calls.append(
                (
                    "build",
                    (
                        cpu.registers[4],  # sector count
                        cpu.registers[5],  # destination
                        cpu.registers[6],  # descriptor buffer
                        cpu.registers[7],  # LBA
                    ),
                )
            )

        def enqueue(cpu: mips_sim.Cpu) -> None:
            calls.append(("enqueue", (cpu.registers[4], cpu.registers[5])))

        def wait(cpu: mips_sim.Cpu) -> None:
            calls.append(("wait", ()))
            # The read completes while waiting: land the requested sector,
            # exactly like the drive DMA would.
            build = next(args for name, args in calls if name == "build")
            lba = build[3]
            memory.load_bytes(build[1], pages[lba - patch.FLOOR_PAGE_BANK_LBA])

        cpu = mips_sim.Cpu(
            memory,
            stubs={
                patch.FLOOR_PAGE_ANIMATOR_CALLBACK_ADDRESS: animator,
                patch._BUILD_CD_READ_DESCRIPTOR_ADDRESS: build_descriptor,
                patch._ENQUEUE_CD_COMMAND_ADDRESS: enqueue,
                patch._WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS: wait,
            },
        )
        cpu.registers[4] = self.ANIMATOR_OBJECT
        cpu.run(patch.FLOOR_PAGE_LOADER_ADDRESS)
        return calls, memory

    def test_a_stale_page_is_replaced_before_the_animator_runs(self) -> None:
        calls, memory = self._run(floor=7, page_floor=1)
        self.assertEqual(
            [name for name, _ in calls],
            ["build", "enqueue", "wait", "animator"],
        )
        _, build = calls[0]
        self.assertEqual(build[0], 1)  # one sector
        self.assertEqual(build[1], patch.FLOOR_PAGE_WINDOW_ADDRESS)
        self.assertEqual(build[3], patch.FLOOR_PAGE_BANK_LBA + 6)  # floor 7
        _, enqueue = calls[1]
        self.assertEqual(enqueue[0], 6)  # the CD read command
        self.assertEqual(enqueue[1], build[2])  # the descriptor it built
        self.assertEqual(
            memory.read16(
                patch.SEED_BLOCK_ADDRESS + patch.FLOOR_PAGE_HEADER_OFFSET + 4
            ),
            7,
        )
        # The animator still runs, with its object pointer intact.
        self.assertEqual(calls[-1][1][0], self.ANIMATOR_OBJECT)

    def test_outside_gameplay_it_only_forwards_to_the_animator(self) -> None:
        for mode in (0x00, 0x01, 0xFF):
            with self.subTest(mode=hex(mode)):
                calls, _ = self._run(floor=7, page_floor=1, mode=mode)
                self.assertEqual(
                    [name for name, _ in calls], ["animator"]
                )
                self.assertEqual(calls[0][1][0], self.ANIMATOR_OBJECT)

    def test_a_current_page_skips_the_read_entirely(self) -> None:
        calls, _ = self._run(floor=1)  # the baked page is floor 1's
        self.assertEqual([name for name, _ in calls], ["animator"])

    def test_floor_forty_never_reads(self) -> None:
        calls, _ = self._run(floor=40, page_floor=39)
        self.assertEqual([name for name, _ in calls], ["animator"])

    def test_floor_thirty_nine_reads_the_last_bank_sector(self) -> None:
        calls, _ = self._run(floor=39, page_floor=38)
        build = dict(calls)["build"]
        self.assertEqual(
            build[3], patch.FLOOR_PAGE_BANK_LBA + patch.FLOOR_PAGE_FLOOR_COUNT - 1
        )

    def test_the_animator_hook_words_materialize_the_loader(self) -> None:
        # The creator's lui/addiu pair must land exactly on the loader; a
        # mismatched pair would register a garbage callback and hang every
        # floor. Recomputed the same way build_player_ppf emits them.
        hi = (patch.FLOOR_PAGE_LOADER_ADDRESS + 0x8000) >> 16
        lo = patch.FLOOR_PAGE_LOADER_ADDRESS & 0xFFFF
        lo_signed = lo - 0x10000 if lo >= 0x8000 else lo
        self.assertEqual(
            (hi << 16) + lo_signed, patch.FLOOR_PAGE_LOADER_ADDRESS
        )

    def test_the_animator_hook_site_matches_the_original_disc(self) -> None:
        original = (
            Path(__file__).parents[4]
            / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
        )
        if not original.is_file():
            self.skipTest("The original disc image is not present.")
        with original.open("rb") as disc:
            disc.seek(patch.FLOOR_PAGE_ANIMATOR_HOOK_RAW_OFFSET)
            words = struct.unpack("<II", disc.read(8))
        self.assertEqual(words, patch.FLOOR_PAGE_ANIMATOR_HOOK_ORIGINAL)


if __name__ == "__main__":
    unittest.main()
