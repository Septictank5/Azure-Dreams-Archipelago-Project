from __future__ import annotations

from typing import TYPE_CHECKING

from . import locations

if TYPE_CHECKING:
    from random import Random

    from BaseClasses import Item


# Equipment Shop gold bands, inclusive at both ends. This shop is open before
# the first tower trip and is the one a player is forced back to when they run
# out of cards on floor 5, so nothing here may cost more than an unlucky short
# run can raise.
#
# Pricing by importance costs nothing in secrecy: every slot already prints
# what it holds and who it is for (see town_shop.format_shop_description), so
# the band only makes the price agree with what the buyer can already read.
PROGRESSION_BAND = (1000, 2500)
MINOR_PROGRESSION_BAND = (900, 2000)
USEFUL_BAND = (700, 1800)
FILLER_BAND = (350, 1000)

# The Monster Shop is three keycards deep, far enough in that a single Dark
# Sword sale covers a slot outright. It scales both ends of the band rather
# than the roll, so its prices are as unrounded as the Equipment Shop's.
MONSTER_SHOP_MULTIPLIER = 10


def price_band(item: Item) -> tuple[int, int]:
    """The Equipment Shop band an item's Archipelago classification earns.

    Tested narrowest first: a source world marks its own insignificant
    progression as progression_skip_balancing, which still reports
    `advancement`, and would otherwise be priced as a full gate.
    """

    if item.skip_in_prog_balancing:
        return MINOR_PROGRESSION_BAND
    if item.advancement:
        return PROGRESSION_BAND
    if item.useful:
        return USEFUL_BAND
    return FILLER_BAND


def shop_slot_price(random: Random, slot: int, item: Item) -> int:
    """Any gold value in the band, not a round one.

    Prices used to be multiples of 100 and read like a price list. Every value
    in range is allowed instead, so a slot can cost 869 - the ragged number is
    the point, and it is what stops the shelf looking generated.
    """

    low, high = price_band(item)
    if slot >= locations.SHOP_SLOTS_PER_BUILDING:
        low *= MONSTER_SHOP_MULTIPLIER
        high *= MONSTER_SHOP_MULTIPLIER
    return random.randint(low, high)
