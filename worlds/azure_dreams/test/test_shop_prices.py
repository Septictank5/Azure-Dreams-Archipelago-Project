import random
import unittest

from BaseClasses import Item, ItemClassification

from .. import locations, shop_prices


EQUIPMENT_SLOT = 0
MONSTER_SLOT = locations.SHOP_SLOTS_PER_BUILDING

ALL_BANDS = (
    shop_prices.PROGRESSION_BAND,
    shop_prices.MINOR_PROGRESSION_BAND,
    shop_prices.USEFUL_BAND,
    shop_prices.FILLER_BAND,
)

EVERY_CLASSIFICATION = (
    ItemClassification.progression,
    ItemClassification.progression_skip_balancing,
    ItemClassification.useful,
    ItemClassification.filler,
    ItemClassification.trap,
)


def _item(classification: ItemClassification) -> Item:
    return Item("Test Item", classification, None, 1)


class TestShopPrices(unittest.TestCase):
    def test_bands_are_ordered_and_positive(self) -> None:
        for low, high in ALL_BANDS:
            self.assertLess(low, high)
            self.assertGreater(low, 0)

    def test_classification_selects_the_band(self) -> None:
        self.assertEqual(
            shop_prices.price_band(_item(ItemClassification.progression)),
            shop_prices.PROGRESSION_BAND,
        )
        self.assertEqual(
            shop_prices.price_band(_item(ItemClassification.useful)),
            shop_prices.USEFUL_BAND,
        )
        self.assertEqual(
            shop_prices.price_band(_item(ItemClassification.filler)),
            shop_prices.FILLER_BAND,
        )
        self.assertEqual(
            shop_prices.price_band(_item(ItemClassification.trap)),
            shop_prices.FILLER_BAND,
        )

    def test_insignificant_progression_undercuts_a_real_gate(self) -> None:
        # progression_skip_balancing still reports advancement, so the narrower
        # test has to win or a source world's filler keys price as gates.
        minor = _item(ItemClassification.progression_skip_balancing)
        self.assertTrue(minor.advancement)
        self.assertEqual(
            shop_prices.price_band(minor), shop_prices.MINOR_PROGRESSION_BAND
        )

        low, high = shop_prices.price_band(minor)
        gate_low, gate_high = shop_prices.PROGRESSION_BAND
        self.assertLess(low, gate_low)
        self.assertLess(high, gate_high)

    def test_useful_progression_prices_as_progression(self) -> None:
        self.assertEqual(
            shop_prices.price_band(
                _item(ItemClassification.progression | ItemClassification.useful)
            ),
            shop_prices.PROGRESSION_BAND,
        )

    def test_prices_stay_inside_their_shop_band(self) -> None:
        rng = random.Random(0xAD)
        for classification in EVERY_CLASSIFICATION:
            item = _item(classification)
            low, high = shop_prices.price_band(item)
            for slot in range(locations.SHOP_LOCATION_COUNT):
                scale = (
                    shop_prices.MONSTER_SHOP_MULTIPLIER
                    if slot >= locations.SHOP_SLOTS_PER_BUILDING
                    else 1
                )
                price = shop_prices.shop_slot_price(rng, slot, item)
                self.assertGreaterEqual(price, low * scale)
                self.assertLessEqual(price, high * scale)

    def test_the_monster_shop_scales_both_ends_of_the_band(self) -> None:
        rng = random.Random(7)
        for classification in EVERY_CLASSIFICATION:
            item = _item(classification)
            low, high = shop_prices.price_band(item)
            observed = [
                shop_prices.shop_slot_price(rng, MONSTER_SLOT, item)
                for _ in range(400)
            ]
            self.assertGreaterEqual(min(observed), low * 10)
            self.assertLessEqual(max(observed), high * 10)
            # The scaled band is 10x wider, so the roll has to actually use it
            # rather than multiplying an Equipment Shop price by ten.
            self.assertGreater(max(observed) - min(observed), (high - low) * 5)

    def test_prices_are_not_rounded(self) -> None:
        """The whole point of the change: 869, not 850 and not 900."""

        rng = random.Random(0xC0FFEE)
        equipment = [
            shop_prices.shop_slot_price(
                rng, EQUIPMENT_SLOT, _item(ItemClassification.progression)
            )
            for _ in range(200)
        ]
        monster = [
            shop_prices.shop_slot_price(
                rng, MONSTER_SLOT, _item(ItemClassification.progression)
            )
            for _ in range(200)
        ]
        for prices in (equipment, monster):
            self.assertTrue(any(price % 10 for price in prices))
            self.assertTrue(any(price % 50 for price in prices))
            self.assertGreater(len(set(prices)), 100)
