"""The group-V move-routine fix (`docs/game/carrier-attack-theories.md` §1).

The disc-backed tests re-measure the claim the fix rests on: that Picket's and
each group-V species' move routines are the same 466 words apart from exactly
the two at +0x324/+0x328, in all seven pre-linked copies.  They skip without
the original disc.
"""

import struct
import unittest
from pathlib import Path

from .. import species_move_fix as smf

ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)
MOVE_ROUTINE_WORDS = 466


def _package_words(disc: bytes, species: int, copy: int, offset: int, count: int) -> list[int]:
    words = []
    for index in range(count):
        raw = smf.archive_raw_offset(species, copy, offset + index * 4)
        words.append(struct.unpack_from("<I", disc, raw)[0])
    return words


class TestLayout(unittest.TestCase):
    def test_thirty_five_sites_two_words_each(self) -> None:
        records = smf.iter_species_move_fix_raw_patches()
        self.assertEqual(len(records), 5 * 7)
        self.assertEqual(len({raw for raw, _ in records}), 35)
        for _, data in records:
            self.assertEqual(data, struct.pack("<II", 0x1440_0059, 0x0000_8821))

    def test_sites_are_the_documented_copy_three_addresses(self) -> None:
        # carrier-attack-theories.md §1 quotes copy 3 (base 0x8015E000).
        documented = {0x0E: 0x8015F734, 0x15: 0x8015F78C, 0x1C: 0x8015F744,
                      0x1D: 0x8015FBD8, 0x2C: 0x8015FC1C}
        for species, offset in smf.MOVE_ROUTINE_PACKAGE_OFFSET.items():
            self.assertEqual(
                smf.ARCHIVE_COPY_BASE[3] + offset + smf.PENDING_WALK_BRANCH_OFFSET,
                documented[species],
                f"0x{species:02X}",
            )
        self.assertEqual(
            smf.ARCHIVE_COPY_BASE[3] + smf.PICKET_MOVE_ROUTINE_PACKAGE_OFFSET
            + smf.PENDING_WALK_BRANCH_OFFSET,
            0x8015FC00,
        )

    def test_switch_off_emits_nothing(self) -> None:
        saved = smf.CARRIER_GROUP_V_FIX
        try:
            smf.CARRIER_GROUP_V_FIX = False
            self.assertEqual(smf.iter_species_move_fix_raw_patches(), ())
        finally:
            smf.CARRIER_GROUP_V_FIX = saved

    def test_append_writes_ppf1_records(self) -> None:
        ppf = bytearray()
        smf.append_species_move_fix_ppf_records(ppf)
        self.assertEqual(len(ppf), 35 * (5 + 8))
        raw, length = struct.unpack_from("<IB", ppf, 0)
        self.assertEqual(length, 8)
        self.assertEqual(raw, smf.iter_group_v_sites()[0][2])


class TestAgainstTheDisc(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ORIGINAL_BIN.exists():
            raise unittest.SkipTest("original disc not present")
        cls.disc = ORIGINAL_BIN.read_bytes()

    def test_every_site_holds_the_group_v_words(self) -> None:
        for species, copy, raw in smf.iter_group_v_sites():
            self.assertEqual(
                self.disc[raw:raw + 8], smf.GROUP_V_ORIGINAL,
                f"0x{species:02X} copy {copy} at raw 0x{raw:X}",
            )

    def test_picket_holds_the_group_p_words_in_every_copy(self) -> None:
        for copy in range(smf.ARCHIVE_COPIES):
            raw = smf.archive_raw_offset(
                0x20, copy, smf.PICKET_MOVE_ROUTINE_PACKAGE_OFFSET + smf.PENDING_WALK_BRANCH_OFFSET
            )
            self.assertEqual(self.disc[raw:raw + 8], smf.GROUP_V_REPLACEMENT, f"copy {copy}")

    def test_move_routines_differ_from_picket_only_at_the_two_words(self) -> None:
        for copy in range(smf.ARCHIVE_COPIES):
            picket = _package_words(
                self.disc, 0x20, copy, smf.PICKET_MOVE_ROUTINE_PACKAGE_OFFSET, MOVE_ROUTINE_WORDS
            )
            for species, offset in smf.MOVE_ROUTINE_PACKAGE_OFFSET.items():
                words = _package_words(self.disc, species, copy, offset, MOVE_ROUTINE_WORDS)
                differing = [
                    index * 4
                    for index, (theirs, ours) in enumerate(zip(words, picket))
                    if theirs != ours
                    # j/jal targets differ by package layout, not by logic
                    and not ((theirs >> 26) in (2, 3) and (ours >> 26) in (2, 3))
                ]
                self.assertEqual(
                    differing,
                    [smf.PENDING_WALK_BRANCH_OFFSET, smf.PENDING_WALK_BRANCH_OFFSET + 4],
                    f"0x{species:02X} copy {copy}",
                )
