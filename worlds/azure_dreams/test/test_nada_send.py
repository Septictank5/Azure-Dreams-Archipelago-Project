"""Nada, the town receive NPC.

Her send machinery was removed on 2026-08-11 (world 0.9.109) - the tests for
the catalog pump, the commit, the callers and the `ADGS` mailbox went with it.
What is left is the shape of her conversation, the erasure of the freed bank
region, and the disc premises that shape rests on.
"""

import struct
import unittest
from pathlib import Path

from .. import nada_send, town_receive, town_shop

ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)


def _encode(text: str) -> bytes:
    return town_shop._encode_shop_name(text, max_characters=None)[:-1]


def _town_file(disc: bytes, file_offset: int, length: int) -> bytes:
    out = bytearray()
    while length:
        within = file_offset % 2048
        take = min(length, 2048 - within)
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA, file_offset
        )
        out += disc[raw:raw + take]
        file_offset += take
        length -= take
    return bytes(out)


class NadaDialogueTests(unittest.TestCase):
    """One question, two answers, three ways out."""

    def test_the_queue_is_armed_before_anything_is_drawn(self) -> None:
        """The snapshot must precede anything the player can react to. It
        is what bounds this conversation's delivery, and what makes a
        client append racing the lock harmless rather than a torn read."""

        script = nada_send.build_nada_script()
        self.assertTrue(script.startswith(nada_send.NADA_SCRIPT_PROLOGUE))
        arm = len(nada_send.NADA_SCRIPT_PROLOGUE)
        self.assertEqual(script[arm], 0x4C)
        self.assertEqual(
            struct.unpack_from("<I", script, arm + 1)[0],
            town_receive.ARM_ADDRESS,
        )

    def test_the_check_runs_before_the_question(self) -> None:
        """A player with nothing waiting is never offered a Yes that would
        do nothing, so the check has to precede the prompt - and the prompt
        is the first thing drawn, so it precedes every glyph too."""

        script = nada_send.build_nada_script()
        check = len(nada_send.NADA_SCRIPT_PROLOGUE) + 5
        self.assertEqual(script[check], 0x4C)
        self.assertEqual(
            struct.unpack_from("<I", script, check + 1)[0],
            town_receive.CHECK_ADDRESS,
        )
        # `0x3E <slot> <addr>` branches when the slot is zero.
        self.assertEqual(script[check + 5:check + 7], bytes((0x3E, 0x0F)))
        nothing = (
            struct.unpack_from("<I", script, check + 7)[0]
            - nada_send.NADA_SCRIPT_RUNTIME_ADDRESS
        )
        self.assertEqual(
            script[nothing:nothing + len(_encode(nada_send.NADA_NO_ITEMS_PAGE))],
            _encode(nada_send.NADA_NO_ITEMS_PAGE),
        )
        # The prompt begins immediately after that branch: nothing renders
        # between entering her script and the answer being known.
        self.assertEqual(
            script.index(_encode(nada_send.NADA_RECEIVE_PROMPT)), check + 11
        )

    def test_the_nothing_page_does_not_clear_a_window_that_is_not_open(
        self,
    ) -> None:
        """`0x08` calls `ClearImage`; doing that before the window exists is
        what crashed v7 of the old passive receive path. That page is reached
        before a single glyph has been drawn, so it must not carry one - and
        the goodbye page, a choice target reached after the menu rendered,
        must."""

        script = nada_send.build_nada_script()
        nothing = script.index(_encode(nada_send.NADA_NO_ITEMS_PAGE))
        self.assertNotEqual(script[nothing - 1], 0x08)
        goodbye = script.index(_encode(nada_send.NADA_GOODBYE_PAGE))
        self.assertEqual(script[goodbye - 1], 0x08)

    def test_the_menu_is_yes_no_and_nothing_else(self) -> None:
        script = nada_send.build_nada_script()
        self.assertEqual(len(script), nada_send.NADA_SCRIPT_CAPACITY)
        # The prose window mode from her original prologue must stay gone -
        # it is what broke the v102 selection grid.
        self.assertNotIn(bytes((0x57, 0x3D)), script)
        header = script.index(bytes((0x2C, 0x02, 0x1A)))
        targets = struct.unpack_from("<2I", script, header + 3)
        self.assertEqual(targets[0], nada_send.RECEIVE_SCRIPT_ADDRESS)
        goodbye = targets[1] - nada_send.NADA_SCRIPT_RUNTIME_ADDRESS
        self.assertEqual(script[goodbye], 0x08)
        expected = (
            bytes((0x0B, 0x81, 0x6D))
            + _encode(nada_send.NADA_YES_ROW)
            + bytes((0x81, 0x6E))
            # `[Yes.]` is 6 cells; the gap pads column 0 to the fixed
            # 16-cell second-column start the native menus use.
            + bytes((0x81, 0x40)) * 10
            + bytes((0x81, 0x6D))
            + _encode(nada_send.NADA_NO_ROW)
            + bytes((0x81, 0x6E))
        )
        self.assertIn(expected, script)

    def test_nothing_offers_a_send(self) -> None:
        """The whole point of the change: there is no send row, no send
        prompt, and no target list anywhere in her bytes."""

        blob = nada_send.build_nada_script() + nada_send.build_machinery_region()
        for word in ("Send", "Sent", "Send items to?"):
            self.assertNotIn(_encode(word), blob, word)
        self.assertFalse(
            [name for name in dir(nada_send) if "COMMIT" in name or "MAILBOX" in name],
            "the send machinery's names are gone with its bytes",
        )

    def test_every_exit_drops_the_receive_lock(self) -> None:
        script = nada_send.build_nada_script()
        for text in (nada_send.NADA_NO_ITEMS_PAGE, nada_send.NADA_GOODBYE_PAGE):
            start = script.index(_encode(text)) + len(_encode(text))
            # Wait for the button, unlock, end.
            self.assertEqual(script[start], 0x11)
            self.assertEqual(script[start + 1], 0x4C)
            self.assertEqual(
                struct.unpack_from("<I", script, start + 2)[0],
                town_receive.UNLOCK_ADDRESS,
            )
            self.assertEqual(script[start + 6], 0x01)
        for text in (nada_send.NADA_RECEIVED_PAGE, nada_send.NADA_NO_ROOM_PAGE):
            page = nada_send._closing_page(text)
            self.assertEqual(page[0], 0x08)
            unlock = page.index(0x4C, 1 + len(_encode(text)))
            self.assertEqual(page[unlock - 1], 0x11)
            self.assertEqual(
                struct.unpack_from("<I", page, unlock + 1)[0],
                town_receive.UNLOCK_ADDRESS,
            )
            self.assertEqual(page[unlock + 5], 0x01)

    def test_the_script_tail_is_end_of_script_padding(self) -> None:
        script = nada_send.build_nada_script()
        end = script.index(_encode(nada_send.NADA_GOODBYE_PAGE))
        end += len(_encode(nada_send.NADA_GOODBYE_PAGE)) + 8
        self.assertEqual(
            set(script[end:]) | {0x01},
            {0x01},
            "the tail must be end-of-script padding, never text",
        )

    def test_yes_delivers_and_reports_both_outcomes(self) -> None:
        script = nada_send.build_receive_script()
        self.assertEqual(script[0], 0x4C)
        self.assertEqual(
            struct.unpack_from("<I", script, 1)[0], town_receive.DELIVER_ADDRESS
        )
        # Delivery returns 0 when everything landed, 1 when storage filled up.
        self.assertEqual(script[5:7], bytes((0x3E, 0x0F)))
        self.assertEqual(
            struct.unpack_from("<I", script, 7)[0],
            nada_send.RECEIVED_PAGE_ADDRESS,
        )
        self.assertEqual(script[11], 0x17)
        self.assertEqual(
            struct.unpack_from("<I", script, 12)[0],
            nada_send.NO_ROOM_PAGE_ADDRESS,
        )
        # It must NOT re-ask: her script already checked, under the arm
        # snapshot, and a second answer could only disagree with the first.
        self.assertNotIn(
            struct.pack("<I", town_receive.CHECK_ADDRESS), script
        )


class NadaMachineryRegionTests(unittest.TestCase):
    def test_region_layout(self) -> None:
        region = nada_send.build_machinery_region()
        base = nada_send.MACHINERY_REGION_OFFSET
        self.assertEqual(
            len(region),
            nada_send.MACHINERY_REGION_END_OFFSET - base,
        )
        for stub in nada_send.GOSSIP_STUB_OFFSETS:
            self.assertEqual(region[stub - base], nada_send.GOSSIP_STUB_BYTE)
        self.assertEqual(
            region[nada_send.GOSSIP_SUBROUTINE_STUB_OFFSET - base],
            nada_send.GOSSIP_SUBROUTINE_STUB_BYTE,
        )
        for offset, end, data in (
            (
                nada_send.RECEIVE_SCRIPT_OFFSET,
                nada_send.RECEIVE_SCRIPT_END_OFFSET,
                nada_send.build_receive_script(),
            ),
            (
                nada_send.RECEIVED_PAGE_OFFSET,
                nada_send.RECEIVED_PAGE_END_OFFSET,
                nada_send._closing_page(nada_send.NADA_RECEIVED_PAGE),
            ),
            (
                nada_send.NO_ROOM_PAGE_OFFSET,
                nada_send.NO_ROOM_PAGE_END_OFFSET,
                nada_send._closing_page(nada_send.NADA_NO_ROOM_PAGE),
            ),
        ):
            start = offset - base
            self.assertEqual(region[start:start + len(data)], data)
            self.assertLessEqual(offset + len(data), end)

    def test_the_freed_tail_is_erased_not_zeroed(self) -> None:
        """A stray script entry into reclaimed space must END a conversation.
        Zeroed scripts crash on entry - that is measured, from Uncle on the
        zeroed disc - so every reclaimed byte is `0x01`."""

        region = nada_send.build_machinery_region()
        tail = 0x600 - nada_send.MACHINERY_REGION_OFFSET
        self.assertEqual(set(region[tail:]), {nada_send.GOSSIP_STUB_BYTE})

    def test_a_page_that_outgrows_its_span_is_a_generation_error(self) -> None:
        original = nada_send.NADA_RECEIVED_PAGE
        try:
            nada_send.NADA_RECEIVED_PAGE = "x" * 400
            with self.assertRaises(ValueError):
                nada_send.build_machinery_region()
        finally:
            nada_send.NADA_RECEIVED_PAGE = original


class NadaDiscPremiseTests(unittest.TestCase):
    def _disc(self) -> bytes:
        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        return ORIGINAL_BIN.read_bytes()

    def test_original_script_and_child_wall_match_the_disc(self) -> None:
        disc = self._disc()
        resource = _town_file(
            disc,
            nada_send.NADA_DIALOGUE_RESOURCE_FILE_OFFSET,
            nada_send.NADA_SCRIPT_END_OFFSET + 0x10,
        )
        self.assertEqual(
            resource[
                nada_send.NADA_SCRIPT_OFFSET:
                nada_send.NADA_SCRIPT_OFFSET
                + len(nada_send.NADA_ORIGINAL_PROLOGUE)
            ],
            nada_send.NADA_ORIGINAL_PROLOGUE,
        )
        self.assertIn(_encode("children"), resource)
        self.assertEqual(
            resource[
                nada_send.NADA_SCRIPT_END_OFFSET:
                nada_send.NADA_SCRIPT_END_OFFSET
                + len(nada_send.NADA_CHILD_SCRIPT_PROLOGUE)
            ],
            nada_send.NADA_CHILD_SCRIPT_PROLOGUE,
        )
        self.assertEqual(resource[nada_send.NADA_SCRIPT_END_OFFSET - 1], 0x01)

    def test_gossip_anchors_match_the_disc(self) -> None:
        """The machinery region's premises, pinned to the disc.

        The three villager entry points must be `0F 2D 03 57 2D` (attach
        variant 0x2D, prose mode) and the shared subroutine must start with
        its measured native-call bytes - if any anchor moves, the state-0
        variant table no longer means what the stubs assume.
        """

        disc = self._disc()
        resource = _town_file(
            disc,
            nada_send.NADA_DIALOGUE_RESOURCE_FILE_OFFSET,
            nada_send.MACHINERY_REGION_END_OFFSET,
        )
        for stub in nada_send.GOSSIP_STUB_OFFSETS:
            self.assertEqual(
                resource[stub:stub + 5],
                bytes((0x0F, 0x2D, 0x03, 0x57, 0x2D)),
                f"villager entry at +0x{stub:03X} is not where the "
                "state-0 variant table says it is",
            )
        self.assertEqual(
            resource[
                nada_send.GOSSIP_SUBROUTINE_STUB_OFFSET:
                nada_send.GOSSIP_SUBROUTINE_STUB_OFFSET + 5
            ],
            bytes((0x4C, 0x44, 0x6A, 0x01, 0x80)),
            "the shared gossip subroutine moved",
        )
        # The three sector-1 callers of that subroutine, byte-pinned.
        sector1 = _town_file(
            disc,
            nada_send.NADA_DIALOGUE_RESOURCE_FILE_OFFSET + 0x800,
            0x800,
        )
        callers = [
            offset
            for offset in range(len(sector1) - 5)
            if sector1[offset] == 0x15
            and struct.unpack_from("<I", sector1, offset + 1)[0]
            == nada_send.NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS
            + nada_send.GOSSIP_SUBROUTINE_STUB_OFFSET
        ]
        self.assertEqual(len(callers), 3, "expected exactly three callers")

    def test_floor_check_signature_matches_the_disc(self) -> None:
        """The three selector floor-10 checks, pinned to the disc.

        Each site must hold the full five-word signature (slti / bne /
        addu a0,s0 / jal 0x80019BC0 / nop) at the mapped file offset.  If
        any word moves, either the outdoor overlay's file mapping drifted
        or the selection functions are not what the decompile said - both
        invalidate the pin, so the build must fail here, not on hardware.
        """

        disc = self._disc()
        signature = struct.pack(
            "<5I", *nada_send.FLOOR_CHECK_ORIGINAL_WORDS
        )
        for address in nada_send.FLOOR_CHECK_ADDRESSES:
            resource = _town_file(
                disc,
                nada_send.outdoor_overlay_runtime_to_file_offset(address),
                len(signature),
            )
            self.assertEqual(
                resource,
                signature,
                f"floor-10 check at 0x{address:08x} does not match the disc",
            )


class NadaPatchRecordTests(unittest.TestCase):
    def test_ppf_records_cover_script_and_machinery(self) -> None:
        ppf = bytearray()
        nada_send.append_nada_ppf_records(ppf)
        offset = 0
        total = 0
        spans = []
        while offset < len(ppf):
            raw, length = struct.unpack_from("<IB", ppf, offset)
            spans.append((raw, length))
            offset += 5 + length
            total += length
        self.assertEqual(
            total,
            nada_send.NADA_SCRIPT_CAPACITY
            + nada_send.MACHINERY_REGION_END_OFFSET
            - nada_send.MACHINERY_REGION_OFFSET
            + 4 * len(nada_send.FLOOR_CHECK_ADDRESSES),
        )
        first = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            nada_send.NADA_DIALOGUE_RESOURCE_FILE_OFFSET
            + nada_send.NADA_SCRIPT_OFFSET,
        )
        self.assertEqual(spans[0][0], first)

    def test_selector_pin_records_target_all_three_checks(self) -> None:
        ppf = bytearray()
        nada_send.append_nada_ppf_records(ppf)
        records: dict[int, bytes] = {}
        offset = 0
        while offset < len(ppf):
            raw, length = struct.unpack_from("<IB", ppf, offset)
            records[raw] = bytes(ppf[offset + 5:offset + 5 + length])
            offset += 5 + length
        replacement = struct.pack("<I", nada_send.FLOOR_CHECK_REPLACEMENT_WORD)
        for address in nada_send.FLOOR_CHECK_ADDRESSES:
            raw = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA,
                nada_send.outdoor_overlay_runtime_to_file_offset(address),
            )
            self.assertEqual(
                records.get(raw),
                replacement,
                f"floor check at 0x{address:08x} is not pinned",
            )

    def test_selector_pin_replacement_is_li_v0_1(self) -> None:
        word = nada_send.FLOOR_CHECK_REPLACEMENT_WORD
        self.assertEqual(word >> 26, 0x09)  # addiu
        self.assertEqual((word >> 21) & 31, 0)  # rs = zero
        self.assertEqual((word >> 16) & 31, 2)  # rt = v0
        self.assertEqual(word & 0xFFFF, 1)  # "floor < 10" always true
