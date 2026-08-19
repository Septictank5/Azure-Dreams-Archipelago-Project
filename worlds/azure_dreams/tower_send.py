"""The tower players-menu "Send" row (M1: the dispatch skeleton).

The tower players menu (Square) is RESIDENT SLUS machinery, not package
code - only the row handlers live in the LZ front-menu package.  The
whole controller was measured 2026-08-07 (docs/tower-send-design.md):

* Confirm dispatch `0x8004FF20`: row = `[state+0x1C]`, handler fetched
  from the six-word table at `0x8007179C`, argument-flag byte from
  `0x800717B4[row]`, then `jalr handler(a0 = state-0x20)`.
* Row order confirmed live (breakpoint on the `jalr` at `0x8004FF8C`):
  Items / Select up / Line up / Fuse / Command / Feet.
* The row count is the single immediate `addiu a2,zero,6` at
  `0x80050458`, feeding the modular stepper `0x80049DE8`.
* The selection cursor's Y is COMPUTED (`selection*16 - 40`, animator
  `0x800500B4` through window-object slot 8), so a seventh row gets a
  correctly placed cursor before it has any label.

M1 ships the smallest thing a disc can prove: a seventh selectable row.

* The handler table grows to seven entries.  It cannot grow in place -
  the flag bytes at `0x800717B4` sit flush against it - so the table is
  relocated to `0x80048D60`, a 64-byte helper whose ONLY reference in
  the whole 2 MB RAM image is one `addiu` at `0x800211E4` inside the
  stream-2 QUIT-prompt code that `save_removal` makes unreachable on
  every ADAP disc (measured 2026-08-07: no jal/j/word reference exists).
  The vanilla table at `0x8007179C` is left untouched for any reader we
  have not met.
* The flag table is NOT relocated: row 6 reads `0x800717BA`, whose
  vanilla value is already `0x00` - exactly the "no familiar
  dependency" flag the Send row wants.  The familiar-dependent rows
  (Line up / Fuse / Command carry flag 1) keep reading the live vanilla
  bytes, so whatever maintains them stays correct.
* The seventh handler is a PLACEHOLDER: the Items-session opener
  `0x80018AB0`.  Selecting the new row must visibly do something
  harmless; the real send session replaces this in M2.
* No label strip yet.  The row renders blank (window-object slot 7 at
  array offset `0x1C` is allocated and initialized but never assigned a
  record by the vanilla assigner `0x800506BC` - reserved for M2's
  strips).  The cursor still slides to it, and confirm dispatches.
"""

from __future__ import annotations

import struct

from . import alternate_pickup, patch, save_removal

# --- The measured vanilla menu controller ------------------------------------

# Six-word handler table, immediately followed by the flag bytes; confirmed
# live 2026-08-07 by breakpointing the dispatcher's jalr for rows 0/3/5.
VANILLA_HANDLER_TABLE_ADDRESS = 0x8007_179C
VANILLA_FLAG_TABLE_ADDRESS = 0x8007_17B4

ITEMS_HANDLER_ADDRESS = 0x8001_8AB0
SELECT_UP_HANDLER_ADDRESS = 0x8001_C9F8
LINE_UP_HANDLER_ADDRESS = 0x8002_3C38
FUSE_HANDLER_ADDRESS = 0x8001_E7B8
COMMAND_HANDLER_ADDRESS = 0x8001_EF0C
FEET_HANDLER_ADDRESS = 0x8001_8AD0

VANILLA_HANDLERS = (
    ITEMS_HANDLER_ADDRESS,
    SELECT_UP_HANDLER_ADDRESS,
    LINE_UP_HANDLER_ADDRESS,
    FUSE_HANDLER_ADDRESS,
    COMMAND_HANDLER_ADDRESS,
    FEET_HANDLER_ADDRESS,
)

# The dispatcher's two instruction pairs (lui/addiu) and the count immediate.
#
# THE DISPATCHER LOADS THE TABLE'S UPPER HALF FROM TWO PLACES, NOT ONE.
# `FUN_8004FF20` reaches the `addiu` at 0x8004FF78 by two routes:
#
#     8004ff58: bne  a1,v0,0x8004ff74   ; [state+0x20] != 2
#     8004ff5c:  _lui v0,0x8007         ; DELAY SLOT - the branch-taken copy
#     ...
#     8004ff70: lui  v0,0x8007          ; the fall-through copy
#     8004ff74: lw   v1,0x1c(s0)
#     8004ff78: addiu v0,v0,0x179c      ; fed by EITHER lui
#
# Patching only 0x8004FF70 left the branch-taken path computing
# 0x80070000 + 0xFFFF8D60 = 0x80068D60 and `jalr`ing a word of .text read as a
# function pointer (row 2 -> 0x0045102A, row 3 -> 0x10400004, row 4 -> 0).
#
# The branch is taken when the row's flag byte at 0x800717B4 is non-zero -
# rows 2 Line up / 3 Fuse / 4 Command, flags `00 00 01 01 01 00` - AND
# [state+0x20] != 2. That word is written immediately before the call from
# 0x800502E0 -> 0x8004FF00 -> the front-menu classifier at 0x80021300, which
# returns 2 when the players menu is paged to KOH and 0 or 1 when it is paged
# to a FAMILIAR. So: confirming Line up / Fuse / Command with the stat panel
# flipped to a familiar crashed. Found 2026-08-18 by the class-system RE pass
# and reproduced on demand the same day; it is also the unexplained fuse crash
# from an earlier session. ADAP's own Send row (index 6) reads flag byte
# 0x800717BA = 0x00 and was always on the safe path.
#
# Vanilla is unaffected: both `lui`s load 0x8007 there, so the two routes agree.
# The defect is purely a consequence of relocating the table.
#
# docs/systems/class-system.md section 7; docs/HANDOFF.md open bug 5.
TABLE_LUI_ADDRESS = 0x8004_FF70      # lui   v0,0x8007  (fall-through)
TABLE_LUI_DELAY_SLOT_ADDRESS = 0x8004_FF5C  # lui v0,0x8007 (branch-taken)
TABLE_ADDIU_ADDRESS = 0x8004_FF78    # addiu v0,v0,0x179C
ROW_COUNT_ADDRESS = 0x8005_0458      # addiu a2,zero,0x0006

# --- The seventh row's visual (M2a) ------------------------------------------
#
# The window-object pointer array is allocated SEVENTEEN entries
# (`addiu a1,zero,0x11` at 0x80050BF8) and vanilla uses fifteen: 0 title,
# 1-6 row record carriers, 8-9 the sliding cursor pair, 10-15 the row
# position objects.  Slots 7 and 16 are allocated, initialized (the
# seventeen-iteration CLUT loop at 0x80050A58 covers them), and never
# assigned - they pair exactly like 1<->10 .. 6<->15.  Konami sized this
# menu for eight rows; ADAP is using the eighth.
#
# Three vanilla loops almost do all the work already:
# * the position loop (`slti v0,a1,0x0010` at 0x800507E8) positions slots
#   10..15 at Y = -40 + 16n and installs the shared render context; bound
#   0x10 -> 0x11 extends it to slot 16, Y = 56 - the seventh row's line;
# * the fade loop (`slti v0,a2,0x0007` at 0x800504FC) runs slots 1..6;
#   bound 7 -> 8 includes slot 7 so the new row fades in with the rest;
# * the record stores are straight-line code, so the ONE thing left - the
#   record pointer into slot 7's object - is a five-word stub hooked over
#   the assigner's `jal 0x8004FFF4` at 0x8005078C, tail-chaining so the
#   displaced call still happens with its own ra.
FADE_BOUND_ADDRESS = 0x8005_04FC       # slti v0,a2,0x0007
POSITION_BOUND_ADDRESS = 0x8005_07E8   # slti v0,a1,0x0010
ASSIGNER_HOOK_ADDRESS = 0x8005_078C    # jal 0x8004FFF4
ASSIGNER_HOOK_CONTINUATION = 0x8004_FFF4

_FADE_BOUND_WORD = 0x28C2_0008         # slti v0,a2,0x0008
_POSITION_BOUND_WORD = 0x28A2_0011     # slti v0,a1,0x0011

# The per-frame pop-out drive (`slti v0,t1,0x0006` at 0x800502B0) walks
# rows 0..5 each frame, setting BOTH paired objects' RGB to
# `popout*5 + 0x58` and the slide X from the per-row state byte at
# `[state+0xA4+row]`.  A row outside the loop keeps its uninitialized
# white shade and unsnapped X - exactly the first M2a test's symptoms.
# Widening to 7 drives row 6 like the others; its state byte lands on
# `[state+0xAA]`, inside the same byte array's alignment padding.
POPOUT_BOUND_ADDRESS = 0x8005_02B0     # slti v0,t1,0x0006
_POPOUT_BOUND_WORD = 0x2922_0007       # slti v0,t1,0x0007

# The menu's intro slides every label in from the right: the state at
# `0x800504B4` walks the label slots each frame and subtracts 256 from the
# shared render context's `+0x02` X offset until it goes non-positive, then
# snaps it to zero.  Widening that loop (above) made it drive slot 7 too - but
# its DONE test reads slot 6 (`lw v0,0x18(v0)` at 0x80050510) and hands the
# menu over to the live input state the moment ROW 5 lands.  Slot 7 was one
# decrement behind, so it froze mid-slide at -104 and ADAP's label drew 104
# pixels left of the scroll, outside it, for the rest of the menu's life.
# Measured 2026-08-07 from a save state: row 6's context `+0x02` read `0xFF98`
# while rows 4 and 5 read `0x0000`.
#
# Testing slot 7 instead (`+0x1C`) keeps the intro running one frame longer,
# which is exactly how long our row needs.  It cannot hang: the loop snaps any
# non-positive value to zero, so the test always resolves.
FADE_DONE_TEST_ADDRESS = 0x8005_0510   # lw v0,0x0018(v0) - slot 6
_FADE_DONE_TEST_WORD = 0x8C42_001C     # lw v0,0x001C(v0) - slot 7

# The seventh row's 12-byte label record.
#   +0/+1 kind, +2/+3 SCREEN offset X/Y (signed), +4/+5 tpage, +6/+7 CLUT,
#   +8/+9 texture U/V, +10/+11 W/H (56x16).
#
# **Field semantics decoded 2026-08-07** from the resident sprite-record
# renderer at 0x800453E0: +2/+3 are sign-extended into vertex X/Y (negated
# under the kind byte's mirror flags), and only +8/+9 reach the GPU as
# texture UV.  The reticule's corner records prove it structurally - four
# records sharing one texture cell, differing only in +2/+3 and mirror
# bits.  Earlier readings ("V is texture AND screen line", "U is coupled
# too") came from experiments that changed both pairs at once: a borrowed
# Fuse record moved the row because it carried Fuse's +2/+3, and the
# icon-page build drew 110px left because +2 was rewritten to 0xA0 = -96.
#
# Since the 2026-08-08 row swap the Send row draws on ROW 5's line - above
# Feet, which moves to the bottom - so the speedrun habit "up from the top
# wraps to Feet" keeps working: +2/+3 = (0x04,0x20), X=+4, Y=+32. The
# texture pair stays (0x90,0x60), the JP 転送 static slot `patch.py`
# overwrites with our strip - texture and position are independent fields.
# Nothing is uploaded at VRAM (0x04,0x30): those texels belong to the item
# menu's targeting reticule, and painting them was the reticule artifact.
# The Feet/Hand records get the reverse edit (+3 -> 0x30, row 6's line) as
# two one-byte patches below; the assigner stub swaps which SLOT carries
# which record so the pop-out drive's row->slot pairing stays true.
SEND_ROW_TPAGE = 0x0017          # 4bpp page at VRAM (448,256) - the sheet
SEND_ROW_CLUT = 0x7CC6           # the sheet palette Fuse's label uses
SEND_ROW_RECORD = struct.pack(
    "<BBBBHHBBBB",
    0xC0, 0x2C,                                  # kind, as every vanilla row
    0x04, 0x20,                                  # screen offset: row 5's line
    SEND_ROW_TPAGE,
    SEND_ROW_CLUT,
    0x90, 0x60,                                  # texture U/V
    0x38, 0x10,                                  # 56 x 16
)

# The vanilla Feet/Hand records keep their slot-7 residence via the assigner
# stub, and their position byte moves to row 6's line. One byte each; the
# texture pairs, CLUTs and sizes are untouched.
FEET_HAND_POSITION_PATCHES = (
    (patch.FEET_LABEL_RECORD_ADDRESS + 3, 0x30),
    (patch.HAND_LABEL_RECORD_ADDRESS + 3, 0x30),
)

# --- The relocated table -----------------------------------------------------

# The dead QUIT-prompt helper: 64 bytes, unreachable on ADAP discs.
RELOCATED_BLOCK_ADDRESS = 0x8004_8D60
RELOCATED_BLOCK_END_ADDRESS = 0x8004_8DA0
HANDLER_TABLE_ADDRESS = RELOCATED_BLOCK_ADDRESS

ROW_COUNT = 7
SEND_ROW_INDEX = 6

# The menu's Send row exists only when the room has somewhere to send TO.
# With no other Azure Dreams player the whole feature is skipped: no row, no
# widened loops, no strip upload, no label - the menu is byte-for-byte vanilla.
# A row whose only outcome is "nobody to send to" is worse than no row, and
# this keeps solo and cross-game rooms completely untouched by the feature.
#
# Capped at three, matching Nada's send menu. The verb menu's geometry is
# unproven past that, and keeping both systems on the same cap means one
# answer to "how many targets can I have".
MAX_TARGETS = 3

# lui rt=v0: 0x8D60 sign-extends negative, so the upper half is 0x8005.
_TABLE_LUI_WORD = 0x3C02_8005                   # lui   v0,0x8005
_TABLE_ADDIU_WORD = 0x2442_0000 | (HANDLER_TABLE_ADDRESS & 0xFFFF)
_ROW_COUNT_WORD = 0x2406_0000 | ROW_COUNT       # addiu a2,zero,7

# The addiu immediate sign-extends: 0x80050000 + (0x8D60 - 0x10000) must
# land exactly on the relocated table.
assert 0x8005_0000 + (HANDLER_TABLE_ADDRESS & 0xFFFF) - 0x1_0000 == (
    HANDLER_TABLE_ADDRESS
)

# The 64-byte block holds the 28-byte handler table plus the two 16-byte
# flag-clearing stubs (60/64 used): the assigner stub moved to the seed page
# (`patch.build_send_row_assigner_stub`) when it needed a tenth word to zero
# the row's uninitialised animation byte, and the 12-byte record sits in the
# alternate-pickup cave's spare tail.
SEND_ROW_RECORD_ADDRESS = patch.SEND_ROW_RECORD_ADDRESS
ASSIGNER_STUB_ADDRESS = patch.SEND_ROW_ASSIGNER_ADDRESS


def _jal(address: int) -> int:
    return 0x0C00_0000 | ((address >> 2) & 0x03FF_FFFF)


# --- send mode ----------------------------------------------------------------
#
# Pressing X on an item in the Send list replaces `Use`/`Have`/`Give` with one
# row per other Azure Dreams player. The whole thing rides seams
# `alternate_pickup` already owns, because of one measured fact: the verb-label
# lookup's caller keeps the row index in `s0` (`s0 = 20 + row`, set at
# 0x8001E010 and used at 0x8001E02C). So every player row can carry the SAME
# verb id and still draw a different name - no new verb ids, no extending the
# in-stream action table that has no free slots, and no LZ re-encode.
#
# Both hooks are reached by replacing the FIRST instruction of the resident
# hook alternate_pickup installed with a jump here; we replay that instruction
# and, for everything that is not a send, jump back one instruction in. The
# retired-driver cave has 12 bytes free, so the code itself lives in the seed
# page - see docs/tower-send-strip-relocation.md for moving it.
SEND_MODE_FLAG_ADDRESS = patch.SEND_MODE_FLAG_ADDRESS
SEND_CONTROLLER_ADDRESS = patch.SEND_CONTROLLER_ADDRESS
SEND_STATE_ADDRESS = SEND_MODE_FLAG_ADDRESS
SEND_STATE_SIZE = 8

_HANDLER_OFFSET = 0x00
_VERB_LIST_OFFSET = 0x20
_LABEL_OFFSET = 0x100
_COMMIT_OFFSET = 0x180

SEND_HANDLER_ROUTINE_ADDRESS = patch.SEND_ROW_CODE_ADDRESS + _HANDLER_OFFSET
SEND_VERB_LIST_ROUTINE_ADDRESS = patch.SEND_ROW_CODE_ADDRESS + _VERB_LIST_OFFSET
SEND_LABEL_ROUTINE_ADDRESS = patch.SEND_ROW_CODE_ADDRESS + _LABEL_OFFSET
SEND_COMMIT_ROUTINE_ADDRESS = patch.SEND_ROW_CODE_ADDRESS + _COMMIT_OFFSET


def _verb_list_hook_address() -> int:
    return alternate_pickup.VERB_LIST_HOOK_ADDRESS


def _label_hook_address() -> int:
    return alternate_pickup.VERB_LABEL_HOOK_ADDRESS


def build_send_handler(target_count: int) -> bytes:
    """The Send row: raise the mode flag, then open the ordinary item list.

    Reached from the menu's own dispatch with `a0` already holding the
    argument the items opener wants, and `ra` the dispatcher's - so this
    tail-jumps and the session behaves exactly as the Items row's does.
    """

    b = patch._MipsBuilder()
    # Two words into the seed page's send-token gate, which either opens
    # the item list or puts up the no-tokens modal. This slot is 32 bytes
    # and the gate is 232; a plain `j` hands over `a0` (the menu state) and
    # the dispatcher's `ra` untouched, so the gate IS the row handler.
    #
    # Two earlier builds refused here with the wrong contract and
    # softlocked, both argued from a disassembly of the FLOOR-GENERATION
    # overlay - a tower RAM dump has that at the menu's addresses. The gate
    # is now modelled on vanilla's "You need 2 familiars", read from a save
    # state taken with this menu open. See `patch._build_send_token_gate`.
    b.emit(patch._j(0x02, patch.SEND_TOKEN_GATE_ADDRESS), 0)
    return b.build()


def build_send_verb_list(target_count: int) -> bytes:
    """Replace the verb rows with one row per target, in send mode only.

    Entered by a jump planted over the first instruction of
    `alternate_pickup`'s verb-list hook, which is the displaced
    `sw s0,0x28(s2)`; that store is replayed here. `s2` is the controller.
    Anything that is not a send - mode off, no descriptor, a marker, a
    descriptor outside the bag array, a familiar, or an equipped item -
    jumps back into the vanilla hook one instruction in, so markers and
    ordinary items keep every verb they had. The marker and bag-range
    rejections are what keep a stale flag harmless: a check still collapses
    to its own Send row, and in-hand/at-feet/ground items keep vanilla
    verbs (Put in first, then send from the bag).
    """

    b = patch._MipsBuilder()
    b.emit(patch._i(0x2B, 18, 16, patch.VERB_ROW_COUNT_OFFSET))  # displaced

    patch._load_address(b, 8, SEND_MODE_FLAG_ADDRESS)
    b.emit(patch._i(0x23, 8, 8, 0), 0)                  # lw t0,0(t0)
    b.branch(0x04, 8, 0, "vanilla")                     # beq t0,zero
    b.emit(0)

    b.emit(patch._i(0x23, 18, 4, patch.VERB_DESCRIPTOR_OFFSET), 0)
    b.branch(0x04, 4, 0, "vanilla")                     # no descriptor
    b.emit(0)
    # A check is collected, never gifted: markers fall back to the vanilla
    # hook, whose own marker test collapses them to the single Send row.
    # `ra` is expendable here for the same reason it is in that hook: the
    # builder tail this rides on has its frame's ra spilled.
    b.emit(patch._j(0x03, alternate_pickup.MARKER_TEST_ADDRESS), 0)
    b.branch(0x05, 2, 0, "vanilla")                     # a marker
    b.emit(0)
    patch._load_address(b, 8, patch.INVENTORY_DESCRIPTORS_ADDRESS)
    b.emit(
        patch._r(4, 8, 8, 0, 0x23),                     # subu t0,a0,t0
        patch._i(0x0B, 8, 8, 4 * patch.INVENTORY_DESCRIPTOR_COUNT),
    )
    b.branch(0x04, 8, 0, "vanilla")                     # not a bag slot
    b.emit(0)
    b.emit(patch._i(0x24, 4, 10, 1), 0)                 # lbu t2,1(a0) category
    b.emit(patch._i(0x09, 0, 11, patch.FAMILIAR_CATEGORY))
    b.branch(0x04, 10, 11, "vanilla")                   # a familiar is not ours
    b.emit(0)
    b.emit(patch._i(0x24, 4, 10, 3), 0)                 # lbu t2,3(a0) flags
    b.emit(patch._i(0x0C, 10, 10, patch.EQUIPPED_FLAG))
    b.branch(0x05, 10, 0, "vanilla")                    # equipped: unequip first
    b.emit(0)

    # Remember the controller so the commit can read which row was confirmed.
    patch._load_address(b, 8, SEND_CONTROLLER_ADDRESS)
    b.emit(patch._i(0x2B, 8, 18, 0))                    # sw s2,0(t0)
    # One row per target, all the same verb id; the label hook tells them apart.
    b.emit(patch._i(0x09, 0, 10, alternate_pickup.SEND_VERB_ID))
    for index in range(target_count):
        b.emit(
            patch._i(0x28, 18, 10, patch.VERB_ROW_ARRAY_OFFSET + index)
        )
    b.emit(
        patch._i(0x09, 0, 10, target_count),
        patch._i(0x2B, 18, 10, patch.VERB_ROW_COUNT_OFFSET),
        patch._j(0x02, alternate_pickup.VERB_LIST_RESUME_ADDRESS),
        0,
    )

    b.label("vanilla")
    b.emit(patch._j(0x02, _verb_list_hook_address() + 4), 0)
    return b.build()


# Re-entry points inside alternate_pickup's label hook, by construction of
# `build_verb_label_hook`: +0x00 `addiu t0,zero,id`, +0x04 the id test, +0x0C
# the marker-flag load, +0x34 its `vanilla` label (the plain table lookup).
# Diverting the hook means its +0x04 test never runs, so this module has to
# pick the right re-entry itself.
_LABEL_MARKER_PATH_OFFSET = 0x0C
_LABEL_VANILLA_PATH_OFFSET = 0x34


def _assert_label_hook_shape() -> None:
    """The two re-entry offsets are derived, so prove them against the build."""

    payload = alternate_pickup.build_verb_label_hook()
    words = struct.unpack(f"<{len(payload) // 4}I", payload)
    if words[0] != patch._i(0x09, 0, 8, alternate_pickup.SEND_VERB_ID):
        raise ValueError("alternate_pickup's label hook no longer starts with the id load.")
    for offset in (_LABEL_MARKER_PATH_OFFSET, _LABEL_VANILLA_PATH_OFFSET):
        if words[offset // 4] >> 26 != 0x0F:      # lui
            raise ValueError(
                f"label hook re-entry +0x{offset:02X} is not a lui; "
                "alternate_pickup's layout changed."
            )


def build_send_label() -> bytes:
    """Draw the target's name on each row, in send mode only.

    Entered by a jump over the first instruction of `alternate_pickup`'s label
    hook (`addiu t0,zero,SEND_VERB_ID`), replayed here. The row being drawn is
    `s0 - 20`, which is the whole trick: the vanilla caller loops `s0` from
    0x14 and reads the row's verb id at `[s1 + s0 + 0x40]`, so `s0` identifies
    the row even though every row carries the same id.
    """

    _assert_label_hook_shape()
    b = patch._MipsBuilder()
    b.emit(patch._i(0x09, 0, 8, alternate_pickup.SEND_VERB_ID))  # displaced
    b.branch(0x05, 4, 8, "table")                       # a0 != our verb id
    b.emit(0)
    patch._load_address(b, 9, SEND_MODE_FLAG_ADDRESS)
    b.emit(patch._i(0x23, 9, 9, 0), 0)
    b.branch(0x04, 9, 0, "marker")                      # not in send mode
    b.emit(0)
    # v0 = names + (s0 - 20) * slot
    b.emit(patch._i(0x09, 16, 2, -0x14))                # addiu v0,s0,-20
    # The slot size is not a power of two, so the index multiply is a sum of
    # shifts - 36 is 32 + 4. Built from the constant so a resize cannot leave
    # a stale shift behind.
    bits = [i for i in range(8) if patch.SEND_ROW_NAME_SLOT_SIZE & (1 << i)]
    if len(bits) == 1:
        b.emit(patch._r(0, 2, 2, bits[0], 0x00))        # sll v0,v0,n
    elif len(bits) == 2:
        b.emit(
            patch._r(0, 2, 9, bits[1], 0x00),           # sll t1,v0,high
            patch._r(0, 2, 10, bits[0], 0x00),          # sll t2,v0,low
            patch._r(9, 10, 2, 0, 0x21),                # addu v0,t1,t2
        )
    else:
        raise ValueError(
            f"Name slot size 0x{patch.SEND_ROW_NAME_SLOT_SIZE:X} needs more "
            "than two shifts; pick a size with at most two set bits."
        )
    patch._load_address(b, 9, patch.SEND_ROW_NAMES_ADDRESS)
    b.emit(
        patch._r(9, 2, 2, 0, 0x21),                     # addu v0,t1,v0
        patch._r(31, 0, 0, 0, 0x08),                    # jr ra
        0,
    )

    # Two different fall-throughs, because the diverted test at +0x04 is gone:
    # a foreign verb id wants the plain table lookup, while our own id outside
    # send mode is a marker and wants alternate_pickup's flag check.
    b.label("marker")
    b.emit(
        patch._j(0x02, _label_hook_address() + _LABEL_MARKER_PATH_OFFSET), 0
    )
    b.label("table")
    b.emit(
        patch._j(0x02, _label_hook_address() + _LABEL_VANILLA_PATH_OFFSET), 0
    )
    return b.build()


# put_item_into_bag classifies its descriptor BEFORE the allocator hook:
# in-hand (0x80081484) and at-feet (0x80081470) branch to the hook, and
# anything that is not the ground descriptor bails to the epilogue at
# 0x80097FE8 - which is exactly what a bag descriptor does, so the M4 seam
# at the allocator jal was never reached and confirming a name row did
# vanilla nothing. The commit is therefore planted on the BAIL TEST
# itself: the two words at 0x80097FE8 (`bne s1,v0,epilogue` + its delay
# `addu v0,s1,zero`) become `j commit` + nop. Verified: no other branch
# or jump in the overlay targets either word.
PUT_IN_SOURCE_TEST_ADDRESS = 0x8009_7FE8
_PUT_IN_SOURCE_TEST_VANILLA_WORDS = (0x1622_0077, 0x0220_1021)


def build_send_commit(target_count: int) -> bytes:
    """Publish the confirmed target's gift instead of the vanilla put-in.

    Confirming a player-name row dispatches verb id 0x0F exactly as
    `Put in` does: the menu posts {descriptor, code 0x12} to the request
    block at 0x80082EB0 and the gameplay dispatcher's case 0x12 calls
    `put_item_into_bag` with the bag descriptor. This routine is entered
    by a `j` planted over that function's source-classifier bail at
    `PUT_IN_SOURCE_TEST_ADDRESS`, so it sees exactly the descriptors the
    vanilla function would REJECT - hand, feet and ground items still take
    their vanilla paths (marker guard included) before this code runs.

    On entry `s1` is the descriptor, `v0` still holds the ground
    descriptor pointer the displaced test compared against, and the frame
    has every register spilled. The ground case is re-tested first and
    continues into the vanilla hook chain; everything else that is not a
    send - mode off, outside the bag array, a familiar, a stale
    controller, or a mailbox still waiting on the client's ack - leaves
    through the vanilla bail (epilogue, v0 = s1) WITHOUT touching the
    item, so an offline client can never lose a gift and a bag put-in
    outside send mode stays the no-op it always was.

    The commit itself is Nada's, single-item: free the slot by the game's
    own occupancy rule, compact the display-order table, publish
    descriptor and target index to the `ADGT` mailbox, bump the sequence
    last, then drop the mode flag - a send lasts exactly one item.
    """

    b = patch._MipsBuilder()
    # The displaced test, re-issued: a ground descriptor continues into
    # the vanilla hook chain (alternate_pickup's marker guard).
    b.branch(0x04, 17, 2, "vanilla_continue")           # beq s1,v0
    b.emit(0)

    patch._load_address(b, 8, SEND_MODE_FLAG_ADDRESS)
    b.emit(patch._i(0x23, 8, 8, 0), 0)                  # lw t0,(flag)
    b.branch(0x04, 8, 0, "bail")                        # mode off: vanilla no-op
    b.emit(0)

    patch._load_address(b, 8, patch.INVENTORY_DESCRIPTORS_ADDRESS)
    b.emit(
        patch._r(17, 8, 8, 0, 0x23),                    # subu t0,s1,t0
        patch._i(0x0B, 8, 8, 4 * patch.INVENTORY_DESCRIPTOR_COUNT),
    )
    b.branch(0x04, 8, 0, "bail")                        # not a bag slot
    b.emit(0)

    # Nada's familiar backstop; unreachable while the verb list refuses
    # familiars, kept because losing one outright is the failure mode.
    b.emit(patch._i(0x24, 17, 8, 1), 0)                 # lbu t0,1(s1)
    b.emit(patch._i(0x09, 0, 9, patch.FAMILIAR_CATEGORY))
    b.branch(0x04, 8, 9, "bail")
    b.emit(0)

    # The confirmed row IS the target index: the dispatcher at 0x8001D73C
    # reads the cursor from [controller+0x1C], and every send row carries
    # the same verb id, so the cursor is the only thing that names a target.
    patch._load_address(b, 10, SEND_CONTROLLER_ADDRESS)
    b.emit(patch._i(0x23, 10, 10, 0), 0)                # lw t2,(saved s2)
    b.branch(0x04, 10, 0, "bail")
    b.emit(0)
    b.emit(patch._i(0x23, 10, 11, patch.VERB_SELECTION_OFFSET), 0)
    b.emit(patch._i(0x0B, 11, 8, target_count))         # sltiu t0,t3,count
    b.branch(0x04, 8, 0, "bail")
    b.emit(0)

    # No token, no send - checked HERE rather than at the menu row.
    #
    # This is the safe place for it: `bail` is the commit's own existing
    # refusal, already used when the mailbox has an unacked send, and it
    # leaves through the vanilla epilogue with the item untouched. The
    # player picks a target and the gift simply does not go, exactly as it
    # already behaves when the client is offline. Two attempts at refusing
    # in the players menu softlocked instead, both argued from the wrong
    # overlay - see build_send_handler.
    #
    # The body is in the seed page: this slot had 36 bytes spare and the
    # check plus its first-touch seeding does not fit. `ra` is free here -
    # every exit from this routine is a jump to a fixed address, and the
    # vanilla epilogue restores `ra` from the frame.
    b.emit(patch._j(0x03, patch.SEND_TOKEN_CHECK_ADDRESS), 0)
    b.branch(0x04, 2, 0, "bail")                        # no tokens: keep it
    b.emit(0)

    # First touch initializes the sequence/ack pair (this RAM is never
    # disc-loaded); a magic already present means the pair is live state.
    patch._load_address(b, 14, patch.TOWER_GIFT_MAILBOX_ADDRESS)
    b.emit(
        patch._i(0x23, 14, 8, 0),                       # lw t0,(magic)
        patch._i(0x0F, 0, 9, patch.TOWER_GIFT_MAILBOX_MAGIC >> 16),
        patch._i(0x0D, 9, 9, patch.TOWER_GIFT_MAILBOX_MAGIC & 0xFFFF),
    )
    b.branch(0x04, 8, 9, "magic_ok")
    b.emit(0)
    b.emit(
        patch._i(0x2B, 14, 0, patch.TOWER_GIFT_MAILBOX_SEQUENCE_OFFSET),
        patch._i(0x2B, 14, 0, patch.TOWER_GIFT_MAILBOX_ACK_OFFSET),
    )
    b.label("magic_ok")
    b.emit(
        patch._i(0x23, 14, 8, patch.TOWER_GIFT_MAILBOX_SEQUENCE_OFFSET),
        patch._i(0x23, 14, 25, patch.TOWER_GIFT_MAILBOX_ACK_OFFSET),
        0,
    )
    b.branch(0x05, 8, 25, "bail")                       # unacked: keep the item
    b.emit(0)

    # Payload first - descriptor, count, target, magic - sequence last.
    b.emit(patch._i(0x23, 17, 12, 0), 0)                # lw t4,(descriptor)
    b.emit(
        patch._i(0x2B, 14, 12, patch.TOWER_GIFT_MAILBOX_ITEMS_OFFSET),
        patch._i(0x28, 17, 0, 1),                       # sb zero,1(s1): free
        patch._i(0x09, 0, 12, 1),
        patch._i(0x2B, 14, 12, patch.TOWER_GIFT_MAILBOX_COUNT_OFFSET),
        patch._i(0x2B, 14, 11, patch.TOWER_GIFT_MAILBOX_TARGET_OFFSET),
        patch._i(0x2B, 14, 9, 0),                       # sw t1,(magic)
    )

    # Nada's order-table compactor, verbatim: drop 0xFFFFFFFF markers and
    # pointers to freed descriptors, zero through the full 21-word table.
    patch._load_address(b, 8, patch.ORDER_TABLE_ADDRESS)
    b.emit(
        patch._r(8, 0, 9, 0, 0x21),                     # t1 = dst
        patch._i(0x09, 0, 10, patch.ORDER_TABLE_WORDS - 1),
        patch._i(0x09, 0, 12, -1),                      # t4 = deletion marker
    )
    b.label("cscan")
    b.emit(patch._i(0x23, 8, 15, 0), 0)                 # lw t7,(src)
    b.branch(0x04, 15, 0, "cdone")
    b.emit(0)
    b.branch(0x04, 15, 12, "cnext")
    b.emit(0)
    b.emit(patch._i(0x24, 15, 25, 1), 0)                # lbu t9,1(pointer)
    b.branch(0x04, 25, 0, "cnext")                      # freed: drop pointer
    b.emit(0)
    b.emit(
        patch._i(0x2B, 9, 15, 0),
        patch._i(0x09, 9, 9, 4),
    )
    b.label("cnext")
    b.emit(patch._i(0x09, 10, 10, -1))
    b.branch(0x05, 10, 0, "cscan")
    b.emit(patch._i(0x09, 8, 8, 4))                     # (delay) src += 4
    b.label("cdone")
    patch._load_address(
        b, 12, patch.ORDER_TABLE_ADDRESS + 4 * patch.ORDER_TABLE_WORDS
    )
    b.label("zfill")
    b.emit(
        patch._i(0x2B, 9, 0, 0),
        patch._i(0x09, 9, 9, 4),
        patch._r(9, 12, 25, 0, 0x2B),                   # sltu t9,dst,end
    )
    b.branch(0x05, 25, 0, "zfill")
    b.emit(0)

    b.emit(patch._i(0x23, 14, 8, patch.TOWER_GIFT_MAILBOX_SEQUENCE_OFFSET), 0)
    b.emit(
        patch._i(0x09, 8, 8, 1),
        patch._i(0x2B, 14, 8, patch.TOWER_GIFT_MAILBOX_SEQUENCE_OFFSET),
    )
    # One send per trip: drop the flag and the controller pointer together
    # (CONTROLLER is FLAG + 4, asserted in patch.py).
    patch._load_address(b, 8, SEND_MODE_FLAG_ADDRESS)
    b.emit(
        patch._i(0x2B, 8, 0, 0),
        patch._i(0x2B, 8, 0, 4),
        # The put-in chime; ra is expendable now, both exits go through the
        # epilogue, which restores it from the frame.
        patch._j(0x03, alternate_pickup.PLAY_SOUND_ADDRESS),
        patch._i(0x09, 0, 4, alternate_pickup.PUT_IN_SOUND_ID),
    )

    # Spend the token and confirm, HERE and nowhere else: this is the only
    # point that knows a gift was actually published (every bail above
    # leaves through the vanilla epilogue with the item untouched).
    #
    # `send_complete` is the spend plus `Sent!` through the tower's bottom
    # message box. That box is an ACTION-context primitive, which is legal
    # here and would not be at the Send row: confirming a target closes the
    # whole menu system before the gameplay dispatcher calls
    # `put_item_into_bag`, so by the time this runs the menu is gone and
    # Koh is back in the dungeon. To drop the confirmation and keep the
    # spend, point this one word back at `SEND_TOKEN_SPEND_ADDRESS`.
    b.emit(
        patch._j(0x03, patch.SEND_COMPLETE_ROUTINE_ADDRESS),
        0,
    )

    # Success: out through the epilogue like any completed put-in.
    b.emit(
        patch._j(0x02, alternate_pickup.PUT_IN_EPILOGUE_ADDRESS),
        patch._r(0, 0, 2, 0, 0x21),                     # (delay) v0 = 0
    )

    # The vanilla bail, byte-faithful: epilogue with v0 = s1, which is what
    # the displaced `bne`'s delay slot produced for every non-source
    # descriptor before this routine existed.
    b.label("bail")
    b.emit(
        patch._j(0x02, alternate_pickup.PUT_IN_EPILOGUE_ADDRESS),
        patch._r(17, 0, 2, 0, 0x21),                    # (delay) v0 = s1
    )

    # A ground descriptor: rejoin the vanilla source chain at the allocator
    # hook, where alternate_pickup's marker guard still sits.
    b.label("vanilla_continue")
    b.emit(patch._j(0x02, alternate_pickup.PUT_IN_HOOK_ADDRESS), 0)
    return b.build()


# --- the flag-clearing dispatch stubs ----------------------------------------
#
# The send-mode flag must never survive into an ordinary item session. The
# players-menu creator already clears it on every build; these two stubs
# close the other entries: the Items and Feet rows dispatch through them (via
# the relocated handler table) before reaching the vanilla openers. They live
# in the relocated block's tail, after the 28-byte table. The one remaining
# uncovered entry is the Square feet-shortcut's own pointer table at
# 0x800294A8..B0 (menu overlay, unmeasured on disc); with the verb-list and
# commit guards above, a stale flag there costs one surprise send menu on bag
# items, never a wrong commit.
ITEMS_STUB_ADDRESS = RELOCATED_BLOCK_ADDRESS + 0x1C
FEET_STUB_ADDRESS = RELOCATED_BLOCK_ADDRESS + 0x2C


def _build_clear_flag_stub(handler: int) -> bytes:
    """Four words: drop the send flag, tail-jump to the vanilla opener."""

    high = (SEND_MODE_FLAG_ADDRESS + 0x8000) >> 16
    b = patch._MipsBuilder()
    b.emit(
        patch._i(0x0F, 0, 8, high),                     # lui t0
        patch._i(0x2B, 8, 0, SEND_MODE_FLAG_ADDRESS & 0xFFFF),
        patch._j(0x02, handler),
        0,
    )
    return b.build()


def build_target_names(target_names: list[str]) -> bytes:
    """The verb rows' labels: one fixed-size CP932 slot per target."""

    slot = patch.SEND_ROW_NAME_SLOT_SIZE
    table = bytearray(slot * len(target_names))
    for index, name in enumerate(target_names):
        encoded = patch._full_width_cp932(name) + b"\0"
        if len(encoded) > slot:
            # Trim on a whole character; a half-written CP932 pair renders as
            # garbage rather than a short name.
            encoded = patch._full_width_cp932(name[: (slot - 1) // 2]) + b"\0"
        table[index * slot:index * slot + len(encoded)] = encoded
    if len(table) > patch.SEND_ROW_NAMES_CAPACITY:
        raise ValueError("The Send row's target names overrun their table.")
    return bytes(table)


def place_seed_page_blocks(block: bytearray, target_names: list[str]) -> None:
    """Write the send-mode code and name table into the generated seed page."""

    if not target_names:
        return
    code = bytearray(patch.SEND_ROW_CODE_CAPACITY)
    count = len(target_names)
    for offset, limit, payload, name in (
        (_HANDLER_OFFSET, _VERB_LIST_OFFSET,
         build_send_handler(count), "handler"),
        (_VERB_LIST_OFFSET, _LABEL_OFFSET,
         build_send_verb_list(count), "verb list"),
        (_LABEL_OFFSET, _COMMIT_OFFSET,
         build_send_label(), "label"),
        (_COMMIT_OFFSET, patch.SEND_ROW_CODE_CAPACITY,
         build_send_commit(count), "commit"),
    ):
        if offset + len(payload) > limit:
            raise ValueError(
                f"The Send row's {name} routine needs {len(payload)} bytes "
                f"and has {limit - offset}."
            )
        code[offset:offset + len(payload)] = payload
    block[
        patch.SEND_ROW_CODE_OFFSET :
        patch.SEND_ROW_CODE_OFFSET + len(code)
    ] = code
    names = build_target_names(target_names)
    block[
        patch.SEND_ROW_NAMES_OFFSET :
        patch.SEND_ROW_NAMES_OFFSET + len(names)
    ] = names


def build_handler_table() -> bytes:
    """Seven little-endian handler words, Send above Feet.

    Row order: Items, Select up, Line up, Fuse, Command, SEND, Feet - the
    Send row sits at index 5 and Feet drops to the bottom, so the habitual
    "up from the top wraps to Feet" input still reaches Feet. Items and
    Feet dispatch through the flag-clearing stubs so an ordinary item
    session can never start with send mode still up. The dispatcher's flag
    bytes need no relocation either way: vanilla 0x800717B9 and
    0x800717BA both read 0x00, the no-familiar-dependency value both rows
    want.
    """

    handlers = list(VANILLA_HANDLERS)
    handlers[0] = ITEMS_STUB_ADDRESS
    handlers[5] = SEND_HANDLER_ROUTINE_ADDRESS
    table = struct.pack(
        "<7I", *handlers, FEET_STUB_ADDRESS
    )
    if RELOCATED_BLOCK_ADDRESS + len(table) > ITEMS_STUB_ADDRESS:
        raise ValueError("The relocated handler table overruns its block.")
    return table


def iter_tower_send_file_patches(
    target_names: list[str],
) -> tuple[tuple[int, bytes], ...]:
    """SLUS file patches for the Send row, or nothing at all with no targets."""

    if not target_names:
        return ()
    if len(SEND_ROW_RECORD) != 12:
        raise ValueError("The Send row record must be exactly 12 bytes.")
    # The record lives in the retired card driver's spare tail, above
    # everything alternate_pickup lays out there.  Fail generation rather
    # than silently overlap if that module ever grows.
    pickup_end = (
        alternate_pickup.BLOCK_ADDRESS + alternate_pickup.resident_block_size()
    )
    if SEND_ROW_RECORD_ADDRESS < pickup_end:
        raise ValueError(
            f"Send row record at 0x{SEND_ROW_RECORD_ADDRESS:08x} overlaps the "
            f"alternate-pickup block ending at 0x{pickup_end:08x}."
        )
    if SEND_ROW_RECORD_ADDRESS + 12 > alternate_pickup.BLOCK_END_ADDRESS:
        raise ValueError("Send row record overruns the retired card driver.")

    items_stub = _build_clear_flag_stub(ITEMS_HANDLER_ADDRESS)
    feet_stub = _build_clear_flag_stub(FEET_HANDLER_ADDRESS)
    if len(items_stub) != FEET_STUB_ADDRESS - ITEMS_STUB_ADDRESS:
        raise ValueError("The Items stub does not fill its slot exactly.")
    if FEET_STUB_ADDRESS + len(feet_stub) > RELOCATED_BLOCK_END_ADDRESS:
        raise ValueError("The Feet stub overruns the relocated block.")

    placements = (
        (HANDLER_TABLE_ADDRESS, build_handler_table()),
        (ITEMS_STUB_ADDRESS, items_stub),
        (FEET_STUB_ADDRESS, feet_stub),
        (SEND_ROW_RECORD_ADDRESS, SEND_ROW_RECORD),
        # Feet and Hand draw on the bottom row's line now.
        *(
            (address, bytes((value,)))
            for address, value in FEET_HAND_POSITION_PATCHES
        ),
        (TABLE_LUI_ADDRESS, struct.pack("<I", _TABLE_LUI_WORD)),
        # Both routes to the addiu, not just the fall-through one - see the
        # comment on TABLE_LUI_DELAY_SLOT_ADDRESS.
        (TABLE_LUI_DELAY_SLOT_ADDRESS, struct.pack("<I", _TABLE_LUI_WORD)),
        (TABLE_ADDIU_ADDRESS, struct.pack("<I", _TABLE_ADDIU_WORD)),
        (ROW_COUNT_ADDRESS, struct.pack("<I", _ROW_COUNT_WORD)),
        (FADE_BOUND_ADDRESS, struct.pack("<I", _FADE_BOUND_WORD)),
        (POSITION_BOUND_ADDRESS, struct.pack("<I", _POSITION_BOUND_WORD)),
        (POPOUT_BOUND_ADDRESS, struct.pack("<I", _POPOUT_BOUND_WORD)),
        (FADE_DONE_TEST_ADDRESS, struct.pack("<I", _FADE_DONE_TEST_WORD)),
        # Grow the cursor rail from six rows to seven. The blits that build
        # the seventh row's art live in the seed-page uploader.
        (
            patch.RAIL_RECORD_ADDRESS + patch.RAIL_HEIGHT_OFFSET,
            bytes((patch.RAIL_EXTENDED_HEIGHT,)),
        ),
        (
            ASSIGNER_HOOK_ADDRESS,
            struct.pack("<I", _jal(ASSIGNER_STUB_ADDRESS)),
        ),
        # Route the players-menu creator through the seed page's strip
        # uploader, which tail-jumps back into the creator.
        (
            patch.PLAYERS_MENU_CREATOR_CALL_ADDRESS,
            struct.pack("<I", _jal(patch.SEND_ROW_UPLOAD_ADDRESS)),
        ),
        # Send mode starts clear; these two words are otherwise vanilla
        # card-driver bytes, and a stale controller pointer would be read.
        (SEND_STATE_ADDRESS, bytes(SEND_STATE_SIZE)),
        # Divert both of alternate_pickup's verb hooks through the send-mode
        # routines. Each replaces the hook's FIRST instruction, which the
        # routine replays before deciding; non-send cases jump back in at +4.
        (
            _verb_list_hook_address(),
            struct.pack("<I", patch._j(0x02, SEND_VERB_LIST_ROUTINE_ADDRESS)),
        ),
        # The label hook needs its SECOND word nopped as well: a jump's delay
        # slot still executes, and that word is a branch on a register the
        # displaced first instruction was supposed to set. Left alone it
        # branched on garbage - the crash the first send-mode disc showed.
        (
            _label_hook_address(),
            struct.pack(
                "<2I", patch._j(0x02, SEND_LABEL_ROUTINE_ADDRESS), 0
            ),
        ),
    )
    return tuple(
        (save_removal.slus_runtime_to_file_offset(address), payload)
        for address, payload in placements
    )


def iter_tower_send_dungeon_file_patches(
    target_names: list[str],
) -> tuple[tuple[int, bytes], ...]:
    """DUNGEON.BIN patches: the put-in source-test bail becomes the commit.

    `put_item_into_bag` rejects a bag descriptor before it ever reaches the
    allocator hook, so the commit is planted on the rejection itself: the
    `bne s1,v0,epilogue` at PUT_IN_SOURCE_TEST_ADDRESS and its delay-slot
    `addu v0,s1,zero` become `j commit` + nop. The commit re-issues both
    displaced behaviours (the ground compare, and v0 = s1 on every bail),
    and alternate_pickup's `jal PUT_IN_GUARD` at the allocator hook is left
    untouched - no overlapping records at all on this seam.
    """

    if not target_names:
        return ()
    return (
        (
            alternate_pickup.dungeon_runtime_to_file_offset(
                PUT_IN_SOURCE_TEST_ADDRESS
            ),
            struct.pack("<2I", patch._j(0x02, SEND_COMMIT_ROUTINE_ADDRESS), 0),
        ),
    )


def append_tower_send_ppf_records(
    ppf: bytearray, target_names: list[str]
) -> None:
    """Emit the Send row's records. With no targets this writes nothing."""

    targets = list(target_names)[:MAX_TARGETS]
    for raw_offset, data in (
        *save_removal._iter_mode2_raw_patches(
            save_removal.SLUS_FILE_START_LBA,
            iter_tower_send_file_patches(targets),
        ),
        *save_removal._iter_mode2_raw_patches(
            save_removal.DUNGEON_FILE_START_LBA,
            iter_tower_send_dungeon_file_patches(targets),
        ),
    ):
        copied = 0
        while copied < len(data):
            record = data[copied:copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
