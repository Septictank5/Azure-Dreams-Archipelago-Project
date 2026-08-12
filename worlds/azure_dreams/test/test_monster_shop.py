from collections import Counter

from BaseClasses import ItemClassification
from Fill import distribute_items_restrictive

from .. import item_manifest, items, locations, monster_shop
from .bases import AzureDreamsTestBase


class TestMonsterShopShaping(AzureDreamsTestBase):
    """The Monster Shop's theme, and the things it is not allowed to cost.

    `WorldTestBase` stops after item creation, so each of these runs the real
    fill and then the real `post_fill` - the shaper only has anything to do
    once locations actually hold items.
    """

    def _fill(self) -> None:
        distribute_items_restrictive(self.multiworld)
        self.world.post_fill()

    def _monster_shop(self):
        return [
            self.multiworld.get_location(locations.shop_location_name(slot), 1)
            for slot in range(
                locations.SHOP_SLOTS_PER_BUILDING, locations.SHOP_LOCATION_COUNT
            )
        ]

    def _guarded(self):
        result = [
            self.multiworld.get_location(locations.shop_location_name(slot), 1)
            for slot in range(locations.SHOP_SLOTS_PER_BUILDING)
        ]
        result += [
            self.multiworld.get_location(
                locations.tower_location_name(floor, slot), 1
            )
            for floor in range(1, locations.TOWER_FLOOR_COUNT + 1)
            for slot in range(locations.TOWER_SLOTS_PER_FLOOR)
        ]
        return result

    @staticmethod
    def _rewards(placed):
        for location in placed:
            if location.item is None:
                continue
            reward = item_manifest.REWARD_BY_NAME.get(location.item.name)
            if reward is not None:
                yield reward

    def test_the_slot_roll_is_odds_and_not_a_ratio(self) -> None:
        """One slot, one independent roll - so an empty shop stays possible.

        Fixed counts would make every shop the same shape, which is not what a
        45% chance means. This pins the band arithmetic directly rather than
        through a generation, so it says what it means.
        """

        class Fixed:
            def __init__(self, value: int) -> None:
                self.value = value

            def randrange(self, _stop: int) -> int:
                return self.value

        # Every band edge, in basis points. The 6200 case is the one that broke
        # while the roll was a float: subtracting the shares left the familiar
        # edge at 0.44999999999999996 and swallowed the first "leave it alone".
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(0)), "roche")
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(199)), "roche")
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(200)), "egg")
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(1699)), "egg")
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(1700)), "familiar")
        self.assertEqual(item_manifest.roll_monster_shop_slot(Fixed(6199)), "familiar")
        # The remaining 38% keeps whatever the flat global pool gave it.
        self.assertIsNone(item_manifest.roll_monster_shop_slot(Fixed(6200)))
        self.assertIsNone(item_manifest.roll_monster_shop_slot(Fixed(item_manifest.BASIS_POINTS - 1)))

        # And the bands really are 45/15/2, leaving 38 alone.
        counts = Counter(
            item_manifest.roll_monster_shop_slot(Fixed(roll))
            for roll in range(item_manifest.BASIS_POINTS)
        )
        self.assertEqual(counts["familiar"], 4500)
        self.assertEqual(counts["egg"], 1500)
        self.assertEqual(counts["roche"], 200)
        self.assertEqual(counts[None], 3800)

    def test_the_shop_reads_as_themed_on_average(self) -> None:
        """The odds have to actually land, which one seed cannot show.

        Deliberately a mean over many seeds rather than a bound on one: a shop
        with no familiar items in it is a legitimate 0.45 failing ten times, and
        a test that forbade it would be testing the wrong thing. The seeds are
        fixed, so this is reproducible rather than flaky.
        """

        familiar_total = 0
        egg_total = 0
        shops = 0
        for seed in range(4000, 4030):
            self.world_setup(seed)
            self._fill()
            rewards = list(self._rewards(self._monster_shop()))
            familiar_total += sum(
                reward.name in item_manifest.FAMILIAR_REWARD_NAME_SET
                for reward in rewards
            )
            egg_total += sum(
                reward.category == item_manifest.EGG_CATEGORY for reward in rewards
            )
            shops += 1

        slots = locations.SHOP_SLOTS_PER_BUILDING
        familiar_rate = familiar_total / (shops * slots)
        egg_rate = egg_total / (shops * slots)
        # Wide bounds on purpose. Keycards occupy shop slots sometimes and a
        # bare cupboard drops the odd slot, so the realised rate sits at or a
        # little under the target; this is here to catch a shaper that stopped
        # working, not to pin a distribution to three decimals.
        self.assertGreater(familiar_rate, item_manifest.SHOP_FAMILIAR_CHANCE - 0.12)
        self.assertLess(familiar_rate, item_manifest.SHOP_FAMILIAR_CHANCE + 0.12)
        self.assertGreater(egg_rate, item_manifest.SHOP_EGG_CHANCE - 0.10)
        self.assertLess(egg_rate, item_manifest.SHOP_EGG_CHANCE + 0.10)

    def test_shaping_moves_nothing_that_gates_progress(self) -> None:
        distribute_items_restrictive(self.multiworld)
        before = {
            location.name: location.item.name
            for location in self.multiworld.get_filled_locations(1)
            if location.item.classification != ItemClassification.filler
        }
        self.world.post_fill()
        after = {
            location.name: location.item.name
            for location in self.multiworld.get_filled_locations(1)
            if location.item.classification != ItemClassification.filler
        }
        self.assertEqual(before, after)

    def test_shaping_neither_creates_nor_destroys_items(self) -> None:
        distribute_items_restrictive(self.multiworld)
        before = Counter(
            location.item.name
            for location in self.multiworld.get_filled_locations(1)
        )
        self.world.post_fill()
        after = Counter(
            location.item.name
            for location in self.multiworld.get_filled_locations(1)
        )
        self.assertEqual(before, after)

    def test_the_guarantees_survive_a_themed_shop(self) -> None:
        """The shop draws eggs from the same pool the guarantee is measured over.

        Without the end swap in `_restore_guarantees`, a shop that took two
        eggs could leave the tower and Equipment Shop with none - which is
        exactly the case the guarantee exists to prevent.
        """

        self._fill()
        guarded = list(self._rewards(self._guarded()))
        self.assertGreaterEqual(
            sum(reward.category == item_manifest.EGG_CATEGORY for reward in guarded),
            item_manifest.GUARANTEED_EGGS,
        )
        self.assertGreaterEqual(
            sum(
                reward.category == item_manifest.BALL_CATEGORY
                and reward.native_item_id in item_manifest.BURN_BALL_IDS
                for reward in guarded
            ),
            item_manifest.GUARANTEED_BURN_BALLS,
        )

    def test_every_teaching_stack_is_a_ten_charge_ball(self) -> None:
        """The familiar set's ball half, pinned.

        Restricting the charge ladder to the six balls that were good to throw
        denied a teaching stack to the balls whose only real use IS teaching -
        Weak Ball above all. Every ball but Acid Rain must reach ten.
        """

        laddered = {
            reward.native_item_id
            for reward in item_manifest.BALL_REWARDS
            if reward.quality == item_manifest.TEACHING_CHARGES
        }
        all_balls = {
            reward.native_item_id for reward in item_manifest.BALL_REWARDS
        }
        self.assertEqual(
            laddered, all_balls - {item_manifest.ACID_RAIN_BALL_ID}
        )
        self.assertTrue(
            all(
                reward.quality == item_manifest.ACID_RAIN_CHARGES
                for reward in item_manifest.BALL_REWARDS
                if reward.native_item_id == item_manifest.ACID_RAIN_BALL_ID
            )
        )
        # And the teaching stacks are exactly the ball half of the familiar set.
        self.assertTrue(
            item_manifest.FAMILIAR_REWARD_NAME_SET.issuperset(
                reward.name for reward in item_manifest.TEACHING_BALL_REWARDS
            )
        )
        # Roche is deliberately outside the familiar set; it has its own slice.
        self.assertNotIn(
            item_manifest.ROCHE_FRUIT_NAME, item_manifest.FAMILIAR_REWARD_NAME_SET
        )
