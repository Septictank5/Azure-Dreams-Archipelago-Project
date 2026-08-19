"""Wotta's pool house - the girls' spawn records and the all-girls test disc.

`docs/systems/pool-house.md` owns the account. The records are pinned against
the original disc when it is present, and the intro-state flags against the
scripts that set them in the pool-house dialogue image.
"""

import struct
import unittest
from pathlib import Path

from .. import pool_house, town_shop

ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)
EXTRACTED_TOWN = Path(__file__).parents[4] / "extracted" / "TOWN.BIN"


def _town_bytes(file_offset: int, length: int) -> bytes | None:
    if EXTRACTED_TOWN.exists():
        with EXTRACTED_TOWN.open("rb") as handle:
            handle.seek(file_offset)
            return handle.read(length)
    if ORIGINAL_BIN.exists():
        raw = town_shop.mode2_file_offset_to_raw_offset(town_shop.TOWN_FILE_START_LBA, file_offset)
        with ORIGINAL_BIN.open("rb") as handle:
            handle.seek(raw)
            return handle.read(length)   # records never cross a sector here
    return None


class PoolHouseRecordTests(unittest.TestCase):
    def test_the_seven_girl_records_are_where_the_overlay_says(self) -> None:
        for entry, template, flag, word, model, (x, y) in pool_house.GIRL_SPAWN_RECORDS:
            expected = pool_house.vanilla_spawn_record(flag, word, model, x, y)
            self.assertEqual(len(expected), pool_house.SPAWN_RECORD_SIZE)
            self.assertEqual(struct.unpack_from("<H", expected, 2)[0], flag)
            self.assertEqual(struct.unpack_from("<H", expected, 0)[0], 0x4000, "dormant bit")
            found = _town_bytes(pool_house.overlay_file_offset(template), pool_house.SPAWN_RECORD_SIZE)
            if found is None:
                self.skipTest("TOWN.BIN not present")
            self.assertEqual(found, expected, f"entry {entry} record at 0x{template:08X} moved")

    def test_the_presence_flags_are_the_rotation_tables(self) -> None:
        # The rotation table at 0x80019088 (overlay) lists the seven girls as
        # 8-byte (presence flag, story flag, condition, 0) entries in this order.
        table = _town_bytes(pool_house.overlay_file_offset(0x8001_9088), 7 * 8)
        if table is None:
            self.skipTest("TOWN.BIN not present")
        flags = [struct.unpack_from("<H", table, 8 * i)[0] for i in range(7)]
        self.assertEqual(flags, [0x607, 0x608, 0x609, 0x60A, 0x60B, 0x60C, 0x60D])
        self.assertEqual(sorted(record[2] for record in pool_house.GIRL_SPAWN_RECORDS), flags)

    def test_the_patch_turns_each_flag_into_always(self) -> None:
        patches = dict(pool_house.iter_pool_house_file_patches())
        if not pool_house.POOL_HOUSE_OPEN:
            self.assertEqual(patches, {})
            return
        for _, template, _, _, _, _ in pool_house.GIRL_SPAWN_RECORDS:
            self.assertEqual(
                patches[pool_house.overlay_file_offset(template)
                        + pool_house.SPAWN_RECORD_FLAG_OFFSET],
                struct.pack("<H", pool_house.ALWAYS_PRESENT_FLAG),
            )

    def test_the_patch_touches_only_the_fields_it_claims(self) -> None:
        """A record is 20 bytes and we edit three fields of it; a stray write
        into the model or the terminator would spawn the wrong thing. The
        dialogue rewrites are whole regions, bounded by their own test."""
        allowed = set()
        for _, template, flag, _, _, _ in pool_house.GIRL_SPAWN_RECORDS:
            base = pool_house.overlay_file_offset(template)
            allowed.add((base + pool_house.SPAWN_RECORD_FLAG_OFFSET, 2))
            allowed.add((base + pool_house.SPAWN_RECORD_POSITION_OFFSET, 4))
            if flag in pool_house.FACING_OVERRIDES:
                allowed.add((base + pool_house.SPAWN_RECORD_FACING_OFFSET, 1))
        for _, _, script, region, _, _ in pool_house.GIRL_DIALOGUE:
            allowed.add((pool_house.dialogue_file_offset(script), region))
        for offset, data in pool_house.iter_pool_house_file_patches():
            self.assertIn((offset, len(data)), allowed, f"stray write at 0x{offset:X}")

    def test_the_record_and_dialogue_edits_never_overlap(self) -> None:
        written = set()
        for offset, data in pool_house.iter_pool_house_file_patches():
            span = set(range(offset, offset + len(data)))
            self.assertFalse(written & span, f"two writers at 0x{offset:X}")
            written |= span

    def test_the_section_base_is_the_one_measured_off_live_actors(self) -> None:
        """`local + base = the live actor's global position`, measured on the
        girls themselves rather than inferred from a scene descriptor.

        The globals below were read out of two save states (every record's
        live actor is a town prop updater with its position at actor+0xA4/
        +0xA6): `Wotta_pool_house_position_check_3.sav` on the m2 disc, where
        the girls sat on their vanilla spots, and `..._check_4.sav` on the m3
        disc, where they sat in the first (mis-placed) row. All fourteen agree
        on (512, 512).

        m3 shipped (0, 0) here - taken from a field of section 0x0D's 32-byte
        scene descriptor - and put the girls outside the pool house on both
        axes. `docs/systems/town-warp-and-uncle.md` §2.1 names that mistake.
        """
        self.assertEqual(pool_house.SECTION_BASE, (512, 512))

        vanilla_live = {
            0x0607: (1568, 1336), 0x0608: (2144, 1184), 0x0609: (1728, 1248),
            0x060A: (1632, 1312), 0x060B: (2016, 1184), 0x060C: (1632, 1024),
            0x060D: (1824, 1504),
        }
        for _, _, flag, _, _, local in pool_house.GIRL_SPAWN_RECORDS:
            self.assertEqual(
                pool_house.local_to_global(*local), vanilla_live[flag],
                f"0x{flag:04X}: vanilla record does not land on its measured actor",
            )

        first_row_live = [
            (2112, 1312), (2240, 1312), (2368, 1312), (2496, 1312),
            (2624, 1312), (2752, 1312), (2880, 1312),
        ]
        first_row_locals = [(1600 + 128 * i, 800) for i in range(7)]
        for local, live in zip(first_row_locals, first_row_live):
            self.assertEqual(pool_house.local_to_global(*local), live)

        # And the conversion round-trips, which is the property the row uses.
        for x, y in pool_house.lineup_globals():
            self.assertEqual(pool_house.local_to_global(*pool_house.global_to_local(x, y)), (x, y))

    def test_the_row_is_measured_evenly_spaced_and_inside_the_limit(self) -> None:
        if not pool_house.POOL_HOUSE_LINE_UP:
            self.skipTest("the lineup is off")
        row = pool_house.lineup_globals()
        self.assertEqual(len(row), len(pool_house.GIRL_SPAWN_RECORDS))
        # Anchored on the measured "by the changing rooms" spot (1605, 795),
        # snapped to the 32-unit grid every vanilla record uses.
        self.assertEqual(row[0], (1600, 800))
        self.assertTrue(all(y == row[0][1] for _, y in row), "one row, one Y")
        steps = {row[i + 1][0] - row[i][0] for i in range(len(row) - 1)}
        self.assertEqual(steps, {pool_house.LINEUP_STEP_X})
        self.assertGreater(pool_house.LINEUP_STEP_X, 78, "wider than the measured pair")
        self.assertLessEqual(row[-1][0], pool_house.LINEUP_LIMIT_GLOBAL_X)
        for x, y in row:
            self.assertEqual((x % 32, y % 32), (0, 0))
        # The records carry LOCAL coordinates, so they must differ from the
        # globals by exactly the base - the m3 bug was writing globals here.
        self.assertEqual(
            pool_house.lineup_positions()[0],
            (row[0][0] - 512, row[0][1] - 512),
        )
        self.assertNotEqual(pool_house.lineup_positions(), row)

    def test_the_row_is_ordered_by_presence_flag(self) -> None:
        """Left to right is 0x0607..0x060D, so "third from the left" names a
        specific girl in a play report."""
        if not pool_house.POOL_HOUSE_LINE_UP:
            self.skipTest("the lineup is off")
        patches = dict(pool_house.iter_pool_house_file_patches())
        row = pool_house.lineup_positions()
        by_flag = sorted(pool_house.GIRL_SPAWN_RECORDS, key=lambda record: record[2])
        self.assertEqual(
            [record[2] for record in by_flag],
            [record[2] for record in pool_house.GIRL_SPAWN_RECORDS],
        )
        for index, (_, template, _, _, _, _) in enumerate(pool_house.GIRL_SPAWN_RECORDS):
            offset = (pool_house.overlay_file_offset(template)
                      + pool_house.SPAWN_RECORD_POSITION_OFFSET)
            self.assertEqual(struct.unpack("<hh", patches[offset]), row[index])


class PoolHouseDialogueTests(unittest.TestCase):
    """The girls' dialogue regions, and the greetings that claim them."""

    def _town(self) -> bytes:
        if not EXTRACTED_TOWN.exists():
            self.skipTest("TOWN.BIN not present")
        return EXTRACTED_TOWN.read_bytes()

    def test_the_encoder_reproduces_a_vanilla_line_byte_for_byte(self) -> None:
        town = self._town()
        # Nico's opening line, at 0x8001BDA7 (right after her 7-byte prologue).
        start = pool_house.dialogue_file_offset(0x8001_BDA7)
        expected = pool_house.encode_text("Hello, {name}.  You came too.")
        self.assertEqual(town[start:start + len(expected)], expected)

    def test_each_region_is_bounded_by_the_next_referenced_script(self) -> None:
        """A region ends where the next address the OVERLAY points at begins.

        Branch targets inside a script are not entry points, so only overlay
        references bound a region - that is what makes the sizes claimable.
        """
        town = self._town()
        overlay = town[pool_house.OVERLAY_FILE_OFFSET:
                       pool_house.OVERLAY_FILE_OFFSET
                       + (pool_house.DIALOGUE_IMAGE_RUNTIME_ADDRESS
                          - pool_house.OVERLAY_RUNTIME_ADDRESS)]
        entries = set()
        for offset in range(0, len(overlay) - 3, 4):
            value = struct.unpack_from("<I", overlay, offset)[0]
            if pool_house.DIALOGUE_IMAGE_RUNTIME_ADDRESS <= value < 0x8002_0000:
                entries.add(value)
        for flag, _, script, region, _, _ in pool_house.GIRL_DIALOGUE:
            self.assertIn(script, entries, f"0x{flag:04X}'s script is not an entry point")
            later = sorted(entry for entry in entries if entry > script)
            self.assertEqual(later[0] - script, region,
                             f"0x{flag:04X}'s region moved")

    def test_the_dialogue_tables_point_where_we_think(self) -> None:
        town = self._town()
        for flag, table, script, _, _, slot in pool_house.GIRL_DIALOGUE:
            offset = pool_house.overlay_file_offset(table)
            key, _state = struct.unpack_from("<HH", town, offset)
            pointer = struct.unpack_from("<I", town, offset + 4)[0]
            self.assertEqual(pointer, script, f"0x{flag:04X}'s table row moved")
            # The table key is the actor/portrait slot the script attaches.
            self.assertEqual(key, slot, f"0x{flag:04X}'s key is not her actor slot")

    def test_the_prologue_is_copied_not_invented(self) -> None:
        town = self._town()
        for index, (flag, _, script, _, size, slot) in enumerate(pool_house.GIRL_DIALOGUE):
            prologue = pool_house.vanilla_prologue(index, town)
            self.assertEqual(len(prologue), size)
            self.assertEqual(prologue[0], 0x57, "window mode")
            self.assertEqual(prologue[1], 0x00)
            self.assertEqual(prologue[2], 0x1C, "portrait attach")
            self.assertEqual(prologue[3], slot)
            self.assertEqual(prologue[-2:], bytes((0x57, slot)), "name-plate window")
            # and nothing of the text has been swept into it
            self.assertTrue(all(b < 0x80 for b in prologue))

    def test_each_greeting_fits_and_the_rest_is_end_of_script_filled(self) -> None:
        town = self._town()
        for index, (flag, _, _, region, _, _) in enumerate(pool_house.GIRL_DIALOGUE):
            script = pool_house.build_hello_script(
                index, pool_house.vanilla_prologue(index, town))
            self.assertEqual(len(script), region, f"0x{flag:04X} does not fill her region")
            # rstrip eats the script's own terminator along with the fill, so
            # the body ends on the wait and the terminator is the first pad byte.
            body = script.rstrip(bytes((pool_house.END_OF_SCRIPT,)))
            self.assertLess(len(body), region)
            self.assertEqual(body[-1], pool_house.WAIT_FOR_BUTTON)
            self.assertEqual(script[len(body)], pool_house.END_OF_SCRIPT)
            # never zero-filled: a stray entry must END, not misparse
            self.assertNotIn(0x00, script[len(body):])
            self.assertEqual(set(script[len(body):]), {pool_house.END_OF_SCRIPT})

    def test_the_greetings_are_distinct_so_a_ride_maps_names_to_flags(self) -> None:
        self.assertEqual(len(set(pool_house.HELLO_LINES)), len(pool_house.GIRL_DIALOGUE))

    def test_the_names_are_the_ones_read_off_the_plates(self) -> None:
        """Ridden 2026-08-15, left to right. The plate comes from a name table
        keyed by the portrait slot, so this mapping cannot be derived - it was
        measured, and every later feature that says "Selfi" depends on it."""
        self.assertEqual(
            [pool_house.GIRL_NAMES[record[0]] for record in pool_house.GIRL_DIALOGUE],
            ["Nico", "Fur", "Selfi", "Cherrl", "Vivian", "Mia", "Patty"],
        )
        self.assertEqual(
            set(pool_house.GIRL_NAMES), {record[2] for record in pool_house.GIRL_SPAWN_RECORDS}
        )

    def test_the_claimed_total_is_what_the_report_says(self) -> None:
        self.assertEqual(pool_house.dialogue_region_bytes(), 3840)

    def test_pattys_facing_is_the_only_record_override(self) -> None:
        self.assertEqual(pool_house.FACING_OVERRIDES, {0x060D: pool_house.FACING_DOWN})
        town = self._town()
        # She is the only girl whose vanilla facing byte is not 1.
        for _, template, flag, word, _, _ in pool_house.GIRL_SPAWN_RECORDS:
            offset = pool_house.overlay_file_offset(template) + pool_house.SPAWN_RECORD_FACING_OFFSET
            self.assertEqual(town[offset], word & 0xFF)
            if flag == 0x060D:
                self.assertEqual(town[offset], 2, "Patty faced left in m4")
            else:
                self.assertEqual(town[offset], pool_house.FACING_DOWN)

    def test_long_writes_are_split_at_mode2_sector_boundaries(self) -> None:
        """A dialogue rewrite is hundreds of bytes and does cross sectors."""
        raw = pool_house.iter_pool_house_raw_patches()
        self.assertGreater(len(raw), len(pool_house.iter_pool_house_file_patches()))
        for offset, data in raw:
            sector_start = offset - (offset % 2_352)
            within = offset - sector_start - 24
            self.assertLessEqual(within + len(data), 2_048,
                                 f"run at 0x{offset:X} runs off its sector")


class PoolHouseIntroFlagTests(unittest.TestCase):
    def test_quest_done_flags_are_in_the_intro_state_when_testing(self) -> None:
        got = set()
        for address, mask in town_shop.INTRO_STATE_WRITES:
            if address < town_shop.STORY_FLAG_ARRAY_ADDRESS:
                continue
            word = (address - town_shop.STORY_FLAG_ARRAY_ADDRESS) // 4
            for bit in range(32):
                if mask >> bit & 1:
                    got.add(word * 32 + bit)
        for flag in town_shop.POOL_HOUSE_QUEST_DONE_FLAGS:
            if pool_house.POOL_HOUSE_OPEN:
                self.assertIn(flag, got)
            else:
                self.assertNotIn(flag, got)

    def test_the_flag_set_is_the_measured_post_quest_state(self) -> None:
        self.assertEqual(
            set(town_shop.POOL_HOUSE_QUEST_DONE_FLAGS),
            {0x05FB, 0x05FC, 0x05FD, 0x0600, 0x11F9, 0x11FC},
        )
        # 0x05FE is Wotta's variant advance, NOT part of the post-quest state:
        # the measured save has it clear. An earlier revision set it.
        self.assertNotIn(0x05FE, town_shop.POOL_HOUSE_QUEST_DONE_FLAGS)

    def test_0x0600_is_the_entry_events_master_switch(self) -> None:
        """`0x8001D07C: if flag(0x0600) is CLEAR goto the chain; else END.`

        Opcode 0x2B is "branch if the flag is clear" (dispatch table
        0x8006AA90, handler 0x80039C74: read u16 flag, call the resident test
        0x80033B2C, `bne v0,zero` past the u32 target). So a set 0x0600 ends
        the pool-house entry script on its first instruction, which is what
        gives the player control on entry.
        """
        entry = _town_bytes(
            0x8001_D07C - pool_house.DIALOGUE_IMAGE_RUNTIME_ADDRESS
            + pool_house.DIALOGUE_IMAGE_FILE_OFFSET,
            8,
        )
        if entry is None:
            self.skipTest("TOWN.BIN not present")
        self.assertEqual(entry[0], 0x2B, "entry script no longer opens with a flag branch")
        self.assertEqual(int.from_bytes(entry[1:3], "little"), 0x0600)
        self.assertEqual(int.from_bytes(entry[3:7], "little"), 0x8001_D084)
        self.assertEqual(entry[7], 0x01, "the not-taken path must be END")
        self.assertIn(0x0600, town_shop.POOL_HOUSE_QUEST_DONE_FLAGS)

    def test_the_stage_scripts_and_the_water_gate_are_where_the_flags_say(self) -> None:
        def image(runtime: int, length: int):
            return _town_bytes(
                runtime - pool_house.DIALOGUE_IMAGE_RUNTIME_ADDRESS
                + pool_house.DIALOGUE_IMAGE_FILE_OFFSET,
                length,
            )
        chain = image(0x8001_D084, 0x40)
        if chain is None:
            self.skipTest("TOWN.BIN not present")
        # The three stage branches, in order, each "if clear -> run the stage".
        for offset, flag, target in (
            (0x1A, 0x05FB, 0x8001_E28C),
            (0x28, 0x05FC, 0x8001_F03C),
            (0x2F, 0x05FD, 0x8001_F271),
        ):
            self.assertEqual(chain[offset], 0x2B, f"stage branch for 0x{flag:04X}")
            self.assertEqual(int.from_bytes(chain[offset + 1:offset + 3], "little"), flag)
            self.assertEqual(int.from_bytes(chain[offset + 3:offset + 7], "little"), target)
            self.assertIn(flag, town_shop.POOL_HOUSE_QUEST_DONE_FLAGS)
        # Stage C is the medal nag; it opens with the text-window mode byte.
        self.assertEqual(image(0x8001_F271, 2), bytes.fromhex("5714"))
        # The quest opener sets 0x11F9, and the delivery the player took sets
        # 0x05FD + 0x11FC (the other delivery path, 0x8001985C, sets 0x05FE).
        self.assertEqual(image(0x8001_F030, 3), bytes.fromhex("0CF911"))
        self.assertEqual(image(0x8001_F8A2, 6), bytes.fromhex("0CFD050CFC11"))
        self.assertEqual(image(0x8001_985C, 6), bytes.fromhex("0CFE050CFC11"))

    def test_the_dry_pool_gate_reads_0x11FC(self) -> None:
        """The scene init's only read of 0x11FC: `addiu a0,zero,0x11FC` at
        0x80016544, inside `if (!flag(0x11FC)) { store/move the pool image }`."""
        word = _town_bytes(pool_house.overlay_file_offset(0x8001_6544), 4)
        if word is None:
            self.skipTest("TOWN.BIN not present")
        self.assertEqual(int.from_bytes(word, "little"), 0x2404_11FC)
        self.assertIn(0x11FC, town_shop.POOL_HOUSE_QUEST_DONE_FLAGS)


if __name__ == "__main__":
    unittest.main()
