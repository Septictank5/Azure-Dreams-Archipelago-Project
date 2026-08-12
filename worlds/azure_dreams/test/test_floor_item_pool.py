import struct
import unittest
from pathlib import Path

from .. import floor_item_pool, save_removal, town_shop

ORIGINAL_DISC = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)

# Vanilla rarity-class weights, for the totals cross-check below.
_VANILLA_WEIGHTS = (128, 85, 32, 1)

_CATEGORY_TABLE = 0x80073414
_RESIDENT_DATA_END = 0x80081438


def _read_resident(disc, address: int, length: int) -> bytes:
    """Read resident SLUS_006.14 bytes off the raw disc image."""

    data = bytearray()
    file_offset = (
        save_removal.SLUS_HEADER_SIZE + address - save_removal.SLUS_LOAD_ADDRESS
    )
    while len(data) < length:
        current = file_offset + len(data)
        within = current % 2_048
        chunk = min(length - len(data), 2_048 - within)
        disc.seek(
            town_shop.mode2_file_offset_to_raw_offset(
                save_removal.SLUS_FILE_START_LBA, current
            )
        )
        data.extend(disc.read(chunk))
    return bytes(data)


class TestFloorItemPool(unittest.TestCase):
    """The spawn-pool rebalance: every write site must still hold the vanilla
    value it claims to replace, the builder/picker weight immediates must agree,
    and the resulting pool total must be the number the module's docstring and
    the design discussion settled on."""

    def test_flag_sites_match_the_untouched_disc(self) -> None:
        if not ORIGINAL_DISC.is_file():
            self.skipTest("The original disc image is not present.")
        with ORIGINAL_DISC.open("rb") as disc:
            for edit in floor_item_pool.SPAWN_FLAG_EDITS:
                with self.subTest(edit.name):
                    have = struct.unpack(
                        "<H", _read_resident(disc, edit.address, 2)
                    )[0]
                    self.assertEqual(
                        have,
                        edit.original,
                        f"{edit.name}: disc holds 0x{have:04X} at "
                        f"0x{edit.address:08X}, not the recorded original "
                        f"0x{edit.original:04X}.",
                    )

    def test_weight_sites_match_both_package_copies(self) -> None:
        if not ORIGINAL_DISC.is_file():
            self.skipTest("The original disc image is not present.")
        with ORIGINAL_DISC.open("rb") as disc:
            for copy_offset in floor_item_pool.FLOOR_GENERATION_FILE_OFFSETS:
                for edit in floor_item_pool.WEIGHT_CODE_EDITS:
                    with self.subTest(f"{edit.name} @ copy +0x{copy_offset:X}"):
                        file_offset = copy_offset + edit.address - 0x8000_0000
                        disc.seek(
                            town_shop.mode2_file_offset_to_raw_offset(
                                save_removal.DUNGEON_FILE_START_LBA, file_offset
                            )
                        )
                        have = struct.unpack("<I", disc.read(4))[0]
                        self.assertEqual(
                            have,
                            edit.original,
                            f"{edit.name}: copy at +0x{copy_offset:X} holds "
                            f"0x{have:08X}, not 0x{edit.original:08X}.",
                        )

    def test_builder_and_picker_weights_agree(self) -> None:
        # The cumulative table and the per-item walk re-derive the same
        # weights; a mismatched immediate desyncs the two-level roll and the
        # picker returns the wrong item (or nothing) for some rolls.
        by_class: dict[str, list[int]] = {}
        for edit in floor_item_pool.WEIGHT_CODE_EDITS:
            which = edit.name.split()[-2]  # "class-0" / "class-3"
            by_class.setdefault(which, []).append(edit.replacement & 0xFFFF)
        for which, immediates in by_class.items():
            with self.subTest(which):
                self.assertEqual(len(immediates), 2)
                self.assertEqual(immediates[0], immediates[1])
        self.assertEqual(
            sorted(imm for pair in by_class.values() for imm in set(pair)),
            sorted(
                {floor_item_pool.CLASS_WEIGHTS[0], floor_item_pool.CLASS_WEIGHTS[3]}
            ),
        )

    def test_replacements_are_addiu_immediates(self) -> None:
        # Both class-3 sites replace a delay-slot `addu` with `addiu rX,zero`;
        # both class-0 sites replace an existing `addiu rX,zero`. Either way
        # the replacement must be `addiu` with rs == zero, or the weight would
        # depend on live register state.
        for edit in floor_item_pool.WEIGHT_CODE_EDITS:
            with self.subTest(edit.name):
                self.assertEqual(edit.replacement >> 26, 0x09)  # addiu
                self.assertEqual((edit.replacement >> 21) & 31, 0)  # rs = zero
                # The destination register must match what the vanilla word
                # fed the weight through (a0 in the builder, a1 in the picker).
                vanilla_rt = (
                    (edit.original >> 16) & 31
                    if edit.original >> 26 == 0x09
                    else (edit.original >> 11) & 31  # addu writes rd
                )
                self.assertEqual((edit.replacement >> 16) & 31, vanilla_rt)

    def test_the_new_pool_total_is_5904(self) -> None:
        # The number the rebalance was designed around: normal-floor pool
        # total 5904 with weights 108/85/32/6, making each class-3 item a
        # 1-in-984 per rolled item. Recomputed from the disc's own definition
        # tables with the flag edits applied.
        if not ORIGINAL_DISC.is_file():
            self.skipTest("The original disc image is not present.")
        edits = {
            edit.address: edit.replacement
            for edit in floor_item_pool.SPAWN_FLAG_EDITS
        }
        vanilla_total = 0
        new_total = 0
        with ORIGINAL_DISC.open("rb") as disc:
            for category in range(1, 0x13):
                record = _read_resident(
                    disc, _CATEGORY_TABLE + category * 0x14, 0x14
                )
                count = record[2]
                table = struct.unpack_from("<I", record, 0xC)[0]
                if not save_removal.SLUS_LOAD_ADDRESS <= table < _RESIDENT_DATA_END:
                    continue  # the egg table lives in an overlay; all excluded
                for item_id in range(1, count):
                    address = table + item_id * 0x14
                    flags = struct.unpack(
                        "<H", _read_resident(disc, address, 2)
                    )[0]
                    if not flags & floor_item_pool.FLAG_NEVER_SPAWN and not (
                        flags & floor_item_pool.FLAG_MODE2_ONLY
                    ):
                        vanilla_total += _VANILLA_WEIGHTS[(flags >> 12) & 3]
                    flags = edits.get(address, flags)
                    if not flags & floor_item_pool.FLAG_NEVER_SPAWN and not (
                        flags & floor_item_pool.FLAG_MODE2_ONLY
                    ):
                        new_total += floor_item_pool.CLASS_WEIGHTS[(flags >> 12) & 3]
        self.assertEqual(vanilla_total, 5_746)
        self.assertEqual(new_total, 5_904)

    def test_the_records_emit_as_ppf_without_overlap(self) -> None:
        ppf = bytearray()
        floor_item_pool.append_floor_item_pool_ppf_records(ppf)
        spans = []
        position = 0
        while position < len(ppf):
            offset, length = struct.unpack_from("<IB", ppf, position)
            spans.append((offset, offset + length))
            within = offset % 2_352
            self.assertGreaterEqual(within, 24)
            self.assertLessEqual(within + length, 24 + 2_048)
            position += 5 + length
        self.assertEqual(position, len(ppf), "A PPF record ran off the end.")
        # 14 flag halfwords + 4 instruction words in each of 2 copies.
        self.assertEqual(len(spans), 22)
        spans.sort()
        for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(first_end, second_start, "Records overlap.")


if __name__ == "__main__":
    unittest.main()
