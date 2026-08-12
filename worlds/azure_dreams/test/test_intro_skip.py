import struct
import unittest
from pathlib import Path

from .. import intro_skip, patch, town_receive, town_shop, town_warp


def _ppf_records(ppf: bytes | bytearray) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    cursor = patch.PPF_HEADER_SIZE
    while cursor < len(ppf):
        raw_offset, length = struct.unpack_from("<IB", ppf, cursor)
        cursor += 5
        records.append((raw_offset, bytes(ppf[cursor : cursor + length])))
        cursor += length
    return records


class TestAzureDreamsIntroSkip(unittest.TestCase):
    def test_preserves_trusted_intro_skip_and_adds_restore_handshake(self) -> None:
        file_patches = dict(intro_skip.iter_intro_skip_file_patches())
        self.assertEqual(file_patches[intro_skip.ANGEL_SCENE_FILE_OFFSET], b"\x05")
        self.assertNotIn(intro_skip.ANGEL_DIALOGUE_FILE_OFFSET, file_patches)
        self.assertEqual(
            file_patches[intro_skip.HOUSE_PITA_FNO_POINTER_FILE_OFFSET],
            struct.pack("<I", town_receive.INTRO_CAPTURE_WRAPPER_ADDRESS),
        )

        returning = intro_skip.build_returning_angel_script()
        self.assertTrue(returning.startswith(intro_skip.ANGEL_ROUTER_ORIGINAL_PREFIX))
        # The player name, the greeting's exclamation, then the acknowledgement.
        self.assertIn(bytes.fromhex("FE 00 81 49 11"), returning)
        self.assertNotIn(bytes.fromhex("2E A7"), returning)
        self.assertIn(
            town_shop._encode_shop_name("Welcome back, ", max_characters=None)[:-1],
            returning,
        )

        # Setting script slot 0 is only the argument of a following call, so
        # the acknowledgement must be followed by vanilla's closing sequence
        # and the call that actually starts the scene transition. Ending the
        # script without it strands the player on the angel screen.
        acknowledgement = returning.index(bytes.fromhex("FE 00 81 49 11")) + 5
        self.assertEqual(
            returning[acknowledgement:],
            intro_skip.ANGEL_CLOSING_POSE
            + bytes.fromhex("34 00 1E 00 00 00")
            + intro_skip.SCRIPT_YIELD_FRAME
            + bytes.fromhex("34 01 01 00 00 00")
            + bytes.fromhex("3C 00 01")
            + b"\x42\x00"
            + struct.pack(
                "<I",
                intro_skip.ANGEL_DIALOGUE_RUNTIME_ADDRESS + acknowledgement + 9,
            )
            + intro_skip.SCRIPT_CLEAR_TEXT
            + b"\x15"
            + struct.pack("<I", intro_skip.ANGEL_SCENE_CALL_RUNTIME_ADDRESS)
            + intro_skip.SCRIPT_END,
        )
        self.assertEqual(intro_skip.ANGEL_SCENE_CALL_RUNTIME_ADDRESS, 0x8001_76FA)
        self.assertEqual(intro_skip.ANGEL_DIALOGUE_RUNTIME_ADDRESS, 0x8001_770A)
        # The staged script must never reach the original closing sequence it
        # replays, which still lives at 0x80017AFB.
        self.assertLess(
            intro_skip.ANGEL_DIALOGUE_RUNTIME_ADDRESS + len(returning),
            0x8001_7AFB,
        )

        wake = file_patches[intro_skip.WAKE_UP_SCRIPT_FILE_OFFSET]
        self.assertEqual(len(wake), intro_skip.WAKE_UP_TRUSTED_PATCH_SIZE)
        self.assertEqual(
            wake,
            bytes.fromhex(
                "0C 14 00 0C 16 00 2E 79 3E 00 1E 22 02 80"
            ),
        )
        self.assertNotIn(0x41_53F0, file_patches)

        raw_patches = intro_skip.iter_intro_skip_raw_patches()
        self.assertEqual(len(raw_patches), len(file_patches))
        for (file_offset, data), (raw_offset, raw_data) in zip(
            intro_skip.iter_intro_skip_file_patches(),
            raw_patches,
        ):
            self.assertEqual(
                raw_offset,
                town_shop.mode2_file_offset_to_raw_offset(
                    town_shop.TOWN_FILE_START_LBA,
                    file_offset,
                ),
            )
            self.assertEqual(raw_data, data)

    def test_complete_player_patch_has_no_intro_skip_overlap(self) -> None:
        base_patch = (
            Path(__file__).parents[1] / "data" / "azure_dreams_base.ppf"
        ).read_bytes()
        player_patch = bytearray(
            patch.build_player_ppf(
                base_patch,
                bytes(patch.SEED_BLOCK_SIZE),
                "ADAP intro-skip audit",
            )
        )
        town_receive.append_town_receive_ppf_records(
            player_patch,
            bytes(town_shop.SHOP_CORE_SIZE),
        )
        town_shop.append_town_shop_hook_ppf_records(player_patch)
        town_warp.append_town_warp_ppf_records(player_patch)
        for lba in town_shop.SHOP_TEXT_SECTOR_LBAS[
            : town_shop.IMPLEMENTED_SHOP_COUNT
        ]:
            patch.append_mode2_form1_sector_ppf_records(
                player_patch,
                lba,
                bytes(patch.FORM1_USER_SIZE),
            )

        intro_skip.append_intro_skip_ppf_records(player_patch)
        records = sorted(
            (
                raw_offset,
                raw_offset + len(data),
                data,
            )
            for raw_offset, data in _ppf_records(player_patch)
        )
        for previous, current in zip(records, records[1:]):
            self.assertGreaterEqual(current[0], previous[1])

        record_by_offset = {
            raw_offset: data
            for raw_offset, _, data in records
        }
        for raw_offset, data in intro_skip.iter_intro_skip_raw_patches():
            self.assertEqual(record_by_offset[raw_offset], data)

    def test_overlap_guard_rejects_a_future_conflict(self) -> None:
        player_patch = bytearray(b"PPF10\0" + bytes(50))
        player_patch.extend(
            struct.pack("<IB", 0xB9_46DC, 4)
            + bytes.fromhex("DE AD BE EF")
        )

        with self.assertRaisesRegex(
            ValueError,
            "Intro-skip patch range .* overlaps existing PPF range",
        ):
            intro_skip.append_intro_skip_ppf_records(player_patch)


if __name__ == "__main__":
    unittest.main()
