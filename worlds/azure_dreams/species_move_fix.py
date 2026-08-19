"""The group-V move-routine fix: five species packages made to walk like the other 39.

**Why this exists.** A carrier - the monster spawned holding the floor's third
check and forced onto Picket's holding AI - sometimes melee-attacked Koh
instead of fleeing. The 2026-08-17 dive (`docs/game/carrier-attack-theories.md`
§1) found the cause is not in the AI at all: every species package compiles its
own copy of the move routine (verb 11, walk), and five of the 44 were built from
a different source snapshot. When a walk is already queued (`unit+0x46 &
0x8000`, which is *always* true on the frame `ai_decide` queues one) the 39
"group P" routines branch straight to the 8-direction step loop and walk the
heading `ai_decide` chose. The five "group V" routines - Nyuel `0x0E`, Pulunpa
`0x15`, Volcano `0x1C`, Cyclone `0x1D`, Golem `0x2C` - branch into the
face-Koh block instead, which re-aims the unit at Koh when the aware bit is set
and throws the flee heading away. Not adjacent: it chases. Adjacent: it stands,
keeps the pending bit, and the next entry takes the ungranted branch into the
bump helper, which hits whatever is on the faced tile. Deterministic, no race,
and structurally impossible for a vanilla holding Picket (group P), which is
why the reporter never saw a thief attack.

**What this does.** Picket's and each group-V species' move routines are the
same 466-word routine differing in exactly two words - measured on the vanilla
disc for all seven pre-linked copies of all six packages, masking only `j`/`jal`
targets (which differ by package layout, not by logic):

    move+0x324   group V: bne v0,zero,+0x90  -> face-Koh block   (0x14400023)
                 group P: bne v0,zero,+0x168 -> step loop        (0x14400059)
    move+0x328   group V: nop                                    (0x00000000)
                 group P: move s1,zero   (step-loop attempt ctr) (0x00008821)

So the conversion is those two words, in place, per copy: 5 species x 7 copies
= 35 sites, 70 words, no payload space. **Both words are load-bearing** - the
delay slot zeroes `s1` for the step loop, and retargeting the branch without it
starts the loop from garbage. The face-Koh block itself is shared with group P
(reached from `+0x344`, `+0x358`, `+0x374` in every package), so nothing is
deleted and nothing else is disturbed.

Golem is patched too although it stays out of `CARRIER_POOL` (its `+0xAE`
latch is a separate defect, `monster_spawns.GOLEM`): with all five converted
there is no group V left on the disc, so the exclusion list stops needing to
know the group ever existed.

**Where the bytes are.** The species archive is not a named ISO file: seven
copies per species, 12 sectors apart, at absolute LBA `17131 + species*84 +
12*k`, copy k linked for base `0x80170000 - 0x6000*k` (`tools/
Census-AdapSpeciesPackages.py`, `docs/game/species-package-ram.md` §1; the
loader reads 12 sectors from LBA+1 into base+0x800). Nothing else in ADAP
writes the archive - all 44 packages were byte-identical between the m7 and
canary discs - so these are plain appended records; the disc builder refuses
any two records that disagree about a byte, so a future collision fails the
build rather than shipping.

**This is a behaviour change to five vanilla combat routines**, not a crash
repair, and it is the one exception to `native_bugfixes`'s "our code or a hook
into a call site we own" rule. Vanilla monsters of these species are aggressive
and never run the holding branch, so their re-aim was nearly invisible in the
retail game; after this they aim once (in `ai_decide`) like the other 39. Flag
below if a ride ever needs the retail behaviour back.
"""

from __future__ import annotations

import struct

# The switch. Off = the five packages walk as retail built them, and the pool
# should then subtract GROUP_V_SPECIES again (carrier-attack-theories.md §1).
CARRIER_GROUP_V_FIX = True

RAW_SECTOR_SIZE = 2_352
FORM1_USER_SIZE = 2_048
_USER_DATA_START = 24

# The species archive on the disc.
ARCHIVE_FIRST_LBA = 17_131          # copy 0 of species 0 (nothing lives there)
ARCHIVE_SPECIES_STRIDE_SECTORS = 84
ARCHIVE_COPY_STRIDE_SECTORS = 12
ARCHIVE_COPIES = 7
ARCHIVE_SLOT_SIZE = 0x6000
ARCHIVE_COPY_BASE = tuple(0x8017_0000 - ARCHIVE_SLOT_SIZE * k for k in range(ARCHIVE_COPIES))

# The move routine's offset inside each package - the same in all seven copies
# of a species (copies differ only by link base). Copy-3 addresses, the ones
# carrier-attack-theories.md quotes, are `0x8015E000 + offset`.
MOVE_ROUTINE_PACKAGE_OFFSET: dict[int, int] = {
    0x0E: 0x1410,   # Nyuel   (copy 3: 0x8015F410, branch 0x8015F734)
    0x15: 0x1468,   # Pulunpa (copy 3: 0x8015F468, branch 0x8015F78C)
    0x1C: 0x1420,   # Volcano (copy 3: 0x8015F420, branch 0x8015F744)
    0x1D: 0x18B4,   # Cyclone (copy 3: 0x8015F8B4, branch 0x8015FBD8)
    0x2C: 0x18F8,   # Golem   (copy 3: 0x8015F8F8, branch 0x8015FC1C)
}
GROUP_V_SPECIES: frozenset[int] = frozenset(MOVE_ROUTINE_PACKAGE_OFFSET)
PICKET_MOVE_ROUTINE_PACKAGE_OFFSET = 0x18DC   # the group-P reference (0x8015F8DC)

# The pending-walk branch and its delay slot.
PENDING_WALK_BRANCH_OFFSET = 0x324
FACE_KOH_BLOCK_OFFSET = 0x3B4      # group V's target
STEP_LOOP_OFFSET = 0x48C           # group P's target
GROUP_V_WORDS = (0x1440_0023, 0x0000_0000)   # bne v0,zero,+0x90 ; nop
GROUP_P_WORDS = (0x1440_0059, 0x0000_8821)   # bne v0,zero,+0x168 ; move s1,zero


def _branch_target(word: int, site: int) -> int:
    imm = word & 0xFFFF
    if imm >= 0x8000:
        imm -= 0x10000
    return site + 4 + imm * 4


assert GROUP_V_WORDS[0] >> 16 == GROUP_P_WORDS[0] >> 16 == 0x1440, "bne v0,zero"
assert _branch_target(GROUP_V_WORDS[0], PENDING_WALK_BRANCH_OFFSET) == FACE_KOH_BLOCK_OFFSET
assert _branch_target(GROUP_P_WORDS[0], PENDING_WALK_BRANCH_OFFSET) == STEP_LOOP_OFFSET
assert GROUP_P_WORDS[1] == 0x21 | (17 << 11), "addu s1,zero,zero"


def archive_copy_lba(species: int, copy: int) -> int:
    return (
        ARCHIVE_FIRST_LBA
        + species * ARCHIVE_SPECIES_STRIDE_SECTORS
        + copy * ARCHIVE_COPY_STRIDE_SECTORS
    )


def archive_raw_offset(species: int, copy: int, package_offset: int) -> int:
    """Raw MODE2/2352 disc offset of a byte inside one pre-linked package copy."""
    sector, within = divmod(package_offset, FORM1_USER_SIZE)
    return (archive_copy_lba(species, copy) + sector) * RAW_SECTOR_SIZE + _USER_DATA_START + within


def iter_group_v_sites() -> tuple[tuple[int, int, int], ...]:
    """(species, copy, raw offset of move+0x324) for all 35 sites."""
    return tuple(
        (species, copy, archive_raw_offset(species, copy, offset + PENDING_WALK_BRANCH_OFFSET))
        for species, offset in sorted(MOVE_ROUTINE_PACKAGE_OFFSET.items())
        for copy in range(ARCHIVE_COPIES)
    )


def _assert_layout() -> None:
    for _, _, raw in iter_group_v_sites():
        within = raw % RAW_SECTOR_SIZE
        if within < _USER_DATA_START or within + 8 > _USER_DATA_START + FORM1_USER_SIZE:
            raise ValueError(f"group-V site at raw 0x{raw:X} leaves its sector's user data")


_assert_layout()

GROUP_V_REPLACEMENT = struct.pack("<II", *GROUP_P_WORDS)
GROUP_V_ORIGINAL = struct.pack("<II", *GROUP_V_WORDS)


def iter_species_move_fix_raw_patches() -> tuple[tuple[int, bytes], ...]:
    if not CARRIER_GROUP_V_FIX:
        return ()
    return tuple((raw, GROUP_V_REPLACEMENT) for _, _, raw in iter_group_v_sites())


def append_species_move_fix_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_species_move_fix_raw_patches():
        ppf.extend(struct.pack("<IB", raw_offset, len(data)))
        ppf.extend(data)
