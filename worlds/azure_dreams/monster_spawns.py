"""Per-floor monster spawn tables, and the carrier's slot in them.

The tower's monster population comes from a 16-slot table per floor, two bytes
per slot: unit id, then **level** (a literal level, not an index).  On disc it is

    DUNGEON.BIN + 0x7A4800 + (floor - 1) * 2048

and at runtime `[0x80083478]` points at the loaded copy.  `docs/game/floor-generation.md`
owns the format; `docs/game/monster-ai.md` owns what ADAP does with it.

**Why this module exists.** Only four monster types can be live on a floor at
once - a six-slot graphics allocator (`0x800A1618`) with two reserved for Koh and
his familiar - so the location-check carrier, which forces its own spawn first
and therefore claims a slot first, costs the floor one of its four.  Rather than
let the loser be whichever type happened to draw last, this picks the casualty
deliberately, per seed, and hands its slots to the survivors.  The side effect is
the point: **every run's floors feel a little different.**

**Two reservations are never touched.** Barong holds exactly one slot on floors
16, 26 and 36 and is the tower's slot-machine monster; the water medal's Picket
holds exactly one slot on floor 25 and the quest depends on it.  Removing either
would be a silent, run-ruining regression, so `PROTECTED_SLOTS` is asserted
against the vanilla tables at import.
"""

from __future__ import annotations

from random import Random

# Vanilla per-floor monster spawn tables, read off the original disc
# 2026-08-14: DUNGEON.BIN + 0x7A4800 + (floor-1)*2048, 16 slots of
# (unit id, level).  Ground truth for every write this module makes.
VANILLA_SPAWN_TABLES: tuple[tuple[tuple[int, int], ...], ...] = (
    # floor 1
    ((0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x17,  1), (0x17,  1), (0x17,  1), (0x17,  1)),
    # floor 2
    ((0x17,  1), (0x17,  1), (0x17,  1), (0x17,  1), (0x17,  1), (0x17,  1), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x06,  3)),
    # floor 3
    ((0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x15,  1), (0x15,  1), (0x15,  1), (0x06,  3), (0x06,  3), (0x06,  3), (0x1D,  4), (0x1D,  4), (0x1D,  4)),
    # floor 4
    ((0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x16,  2), (0x1D,  4), (0x1D,  4), (0x1D,  4), (0x1D,  4), (0x1D,  4), (0x06,  3), (0x06,  3), (0x06,  3), (0x06,  3), (0x06,  3), (0x19,  5)),
    # floor 5
    ((0x06,  3), (0x06,  3), (0x06,  3), (0x06,  3), (0x1D,  4), (0x1D,  4), (0x1D,  4), (0x1D,  4), (0x19,  5), (0x19,  5), (0x19,  5), (0x19,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5)),
    # floor 6
    ((0x19,  5), (0x19,  5), (0x19,  5), (0x19,  5), (0x19,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1D,  4), (0x1D,  4), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6)),
    # floor 7
    ((0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x19,  5), (0x19,  5), (0x19,  5), (0x18,  7), (0x18,  7), (0x18,  7)),
    # floor 8
    ((0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x18,  7), (0x18,  7), (0x18,  7), (0x18,  7), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x1E,  5), (0x10,  8), (0x10,  8)),
    # floor 9
    ((0x10,  8), (0x10,  8), (0x10,  8), (0x10,  8), (0x10,  8), (0x18,  7), (0x18,  7), (0x18,  7), (0x18,  7), (0x18,  7), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1B,  6), (0x1A,  9), (0x1A,  9)),
    # floor 10
    ((0x10,  8), (0x10,  8), (0x10,  8), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x16, 10), (0x16, 10), (0x16, 10), (0x16, 10), (0x16, 10), (0x16, 10), (0x1C, 11), (0x1C, 11), (0x1C, 11)),
    # floor 11
    ((0x16, 10), (0x16, 10), (0x16, 10), (0x16, 10), (0x16, 10), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1C, 11), (0x1C, 11), (0x1C, 11), (0x1C, 11), (0x08, 12), (0x08, 12), (0x08, 12)),
    # floor 12
    ((0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1A,  9), (0x1A, 11), (0x1C, 11), (0x1C, 11), (0x1C, 11), (0x1C, 11), (0x08, 12), (0x08, 12), (0x08, 12), (0x08, 12), (0x21, 13), (0x21, 13)),
    # floor 13
    ((0x08, 12), (0x08, 12), (0x08, 12), (0x08, 12), (0x08, 12), (0x08, 12), (0x21, 13), (0x21, 13), (0x21, 13), (0x21, 13), (0x1C, 11), (0x1C, 11), (0x1C, 11), (0x0E, 14), (0x0E, 14), (0x0E, 14)),
    # floor 14
    ((0x0E, 14), (0x0E, 14), (0x0E, 14), (0x0E, 14), (0x0E, 14), (0x21, 13), (0x21, 13), (0x21, 13), (0x21, 13), (0x16, 15), (0x16, 15), (0x16, 15), (0x16, 15), (0x29, 16), (0x29, 16), (0x29, 16)),
    # floor 15
    ((0x29, 16), (0x29, 16), (0x29, 16), (0x29, 16), (0x29, 16), (0x29, 16), (0x0E, 14), (0x0E, 14), (0x0E, 14), (0x0E, 14), (0x0E, 14), (0x16, 15), (0x16, 15), (0x16, 15), (0x16, 15), (0x21, 13)),
    # floor 16
    ((0x16, 15), (0x16, 15), (0x16, 15), (0x16, 15), (0x16, 15), (0x29, 16), (0x29, 16), (0x29, 16), (0x29, 16), (0x29, 16), (0x1E, 15), (0x1E, 15), (0x1E, 15), (0x1E, 15), (0x1E, 15), (0x1F, 20)),
    # floor 17
    ((0x16,  2), (0x16,  2), (0x16, 10), (0x16, 10), (0x16, 10), (0x16, 15), (0x16, 15), (0x1E, 15), (0x1E, 15), (0x1E, 15), (0x29, 16), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17)),
    # floor 18
    ((0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x1E, 15), (0x1E, 15), (0x1E, 15), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18)),
    # floor 19
    ((0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x20, 17), (0x20, 17), (0x20, 17), (0x20, 17), (0x1E, 15), (0x1E, 15), (0x22, 19), (0x22, 19), (0x22, 19), (0x22, 19)),
    # floor 20
    ((0x22, 19), (0x22, 19), (0x22, 19), (0x22, 19), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x0C, 18), (0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21)),
    # floor 21
    ((0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x22, 19), (0x22, 19), (0x22, 19), (0x22, 19), (0x15,  1), (0x15,  1), (0x15, 16)),
    # floor 22
    ((0x15,  1), (0x15,  1), (0x15,  1), (0x15,  1), (0x15, 16), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x14, 22), (0x14, 22)),
    # floor 23
    ((0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x12, 20), (0x14, 22), (0x14, 22), (0x14, 22), (0x14, 22), (0x23, 23)),
    # floor 24
    ((0x14, 22), (0x14, 22), (0x14, 22), (0x14, 22), (0x14, 22), (0x14, 22), (0x14, 22), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x24, 21), (0x23, 23), (0x26, 24), (0x26, 24), (0x26, 24)),
    # floor 25
    ((0x23, 23), (0x23, 23), (0x23, 23), (0x23, 23), (0x23, 23), (0x23, 23), (0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x14, 22), (0x14, 22), (0x14, 22), (0x20, 17)),
    # floor 26
    ((0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x26, 24), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x27, 26), (0x27, 26), (0x27, 26), (0x27, 26), (0x1F, 20)),
    # floor 27
    ((0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x27, 26), (0x27, 26), (0x27, 26), (0x27, 26), (0x26, 24), (0x26, 24), (0x26, 24), (0x1E, 25), (0x1E, 25), (0x1E, 25), (0x1E, 25)),
    # floor 28
    ((0x1E, 25), (0x1E, 25), (0x1E, 25), (0x1E, 25), (0x1E, 25), (0x27, 26), (0x27, 26), (0x27, 26), (0x27, 26), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x25, 27), (0x25, 27), (0x25, 27)),
    # floor 29
    ((0x1E, 25), (0x1E, 25), (0x1E, 25), (0x1E, 25), (0x1E, 25), (0x25, 27), (0x25, 27), (0x25, 27), (0x25, 27), (0x25, 27), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x0A, 25), (0x28, 28)),
    # floor 30
    ((0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x25, 27), (0x25, 27), (0x25, 27), (0x1E, 25), (0x1E, 25), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29)),
    # floor 31
    ((0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x25, 27), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29)),
    # floor 32
    ((0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x28, 28), (0x28, 28), (0x28, 28), (0x28, 28), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30)),
    # floor 33
    ((0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29), (0x2B, 29), (0x2B, 29), (0x2B, 29), (0x2C, 30), (0x2C, 30)),
    # floor 34
    ((0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2B, 29), (0x2B, 29)),
    # floor 35
    ((0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29), (0x2D, 30)),
    # floor 36
    ((0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x1F, 20)),
    # floor 37
    ((0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29)),
    # floor 38
    ((0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29)),
    # floor 39
    ((0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2D, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2C, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x2A, 30), (0x03, 29), (0x03, 29), (0x03, 29), (0x03, 29)),
)


SPAWN_TABLE_DUNGEON_OFFSET = 0x7A_4800
SPAWN_TABLE_STRIDE = 2048
SPAWN_TABLE_FLOORS = 39
SPAWN_TABLE_SLOTS = 16
SPAWN_TABLE_BYTES = SPAWN_TABLE_SLOTS * 2

BARONG = 0x1F
PICKET = 0x20

# (floor, unit id) pairs that must survive every rewrite.  Barong is the
# slot-machine monster the user farms; the floor-25 Picket carries the water
# medal (`docs/game/floor-generation.md` §10).
PROTECTED_SLOTS = frozenset({(16, BARONG), (26, BARONG), (36, BARONG), (25, PICKET)})

CARRIER_LEVEL = 1


def _species(floor_index: int) -> list[int]:
    seen: list[int] = []
    for unit, _ in VANILLA_SPAWN_TABLES[floor_index]:
        if unit not in seen:
            seen.append(unit)
    return seen


# Every species the vanilla tables ever use, minus Barong: a carrier that looked
# like the slot machine would be actively misleading.  Drawing only from species
# the game already spawns somewhere keeps us away from the scripted actors
# (Ghosh, Selfi, the walls, Beldo), which have no business being constructed by
# the ordinary spawner.
# Two more are out because they defeat the carrier's whole premise, a monster
# you can see is out of place: Stealth (0x23) is invisible by species mechanic,
# and Manoeva (0x1E) sits disguised as an item until touched.
STEALTH = 0x23
MANOEVA = 0x1E

# **Golem is out because its package can switch our forced AI off.**  All 44
# species packages were extracted from the disc archive and each one's
# per-frame handler was run against one identical carrier state in the MIPS
# interpreter (`docs/game/monster-ai.md` §2b).  Every species takes Picket's
# holding branch, turns away and walks - except that Golem's handler carries a
# private latch, `unit+0xAE`, and while it is set the handler jumps *past* the
# `ai_decide` call to the action switch and runs the move routine on whatever
# heading it already had.  The idle branch faces a monster at Koh between
# turns, so a latched Golem bump-attacks every turn instead of fleeing, which
# is exactly the floor-2 report.  Golem is the ONLY species of the 44 whose
# private state can do this (Mandara has a latch at +0xA6 that does not reach
# the move routine), so this is a one-species exclusion, not a class of them.
GOLEM = 0x2C

# **Eleven species never drop what they carry, so they cannot be carriers.**
# Every species package carries its OWN unit runner and its own state table,
# and state 8 (death) is per package: 33 of the 44 route it to the overlay's
# full death routine (`0x800AD058`, which tosses `unit+0x48` onto the floor),
# but these eleven link a slimmer local death that unlinks the unit and never
# reads the carried slot.  Measured 2026-08-15 off the disc archive for all 44
# and confirmed in the MIPS interpreter on the ride-3 states: the floor-3
# Unicorn's own death (`0x80161450`) deletes the unit with the marker still in
# `+0x48`, the Golem's goes through `0x800AD058` and tosses it - which is
# exactly the two kills the user reported (`docs/game/monster-ai.md` §2c).
# A carrier that cannot drop is a lost check, so the whole set is out.  Kid,
# Ifrit, Battnel and Univern are not wild species anyway; the seven that were
# in the pool are Flame, Arachne, Unicorn, Baloon, Kraken, Zu and Mandara.
# Retargeting a package's death entry at the overlay routine on the disc would
# buy any of them back (§2c has the sector map); not done.
NO_DROP_DEATH_SPECIES: frozenset[int] = frozenset(
    {0x04, 0x05, 0x06, 0x0C, 0x0D, 0x11, 0x12, 0x19, 0x21, 0x26, 0x27}
)

# **Troll never receives the marker.**  Its package constructor writes a weapon
# into the carried slot (`+0x48 = 0x0E`, `+0x49 = 0x0F`; Hammer under floor 10,
# Bow Gun under 13, else 0x0D) *before* the spawner's carried-item gate, so
# `lbu v0,0x49(s0)` at 0x800A0B2C is non-zero and the `jal` at 0x800A0B3C -
# where the claim stub lives - is skipped.  The Troll holds its weapon, fails
# the dispatch test, runs Troll AI, and the floor's third check has no carrier
# item at all (`docs/game/carrier-attack-theories.md` §4, 2026-08-17).
TROLL = 0x16

# Nyuel, Pulunpa, Volcano and Cyclone are NOT excluded, on purpose.  Their
# packages' move routines (with Golem's) were built from a different source
# snapshot and re-aim at Koh on a queued walk, which is what turned those
# carriers into attackers; `species_move_fix` converts all five on the disc to
# the other 39 species' routine (two words per copy), so they flee like the
# rest.  If that fix is ever switched off, subtract
# `species_move_fix.GROUP_V_SPECIES` here again.

CARRIER_POOL: tuple[int, ...] = tuple(
    sorted(
        {unit for floor in VANILLA_SPAWN_TABLES for unit, _ in floor}
        - {BARONG, STEALTH, MANOEVA, GOLEM, TROLL}
        - NO_DROP_DEATH_SPECIES
    )
)

# TESTING: force every floor's carrier to one species (a bisect knob).
# `patches/carrier-forced-spawn-v2` - the last build that rode clean - was
# hard-wired to a Snowman (0x0A).  Set this to 0x0A to rebuild that condition
# on today's code.  None = the per-seed draw.
CARRIER_SPECIES_OVERRIDE: int | None = None


def _assert_protected_slots_exist() -> None:
    for floor, unit in PROTECTED_SLOTS:
        table = VANILLA_SPAWN_TABLES[floor - 1]
        count = sum(1 for u, _ in table if u == unit)
        if count != 1:
            raise ValueError(
                f"floor {floor} should hold exactly one 0x{unit:02X} slot, holds {count}"
            )


_assert_protected_slots_exist()


def plan_floor_spawns(rng: Random) -> tuple[list[bytes], list[int]]:
    """Return (per-floor 32-byte tables, per-floor carrier species).

    For each floor: drop one unprotected species when the floor carries four or
    more, hand its slots to the survivors in proportion to how many slots they
    already had, and pick a carrier from outside the floor's own roster.  Floors
    that already carry three types (1 and 18) lose nothing - the carrier simply
    becomes their fourth.
    """
    tables: list[bytes] = []
    carriers: list[int] = []
    for index in range(SPAWN_TABLE_FLOORS):
        floor = index + 1
        table = list(VANILLA_SPAWN_TABLES[index])
        present = _species(index)
        removable = [
            unit for unit in present if (floor, unit) not in PROTECTED_SLOTS
        ]
        if len(present) >= 4 and len(removable) >= 2:
            doomed = rng.choice(removable)
            # Protected species must not *gain* slots either: Barong at four
            # slots is no longer a slot machine, and a second medal Picket is
            # its own kind of wrong.  They keep exactly the count vanilla gave
            # them, so the vacated slots go only to ordinary survivors.
            survivors = [
                (unit, level)
                for unit, level in table
                if unit != doomed and (floor, unit) not in PROTECTED_SLOTS
            ]
            table = [
                (unit, level) if unit != doomed else rng.choice(survivors)
                for unit, level in table
            ]
        if CARRIER_SPECIES_OVERRIDE is not None:
            carriers.append(CARRIER_SPECIES_OVERRIDE)
        else:
            carriers.append(
                rng.choice([unit for unit in CARRIER_POOL if unit not in present])
            )
        tables.append(bytes(byte for pair in table for byte in pair))
    return tables, carriers


def place_carrier_table(block: bytearray, carriers: "list[int]") -> None:
    """Write the per-floor carrier species byte table into the seed page.

    Imported here rather than at module scope because `patch` imports nothing
    from this module, and the layout constant belongs to `patch`.
    """
    from . import patch

    if len(carriers) != patch.CARRIER_SPECIES_TABLE_FLOORS:
        raise ValueError(
            f"Expected {patch.CARRIER_SPECIES_TABLE_FLOORS} carrier species, "
            f"got {len(carriers)}."
        )
    start = patch.CARRIER_SPECIES_TABLE_OFFSET
    block[start : start + len(carriers)] = bytes(carriers)
