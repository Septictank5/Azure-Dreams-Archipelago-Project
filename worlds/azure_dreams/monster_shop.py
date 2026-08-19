"""Shaping the Monster Shop's ten slots after the fill has run.

The Monster Shop is the one location group with a theme. The tower and the
Equipment Shop take whatever the flat pool hands them; the Monster Shop is
weighted toward what a familiar wants, so that a player who has climbed to
keycard 3 and opened it finds something worth having opened.

**Why this runs in `post_fill` and not `pre_fill`.** Placing the ten slots
before the fill would be exact and immune to everything downstream, but it
would also take ten locations away from keycard placement. Keycards are meant
to land anywhere, chosen by Archipelago's own progression fill with the
player's `progression_balancing` applied; that matters more than hitting the
shop's ratios to the item. So the fill runs first and this rearranges what it
produced, swapping only between locations that hold non-progression items.

**One consequence, accepted.** `balance_multiworld_progression` runs *after*
`post_fill` (`Main.py`), so in a multiplayer seed with balancing enabled it can
drop a progression item into a shop slot and displace one of these. That costs
a slot or two of theme, not correctness, and it is the price of the paragraph
above.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import ItemClassification, Location

from . import item_manifest, locations

if TYPE_CHECKING:
    from .world import AzureDreamsWorld


# The odds themselves live in `item_manifest` beside the pool's own, because
# `create_all_items` rolls this plan when it builds the pool - see below.
SLOT_COUNT = item_manifest.MONSTER_SHOP_SLOT_COUNT


def _is_swappable(location: Location) -> bool:
    """Whether this location's item may be moved.

    Progression and locked placements are off limits: moving a keycard would
    invalidate the reachability the fill just proved, and this pass has no way
    to re-prove it.
    """

    return (
        location.item is not None
        and not location.locked
        and location.item.classification == ItemClassification.filler
    )


def _monster_shop_locations(world: AzureDreamsWorld) -> list[Location]:
    first = locations.SHOP_SLOTS_PER_BUILDING
    return [
        world.get_location(locations.shop_location_name(slot))
        for slot in range(first, locations.SHOP_LOCATION_COUNT)
    ]


def _guarded_locations(world: AzureDreamsWorld) -> list[Location]:
    """Everything the two guarantees are measured over: tower and Equipment Shop.

    The Monster Shop runs its own draw and is deliberately not counted, so a
    themed shop cannot satisfy - or consume - the eggs and burn balls the rest
    of the seed is promised.
    """

    result = [
        world.get_location(locations.shop_location_name(slot))
        for slot in range(locations.SHOP_SLOTS_PER_BUILDING)
    ]
    result += [
        world.get_location(locations.tower_location_name(floor, slot))
        for floor in range(1, locations.TOWER_FLOOR_COUNT + 1)
        for slot in range(locations.tower_slots_for(world))
    ]
    return result


def _reward_of(location: Location, world: AzureDreamsWorld):
    """The native reward a location holds, or None if it is not one of ours."""

    if location.item is None or location.item.player != world.player:
        return None
    return item_manifest.REWARD_BY_NAME.get(location.item.name)


def _swap(first: Location, second: Location) -> None:
    first.item, second.item = second.item, first.item


def _take(
    world: AzureDreamsWorld,
    target: Location,
    donors: list[Location],
    matches,
) -> bool:
    """Swaps a `matches` item from `donors` into `target`. False if none exists."""

    for donor in donors:
        if donor is target or not _is_swappable(donor):
            continue
        reward = _reward_of(donor, world)
        if reward is None or not matches(reward):
            continue
        _swap(target, donor)
        donors.remove(donor)
        return True
    return False


def _matcher(want: str):
    if want == "egg":
        return lambda reward: reward.category == item_manifest.EGG_CATEGORY
    if want == "familiar":
        return lambda reward: reward.name in item_manifest.FAMILIAR_REWARD_NAME_SET
    return lambda reward: reward.name == item_manifest.ROCHE_FRUIT_NAME


def shape_monster_shop(world: AzureDreamsWorld) -> None:
    """Applies the plan `create_all_items` rolled, once the fill has run.

    The plan is not re-rolled here. It was rolled when the pool was built and
    the pool was drawn to satisfy it, so every band this walks through has a
    matching item somewhere in the seed to go and fetch. Rolling again would
    ask for items nobody was told to create.

    Every move is a swap between two locations already holding filler, so the
    multiworld's item set is unchanged and nothing that gates progress moves. A
    slot the fill gave a keycard is skipped - its planned item simply stays
    wherever it landed, which is the price of letting keycards go anywhere.
    """

    plan = getattr(world, "monster_shop_plan", None)
    if not plan:
        return

    shop = _monster_shop_locations(world)

    # Donors are everywhere else this player owns. Shuffled so the items pulled
    # into the shop are not always the lowest-numbered floors.
    donors = [
        location
        for location in world.multiworld.get_filled_locations(world.player)
        if location not in shop and _is_swappable(location)
    ]
    world.random.shuffle(donors)

    for slot, want in zip(shop, plan):
        if want is None or not _is_swappable(slot):
            continue
        matches = _matcher(want)
        held = _reward_of(slot, world)
        # Already what the plan asked for; swapping would only churn.
        if held is not None and matches(held):
            continue
        _take(world, slot, donors, matches)

    _restore_guarantees(world)


def _restore_guarantees(world: AzureDreamsWorld) -> None:
    """Puts the eggs and burn balls back if shaping ate them.

    The shop pulls from the same pool the guarantees are measured over, so
    taking two eggs into the Monster Shop can leave the tower and Equipment
    Shop below the two they are promised. This is the end swap that fixes it:
    it trades back from the Monster Shop's own untouched slots, which is the
    only place the missing items can have gone.
    """

    guarded = [
        location for location in _guarded_locations(world) if location.item is not None
    ]
    shop = _monster_shop_locations(world)

    def count(where, matches) -> int:
        total = 0
        for location in where:
            reward = _reward_of(location, world)
            if reward is not None and matches(reward):
                total += 1
        return total

    def is_egg(reward) -> bool:
        return reward.category == item_manifest.EGG_CATEGORY

    def is_burn_ball(reward) -> bool:
        return (
            reward.category == item_manifest.BALL_CATEGORY
            and reward.native_item_id in item_manifest.BURN_BALL_IDS
        )

    for matches, required in (
        (is_egg, item_manifest.GUARANTEED_EGGS),
        (is_burn_ball, item_manifest.GUARANTEED_BURN_BALLS),
    ):
        shortfall = required - count(guarded, matches)
        if shortfall <= 0:
            continue
        # Only surplus copies may come back: the shop's own share is not a
        # donor for the guarantee it is exempt from.
        surplus = [
            location
            for location in shop
            if _is_swappable(location)
            and (reward := _reward_of(location, world)) is not None
            and matches(reward)
        ]
        recipients = [
            location
            for location in guarded
            if _is_swappable(location)
            and (reward := _reward_of(location, world)) is not None
            and not matches(reward)
        ]
        world.random.shuffle(recipients)
        while shortfall > 0 and surplus and recipients:
            _swap(surplus.pop(), recipients.pop())
            shortfall -= 1
