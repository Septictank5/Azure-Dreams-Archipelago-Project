import struct
import unittest
from pathlib import Path

from . import mips_sim
from .. import town_shop

ORIGINAL_BIN = (
    Path(__file__).parents[4] / "Azure Dreams (Original)" / "Azure Dreams (USA).bin"
)


class TestTownShopPatch(unittest.TestCase):
    def test_payload_fits_exact_town_wide_region(self) -> None:
        slots: list[town_shop.ShopSlot | None] = [
            town_shop.ShopSlot(bytes((2, 15, 0, 0)), 500, "Progressive Sword")
            for _ in range(town_shop.SHOP_SLOT_COUNT)
        ]
        payload = town_shop.build_town_shop_payload(slots)
        self.assertEqual(len(payload), town_shop.SHOP_CORE_SIZE)
        self.assertEqual(payload[:8], town_shop.SHOP_CORE_MAGIC)
        self.assertEqual(
            town_shop.SHOP_CORE_ADDRESS + len(payload) - 1,
            0x800F_C417,
        )

    def test_generated_signature_and_game_owned_state_initializer_fit(self) -> None:
        signature = b"SHOPTEST"
        payload = town_shop.build_town_shop_payload(
            [None] * town_shop.SHOP_SLOT_COUNT,
            signature,
        )
        self.assertEqual(
            payload[
                town_shop.SEED_SIGNATURE_OFFSET :
                town_shop.SEED_SIGNATURE_OFFSET + town_shop.SEED_SIGNATURE_SIZE
            ],
            signature,
        )
        initializer = town_shop._build_state_initializer()
        self.assertLessEqual(
            len(initializer),
            town_shop.MANIFEST_OFFSET - town_shop.STATE_INITIALIZER_OFFSET,
        )
        self.assertIn(
            struct.pack("<I", town_shop._j(0x03, town_shop.STATE_INITIALIZER_ADDRESS)),
            town_shop._build_catalog_builder(),
        )

    def test_catalog_builder_embeds_the_cross_overlay_leading_descriptor(self) -> None:
        builder = town_shop._build_catalog_builder()
        self.assertIn(
            struct.pack(
                "<2I",
                town_shop._i(0x09, 0, 9, town_shop.SHOP_LEADING_ENTRY_WORD),
                town_shop._i(0x2B, 4, 9, 0),
            ),
            builder,
        )
        self.assertNotIn(struct.pack("<I", 0x8001_8A98), builder)

    def test_catalog_resolvers_use_the_active_overlays_recorded_catalog_base(self) -> None:
        builder = town_shop._build_catalog_builder()
        self.assertIn(
            struct.pack(
                "<I",
                town_shop._i(0x2B, 25, 4, town_shop.ACTIVE_CATALOG_OFFSET),
            ),
            builder,
        )
        lookup = struct.pack(
            "<3I",
            town_shop._i(0x23, 25, 8, town_shop.ACTIVE_CATALOG_OFFSET),
            0,
            town_shop._r(4, 8, 9, 0, 0x23),
        )
        for resolver in (
            town_shop._build_buy_price_resolver(),
            town_shop._build_item_name_resolver(),
            town_shop._build_description_resolver(),
        ):
            self.assertIn(lookup, resolver)

    def test_shop_text_loader_preserves_arguments_and_uses_synchronous_cd_path(self) -> None:
        loader = town_shop._build_shop_text_loader()
        self.assertEqual(len(loader), 0x6C)
        self.assertLessEqual(
            len(loader),
            town_shop.BUY_PRICE_OFFSET - town_shop.SHOP_TEXT_LOADER_OFFSET,
        )
        words = struct.unpack(f"<{len(loader) // 4}I", loader)
        self.assertEqual(words[0], town_shop._i(0x09, 29, 29, -40))
        self.assertEqual(words[-2], town_shop._j(0x02, town_shop.GENERIC_BUILDER_ADDRESS))
        self.assertEqual(words[-1], 0)
        self.assertEqual(
            words.count(town_shop._j(0x03, town_shop.WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS)),
            2,
        )
        self.assertIn(
            town_shop._j(0x03, town_shop.BUILD_CD_READ_DESCRIPTOR_ADDRESS),
            words,
        )
        self.assertIn(
            town_shop._j(0x03, town_shop.ENQUEUE_CD_COMMAND_ADDRESS),
            words,
        )
        self.assertIn(
            town_shop._i(0x09, 0, 8, town_shop.SHOP_TEXT_SECTOR_LBAS[0]),
            words,
        )
        self.assertIn(
            town_shop._r(8, 9, 7, 0, 0x23),
            words,
        )

        wrapper = struct.unpack("<4I", town_shop._build_equipment_builder())
        self.assertEqual(wrapper[1], town_shop._j(0x02, town_shop.SHOP_TEXT_LOADER_ADDRESS))

        monster_wrapper = struct.unpack("<4I", town_shop._build_monster_builder())
        self.assertEqual(monster_wrapper[0], town_shop._i(0x09, 0, 5, 1))
        self.assertEqual(
            monster_wrapper[1],
            town_shop._j(0x02, town_shop.SHOP_TEXT_LOADER_ADDRESS),
        )

    def test_monster_commit_wrapper_supplies_catalog_and_native_compaction(self) -> None:
        words = struct.unpack("<8I", town_shop._build_monster_commit_wrapper())
        self.assertEqual(words[0], town_shop._i(0x0F, 0, 4, 0x8002))
        self.assertEqual(words[1], town_shop._i(0x09, 4, 4, -0x7730))
        self.assertEqual(words[2], town_shop._r(31, 0, 25, 0, 0x21))
        self.assertEqual(words[3], town_shop._j(0x03, town_shop.COMMIT_PURCHASES_ADDRESS))
        self.assertEqual(words[4], 0)
        self.assertEqual(words[5], town_shop._r(25, 0, 31, 0, 0x21))
        self.assertEqual(
            words[6],
            town_shop._j(
                0x02,
                town_shop.MONSTER_COMPACT_INVENTORY_POINTER_TABLE_ADDRESS,
            ),
        )
        self.assertEqual(words[7], 0)
        self.assertEqual(
            len(town_shop._build_monster_commit_wrapper()),
            town_shop.COMMIT_PURCHASES_OFFSET - town_shop.MONSTER_COMMIT_WRAPPER_OFFSET,
        )

    def test_purchase_commit_does_not_write_the_town_stack_mailbox_address(self) -> None:
        unsafe_mailbox_load = struct.pack(
            "<II",
            town_shop._i(0x0F, 0, 9, 0x8020),
            town_shop._i(0x09, 9, 9, -0x54),
        )
        self.assertNotIn(unsafe_mailbox_load, town_shop._build_purchase_commit())

    def test_purchase_commit_waits_for_selected_flag_load(self) -> None:
        selected_flag_load = town_shop._i(0x24, 12, 10, 3)
        selected_flag_test = town_shop._i(0x0C, 10, 10, 0x20)
        words = struct.unpack(
            f"<{len(town_shop._build_purchase_commit()) // 4}I",
            town_shop._build_purchase_commit(),
        )
        load_index = words.index(selected_flag_load)
        self.assertEqual(words[load_index + 1], 0)
        self.assertEqual(words[load_index + 2], selected_flag_test)

    def test_catalog_gate_waits_for_saved_keycard_level_load(self) -> None:
        keycard_level_load = town_shop._i(0x23, 9, 10, 0)
        below_required_level_test = town_shop._r(10, 8, 11, 0, 0x2B)
        words = struct.unpack(
            f"<{len(town_shop._build_catalog_builder()) // 4}I",
            town_shop._build_catalog_builder(),
        )
        load_index = words.index(keycard_level_load)
        self.assertEqual(words[load_index + 1], 0)
        self.assertEqual(words[load_index + 2], below_required_level_test)

    def test_interior_keycard_gate_covers_only_the_unbuilt_third_shop(self) -> None:
        """Required levels are 0, 0, 6: the Monster Shop's lock is its door.

        The door gate (build_monster_door_gate) was proven live 2026-07-27,
        so the interior list gate came out - two gates for one lock would
        hide door-gate regressions.  Fur's shop keeps the interior guard
        until it has a door of its own.
        """

        required_level_sequence = (
            town_shop._i(0x0C, 5, 8, 2),   # andi t0,a1,2
            town_shop._r(0, 8, 9, 1, 0x00),  # sll t1,t0,1
            town_shop._r(8, 9, 8, 0, 0x21),  # addu t0,t0,t1
        )
        words = struct.unpack(
            f"<{len(town_shop._build_catalog_builder()) // 4}I",
            town_shop._build_catalog_builder(),
        )
        index = words.index(required_level_sequence[0])
        self.assertEqual(
            words[index:index + 3], required_level_sequence,
            "the (shop & 2) * 3 requirement computation moved or changed",
        )
        # The formula the instructions implement, kept honest in one place.
        for shop, expected in enumerate((0, 0, 6)):
            self.assertEqual((shop & 2) * 3, expected, f"shop {shop}")
        self.assertEqual(
            town_shop.MONSTER_SHOP_KEYCARD_REQUIREMENT,
            3,
            "the requirement itself is unchanged; only where it is enforced",
        )

    def test_purchase_commit_preserves_inventory_order_compaction(self) -> None:
        self.assertTrue(
            town_shop._build_purchase_commit().endswith(
                struct.pack(
                    "<II",
                    town_shop._j(
                        0x02,
                        town_shop.COMPACT_INVENTORY_POINTER_TABLE_ADDRESS,
                    ),
                    0,
                )
            )
        )

        words = struct.unpack(
            f"<{len(town_shop._build_purchase_commit()) // 4}I",
            town_shop._build_purchase_commit(),
        )
        active_shop_load = town_shop._i(
            0x24,
            8,
            3,
            town_shop.ACTIVE_SHOP_OFFSET,
        )
        load_index = words.index(active_shop_load)
        self.assertEqual(words[load_index + 1], town_shop._i(0x09, 0, 10, town_shop.SHOP_COUNT))
        self.assertEqual(words[load_index + 2], town_shop._r(3, 10, 11, 0, 0x2B))
        self.assertIn(town_shop._r(31, 0, 0, 0, 0x08), words[-6:])

    def test_monster_buy_script_fits_confirmed_zero_tail_and_uses_monster_seams(self) -> None:
        script = town_shop._build_monster_buy_script()
        self.assertLessEqual(len(script), town_shop.MONSTER_BUY_SCRIPT_CAPACITY)
        self.assertEqual(script[0:2], bytes((0x08, 0x15)))
        self.assertEqual(script[-1], 0x16)
        for address in (
            town_shop.MONSTER_BUY_MENU_SCRIPT_ADDRESS,
            town_shop.MONSTER_SUM_SELECTED_BUY_ADDRESS,
            town_shop.MONSTER_CAN_AFFORD_ADDRESS,
            town_shop.MONSTER_SUBTRACT_TOTAL_ADDRESS,
            town_shop.MONSTER_COMMIT_WRAPPER_ADDRESS,
        ):
            self.assertIn(struct.pack("<I", address), script)

        self.assertNotIn(struct.pack("<I", 0x8001_670C), script)
        self.assertNotIn(struct.pack("<I", 0x8001_6764), script)
        self.assertNotIn(struct.pack("<I", 0x8001_678C), script)
        self.assertNotIn(struct.pack("<I", 0x8001_67DC), script)

    def test_remote_name_is_full_width_and_native_name_uses_zero_pointer(self) -> None:
        slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        slots[0] = town_shop.ShopSlot(
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
            123,
            "Progressive Sword",
        )
        slots[1] = town_shop.ShopSlot(bytes((17, 4, 1, 0)), 456)
        payload = town_shop.build_town_shop_payload(slots)

        first = town_shop.MANIFEST_OFFSET
        second = first + town_shop.MANIFEST_RECORD_SIZE
        self.assertEqual(
            payload[first : first + 4],
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
        )
        self.assertEqual(struct.unpack_from("<I", payload, first + 4)[0], 123)
        remote_pointer = struct.unpack_from("<I", payload, first + 8)[0]
        self.assertGreaterEqual(remote_pointer, town_shop.SHOP_CORE_ADDRESS + town_shop.NAME_DATA_OFFSET)
        remote_offset = remote_pointer - town_shop.SHOP_CORE_ADDRESS
        self.assertTrue(
            payload[remote_offset:].startswith(
                town_shop._encode_shop_name("Progressiv", max_characters=None)
            )
        )

        self.assertEqual(payload[second : second + 4], bytes((17, 4, 1, 0)))
        self.assertEqual(struct.unpack_from("<I", payload, second + 4)[0], 456)
        self.assertEqual(struct.unpack_from("<I", payload, second + 8)[0], 0)

    def test_manifest_records_never_carry_item_flags(self) -> None:
        """Byte +3 is the menu's check flag once the row reaches the catalog.

        The buy catalog copies this word straight through, so a descriptor
        arriving with 0x80 (unidentified) or 0xC0 (cursed) reads as a row the
        menu has already dealt with and cannot be selected - which is what made
        every equipment row in both shops unbuyable. Quality at byte +2 must
        still survive; the native renderer prints it as a charge count.
        """

        slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        # A cursed -1 Copper Sword and an unidentified +2 Iron Shield.
        slots[0] = town_shop.ShopSlot(bytes((2, 15, 0xFF, 0xC0)), 1000, "Copper Sword")
        slots[1] = town_shop.ShopSlot(bytes((5, 17, 0x02, 0x80)), 1000, "Iron Shield")
        payload = town_shop.build_town_shop_payload(slots)

        for index, expected in enumerate((bytes((2, 15, 0xFF)), bytes((5, 17, 0x02)))):
            record = town_shop.MANIFEST_OFFSET + index * town_shop.MANIFEST_RECORD_SIZE
            self.assertEqual(payload[record : record + 3], expected)
            self.assertEqual(payload[record + 3], 0)

    def test_unfamiliar_item_description_and_proxy_use_resident_fallback(self) -> None:
        payload = town_shop.build_town_shop_payload(
            [None] * town_shop.SHOP_SLOT_COUNT
        )
        encoded = town_shop._encode_shop_name(
            town_shop.UNFAMILIAR_ITEM_DESCRIPTION,
            max_characters=None,
        )
        start = town_shop.UNFAMILIAR_ITEM_DESCRIPTION_OFFSET
        self.assertEqual(payload[start : start + len(encoded)], encoded)
        self.assertEqual(
            town_shop.UNFAMILIAR_ITEM_DESCRIPTION_ADDRESS,
            town_shop.SHOP_CORE_ADDRESS + start,
        )
        self.assertEqual(
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
            bytes((1, 11, 0, 0)),
        )

        resident_patches = dict(town_shop.iter_town_shop_resident_file_patches())
        self.assertEqual(
            resident_patches[
                town_shop.slus_runtime_to_file_offset(
                    town_shop.UNFAMILIAR_ITEM_DESCRIPTION_POINTER_ADDRESS
                )
            ],
            struct.pack(
                "<I",
                town_shop.RESIDENT_UNFAMILIAR_ITEM_DESCRIPTION_ADDRESS,
            ),
        )

        resident_slot = resident_patches[
            town_shop.slus_runtime_to_file_offset(
                town_shop.RESIDENT_DESCRIPTION_SLOT_ADDRESS
            )
        ]
        self.assertEqual(len(resident_slot), town_shop.RESIDENT_DESCRIPTION_SLOT_SIZE)
        self.assertEqual(
            resident_slot[
                town_shop.RESIDENT_DESCRIPTION_GATE_SIZE :
                town_shop.RESIDENT_DESCRIPTION_GATE_SIZE + len(encoded)
            ],
            encoded,
        )

    def test_shop_text_sector_packs_bounded_remote_descriptions(self) -> None:
        item_name = "12345678901234567890"
        player_name = "abcdefghijklmnopq"
        description = town_shop.format_shop_description(
            item_name,
            player_name,
        )
        self.assertEqual(
            description,
            "12345678901234567890\nfor abcdefghijklmnop",
        )
        self.assertEqual(len(description), 41)
        maximum = town_shop.format_shop_description(
            "123456789012345678901234567890EXTRA",
            "abcdefghijklmnopq",
        )
        self.assertEqual(
            maximum,
            "123456789012345678901234567890\nfor abcdefghijklmnop",
        )
        self.assertEqual(len(maximum), town_shop.MAX_SHOP_DESCRIPTION_CHARACTERS)

        slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        slots[0] = town_shop.ShopSlot(
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
            100,
            town_shop.REMOTE_ITEM_DISPLAY_NAME,
            description,
        )
        slots[9] = town_shop.ShopSlot(
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
            1_000,
            town_shop.REMOTE_ITEM_DISPLAY_NAME,
            "Hookshot\nfor Septic",
        )
        signature = b"SHOPTEXT"
        sector = town_shop.build_shop_text_sector(slots, 0, signature)
        self.assertEqual(len(sector), town_shop.SHOP_TEXT_BANK_SIZE)
        self.assertEqual(sector[:4], town_shop.SHOP_TEXT_MAGIC)
        self.assertEqual(
            struct.unpack_from("<HH", sector, 4),
            (town_shop.SHOP_TEXT_VERSION, 10),
        )
        self.assertEqual(sector[8], 0)
        self.assertEqual(sector[0x0C:0x14], signature)
        self.assertEqual(
            sector[town_shop.SHOP_TEXT_END_MARKER_OFFSET :],
            town_shop.SHOP_TEXT_END_MARKER,
        )

        first_offset = struct.unpack_from(
            "<H",
            sector,
            town_shop.SHOP_TEXT_OFFSET_TABLE,
        )[0]
        last_offset = struct.unpack_from(
            "<H",
            sector,
            town_shop.SHOP_TEXT_OFFSET_TABLE + 9 * 2,
        )[0]
        self.assertEqual(first_offset, town_shop.SHOP_TEXT_DATA_OFFSET)
        self.assertGreater(last_offset, first_offset)
        self.assertTrue(
            sector[first_offset:].startswith(
                town_shop._encode_shop_name(description, max_characters=None)
            )
        )
        self.assertTrue(
            sector[last_offset:].startswith(
                town_shop._encode_shop_name("Hookshot\nfor Septic", max_characters=None)
            )
        )

    def test_selected_description_resolver_validates_bank_and_hooks_both_menu_modes(self) -> None:
        resolver = town_shop._build_description_resolver()
        self.assertLessEqual(
            len(resolver),
            town_shop.MENU_CONSTRUCTOR_OFFSET - town_shop.DESCRIPTION_RESOLVER_OFFSET,
        )
        words = struct.unpack(f"<{len(resolver) // 4}I", resolver)
        self.assertIn(
            town_shop._j(0x03, town_shop.SHOW_ITEM_DESCRIPTION_ADDRESS),
            words,
        )
        self.assertEqual(
            words[-2:],
            (town_shop._j(0x02, town_shop.VANILLA_ITEM_DESCRIPTION_ADDRESS), 0),
        )
        self.assertIn(
            town_shop._i(0x25, 11, 11, 0),
            words,
        )

        resident_patches = dict(town_shop.iter_town_shop_resident_file_patches())
        expected_hook = struct.pack(
            "<I", town_shop._j(0x03, town_shop.RESIDENT_DESCRIPTION_GATE_ADDRESS)
        )
        for address in town_shop.SELECTED_DESCRIPTION_HOOK_ADDRESSES:
            self.assertEqual(
                resident_patches[town_shop.slus_runtime_to_file_offset(address)],
                expected_hook,
            )

    def test_resident_description_gate_checks_town_magic_with_load_delay(self) -> None:
        gate = town_shop._build_resident_description_gate()
        self.assertEqual(len(gate), town_shop.RESIDENT_DESCRIPTION_GATE_SIZE)
        words = struct.unpack(f"<{len(gate) // 4}I", gate)
        magic = int.from_bytes(town_shop.SHOP_CORE_MAGIC[:4], "little")
        self.assertEqual(
            words,
            (
                town_shop._i(
                    0x0F,
                    0,
                    8,
                    town_shop._upper(town_shop.SHOP_CORE_ADDRESS),
                ),
                town_shop._i(
                    0x23,
                    8,
                    9,
                    town_shop._lower(town_shop.SHOP_CORE_ADDRESS),
                ),
                town_shop._i(0x0F, 0, 10, (magic >> 16) & 0xFFFF),
                town_shop._i(0x0D, 10, 10, magic & 0xFFFF),
                town_shop._i(0x05, 9, 10, 3),
                0,
                town_shop._j(0x02, town_shop.DESCRIPTION_RESOLVER_ADDRESS),
                0,
                town_shop._j(0x02, town_shop.VANILLA_ITEM_DESCRIPTION_ADDRESS),
                0,
            ),
        )

    def test_ten_maximum_two_line_descriptions_fit_one_sector(self) -> None:
        slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        for index in range(town_shop.SLOTS_PER_SHOP):
            item_name = f"{index:02d}" + "I" * 28
            player_name = f"{index:02d}" + "P" * 14
            slots[index] = town_shop.ShopSlot(
                town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
                100,
                town_shop.REMOTE_ITEM_DISPLAY_NAME,
                town_shop.format_shop_description(item_name, player_name),
            )

        sector = town_shop.build_shop_text_sector(slots, 0)
        used = struct.unpack_from("<H", sector, 0x0A)[0]
        self.assertEqual(used, 0x43C)
        self.assertLess(used, town_shop.SHOP_TEXT_END_MARKER_OFFSET)

    def test_monster_text_sector_uses_manifest_slots_ten_through_nineteen(self) -> None:
        slots: list[town_shop.ShopSlot | None] = [None] * town_shop.SHOP_SLOT_COUNT
        slots[10] = town_shop.ShopSlot(
            town_shop.UNFAMILIAR_ITEM_PROXY_DESCRIPTOR,
            100,
            town_shop.REMOTE_ITEM_DISPLAY_NAME,
            "Monster Slot 1\nfor Septic",
        )
        sector = town_shop.build_shop_text_sector(slots, 1, b"MONSTER1")
        self.assertEqual(sector[8], 1)
        first_offset = struct.unpack_from(
            "<H",
            sector,
            town_shop.SHOP_TEXT_OFFSET_TABLE,
        )[0]
        self.assertEqual(first_offset, town_shop.SHOP_TEXT_DATA_OFFSET)
        self.assertTrue(
            sector[first_offset:].startswith(
                town_shop._encode_shop_name(
                    "Monster Slot 1\nfor Septic",
                    max_characters=None,
                )
            )
        )

    def test_hooks_target_core_and_never_cross_sector_user_data(self) -> None:
        payload = town_shop.build_town_shop_payload([None] * town_shop.SHOP_SLOT_COUNT)
        patches = town_shop.iter_town_shop_raw_patches(payload)
        self.assertTrue(any(data.startswith(town_shop.SHOP_CORE_MAGIC) for _, data in patches))
        for raw_offset, data in patches:
            self.assertLessEqual((raw_offset - 24) % 2_352 + len(data), 2_048)

        file_patches = dict(town_shop.iter_town_shop_file_patches(payload))
        self.assertEqual(
            file_patches[town_shop.equipment_runtime_to_file_offset(town_shop.EQUIPMENT_BUILDER_HOOK_ADDRESS)],
            struct.pack("<I", town_shop._j(0x03, town_shop.EQUIPMENT_BUILDER_ADDRESS)),
        )
        self.assertEqual(
            file_patches[town_shop.monster_runtime_to_file_offset(town_shop.MONSTER_BUILDER_HOOK_ADDRESS)],
            struct.pack("<I", town_shop._j(0x03, town_shop.MONSTER_BUILDER_ADDRESS)),
        )
        self.assertEqual(
            file_patches[town_shop.MONSTER_BUY_CHOICE_POINTER_FILE_OFFSET],
            struct.pack("<I", town_shop.MONSTER_BUY_SCRIPT_ADDRESS),
        )
        self.assertEqual(
            file_patches[town_shop.MONSTER_BUY_SCRIPT_FILE_OFFSET],
            town_shop._build_monster_buy_script(),
        )
        self.assertEqual(
            file_patches[town_shop.town_runtime_to_file_offset(town_shop.TOWN_BUY_PRICE_POINTER_ADDRESS)],
            struct.pack("<I", town_shop.BUY_PRICE_ADDRESS),
        )

    def test_large_monster_script_is_split_into_valid_ppf_records(self) -> None:
        ppf = bytearray()
        town_shop.append_town_shop_hook_ppf_records(ppf)

        records: dict[int, bytes] = {}
        cursor = 0
        while cursor < len(ppf):
            raw_offset, length = struct.unpack_from("<IB", ppf, cursor)
            cursor += 5
            self.assertGreater(length, 0)
            self.assertLessEqual(length, 255)
            records[raw_offset] = bytes(ppf[cursor : cursor + length])
            cursor += length

        script = town_shop._build_monster_buy_script()
        raw_offset = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            town_shop.MONSTER_BUY_SCRIPT_FILE_OFFSET,
        )
        self.assertEqual(records[raw_offset], script[:255])
        self.assertEqual(records[raw_offset + 255], script[255:])


class TestEquipmentGreetingRemoval(unittest.TestCase):
    """Barry's greeting is replaced by an immediate Buy/Sell/Leave menu."""

    def _disc(self):
        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        return ORIGINAL_BIN.read_bytes()

    def _town(self, disc, file_offset, length):
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA, file_offset
        )
        return disc[raw:raw + length]

    def test_dialogue_base_comes_from_the_self_referencing_stub(self) -> None:
        """The stub's own `3E 0F` operand is its runtime address.

        Applied to Monster this must reproduce the documented 0x80018D18, which
        is what licenses using the same trick for Equipment.
        """

        disc = self._disc()
        for file_offset, base, expected_stub in (
            (0x603804, 0x80018D18, 0x80018D1C),   # Monster - the control
            (0x61B004, town_shop.EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS, 0x80018F0C),
        ):
            stub = self._town(disc, file_offset, 18)
            self.assertEqual(stub[0], 0x30, "stub must start with yield")
            self.assertEqual(stub[7], 0x4C, "stub must call a native opener")
            self.assertEqual(stub[12], 0x3E)
            self.assertEqual(stub[13], 0x0F)
            back = struct.unpack("<I", stub[14:18])[0]
            self.assertEqual(back, expected_stub)
            # The stub sits four bytes into the resource, so its self-reference
            # minus four is the resource base. This is the whole derivation.
            self.assertEqual(back - 4, base, f"base from stub at 0x{file_offset:x}")

        self.assertEqual(
            town_shop.EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS, 0x8001_8F08
        )
        self.assertEqual(town_shop.EQUIPMENT_DIALOGUE_FILE_OFFSET, 0x61_B000)
        self.assertEqual(
            town_shop.equipment_dialogue_runtime_to_file_offset(0x8001_9130),
            0x61_B228,
        )

    def test_every_greeting_becomes_a_six_byte_menu_call(self) -> None:
        skip = town_shop.build_equipment_greeting_skip()
        self.assertEqual(
            skip,
            bytes([0x15])
            + struct.pack("<I", town_shop.EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS)
            + bytes([0x01]),
        )
        self.assertEqual(len(skip), 6)

        patches = dict(town_shop.iter_equipment_greeting_file_patches())
        # five greetings + nine retargets + the shared tail
        self.assertEqual(len(patches), 15)
        for entry in town_shop.EQUIPMENT_GREETING_ENTRY_ADDRESSES:
            offset = town_shop.equipment_dialogue_runtime_to_file_offset(entry)
            self.assertEqual(patches[offset], skip, f"entry 0x{entry:08x}")

    def test_retargets_are_four_byte_absolute_addresses_in_the_resource(self) -> None:
        patches = dict(town_shop.iter_equipment_greeting_file_patches())
        for address, target, label in town_shop.EQUIPMENT_DIALOGUE_RETARGETS:
            offset = town_shop.equipment_dialogue_runtime_to_file_offset(address)
            self.assertEqual(patches[offset], struct.pack("<I", target), label)
            # every retarget must land on a live destination we keep
            self.assertIn(
                target,
                (
                    town_shop.EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS,
                    town_shop.EQUIPMENT_BUY_OPEN_ADDRESS,
                    town_shop.EQUIPMENT_SELL_OPEN_ADDRESS,
                ),
                label,
            )

    def test_the_transaction_tail_keeps_its_acknowledgement(self) -> None:
        """`Thanks very much.` borrows this wait; dropping it would flash by."""

        tail = town_shop.build_equipment_transaction_tail()
        self.assertEqual(tail[0], 0x11, "the wait must survive")
        self.assertEqual(tail[1], 0x17)
        self.assertEqual(
            struct.unpack("<I", tail[2:])[0],
            town_shop.EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS,
        )

    def test_buy_and_sell_openers_are_call_sites_we_keep(self) -> None:
        """The retargets aim at `08 clear | 15 call <opener>`, not at prose."""

        disc = self._disc()
        for address, opener in (
            (town_shop.EQUIPMENT_BUY_OPEN_ADDRESS, 0x80018F0C),
            (town_shop.EQUIPMENT_SELL_OPEN_ADDRESS, 0x80018F20),
        ):
            offset = town_shop.equipment_dialogue_runtime_to_file_offset(address)
            block = self._town(disc, offset, 6)
            self.assertEqual(block[0], 0x08, f"0x{address:08x} must clear first")
            self.assertEqual(block[1], 0x15, f"0x{address:08x} must call")
            self.assertEqual(struct.unpack("<I", block[2:6])[0], opener)

    def test_greetings_really_were_greetings(self) -> None:
        """Every entry we overwrite must start `57 <mode>` - a text window.

        If one of them were a menu opener or a state stub instead, replacing it
        with a menu call would remove a behaviour rather than a line of chat.
        """

        disc = self._disc()
        for entry in town_shop.EQUIPMENT_GREETING_ENTRY_ADDRESSES:
            offset = town_shop.equipment_dialogue_runtime_to_file_offset(entry)
            head = self._town(disc, offset, 3)
            self.assertEqual(head[0], 0x57, f"entry 0x{entry:08x} is not text")

    def test_the_menu_survives_and_is_still_a_choice_block(self) -> None:
        disc = self._disc()
        menu = town_shop.equipment_dialogue_runtime_to_file_offset(
            town_shop.EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS
        )
        # 08 clear | 57 01 choice window | 0B row | 0x816D opening bracket
        self.assertEqual(
            self._town(disc, menu, 6), bytes([0x08, 0x57, 0x01, 0x0B, 0x81, 0x6D])
        )

        # The only bytes we may write inside the menu block are the first two
        # slots of its branch table - the Buy and Sell row destinations. Its
        # rows, its `19 03` count and the Leave destination must be untouched.
        written = set()
        for offset, data in town_shop.iter_equipment_greeting_file_patches():
            written.update(range(offset, offset + len(data)))
        allowed = set()
        for address in (0x8001_91B5, 0x8001_91B9):
            base = town_shop.equipment_dialogue_runtime_to_file_offset(address)
            allowed.update(range(base, base + 4))
        self.assertFalse(
            (written & set(range(menu, menu + 0x91))) - allowed,
            "a patch overlaps the Buy/Sell/Leave menu outside its branch table",
        )
        leave = town_shop.equipment_dialogue_runtime_to_file_offset(0x8001_91BD)
        self.assertFalse(
            written & set(range(leave, leave + 4)),
            "the Leave row destination must not move",
        )
        self.assertEqual(
            struct.unpack("<I", self._town(disc, leave, 4))[0],
            0x8001_96D9,
            "Leave must still reach 'Then good-bye.'",
        )

    def test_free_spans_are_disjoint_from_every_write(self) -> None:
        """The spans declared free must not contain anything we wrote."""

        written = set()
        for offset, data in town_shop.iter_equipment_greeting_file_patches():
            written.update(range(offset, offset + len(data)))

        total = 0
        previous_end = 0
        for start, end, label in town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS:
            self.assertLess(start, end, label)
            self.assertGreater(start, previous_end, f"{label} is out of order")
            previous_end = end
            lo = town_shop.equipment_dialogue_runtime_to_file_offset(start)
            hi = town_shop.equipment_dialogue_runtime_to_file_offset(end)
            self.assertFalse(
                written & set(range(lo, hi + 1)),
                f"free span {label} covers bytes we write",
            )
            total += end - start + 1

        self.assertEqual(total, 2039, "freed byte count changed")

        # Each greeting body must begin exactly where its stub ends, which is
        # what proves the spans line up with the edit rather than being guessed.
        stub_ends = {
            entry + len(town_shop.build_equipment_greeting_skip())
            for entry in town_shop.EQUIPMENT_GREETING_ENTRY_ADDRESSES
        }
        starts = {start for start, _, _ in town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS}
        self.assertTrue(
            stub_ends <= starts,
            f"greeting bodies not all freed: {stub_ends - starts}",
        )

        # Nothing we keep may fall inside a freed span.
        for kept in (
            town_shop.EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS,
            town_shop.EQUIPMENT_BUY_OPEN_ADDRESS,
            town_shop.EQUIPMENT_SELL_OPEN_ADDRESS,
            town_shop.EQUIPMENT_TRANSACTION_TAIL_ADDRESS,
            0x8001_93DF,  # "Thanks very much." (Buy)
            0x8001_9607,  # the sale commit and its confirmation
            0x8001_9337,  # "You don't have enough money."
            0x8001_96D9,  # "Then good-bye."
        ):
            for start, end, label in town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS:
                self.assertFalse(
                    start <= kept <= end,
                    f"0x{kept:08x} is kept but sits inside freed span {label}",
                )


class TestIntroState(unittest.TestCase):
    """The v112 intro state: hut egg, no tutorial, no entrance sequence.

    Every value is measured from the 2026-07-28 snapshot diffs; the sim
    proves the writer fires on the fresh-save branch and only there.
    """

    def _memory(self) -> mips_sim.Memory:
        memory = mips_sim.Memory()
        memory.load_bytes(
            town_shop.STATE_INITIALIZER_ADDRESS,
            town_shop._build_state_initializer(),
        )
        memory.load_bytes(
            town_shop.INTRO_STATE_WRITER_ADDRESS,
            town_shop._build_intro_state_writer(),
        )
        memory.load_bytes(
            town_shop.INTRO_STATE_TABLE_ADDRESS,
            town_shop._build_intro_state_table(),
        )
        signature = town_shop.SHOP_CORE_ADDRESS + town_shop.SEED_SIGNATURE_OFFSET
        memory.write32(signature, 0x1111_1111)
        memory.write32(signature + 4, 0x2222_2222)
        return memory

    def test_fresh_save_gets_the_intro_state(self) -> None:
        memory = self._memory()
        cpu = mips_sim.Cpu(memory)
        cpu.run(town_shop.STATE_INITIALIZER_ADDRESS)
        self.assertEqual(
            memory.read32(town_shop.PERSISTENT_STATE_ADDRESS),
            town_shop.PERSISTENT_STATE_MAGIC,
        )
        for address, mask in town_shop.INTRO_STATE_WRITES:
            self.assertEqual(
                memory.read32(address) & mask,
                mask,
                f"intro write at 0x{address:08X} missing",
            )
        self.assertEqual(
            memory.read32(town_shop.MONSTER_HUT_FIRST_SLOT_ADDRESS),
            town_shop.KEWNE_EGG_RECORD_WORD,
        )

    def test_intro_writes_are_ors_not_stores(self) -> None:
        memory = self._memory()
        # Pre-set an unrelated bit in every target word; it must survive.
        for address, _ in town_shop.INTRO_STATE_WRITES:
            memory.write32(address, 0x4000_0000)
        cpu = mips_sim.Cpu(memory)
        cpu.run(town_shop.STATE_INITIALIZER_ADDRESS)
        for address, mask in town_shop.INTRO_STATE_WRITES:
            self.assertEqual(
                memory.read32(address), 0x4000_0000 | mask,
                f"OR at 0x{address:08X} clobbered neighbours",
            )

    def test_initialized_save_is_left_alone(self) -> None:
        memory = self._memory()
        cpu = mips_sim.Cpu(memory)
        cpu.run(town_shop.STATE_INITIALIZER_ADDRESS)
        # Wipe the intro state; a second pass must not rewrite it because
        # the journal is now valid.
        for address, _ in town_shop.INTRO_STATE_WRITES:
            memory.write32(address, 0)
        cpu = mips_sim.Cpu(memory)
        cpu.run(town_shop.STATE_INITIALIZER_ADDRESS)
        for address, _ in town_shop.INTRO_STATE_WRITES:
            self.assertEqual(
                memory.read32(address), 0,
                f"initializer re-fired at 0x{address:08X}",
            )

    def test_flag_masks_encode_the_measured_flag_ids(self) -> None:
        # v113: entrance removal only - the tutorial flag set is retired
        # (played v112: it did not remove the tutorial).
        expected = {0xDAB, 0xDAC}
        got = set()
        for address, mask in town_shop.INTRO_STATE_WRITES:
            if address < town_shop.STORY_FLAG_ARRAY_ADDRESS:
                continue
            word = (address - town_shop.STORY_FLAG_ARRAY_ADDRESS) // 4
            for bit in range(32):
                if mask >> bit & 1:
                    got.add(word * 32 + bit)
        self.assertEqual(got, expected)


class TestSendMenuUiGates(unittest.TestCase):
    """The price and BUY-tag gates for Nada's send menu (v108).

    Both are keyed on ACTIVE_SHOP == SEND_MENU_SHOP_MARKER, so only her
    menu changes; every other menu falls through to (price) or replays
    (tag) the vanilla behaviour.
    """

    def _disc(self):
        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        return ORIGINAL_BIN.read_bytes()

    def _word(self, disc, address: int) -> int:
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            town_shop.town_runtime_to_file_offset(address),
        )
        return struct.unpack("<I", disc[raw:raw + 4])[0]

    def test_hook_sites_match_the_disc(self) -> None:
        disc = self._disc()
        self.assertEqual(
            self._word(disc, town_shop.GENERIC_PRICE_VISIBILITY_HOOK_ADDRESS),
            0x0C02C3E5,  # jal 0x800B0F94 - its only call site
        )
        self.assertEqual(
            self._word(disc, town_shop.CHECKED_TAG_JUMP_ADDRESS),
            0x0802C2F2,  # j 0x800B0BC8
        )
        self.assertEqual(
            self._word(disc, town_shop.CHECKED_TAG_JUMP_ADDRESS + 4),
            town_shop.CHECKED_TAG_STORE_WORD,
        )
        # The vanilla per-entry price test we fall through to: the
        # category-0x16/0x19 check, pinned so the fallback stays real.
        self.assertEqual(
            self._word(disc, town_shop.VANILLA_PRICE_VISIBILITY_GATE_ADDRESS),
            0x90830001,  # lbu v1,0x1(a0)
        )
        self.assertEqual(
            self._word(
                disc, town_shop.VANILLA_PRICE_VISIBILITY_GATE_ADDRESS + 4
            ),
            0x24020016,  # li v0,0x16
        )

    def test_header_slot_and_string_match_the_disc(self) -> None:
        disc = self._disc()
        self.assertEqual(
            self._word(disc, town_shop.MENU_HEADER_POINTER_SLOT_ADDRESS),
            town_shop.VANILLA_MENU_HEADER_TEXT_ADDRESS,
            "the header pointer slot does not hold the Pay string",
        )
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            town_shop.town_runtime_to_file_offset(
                town_shop.VANILLA_MENU_HEADER_TEXT_ADDRESS
            ),
        )
        self.assertEqual(
            disc[raw:raw + 6],
            bytes.fromhex("826f82818299"),  # fullwidth "Pay"
            "the vanilla header string moved",
        )

    def _run_price_gate(self, active_shop: int) -> int:
        memory = mips_sim.Memory()
        memory.load_bytes(
            town_shop.PRICE_VISIBILITY_GATE_ADDRESS,
            town_shop._build_price_visibility_gate(),
        )
        # A stand-in vanilla test that returns 0x77, so a fall-through is
        # distinguishable from the gate's own zero.
        memory.load_bytes(
            town_shop.VANILLA_PRICE_VISIBILITY_GATE_ADDRESS,
            struct.pack(
                "<2I",
                0x03E00008,  # jr ra
                0x24020077,  # (delay) li v0,0x77
            ),
        )
        memory.write8(
            town_shop.SHOP_CORE_ADDRESS + town_shop.ACTIVE_SHOP_OFFSET,
            active_shop,
        )
        cpu = mips_sim.Cpu(memory)
        return cpu.run(town_shop.PRICE_VISIBILITY_GATE_ADDRESS)

    def test_price_gate_hides_send_menu_prices_only(self) -> None:
        self.assertEqual(
            self._run_price_gate(town_shop.SEND_MENU_SHOP_MARKER), 0
        )
        self.assertEqual(self._run_price_gate(0), 0x77)
        self.assertEqual(self._run_price_gate(1), 0x77)

    def _run_tag_gate(self, active_shop: int) -> tuple[int, int]:
        memory = mips_sim.Memory()
        memory.load_bytes(
            town_shop.CHECKED_TAG_GATE_ADDRESS,
            town_shop._build_checked_tag_gate(),
        )
        memory.load_bytes(
            town_shop.CHECKED_TAG_RESUME_ADDRESS,
            struct.pack("<2I", 0x03E00008, 0),  # jr ra / nop
        )
        memory.write8(
            town_shop.SHOP_CORE_ADDRESS + town_shop.ACTIVE_SHOP_OFFSET,
            active_shop,
        )
        tag_object = 0x8010_0000
        memory.write32(tag_object, 0)
        cpu = mips_sim.Cpu(memory)
        cpu.registers[2] = 0x800D_15EC  # v0: the tag pointer being stored
        cpu.registers[3] = tag_object   # v1: the row's tag object
        cpu.run(town_shop.CHECKED_TAG_GATE_ADDRESS)
        return memory.read32(tag_object)

    def test_tag_gate_skips_the_tag_for_the_send_menu_only(self) -> None:
        self.assertEqual(
            self._run_tag_gate(town_shop.SEND_MENU_SHOP_MARKER), 0
        )
        self.assertEqual(self._run_tag_gate(1), 0x800D_15EC)

    def _run_capacity_gate(self, active_shop: int) -> int:
        memory = mips_sim.Memory()
        memory.load_bytes(
            town_shop.CHECK_CAPACITY_GATE_ADDRESS,
            town_shop._build_check_capacity_gate(),
        )
        # A stand-in vanilla guard that reports a distinctive refusal.
        memory.load_bytes(
            town_shop.VANILLA_CHECK_CAPACITY_GUARD_ADDRESS,
            struct.pack("<2I", 0x03E00008, 0x24020077),  # jr ra / li v0,0x77
        )
        memory.write8(
            town_shop.SHOP_CORE_ADDRESS + town_shop.ACTIVE_SHOP_OFFSET,
            active_shop,
        )
        cpu = mips_sim.Cpu(memory)
        return cpu.run(town_shop.CHECK_CAPACITY_GATE_ADDRESS)

    def test_capacity_gate_lifts_the_bag_limit_for_sends_only(self) -> None:
        """A send removes items, so the vanilla buy rule - checked rows plus
        occupied slots under twenty - must never refuse a send-menu check;
        it inverted into "a nineteen-item bag can send exactly one item per
        conversation". Real shops (and vanilla menus, ACTIVE_SHOP 0xFF) keep
        the vanilla guard byte for byte."""

        self.assertEqual(
            self._run_capacity_gate(town_shop.SEND_MENU_SHOP_MARKER), 1
        )
        self.assertEqual(self._run_capacity_gate(0), 0x77)
        self.assertEqual(self._run_capacity_gate(1), 0x77)
        self.assertEqual(self._run_capacity_gate(0xFF), 0x77)

    def test_capacity_hook_site_matches_the_disc(self) -> None:
        """The interposed `jal` must still be where the disassembly put it.

        The hook rewrites the A-press handler's guard call at
        `CHECK_CAPACITY_HOOK_ADDRESS`; if the town-mode overlay ever shifts,
        this catches it before a built disc aims the gate at junk."""

        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        disc = ORIGINAL_BIN.read_bytes()
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            town_shop.town_runtime_to_file_offset(
                town_shop.CHECK_CAPACITY_HOOK_ADDRESS
            ),
        )
        self.assertEqual(
            struct.unpack("<2I", disc[raw:raw + 8]),
            (
                town_shop._j(
                    0x03, town_shop.VANILLA_CHECK_CAPACITY_GUARD_ADDRESS
                ),
                0x0200_2021,  # delay slot: addu a0,s0,zero
            ),
            "The A-press handler's guard call moved; re-derive the hook site.",
        )


class TestMonsterDoorGate(unittest.TestCase):
    """The gate interposes on the armed door states' readiness test.

    The 2026-07-27 Ghidra pass (AzureDreamsTownLive) showed both armed states
    commit NOTHING before `is_player_ready_for_scene_transition` returns
    nonzero, so refusing there - by forcing a zero return - leaves the door
    resting with no half-started walk.  The gate blocks only when the actor's
    record at +0x48 is the Monster Shop's and the Keycard is below 3, and
    publishes `Door is locked.` through town_receive's validated dialogue
    queue.
    """

    def _disc(self):
        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        return ORIGINAL_BIN.read_bytes()

    def test_record_and_position_match_the_disc(self) -> None:
        """The measured record must still be there, or the gate aims at junk."""

        disc = self._disc()
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA,
            0x2AB800 + town_shop.MONSTER_DOOR_RECORD_ADDRESS - 0x8001_6000,
        )
        self.assertEqual(
            struct.unpack("<I", disc[raw:raw + 4])[0],
            town_shop.MONSTER_DOOR_TYPE_AND_INDEX,
        )
        self.assertEqual(
            struct.unpack("<I", disc[raw + 4:raw + 8])[0],
            town_shop.MONSTER_DOOR_OPEN_POSITION,
        )
        self.assertEqual(
            town_shop.MONSTER_DOOR_TYPE_AND_INDEX >> 16,
            town_shop.MONSTER_DOOR_RECORD_INDEX,
        )

    def test_ready_hook_sites_match_the_disc(self) -> None:
        """Both patched words must be `jal is_player_ready_for_scene_transition`.

        If either site holds anything else, the armed-state layout has moved
        and the gate would clobber a live instruction - the delay-slot lesson
        from the Kewne crash, checked here before it can ship.
        """

        self.assertEqual(
            town_shop.DOOR_READY_ORIGINAL_WORD,
            town_shop._j(0x03, town_shop.DOOR_READY_TEST_ADDRESS),
        )
        disc = self._disc()
        for address in town_shop.DOOR_READY_HOOK_ADDRESSES:
            raw = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA,
                town_shop.town_runtime_to_file_offset(address),
            )
            self.assertEqual(
                struct.unpack("<I", disc[raw:raw + 4])[0],
                town_shop.DOOR_READY_ORIGINAL_WORD,
                f"0x{address:08x} is not the readiness jal on the disc",
            )

    def test_gate_and_script_are_installed(self) -> None:
        payload = town_shop.build_town_shop_payload(
            [None] * town_shop.SHOP_SLOT_COUNT
        )
        script = town_shop.build_door_locked_script()
        gate = town_shop.build_monster_door_gate()
        start = town_shop.DOOR_GATE_SCRIPT_OFFSET
        self.assertEqual(payload[start:start + len(script)], script)
        start = town_shop.DOOR_GATE_OFFSET
        self.assertEqual(payload[start:start + len(gate)], gate)
        # The cooldown latch ships zeroed.
        latch = town_shop.DOOR_GATE_LATCH_OFFSET
        self.assertEqual(payload[latch:latch + 4], bytes(4))

        hook = struct.pack("<I", town_shop._j(0x03, town_shop.DOOR_GATE_ADDRESS))
        patches = dict(town_shop.iter_town_shop_hook_file_patches())
        for address in town_shop.DOOR_READY_HOOK_ADDRESSES:
            offset = town_shop.town_runtime_to_file_offset(address)
            self.assertEqual(patches.get(offset), hook, f"0x{address:08x}")

    def test_script_is_text_wait_end(self) -> None:
        """CP932 text + 0x11 + 0x01; a trailing zero would be a script RETURN."""

        script = town_shop.build_door_locked_script()
        self.assertEqual(script[-2:], bytes([0x11, 0x01]))
        self.assertNotIn(0, script)
        # Full-width CP932: two bytes per character.
        self.assertEqual(len(script), 2 * len(town_shop.DOOR_LOCKED_MESSAGE) + 2)

    def test_full_name_catalog_cannot_reach_the_gate(self) -> None:
        """Thirty maximum-length unique names must still fit below the script."""

        slots = [
            town_shop.ShopSlot(bytes((2, 15, 0, 0)), 500, f"Item Nr {index:02d}")
            for index in range(town_shop.SHOP_SLOT_COUNT)
        ]
        payload = town_shop.build_town_shop_payload(slots)
        self.assertEqual(
            payload[
                town_shop.DOOR_GATE_SCRIPT_OFFSET :
                town_shop.DOOR_GATE_SCRIPT_OFFSET
                + len(town_shop.build_door_locked_script())
            ],
            town_shop.build_door_locked_script(),
        )


class TestMonsterDoorGateBehaviour(unittest.TestCase):
    """Runs the generated gate bytes through the load-delay simulator."""

    ACTOR = 0x801E_C000
    RESULT_READY = 3  # any nonzero vanilla readiness result
    MOTION_BLOCK = 0x8008_2D00
    AUX_BLOCK = 0x8008_2E00

    def _run(
        self,
        record: int,
        keycard: int,
        ready: bool,
        latch: int = 0,
        queued: int = 0,
        extra: dict[int, int] | None = None,
    ):
        memory = mips_sim.Memory()
        memory.load_bytes(
            town_shop.DOOR_GATE_ADDRESS, town_shop.build_monster_door_gate()
        )
        for address, value in (extra or {}).items():
            memory.write32(address, value)
        memory.write32(self.ACTOR + 0x48, record)
        memory.write32(town_shop.KEYCARD_LEVEL_ADDRESS, keycard)
        memory.write32(town_shop.DOOR_GATE_LATCH_ADDRESS, latch)
        queue_address = (
            town_shop.TOWN_DIALOGUE_DESCRIPTOR_ADDRESS
            + town_shop.TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET
        )
        memory.write32(queue_address, queued)
        # Nonzero column/row prove the publish zeroes them.
        memory.write32(
            town_shop.TOWN_DIALOGUE_DESCRIPTOR_ADDRESS
            + town_shop.TOWN_DIALOGUE_COLUMN_OFFSET,
            0x0BAD_0BAD,
        )
        # The player-state globals the stop sequence must pass through, and
        # a poisoned +0x2C word the publish must clear.
        memory.write32(
            town_shop.PLAYER_MOTION_POINTER_ADDRESS, self.MOTION_BLOCK
        )
        memory.write32(town_shop.PLAYER_AUX_POINTER_ADDRESS, self.AUX_BLOCK)
        memory.write32(
            town_shop.PLAYER_STATE_ADDRESS + town_shop.PLAYER_TALK_CLEAR_OFFSET,
            0x0BAD_0BAD,
        )

        calls = []

        def readiness_stub(cpu: mips_sim.Cpu) -> None:
            calls.append(("ready", cpu.registers[4], cpu.registers[5]))
            cpu.registers[2] = self.RESULT_READY if ready else 0

        def face_stub(cpu: mips_sim.Cpu) -> None:
            calls.append(("face", cpu.registers[4]))

        def standing_stub(cpu: mips_sim.Cpu) -> None:
            calls.append(
                ("stand", cpu.registers[4], cpu.registers[5], cpu.registers[6])
            )

        cpu = mips_sim.Cpu(
            memory,
            stubs={
                town_shop.DOOR_READY_TEST_ADDRESS: readiness_stub,
                town_shop.PLAYER_FACE_INPUT_DIRECTION_ADDRESS: face_stub,
                town_shop.PLAYER_ENTER_STANDING_STATE_ADDRESS: standing_stub,
            },
        )
        cpu.registers[4] = self.ACTOR
        cpu.registers[5] = self.ACTOR
        cpu.registers[17] = self.ACTOR  # s1, as both armed states hold it
        result = cpu.run(town_shop.DOOR_GATE_ADDRESS)
        return result, memory, calls, queue_address

    def _stop_calls(self, calls):
        return [entry for entry in calls if entry[0] in ("face", "stand")]

    def test_other_doors_pass_the_vanilla_result_through(self) -> None:
        for ready in (True, False):
            result, memory, calls, queue = self._run(
                record=0x8001_BF6C, keycard=0, ready=ready
            )
            self.assertEqual(result, self.RESULT_READY if ready else 0)
            self.assertEqual(len(calls), 1, "vanilla test must be consulted")
            self.assertEqual(memory.read32(queue), 0, "no message for other doors")
            self.assertEqual(self._stop_calls(calls), [], "player untouched")

    def test_keycard_three_enters_normally(self) -> None:
        result, memory, _, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS, keycard=3, ready=True
        )
        self.assertEqual(result, self.RESULT_READY)
        self.assertEqual(memory.read32(queue), 0)

    def test_low_keycard_refuses_and_publishes_once(self) -> None:
        result, memory, calls, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS, keycard=2, ready=True
        )
        self.assertEqual(result, 0, "the armed state must keep resting")
        self.assertEqual(memory.read32(queue), town_shop.DOOR_GATE_SCRIPT_ADDRESS)
        # The vanilla collision-talk stop, with the exact vanilla arguments.
        self.assertEqual(
            self._stop_calls(calls),
            [
                ("face", town_shop.PLAYER_STATE_ADDRESS),
                (
                    "stand",
                    town_shop.PLAYER_STATE_ADDRESS,
                    self.MOTION_BLOCK,
                    self.AUX_BLOCK,
                ),
            ],
            "publishing must stop the player into the standing state",
        )
        self.assertEqual(
            memory.read32(
                town_shop.PLAYER_STATE_ADDRESS
                + town_shop.PLAYER_TALK_CLEAR_OFFSET
            ),
            0,
            "the vanilla talk path clears player+0x2C; so must the gate",
        )
        self.assertEqual(
            memory.read16(
                town_shop.TOWN_DIALOGUE_DESCRIPTOR_ADDRESS
                + town_shop.TOWN_DIALOGUE_COLUMN_OFFSET
            ),
            0,
        )
        self.assertEqual(
            memory.read16(
                town_shop.TOWN_DIALOGUE_DESCRIPTOR_ADDRESS
                + town_shop.TOWN_DIALOGUE_ROW_OFFSET
            ),
            0,
        )
        self.assertEqual(
            memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS),
            town_shop.DOOR_GATE_MESSAGE_COOLDOWN_FRAMES,
        )

    def test_cooldown_suppresses_a_second_message(self) -> None:
        result, memory, calls, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
            keycard=2,
            ready=True,
            latch=17,
        )
        self.assertEqual(result, 0)
        self.assertEqual(memory.read32(queue), 0, "no republish under cooldown")
        self.assertEqual(
            memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS),
            17,
            "a ready frame must not tick the cooldown",
        )
        self.assertEqual(self._stop_calls(calls), [], "no second stop either")

    def test_busy_queue_defers_to_the_mailbox(self) -> None:
        result, memory, calls, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
            keycard=2,
            ready=True,
            queued=0x8009_9999,
        )
        self.assertEqual(result, 0)
        self.assertEqual(memory.read32(queue), 0x8009_9999, "never clobber")
        self.assertEqual(
            memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS),
            0,
            "an unshown message must retry next frame",
        )
        self.assertEqual(
            self._stop_calls(calls), [],
            "the player is only stopped when the message actually publishes",
        )

    def test_not_ready_ticks_the_cooldown_down_to_zero(self) -> None:
        result, memory, _, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
            keycard=2,
            ready=False,
            latch=2,
        )
        self.assertEqual(result, 0)
        self.assertEqual(memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS), 1)
        self.assertEqual(memory.read32(queue), 0)

        result, memory, _, _ = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
            keycard=2,
            ready=False,
            latch=0,
        )
        self.assertEqual(result, 0)
        self.assertEqual(
            memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS),
            0,
            "an expired cooldown must not underflow",
        )

    def test_keycard_boundary_across_all_levels(self) -> None:
        for level in range(0, 9):
            result, _, _, _ = self._run(
                record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
                keycard=level,
                ready=True,
            )
            self.assertEqual(
                result,
                self.RESULT_READY
                if level >= town_shop.MONSTER_SHOP_KEYCARD_REQUIREMENT
                else 0,
                f"keycard {level}",
            )

    def test_open_foreign_modal_defers_the_publish(self) -> None:
        """A modal already open belongs to someone else - retry next frame."""

        memory_seed = {town_shop.TOWN_MODAL_ROOT_ADDRESS: 0x8009_1234}
        result, memory, calls, queue = self._run(
            record=town_shop.MONSTER_DOOR_RECORD_ADDRESS,
            keycard=2,
            ready=True,
            extra=memory_seed,
        )
        self.assertEqual(result, 0)
        self.assertEqual(memory.read32(queue), 0, "no publish under a modal")
        self.assertEqual(memory.read32(town_shop.DOOR_GATE_LATCH_ADDRESS), 0)
        self.assertEqual(self._stop_calls(calls), [], "no stop without publish")


class TestMonsterGreetingRemoval(unittest.TestCase):
    def _disc(self):
        if not ORIGINAL_BIN.exists():
            self.skipTest("original disc not present")
        return ORIGINAL_BIN.read_bytes()

    def _town(self, disc, file_offset, length):
        raw = town_shop.mode2_file_offset_to_raw_offset(
            town_shop.TOWN_FILE_START_LBA, file_offset
        )
        return disc[raw:raw + length]

    def test_only_the_four_merchant_greetings_are_touched(self) -> None:
        """The other twelve table entries are story dialogue, not the shop.

        They are told apart by never reaching the shop menu. Overwriting one of
        them would delete a scene, so the count is pinned.
        """

        self.assertEqual(len(town_shop.MONSTER_GREETING_ENTRY_ADDRESSES), 4)
        disc = self._disc()
        for entry in town_shop.MONSTER_GREETING_ENTRY_ADDRESSES:
            offset = town_shop.monster_dialogue_runtime_to_file_offset(entry)
            self.assertEqual(
                self._town(disc, offset, 1)[0],
                0x57,
                f"0x{entry:08x} is not a text window",
            )

    def test_monster_menu_and_kept_pages_survive(self) -> None:
        disc = self._disc()
        menu = town_shop.monster_dialogue_runtime_to_file_offset(
            town_shop.MONSTER_SHOP_MENU_SCRIPT_ADDRESS
        )
        self.assertEqual(
            self._town(disc, menu, 6),
            bytes([0x08, 0x57, 0x01, 0x0B, 0x81, 0x6D]),
        )
        # The Sell retarget must land on `08 clear | 15 call <sell opener>`.
        sell = town_shop.monster_dialogue_runtime_to_file_offset(
            town_shop.MONSTER_SELL_OPEN_ADDRESS
        )
        block = self._town(disc, sell, 6)
        self.assertEqual(block[0], 0x08)
        self.assertEqual(block[1], 0x15)
        self.assertEqual(struct.unpack("<I", block[2:6])[0], 0x8001_8D30)

        written = set()
        for offset, data in town_shop.iter_monster_greeting_file_patches():
            written.update(range(offset, offset + len(data)))
        # Only the Sell slot of the menu's branch table may be written.
        allowed = set(
            range(
                town_shop.monster_dialogue_runtime_to_file_offset(0x8001_A06D),
                town_shop.monster_dialogue_runtime_to_file_offset(0x8001_A06D) + 4,
            )
        )
        self.assertFalse(
            (written & set(range(menu, menu + 0x90))) - allowed,
            "a patch overlaps the Monster menu outside its Sell slot",
        )
        # The Buy slot belongs to the AP script redirect and must not move.
        buy = town_shop.monster_dialogue_runtime_to_file_offset(0x8001_A071)
        self.assertFalse(written & set(range(buy, buy + 4)))
        self.assertEqual(buy, town_shop.MONSTER_BUY_CHOICE_POINTER_FILE_OFFSET)

    def test_the_guy_lockout_substitution_is_removed(self) -> None:
        """One instruction installs the second-talk lockout; nop exactly it."""

        disc = self._disc()
        offset = town_shop.monster_runtime_to_file_offset(
            town_shop.MONSTER_GUY_SUBSTITUTION_ADDRESS
        )
        # The word we replace must be the ADDIU that loads 0x8001A41C, or the
        # overlay has moved and this patch is aimed at the wrong instruction.
        self.assertEqual(
            struct.unpack("<I", self._town(disc, offset, 4))[0],
            town_shop.MONSTER_GUY_SUBSTITUTION_ORIGINAL,
        )
        self.assertEqual(
            town_shop.MONSTER_GUY_SUBSTITUTION_ORIGINAL & 0xFFFF,
            0xA41C,
            "the immediate must be the low half of the Guy block address",
        )

        patches = dict(town_shop.iter_monster_greeting_file_patches())
        self.assertEqual(patches[offset], struct.pack("<I", 0))

        # The Guy block itself is now unreachable and declared free.
        self.assertIn(
            (0x8001_A41C, 0x8001_A4C3, "the 'Your father, Guy...' second-talk lockout"),
            town_shop.MONSTER_DIALOGUE_FREE_SPANS,
        )

    def test_monster_free_spans_are_disjoint_from_every_write(self) -> None:
        written = set()
        for offset, data in town_shop.iter_monster_greeting_file_patches():
            written.update(range(offset, offset + len(data)))
        total = 0
        previous_end = 0
        for start, end, label in town_shop.MONSTER_DIALOGUE_FREE_SPANS:
            self.assertLess(start, end, label)
            self.assertGreater(start, previous_end, f"{label} is out of order")
            previous_end = end
            lo = town_shop.monster_dialogue_runtime_to_file_offset(start)
            hi = town_shop.monster_dialogue_runtime_to_file_offset(end)
            self.assertFalse(written & set(range(lo, hi + 1)), label)
            total += end - start + 1
        self.assertEqual(total, 691, "freed byte count changed")

        for kept in (
            town_shop.MONSTER_SHOP_MENU_SCRIPT_ADDRESS,
            town_shop.MONSTER_SELL_OPEN_ADDRESS,
            0x8001_A16D,  # the sale commit and "(Receives N.)"
            0x8001_A3BC,  # "Thanks.  Come again."
            town_shop.MONSTER_BUY_SCRIPT_ADDRESS,
        ):
            for start, end, label in town_shop.MONSTER_DIALOGUE_FREE_SPANS:
                self.assertFalse(
                    start <= kept <= end,
                    f"0x{kept:08x} is kept but sits inside freed span {label}",
                )


if __name__ == "__main__":
    unittest.main()
