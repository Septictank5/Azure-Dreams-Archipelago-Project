"""Vanilla crash fixes, ported from ProGrammar's AD-DeRandomizer.

These are not ADAP behaviour. They are repairs to bugs that shipped in the
retail game, adopted because a multiworld seed that can hard-lock is worse than
one that diverges from vanilla, and because native crashes are otherwise
indistinguishable from ours in a bug report. Every crash ruled out here is one
that stops costing a session to re-diagnose.

Provenance: `references/AD-DeRandomizer/util.js` `applyFixCrashes`, with the
disc offsets from that project's `constants.js`. The originals below were read
out of `Azure Dreams (Original)/Azure Dreams (USA).bin` on 2026-08-04 and each
decodes to exactly what the fix expects to be replacing, which is the check that
the offsets are still right for this disc revision.

**These are the only edits ADAP makes to vanilla combat code.** Every other
patch in this project is either our own code or a hook into a call site we own.
Keep it that way: anything added here has to be a repair to a documented native
bug, not a behaviour change, or the "is it us or the game" question stops having
an answer.

What is fixed, all three from `docs/archive/` and the derandomizer's bug list:

* **The EXP overflow crash.** Gaining 32,767-39,480 experience from one action
  reads out of bounds and crashes. The routine sign-extends the amount
  (`sll`/`sra` by 16) and then branches on it as signed; above 0x7FFF that goes
  negative and the popup indexes backwards. Both the calculation and the two
  popup call sites are switched to unsigned.
* **The level-over-99 infinite loops**, in three places: placing a monster in a
  room, stepping on a monster den on floor 99 of the second tower, and
  egg-bombing with Koh at level 50 or above. The game cannot express a monster
  above level 99 and spins forever trying. Each site has a spare `nop` that
  becomes an early-out branch.

Not adopted: `applyFixBugs`, which is a single byte making the Salamander's
particle 2x1 so it renders at all. Cosmetic, and it is one `NativeFix` row away
if that changes.

The write sites are raw disc offsets into the MODE2/2352 image, the same
coordinate space our PPF records already use. None of them lands in a sector
header and none straddles a sector boundary - asserted at import. They also do
not overlap anything ADAP writes; `tools/Build-AdapTestDisc.py` refuses to build
a disc where two records disagree about a byte, so a future collision fails the
build rather than shipping.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

RAW_SECTOR_SIZE = 2_352
# MODE2/2352: 24 bytes of sync+header+subheader, 2048 of user data, then EDC/ECC.
_USER_DATA_START = 24
_USER_DATA_END = _USER_DATA_START + 2_048


def _instruction(word: int) -> bytes:
    """One MIPS instruction, in the derandomizer's own literal convention.

    Its `writeInstruction` writes the literal's bytes most-significant first, so
    `0x80002232` lands as `80 00 22 32` and reads back as the little-endian word
    `0x32220080` - `andi v0,s1,0x80`. Keeping the literals in that form means
    they can be diffed against `util.js` without mental byte-swapping.
    """

    return word.to_bytes(4, "big")


@dataclass(frozen=True)
class NativeFix:
    name: str
    raw_offset: int
    runtime_address: int
    original: bytes
    replacement: bytes
    reason: str


NATIVE_FIXES: tuple[NativeFix, ...] = (
    NativeFix(
        name="experience popup sign handling",
        raw_offset=0x1C8AD8C,
        runtime_address=0x800B4F14,
        # nor v0,zero,s0 / sll v0,v0,0x10 / sra a1,v0,0x10 / blez a1,+2
        original=bytes.fromhex("2710100000140200032c020002 00a018".replace(" ", "")),
        replacement=(
            _instruction(0x80002232)  # andi v0,s1,0x80   - is this an EXP popup?
            + _instruction(0x91FF4014)  # bnez v0,-0x1b8  - if so, take the normal path
            + _instruction(0x21180002)  # move v1,s0      - with the amount unsigned
            + _instruction(0x27280300)  # nor  a1,zero,v1
        ),
        reason="EXP overflow crash: 32,767-39,480 experience in one action read out of bounds.",
    ),
    NativeFix(
        name="experience popup unsigned, site 1",
        raw_offset=0x1C762A4,
        runtime_address=0x800A2EEC,
        original=bytes.fromhex("0034120003340600"),  # sll a2,s2,0x10 / sra a2,a2,0x10
        replacement=_instruction(0x21304002) + _instruction(0x00000000),  # move a2,s2 / nop
        reason="Same crash, popup call site: the amount must not be sign-extended.",
    ),
    NativeFix(
        name="experience popup unsigned, site 2",
        raw_offset=0x1C76450,
        runtime_address=0x800A2F68,
        original=bytes.fromhex("0034120003340600"),
        replacement=_instruction(0x21304002) + _instruction(0x00000000),
        reason="Same crash, second popup call site.",
    ),
    NativeFix(
        name="place monster above level 99",
        raw_offset=0x1C73A00,
        runtime_address=0x800A0B08,
        original=bytes(4),  # nop
        replacement=_instruction(0x08000412),
        reason="Infinite loop in placeMonsterInRoom when the roll exceeds level 99.",
    ),
    NativeFix(
        name="monster den above level 99",
        raw_offset=0x1CA88B0,
        runtime_address=0x800CEC78,
        original=bytes(4),
        replacement=_instruction(0x08000412),
        reason="Same loop, reached by stepping on a den on floor 99 of the second tower.",
    ),
    NativeFix(
        name="egg bomb above level 99",
        raw_offset=0x1C8DEE4,
        runtime_address=0x800B794C,
        original=bytes(4),
        replacement=_instruction(0x07002412),
        reason="Same loop, reached by egg-bombing with Koh at level 50 or above.",
    ),
)


def _assert_layout() -> None:
    seen: list[tuple[int, int]] = []
    for fix in NATIVE_FIXES:
        if len(fix.original) != len(fix.replacement):
            raise ValueError(
                f"Native fix {fix.name!r} replaces {len(fix.original)} bytes with "
                f"{len(fix.replacement)}; an in-place repair must not resize."
            )
        within = fix.raw_offset % RAW_SECTOR_SIZE
        end = within + len(fix.replacement)
        if within < _USER_DATA_START or end > _USER_DATA_END:
            raise ValueError(
                f"Native fix {fix.name!r} at 0x{fix.raw_offset:X} leaves the sector's "
                "user data; it would land in a header or straddle a boundary."
            )
        for other_offset, other_length in seen:
            if fix.raw_offset < other_offset + other_length and (
                other_offset < fix.raw_offset + len(fix.replacement)
            ):
                raise ValueError(f"Native fix {fix.name!r} overlaps another native fix.")
        seen.append((fix.raw_offset, len(fix.replacement)))


_assert_layout()


def iter_native_bugfix_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return tuple((fix.raw_offset, fix.replacement) for fix in NATIVE_FIXES)


def append_native_bugfix_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_native_bugfix_raw_patches():
        ppf.extend(struct.pack("<IB", raw_offset, len(data)))
        ppf.extend(data)
