from __future__ import annotations

from dataclasses import dataclass


ITEM_ID_BASE = 0x0AD0_0000

# Native inventory item IDs encode their complete four-byte descriptor in the
# low bits of the Archipelago ID. The category and native item ID consume five
# and six bits; the low five hold the quality MAGNITUDE.
#
# Quality is signed and equipment carries flags, neither of which the original
# sixteen-bit layout could express - it stored quality unsigned and dropped
# flags entirely. Bits 16-18 carry the rest. They sit below the 0xAD the base
# puts at bits 20-27, so they were free, and an ID with all three clear is
# byte-identical to what the v1 layout produced for the same item.
#
# `client/src/Adap.Client/Games/AzureDreamsItemManifest.cs` decodes this and
# has to agree bit for bit.
_CATEGORY_SHIFT = 11
_NATIVE_ITEM_SHIFT = 5
_FIELD_MASK = 0x1F
_NEGATIVE_QUALITY_BIT = 1 << 16
_UNIDENTIFIED_BIT = 1 << 17
_CURSED_BIT = 1 << 18

# Native descriptor flag bits. 0x20 is "equipped" - every equipped sword and
# shield in a live save carries it - so nothing here ever sets it; a granted
# item must not arrive already worn.
FLAG_UNIDENTIFIED = 0x80
FLAG_CURSED = 0x40

# A hundred and twenty-nine native rewards fill the 117 tower checks plus twenty
# town-shop checks alongside the eight Progressive Keycards. **2026-08-15**: was
# ninety, for 78 tower checks at two per floor; the third check per floor - the
# one a monster carries - added 39. The pool is flat and uniform, so this is a
# count and not a balance decision.
#
# **One pool, equal distribution, shops included.** Every reward is drawn as
# often as every other one; the great/good/junk tiers this file used to carry
# are gone - they weighted the draw AND the Archipelago classification, and
# neither survived the decision that the pool is flat.
REWARD_COUNT = 129

# **Odds, not ratios.** Every reward rolls these chances independently. There is
# deliberately no share, budget or quota anywhere: a seed that keeps hitting the
# ball band and comes out with thirty of them is not a bug, it is what makes
# that seed the one people talk about. The only floors are the two guarantees
# below, and those are repaired afterwards rather than reserved in advance.
POOL_BALL_CHANCE = 0.05
# 0.07 until 2026-08-05; pulled back to 0.03 after play showed eggs crowding
# the tower/Equipment pool. The GUARANTEED_EGGS floor below still repairs a
# dry seed, so the change narrows the ceiling, not the floor. The Monster
# Shop's SHOP_EGG_CHANCE is its own draw and is deliberately untouched.
POOL_EGG_CHANCE = 0.03

# The Monster Shop's own per-slot odds. Familiar items are a category *here and
# nowhere else* - in the tower and the Equipment Shop a Pita Fruit is just
# another item, which is why the pool roll above has no familiar band.
SHOP_ROCHE_CHANCE = 0.02
SHOP_EGG_CHANCE = 0.15
SHOP_FAMILIAR_CHANCE = 0.45

# Kept here rather than imported from `locations`, which imports `items`, which
# imports this - the suite asserts the two agree instead.
MONSTER_SHOP_SLOT_COUNT = 10

# The two floors, and the only place the pool stops being pure chance. Repaired
# after the roll rather than reserved before it, so they cost the odds nothing:
# a seed that rolled plenty already passes untouched. Both are measured over the
# tower and the Equipment Shop specifically - the Monster Shop runs its own
# draw and neither satisfies nor consumes them.
GUARANTEED_EGGS = 2
GUARANTEED_BURN_BALLS = 2

BALL_CATEGORY = 4
EGG_CATEGORY = 18
SWORD_CATEGORY = 15
WAND_CATEGORY = 16
SHIELD_CATEGORY = 17
EQUIPMENT_CATEGORIES = (SWORD_CATEGORY, WAND_CATEGORY, SHIELD_CATEGORY)

# Fire / Blaze / Flame / Pillar.
BURN_BALL_IDS = (1, 2, 3, 4)
# Kewne is the starting familiar and Ultimate is not in the catalog at all.
EXCLUDED_EGG_IDS = (1, 2)
# The only wand the game lets you temper.
TEMPERABLE_WAND_ID = 2

# A ball with EXACTLY ten charges can be fed to a familiar to teach it that
# ball's spell. That is the whole reason ten-charge stacks are in the pool:
# vanilla caps found balls around seven, so reaching ten normally costs a pile
# of White Sands, and many players never learn the mechanic at all. A Weak Ball
# is the case that sells it - poor as a thrown item, excellent as a taught
# spell - which is why the ladder is not restricted to the balls that are good
# to throw.
TEACHING_CHARGES = 10
# Acid Rain is the one ball this does not apply to, and it is held at a single
# charge everywhere.
ACID_RAIN_BALL_ID = 17
ACID_RAIN_CHARGES = 1

# Quality roll for temperable equipment, as (quality, weight) summing to
# DEFAULT_WEIGHT so an equipment item is drawn exactly as often as any other
# item and the split is purely conditional on having drawn it.
TEMPERED_QUALITY_WEIGHTS = ((1, 20), (2, 20), (-1, 20), (0, 40))
# Everything else in category 16 is untemperable: no positive quality, but the
# game can still curse it.
UNTEMPERABLE_QUALITY_WEIGHTS = ((-1, 20), (0, 80))

DEFAULT_WEIGHT = 100

# Charges for every ball but Acid Rain, as (charges, weight). Summing to
# DEFAULT_WEIGHT is the point: it makes each ball TYPE as likely as any other
# item and leaves the charge count purely conditional on having drawn that ball,
# exactly as TEMPERED_QUALITY_WEIGHTS does for equipment. Flat within the ladder,
# so a ten-charge stack is one draw in five of any ball.
BALL_CHARGE_WEIGHTS = ((4, 20), (5, 20), (6, 20), (7, 20), (TEACHING_CHARGES, 20))


@dataclass(frozen=True)
class NativeReward:
    name: str
    base_name: str
    category: int
    native_item_id: int
    quality: int
    flags: int
    weight: int = DEFAULT_WEIGHT

    @property
    def display_name(self) -> str:
        """What the GAME may call this item. `name` is the Archipelago identity.

        An unidentified item must not announce its quality: the point of the
        flag is that the player has not appraised it yet, and `Vital Sword (-1)`
        on the floor gives away exactly what the inventory is hiding.

        Keyed on the flag rather than on the category, so this follows whatever
        carries FLAG_UNIDENTIFIED. Balls keep their charge count today because
        they are handed over identified; make them unidentified and their count
        disappears from these strings with no change here.

        Built from the base name rather than by stripping a suffix off `name`,
        so it cannot be fooled by an item whose own name ends in parentheses.
        """

        return self.base_name if self.flags & FLAG_UNIDENTIFIED else self.name

    @property
    def protocol_item_id(self) -> int:
        return native_protocol_item_id(
            self.category, self.native_item_id, self.quality, self.flags
        )

    @property
    def descriptor(self) -> bytes:
        return bytes(
            (self.native_item_id, self.category, self.quality & 0xFF, self.flags)
        )


def native_protocol_item_id(
    category: int, native_item_id: int, quality: int, flags: int = 0
) -> int:
    if not 0 < category <= _FIELD_MASK:
        raise ValueError(f"Native category {category} does not fit the protocol item ID.")
    if not 0 < native_item_id <= 0x3F:
        raise ValueError(f"Native item ID {native_item_id} does not fit the protocol item ID.")
    if not -_FIELD_MASK <= quality <= _FIELD_MASK:
        raise ValueError(f"Native quality {quality} does not fit the protocol item ID.")
    if flags & ~(FLAG_UNIDENTIFIED | FLAG_CURSED):
        raise ValueError(
            f"Native flags 0x{flags:02X} carry a bit the protocol item ID cannot encode."
        )
    encoded = (
        ITEM_ID_BASE
        | (category << _CATEGORY_SHIFT)
        | (native_item_id << _NATIVE_ITEM_SHIFT)
        | abs(quality)
    )
    if quality < 0:
        encoded |= _NEGATIVE_QUALITY_BIT
    if flags & FLAG_UNIDENTIFIED:
        encoded |= _UNIDENTIFIED_BIT
    if flags & FLAG_CURSED:
        encoded |= _CURSED_BIT
    return encoded


# The subset of the supplied save-editor catalog this pool draws from.
# Categories 0x0B, 0x0D, and 0x0E (gift, quest, and coin) are absent, as is all
# of special (0x0C) but the Oleem. That is a decision about the pool and not a
# claim about the game - a Gold Coin is as real an item as a sword. What a
# player may actually be handed is the ROM's business; nothing downstream of
# here, the client included, gets a second vote on it.
#
# The hospital-only Medicinal Herb (category 1, ID 1), cutscene Seraphim Sword
# (category 15, ID 11), and Ultimate Egg are likewise excluded in favor of
# their ordinary tower counterparts or dedicated goal behavior.
#
# Three more are excluded as dead weight rather than as unsafe: the Healing
# Herb (category 1, ID 8) and Focus Loupe (category 9, ID 3) do nothing a
# player can use, and the Oleem Fruit (category 2, ID 9) is the inert quest
# stand-in for the real category 0x0C Oleem that replaces it here.
_CATALOG: dict[int, dict[int, str]] = {
    1: {
        2: "Antidote Herb",
        3: "Antichaos Herb",
        4: "Wake-Up Herb",
        5: "Cure-All Herb",
        6: "Hazak Herb",
        7: "Shomuro Herb",
        9: "Poison Herb",
        10: "Paralyze Herb",
        11: "Harash Herb",
        12: "Horrey Herb",
        13: "Sleep Herb",
        14: "Roeam Herb",
        15: "Medicinal Herb",
    },
    2: {
        1: "Pita Fruit",
        2: "Big Pita Fruit",
        3: "Tumna Fruit",
        4: "Leva Fruit",
        5: "Leolam Fruit",
        6: "Laev Fruit",
        7: "Roche Fruit",
        8: "Limit Fruit",
        10: "Geropita Fruit",
    },
    3: {
        1: "Hazak Seed",
        2: "Shomuro Seed",
        3: "Mazarr Seed",
        4: "Mahell Seed",
        5: "Light Seed",
        6: "Sea Seed",
        7: "Wind Seed",
        8: "Lar Seed",
        9: "Slow Seed",
        10: "Tovar Seed",
    },
    4: {
        1: "Fire Ball",
        2: "Blaze Ball",
        3: "Flame Ball",
        4: "Pillar Ball",
        5: "Poison Ball",
        6: "Water Ball",
        7: "Repel Ball",
        8: "Ice Rock Ball",
        9: "Recovery Ball",
        11: "Blinder Ball",
        12: "Binding Ball",
        13: "Sleep Ball",
        14: "Weak Ball",
        17: "Acid Rain Ball",
    },
    5: {
        1: "Holy Scroll",
        2: "Malicious Scroll",
        3: "Trap Scroll",
        4: "Restore Scroll",
        5: "De-Curse Scroll",
        6: "Flat Scroll",
        7: "Alchemic Scroll",
    },
    6: {1: "Fire Crystal", 2: "Water Crystal", 3: "Wind Crystal"},
    7: {1: "Holy Bell", 2: "Malicious Bell", 3: "Familiar Bell"},
    8: {1: "Truth Glasses", 2: "Star Glasses"},
    9: {
        1: "Exit Loupe",
        2: "Trap Loupe",
        4: "Monster Loupe",
        5: "Treasure Loupe",
    },
    10: {1: "Red Sand", 2: "Blue Sand", 3: "White Sand"},
    12: {9: "Oleem"},
    15: {
        1: "Gold Sword",
        2: "Copper Sword",
        3: "Iron Sword",
        4: "Steel Sword",
        5: "Fire Sword",
        6: "Blizzard Sword",
        7: "Gulfwind Sword",
        8: "Vital Sword",
        9: "Dark Sword",
        10: "Holy Sword",
        12: "Seraphim Sword",
        13: "Troll Sword",
        14: "Hammer",
        15: "Bow Gun",
    },
    16: {
        1: "Wooden Wand",
        2: "Trained Wand",
        3: "Life Wand",
        4: "Paralyze Wand",
        5: "Money Wand",
        6: "Scarlet Wand",
        7: "Stream Wand",
        8: "Gulf Wand",
        9: "Seal Wand",
    },
    17: {
        1: "Wood Shield",
        2: "Leather Shield",
        3: "Mirror Shield",
        4: "Copper Shield",
        5: "Iron Shield",
        6: "Steel Shield",
        7: "Diamond Shield",
        8: "Scorch Shield",
        9: "Ice Shield",
        10: "Earth Shield",
        11: "Live Shield",
    },
    18: {
        2: "Kewne Egg",
        3: "Dragon Egg",
        4: "Kid Egg",
        5: "Ifrit Egg",
        6: "Flame Egg",
        7: "Grineut Egg",
        8: "Griffon Egg",
        9: "Saber Egg",
        10: "Snowman Egg",
        11: "Ashra Egg",
        12: "Arachne Egg",
        13: "Battnel Egg",
        14: "Nyuel Egg",
        15: "Death Egg",
        16: "Clown Egg",
        17: "Univern Egg",
        18: "Unicorn Egg",
        19: "Metal Egg",
        20: "Block Egg",
        21: "Pulunpa Egg",
        22: "Troll Egg",
        23: "Noise Egg",
        24: "U-Boat Egg",
        25: "Baloon Egg",
        26: "Dreamin Egg",
        27: "Blume Egg",
        28: "Volcano Egg",
        29: "Cyclone Egg",
        30: "Manoeva Egg",
        31: "Barong Egg",
        32: "Picket Egg",
        33: "Kraken Egg",
        34: "Weadog Egg",
        35: "Stealth Egg",
        36: "Viper Egg",
        37: "Naplass Egg",
        38: "Zu Egg",
        39: "Mandara Egg",
        40: "Killer Egg",
        41: "Garuda Egg",
        42: "Glacier Egg",
        43: "Tyrant Egg",
        44: "Golem Egg",
        45: "Maximum Egg",
    },
}

def _build_rewards() -> tuple[NativeReward, ...]:
    rewards: list[NativeReward] = []
    for category, entries in _CATALOG.items():
        for native_item_id, base_name in entries.items():
            # Every ball but Acid Rain rides the full charge ladder. Restricting
            # it to the six balls that are good to throw was an oversight: it
            # denied a teaching stack to exactly the balls whose only real use
            # IS teaching.
            if category == BALL_CATEGORY and native_item_id != ACID_RAIN_BALL_ID:
                for quality, weight in BALL_CHARGE_WEIGHTS:
                    rewards.append(
                        NativeReward(
                            name=f"{base_name} ({quality})",
                            base_name=base_name,
                            category=category,
                            native_item_id=native_item_id,
                            quality=quality,
                            flags=0,
                            weight=weight,
                        )
                    )
                continue

            if category == EGG_CATEGORY and native_item_id in EXCLUDED_EGG_IDS:
                continue

            if category in EQUIPMENT_CATEGORIES:
                # Every piece is handed over unidentified; a negative roll is
                # also cursed. Weights sum to DEFAULT_WEIGHT, so the quality
                # split is conditional on the draw and does not change how
                # often the item itself comes up.
                temperable = (
                    category != WAND_CATEGORY
                    or native_item_id == TEMPERABLE_WAND_ID
                )
                spread = (
                    TEMPERED_QUALITY_WEIGHTS
                    if temperable
                    else UNTEMPERABLE_QUALITY_WEIGHTS
                )
                for quality, weight in spread:
                    # Both flags survive delivery now. Bit 7 used to be the
                    # mailbox's presentation marker, so a 0x80 set here was
                    # rewritten in transit and equipment always arrived
                    # appraised; the marker moved to MAILBOX_PRESENTATION_FLAG
                    # (0x02) on 2026-08-02 and bit 7 is the game's again.
                    flags = FLAG_UNIDENTIFIED | (FLAG_CURSED if quality < 0 else 0)
                    suffix = f" ({quality:+d})" if quality else ""
                    rewards.append(
                        NativeReward(
                            name=f"{base_name}{suffix}",
                            base_name=base_name,
                            category=category,
                            native_item_id=native_item_id,
                            quality=quality,
                            flags=flags,
                            weight=weight,
                        )
                    )
                continue

            # Eggs carry a warming quality; every other survivor here is a
            # plain item. All of them are drawn at the same weight - which egg
            # you get is now flat across the whole roster, inside whatever the
            # egg share allots.
            if category == EGG_CATEGORY:
                quality = 20
            elif category == BALL_CATEGORY:
                quality = ACID_RAIN_CHARGES
            else:
                quality = 0

            rewards.append(
                NativeReward(
                    name=base_name,
                    base_name=base_name,
                    category=category,
                    native_item_id=native_item_id,
                    quality=quality,
                    flags=0,
                )
            )

    names = [reward.name for reward in rewards]
    ids = [reward.protocol_item_id for reward in rewards]
    if len(names) != len(set(names)):
        raise ValueError("Azure Dreams reward names must be unique.")
    if len(ids) != len(set(ids)):
        raise ValueError("Azure Dreams protocol item IDs must be unique.")
    return tuple(rewards)


NATIVE_REWARDS = _build_rewards()
REWARD_BY_NAME = {reward.name: reward for reward in NATIVE_REWARDS}


def display_name_for(item_name: str) -> str:
    """The name the game may show for an Archipelago item name.

    Items from other worlds pass through untouched - we know nothing about them
    beyond what Archipelago called them.
    """

    reward = REWARD_BY_NAME.get(item_name)
    return reward.display_name if reward is not None else item_name
REWARD_BY_ID = {reward.protocol_item_id: reward for reward in NATIVE_REWARDS}

# The draws the pool is built from. Shops draw from the same list as the tower;
# a shop slot is a location, not a separate species of reward.
BALL_REWARDS = tuple(r for r in NATIVE_REWARDS if r.category == BALL_CATEGORY)
EGG_REWARDS = tuple(r for r in NATIVE_REWARDS if r.category == EGG_CATEGORY)
BURN_BALL_REWARDS = tuple(
    r for r in BALL_REWARDS if r.native_item_id in BURN_BALL_IDS
)

# Everything a familiar interacts with. Not "everything the description
# mentions a familiar in": these are the items whose POINT is the familiar.
#
# Deliberately absent: Wake-Up Herb and Limit Fruit, which Koh can also consume
# for himself; Roche Fruit, which gets its own thin slice because thrown at a
# monster it converts the entire familiar grind into one item and defines a run
# rather than a floor; and Acid Rain, the one ball with no teaching stack.
#
# Geropita and Leolam earn their place on the throwing side rather than the
# feeding one - every item in this game can be thrown, and Geropita thrown at
# an enemy zeroes it out.
FAMILIAR_REWARD_NAMES = (
    "Restore Scroll",
    "Pita Fruit",
    "Big Pita Fruit",
    "Leva Fruit",
    "Leolam Fruit",
    "Geropita Fruit",
    "Water Crystal",
    "Familiar Bell",
    "Oleem",
)
ROCHE_FRUIT_NAME = "Roche Fruit"

# A ten-charge ball is a familiar item in everything but category: it is fed to
# one to teach a spell. A ball at any other charge count is not, and stays an
# ordinary ball in the global draw.
TEACHING_BALL_REWARDS = tuple(
    r for r in BALL_REWARDS if r.quality == TEACHING_CHARGES
)
FAMILIAR_REWARDS = (
    tuple(REWARD_BY_NAME[name] for name in FAMILIAR_REWARD_NAMES)
    + TEACHING_BALL_REWARDS
)
FAMILIAR_REWARD_NAME_SET = frozenset(r.name for r in FAMILIAR_REWARDS)

# The familiar draw picks a KIND first, then an item inside it. "A ball with a
# teaching stack" is one kind, the way it reads in the design - not thirteen
# separate ones competing with the nine named items.
#
# Drawing flat over the 22 entries instead would make teaching balls 59% of
# every familiar slot and turn the Monster Shop into a ball shop. Drawing by
# `weight` - which is what this did first - was worse and accidental: a
# teaching ball's weight is 20 because it is one rung of the five-charge
# ladder, and that number means nothing here, where the charge is already
# fixed at ten. It quietly made them 22% instead of the intended 10%.
FAMILIAR_REWARD_KINDS = tuple(
    [(REWARD_BY_NAME[name],) for name in FAMILIAR_REWARD_NAMES]
    + [TEACHING_BALL_REWARDS]
)

# The blacksmith's progressive unlocks (docs/systems/blacksmith.md): a Red
# Sand raises his weapon temper level, a Blue Sand his shield level, a White
# Sand the ball charger's level (docs/systems/fortune-teller.md section 5), and
# none of them enters the bag any more. items.py puts a fixed three of each into
# the pool; they are out of every random draw here so a seed cannot hold more.
SAND_CATEGORY = 10
RED_SAND_NAME = "Red Sand"
BLUE_SAND_NAME = "Blue Sand"
WHITE_SAND_NAME = "White Sand"
TEMPER_SAND_NAMES = (RED_SAND_NAME, BLUE_SAND_NAME, WHITE_SAND_NAME)

# Everything that is not a ball and not an egg - familiar items INCLUDED. They
# are only a category to the Monster Shop; anywhere else a Water Crystal is
# just another item and has to be as likely as one. Minus the three sands,
# which are fixed-count progression rather than draws.
ORDINARY_REWARDS = tuple(
    r
    for r in NATIVE_REWARDS
    if r.category not in (BALL_CATEGORY, EGG_CATEGORY) and r.name not in TEMPER_SAND_NAMES
)

# Rolls are drawn in basis points rather than as floats. Subtracting float
# shares from a float roll left a band edge at 0.44999999999999996, so a roll
# that should have landed in the next band came back in the previous one.
BASIS_POINTS = 10_000

POOL_BANDS = (("ball", POOL_BALL_CHANCE), ("egg", POOL_EGG_CHANCE))
SHOP_BANDS = (
    ("roche", SHOP_ROCHE_CHANCE),
    ("egg", SHOP_EGG_CHANCE),
    ("familiar", SHOP_FAMILIAR_CHANCE),
)


def _roll(random, bands) -> str | None:
    roll = random.randrange(BASIS_POINTS)
    threshold = 0
    for band, chance in bands:
        threshold += round(chance * BASIS_POINTS)
        if roll < threshold:
            return band
    return None


def roll_pool_item(random) -> str | None:
    """Which band one pool item lands in, or None for an ordinary item."""

    return _roll(random, POOL_BANDS)


def roll_monster_shop_slot(random) -> str | None:
    """Which band one Monster Shop slot lands in, or None to leave it alone."""

    return _roll(random, SHOP_BANDS)


def _uniform(random, rewards) -> NativeReward:
    """A flat draw, save for the ladders that decide an item's shape.

    `weight` only ever carries the equipment enchantment and ball charge
    spreads, both of which sum to DEFAULT_WEIGHT - so this is uniform over
    items, and the ladder is conditional on having drawn one.
    """

    return random.choices(
        rewards, weights=[reward.weight for reward in rewards], k=1
    )[0]


def draw_pool_item(random) -> NativeReward:
    """One reward for a tower or Equipment Shop location, rolled fresh."""

    band = roll_pool_item(random)
    if band == "ball":
        return _uniform(random, BALL_REWARDS)
    if band == "egg":
        return _uniform(random, EGG_REWARDS)
    return _uniform(random, ORDINARY_REWARDS)


def draw_monster_shop_item(random, band: str | None) -> NativeReward:
    """One reward for a Monster Shop slot that rolled `band`.

    A slot that rolled nothing takes an ordinary pool draw, which is what makes
    the shop's remaining 38% read exactly like anywhere else in the seed.
    """

    if band == "roche":
        return REWARD_BY_NAME[ROCHE_FRUIT_NAME]
    if band == "egg":
        return _uniform(random, EGG_REWARDS)
    if band == "familiar":
        return random.choice(random.choice(FAMILIAR_REWARD_KINDS))
    return draw_pool_item(random)


def apply_guarantees(random, rewards: list[NativeReward]) -> None:
    """Repairs `rewards` in place until both floors hold.

    Deliberately a repair and not a reservation. Drawing the guaranteed items
    first would put a floor under the odds and quietly bias every seed; this
    way a seed that already rolled two eggs is not touched at all, and only a
    seed that rolled none pays anything.

    Replacements come out of the ordinary items, never out of the other
    guarantee - fixing the burn balls must not spend the eggs that were just
    fixed.
    """

    def satisfy(matches, required: int, pick) -> None:
        held = [index for index, r in enumerate(rewards) if matches(r)]
        if len(held) >= required:
            return
        protected = set(held)
        spare = [
            index
            for index, r in enumerate(rewards)
            if index not in protected
            and r.category != EGG_CATEGORY
            and not _is_burn_ball(r)
        ]
        random.shuffle(spare)
        for index in spare[: required - len(held)]:
            rewards[index] = pick()

    satisfy(
        lambda r: r.category == EGG_CATEGORY,
        GUARANTEED_EGGS,
        lambda: _uniform(random, EGG_REWARDS),
    )
    satisfy(
        _is_burn_ball,
        GUARANTEED_BURN_BALLS,
        lambda: _uniform(random, BURN_BALL_REWARDS),
    )


def _is_burn_ball(reward: NativeReward) -> bool:
    return (
        reward.category == BALL_CATEGORY
        and reward.native_item_id in BURN_BALL_IDS
    )
