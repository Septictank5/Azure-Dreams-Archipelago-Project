"""The alternate ways a player can touch a floor marker.

Walking onto a marker has been hooked since the beginning, at the pickup
classifier `0x8009513C`.  Every *other* route the game offers reached the
marker unguarded and treated it as what its descriptor claims to be - a Wind
Crystal - which is how `put in` inserted a fake item that crashed when used.

Four routes exist and they meet in three places:

| Route | Seam |
| --- | --- |
| walk onto it | pickup classifier `0x8009513C` - hooked already |
| lift it (hold Circle) | held-object constructor's asset call `0x800A8FA0` |
| stand on it / read its name anywhere | resident name lookup `0x8004AC3C` |
| menu `put in`, from hand or from feet | `put_item_into_bag` `0x80097F84` |

`docs/design-invariants.md`: a pickup by any route must record the check,
remove the marker, show generated text, and grant nothing.

Everything here lives in the retired memory-card driver at
`0x8004EB68`-`0x8004EE4F`, which is resident in `SLUS_006.14` from boot and is
never covered by an overlay load.  That matters for the name lookup, which the
*front-menu package* also calls: hooking the resident callee reaches all six
call sites, five of which are inside an LZ-compressed stream we would otherwise
have to re-encode.
"""

from __future__ import annotations

import struct

from . import patch, save_removal


# --- the marker descriptor ---------------------------------------------------
#
# Defined in `patch.py`, which also builds the spawner-side descriptor and the
# render resolver from the same four values.  It used to be a Wind Crystal
# identity; it is a Gift now, because two lookups nobody had hooked believed
# the category - the per-category name suffix table appended `　crystal`, and
# menus drew the crystal icon.
MARKER_ID = patch.MARKER_ID
MARKER_CATEGORY = patch.MARKER_CATEGORY
MARKER_STATUS = patch.MARKER_STATUS
MARKER_SLOT_COUNT = patch.MARKER_SLOT_COUNT

TOWER_FLOOR_COUNT = 39
LOCATION_COUNT = patch.LOCATION_COUNT
# Nothing here multiplies the floor by the slot count any more: the journal is
# one byte per floor and the bit is the slot (patch.PERSISTENT_LOCATION_MASK_OFFSET).
# The `sll t2,a0,1 / addu / sll 2` in the put-in guard is the ground-entity
# record stride (x12), not a location index - it looks like the bug and is not.

# --- vanilla seams -----------------------------------------------------------

# `get_item_display_name(descriptor, out_flags) -> char *`, resident.  Called
# once from the dungeon overlay's message system and five times from the
# front-menu package, so it is the single place every rendered item name passes
# through.  Returns a NUL-terminated *raw full-width CP932* string; the compact
# battle encoding used by the placement text is not accepted here.
NAME_LOOKUP_ADDRESS = 0x8004_AC3C
# `sw zero,0x0(s1)` - the first instruction after the frame is complete and
# `s1 = a1`.  Hooking the entry itself is not possible: its delay slot is
# `sw s0,0x10(sp)`, which would run against the caller's stack pointer.
NAME_LOOKUP_HOOK_ADDRESS = 0x8004_AC54
NAME_LOOKUP_RESUME_ADDRESS = 0x8004_AC5C
NAME_LOOKUP_FRAME_SIZE = 0x20

# `put_item_into_bag(descriptor, full_prefix, full_suffix, show_message)`.
# Accepts exactly three descriptor sources - the in-hand slot, the at-feet
# staging slot, and the ground descriptor the actor is standing on - and is
# reached from the menu's `put in` action (`caseD_11` at `0x800913B0`) and two
# return-to-bag helpers.  One guard covers all of them.
PUT_IN_ADDRESS = 0x8009_7F84
# `jal find_unused_inventory_descriptor_index`.  By this point all nine
# callee-saved registers and `ra` are spilled, `s1` holds the descriptor and
# `s3` holds show_message, so the guard can either fall through to the vanilla
# allocator or leave through the routine's own epilogue.
PUT_IN_HOOK_ADDRESS = 0x8009_7FF0
PUT_IN_ALLOCATOR_ADDRESS = 0x8009_8FB0
PUT_IN_EPILOGUE_ADDRESS = 0x8009_81C8

# `get_item_description(descriptor, fallback_table) -> char *`, resident, and
# already known to this project as `town_shop.VANILLA_ITEM_DESCRIPTION_ADDRESS`.
# `0x80049390` is the first instruction after `s0 = a0` and `ra` is spilled, and
# its delay slot is a `nop`, which makes it the one clean place to divert.
DESCRIPTION_ADDRESS = 0x8004_9374
DESCRIPTION_HOOK_ADDRESS = 0x8004_9390
DESCRIPTION_RESUME_ADDRESS = 0x8004_9398

HELD_ITEM_ASSET_CALL_ADDRESS = 0x800A_8FA0
GROUND_ITEM_ASSET_CALL_ADDRESS = 0x800A_7BA0
VANILLA_ASSET_RESOLVER_ADDRESS = 0x800A_7A38

IN_HAND_DESCRIPTOR_ADDRESS = 0x8008_1484
PLAYER_ACTOR_POINTER_ADDRESS = 0x8008_14A8
FLOOR_CONTEXT_POINTER_ADDRESS = 0x800E_3D7C
GROUND_ITEM_DESCRIPTORS_ADDRESS = 0x800E_3548
GROUND_ITEM_ENTITIES_ADDRESS = 0x800E_36C8
CURRENT_FLOOR_ADDRESS = 0x8008_146C
# The tower journal: ADSV +0x10, one byte per floor. It was a hard-coded
# 0x8001_5FD0 (ADSV v3's mask) until the v4 re-lay moved the record.
COLLECTION_JOURNAL_ADDRESS = (
    patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_LOCATION_MASK_OFFSET
)

FIND_ARRAY_INDEX_ADDRESS = 0x8004_22A8
CLEAR_TILE_FLAGS_ADDRESS = 0x8009_A3D0
ALLOCATE_MESSAGE_BUFFER_ADDRESS = 0x8009_90FC
APPEND_CONTROL_CODE_ADDRESS = 0x8009_929C
TERMINATE_MESSAGE_ADDRESS = 0x8009_9290
DISPLAY_MESSAGE_ADDRESS = 0x800A_5720
PLAY_SOUND_ADDRESS = 0x800A_56E0
PUT_IN_SOUND_ID = 0x508
MESSAGE_START_CONTROL_CODE = 8
TILE_OCCUPIED_BY_ITEM = 0x800
ACTOR_HOLDING_ITEM_BIT = 0x0010_0000

# --- our resident block ------------------------------------------------------
#
# `save_removal.build_title_label_helper` ends at 0x8004EB68 and the two
# disabled card entries start at 0x8004EE50, so this is the whole remaining
# span of the retired driver.  Generation asserts against both ends.
BLOCK_ADDRESS = 0x8004_EB68
BLOCK_END_ADDRESS = save_removal.RETIRED_CARD_DRIVER_END_ADDRESS

# Data comes first at fixed offsets; the four code blocks are packed after it
# in declaration order by `_resolve_layout`, which runs the builders twice -
# once to measure, once for real.  A jump target changes an instruction's bits
# but never its length, so the second pass cannot shift anything.
# The two strings moved out to the tower gameplay payload's own free tail; the
# retired driver has no room for them and every reader reaches them only after
# the marker test has proved the payload is loaded.
MARKER_NAME_ADDRESS = patch.MARKER_DISPLAY_NAME_ADDRESS  # the `Strange...` field text
SEND_LABEL_ADDRESS = patch.MARKER_SEND_LABEL_ADDRESS
MENU_MARKER_FLAG_ADDRESS = BLOCK_ADDRESS  # 0x8004EB68
CODE_ADDRESS = BLOCK_ADDRESS + 0x04  # 0x8004EB6C

# Filled in by `_resolve_layout` before any builder emits a real jump.
MARKER_TEST_ADDRESS = CODE_ADDRESS
NAME_GUARD_ADDRESS = CODE_ADDRESS
PUT_IN_GUARD_ADDRESS = CODE_ADDRESS
VERB_LIST_HOOK_ADDRESS = CODE_ADDRESS
VERB_LABEL_HOOK_ADDRESS = CODE_ADDRESS
DESCRIPTION_TRAMPOLINE_ADDRESS = CODE_ADDRESS

# --- front-menu package seams ------------------------------------------------
#
# These addresses are inside the LZ-compressed tower front-menu package
# (`0x80016000`-`0x80024B1F`, streams at `DUNGEON.BIN+0x4A3014` and `+0x4A7014`).
# Both edits are one-word instruction rewrites in stream 1, so the decoded
# length does not change; `tools/Rebuild-AdapFrontMenuStreams.ps1` re-encodes
# and splices them into the base patch.
VERB_LIST_BUILDER_TAIL_ADDRESS = 0x8001_D0D4
VERB_LIST_RESUME_ADDRESS = 0x8001_D0D8
VERB_LABEL_LOOKUP_ADDRESS = 0x8001_CD5C
VERB_LABEL_TABLE_ADDRESS = 0x8007_16F4
FRONT_MENU_STREAM_BASE_ADDRESS = 0x8001_6000

# Vanilla row 0x0F, `Put in`, whose action is the menu's put-in dispatch at
# `caseD_11`.  Reused rather than invented: a new verb id would need both the
# resident label table and the in-stream action table extended, and neither has
# a free slot.
SEND_VERB_ID = 0x0F

# **A generic name is required, not a shortcut.**  `docs/design-invariants.md`:
# ground items do not leak ownership, and every marker renders through the same
# gift asset "so a check on the floor cannot be read as local or remote before
# pickup".  Naming the floor item after its contents would defeat that far more
# thoroughly than the render ever could.  The pickup message still names the
# real item, because by then the check has been recorded.
MARKER_DISPLAY_NAME = "Gift"

# The menu verb for a marker.  Vanilla row 0x0F is `Put in`; the label hook
# swaps the string while the flag is set, so only markers read `Send`.
SEND_VERB_LABEL = "Send"


def _text(value: str) -> bytes:
    encoded = patch._full_width_cp932(value) + b"\0"
    if len(encoded) > TEXT_SLOT_SIZE:
        raise ValueError(
            f"{value!r} encodes to {len(encoded)} bytes, over the "
            f"{TEXT_SLOT_SIZE}-byte slot."
        )
    return encoded.ljust(TEXT_SLOT_SIZE, b"\0")


def build_marker_test() -> bytes:
    """`is_ap_marker(a0) -> v0`, the one copy of the test.

    Mirrors what the collect hook at `0x801FE8B0` already performs, seed page
    included, so a stray descriptor that happens to look like a marker on a
    disc with no seed cannot be mistaken for one.

    Clobbers `v0`, `t0` and `t1` only.  Every caller needs `a0` afterwards and
    keeps it; inlining this three times cost 240 of the retired driver's 744
    bytes, which is why it is a subroutine.

    Every load is followed by an instruction independent of its destination
    register; the R3000A has no load interlock.
    """

    b = patch._MipsBuilder()
    b.emit(
        patch._i(0x24, 4, 8, 0),  # lbu t0,0(a0)   id
        patch._i(0x09, 0, 9, MARKER_ID),
    )
    b.branch(0x05, 8, 9, "no")
    b.emit(
        patch._i(0x24, 4, 8, 1),  # (delay) lbu t0,1(a0)   category
        patch._i(0x09, 0, 9, MARKER_CATEGORY),
    )
    b.branch(0x05, 8, 9, "no")
    b.emit(
        patch._i(0x24, 4, 8, 3),  # (delay) lbu t0,3(a0)   status
        patch._i(0x09, 0, 9, MARKER_STATUS),
    )
    b.branch(0x05, 8, 9, "no")
    b.emit(
        patch._i(0x24, 4, 8, 2),  # (delay) lbu t0,2(a0)   slot
        0,
        patch._i(0x0B, 8, 9, MARKER_SLOT_COUNT),
    )
    b.branch(0x04, 9, 0, "no")
    b.emit(0)

    patch._load_address(b, 8, patch.SEED_BLOCK_ADDRESS)
    b.emit(
        patch._i(0x23, 8, 8, 0),  # lw t0,0(t0)   seed magic
        patch._i(0x0F, 0, 9, patch.SEED_MAGIC >> 16),
        patch._i(0x0D, 9, 9, patch.SEED_MAGIC),
    )
    b.branch(0x05, 8, 9, "no")
    b.emit(0)
    b.emit(
        patch._r(31, 0, 0, 0, 0x08),  # jr ra
        patch._i(0x09, 0, 2, 1),  # (delay) li v0,1
    )

    b.label("no")
    b.emit(
        patch._r(31, 0, 0, 0, 0x08),  # jr ra
        patch._r(0, 0, 2, 0, 0x21),  # (delay) move v0,zero
    )
    return b.build()


def _emit_marker_test(
    builder: patch._MipsBuilder,
    pointer: int,
    reject: str,
) -> None:
    """Call `is_ap_marker` on `pointer` and branch to `reject` when it is not.

    `ra` is destroyed.  Every caller either has its own `ra` spilled by the
    routine it was planted in, or saves it first.
    """

    builder.emit(
        patch._j(0x03, MARKER_TEST_ADDRESS),
        # The delay slot carries the argument when it needs moving at all.
        patch._r(pointer, 0, 4, 0, 0x21) if pointer != 4 else 0,
    )
    builder.branch(0x04, 2, 0, reject)  # beq v0,zero
    builder.emit(0)


def build_name_guard() -> bytes:
    """Divert a marker's name to the payload-side entry point.

    Entered by a `j` planted over `sw zero,0x0(s1)`, so the store is replayed
    here.  `v1` already holds the town-mode byte loaded in the jump's delay
    slot and the vanilla continuation compares it, so it must survive; `a0`
    (the descriptor) and `s0`/`s1`/`ra`/`sp` must survive too.

    `ra` is captured first, because the marker test is a `jal` and destroys it,
    and the payload side needs it: the message system gets the real item name
    and the item-name field gets `Strange...`, and the return address is what
    tells them apart.
    """

    b = patch._MipsBuilder()
    b.emit(
        patch._i(0x2B, 17, 0, 0),  # sw zero,0(s1) - the displaced store
        patch._r(31, 0, 10, 0, 0x21),  # move t2,ra   before the jal below
    )
    _emit_marker_test(b, pointer=4, reject="vanilla")
    b.emit(
        patch._j(0x02, patch.MARKER_NAME_ENTRY_ADDRESS),
        patch._r(10, 0, 6, 0, 0x21),  # (delay) move a2,t2
    )

    b.label("vanilla")
    b.emit(patch._j(0x02, NAME_LOOKUP_RESUME_ADDRESS), 0)
    return b.build()


def build_put_in_guard() -> bytes:
    """Send the check instead of inserting the marker.

    Entered by `jal` in place of `jal find_unused_inventory_descriptor_index`,
    so `ra` is expendable: an ordinary descriptor tail-jumps to the allocator,
    whose own `jr ra` returns to the instruction after the hook exactly as
    before.

    A marker never reaches the inventory.  It records the journal bit, shows
    the generated placement text, clears whichever source it came from, and
    leaves through the routine's own epilogue with `v0 = 0`, which every caller
    already handles as "nothing was inserted".

    `s1` is the descriptor, `s3` is show_message.  The vanilla source-clearing
    sequences are reproduced from `0x800980E8` (in hand) and `0x80098110` (at
    feet) with only the two inventory stores omitted.
    """

    b = patch._MipsBuilder()
    # A sixteen-byte frame: `ra` has to survive the marker test's own `jal`
    # because the ordinary path leaves through the vanilla allocator's `jr ra`,
    # and the message buffer has to survive four calls.  Both exits restore it,
    # so the epilogue at 0x800981C8 still sees its own frame.
    b.emit(
        patch._i(0x09, 29, 29, -0x10),  # addiu sp,sp,-0x10
        patch._i(0x2B, 29, 31, 0),  # sw ra,0(sp)
    )
    _emit_marker_test(b, pointer=17, reject="vanilla")

    # Journal byte = floor - 1, bounded by the floor count; the marker test
    # has already bounded the slot. Same shape as the payload's spawner and
    # collect hook (tools/Rebuild-AdapGameplayPayload.py converts those).
    b.emit(
        patch._i(0x24, 17, 9, 2),       # lbu   t1,2(s1)     slot
        patch._i(0x0F, 0, 8, 0x8008),
        patch._i(0x25, 8, 10, 0x146C),  # lhu   t2,floor
        0,
        patch._i(0x09, 10, 10, -1),     # addiu t2,t2,-1     journal byte = floor-1
        patch._i(0x0B, 10, 11, patch.PERSISTENT_TOWER_MASK_FLOORS),
    )
    b.branch(0x04, 11, 0, "vanilla")
    b.emit(0)

    # Set the location's bit: journal[floor - 1] |= 1 << slot. One byte per
    # floor means no multiply and no bit-index split - the arithmetic that used
    # to live here was unrolled for two slots per floor and broke silently when
    # the count changed. The marker test has already bounded the slot.
    b.emit(
        patch._i(0x09, 0, 12, 1),
        patch._r(9, 12, 12, 0, 0x04),   # sllv  t4,t4,t1     1 << slot
    )
    patch._load_address(b, 8, COLLECTION_JOURNAL_ADDRESS)
    b.emit(
        patch._r(8, 10, 8, 0, 0x21),    # addu  t0,t0,t2
        patch._i(0x24, 8, 10, 0),       # lbu   t2,0(t0)
        0,
        patch._r(10, 12, 10, 0, 0x25),  # or    t2,t2,t4
        patch._i(0x28, 8, 10, 0),       # sb    t2,0(t0)
    )

    # The generated placement text, composed exactly as the walk-over pickup
    # composes it: control code 8, then the pooled sentence.
    b.emit(patch._j(0x03, ALLOCATE_MESSAGE_BUFFER_ADDRESS), 0)
    b.emit(
        patch._i(0x2B, 29, 2, 4),  # sw v0,4(sp)   the message buffer
        patch._i(0x09, 0, 4, MESSAGE_START_CONTROL_CODE),
        patch._j(0x03, APPEND_CONTROL_CODE_ADDRESS),
        patch._r(2, 0, 5, 0, 0x21),  # (delay) move a1,v0
        patch._r(2, 0, 5, 0, 0x21),  # move a1,v0   cursor
        patch._j(0x03, patch.APPEND_LOCATION_MESSAGE_ADDRESS),
        patch._r(17, 0, 4, 0, 0x21),  # (delay) move a0,s1
        patch._j(0x03, TERMINATE_MESSAGE_ADDRESS),
        patch._r(2, 0, 4, 0, 0x21),  # (delay) move a0,v0
    )
    b.branch(0x04, 19, 0, "sound")  # beq s3,zero - show_message
    b.emit(
        patch._i(0x23, 29, 4, 4),  # (delay) lw a0,4(sp)
        patch._j(0x03, DISPLAY_MESSAGE_ADDRESS),
        0,
    )

    b.label("sound")
    b.emit(
        patch._j(0x03, PLAY_SOUND_ADDRESS),
        patch._i(0x09, 0, 4, PUT_IN_SOUND_ID),  # (delay) li a0,sound
    )

    # Clear whichever source the descriptor came from.
    patch._load_address(b, 8, IN_HAND_DESCRIPTOR_ADDRESS)
    b.branch(0x05, 17, 8, "at_feet")
    b.emit(0)

    # In hand: drop the descriptor and clear the actor's holding state.
    patch._load_address(b, 9, PLAYER_ACTOR_POINTER_ADDRESS)
    b.emit(
        patch._i(0x23, 9, 9, 0),  # lw t1,(pointer)
        0,
        patch._i(0x23, 9, 10, 0x1C),  # lw t2,0x1c(t1)
        patch._i(0x0F, 0, 11, (~ACTOR_HOLDING_ITEM_BIT >> 16) & 0xFFFF),
        patch._i(0x0D, 11, 11, 0xFFFF),
        patch._r(10, 11, 10, 0, 0x24),  # and t2,t2,t3
        patch._i(0x2B, 8, 0, 0),  # sw zero,0(t0)
        patch._i(0x2B, 9, 0, 0x124),  # sw zero,0x124(t1)
        patch._i(0x2B, 9, 10, 0x1C),  # sw t2,0x1c(t1)
    )
    b.branch(0x04, 0, 0, "done")
    b.emit(0)

    # At feet: clear the tile's item flag, then the ground descriptor.
    b.label("at_feet")
    patch._load_address(b, 9, FLOOR_CONTEXT_POINTER_ADDRESS)
    b.emit(
        patch._i(0x23, 9, 9, 0),
        patch._i(0x09, 0, 6, 4),  # li a2,4      element size
        patch._i(0x23, 9, 4, 0xF0),  # lw a0,0xf0(t1)
    )
    patch._load_address(b, 5, GROUND_ITEM_DESCRIPTORS_ADDRESS)
    b.emit(
        patch._j(0x03, FIND_ARRAY_INDEX_ADDRESS),
        patch._i(0x09, 0, 7, 0x40),  # (delay) li a3,64
        patch._r(0, 2, 2, 16, 0x00),  # sll v0,v0,16
        patch._r(0, 2, 4, 16, 0x03),  # sra a0,v0,16
    )
    b.branch(0x01, 4, 0, "clear_ground")  # bltz a0
    b.emit(0)
    patch._load_address(b, 9, GROUND_ITEM_ENTITIES_ADDRESS)
    b.emit(
        patch._r(0, 4, 10, 1, 0x00),  # sll t2,a0,1
        patch._r(10, 4, 10, 0, 0x21),  # addu t2,t2,a0
        patch._r(0, 10, 10, 2, 0x00),  # sll t2,t2,2      index * 12
        patch._r(9, 10, 10, 0, 0x21),  # addu t2,t1,t2
        patch._i(0x24, 10, 4, 0),  # lbu a0,0(t2)
        patch._i(0x24, 10, 5, 1),  # lbu a1,1(t2)
        patch._j(0x03, CLEAR_TILE_FLAGS_ADDRESS),
        patch._i(0x09, 0, 6, TILE_OCCUPIED_BY_ITEM),  # (delay) li a2
    )

    b.label("clear_ground")
    patch._load_address(b, 9, PLAYER_ACTOR_POINTER_ADDRESS)
    b.emit(
        patch._i(0x23, 9, 9, 0),
        0,
        patch._i(0x23, 9, 10, 0xF0),  # lw t2,0xf0(t1)
        0,
        patch._i(0x2B, 10, 0, 0),  # sw zero,0(t2)
    )

    b.label("done")
    b.emit(
        patch._i(0x2B, 17, 0, 0),  # sw zero,0(s1)
        patch._i(0x09, 29, 29, 0x10),  # addiu sp,sp,0x10
        patch._j(0x02, PUT_IN_EPILOGUE_ADDRESS),
        patch._r(0, 0, 2, 0, 0x21),  # (delay) move v0,zero - nothing inserted
    )

    b.label("vanilla")
    b.emit(
        patch._i(0x23, 29, 31, 0),  # lw ra,0(sp)
        patch._i(0x09, 29, 29, 0x10),  # addiu sp,sp,0x10
        patch._j(0x02, PUT_IN_ALLOCATOR_ADDRESS),
        0,
    )
    return b.build()


def build_verb_list_hook() -> bytes:
    """Collapse a marker's command menu to one row.

    `FUN_8001CDB0` picks its rows from a per-category menu class byte at
    `0x80073424 + category * 0x14`.  Gift and Crystal are both class 0, and
    class 0 always emits `Use` - so no choice of category removes it.  This
    runs at the builder's tail instead, after the vanilla list is complete, and
    replaces it wholesale for markers.

    Entered by a `j` planted over `sw s0,0x28(s2)`; the store is replayed here.
    `s2` is the controller and its `+0x68` is the descriptor pointer.  The
    epilogue that follows restores every callee-saved register, so only `s2`
    has to survive, and it is read-only here.
    """

    b = patch._MipsBuilder()
    b.emit(patch._i(0x2B, 18, 16, 0x28))  # sw s0,0x28(s2) - displaced

    # Default the label flag off; the vanilla row 0x0F must still read `Put in`
    # for every ordinary item.
    patch._load_address(b, 8, MENU_MARKER_FLAG_ADDRESS)
    b.emit(
        patch._i(0x2B, 8, 0, 0),  # sw zero,0(t0)
        patch._i(0x23, 18, 4, 0x68),  # lw a0,0x68(s2)  descriptor
        0,
    )
    _emit_marker_test(b, pointer=4, reject="resume")

    b.emit(
        patch._i(0x09, 0, 10, SEND_VERB_ID),
        patch._i(0x28, 18, 10, 0x54),  # sb t2,0x54(s2)   the only row
        patch._i(0x09, 0, 10, 1),
        patch._i(0x2B, 18, 10, 0x28),  # sw t2,0x28(s2)   count = 1
    )
    patch._load_address(b, 8, MENU_MARKER_FLAG_ADDRESS)
    b.emit(patch._i(0x2B, 8, 10, 0))  # sw t2,0(t0)      relabel row 0x0F

    b.label("resume")
    b.emit(patch._j(0x02, VERB_LIST_RESUME_ADDRESS), 0)
    return b.build()


def build_verb_label_hook() -> bytes:
    """Read `Send` instead of `Put in`, for markers only.

    `FUN_8001CD5C` is a seven-instruction table read with no item context, so
    the verb-list hook leaves a flag for it.  Entered by a `j` planted over its
    first instruction; the delay slot (`addiu v0,v0,0x16f4`) still runs against
    an undefined `v0` and its result is discarded here.
    """

    b = patch._MipsBuilder()
    b.emit(patch._i(0x09, 0, 8, SEND_VERB_ID))
    b.branch(0x05, 4, 8, "vanilla")
    b.emit(0)
    patch._load_address(b, 8, MENU_MARKER_FLAG_ADDRESS)
    b.emit(patch._i(0x23, 8, 8, 0), 0)
    b.branch(0x04, 8, 0, "vanilla")
    b.emit(0)
    patch._load_address(b, 2, SEND_LABEL_ADDRESS)
    b.emit(patch._r(31, 0, 0, 0, 0x08), 0)

    # Every other verb rejoins the vanilla lookup one instruction in, with the
    # table base supplied here because the `lui` that used to produce it is the
    # instruction we replaced.
    b.label("vanilla")
    patch._load_address(b, 2, VERB_LABEL_TABLE_ADDRESS)
    b.emit(patch._j(0x02, VERB_LABEL_LOOKUP_ADDRESS + 8), 0)
    return b.build()


def iter_front_menu_stream_patches() -> tuple[tuple[int, bytes], ...]:
    """(decoded stream-1 offset, replacement word) for the two menu edits."""

    _resolve_layout()  # the hook addresses are assigned there, not declared
    return (
        (
            VERB_LIST_BUILDER_TAIL_ADDRESS - FRONT_MENU_STREAM_BASE_ADDRESS,
            struct.pack("<I", patch._j(0x02, VERB_LIST_HOOK_ADDRESS)),
        ),
        (
            VERB_LABEL_LOOKUP_ADDRESS - FRONT_MENU_STREAM_BASE_ADDRESS,
            struct.pack("<I", patch._j(0x02, VERB_LABEL_HOOK_ADDRESS)),
        ),
    )


def build_description_trampoline() -> bytes:
    """Divert a marker's item description to the payload-side builder.

    The builder itself lives in the tower gameplay payload because the retired
    driver has no room, and it may only be entered once the seed page is known
    loaded - which is exactly what the marker test proves, since it reads the
    page's magic.  So the resident side is the test and the branch, and nothing
    else.

    Entered by a `j` planted over `lbu v0,0x1(s0)`; the delay slot is a vanilla
    `nop`.  `s0` is the descriptor, and `0x80049374` has already spilled `ra`,
    `s0` and `s1`, so the builder can unwind that frame itself.
    """

    b = patch._MipsBuilder()
    b.emit(
        patch._j(0x03, MARKER_TEST_ADDRESS),
        patch._r(16, 0, 4, 0, 0x21),  # (delay) move a0,s0
    )
    b.branch(0x04, 2, 0, "vanilla")
    b.emit(
        0,
        patch._j(0x02, patch.MARKER_DESCRIBE_ENTRY_ADDRESS),
        patch._r(16, 0, 4, 0, 0x21),  # (delay) move a0,s0
    )

    b.label("vanilla")
    b.emit(
        patch._i(0x24, 16, 2, 1),  # lbu v0,0x1(s0) - the displaced load
        patch._j(0x02, DESCRIPTION_RESUME_ADDRESS),
        0,
    )
    return b.build()


# --- layout and record emission ----------------------------------------------

# Packed in this order, immediately after the data words.  The names double as
# the module-level address constants the builders read.
_CODE_BLOCKS = (
    ("MARKER_TEST_ADDRESS", "build_marker_test"),
    ("NAME_GUARD_ADDRESS", "build_name_guard"),
    ("PUT_IN_GUARD_ADDRESS", "build_put_in_guard"),
    ("VERB_LIST_HOOK_ADDRESS", "build_verb_list_hook"),
    ("VERB_LABEL_HOOK_ADDRESS", "build_verb_label_hook"),
    ("DESCRIPTION_TRAMPOLINE_ADDRESS", "build_description_trampoline"),
)


def _resolve_layout() -> tuple[tuple[int, bytes], ...]:
    """Assign the code blocks their addresses, then build them for real.

    Two passes.  The first runs every builder with the provisional addresses
    just to measure it; a jump's target changes its bits but never its length,
    so the second pass produces the same sizes at the final addresses.  The
    alternative - hand-maintained offsets - was wrong twice while this was
    being written, once by 136 bytes.
    """

    globals_ = globals()
    for _ in range(2):
        address = CODE_ADDRESS
        built: list[tuple[int, bytes]] = []
        for name, builder in _CODE_BLOCKS:
            globals_[name] = address
            payload = globals_[builder]()
            built.append((address, payload))
            address += len(payload)
            if address % 4:
                raise ValueError(f"{builder} is not a whole number of words.")

    if address > BLOCK_END_ADDRESS:
        raise ValueError(
            f"The alternate-pickup block needs {address - BLOCK_ADDRESS} bytes "
            f"and the retired card driver has "
            f"{BLOCK_END_ADDRESS - BLOCK_ADDRESS}. It ends at "
            f"0x{address:08x}, past 0x{BLOCK_END_ADDRESS:08x}."
        )
    return tuple(built)


def iter_slus_file_patches() -> tuple[tuple[int, bytes], ...]:
    """Resident block plus the one-word hook over the name lookup."""

    placements = (
        (MENU_MARKER_FLAG_ADDRESS, bytes(4)),
        *_resolve_layout(),
        (
            NAME_LOOKUP_HOOK_ADDRESS,
            struct.pack("<I", patch._j(0x02, NAME_GUARD_ADDRESS)),
        ),
        (
            DESCRIPTION_HOOK_ADDRESS,
            struct.pack("<I", patch._j(0x02, DESCRIPTION_TRAMPOLINE_ADDRESS)),
        ),
    )
    return tuple(
        (save_removal.slus_runtime_to_file_offset(address), payload)
        for address, payload in placements
    )


def resident_block_size() -> int:
    """How much of the retired card driver this module actually uses."""

    return max(
        address + len(payload) for address, payload in _resolve_layout()
    ) - BLOCK_ADDRESS


def iter_dungeon_file_patches() -> tuple[tuple[int, bytes], ...]:
    """Point the held-object and dropped-object asset calls at the seed resolver.

    The ground entity's own call at `0x800A8690` has been redirected since the
    marker first rendered; these two were missed, which is why a lifted marker
    still showed the Wind Crystal.  The seed resolver tail-calls the vanilla one
    for every ordinary descriptor, so redirecting a call it does not need is
    harmless.
    """

    redirect = struct.pack(
        "<I", patch._j(0x03, patch.RESOLVE_LOCATION_RENDER_ADDRESS)
    )
    return tuple(
        (dungeon_runtime_to_file_offset(address), redirect)
        for address in (
            HELD_ITEM_ASSET_CALL_ADDRESS,
            GROUND_ITEM_ASSET_CALL_ADDRESS,
        )
    )


# The gameplay overlay is an untouched slice of DUNGEON.BIN from file offset
# 0x1A8A0 that runs at 0x80000000; `DUNGEON.BIN+0xA3000` is `0x80088760`.
DUNGEON_OVERLAY_FILE_BASE = 0x1_A8A0


def dungeon_runtime_to_file_offset(address: int) -> int:
    if not 0x8000_0000 <= address < 0x800F_0000 + DUNGEON_OVERLAY_FILE_BASE:
        raise ValueError(f"Address 0x{address:08x} is outside the gameplay overlay.")
    return address - 0x8000_0000 + DUNGEON_OVERLAY_FILE_BASE


def append_alternate_pickup_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_alternate_pickup_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)


def iter_alternate_pickup_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        *save_removal._iter_mode2_raw_patches(
            save_removal.SLUS_FILE_START_LBA,
            iter_slus_file_patches(),
        ),
        *save_removal._iter_mode2_raw_patches(
            save_removal.DUNGEON_FILE_START_LBA,
            iter_dungeon_file_patches(),
        ),
        *_iter_put_in_hook_raw_patches(),
    )


def _iter_put_in_hook_raw_patches() -> tuple[tuple[int, bytes], ...]:
    hook = struct.pack("<I", patch._j(0x03, PUT_IN_GUARD_ADDRESS))
    return save_removal._iter_mode2_raw_patches(
        save_removal.DUNGEON_FILE_START_LBA,
        ((dungeon_runtime_to_file_offset(PUT_IN_HOOK_ADDRESS), hook),),
    )


# Resolve at import so the five address constants are never read stale.  They
# are declared above only so the builders have something to close over; until
# this runs they all still say `CODE_ADDRESS`, and a caller that read one early
# would silently emit a jump into the marker test.
_resolve_layout()


__all__ = [
    "append_alternate_pickup_ppf_records",
    "iter_alternate_pickup_raw_patches",
    "iter_front_menu_stream_patches",
]
