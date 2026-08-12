import struct
import unittest

from .. import town_receive, town_shop
from . import mips_sim


class TestTownReceivePatch(unittest.TestCase):
    def test_payload_contains_mailbox_and_fits_dispatcher(self) -> None:
        payload = town_receive.build_town_receive_payload()
        self.assertEqual(len(payload), town_shop.SHOP_CORE_SIZE)
        self.assertEqual(
            struct.unpack_from("<I", payload, town_receive.MAILBOX_OFFSET)[0],
            town_receive.MAILBOX_MAGIC,
        )
        self.assertEqual(
            struct.unpack_from("<H", payload, town_receive.MAILBOX_OFFSET + 4)[0],
            town_receive.MAILBOX_VERSION,
        )
        self.assertEqual(
            struct.unpack_from("<H", payload, town_receive.MAILBOX_OFFSET + 6)[0],
            town_receive.MAILBOX_SIZE,
        )
        self.assertEqual(
            payload[
                town_receive.MAILBOX_OFFSET
                + town_receive.INTRO_RESTORE_PROTOCOL_OFFSET
            ],
            town_receive.INTRO_RESTORE_PROTOCOL_VERSION,
        )
        intro_helper = town_receive._build_intro_restore_helper()
        intro_probe = town_receive._build_intro_restore_probe()
        intro_capture = town_receive._build_intro_capture_wrapper()
        self.assertEqual(
            payload[
                town_receive.INTRO_RESTORE_HELPER_OFFSET :
                town_receive.INTRO_RESTORE_HELPER_OFFSET + len(intro_helper)
            ],
            intro_helper,
        )
        self.assertEqual(
            payload[
                town_receive.INTRO_RESTORE_PROBE_OFFSET :
                town_receive.INTRO_RESTORE_PROBE_OFFSET + len(intro_probe)
            ],
            intro_probe,
        )
        self.assertEqual(
            payload[
                town_receive.INTRO_CAPTURE_WRAPPER_OFFSET :
                town_receive.INTRO_CAPTURE_WRAPPER_OFFSET + len(intro_capture)
            ],
            intro_capture,
        )
        self.assertEqual(len(intro_helper), 0x18)
        self.assertEqual(len(intro_probe), 0x10)
        self.assertEqual(
            len(intro_capture),
            town_shop.INTRO_CAPTURE_WRAPPER_SIZE,
        )

    def test_receive_payload_embeds_after_maximum_shop_name_data(self) -> None:
        slots = [
            town_shop.ShopSlot(
                bytes((2, 15, 0, 0)),
                500,
                f"{index:02d}" + "W" * 40,
            )
            for index in range(town_shop.SHOP_SLOT_COUNT)
        ]
        shop_payload = town_shop.build_town_shop_payload(slots)
        payload = town_receive.build_town_receive_payload(shop_payload)
        self.assertEqual(
            payload[town_receive.MAILBOX_OFFSET : town_receive.MAILBOX_OFFSET + 4],
            b"ADTR",
        )

    def test_intro_capture_wrapper_uses_one_shot_restored_pita_guard(self) -> None:
        wrapper = town_receive._build_intro_capture_wrapper()
        words = struct.unpack(f"<{len(wrapper) // 4}I", wrapper)
        self.assertEqual(len(wrapper), town_shop.INTRO_CAPTURE_WRAPPER_SIZE)
        self.assertEqual(
            words[5],
            town_shop._i(0x04, 2, 0, 4),
        )
        self.assertEqual(
            words[10],
            town_shop._j(
                0x03,
                town_receive.ORIGINAL_HOUSE_PITA_FNO_ADDRESS,
            ),
        )
        self.assertEqual(
            words[7],
            town_shop._i(
                0x28,
                3,
                0,
                town_shop._lower(town_receive.INTRO_RESTORE_MARKER_ADDRESS),
            ),
        )
        self.assertEqual(
            words[14],
            town_shop._i(
                0x28,
                3,
                8,
                town_shop._lower(
                    town_receive.INTRO_FIRST_RUN_READY_ADDRESS
                ),
            ),
        )
        self.assertNotIn(
            town_shop._i(0x04, 2, 9, -3),
            words,
        )

    def test_safe_capacity_and_resident_hook_patches_are_present(self) -> None:
        payload = town_receive.build_town_receive_payload()
        patches = town_receive.iter_town_receive_raw_patches(payload)
        self.assertTrue(any(data == bytes((60, 60, 60)) for _, data in patches))
        expected_hook = struct.pack(
            "<I", town_shop._j(0x03, town_receive.RESIDENT_WRAPPER_ADDRESS)
        )
        self.assertTrue(any(data == expected_hook for _, data in patches))
        for raw_offset, data in patches:
            self.assertLessEqual((raw_offset - 24) % 2_352 + len(data), 2_048)

    def test_resident_wrapper_stays_before_runtime_globals(self) -> None:
        wrapper = town_receive._build_resident_wrapper()
        self.assertLessEqual(len(wrapper), 0x58)

    def test_frame_hook_only_runs_the_seed_state_initializer(self) -> None:
        """The hook survives the dispatcher's retirement solely to keep
        calling the durable seed-state initializer once a frame. Nothing
        else may run per frame - a per-frame call that can open a window is
        the entire Nada / Monster hut fault."""

        wrapper = town_receive._build_resident_wrapper()
        words = struct.unpack(f"<{len(wrapper) // 4}I", wrapper)
        calls = [
            word
            for word in words
            if word >> 26 == 0x03 or (word >> 26 == 0x02 and word != 0)
        ]
        self.assertEqual(
            calls,
            [
                town_shop._j(0x03, town_receive.VANILLA_TOWN_FRAME_SERVICE_ADDRESS),
                town_shop._j(0x03, town_shop.STATE_INITIALIZER_ADDRESS),
            ],
        )

    def test_retired_receive_machinery_is_erased_not_merely_unused(self) -> None:
        """A retired span that still holds plausible code is how a later
        reader concludes a dead feature is live."""

        payload = town_receive.build_town_receive_payload()
        for start, end, name in town_receive.FREE_SLAB_SPANS:
            self.assertEqual(
                payload[start:end],
                bytes(end - start),
                f"{name} was left non-zero at 0x{start:x}",
            )
        self.assertFalse(hasattr(town_receive, "_build_dispatcher"))
        self.assertFalse(hasattr(town_receive, "_build_notification_helper"))


class TestTownReceiveQueue(unittest.TestCase):
    """The receive queue Nada drains from inside her own conversation.

    Every test here runs the real generated MIPS in the R3000 simulator,
    which models the load delay - the hazard that has produced more bugs in
    this project's generated code than any other.
    """

    ORDER_TABLE = town_receive.INVENTORY_POINTERS_ADDRESS
    # Somewhere the free-descriptor allocator can hand out slots from.
    DESCRIPTOR_POOL = 0x8001_0248

    def _memory(self) -> mips_sim.Memory:
        memory = mips_sim.Memory()
        for address, builder in (
            (town_receive.ARM_ADDRESS, town_receive._build_queue_arm),
            (town_receive.CHECK_ADDRESS, town_receive._build_queue_check),
            (town_receive.UNLOCK_ADDRESS, town_receive._build_queue_unlock),
            (town_receive.DELIVER_ADDRESS, town_receive._build_queue_deliver),
        ):
            memory.load_bytes(address, builder())
        memory.write32(town_receive.QUEUE_ADDRESS, town_receive.QUEUE_MAGIC)
        return memory

    def _byte(self, memory: mips_sim.Memory, offset: int) -> int:
        return memory.read8(town_receive.QUEUE_ADDRESS + offset)

    def _set_byte(self, memory: mips_sim.Memory, offset: int, value: int) -> None:
        memory.write8(town_receive.QUEUE_ADDRESS + offset, value)

    def _append(
        self,
        memory: mips_sim.Memory,
        count: int,
        descriptor: bytes,
        token: int,
    ) -> int:
        """Exactly what the client does: write the entry at `count & 15`,
        then publish `count` last."""

        entry = (
            town_receive.QUEUE_ADDRESS
            + town_receive.QUEUE_ENTRIES_OFFSET
            + (count % town_receive.QUEUE_SLOTS) * town_receive.QUEUE_ENTRY_SIZE
        )
        memory.write32(entry, int.from_bytes(descriptor, "little"))
        memory.write32(entry + town_receive.QUEUE_ENTRY_TOKEN_OFFSET, token & 0xFFFF_FFFF)
        count = (count + 1) & 0xFF
        self._set_byte(memory, town_receive.QUEUE_COUNT_OFFSET, count)
        return count

    def _deliver(self, memory: mips_sim.Memory) -> int:
        """Runs the delivery routine with the native free-descriptor
        allocator stubbed by a bump allocator over a private pool."""

        handed_out: list[int] = []

        def allocate(cpu: mips_sim.Cpu) -> None:
            address = self.DESCRIPTOR_POOL + 4 * len(handed_out)
            handed_out.append(address)
            cpu.registers[2] = address

        cpu = mips_sim.Cpu(
            memory,
            stubs={town_receive.FIND_UNUSED_DESCRIPTOR_ADDRESS: allocate},
        )
        return cpu.run(town_receive.DELIVER_ADDRESS)

    def _empty_order_table(self, memory: mips_sim.Memory) -> None:
        for index in range(21):
            memory.write32(self.ORDER_TABLE + 4 * index, 0)

    def _fill_safe(self, memory: mips_sim.Memory, used: int) -> None:
        for index in range(town_receive.SAFE_CAPACITY):
            # Byte +1 is the category; nonzero means the slot is occupied.
            memory.write32(
                town_receive.SAFE_DESCRIPTORS_ADDRESS + 4 * index,
                0x0000_0F02 if index < used else 0,
            )

    def _run(self, memory: mips_sim.Memory, address: int) -> int:
        return mips_sim.Cpu(memory).run(address)

    # --- arm ---------------------------------------------------------------

    def test_arm_takes_the_lock_and_snapshots_the_count(self) -> None:
        memory = self._memory()
        self._set_byte(memory, town_receive.QUEUE_COUNT_OFFSET, 5)
        self._set_byte(memory, town_receive.QUEUE_RESULT_OFFSET, 2)
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_LOCK_OFFSET), 1)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_LIMIT_OFFSET), 5)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_RESULT_OFFSET), 0)

    def test_an_append_racing_the_lock_is_left_for_the_next_conversation(self) -> None:
        """The safety property the whole design rests on: because `arm`
        snapshots `count`, an append that lands after it is out of this
        conversation's bounded range. It is not lost - `count` still carries
        it - it is simply not delivered now."""

        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        count = self._append(memory, 0, bytes((1, 1, 0, 0)), 1)
        self._run(memory, town_receive.ARM_ADDRESS)
        # The client, mid-poll, appends a second item after the snapshot.
        count = self._append(memory, count, bytes((2, 15, 0, 0)), 2)

        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 1)
        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 1)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_COUNT_OFFSET), 2)
        # Exactly one item was delivered, and the racer is still pending.

        # The next conversation picks it up with no special handling.
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 1)
        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 2)

    # --- check -------------------------------------------------------------

    def test_check_reports_pending_against_the_snapshot(self) -> None:
        memory = self._memory()
        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 0)

        self._append(memory, 0, bytes((1, 1, 0, 0)), 1)
        # Still zero: nothing is pending until a conversation arms.
        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 0)
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 1)

    def test_check_survives_byte_cursor_wraparound(self) -> None:
        """Sixteen slots divide 256, which is what lets head and count
        free-run as bytes with no reset step - and a reset is a second
        writer on a byte, the exact race being removed."""

        memory = self._memory()
        self._set_byte(memory, town_receive.QUEUE_HEAD_OFFSET, 0xFE)
        self._set_byte(memory, town_receive.QUEUE_COUNT_OFFSET, 0x02)
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_LIMIT_OFFSET), 0x02)
        # (0x02 - 0xFE) & 0xFF == 4 pending, not a huge negative.
        self.assertEqual(self._run(memory, town_receive.CHECK_ADDRESS), 1)

    # --- unlock ------------------------------------------------------------

    def test_unlock_releases_the_client(self) -> None:
        memory = self._memory()
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_LOCK_OFFSET), 1)
        self._run(memory, town_receive.UNLOCK_ADDRESS)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_LOCK_OFFSET), 0)

    # --- deliver -----------------------------------------------------------

    def test_delivery_fills_inventory_then_reports_success(self) -> None:
        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        count = 0
        for descriptor, token in (
            (bytes((1, 1, 0, 0)), 1),
            (bytes((2, 15, 10, 0)), 2),
        ):
            count = self._append(memory, count, descriptor, token)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(
            self._byte(memory, town_receive.QUEUE_RESULT_OFFSET),
            town_receive.QUEUE_RESULT_DELIVERED,
        )
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 2)
        first = memory.read32(self.ORDER_TABLE)
        second = memory.read32(self.ORDER_TABLE + 4)
        self.assertNotEqual(first, 0)
        self.assertNotEqual(second, 0)
        self.assertEqual(memory.read32(first), 0x0000_0101)
        # id 2, category 15, quality +10 - the descriptor is stored exactly,
        # quality byte and all, never rebuilt from the base item id.
        self.assertEqual(memory.read32(second), 0x000A_0F02)

    def test_an_inventory_append_writes_a_fresh_zero_terminator(self) -> None:
        """V22's crash: shop compaction leaves stale pointers past the
        terminator, so overwriting only the old zero exposes that tail to
        the Items walk."""

        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        # A stale tail exactly as a shop self-return leaves it.
        memory.write32(self.ORDER_TABLE + 4, 0xDEAD_BEEF)
        memory.write32(self.ORDER_TABLE + 8, 0xFFFF_FFFF)
        self._append(memory, 0, bytes((1, 1, 0, 0)), 1)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(memory.read32(self.ORDER_TABLE + 4), 0)

    def test_a_compacting_order_table_diverts_to_the_safe(self) -> None:
        """The shop's -1 deletion marker means the table is mid-compaction.
        The old dispatcher deferred a frame; a script call cannot, so the
        item goes to the safe rather than walking a table mid-edit."""

        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        memory.write32(self.ORDER_TABLE, 0xFFFF_FFFF)
        self._append(memory, 0, bytes((1, 1, 0, 0)), 1)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(
            memory.read32(town_receive.SAFE_DESCRIPTORS_ADDRESS), 0x0000_0101
        )
        # The order table was not touched.
        self.assertEqual(memory.read32(self.ORDER_TABLE), 0xFFFF_FFFF)

    def test_a_full_inventory_falls_back_to_the_safe(self) -> None:
        memory = self._memory()
        for index in range(20):
            memory.write32(self.ORDER_TABLE + 4 * index, 0x8001_0000 + 4 * index)
        memory.write32(self.ORDER_TABLE + 80, 0)
        self._fill_safe(memory, 3)
        self._append(memory, 0, bytes((1, 1, 0, 0)), 1)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(
            memory.read32(town_receive.SAFE_DESCRIPTORS_ADDRESS + 12), 0x0000_0101
        )

    def test_full_storage_stops_at_the_undelivered_entry(self) -> None:
        """head advances only after an entry is safely stored, so an
        interrupted pass re-offers exactly what it failed to deliver - and
        the durable cursor sits at the last item that really landed."""

        memory = self._memory()
        for index in range(20):
            memory.write32(self.ORDER_TABLE + 4 * index, 0x8001_0000 + 4 * index)
        memory.write32(self.ORDER_TABLE + 80, 0)
        self._fill_safe(memory, town_receive.SAFE_CAPACITY - 1)
        count = 0
        for descriptor, token in (
            (bytes((1, 1, 0, 0)), 1),
            (bytes((2, 15, 0, 0)), 2),
        ):
            count = self._append(memory, count, descriptor, token)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 1)
        self.assertEqual(
            self._byte(memory, town_receive.QUEUE_RESULT_OFFSET),
            town_receive.QUEUE_RESULT_NO_ROOM,
        )
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 1)

    def test_delivery_never_touches_the_durable_receive_cursor(self) -> None:
        """The client owns that number as of 2026-08-02.

        It used to be written here AND by the tower dispatcher - two
        game-side writers of one client-facing value, which is how town and
        tower came to disagree about how far delivery had got. No game code
        ever read it. The game now reports only `head`, and this test is the
        thing that keeps it that way: it holds for EVERY token, not just
        gifts, so the old sign-bit guard is not merely bypassed but
        unnecessary."""

        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        memory.write32(town_receive.PERSISTENT_RECEIVED_COUNT_ADDRESS, 7)
        count = self._append(memory, 0, bytes((1, 1, 0, 0)), 0x8000_0001)
        count = self._append(memory, count, bytes((2, 15, 0, 0)), 8)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        # Both landed, and the cursor is exactly as it was.
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 2)
        self.assertEqual(
            memory.read32(town_receive.PERSISTENT_RECEIVED_COUNT_ADDRESS), 7,
            "the delivery routine wrote the durable cursor; the client owns it",
        )

    def test_an_invalid_descriptor_is_consumed_rather_than_wedging_the_queue(self) -> None:
        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        count = 0
        for descriptor, token in (
            (bytes((0, 1, 0, 0)), 1),   # zero item id
            (bytes((1, 0, 0, 0)), 2),   # zero category - the game's own free marker
            (bytes((1, 1, 0, 0)), 3),
        ):
            count = self._append(memory, count, descriptor, token)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 3)
        # Only the valid descriptor reached storage.
        self.assertEqual(memory.read32(self.ORDER_TABLE + 4), 0)
        self.assertEqual(
            memory.read32(memory.read32(self.ORDER_TABLE)), 0x0000_0101
        )

    def test_a_full_sixteen_slot_pass_delivers_every_entry(self) -> None:
        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        count = 0
        for index in range(town_receive.QUEUE_SLOTS):
            count = self._append(memory, count, bytes((1, 1, 0, 0)), index + 1)
        self._run(memory, town_receive.ARM_ADDRESS)

        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(
            self._byte(memory, town_receive.QUEUE_HEAD_OFFSET),
            town_receive.QUEUE_SLOTS,
        )
        # Sixteen fits inside the twenty ordered inventory slots, so a full
        # pass never needs the safe at all.
        self.assertEqual(
            memory.read32(town_receive.SAFE_DESCRIPTORS_ADDRESS), 0
        )
        self.assertEqual(memory.read32(self.ORDER_TABLE + 4 * 16), 0)

    def test_delivering_an_empty_queue_is_a_no_op(self) -> None:
        memory = self._memory()
        self._empty_order_table(memory)
        self._fill_safe(memory, 0)
        self._run(memory, town_receive.ARM_ADDRESS)
        self.assertEqual(self._deliver(memory), 0)
        self.assertEqual(self._byte(memory, town_receive.QUEUE_HEAD_OFFSET), 0)
        self.assertEqual(memory.read32(self.ORDER_TABLE), 0)


if __name__ == "__main__":
    unittest.main()
