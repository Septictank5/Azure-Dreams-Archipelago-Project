"""Wotta's pool house: the girls' spawn records, and the test that spawns them all.

Read 2026-08-15 off `Wotta_pool_house.sav` and TOWN.BIN with a headless Ghidra
pass over the interior overlay (`docs/systems/pool-house.md` owns the account;
`docs/game/dialogue-scripts.md` §4 has the shared machinery).

The pool-house interior is a self-contained scene program:

* **overlay** `TOWN.BIN+0x51E000`, loaded at `0x80016000` (code, then the
  scene table at `0x80018EE4` - fourteen 0x1C-byte actor entries - the
  variant rows they point at, and the SPAWN TEMPLATES at `0x80019724..`);
* **dialogue image** `TOWN.BIN+0x532FC0` (bundle 17), loaded over the tail
  of the overlay at `0x800196E0..0x8001FF20` AFTER the scene init has copied
  the templates into the runtime arena at `0x80019258`.

A spawn template is a run of 20-byte records ending in `00 80 00 00`:

    +0x00  u16  0x4000 = dormant (bit 0 of the high byte inverts the test)
    +0x02  u16  PRESENCE FLAG ID - the record instantiates only while this
                story flag is set; 1 means always (Wotta), 0x0607..0x060D are
                the seven girls, and each girl's flag id happens to equal her
                pool-house "character" number in the rotation table
    +0x04  u32  1 (2 for one girl, 0 for two props) - unread by us
    +0x08  u32  0 (runtime: scene word)   +0x0C u32 low = model (0x6F..0x81)
    +0x10  i16 X, +0x12 i16 Y  section-local

The activation test is `FUN_Town__8009DA50` (`if flag(rec+2) then create the
actor at rec+0x10/+0x12`), through the resident flag test `0x80033B2C`, and
the game's own rotation (`0x80016948`, called from Wotta's scripts) sets
exactly one of the seven girl flags per visit from the table at
`0x80019088` - which is why one girl at a time swims. Setting a record's
flag id to 1 spawns her regardless, and leaves the rotation, her dialogue
getter and her scripts exactly as they were.
"""

from __future__ import annotations

import struct
from pathlib import Path

# SHIPPED as an easter egg (2026-08-18; built 2026-08-15 as a test flag): every
# girl always present, and Wotta's quest pre-cleared through
# `town_shop.POOL_HOUSE_QUEST_DONE_FLAGS` so the pool is open from the first
# visit with no Water Medal errand. There is nothing to collect in there yet -
# it ships because a room full of girls who each say where they are standing is
# a fine thing to find, and because the intended end state (the Water Medal in
# the multiworld pool, the door locked with Wotta outside asking for it, the
# girls as checks) is a change to what this opens, not a change to whether it
# opens. Both halves go together, whichever way they go.
POOL_HOUSE_OPEN = True

TOWN_FILE_START_LBA = 3_077

# The interior overlay: file <-> runtime.
OVERLAY_FILE_OFFSET = 0x51E000
OVERLAY_RUNTIME_ADDRESS = 0x8001_6000
# The dialogue image that lands over the overlay's tail (for the record; the
# templates below are read from the OVERLAY copy, which is the one the scene
# init consumes before this image overwrites them).
DIALOGUE_IMAGE_FILE_OFFSET = 0x532FC0
DIALOGUE_IMAGE_RUNTIME_ADDRESS = 0x8001_96E0

SCENE_TABLE_ADDRESS = 0x8001_8EE4
SCENE_ENTRY_SIZE = 0x1C
SPAWN_RECORD_SIZE = 0x14
SPAWN_RECORD_FLAG_OFFSET = 0x02
SPAWN_RECORD_FACING_OFFSET = 0x04
SPAWN_RECORD_POSITION_OFFSET = 0x10

# The `+0x04` word's low byte is the spawn FACING, confirmed by the m4 ride:
# six girls carry 1 and face down; 0x060D (Patty) carries 2 and was the one
# girl facing left. Other actors in the scene use 0..3 there (entry 12's props
# 0x0100/0x0103/0x0003, the door triggers 0x0300). Setting hers to 1 turns her
# to match the row - one byte, and it leaves the rest of the word alone.
FACING_DOWN = 1
FACING_OVERRIDES: dict[int, int] = {0x060D: FACING_DOWN}
ALWAYS_PRESENT_FLAG = 0x0001

# TESTING: line the seven up in one row for easy conversation, instead of
# leaving them on their vanilla poolside spots.
#
# **THE SECTION BASE IS MEASURED, NOT INFERRED**, because inferring it is the
# documented way this goes wrong (`docs/systems/town-warp-and-uncle.md` §2.1:
# "measure the subject's own base as live-actor-position minus template-local;
# never infer a base from neighbouring records"). The m3 ride is the proof of
# that rule: this module first took section 0x0D's origin to be (0, 0) from a
# field of its 32-byte scene descriptor, and the girls landed outside the pool
# house on both axes.
#
# Measured instead from the girls themselves, on two discs at once - every
# record's live actor (a town prop updater, position at actor+0xA4/+0xA6)
# minus its own template-local coordinate:
#
#     m2 disc, vanilla spots   7/7 girls   live = local + (512, 512)
#     m3 disc, the first row   7/7 girls   live = local + (512, 512)
#
# so **SECTION_BASE = (512, 512)** and `local = global - SECTION_BASE`.
#
# The row anchors on the player's own measurements, taken standing where the
# row should run (Koh's global position from the motion block [0x800834A0]):
#
#     by the changing rooms  (1605, 795)   <- girl 1
#     a little to the right  (1683, 821)
#     the right-hand limit   (2404, 831)
#
# so **+X is screen-right** and the Y drift over that walk (36) is just free
# movement. Every vanilla record's X *and* Y is a multiple of 32, so the row
# keeps to that grid: anchor (1600, 800) global, stepping 128 - wider than the
# 78 between the first two checks - putting the last girl at global 2368,
# inside the 2404 limit.
POOL_HOUSE_LINE_UP = True
SECTION_BASE = (512, 512)
LINEUP_ANCHOR_GLOBAL = (1600, 800)
LINEUP_STEP_X = 128
LINEUP_LIMIT_GLOBAL_X = 2404      # the player's stated right-hand limit
POSITION_GRID = 32

# The seven girls' spawn records (scene entry -> template address, vanilla
# presence flag, model, vanilla section-local position). Entry 6 carries a
# second record (flag 0x0610) after hers; it is left alone.
GIRL_SPAWN_RECORDS: tuple[tuple[int, int, int, int, int, tuple[int, int]], ...] = (
    # entry, template runtime address, presence flag, +0x04 word, model, (x, y)
    (2, 0x8001_979C, 0x0607, 1, 0x78, (1056, 824)),
    (3, 0x8001_97C4, 0x0608, 1, 0x72, (1632, 672)),
    (4, 0x8001_97EC, 0x0609, 1, 0x7E, (1216, 736)),
    (5, 0x8001_9814, 0x060A, 1, 0x71, (1120, 800)),
    (6, 0x8001_988C, 0x060B, 1, 0x7F, (1504, 672)),
    (8, 0x8001_992C, 0x060C, 1, 0x77, (1120, 512)),
    (9, 0x8001_9954, 0x060D, 2, 0x7C, (1312, 992)),
)


def overlay_file_offset(runtime_address: int) -> int:
    return runtime_address - OVERLAY_RUNTIME_ADDRESS + OVERLAY_FILE_OFFSET


def dialogue_file_offset(runtime_address: int) -> int:
    return runtime_address - DIALOGUE_IMAGE_RUNTIME_ADDRESS + DIALOGUE_IMAGE_FILE_OFFSET


def global_to_local(x: int, y: int) -> tuple[int, int]:
    """A global position (what a save state reports) -> a spawn record's field."""
    return (x - SECTION_BASE[0], y - SECTION_BASE[1])


def local_to_global(x: int, y: int) -> tuple[int, int]:
    return (x + SECTION_BASE[0], y + SECTION_BASE[1])


def lineup_globals() -> tuple[tuple[int, int], ...]:
    """Where the row stands in the coordinates a save state reports."""
    anchor_x, anchor_y = LINEUP_ANCHOR_GLOBAL
    positions = tuple(
        (anchor_x + LINEUP_STEP_X * index, anchor_y)
        for index in range(len(GIRL_SPAWN_RECORDS))
    )
    if positions[-1][0] > LINEUP_LIMIT_GLOBAL_X:
        raise ValueError(
            f"The row runs to x={positions[-1][0]}, past the measured limit "
            f"{LINEUP_LIMIT_GLOBAL_X}."
        )
    for x, y in positions:
        if x % POSITION_GRID or y % POSITION_GRID:
            raise ValueError(f"({x}, {y}) is off the {POSITION_GRID}-unit grid.")
    return positions


def lineup_positions() -> tuple[tuple[int, int], ...]:
    """The row as SPAWN-RECORD (section-local) coordinates, left to right in
    presence-flag order (0x0607 leftmost).

    Ordering by flag rather than by vanilla position is deliberate: it makes
    "the third from the left" name a specific girl, so a play report can be
    tied back to a record without another save state.
    """
    return tuple(global_to_local(x, y) for x, y in lineup_globals())


def vanilla_spawn_record(presence_flag: int, word: int, model: int, x: int, y: int) -> bytes:
    """The 20 bytes the disc holds for a girl's record (used to pin the disc)."""
    return struct.pack("<HHIIIhh", 0x4000, presence_flag, word, 0, model, x, y)


def iter_pool_house_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN offset, bytes) edits to the interior overlay's spawn records.

    Two per girl at most: the presence-flag halfword -> 1 (always present),
    and, when POOL_HOUSE_LINE_UP is on, the section-local X/Y pair. Nothing
    else in the 20-byte record moves - same model, same variant rows, same
    dialogue.
    """
    if not POOL_HOUSE_OPEN:
        return ()
    patches: list[tuple[int, bytes]] = []
    positions = lineup_positions() if POOL_HOUSE_LINE_UP else None
    for index, (_, template, flag, _, _, _) in enumerate(GIRL_SPAWN_RECORDS):
        base = overlay_file_offset(template)
        patches.append(
            (base + SPAWN_RECORD_FLAG_OFFSET, struct.pack("<H", ALWAYS_PRESENT_FLAG))
        )
        if flag in FACING_OVERRIDES:
            patches.append(
                (base + SPAWN_RECORD_FACING_OFFSET, bytes((FACING_OVERRIDES[flag],)))
            )
        if positions is not None:
            patches.append(
                (base + SPAWN_RECORD_POSITION_OFFSET, struct.pack("<hh", *positions[index]))
            )
    if POOL_HOUSE_HELLO_DIALOGUE:
        town_bin = _read_town_bin()
        for index, (_, _, script, _, _, _) in enumerate(GIRL_DIALOGUE):
            patches.append(
                (dialogue_file_offset(script),
                 build_hello_script(index, vanilla_prologue(index, town_bin)))
            )
    return tuple(patches)


def iter_pool_house_raw_patches() -> tuple[tuple[int, bytes], ...]:
    """File patches split at Mode-2 Form-1 sector boundaries.

    TOWN.BIN is Mode-2, so a run longer than what is left in the current
    2048-byte sector does not continue at the next raw byte - it skips the
    sector header. The dialogue rewrites are hundreds of bytes and do cross
    sectors; the record edits do not, but they go through the same path.
    """
    from . import town_shop

    result: list[tuple[int, bytes]] = []
    for file_offset, data in iter_pool_house_file_patches():
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            length = min(len(data) - copied, 2_048 - current % 2_048)
            result.append(
                (town_shop.mode2_file_offset_to_raw_offset(TOWN_FILE_START_LBA, current),
                 data[copied:copied + length])
            )
            copied += length
    return tuple(result)


def append_pool_house_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_pool_house_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied:copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)) + record)
            copied += len(record)


# --- The girls' dialogue ------------------------------------------------------
# Each scene entry has a DIALOGUE TABLE of 8-byte rows `(u16 key, u16 state,
# u32 script)` that its getter searches by key; the key turns out to be the
# actor/portrait slot, which is why every girl's script opens
# `57 00 | 1C <slot> <n> | 57 <slot>` - window mode, attach the portrait, then
# set the window to her name plate. Everything after that is text.
#
# A girl's claimable region runs from her script to the next address the
# OVERLAY references in the dialogue image (that reference set is what makes a
# boundary; branch targets inside a script are not entry points). Measured
# 2026-08-15 with `tools/Report-AdapPoolHouseDialogue.py`; all seven are packed
# with **zero slack**, so the region size IS the vanilla script size.
#
# **The names, read off the plates on the m5 ride** (each girl's greeting named
# her position in the row, so one pass mapped every plate to a presence flag).
# This is the only way to get it: the plate comes from a name table keyed by
# the portrait slot, not from anything the record or the script says.
GIRL_NAMES: dict[int, str] = {
    0x0607: "Nico",
    0x0608: "Fur",
    0x0609: "Selfi",
    0x060A: "Cherrl",
    0x060B: "Vivian",
    0x060C: "Mia",
    0x060D: "Patty",
}

# (flag, dialogue table, script, region bytes, prologue bytes, actor slot)
GIRL_DIALOGUE: tuple[tuple[int, int, int, int, int, int], ...] = (
    (0x0607, 0x8001_8D34, 0x8001_BDA0, 476, 7, 0x0B),
    (0x0608, 0x8001_8D4C, 0x8001_BF7C, 872, 7, 0x09),
    (0x0609, 0x8001_8D64, 0x8001_C2E4, 552, 7, 0x07),
    (0x060A, 0x8001_8D7C, 0x8001_C50C, 680, 7, 0x0D),
    (0x060B, 0x8001_8DAC, 0x8001_C7B4, 512, 10, 0x0C),
    (0x060C, 0x8001_8DFC, 0x8001_CDD8, 420, 7, 0x0A),
    (0x060D, 0x8001_8E14, 0x8001_CF7C, 328, 7, 0x08),
)

# Girl 0x060B's table has two more rows, keys 0x39/0x3A, both pointing at a
# further 1060-byte script at 0x8001C9B4 (TOWN.BIN+0x536294). NOT claimed:
# what those keys select is unknown, so overwriting it could break a scene we
# have not seen. Recorded because it is the next 1 KB available here.
GIRL_ALTERNATE_SCRIPT = (0x8001_C9B4, 1060)

END_OF_SCRIPT = 0x01          # also the canvas fill - never zero-fill a script
WAIT_FOR_BUTTON = 0x11
PLAYER_NAME = b"\xFE\x00"

# CP932, as the interpreter's text path reads it (docs/game/dialogue-scripts.md
# §5). Verified by round-tripping every vanilla girl script through the decoder
# in `tools/Decode-AdapTownScript.py`.
_PUNCTUATION = {" ": b"\x81\x40", ",": b"\x81\x43", ".": b"\x81\x44",
                "?": b"\x81\x48", "!": b"\x81\x49", "'": b"\x81\x66"}


def encode_text(text: str) -> bytes:
    """ASCII -> the full-width encoding the town dialogue interpreter renders.

    `\n` is the interpreter's own single-byte line break (0x0A); `{name}`
    becomes the live player-name token.
    """
    out = bytearray()
    index = 0
    while index < len(text):
        if text.startswith("{name}", index):
            out += PLAYER_NAME
            index += 6
            continue
        character = text[index]
        index += 1
        if character == "\n":
            out.append(0x0A)
        elif character in _PUNCTUATION:
            out += _PUNCTUATION[character]
        elif "A" <= character <= "Z":
            out += bytes((0x82, 0x60 + ord(character) - ord("A")))
        elif "a" <= character <= "z":
            out += bytes((0x82, 0x81 + ord(character) - ord("a")))
        elif "0" <= character <= "9":
            out += bytes((0x82, 0x4F + ord(character) - ord("0")))
        else:
            raise ValueError(f"No encoding for {character!r}")
    return bytes(out)


# TESTING: replace every girl's dialogue with a greeting, claiming the whole
# 3840-byte region for whatever they end up doing. Each line names her position
# in the row, so one ride maps every name plate to a presence flag - which is
# the thing the next pass (give an item -> alternate dialogue -> a location
# check) needs and cannot get any other way.
POOL_HOUSE_HELLO_DIALOGUE = True
HELLO_LINES: tuple[str, ...] = (
    "Hello, {name}.\nI am number one in the row.",
    "Hello, {name}.\nI am number two in the row.",
    "Hello, {name}.\nI am number three in the row.",
    "Hello, {name}.\nI am number four in the row.",
    "Hello, {name}.\nI am number five in the row.",
    "Hello, {name}.\nI am number six in the row.",
    "Hello, {name}.\nI am number seven in the row.",
)


def _read_town_bin() -> bytes:
    """The extracted TOWN.BIN the prologues are copied from.

    Generation needs it only when POOL_HOUSE_HELLO_DIALOGUE is on, which is a
    TESTING flag; a shipping build never reads it.
    """
    path = Path(__file__).resolve().parents[3] / "extracted" / "TOWN.BIN"
    return path.read_bytes()


def vanilla_prologue(index: int, town_bin: bytes) -> bytes:
    """The `57 00 / 1C slot n / 57 slot` head of a girl's vanilla script.

    Copied rather than rebuilt: girl 0x060B carries an extra `0F 0C 03`
    portrait attach, and whatever else a girl sets up before her first text
    byte is hers to keep.
    """
    _, _, script, _, prologue, _ = GIRL_DIALOGUE[index]
    start = script - DIALOGUE_IMAGE_RUNTIME_ADDRESS + DIALOGUE_IMAGE_FILE_OFFSET
    return town_bin[start:start + prologue]


def build_hello_script(index: int, prologue: bytes) -> bytes:
    """One greeting, padded with the end-of-script byte to fill her region.

    The pad is `0x01`, never zero: a stray entry into reclaimed bytes must
    close the conversation instead of misparsing (the standing rule in
    `docs/game/dialogue-scripts.md` §4, paid for by the chunk-5 zero test).
    """
    _, _, _, region, prologue_size, slot = GIRL_DIALOGUE[index]
    if len(prologue) != prologue_size:
        raise ValueError(f"Girl {index} prologue is {len(prologue)}, not {prologue_size}.")
    body = prologue + encode_text(HELLO_LINES[index]) + bytes((WAIT_FOR_BUTTON, END_OF_SCRIPT))
    if len(body) > region:
        raise ValueError(
            f"Girl {index}'s greeting is {len(body)} bytes, over her {region}-byte region."
        )
    return body + bytes([END_OF_SCRIPT]) * (region - len(body))


def dialogue_region_bytes() -> int:
    return sum(record[3] for record in GIRL_DIALOGUE)
