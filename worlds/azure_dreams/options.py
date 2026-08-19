from dataclasses import dataclass

from Options import DefaultOnToggle, PerGameCommonOptions, Range, Toggle


class EnableTraps(Toggle):
    """Add Azure Dreams tower traps to the item pool.

    A trap is placed only in your own world (the machinery that springs it
    exists only in your tower), and in the tower it is DISGUISED: the floor
    dialogue, the at-feet menu and the description all report a Progressive
    Keycard, so you cannot tell a trap from real progress until you grab it.
    Picking it up springs the trap on the spot - it is planted at your tile
    and fired by the game's own trap machinery, then stays on the floor like
    any revealed trap.
    """

    display_name = "Enable Traps"


class TrapChance(Range):
    """Percent chance for each ordinary filler slot to hold a trap instead.

    Rolled once per non-guaranteed pool slot (the tower and shop check count
    never changes), so 3 means two or three traps in a typical seed. Kept
    low by default because a trap spends a whole tower check on a setback
    and they compound. Monster dens are always extremely rare regardless of
    this setting - about 1% of rolled traps - because one can end a run
    outright.
    """

    display_name = "Trap Chance"
    range_start = 0
    range_end = 100
    default = 3


class HintSystem(DefaultOnToggle):
    """The fortune teller reads the tower for you.

    Mademoiselle Shiela, in the Fortune Telling Palace, will look at one of
    the lowest three tower floors that still holds un-collected checks and
    describe, in her own vague terms, what KIND of thing is waiting there
    (a leaf, a sphere within her sphere, a thin cold card, something not of
    this world...) - and whether it lies on the floor or is carried by a
    monster. Costs 1000 gold a reading. Off: she says the mists are still.
    """

    display_name = "Fortune Teller Hints"


class TemperSystem(DefaultOnToggle):
    """The blacksmith and the ball charger, unlocked by the sands.

    On: three Red Sands, three Blue Sands and three White Sands are in the
    pool. The smith in the equipment shop tempers swords, shields and the
    Trained Wand for gold up to +10/+20/+40 per Red (weapon) or Blue (shield)
    Sand received; the ball charger beside the fortune teller refills spell
    balls for gold, 1/2/3 charges per town visit per White Sand received (any
    ball reaches ten charges at any level). The sands
    never enter the bag and the floors stop dropping them. Off: neither NPC
    appears, no sands are in the pool, and the floors drop them as in vanilla.
    """

    display_name = "Blacksmith and Ball Charger"


class CarrierSystem(DefaultOnToggle):
    """A third check on every tower floor, carried by a monster.

    On: every floor 1-39 holds three Archipelago locations - the two markers
    on the ground, and one held by a level-1 monster of a species that does
    not belong on that floor. It ignores you, heads for a room exit, and drops
    the marker when it dies. Each floor also trades one of its native monster
    types for the carrier, so the roster stays the same size.

    Off: two checks per floor as before, 39 fewer locations and 39 fewer items,
    no forced spawn, and every floor keeps its vanilla monster roster.
    """

    display_name = "Monster-Carried Checks"


@dataclass
class AzureDreamsOptions(PerGameCommonOptions):
    traps: EnableTraps
    trap_chance: TrapChance
    hint_system: HintSystem
    temper_system: TemperSystem
    carrier_system: CarrierSystem
