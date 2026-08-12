from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Region

if TYPE_CHECKING:
    from .world import AzureDreamsWorld


TOWN_REGION = "Town"

# (first floor, last floor, progressive keycards required)
TOWER_BANDS = (
    (1, 4, 0),
    (5, 9, 1),
    (10, 14, 2),
    (15, 19, 3),
    (20, 24, 4),
    (25, 29, 5),
    (30, 34, 6),
    (35, 39, 7),
    (40, 40, 8),
)


def tower_region_name(floor: int) -> str:
    for first_floor, last_floor, _ in TOWER_BANDS:
        if floor in range(first_floor, last_floor + 1):
            if first_floor == last_floor:
                return f"Tower Floor {first_floor}"
            return f"Tower Floors {first_floor}-{last_floor}"
    raise ValueError(f"Tower floor must be 1-40, got {floor}.")


def tower_entrance_name(first_floor: int, last_floor: int) -> str:
    if first_floor == last_floor:
        return f"Ascend to Floor {first_floor}"
    return f"Ascend to Floors {first_floor}-{last_floor}"


def create_and_connect_regions(world: AzureDreamsWorld) -> None:
    town = Region(TOWN_REGION, world.player, world.multiworld)
    tower_regions = [
        Region(tower_region_name(first_floor), world.player, world.multiworld)
        for first_floor, _, _ in TOWER_BANDS
    ]
    world.multiworld.regions += [town, *tower_regions]

    previous_region = town
    for tower_region, (first_floor, last_floor, _) in zip(tower_regions, TOWER_BANDS):
        previous_region.connect(tower_region, tower_entrance_name(first_floor, last_floor))
        previous_region = tower_region
