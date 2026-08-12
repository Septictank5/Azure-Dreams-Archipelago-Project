from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Item, ItemClassification

from . import item_manifest

if TYPE_CHECKING:
    from .world import AzureDreamsWorld


GAME_NAME = "Azure Dreams"

# Progressive Keycard and the gold package are the two fixed protocol items.
# Native reward IDs are descriptor-derived by item_manifest so the C# client
# can decode them without maintaining a second 173-entry lookup table. The
# fixed IDs sit below every native ID (the smallest native encoding is
# ITEM_ID_BASE + 0x800, category 1), so the range +0..+0x7FF is theirs.
ITEM_ID_BASE = item_manifest.ITEM_ID_BASE
PROGRESSIVE_KEYCARD = "Progressive Keycard"
# Granted client-side straight into the gold counter, like the keycard's
# clearance level - no inventory slot, no native descriptor. Exactly-once
# comes from the durable receive cursor, which is why gold is granted AT the
# cursor in history order rather than eagerly the way keycards are: gold is
# cumulative and cannot be re-derived from the history the way a level can.
GOLD_PACKAGE = "5000 Gold"
GOLD_PACKAGE_AMOUNT = 5_000
# One tower send costs one token. A fresh save is given one by the seed
# initializer, so sending is never wholly gated behind the multiworld; these
# are the rest, and finding them early is meant to feel like luck.
#
# Granted client-side into the game's own token counter, like gold - no
# inventory slot and no native descriptor - and reconciled against a durable
# banked count (patch.SEND_TOKEN_BANKED_ADDRESS) because the count is
# cumulative and spent in game.
SEND_TOKEN = "Send Token"
VICTORY = "Victory"

# Trap items (own-world only, tower only, disguised in-game as Progressive
# Keycards - docs/systems/forced-trap.md). Protocol IDs carry the game's
# trap id in the low byte so the C# client can poke it straight into the
# forced-trap request byte: TRAP_ITEM_ID_BASE + game trap id, inside the
# fixed-ID range below the smallest native encoding (ITEM_ID_BASE + 0x800).
#
# Pool membership is deliberate: go-up (4) is excluded because ADAP
# repurposes it as the bonus-floor entry - a reward, not a trap; the dud (6)
# does nothing; crack (15) and upheaval (16) are excluded because the native
# roller gates them on a per-floor support flag (`[0x800E296C]` bit
# 0x20000000) and forcing them on an unsupported floor is unvalidated.
# Monster den (19) is in the pool but at ~1% of rolled traps regardless of
# the trap chance - a den can end a run outright (user call, 2026-08-09).
TRAP_ITEM_ID_BASE = ITEM_ID_BASE + 0x40
TRAP_NAME_BY_GAME_ID = {
    1: "Reversal Trap",
    2: "Slow Trap",
    3: "Warp Trap",
    5: "Chaos Trap",
    7: "Bomb Trap",
    8: "Slam Trap",
    9: "Sleep Trap",
    10: "Blinder Trap",
    11: "Poison Trap",
    12: "Prison Trap",
    13: "Frog Trap",
    14: "Bump Trap",
    17: "Seal Trap",
    18: "Rust Trap",
    19: "Monster Den Trap",
}
MONSTER_DEN_TRAP_GAME_ID = 19
MONSTER_DEN_TRAP_SHARE = 0.01
ORDINARY_TRAP_GAME_IDS = tuple(
    game_id
    for game_id in TRAP_NAME_BY_GAME_ID
    if game_id != MONSTER_DEN_TRAP_GAME_ID
)
TRAP_GAME_ID_BY_NAME = {name: game_id for game_id, name in TRAP_NAME_BY_GAME_ID.items()}
TRAP_NAMES = frozenset(TRAP_NAME_BY_GAME_ID.values())
# The pool cannot hold more traps than the tower can: traps are local-only
# and refused by the twenty shop shelves, so the 78 tower checks must also
# fit the five gold packages (shop-refused too), this world's own keycards
# when the fill leans local, and other players' progression spillover. 70
# was measured too tight - a 100% two-player room FillErrored when the last
# trap met a tower already full of exactly those things - so 60, leaving 18
# slots of headroom.
TRAP_COUNT_CAP = 60


def is_trap_name(name: str) -> bool:
    return name in TRAP_NAMES


def roll_trap_name(random) -> str:
    if random.random() < MONSTER_DEN_TRAP_SHARE:
        return TRAP_NAME_BY_GAME_ID[MONSTER_DEN_TRAP_GAME_ID]
    return TRAP_NAME_BY_GAME_ID[random.choice(ORDINARY_TRAP_GAME_IDS)]


ITEM_NAME_TO_ID = {
    PROGRESSIVE_KEYCARD: ITEM_ID_BASE,
    GOLD_PACKAGE: ITEM_ID_BASE + 1,
    SEND_TOKEN: ITEM_ID_BASE + 2,
    **{
        name: TRAP_ITEM_ID_BASE + game_id
        for game_id, name in TRAP_NAME_BY_GAME_ID.items()
    },
    **{
        reward.name: reward.protocol_item_id
        for reward in item_manifest.NATIVE_REWARDS
    },
}

# The keycard is the only progression item; everything else is filler.
#
# Not an opinion about the items - it follows from the pool being flat. There is
# no longer any basis for calling one reward useful and another filler, and
# marking them all useful would be actively harmful: Archipelago refuses to put
# useful items in excluded locations, so a yaml using `exclude_locations` could
# not be filled at all.
ITEM_CLASSIFICATIONS = {
    PROGRESSIVE_KEYCARD: ItemClassification.progression,
    GOLD_PACKAGE: ItemClassification.filler,
    # Filler, not useful: nothing in logic is reachable only by sending, and
    # `useful` would make Archipelago refuse to place these in excluded
    # locations - the same trap the flat native pool above avoids.
    SEND_TOKEN: ItemClassification.filler,
    **{name: ItemClassification.trap for name in TRAP_NAMES},
    **{
        reward.name: ItemClassification.filler
        for reward in item_manifest.NATIVE_REWARDS
    },
}

PROGRESSIVE_KEYCARD_COUNT = 8
# Five guaranteed packages, replacing five native rewards; the 98 locations
# stay fixed. Placement is ordinary multiworld fill EXCEPT the twenty Azure
# Dreams shop locations, which refuse them (rules.set_all_rules): a shop
# check's item is displayed as merchandise, and gold for sale is money
# printing in its own world and nonsense in any other Azure Dreams world.
GOLD_PACKAGE_COUNT = 5
# Five tokens, and unlike gold they carry NO placement restriction: a token
# on a shop shelf is a perfectly sensible thing to buy, and a token in
# another Azure Dreams world is a perfectly sensible thing to find. Each one
# displaces a native draw so the 98 locations stay exactly filled.
SEND_TOKEN_COUNT = 5


def send_token_count(world: AzureDreamsWorld) -> int:
    """Five tokens, but only when there is somebody to send to.

    A send needs another Azure Dreams player: the tower's Send row is built
    from the other AD slots in the room and does not exist at all in a solo
    one, and Nada's menu drops out the same way (`world.generate_output`
    already computes that target list). So in a one-AD-player seed a token
    would be an item you can never spend, and five of them would be five dead
    checks.

    They become ordinary draws instead - which means they are subject to the
    trap roll like every other slot, so a solo seed with traps on gets traps
    out of them at the configured chance rather than silently losing the
    slots.
    """

    others = [
        player
        for player in world.multiworld.get_game_players(GAME_NAME)
        if player != world.player
    ]
    return SEND_TOKEN_COUNT if others else 0


class AzureDreamsItem(Item):
    game = GAME_NAME


def create_item(world: AzureDreamsWorld, name: str) -> AzureDreamsItem:
    return AzureDreamsItem(
        name,
        ITEM_CLASSIFICATIONS[name],
        ITEM_NAME_TO_ID[name],
        world.player,
    )


def create_event_item(world: AzureDreamsWorld, name: str) -> AzureDreamsItem:
    return AzureDreamsItem(name, ItemClassification.progression, None, world.player)


def create_all_items(world: AzureDreamsWorld) -> None:
    unfilled_location_count = len(world.multiworld.get_unfilled_locations(world.player))
    expected_location_count = PROGRESSIVE_KEYCARD_COUNT + item_manifest.REWARD_COUNT
    if unfilled_location_count != expected_location_count:
        raise ValueError(
            f"Azure Dreams has {unfilled_location_count} locations but its configured pool "
            f"requires exactly {expected_location_count}."
        )

    itempool = [world.create_item(PROGRESSIVE_KEYCARD) for _ in range(PROGRESSIVE_KEYCARD_COUNT)]
    itempool.extend(
        world.create_item(GOLD_PACKAGE) for _ in range(GOLD_PACKAGE_COUNT)
    )
    tokens = send_token_count(world)
    itempool.extend(world.create_item(SEND_TOKEN) for _ in range(tokens))

    # The Monster Shop's ten slots are rolled HERE, not in post_fill, and the
    # items they ask for are drawn into the pool alongside everything else. The
    # shaper then applies this same plan once the fill has run.
    #
    # Rolling once rather than twice is what makes supply match demand exactly:
    # rolling again later would ask the pool for a number of familiar items it
    # was never told to contain, and the shop would quietly come up short about
    # half the time.
    world.monster_shop_plan = [
        item_manifest.roll_monster_shop_slot(world.random)
        for _ in range(item_manifest.MONSTER_SHOP_SLOT_COUNT)
    ]
    selected = [
        item_manifest.draw_monster_shop_item(world.random, band)
        for band in world.monster_shop_plan
    ]

    # Everything else. One independent roll per item, no share and no quota, so
    # a seed really can come out with thirty balls in it. The gold packages
    # above each displace one native draw so the pool still matches the
    # location count exactly.
    elsewhere_count = (
        item_manifest.REWARD_COUNT - len(selected) - GOLD_PACKAGE_COUNT - tokens
    )

    # Traps displace ordinary draws, one independent trap-chance roll per
    # slot, decided BEFORE the natives are drawn so the floor guarantees
    # below repair only what actually stays native. Trap TYPE is rolled per
    # trap (monster den pinned to ~1%). The cap keeps a solo 100%-trap yaml
    # fillable - see TRAP_COUNT_CAP.
    trap_count = 0
    if world.options.traps:
        chance = world.options.trap_chance.value
        trap_count = min(
            sum(
                1
                for _ in range(elsewhere_count)
                if world.random.random() * 100 < chance
            ),
            TRAP_COUNT_CAP,
        )

    elsewhere = [
        item_manifest.draw_pool_item(world.random)
        for _ in range(elsewhere_count - trap_count)
    ]
    # The two floors, repaired after the fact and applied only to what the
    # Monster Shop is not taking - the shop does not count toward either.
    item_manifest.apply_guarantees(world.random, elsewhere)
    selected += elsewhere

    itempool.extend(world.create_item(reward.name) for reward in selected)
    itempool.extend(
        world.create_item(roll_trap_name(world.random)) for _ in range(trap_count)
    )

    if len(itempool) != unfilled_location_count:
        raise ValueError(
            f"Azure Dreams generated {len(itempool)} items for "
            f"{unfilled_location_count} unfilled locations."
        )

    world.multiworld.itempool += itempool
