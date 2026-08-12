import unittest
from pathlib import Path

from .. import native_bugfixes

ORIGINAL_DISC = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)


class TestNativeBugfixes(unittest.TestCase):
    """The vanilla crash repairs ported from ProGrammar's AD-DeRandomizer.

    The offsets came from another project's constant table, so the load-bearing
    test is that each one still names the instruction the fix expects to be
    replacing. If a disc revision or a transcription error moved any of them,
    the replacement would land on an unrelated instruction in live combat code
    and the failure would surface as a crash nobody could attribute.
    """

    def test_every_site_matches_the_untouched_disc(self) -> None:
        if not ORIGINAL_DISC.is_file():
            self.skipTest("The original disc image is not present.")

        with ORIGINAL_DISC.open("rb") as disc:
            for fix in native_bugfixes.NATIVE_FIXES:
                with self.subTest(fix.name):
                    disc.seek(fix.raw_offset)
                    self.assertEqual(
                        disc.read(len(fix.original)),
                        fix.original,
                        f"{fix.name}: the disc does not hold the instruction this fix "
                        f"replaces at 0x{fix.raw_offset:X} (runtime "
                        f"0x{fix.runtime_address:08X}).",
                    )

    def test_the_repairs_are_in_place_and_inside_user_data(self) -> None:
        # A resize would shift every following instruction; a write into a
        # sector header would corrupt the sector rather than the code.
        for fix in native_bugfixes.NATIVE_FIXES:
            with self.subTest(fix.name):
                self.assertEqual(len(fix.original), len(fix.replacement))
                within = fix.raw_offset % native_bugfixes.RAW_SECTOR_SIZE
                self.assertGreaterEqual(within, 24)
                self.assertLessEqual(within + len(fix.replacement), 24 + 2048)

    def test_the_repairs_actually_change_something(self) -> None:
        for fix in native_bugfixes.NATIVE_FIXES:
            with self.subTest(fix.name):
                self.assertNotEqual(
                    fix.original,
                    fix.replacement,
                    f"{fix.name} writes back what was already there.",
                )

    def test_the_literal_convention_matches_the_derandomizer(self) -> None:
        # ProGrammar's `writeInstruction` writes the literal most-significant
        # byte first, so `0x80002232` must land as `andi v0,s1,0x80`
        # (little-endian word 0x32220080). Getting this backwards would write
        # four plausible-looking but wrong instructions.
        self.assertEqual(
            native_bugfixes._instruction(0x80002232),
            bytes((0x80, 0x00, 0x22, 0x32)),
        )
        self.assertEqual(
            int.from_bytes(native_bugfixes._instruction(0x80002232), "little"),
            0x32220080,
        )

    def test_the_records_emit_as_ppf(self) -> None:
        ppf = bytearray()
        native_bugfixes.append_native_bugfix_ppf_records(ppf)
        emitted = 0
        position = 0
        while position < len(ppf):
            length = ppf[position + 4]
            emitted += 1
            position += 5 + length
        self.assertEqual(position, len(ppf), "A PPF record ran off the end.")
        self.assertEqual(emitted, len(native_bugfixes.NATIVE_FIXES))


if __name__ == "__main__":
    unittest.main()
