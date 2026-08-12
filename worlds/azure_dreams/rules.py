from __future__ import annotations

from typing import TYPE_CHECKING

from worlds.generic.Rules import add_item_rule, set_rule

from . import items, locations, regions

if TYPE_CHECKING:
    from .world import AzureDreamsWorld


def set_all_rules(world: AzureDreamsWorld) -> None:
    player = world.player

    for first_floor, last_floor, keycard_count in regions.TOWER_BANDS:
        if keycard_count == 0:
            continue
        entrance = world.get_entrance(regions.tower_entrance_name(first_floor, last_floor))
        set_rule(
            entrance,
            lambda state, count=keycard_count: state.has(items.PROGRESSIVE_KEYCARD, player, count),
        )

    monster_first_slot = locations.SHOP_SLOTS_PER_BUILDING
    for slot in range(monster_first_slot, locations.SHOP_LOCATION_COUNT):
        location = world.get_location(locations.shop_location_name(slot))
        set_rule(
            location,
            lambda state: state.has(items.PROGRESSIVE_KEYCARD, player, 3),
        )

    # No gold package on any shelf. A shop check's item is displayed as
    # merchandise; selling this world's own gold is money printing, and any
    # Azure Dreams world's gold on any Azure Dreams shelf reads as buying
    # money with money. Tower checks and other games' locations are all fair,
    # which is the entire remaining fill space.
    #
    # No trap on any shelf either: the forced-trap machinery is tower-only,
    # and a trap for sale as disguised merchandise has no tile to spring on.
    # Traps are also local_items (world.generate_early), so between the two
    # rules a trap can land ONLY on this player's 78 tower checks.
    for slot in range(locations.SHOP_LOCATION_COUNT):
        location = world.get_location(locations.shop_location_name(slot))
        add_item_rule(
            location,
            lambda item: not (
                item.game == items.GAME_NAME
                and (
                    item.name == items.GOLD_PACKAGE
                    or items.is_trap_name(item.name)
                )
            ),
        )

    world.multiworld.completion_condition[player] = lambda state: state.has(items.VICTORY, player)
