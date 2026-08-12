from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Location

from . import items, regions

if TYPE_CHECKING:
    from .world import AzureDreamsWorld


GAME_NAME = "Azure Dreams"
LOCATION_ID_BASE = 0x0AD1_0000
SHOP_LOCATION_ID_BASE = LOCATION_ID_BASE + 0x100
SHOP_SLOTS_PER_BUILDING = 10
SHOP_NAMES = ("Equipment Shop", "Monster Shop")
SHOP_LOCATION_COUNT = len(SHOP_NAMES) * SHOP_SLOTS_PER_BUILDING
TOWER_FLOOR_COUNT = 39
TOWER_SLOTS_PER_FLOOR = 2
TOWER_LOCATION_COUNT = TOWER_FLOOR_COUNT * TOWER_SLOTS_PER_FLOOR
ULTIMATE_EGG_LOCATION = "Ultimate Egg Acquired"


def tower_location_name(floor: int, slot: int) -> str:
    if floor not in range(1, TOWER_FLOOR_COUNT + 1):
        raise ValueError(f"Tower floor must be 1-{TOWER_FLOOR_COUNT}, got {floor}.")
    if slot not in range(TOWER_SLOTS_PER_FLOOR):
        raise ValueError(f"Tower slot must be 0-{TOWER_SLOTS_PER_FLOOR - 1}, got {slot}.")
    return f"Tower Floor {floor:02d} - Slot {slot + 1}"


def tower_location_id(floor: int, slot: int) -> int:
    # The offset exactly matches the PS1 mailbox bit index.
    return LOCATION_ID_BASE + (floor - 1) * TOWER_SLOTS_PER_FLOOR + slot


def shop_location_name(slot: int) -> str:
    if slot not in range(SHOP_LOCATION_COUNT):
        raise ValueError(f"Town Shop slot must be 0-{SHOP_LOCATION_COUNT - 1}, got {slot}.")
    shop_index, relative_slot = divmod(slot, SHOP_SLOTS_PER_BUILDING)
    return f"{SHOP_NAMES[shop_index]} - Slot {relative_slot + 1:02d}"


def shop_location_id(slot: int) -> int:
    if slot not in range(SHOP_LOCATION_COUNT):
        raise ValueError(f"Town Shop slot must be 0-{SHOP_LOCATION_COUNT - 1}, got {slot}.")
    return SHOP_LOCATION_ID_BASE + slot


LOCATION_NAME_TO_ID = {
    tower_location_name(floor, slot): tower_location_id(floor, slot)
    for floor in range(1, TOWER_FLOOR_COUNT + 1)
    for slot in range(TOWER_SLOTS_PER_FLOOR)
}
LOCATION_NAME_TO_ID.update(
    {
        shop_location_name(slot): shop_location_id(slot)
        for slot in range(SHOP_LOCATION_COUNT)
    }
)


class AzureDreamsLocation(Location):
    game = GAME_NAME


def create_all_locations(world: AzureDreamsWorld) -> None:
    town = world.get_region(regions.TOWN_REGION)
    town.add_locations(
        {
            shop_location_name(slot): shop_location_id(slot)
            for slot in range(SHOP_LOCATION_COUNT)
        },
        AzureDreamsLocation,
    )

    for floor in range(1, TOWER_FLOOR_COUNT + 1):
        region = world.get_region(regions.tower_region_name(floor))
        region.add_locations(
            {
                tower_location_name(floor, slot): tower_location_id(floor, slot)
                for slot in range(TOWER_SLOTS_PER_FLOOR)
            },
            AzureDreamsLocation,
        )

    floor_40 = world.get_region(regions.tower_region_name(40))
    ultimate_egg = AzureDreamsLocation(world.player, ULTIMATE_EGG_LOCATION, None, floor_40)
    ultimate_egg.place_locked_item(items.create_event_item(world, items.VICTORY))
    floor_40.locations.append(ultimate_egg)
