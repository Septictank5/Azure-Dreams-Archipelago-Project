import json
import unittest
from pathlib import Path

from BaseClasses import ItemClassification
from test.general import setup_multiworld

from .. import item_manifest, items, locations, patch
from ..world import AzureDreamsWorld
from .bases import AzureDreamsTestBase

ITEM_DATA = Path(__file__).parents[4] / "data.json"


class TestAzureDreamsWorld(AzureDreamsTestBase):
    def test_pool_shape(self) -> None:
        regular_locations = [location for location in self.world.get_locations() if location.address is not None]
        self.assertEqual(
            len(regular_locations),
            locations.TOWER_LOCATION_COUNT + locations.SHOP_LOCATION_COUNT,
        )
        self.assertEqual(locations.TOWER_LOCATION_COUNT, 78)
        self.assertEqual(locations.SHOP_LOCATION_COUNT, 20)
        self.assertEqual(len(self.multiworld.itempool), 98)
        self.assertEqual(len(self.get_items_by_name(items.PROGRESSIVE_KEYCARD)), 8)
        self.assertTrue(all(item.advancement for item in self.get_items_by_name(items.PROGRESSIVE_KEYCARD)))
        gold = self.get_items_by_name(items.GOLD_PACKAGE)
        self.assertEqual(len(gold), items.GOLD_PACKAGE_COUNT)
        self.assertTrue(
            all(item.code == items.ITEM_ID_BASE + 1 for item in gold),
            "The gold package's protocol id is fixed; the client decodes it "
            "without slot data.",
        )
        # This base is a SOLO room, so there is nobody to send to and the
        # tokens are gated out - see test_send_tokens for both directions.
        self.assertFalse(self.get_items_by_name(items.SEND_TOKEN))
        self.assertEqual(items.ITEM_NAME_TO_ID[items.SEND_TOKEN], items.ITEM_ID_BASE + 2)

        # **Odds, not counts.** Every reward rolls its band independently, so
        # nothing here may assert an exact ball or egg total - a seed that
        # rolled thirty balls is a legitimate seed, and an assertion that
        # forbade it would be asserting the opposite of the design.
        generated = [
            item_manifest.REWARD_BY_NAME[item.name]
            for item in self.multiworld.itempool
            if item.name not in (
                items.PROGRESSIVE_KEYCARD, items.GOLD_PACKAGE, items.SEND_TOKEN
            )
        ]
        self.assertEqual(
            len(generated),
            item_manifest.REWARD_COUNT - items.GOLD_PACKAGE_COUNT,
        )

        # The two floors are the only thing the pool promises. They are
        # repaired after the roll, and they exclude the Monster Shop's own ten
        # items - so they are checked over what is left.
        elsewhere = generated[item_manifest.MONSTER_SHOP_SLOT_COUNT:]
        self.assertGreaterEqual(
            sum(r.category == item_manifest.EGG_CATEGORY for r in elsewhere),
            item_manifest.GUARANTEED_EGGS,
        )
        self.assertGreaterEqual(
            sum(
                r.category == item_manifest.BALL_CATEGORY
                and r.native_item_id in item_manifest.BURN_BALL_IDS
                for r in elsewhere
            ),
            item_manifest.GUARANTEED_BURN_BALLS,
        )
        self.assertFalse(
            [
                r
                for r in generated
                if r.category == item_manifest.EGG_CATEGORY
                and r.native_item_id in item_manifest.EXCLUDED_EGG_IDS
            ]
        )
        # The shop's slot count has to agree with the locations module, which
        # cannot be imported from the manifest without a cycle.
        self.assertEqual(
            item_manifest.MONSTER_SHOP_SLOT_COUNT,
            locations.SHOP_SLOTS_PER_BUILDING,
        )

        # Equipment arrives unidentified; a negative roll is also cursed. Both
        # survive delivery only because the mailbox's presentation marker moved
        # off bit 7 - see MAILBOX_PRESENTATION_FLAG.
        for reward in generated:
            if reward.category not in item_manifest.EQUIPMENT_CATEGORIES:
                self.assertEqual(reward.flags, 0, reward.name)
                continue
            self.assertTrue(reward.flags & item_manifest.FLAG_UNIDENTIFIED, reward.name)
            self.assertEqual(
                bool(reward.flags & item_manifest.FLAG_CURSED),
                reward.quality < 0,
                reward.name,
            )
            # 0x20 is "equipped" - nothing granted may arrive already worn.
            self.assertFalse(reward.flags & 0x20, reward.name)

    def test_native_reward_manifest(self) -> None:
        rewards = item_manifest.NATIVE_REWARDS
        self.assertEqual(len(rewards), len({reward.name for reward in rewards}))
        self.assertEqual(len(rewards), len({reward.protocol_item_id for reward in rewards}))
        self.assertTrue(all(reward.category not in {11, 13, 14} for reward in rewards))
        self.assertNotIn("Ultimate Egg", item_manifest.REWARD_BY_NAME)
        self.assertNotIn(items.ITEM_ID_BASE + 1, item_manifest.REWARD_BY_ID)

        # The Oleem is the one special-category item that behaves like ordinary
        # inventory; the rest of category 12 stays out with gifts and coins.
        oleem = item_manifest.REWARD_BY_NAME["Oleem"]
        self.assertEqual(oleem.descriptor, bytes((9, 12, 0, 0)))
        self.assertEqual(
            [reward.name for reward in rewards if reward.category == 12],
            ["Oleem"],
        )

        # Quest stand-ins and no-op tools a player can never use.
        for excluded in ("Healing Herb", "Oleem Fruit", "Focus Loupe"):
            self.assertNotIn(excluded, item_manifest.REWARD_BY_NAME)

        acid_rain = item_manifest.REWARD_BY_NAME["Acid Rain Ball"]
        self.assertEqual(acid_rain.descriptor, bytes((17, 4, 1, 0)))

        ten_charge_fire = item_manifest.REWARD_BY_NAME["Fire Ball (10)"]
        self.assertEqual(ten_charge_fire.descriptor, bytes((1, 4, 10, 0)))

        dragon_egg = item_manifest.REWARD_BY_NAME["Dragon Egg"]
        self.assertEqual(dragon_egg.descriptor, bytes((3, 18, 20, 0)))
        # Flat: which egg you get is not weighted any more.
        self.assertEqual(
            {reward.weight for reward in item_manifest.EGG_REWARDS},
            {item_manifest.DEFAULT_WEIGHT},
        )

        # Kewne is the starting familiar and Ultimate is not in the catalog at
        # all, so neither egg can be placed.
        self.assertNotIn("Kewne Egg", item_manifest.REWARD_BY_NAME)
        self.assertFalse(
            [
                reward
                for reward in rewards
                if reward.category == item_manifest.EGG_CATEGORY
                and reward.native_item_id in item_manifest.EXCLUDED_EGG_IDS
            ]
        )

        # Signed quality and equipment flags ride in bits 16-18, which the
        # client decodes; an ID with all three clear must still match what the
        # earlier sixteen-bit layout produced for the same item.
        plain = item_manifest.REWARD_BY_NAME["Copper Sword"]
        cursed = item_manifest.REWARD_BY_NAME["Copper Sword (-1)"]
        self.assertEqual(plain.descriptor, bytes((2, 15, 0, 0x80)))
        self.assertEqual(cursed.descriptor, bytes((2, 15, 0xFF, 0xC0)))
        self.assertNotEqual(plain.protocol_item_id, cursed.protocol_item_id)

        # The golden vector the client's self-test pins, so the two encoders
        # cannot drift.
        self.assertEqual(
            item_manifest.native_protocol_item_id(15, 2, 0, 0x80), 0x0AD2_7840
        )
        self.assertEqual(
            item_manifest.native_protocol_item_id(15, 2, -1, 0xC0), 0x0AD7_7841
        )
        self.assertEqual(
            item_manifest.native_protocol_item_id(4, 1, 10),
            item_manifest.ITEM_ID_BASE | (4 << 11) | (1 << 5) | 10,
        )

        # Presentation must live outside the descriptor entirely. While it rode
        # in the flags byte it overwrote the item's own unidentified bit, and
        # the dispatcher then stripped that bit on every delivery.
        self.assertEqual(patch.MAILBOX_RECEIVE_PRESENTATION_OFFSET, 0xAC)
        self.assertGreaterEqual(
            patch.MAILBOX_RECEIVE_PRESENTATION_OFFSET, 0xAC
        )  # past the last descriptor/status field

        # An unidentified item must not announce its quality in any string the
        # GAME renders - the floor message and the at-feet menu both come from
        # this. Keyed on the flag, not the category, so it follows whatever
        # carries FLAG_UNIDENTIFIED rather than needing a list kept in step.
        self.assertEqual(
            item_manifest.display_name_for("Vital Sword (-1)"), "Vital Sword"
        )
        self.assertEqual(
            item_manifest.display_name_for("Copper Sword (+2)"), "Copper Sword"
        )
        # Balls are handed over identified, so their charge count is theirs to
        # show. Make them unidentified and this drops with no change here.
        self.assertEqual(
            item_manifest.display_name_for("Fire Ball (10)"), "Fire Ball (10)"
        )
        # Another world's item tells us nothing; it passes through untouched.
        self.assertEqual(
            item_manifest.display_name_for("Master Sword"), "Master Sword"
        )
        for reward in rewards:
            expected = (
                reward.base_name
                if reward.flags & item_manifest.FLAG_UNIDENTIFIED
                else reward.name
            )
            self.assertEqual(reward.display_name, expected, reward.name)

        # Only the Trained Wand tempers; the rest can be cursed but never plus.
        wand_qualities = {
            reward.quality
            for reward in rewards
            if reward.category == item_manifest.WAND_CATEGORY
            and reward.native_item_id != item_manifest.TEMPERABLE_WAND_ID
        }
        self.assertEqual(wand_qualities, {0, -1})

        # The keycard is the only progression item and nothing else is ranked
        # above anything else, so every reward is filler (and every trap is a
        # trap - the classification AP's fill and other players' trap-fill
        # settings understand). Marking rewards useful instead would make
        # `exclude_locations` unfillable.
        self.assertEqual(
            {
                classification
                for name, classification in items.ITEM_CLASSIFICATIONS.items()
                if name != items.PROGRESSIVE_KEYCARD
                and not items.is_trap_name(name)
            },
            {ItemClassification.filler},
        )
        self.assertEqual(
            {
                classification
                for name, classification in items.ITEM_CLASSIFICATIONS.items()
                if items.is_trap_name(name)
            },
            {ItemClassification.trap},
        )
        self.assertEqual(
            items.ITEM_CLASSIFICATIONS[items.PROGRESSIVE_KEYCARD],
            ItemClassification.progression,
        )

    def test_every_reward_is_deliverable_by_the_client(self) -> None:
        """The client bounds delivery by data.json, not by its own item list.

        That only works while data.json is a superset of what generation can
        place: an item missing here is one the player is told they received
        and never gets. This is the check that has to fail loudly, because the
        client's own version of it deliberately no longer exists.
        """

        catalog = json.loads(ITEM_DATA.read_text(encoding="utf-8"))
        known = {
            (int(category), int(native_item_id))
            for category, entries in catalog.items()
            for native_item_id in entries
            if native_item_id.isdigit()
        }

        for reward in item_manifest.NATIVE_REWARDS:
            key = (reward.category, reward.native_item_id)
            self.assertIn(key, known, f"{reward.name} is absent from data.json.")

    def test_location_ids_match_mailbox_bits(self) -> None:
        self.assertEqual(locations.tower_location_id(1, 0), locations.LOCATION_ID_BASE)
        self.assertEqual(locations.tower_location_id(1, 1), locations.LOCATION_ID_BASE + 1)
        self.assertEqual(locations.tower_location_id(39, 1), locations.LOCATION_ID_BASE + 77)
        self.assertEqual(locations.shop_location_id(0), locations.SHOP_LOCATION_ID_BASE)
        self.assertEqual(locations.shop_location_id(9), locations.SHOP_LOCATION_ID_BASE + 9)
        self.assertEqual(locations.shop_location_id(10), locations.SHOP_LOCATION_ID_BASE + 10)
        self.assertEqual(locations.shop_location_id(19), locations.SHOP_LOCATION_ID_BASE + 19)
        self.assertEqual(locations.shop_location_name(9), "Equipment Shop - Slot 10")
        self.assertEqual(locations.shop_location_name(10), "Monster Shop - Slot 01")
        self.assertTrue(
            self.world.get_location(locations.shop_location_name(0)).can_reach(
                self.multiworld.state
            )
        )
        self.assertNotIn("Tower Floor 40 - Slot 1", self.world.location_name_to_id)

    def test_gold_packages_never_reach_the_shop_shelves(self) -> None:
        """A shop check's item is displayed as merchandise, so gold for sale
        is money printing (own world) or buying money with money (any other
        Azure Dreams world). The tower and every other game stay fair."""

        gold = self.world.create_item(items.GOLD_PACKAGE)
        native = next(
            item
            for item in self.multiworld.itempool
            if item.name not in (items.PROGRESSIVE_KEYCARD, items.GOLD_PACKAGE)
        )
        for slot in range(locations.SHOP_LOCATION_COUNT):
            location = self.world.get_location(locations.shop_location_name(slot))
            self.assertFalse(
                location.item_rule(gold),
                f"{location.name} accepted a gold package.",
            )
            self.assertTrue(
                location.item_rule(native),
                f"{location.name} refused an ordinary native reward.",
            )
        tower_location = self.world.get_location(
            locations.tower_location_name(1, 0)
        )
        self.assertTrue(
            tower_location.item_rule(gold),
            "The tower must accept gold packages.",
        )

    def test_slot_data_advertises_town_receive_protocol_generation(self) -> None:
        slot_data = self.world.fill_slot_data()
        self.assertEqual(slot_data["apworld_version"], 16)
        self.assertEqual(
            slot_data["shop_location_id_base"],
            locations.SHOP_LOCATION_ID_BASE,
        )
        self.assertEqual(
            slot_data["shop_location_count"],
            locations.SHOP_LOCATION_COUNT,
        )
        self.assertEqual(
            slot_data["persistent_shop_mask_address"],
            0x8001_5FE4,
        )

    def test_progressive_floor_gates(self) -> None:
        state = self.multiworld.state
        keycards = self.get_items_by_name(items.PROGRESSIVE_KEYCARD)

        self.assertTrue(self.world.get_location(locations.tower_location_name(4, 1)).can_reach(state))
        self.assertFalse(self.world.get_location(locations.tower_location_name(5, 0)).can_reach(state))

        for keycard_count in range(1, 8):
            self.collect(keycards[keycard_count - 1])
            highest_reachable_floor = keycard_count * 5 + 4
            self.assertTrue(
                self.world.get_location(locations.tower_location_name(highest_reachable_floor, 1)).can_reach(state)
            )
            if highest_reachable_floor < locations.TOWER_FLOOR_COUNT:
                self.assertFalse(
                    self.world.get_location(
                        locations.tower_location_name(highest_reachable_floor + 1, 0)
                    ).can_reach(state)
                )

        ultimate_egg = self.world.get_location(locations.ULTIMATE_EGG_LOCATION)
        self.assertFalse(ultimate_egg.can_reach(state))
        self.collect(keycards[7])
        self.assertTrue(ultimate_egg.can_reach(state))

    def test_monster_shop_requires_three_progressive_keycards(self) -> None:
        state = self.multiworld.state
        keycards = self.get_items_by_name(items.PROGRESSIVE_KEYCARD)
        equipment = self.world.get_location(locations.shop_location_name(0))
        monster = self.world.get_location(
            locations.shop_location_name(locations.SHOP_SLOTS_PER_BUILDING)
        )

        self.assertTrue(equipment.can_reach(state))
        self.assertFalse(monster.can_reach(state))
        self.collect(keycards[:2])
        self.assertFalse(monster.can_reach(state))
        self.collect(keycards[2])
        self.assertTrue(monster.can_reach(state))

    def test_ultimate_egg_event_completes_world(self) -> None:
        keycards = self.get_items_by_name(items.PROGRESSIVE_KEYCARD)
        completion_condition = self.multiworld.completion_condition[self.player]
        self.collect(keycards[:7])
        self.assertFalse(completion_condition(self.multiworld.state))

        ultimate_egg = self.world.get_location(locations.ULTIMATE_EGG_LOCATION)
        self.assertFalse(ultimate_egg.can_reach(self.multiworld.state))

        # Archipelago sweeps a reachable locked event automatically. The real
        # client still reports CLIENT_GOAL only after observing the in-game egg.
        self.collect(keycards[7])
        self.assertTrue(ultimate_egg.can_reach(self.multiworld.state))
        self.assertTrue(completion_condition(self.multiworld.state))


class TestFloorKeycardOwnership(unittest.TestCase):
    """A floor is only marked as holding progress for the player standing on it.

    The elevator withholds Return to Town at a clearance ceiling when the floor
    still holds a keycard. Marking a floor for someone else's keycard stranded
    a player there in a real two-player run: nothing on that floor could raise
    their clearance, so the ascent stayed refused and the way down never
    appeared. Only a Wind Crystal got them out.
    """

    def setUp(self) -> None:
        self.multiworld = setup_multiworld([AzureDreamsWorld, AzureDreamsWorld])
        self.mine = self.multiworld.worlds[1]
        self.theirs = self.multiworld.worlds[2]

    def test_only_the_local_players_keycard_counts_as_progress(self) -> None:
        own = self.mine.create_item(items.PROGRESSIVE_KEYCARD)
        foreign = self.theirs.create_item(items.PROGRESSIVE_KEYCARD)

        # Same game, same item name, same everything but the owner.
        self.assertEqual(own.name, foreign.name)
        self.assertEqual(own.game, foreign.game)

        self.assertTrue(self.mine._is_own_keycard(own))
        self.assertFalse(self.mine._is_own_keycard(foreign))
        self.assertTrue(self.theirs._is_own_keycard(foreign))
        self.assertFalse(self.theirs._is_own_keycard(own))

    def test_an_ordinary_item_is_never_progress(self) -> None:
        self.assertFalse(
            self.mine._is_own_keycard(self.mine.create_item("Pita Fruit"))
        )

    def test_a_foreign_keycard_leaves_its_floor_unmarked(self) -> None:
        """The bit the elevator actually reads, end of the chain."""

        # Placement index 6 is floor 4, slot 1 - so bit 3 of the first mask byte.
        floor_four = 0b1000

        def mask_for(owner: AzureDreamsWorld, recipient: str) -> int:
            keycard = owner.create_item(items.PROGRESSIVE_KEYCARD)
            placements = [
                patch.LocationPlacement("Gold", "Tester1", False) for _ in range(78)
            ]
            placements[6] = patch.LocationPlacement(
                keycard.name,
                recipient,
                remote=owner is not self.mine,
                progressive_keycard=self.mine._is_own_keycard(keycard),
            )
            block = patch.build_seed_block(b"12345678", placements)
            return block[patch.FLOOR_KEYCARD_MASK_OFFSET] & 0b1111

        self.assertEqual(mask_for(self.theirs, "Tester2"), 0)
        self.assertEqual(mask_for(self.mine, "Tester1"), floor_four)
