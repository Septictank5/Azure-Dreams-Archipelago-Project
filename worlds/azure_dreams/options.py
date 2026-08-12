from dataclasses import dataclass

from Options import PerGameCommonOptions, Range, Toggle


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


@dataclass
class AzureDreamsOptions(PerGameCommonOptions):
    traps: EnableTraps
    trap_chance: TrapChance
