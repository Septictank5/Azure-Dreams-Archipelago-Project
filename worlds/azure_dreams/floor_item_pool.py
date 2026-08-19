"""Floor spawn-pool rebalance: the tower's random item odds, retuned for ADAP.

Vanilla Azure Dreams is built for many short tower entries over a long game;
an Archipelago seed is closer to a single sustained run. A handful of items
the run needs are locked behind "mode 2" floors (definition-flag bit 0x40 -
only spawnable after an elevator-type-4 arrival) or excluded outright (bit
0x10), and the stock rarity curve leaves the class-3 tier effectively
unobtainable in one entry (weight 1 in a ~5700-weight pool). This module is
the deliberate behaviour change that fixes both, applied unconditionally to
every generated seed. It is NOT a bug repair - that is why it does not live
in `native_bugfixes.py`, whose charter excludes behaviour changes.

The mechanism it edits is documented in `docs/game/floor-generation.md` §10
"The random item roll, decoded (2026-08-09)". In one paragraph: every floor
build, the overlay routine `0x8001E994` walks the resident item-definition
tables and turns each item's flags halfword (row `+0`) into a weight -
bits `0x3000` select rarity class 0-3, vanilla weights 128/85/32/1; bit
`0x10` excludes the item; bit `0x40` restricts it to mode-2 floors. The
picker `0x8001EAA4` re-derives the same weights while walking to the rolled
item, so the class-weight immediates exist in BOTH routines and must agree.

Two kinds of edit, both verified against the original bytes at import:

* **Flag halfwords** (resident `SLUS_006.14` `.data`, one write site each):
  re-enable Shomuro/Paralyze/Sleep Herb, Geropita Fruit, Slow Seed, Flat
  Scroll, Seraphim Sword and Trained Wand, and shift a few stock items
  between classes. Per-item rationale sits on each record. The Seraphim
  enabled here is category 15 id 12 - the ordinary sword the AP item pool
  already ships; id 11 is the scripted-boss cutscene sword and stays
  excluded (`item_manifest.py` records that split).
* **Class-weight immediates** (floor-generation overlay, both DUNGEON.BIN
  copies): class 0 128 -> 108 and class 3 1 -> 6, in the builder and the
  picker. Classes 1 (85) and 2 (32) are untouched. The class-3 weight is
  not an immediate in vanilla - it rides a delay-slot `addu` from a
  register holding 1 - so both class-3 sites become `addiu` immediates.

Resulting normal-floor pool total: 5904 (was 5746); a class-3 item is
6/5904 ≈ 1/984 per rolled item instead of 1/5746. Mode-2 floors still add
their remaining exclusive items on top.

The weight edits land inside the floor-generation package's shared range
(`0x80016000`-`0x80021FFF`, byte-identical across both copies), so the same
file-relative offsets apply to each copy - the standing both-copies rule
from `docs/game/floor-generation.md` §1. Nothing here overlaps the
production construction hook at `0x8001E630` or any other generated record;
`tools/Build-AdapTestDisc.py` refuses a disc where two records disagree
about a byte, so a future collision fails the build rather than shipping.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import save_removal, town_shop

# Definition-row flags halfword, bit meanings (docs/game/floor-generation.md §10).
FLAG_NEVER_SPAWN = 0x0010
FLAG_MODE2_ONLY = 0x0040
RARITY_MASK = 0x3000  # class = (flags >> 12) & 3

# The retuned class weights. Vanilla is (128, 85, 32, 1).
CLASS_WEIGHTS = (108, 85, 32, 6)


@dataclass(frozen=True)
class SpawnFlagEdit:
    name: str
    category: int
    item_id: int
    address: int  # resident RAM address of the definition row's flags halfword
    original: int
    replacement: int
    reason: str


# The definition-table bases these rows live in, straight from the category
# records at 0x80073414 (+0xC of each 0x14-byte record). Used only to assert
# that each edit's address is exactly `table + id * 0x14`.
_DEFINITION_TABLES = {
    0x01: 0x80072DC0,  # herb
    0x02: 0x80072F00,  # fruit
    0x03: 0x80072FDC,  # seed
    0x04: 0x800730B8,  # ball
    0x05: 0x80073220,  # scroll
    0x0A: 0x800733C4,  # sand
    0x0F: 0x8007249C,  # sword
    0x10: 0x800725DC,  # wand
}

SPAWN_FLAG_EDITS: tuple[SpawnFlagEdit, ...] = (
    # --- brought back from mode-2-only (bit 0x40 cleared) ---
    SpawnFlagEdit(
        "Shomuro Herb", 0x01, 7, 0x80072E4C, 0x0048, 0x0008,
        "Defense counterpart to the always-available Hazak Herb (attack); "
        "recoverable defense keeps block fusions viable in a single entry. "
        "Class 0, symmetric with Hazak's class 0.",
    ),
    SpawnFlagEdit(
        "Paralyze Herb", 0x01, 10, 0x80072E88, 0x0048, 0x2008,
        "Reliable disable - defined turn count, always > 1 turn. Class 2.",
    ),
    SpawnFlagEdit(
        "Sleep Herb", 0x01, 13, 0x80072EC4, 0x0048, 0x1008,
        "Weaker disable - proximity wake-up odds make it unreliable at "
        "point-blank. Class 1, one tier more common than Paralyze.",
    ),
    SpawnFlagEdit(
        "Geropita Fruit", 0x02, 10, 0x80072FC8, 0x1048, 0x1008,
        "Un-gated at its stock class 1.",
    ),
    SpawnFlagEdit(
        "Slow Seed", 0x03, 9, 0x80073090, 0x0048, 0x2008,
        "Strong throwable; class 2 rather than the class 0 its stock byte "
        "would give.",
    ),
    SpawnFlagEdit(
        "Flat Scroll", 0x05, 6, 0x80073298, 0x0048, 0x2008,
        "Very powerful; class 2 rather than stock class 0.",
    ),
    SpawnFlagEdit(
        "Trained Wand", 0x10, 2, 0x80072604, 0xB040, 0xB000,
        "Un-gated at its stock class 3 (now weight 6).",
    ),
    # --- brought back from never-spawn (bit 0x10 cleared) ---
    SpawnFlagEdit(
        "Seraphim Sword", 0x0F, 12, 0x8007258C, 0x8210, 0xB200,
        "The ordinary Seraphim the AP pool ships (id 11, the scripted-boss "
        "version, stays excluded). Class 3 alongside Dark/Holy. Bit 0x200 "
        "is unattributed and preserved.",
    ),
    # --- removed from the floors (bit 0x10 set) ---
    SpawnFlagEdit(
        "Red Sand", 0x0A, 1, 0x800733D8, 0x1008, 0x1018,
        "2026-08-16: the sands are the blacksmith's progressive unlocks now "
        "(three Red, three Blue in the Archipelago pool; each raises his weapon / "
        "shield temper level and never enters the bag), so the floors stop "
        "dropping them. Was class 1 -> 0 as a commodity consumable.",
    ),
    SpawnFlagEdit(
        "Blue Sand", 0x0A, 2, 0x800733EC, 0x1008, 0x1018,
        "Same as Red Sand.",
    ),
    SpawnFlagEdit(
        "White Sand", 0x0A, 3, 0x80073400, 0x1808, 0x1818,
        "2026-08-16: the ball charger's progressive unlock (three in the pool, "
        "each raises the charger's per-visit allowance to 1/2/3 charges "
        "and never enters the bag - "
        "docs/systems/fortune-teller.md section 5), so the floors stop dropping "
        "the free version. Class 1 kept; bit 0x800 is unattributed and preserved.",
    ),
    SpawnFlagEdit(
        "Laev Fruit", 0x02, 6, 0x80072F78, 0x1008, 0x2008,
        "The one item with no found use; class 1 -> 2 nudges everything "
        "better upward.",
    ),
    SpawnFlagEdit(
        "Money Wand", 0x10, 5, 0x80072640, 0x2000, 0x1000,
        "A money item, same practical tier as the class-1 Gold Sword; "
        "class 2 -> 1.",
    ),
    SpawnFlagEdit(
        "Blaze Ball", 0x04, 2, 0x800730E0, 0x2001, 0x1001,
        "Weakest offense ball; class 2 -> 1.",
    ),
    SpawnFlagEdit(
        "Sleep Ball", 0x04, 13, 0x800731BC, 0x2004, 0x1004,
        "Low on the ball totem but useful; class 2 -> 1.",
    ),
)


@dataclass(frozen=True)
class WeightCodeEdit:
    name: str
    address: int  # runtime address inside the floor-generation package
    original: int  # instruction word being replaced
    replacement: int
    reason: str


# The four class-weight sites. Builder immediates feed the cumulative table at
# 0x8001F6F8; picker immediates re-derive per-item weights during the walk.
# The pairs MUST encode the same weights or the two-level roll desyncs.
WEIGHT_CODE_EDITS: tuple[WeightCodeEdit, ...] = (
    WeightCodeEdit(
        "builder class-0 weight", 0x8001EA40,
        0x24040080,  # addiu a0,zero,0x80
        0x24040000 | CLASS_WEIGHTS[0],
        "128 -> 108 in the cumulative-table builder (0x8001E994).",
    ),
    WeightCodeEdit(
        "builder class-3 weight", 0x8001EA54,
        0x00402021,  # addu a0,v0,zero  (v0 holds 1)
        0x24040000 | CLASS_WEIGHTS[3],  # addiu a0,zero,<w3>
        "1 -> 6; vanilla rode a register holding 1, replaced with an "
        "immediate in the same delay slot.",
    ),
    WeightCodeEdit(
        "picker class-0 weight", 0x8001EBD0,
        0x24050080,  # addiu a1,zero,0x80
        0x24050000 | CLASS_WEIGHTS[0],
        "Same 128 -> 108 in the item walk (0x8001EAA4).",
    ),
    WeightCodeEdit(
        "picker class-3 weight", 0x8001EBE4,
        0x00402821,  # addu a1,v0,zero  (v0 holds 1)
        0x24050000 | CLASS_WEIGHTS[3],  # addiu a1,zero,<w3>
        "Same 1 -> 6, picker's delay slot.",
    ),
)

# The floor-generation package's two on-disc copies (docs/game/floor-generation.md §1).
FLOOR_GENERATION_FILE_OFFSETS = (0x28_D000, 0x50_8800)


def _assert_layout() -> None:
    for edit in SPAWN_FLAG_EDITS:
        table = _DEFINITION_TABLES[edit.category]
        expected = table + edit.item_id * 0x14
        if edit.address != expected:
            raise ValueError(
                f"{edit.name!r}: flags halfword should be at 0x{expected:08X} "
                f"(table 0x{table:08X} + id {edit.item_id} * 0x14), record says "
                f"0x{edit.address:08X}."
            )
        if edit.original == edit.replacement:
            raise ValueError(f"{edit.name!r}: edit is a no-op.")
        changed = edit.original ^ edit.replacement
        if changed & ~(FLAG_NEVER_SPAWN | FLAG_MODE2_ONLY | RARITY_MASK):
            raise ValueError(
                f"{edit.name!r}: touches bits 0x{changed:04X} outside the "
                "spawn-gate and rarity fields; element tags and the "
                "unattributed bits must ride through unchanged."
            )
    for edit in WEIGHT_CODE_EDITS:
        if not 0x8001_6000 <= edit.address < 0x8002_0000:
            raise ValueError(
                f"{edit.name!r} at 0x{edit.address:08X} is outside the range "
                "verified byte-identical across both package copies."
            )
        if edit.address % 4:
            raise ValueError(f"{edit.name!r} is not word-aligned.")


_assert_layout()


# The three sands' never-spawn edits belong to the temper system (blacksmith +
# ball charger); with that option off the floors drop them as in vanilla.
TEMPER_SYSTEM_EDIT_NAMES = frozenset({"Red Sand", "Blue Sand", "White Sand"})


def iter_spawn_flag_file_patches(temper_system: bool = True) -> tuple[tuple[int, bytes], ...]:
    """(SLUS_006.14 file offset, halfword) per flag edit; the sand edits only
    while the temper system (their NPCs) is on."""

    return tuple(
        (
            save_removal.SLUS_HEADER_SIZE
            + edit.address
            - save_removal.SLUS_LOAD_ADDRESS,
            struct.pack("<H", edit.replacement),
        )
        for edit in SPAWN_FLAG_EDITS
        if temper_system or edit.name not in TEMPER_SYSTEM_EDIT_NAMES
    )


def iter_weight_code_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(DUNGEON.BIN file offset, instruction word), for BOTH package copies."""

    return tuple(
        (
            copy_offset + edit.address - 0x8000_0000,
            struct.pack("<I", edit.replacement),
        )
        for copy_offset in FLOOR_GENERATION_FILE_OFFSETS
        for edit in WEIGHT_CODE_EDITS
    )


def _iter_mode2_raw_patches(
    file_start_lba: int,
    file_patches: tuple[tuple[int, bytes], ...],
) -> tuple[tuple[int, bytes], ...]:
    result: list[tuple[int, bytes]] = []
    for file_offset, data in file_patches:
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            within_sector = current % 2_048
            length = min(len(data) - copied, 2_048 - within_sector)
            raw_offset = town_shop.mode2_file_offset_to_raw_offset(
                file_start_lba,
                current,
            )
            result.append((raw_offset, data[copied : copied + length]))
            copied += length
    return tuple(result)


def iter_floor_item_pool_raw_patches(temper_system: bool = True) -> tuple[tuple[int, bytes], ...]:
    return (
        *_iter_mode2_raw_patches(
            save_removal.SLUS_FILE_START_LBA,
            iter_spawn_flag_file_patches(temper_system),
        ),
        *_iter_mode2_raw_patches(
            save_removal.DUNGEON_FILE_START_LBA,
            iter_weight_code_file_patches(),
        ),
    )


def append_floor_item_pool_ppf_records(ppf: bytearray, temper_system: bool = True) -> None:
    for raw_offset, data in iter_floor_item_pool_raw_patches(temper_system):
        ppf.extend(struct.pack("<IB", raw_offset, len(data)))
        ppf.extend(data)
