"""TESTING - the force-single-room knob (patch.FORCE_SINGLE_ROOM_TEST). Delete with it.

Checks the edit against the original disc bytes (the word we replace really is
the `slti v0,v0,2` at 0x80019BE0 in BOTH floor-generation package copies), that
the replacement is the same instruction with the 0x7FFF immediate, and that the
section contributes nothing to a seed when the flag is off.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from unittest import mock

from .. import floor_item_pool, patch

_DUNGEON_BIN = Path(__file__).resolve().parents[4] / "extracted" / "DUNGEON.BIN"


class TestForceSingleRoom(unittest.TestCase):
    def test_replacement_is_slti_with_the_max_immediate(self) -> None:
        original, replacement = (
            patch.FORCE_SINGLE_ROOM_ORIGINAL_WORD,
            patch.FORCE_SINGLE_ROOM_REPLACEMENT_WORD,
        )
        # Same opcode/rs/rt (slti v0,v0,...), only the immediate changes.
        self.assertEqual(original >> 16, 0x2842)
        self.assertEqual(replacement >> 16, 0x2842)
        self.assertEqual(original & 0xFFFF, 2)
        self.assertEqual(replacement & 0xFFFF, 0x7FFF)

    def test_both_package_copies_are_written(self) -> None:
        with mock.patch.object(patch, "FORCE_SINGLE_ROOM_TEST", True):
            patches = patch.iter_force_single_room_dungeon_file_patches()
        self.assertEqual(len(patches), 2)
        offsets = {file_offset for file_offset, _ in patches}
        self.assertEqual(
            offsets,
            {
                copy_offset + patch.FORCE_SINGLE_ROOM_COMPARE_ADDRESS - 0x8000_0000
                for copy_offset in floor_item_pool.FLOOR_GENERATION_FILE_OFFSETS
            },
        )
        for _, data in patches:
            self.assertEqual(
                struct.unpack("<I", data)[0], patch.FORCE_SINGLE_ROOM_REPLACEMENT_WORD
            )

    @unittest.skipUnless(_DUNGEON_BIN.exists(), "extracted DUNGEON.BIN not present")
    def test_original_word_matches_the_disc_in_both_copies(self) -> None:
        with mock.patch.object(patch, "FORCE_SINGLE_ROOM_TEST", True):
            patches = patch.iter_force_single_room_dungeon_file_patches()
        with _DUNGEON_BIN.open("rb") as handle:
            for file_offset, _ in patches:
                handle.seek(file_offset)
                word = struct.unpack("<I", handle.read(4))[0]
                self.assertEqual(
                    word, patch.FORCE_SINGLE_ROOM_ORIGINAL_WORD, hex(file_offset)
                )

    def test_flag_off_contributes_nothing(self) -> None:
        with mock.patch.object(patch, "FORCE_SINGLE_ROOM_TEST", False):
            self.assertEqual(patch.iter_force_single_room_dungeon_file_patches(), ())


if __name__ == "__main__":
    unittest.main()
