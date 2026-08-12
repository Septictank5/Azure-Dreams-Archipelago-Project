from __future__ import annotations

import struct

from . import patch, town_shop


# The outdoor-town overlay is loaded at 0x80016000 from TOWN.BIN+0x2ab800.
# Scene entry 0x8001b330 belongs to Uncle, the NPC just outside Koh's house.
# The town variant selector accepts a descriptor only when both its event flag
# and eligibility callback return zero.  Uncle's vanilla callback returns the
# highest floor reached, which deliberately removes him after the first tower
# ascent.  Replace only that guard with the shared return-zero callback, then
# redirect his actor-specific dialogue getter.  Keep his event flag, placement
# pointer, and model vanilla.  His spawn coordinates are relocated - see
# UNCLE_RELOCATED_COORDINATES.
OUTDOOR_OVERLAY_FILE_OFFSET = 0x2A_B800
OUTDOOR_OVERLAY_RUNTIME_ADDRESS = 0x8001_6000
OUTDOOR_STATIC_DATA_END_ADDRESS = 0x8001_DF00
UNCLE_SELECTION_FUNCTION_POINTER_ADDRESS = 0x8001_B2F4
UNCLE_DIALOGUE_FUNCTION_POINTER_ADDRESS = 0x8001_B2F8
UNCLE_PLACEMENT_POINTER_ADDRESS = 0x8001_B2FC
UNCLE_EVENT_FLAG_ID_ADDRESS = 0x8001_B300
UNCLE_PLACEMENT_COORDINATE_ADDRESS = 0x8001_DDDC
UNCLE_DIALOGUE_GETTER_RETURN_INSTRUCTION_ADDRESS = 0x8001_6724
UNCLE_ALWAYS_SELECTED_CALLBACK_ADDRESS = 0x8001_A514
UNCLE_VANILLA_COORDINATES = (0x0360, 0x0320)
# Where Uncle now stands.  Two coordinate systems are in play, and the
# transform for UNCLE SPECIFICALLY is measured end to end from
# koh_next_to_uncle_position.bin (2026-07-27):
#
#   * The PLAYER's motion block ([0x800834A0]; 16.16 fixed-point words,
#     integer X/Y in the halfwords at +2/+6) is GLOBAL.  Koh standing at
#     the target spot (Koh_position.bin) read (3937, 1702).
#   * Spawn coordinates are SIGNED LOCAL offsets from the owning scene
#     section's global base.  The struct at 0x8001DDCC is a TEMPLATE the
#     scene loader copies into a runtime spawn-record arena (Uncle's live
#     record: 0x8001C4D0, zeros on disc; halfword 0 gains bit 0 while his
#     actor is instantiated, and actors activate by proximity to the
#     RECORD's position, so moving the record moves spawn and activation
#     zone together).
#
# Uncle's base is (3200, 2816): his template local (864, 800) put his live
# actor (update 0x800C3174, position at header+0xA4) at exactly
# (4064, 3616), with Koh measured beside him.  The patched pair is
# therefore (3937, 1702) - (3200, 2816) = (737, -1114).
#
# The failed attempts, kept for the method lesson: attempt one read the
# dialogue-reused RAM copy of the template (CP932 text).  Attempt two
# (0.9.58) wrote the GLOBAL pair as the offset, resolving off-map at
# (7137, 4518).  Attempt three (0.9.59) subtracted a NEIGHBORING section's
# origin (4096, 1792) inferred from prop records instead of measuring
# Uncle's own, resolving to (3041, 2726) - off-path and unfindable.  Only
# the live-actor measurement settled the base.
UNCLE_SECTION_ORIGIN = (3200, 2816)
UNCLE_VANILLA_GLOBAL_COORDINATES = (4064, 3616)
UNCLE_TARGET_GLOBAL_COORDINATES = (3937, 1702)
UNCLE_RELOCATED_COORDINATES = (
    (UNCLE_TARGET_GLOBAL_COORDINATES[0] - UNCLE_SECTION_ORIGIN[0]) & 0xFFFF,
    (UNCLE_TARGET_GLOBAL_COORDINATES[1] - UNCLE_SECTION_ORIGIN[1]) & 0xFFFF,
)

# Nada - the "children's songs are messages from God" NPC, future host of
# the send-shop (docs/nada-send-shop.md).  Identified and measured
# 2026-07-27 from koh_next_to_nada.bin by the same recipe as Uncle: her
# live actor (update 0x800C3174, record 0x8001C4F8) stood at (4064, 4160)
# with template local (480, 64), giving base (3584, 4096).  Her template
# and the child's are consecutive; the child (model 0x38, "Press X to
# attack" dialogue) deliberately stays at his vanilla spot.
#
# Closure of an old note: 0x8001DE7C and 0x8001DE90 were listed during the
# Uncle work as "old coordinate candidates ... not patched" - they were
# never Uncle's.  They are Nada's and the child's template coordinates.
NADA_TEMPLATE_ADDRESS = 0x8001_DE6C
NADA_PLACEMENT_COORDINATE_ADDRESS = 0x8001_DE7C
NADA_CHILD_TEMPLATE_ADDRESS = 0x8001_DE80
NADA_CHILD_COORDINATE_ADDRESS = 0x8001_DE90
NADA_MODEL = 0x39
NADA_CHILD_MODEL = 0x38
NADA_VANILLA_COORDINATES = (0x01E0, 0x0040)  # local (480, 64)
NADA_SECTION_ORIGIN = (3584, 4096)
NADA_VANILLA_GLOBAL_COORDINATES = (4064, 4160)
NADA_TARGET_GLOBAL_COORDINATES = (4284, 2974)  # koh_position_for_Nada.bin
NADA_RELOCATED_COORDINATES = (
    (NADA_TARGET_GLOBAL_COORDINATES[0] - NADA_SECTION_ORIGIN[0]) & 0xFFFF,
    (NADA_TARGET_GLOBAL_COORDINATES[1] - NADA_SECTION_ORIGIN[1]) & 0xFFFF,
)
UNCLE_VANILLA_DESCRIPTOR = {
    0x8001_B2F4: 0x8001_6728,
    0x8001_B2F8: 0x8001_671C,
    0x8001_B2FC: 0x8001_DDCC,
    0x8001_B300: 0x0000_0DB5,
}

# Uncle's resource is loaded at 0x8001dcd0 from TOWN.BIN+0x2d9800.  The
# original three-page script occupies 0x8001e869..0x8001e977.  Its retired
# body supplies three floor callbacks and the level-2 check.  The zero gap
# below it supplies the remaining checks and script.
#
# **The gap starts at 0x8001eaec, not 0x8001eae8.**  0x8001eae4 is the `jr ra`
# ending the routine that begins at 0x8001eab4, and 0x8001eae8 is its DELAY
# SLOT - a real instruction the console executes, not padding.  Writing the
# warp script over it put a dialogue-script byte pair where that `nop` belongs
# and killed the tower-entrance Kewne event; see
# `docs/kewne-tower-entry-crash.md`.  The earlier "live tracing found no
# execution in the gap" note was wrong: the trace it rested on predates the
# region watch, and the watch caught this exact address.
#
# Do not touch 0x8001eab4..0x8001eae8.  That routine runs during the event.
UNCLE_DIALOGUE_FILE_OFFSET = 0x2D_9800
OUTDOOR_DIALOGUE_RUNTIME_ADDRESS = 0x8001_DCD0
UNCLE_VANILLA_DIALOGUE_ENTRY_ADDRESS = 0x8001_E869
UNCLE_RETIRED_SCRIPT_CODE_START_ADDRESS = 0x8001_E86C
UNCLE_RETIRED_SCRIPT_END_ADDRESS = 0x8001_E978
UNCLE_NATIVE_CODE_START_ADDRESS = 0x8001_E98C
UNCLE_ZERO_GAP_ORIGINAL_START_ADDRESS = 0x8001_EAE8  # the jr ra delay slot
UNCLE_ZERO_GAP_START_ADDRESS = 0x8001_EAEC
UNCLE_NEXT_DIALOGUE_START_ADDRESS = 0x8001_ECD0
UNCLE_RESOURCE_END_ADDRESS = 0x8001_F4D0

# The retired script body is repacked with no gaps.  Compacting the floor
# callbacks to 32 bytes and the threshold checks to 20 moves both of the
# checks that used to sit below the script out of the zero gap, and leaves a
# contiguous tail for the inventory-limit check and its refusal page.  The
# whole of 0x8001eaec..0x8001ec78 is then available to the script, which is
# what pays for the `Destination?` prompt.  That is 396 bytes for a 386-byte
# script - the four bytes surrendered to the delay slot come out of slack, so
# nothing had to be cut to fix the crash.
WARP_FLOOR_10_ADDRESS = 0x8001_E86C
WARP_FLOOR_20_ADDRESS = 0x8001_E88C
WARP_FLOOR_30_ADDRESS = 0x8001_E8AC
WARP_LEVEL_2_CHECK_ADDRESS = 0x8001_E8CC
WARP_LEVEL_4_CHECK_ADDRESS = 0x8001_E8E0
WARP_LEVEL_6_CHECK_ADDRESS = 0x8001_E8F4
WARP_ITEM_LIMIT_CHECK_ADDRESS = 0x8001_E908
# The descriptor-based item-limit check is 44 bytes where the order-table one
# was 48, so the page slides down four to keep the body packed.  That is worth
# more than the four bytes: the page is 54 bytes, so it now ends at 0x8001E969
# instead of 0x8001E96D, and the Uncle rewrite no longer touches the word at
# 0x8001E96C that the region watch flagged.  See
# `docs/kewne-tower-entry-crash.md` - that overrun was left open deliberately
# because closing it meant shortening a player-facing message.  It closed here
# for free instead.
WARP_ITEM_LIMIT_PAGE_ADDRESS = 0x8001_E934
WARP_SCRIPT_ADDRESS = UNCLE_ZERO_GAP_START_ADDRESS
WARP_SCRIPT_LIMIT_ADDRESS = 0x8001_EC78
WARP_ACTIVATE_TOWER_ENTRANCE_ADDRESS = WARP_SCRIPT_LIMIT_ADDRESS

# v110: Uncle's rebuilt dialogue home - window +0x870, which is Nada's
# region plus her 512-byte spare tail in the unified window-offset map
# (docs/chunk5-layout.md).  In his bank that is file +0x2DA070, vanilla
# dialogue text we overwrite; the span ends where his vanilla entry (and
# the retired-script region holding the still-live level/item checks)
# begins.  The old script at WARP_SCRIPT_ADDRESS keeps being emitted but
# is unreachable once the getter points here; its floor-warp callbacks -
# the vanilla-helper path behind the shortcut flake - are NOT called by
# the rebuilt script (floor picks end silently until the tower-load
# rebuild).
UNCLE_REBUILT_SCRIPT_ADDRESS = 0x8001_E540
# Dialogue segment: the 384-byte script plus 32 bytes of edit slack;
# the section formula's 96-byte buffer follows, then the machinery.
UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS = 0x8001_E6E0

# v114: the deterministic warp trigger.  The V35 helper this replaces
# scavenged the live actor list for any scene-transition actor and
# retargeted its descriptor - and silently no-opped when the scan missed,
# which was the shortcut flake (docs/town-warp-implementation.md has the
# V31..V38 history).  The actor's only contribution to the transition is
# its descriptor: begin_scene_transition_from_actor_entry (0x800A0B74)
# reduces to "resolve descriptor, call 0x8003BAF8", and the tower road's
# descriptor is static and live-traced (0x800D4404, type 5, index 0).
# The rebuilt trigger stages the V38 marked floor (unchanged, validated),
# calls begin_scene_transition_from_descriptor directly, then
# initialize_new_tower_run_state - the same order as the native fade
# finale - with no actor dependency and no silent path.  The fade-out
# cosmetic is skipped; the cut is abrupt by design until someone misses
# it.
BEGIN_SCENE_TRANSITION_FROM_DESCRIPTOR_ADDRESS = 0x8003_BAF8  # SLUS resident
INITIALIZE_NEW_TOWER_RUN_STATE_ADDRESS = 0x800A_0EB8  # town mode overlay
TOWER_ROAD_TRANSITION_DESCRIPTOR_ADDRESS = 0x800D_4404  # town mode overlay data
# Uncle section machinery: dialogue segment ends 0x8001E6E0, the
# formula's 96-byte buffer runs to 0x8001E740, then the trigger and the
# three per-floor callbacks.
UNCLE_WARP_TRIGGER_ADDRESS = 0x8001_E740
UNCLE_REBUILT_FLOOR_CB_ADDRESS = 0x8001_E790
UNCLE_REBUILT_FLOOR_CB_STRIDE = 32
UNCLE_REBUILT_MACHINERY_LIMIT_ADDRESS = UNCLE_VANILLA_DIALOGUE_ENTRY_ADDRESS

# Entering the tower on foot rejects a player carrying more than five items.
# The shortcut retargets the native entrance actor and so never reaches that
# gate, which matters more now that a warp also grants levels.  Enforce the
# same rule from the dialogue instead: count the occupied slots in Koh's
# inventory-order table and refuse the menu outright.
INVENTORY_ORDER_TABLE_ADDRESS = 0x8001_029C

# The twenty *physical* inventory descriptors the order table points into.
# Established three ways from vanilla code, all agreeing:
#
#   * `find_unused_inventory_descriptor_index` 0x80098FB0 walks index 19 down
#     to 0 reading one byte at 0x80010249 + index * 4 and calls the slot free
#     when that byte is zero;
#   * the ordinary pickup path at 0x800953AC builds the pointer it appends to
#     the order table as `lui a0,0x8001 / ori a0,a0,0x0248 / sll s1,s1,2 /
#     addu s1,s1,a0` - literally 0x80010248 + index * 4;
#   * `town_receive`'s safe-storage scan already uses the same `lbu +1 == 0`
#     free test on the parallel array at 0x80011F80, and is live-validated.
#
# Byte +1 of a four-byte descriptor is its category, and category zero is the
# game's own definition of an empty slot.  0x80010298, immediately past the
# array, is the special-pickup descriptor; 0x8001029C is the order table.
INVENTORY_DESCRIPTOR_TABLE_ADDRESS = 0x8001_0248
INVENTORY_DESCRIPTOR_SLOT_COUNT = 20
INVENTORY_DESCRIPTOR_SIZE = 4
INVENTORY_DESCRIPTOR_CATEGORY_OFFSET = 1
INVENTORY_ORDER_SLOT_COUNT = 20
TOWER_ENTRY_ITEM_LIMIT = 5
WARP_ITEM_LIMIT_MESSAGE = "Take no more than 5 items."
WARP_DESTINATION_PROMPT = "Destination?"
WARP_NO_SHORTCUTS_MESSAGE = "No shortcuts unlocked."

ACTIVE_ACTOR_LIST_HEAD_ADDRESS = 0x8008_1498
SCENE_TRANSITION_ACTOR_UPDATE_ADDRESS = 0x800A_0708
START_NORMAL_TOWER_ENTRANCE_SEQUENCE_ADDRESS = 0x800A_0A88
TOWER_ENTRANCE_TRANSITION_DESCRIPTOR_ADDRESS = 0x800D_4404
PERSISTENT_TOWER_FLOOR_ADDRESS = 0x8001_0234
TOWER_FLOOR_MARKER = 0x8000
KEYCARD_LEVEL_ADDRESS = (
    patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_KEYCARD_LEVEL_OFFSET
)

# The floor-generation package exists twice in DUNGEON.BIN.  After the town
# transition has completed, its bootstrap at 0x800163f4 decides whether to
# hard-code floor 1 or restore a resumed run.  Preserve that decision.  On only
# the vanilla new-run branch, hook the LUI at 0x80016448 and let the resident
# helper consume bit 15 of Uncle's one-use persistent-floor request.  Unmarked
# normal entries and the original resume branch keep their vanilla behavior.
DUNGEON_FILE_START_LBA = 0x3016
FLOOR_GENERATION_FILE_OFFSETS = (0x28_D000, 0x50_8800)
TOWER_FLOOR_BOOTSTRAP_HOOK_OFFSET = 0x1_6448


def _overlay_runtime_to_file_offset(address: int) -> int:
    if not (
        OUTDOOR_OVERLAY_RUNTIME_ADDRESS
        <= address
        < OUTDOOR_STATIC_DATA_END_ADDRESS
    ):
        raise ValueError(f"Address 0x{address:08x} is outside the outdoor overlay.")
    return (
        OUTDOOR_OVERLAY_FILE_OFFSET
        + address
        - OUTDOOR_OVERLAY_RUNTIME_ADDRESS
    )


def _dialogue_runtime_to_file_offset(address: int) -> int:
    if not (
        OUTDOOR_DIALOGUE_RUNTIME_ADDRESS
        <= address
        < UNCLE_RESOURCE_END_ADDRESS
    ):
        raise ValueError(f"Address 0x{address:08x} is outside the outdoor dialogue resource.")
    return (
        UNCLE_DIALOGUE_FILE_OFFSET
        + address
        - OUTDOOR_DIALOGUE_RUNTIME_ADDRESS
    )


def _build_level_check(required_level: int) -> bytes:
    """Return one when the current Progressive Keycard level meets a threshold."""

    if required_level not in (2, 4, 6):
        raise ValueError("Town warp checkpoints require Keycard level 2, 4, or 6.")

    # Seeding v0 with required-1 turns the test into a single SLT and removes
    # both the load-delay NOP and the XORI the earlier form needed.  The SLT is
    # safe in the JR delay slot, and it is three instructions after the load.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x0F, 0, 8, town_shop._upper(KEYCARD_LEVEL_ADDRESS)),
        town_shop._i(0x23, 8, 8, town_shop._lower(KEYCARD_LEVEL_ADDRESS)),
        town_shop._i(0x09, 0, 2, required_level - 1),  # addiu v0,zero,required-1
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._r(2, 8, 2, 0, 0x2A),  # slt v0,v0,t0
    )
    return b.build()


def _build_item_limit_check() -> bytes:
    """Return one when Koh carries more items than the tower entrance allows.

    **Count the descriptors, not the order table.**  The order table at
    0x8001029C is display ordering: twenty *pointers* into the descriptor
    array, and it is not a reliable occupancy record.  It holds at least three
    kinds of junk, and each one broke a different build:

      * `0xFFFFFFFF` deletion markers.  A live capture with an *empty* bag
        showed seven consecutive ones with real pointers on both sides.
      * **stale pointers past the zero terminator.**  Each shop overlay
        compacts the table with its own routine - the Monster Shop's is at
        0x80016CA8, reached from its `process_selected_shop_items` at
        0x8001702C - and compaction packs live pointers toward the front and
        writes a terminator *without clearing the tail*.  Those leftovers are
        real, plausible, in-range pointers.  Nothing distinguishes them from
        live entries by value, which is why folding `-1` in was not enough and
        the Monster Shop reproduced the refusal all over again.
      * holes left by a partially completed compaction.

    The descriptor array has none of that.  It is the physical inventory,
    vanilla's own `find_unused_inventory_descriptor_index` calls a slot free
    exactly when its category byte is zero, and the shops go through the same
    allocator - so a freed slot is genuinely zeroed rather than merely
    unlinked.  Counting occupied categories is both what the five-item rule
    actually means and immune to whatever any shop leaves behind in the
    ordering.

    Walk all twenty and count: no terminator to trust, no ordering to trust.
    Eleven instructions, 44 bytes - four fewer than the order-table version,
    so the refusal page does not move.
    """

    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(
            0x0F, 0, 8, town_shop._upper(INVENTORY_DESCRIPTOR_TABLE_ADDRESS)
        ),
        town_shop._i(
            0x09, 8, 8, town_shop._lower(INVENTORY_DESCRIPTOR_TABLE_ADDRESS)
        ),
        # addiu t1,t0,80 - one past the last descriptor
        town_shop._i(
            0x09,
            8,
            9,
            INVENTORY_DESCRIPTOR_SLOT_COUNT * INVENTORY_DESCRIPTOR_SIZE,
        ),
        town_shop._i(0x09, 0, 2, -TOWER_ENTRY_ITEM_LIMIT),
    )
    b.label("count")
    b.emit(
        # lbu t2,1(t0) - the category byte
        town_shop._i(0x24, 8, 10, INVENTORY_DESCRIPTOR_CATEGORY_OFFSET),
        # addiu t0,t0,4 - R3000 load delay, filled with the step
        town_shop._i(0x09, 8, 8, INVENTORY_DESCRIPTOR_SIZE),
        town_shop._r(0, 10, 10, 0, 0x2B),  # sltu t2,zero,t2 - one when occupied
    )
    b.branch(0x05, 8, 9, "count")
    b.emit(
        town_shop._r(2, 10, 2, 0, 0x21),  # addu v0,v0,t2 - delay slot
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._r(0, 2, 2, 0, 0x2A),  # slt v0,zero,v0 - held > limit
    )
    return b.build()


def _build_uncle_dialogue_getter_return() -> bytes:
    """Redirect Uncle's existing LUI/ADDIU getter pair to the new script."""

    # 0x8001671c loads 0x80020000 into v0.  Preserve that instruction and
    # replace only the ADDIU at 0x80016724.
    return struct.pack(
        "<I",
        town_shop._i(
            0x09,
            2,
            2,
            UNCLE_REBUILT_SCRIPT_ADDRESS - 0x8002_0000,
        ),
    )


def _build_uncle_variant_guard_pointer() -> bytes:
    """Keep Uncle's visible scene variant eligible after tower progress."""

    return struct.pack("<I", UNCLE_ALWAYS_SELECTED_CALLBACK_ADDRESS)


def _build_warp_trigger() -> bytes:
    """The v114 deterministic tower warp: descriptor call, no actor scan.

    Contract unchanged from the V35 helper: a0 arrives as
    `TOWER_FLOOR_MARKER | (floor - 1)`, or zero when the callback's
    threshold recheck failed - zero is refused.  Stages the V38 marked
    floor in persistent `0x80010234` (the seed-page consumer at tower
    load is untouched), then calls
    `begin_scene_transition_from_descriptor(0x800D4404)` and
    `initialize_new_tower_run_state` - the exact order the native fade
    finale uses.  Every step is unconditional: there is no scan and no
    silent path.
    """

    b = town_shop._MipsBuilder()
    b.branch(0x05, 4, 0, "go")                       # bne a0,zero,go
    b.emit(0)
    b.emit(town_shop._r(31, 0, 0, 0, 0x08), 0)       # refused: jr ra
    b.label("go")
    b.emit(
        town_shop._i(0x09, 29, 29, -0x18),           # addiu sp,sp,-0x18
        town_shop._i(0x2B, 29, 31, 0x10),            # sw ra,0x10(sp)
        town_shop._i(0x09, 4, 2, 1),                 # v0 = a0+1 = mark|floor
        town_shop._i(0x0F, 0, 8, town_shop._upper(PERSISTENT_TOWER_FLOOR_ADDRESS)),
        town_shop._i(0x29, 8, 2, town_shop._lower(PERSISTENT_TOWER_FLOOR_ADDRESS)),
    )
    b.emit(
        town_shop._i(0x0F, 0, 4, town_shop._upper(TOWER_ROAD_TRANSITION_DESCRIPTOR_ADDRESS)),
        town_shop._i(0x09, 4, 4, town_shop._lower(TOWER_ROAD_TRANSITION_DESCRIPTOR_ADDRESS)),
        town_shop._j(0x03, BEGIN_SCENE_TRANSITION_FROM_DESCRIPTOR_ADDRESS),
        0,
        town_shop._j(0x03, INITIALIZE_NEW_TOWER_RUN_STATE_ADDRESS),
        0,
        town_shop._i(0x23, 29, 31, 0x10),            # lw ra,0x10(sp)
        0,
        town_shop._r(31, 0, 0, 0, 0x08),             # jr ra
        town_shop._i(0x09, 29, 29, 0x18),            # (delay) sp restore
    )
    return b.build()


# --- resuming a tower run through the greeting's own scene transition --------
#
# `Continue` -> the angel says "Welcome back, <name>" -> confirming it runs
# `script_call(ANGEL_SCENE_CALL_RUNTIME_ADDRESS)`, which sets script slot 0 and
# calls FNO 0x80. The live town table maps that to TOWN_SCENE_FNO_HANDLER
# below, which indexes the scene descriptor table at 0x800D4758 and calls
# `begin_scene_transition_from_descriptor` - the SAME function Uncle's trigger
# calls, reached the same way. So a resume is not a new mechanism: it is that
# one transition going to the tower instead of the town.
#
# The wrapper diverts only when a floor request is pending and tail-calls the
# vanilla handler otherwise, so every other scene change in town is untouched.
#
# HOME: 0x8004EE90-0x8004EEBB, 44 bytes - the retired card driver's tail
# ("wakes the parent actor") in SLUS_006.14, resident from boot and never
# covered by an overlay load.
#
# **Residency is the requirement, and it is a different question from
# reachability.** The first attempt put this in the retired card-SCREEN span at
# 0x80021160, which is unreachable code (an execution breakpoint on
# `create_memory_card_screen_actor` held through a full session never fired) -
# but that span lives in the FRONT-MENU package, and in town its addresses hold
# the outdoor town overlay (OUTDOOR_OVERLAY_FILE_OFFSET at the top of this
# module: TOWN.BIN+0x2AB800 -> 0x80016000). The trampoline jumped into live town
# code and the game died at the angel's "On your way!". Every scan asked whether
# the bytes were dead; none asked whether they were THERE.
#
# This home answers both: the tail is part of the same never-entered driver the
# breakpoint cleared, and SLUS is resident always. Measured present-but-dead in
# all 55 town dumps in the workspace.
#
# 44 bytes is enough only because the stub does not do the work. Uncle's warp
# trigger already stages the marked floor, calls
# begin_scene_transition_from_descriptor and initialize_new_tower_run_state, and
# returns - and it lives in the outdoor dialogue resource, which IS town
# resident, and has been proven in play on floors 10/20/30. The stub tests the
# request and tail-jumps to it.
# The card-load resume, lifted from the memory-card module's own post-load
# routine at 0x800251A4 (disassembled from extracted/MAIN.BIN, 2026-08-11).
# After `load_save(slot, 0x80010000, 1)` succeeds - a 0xC0-frame, 0x6000-byte
# read straight into the save block - that routine does exactly four things
# besides tearing down its own screen:
#
#   [0x80013714] = 3            tower floor-state flags
#   [0x80082E76] |= 2           transition byte
#   jal 0x800439F8              SLUS; self-gating, a no-op unless its own flag
#   jal 0x80040AA0(5 or 6)      request_game_mode_overlay
#
# **The mode is 6 when [0x80010208] is ZERO and 5 when it is NONZERO** - read
# the delay slot, not the source order:
#
#   80025204  beq   v0,zero,0x80025210
#   80025208  addiu a0,zero,6            <- DELAY SLOT, always executes
#   8002520c  addiu a0,zero,5            <- fall-through only, i.e. flag != 0
#   80025210  jal   0x80040AA0
#
# m6 requested 6 on the reasoning that 6 was the tower, and the ride landed the
# player next to Mom - the IN-TOWN save spot. So **5 is the tower and 6 is the
# town**, confirmed in play 2026-08-11. `0x80010208` is the saved-in-tower flag
# (the slot-summary builder at 0x80025F84 branches on it to choose between two
# description strings), and the branch selects the OPPOSITE-looking constant.
#
# This is the whole tower resume, and it is why vanilla's elevator `QUIT?` save
# could put you back mid-climb. Everything it needs - floor, inventory, stats,
# familiar - comes from the restored block, which is exactly what the client's
# checkpoint already is. The two calls we omit (0x80027BF4, 0x80025D34) are
# card-screen teardown, and there is no screen here.
RESUME_TOWER_FLOOR_STATE_ADDRESS = 0x8001_3714
RESUME_TOWER_FLOOR_STATE_VALUE = 3
RESUME_TRANSITION_BYTE_ADDRESS = 0x8008_2E76
RESUME_TRANSITION_BIT = 2
RESUME_COMMIT_PENDING_ADDRESS = 0x8004_39F8      # SLUS
REQUEST_GAME_MODE_OVERLAY_ADDRESS = 0x8004_0AA0  # SLUS
RESUME_TOWER_GAME_MODE = 5
RESUME_TOWN_GAME_MODE = 6
SAVED_IN_TOWER_FLAG_ADDRESS = 0x8001_0208

RESUME_WARP_STUB_ADDRESS = 0x8004_EE50
RESUME_WARP_STUB_END_ADDRESS = 0x8004_EEBC
TOWN_SCENE_FNO_HANDLER_ADDRESS = 0x800C_24C8
# Derived, not assumed: 48 live bytes from 0x800C24C8 in a town dump occur
# exactly ONCE in TOWN.BIN, at +0x44D68, and once in
# extracted/TOWN_MODE_OVERLAY_80088760.bin at +0x39D68 - and the two agree
# (0x44D68 - 0x39D68 = 0xB000). Cross-checked semantically: those bytes are a
# prologue followed by `lui v0,0x800d` / `addiu v0,v0,0x4758`, the scene
# descriptor table this handler is documented to index. Recorded in
# docs/systems/save-removal-and-intro.md.
TOWN_MODE_OVERLAY_FILE_BASE = 0xB000
TOWN_MODE_OVERLAY_RUNTIME_BASE = 0x8008_8760
# The two words the trampoline displaces, replayed by the wrapper's vanilla
# path. Both are ordinary and side-effect-free to repeat.
TOWN_SCENE_FNO_DISPLACED_WORDS = (
    town_shop._i(0x09, 29, 29, -0x18),                     # addiu sp,sp,-0x18
    town_shop._i(0x0F, 0, 2, 0x800D),                      # lui v0,0x800d
)


def town_mode_overlay_file_offset(address: int) -> int:
    """Runtime address in the town-mode gameplay overlay -> TOWN.BIN offset."""

    return (
        address - TOWN_MODE_OVERLAY_RUNTIME_BASE + TOWN_MODE_OVERLAY_FILE_BASE
    )


def build_town_scene_fno_trampoline() -> bytes:
    """`j stub` + `nop` over the handler's first two words.

    Two words, not one: a `j` runs its delay slot, so leaving the handler's
    second instruction in place would execute it before the stub started. The
    stub replays both on its not-pending path.
    """

    return struct.pack("<2I", town_shop._j(0x02, RESUME_WARP_STUB_ADDRESS), 0)


def _build_resume_warp_stub() -> bytes:
    """Resume a saved tower run by doing what the CARD LOAD does.

    Four earlier versions re-implemented the entry by hand - stage a marked
    floor, start a scene transition, initialise a new run - and each one failed
    on something the game already knew: residency of the body (m2), the
    handler's argument register (m3), residency of the callee (m4), and then an
    extent for Koh's stats that was guessed from where a mirror stopped
    agreeing rather than from what the game saves (m5, which reached the floor
    but with the intro's Pita for an inventory and wrong maximums).

    So this stops re-implementing. Vanilla's elevator `QUIT?` saved mid-tower
    and `Continue` resumed it, and the post-load routine at 0x800251A4 is that
    resume: set the tower floor-state flags, set the transition bit, call one
    self-gating SLUS helper, and request game-mode overlay 6. Everything else -
    floor, inventory, stats, familiar, maximums - comes out of the 0x6000 bytes
    at 0x80010000, which is precisely what the client's checkpoint restores.

    The not-pending path is unchanged: replay the two words the trampoline
    displaced and rejoin the vanilla handler with `a0` untouched.
    """

    request_offset = (
        patch.TOWER_RESUME_CARRIER_ADDRESS
        + patch.TOWER_RESUME_FLOOR_REQUEST_OFFSET
        - 0x8001_0000
    )
    floor_state_offset = RESUME_TOWER_FLOOR_STATE_ADDRESS - 0x8001_0000
    for name, offset in (
        ("carrier", request_offset),
        ("floor-state flags", floor_state_offset),
    ):
        if not 0 <= offset < 0x8000:
            raise ValueError(f"The {name} is out of `lui t0,0x8001` reach.")

    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x0F, 0, 8, 0x8001),                  # lui t0,0x8001
        town_shop._i(0x23, 8, 9, request_offset),          # lw t1,(request)
        town_shop._i(0x2B, 8, 0, request_offset),          # sw zero,(request)
    )
    b.branch(0x05, 9, 0, "pending")                        # bne t1,zero
    b.emit(
        0,
        *TOWN_SCENE_FNO_DISPLACED_WORDS,
        town_shop._j(0x02, TOWN_SCENE_FNO_HANDLER_ADDRESS + 8),
        0,
    )
    b.label("pending")
    b.emit(
        # [0x80013714] = 3
        town_shop._i(0x09, 0, 9, RESUME_TOWER_FLOOR_STATE_VALUE),
        town_shop._i(0x29, 8, 9, floor_state_offset),
        # [0x80082E76] |= 2
        town_shop._i(0x0F, 0, 10, 0x8008),
        town_shop._i(0x25, 10, 11, RESUME_TRANSITION_BYTE_ADDRESS & 0xFFFF),
        0,                                                 # load delay
        town_shop._i(0x0D, 11, 11, RESUME_TRANSITION_BIT),
        town_shop._i(0x29, 10, 11, RESUME_TRANSITION_BYTE_ADDRESS & 0xFFFF),
        # the two resident calls, in the load path's order
        town_shop._i(0x09, 29, 29, -0x18),
        town_shop._i(0x2B, 29, 31, 0x10),
        town_shop._j(0x03, RESUME_COMMIT_PENDING_ADDRESS),
        0,
        town_shop._j(0x03, REQUEST_GAME_MODE_OVERLAY_ADDRESS),
        town_shop._i(0x09, 0, 4, RESUME_TOWER_GAME_MODE),   # (delay) a0 = 5
        town_shop._i(0x23, 29, 31, 0x10),
        0,
        town_shop._r(31, 0, 0, 0, 0x08),                   # jr ra
        town_shop._i(0x09, 29, 29, 0x18),
    )
    code = b.build()
    capacity = RESUME_WARP_STUB_END_ADDRESS - RESUME_WARP_STUB_ADDRESS
    if len(code) > capacity:
        raise ValueError(
            f"The resume stub is {len(code)} bytes but only {capacity} are free "
            f"at 0x{RESUME_WARP_STUB_ADDRESS:08x}."
        )
    return code


def iter_resume_warp_slus_patches() -> tuple[tuple[int, bytes], ...]:
    """The stub, as a SLUS_006.14 file patch.

    It occupies the retired card driver's two disabled save entries AND its
    tail - 108 contiguous bytes. save_removal no longer writes its "already
    done" stubs over the entries: those were belt and braces for routes that do
    not exist. An entry's only job is to call create_memory_card_screen_actor,
    and an execution breakpoint on that, held through a full session, never
    fired - so anything that could reach a stub could reach the actor.
    """

    from . import save_removal

    return (
        (
            save_removal.slus_runtime_to_file_offset(RESUME_WARP_STUB_ADDRESS),
            _build_resume_warp_stub(),
        ),
    )


def iter_resume_warp_stream_patches() -> tuple[tuple[int, bytes], ...]:
    """No front-menu package edits. Kept as the record of a wrong turn.

    The resume wrapper was briefly hosted in the retired card-screen span at
    0x80021160, inside this package. The span is genuinely unreachable code -
    but the package is NOT resident in town, where the angel hands over, and
    that address range holds the outdoor town overlay instead. The trampoline
    jumped into live town code and the game died at "On your way!".

    `tools/Rebuild-AdapFrontMenuStreams.py` can still address stream 2 (its
    parameters are measured and its round trip verified), so the capability
    survives even though nothing uses it today.
    """

    return ()


def _build_floor_warp(
    required_level: int,
    floor: int,
    target: int = UNCLE_WARP_TRIGGER_ADDRESS,
) -> bytes:
    """Tail-call the warp trigger with an encoded start floor."""

    # The threshold recheck is a mask rather than a branch: a failing level
    # clears a0 to zero and the helper, which now rejects a zero request,
    # returns without staging anything.  That removes the private return stub
    # each callback used to carry, and 32 bytes times three is what lets the
    # inventory-limit check and its refusal page share the retired body.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x0F, 0, 8, town_shop._upper(KEYCARD_LEVEL_ADDRESS)),
        town_shop._i(0x23, 8, 9, town_shop._lower(KEYCARD_LEVEL_ADDRESS)),
        # R3000 load delay, and the request the helper consumes.
        town_shop._i(0x09, 0, 4, TOWER_FLOOR_MARKER | (floor - 1)),
        town_shop._i(0x0B, 9, 10, required_level),  # sltiu t2,t1,required
        town_shop._i(0x09, 10, 10, 0xFFFF),  # addiu t2,t2,-1
        town_shop._r(4, 10, 4, 0, 0x24),  # and a0,a0,t2
        town_shop._j(0x02, target),
        0,
    )
    return b.build()


def _build_activate_tower_entrance() -> bytes:
    """Retarget the active town transition actor and tail-call native entry."""

    # Near Uncle the outdoor scene has one normal scene-transition actor, but
    # its +0x68 descriptor is a local-town destination rather than 0x800d4404.
    # The native finder therefore returns null.  Scan the ordinary actor list
    # for its 0x800a0708 update routine, replace only that descriptor, stage
    # marker|floor in the save-backed tower carrier, then tail-call the
    # unmodified native entrance starter.  The floor overlay consumes and
    # clears that marker immediately before its hardcoded floor-1 store.  A null
    # list read in BEQ's delay slot accesses harmless RAM address 0x10; the
    # missing path returns without staging the carrier.
    b = town_shop._MipsBuilder()
    # A cleared request means a destination callback failed its own Keycard
    # recheck.  Reject it here so the callbacks do not each need a return stub.
    b.branch(0x04, 4, 0, "missing")
    b.emit(
        town_shop._r(4, 0, 7, 0, 0x21),  # move a3,a0 - delay slot
        town_shop._i(
            0x0F,
            0,
            3,
            (ACTIVE_ACTOR_LIST_HEAD_ADDRESS >> 16) & 0xFFFF,
        ),
        town_shop._i(
            0x23,
            3,
            2,
            ACTIVE_ACTOR_LIST_HEAD_ADDRESS & 0xFFFF,
        ),
    )
    town_shop._load_address(b, 4, SCENE_TRANSITION_ACTOR_UPDATE_ADDRESS)

    b.label("loop")
    b.branch(0x04, 2, 0, "missing")
    b.emit(town_shop._i(0x23, 2, 3, 0x10))  # delay slot; lw v1,actor+0x10
    b.emit(
        town_shop._i(
            0x0F,
            0,
            5,
            (TOWER_ENTRANCE_TRANSITION_DESCRIPTOR_ADDRESS >> 16) & 0xFFFF,
        )
    )  # load-delay separator
    b.branch(0x05, 3, 4, "next")
    b.emit(
        town_shop._i(
            0x09,
            5,
            5,
            TOWER_ENTRANCE_TRANSITION_DESCRIPTOR_ADDRESS & 0xFFFF,
        ),
        town_shop._i(0x2B, 2, 5, 0x68),
        town_shop._i(0x09, 7, 6, 1),  # a2=actual destination floor
        town_shop._i(
            0x0F,
            0,
            3,
            (PERSISTENT_TOWER_FLOOR_ADDRESS >> 16) & 0xFFFF,
        ),
        town_shop._i(
            0x29,
            3,
            6,
            PERSISTENT_TOWER_FLOOR_ADDRESS & 0xFFFF,
        ),
        town_shop._j(0x02, START_NORMAL_TOWER_ENTRANCE_SEQUENCE_ADDRESS),
        0,
    )

    b.label("next")
    b.emit(town_shop._i(0x23, 2, 2, 0))
    b.branch(0x04, 0, 0, "loop")
    b.emit(0)

    b.label("missing")
    b.emit(
        town_shop._r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _build_warp_script(
    script_address: int = WARP_SCRIPT_ADDRESS,
    limit_address: int = WARP_SCRIPT_LIMIT_ADDRESS,
    floor_callbacks: tuple[int, int, int] | None = None,
) -> bytes:
    script = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []

    def emit(*values: int) -> None:
        script.extend(value & 0xFF for value in values)

    def emit_address(address: int) -> None:
        script.extend(struct.pack("<I", address))

    def emit_label_address(name: str) -> None:
        fixups.append((len(script), name))
        script.extend(bytes(4))

    def label(name: str) -> None:
        if name in labels:
            raise ValueError(f"Duplicate town-warp dialogue label: {name}")
        labels[name] = len(script)

    def emit_text(text: str) -> None:
        script.extend(town_shop._encode_shop_name(text, max_characters=None)[:-1])

    def emit_native_call(address: int) -> None:
        emit(0x4C)
        emit_address(address)

    def emit_choice(text: str, *, row_start: bool) -> None:
        if row_start:
            emit(0x0B)
        emit(0x81, 0x6D)
        emit_text(text)
        emit(0x81, 0x6E)

    def emit_gap(after_text: str) -> None:
        # Native two-column menus pad column 0 plus the gap to a fixed 16
        # cells - `[Yes.]` gets ten spaces, `[I'll buy.]` five (measured
        # from the vanilla shop menus) - and the selection highlight
        # covers that fixed region.  A hardcoded 4 is right only for a
        # 12-cell column 0, which is why `[Floor 10]` bled highlight into
        # the next bracket.
        cells = len(
            town_shop._encode_shop_name(after_text, max_characters=None)[:-1]
        ) // 2 + 2  # brackets
        emit(*(0x81, 0x40) * max(16 - cells, 2))

    # Opcode 0x3f/0x0f branches when the preceding native callback returned
    # true.  Ask "is anything unlocked at all?" first so the refusal pages
    # print on their own, then print the prompt once and let the remaining two
    # thresholds pick a menu.  The prompt is ordinary page text followed by a
    # row break, so it shares the page with the bracketed choices; V32 removed
    # three separate prompt pages, which is what made the old form expensive.
    emit_native_call(WARP_LEVEL_2_CHECK_ADDRESS)
    emit(0x3F, 0x0F)
    emit_label_address("unlocked")
    emit_text(WARP_NO_SHORTCUTS_MESSAGE)
    emit(0x11, 0x01)

    label("unlocked")
    emit_native_call(WARP_ITEM_LIMIT_CHECK_ADDRESS)
    emit(0x3F, 0x0F)
    emit_address(WARP_ITEM_LIMIT_PAGE_ADDRESS)
    emit_text(WARP_DESTINATION_PROMPT)
    emit(0x0A)
    emit_native_call(WARP_LEVEL_6_CHECK_ADDRESS)
    emit(0x3F, 0x0F)
    emit_label_address("menu_30")
    emit_native_call(WARP_LEVEL_4_CHECK_ADDRESS)
    emit(0x3F, 0x0F)
    emit_label_address("menu_20")

    label("menu_10")
    emit_choice("Floor 10", row_start=True)
    emit_gap("Floor 10")
    emit_choice("Cancel", row_start=False)
    emit(0x2C, 0x02, 0x1A)
    emit_label_address("floor_10")
    emit_label_address("cancel")

    label("menu_20")
    emit_choice("Floor 10", row_start=True)
    emit_gap("Floor 10")
    emit_choice("Floor 20", row_start=False)
    emit(0x0A)
    emit_choice("Cancel", row_start=True)
    emit(0x2C, 0x03, 0x1A)
    emit_label_address("floor_10")
    emit_label_address("floor_20")
    emit_label_address("cancel")

    label("menu_30")
    emit_choice("Floor 10", row_start=True)
    emit_gap("Floor 10")
    emit_choice("Floor 20", row_start=False)
    emit(0x0A)
    emit_choice("Floor 30", row_start=True)
    emit_gap("Floor 30")
    emit_choice("Cancel", row_start=False)
    emit(0x2C, 0x04, 0x1A)
    emit_label_address("floor_10")
    emit_label_address("floor_20")
    emit_label_address("floor_30")
    emit_label_address("cancel")

    if floor_callbacks is not None:
        # v114: each pick calls its rebuilt callback (threshold recheck +
        # the deterministic warp trigger) and ends the conversation; the
        # transition is already underway when the 01 runs.
        for name, callback in zip(
            ("floor_10", "floor_20", "floor_30"), floor_callbacks
        ):
            label(name)
            emit_native_call(callback)
            emit(0x01)
        label("cancel")
        emit(0x01)
    else:
        # No callbacks (v110..v113): floor picks end silently.
        label("floor_10")
        label("floor_20")
        label("floor_30")
        label("cancel")
        emit(0x01)

    for offset, name in fixups:
        if name not in labels:
            raise ValueError(f"Unknown town-warp dialogue label: {name}")
        struct.pack_into("<I", script, offset, script_address + labels[name])

    if script_address + len(script) > limit_address:
        raise ValueError("Town-warp dialogue exceeds its audited allocation.")
    return bytes(script)


def _build_item_limit_page() -> bytes:
    """The refusal page, kept in the retired body so the script has room."""

    page = bytearray(
        town_shop._encode_shop_name(WARP_ITEM_LIMIT_MESSAGE, max_characters=None)[:-1]
    )
    page.extend((0x11, 0x01))
    return bytes(page)


def iter_town_warp_file_patches() -> tuple[tuple[int, bytes], ...]:
    # The resume trampoline over the town scene-transition FNO handler; its
    # body is a SLUS patch (iter_resume_warp_slus_patches).
    scene_trampoline = (
        town_mode_overlay_file_offset(TOWN_SCENE_FNO_HANDLER_ADDRESS),
        build_town_scene_fno_trampoline(),
    )
    level_2 = _build_level_check(2)
    level_4 = _build_level_check(4)
    level_6 = _build_level_check(6)
    item_limit = _build_item_limit_check()
    item_limit_page = _build_item_limit_page()
    # v110: the old script, the floor-warp callbacks (the flaky
    # vanilla-activation-helper path), and the activate-entrance helper
    # are NOT emitted any more - the rebuilt script never calls them, and
    # not writing the zero-gap region leaves the Kewne-sensitive routine
    # and its delay slot in untouched vanilla bytes.  The builders remain
    # for the tower-load rebuild to reference.
    warp_trigger = _build_warp_trigger()
    rebuilt_callbacks = tuple(
        _build_floor_warp(level, floor)
        for level, floor in ((2, 10), (4, 20), (6, 30))
    )
    callback_addresses = tuple(
        UNCLE_REBUILT_FLOOR_CB_ADDRESS + UNCLE_REBUILT_FLOOR_CB_STRIDE * i
        for i in range(3)
    )
    rebuilt_script = _build_warp_script(
        UNCLE_REBUILT_SCRIPT_ADDRESS,
        UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
        floor_callbacks=callback_addresses,
    )
    getter_return = _build_uncle_dialogue_getter_return()
    variant_guard = _build_uncle_variant_guard_pointer()

    spans = (
        (
            UNCLE_REBUILT_SCRIPT_ADDRESS,
            rebuilt_script,
            UNCLE_REBUILT_SCRIPT_LIMIT_ADDRESS,
        ),
        (
            UNCLE_WARP_TRIGGER_ADDRESS,
            warp_trigger,
            UNCLE_REBUILT_FLOOR_CB_ADDRESS,
        ),
        *(
            (
                callback_addresses[i],
                rebuilt_callbacks[i],
                UNCLE_REBUILT_MACHINERY_LIMIT_ADDRESS
                if i == 2
                else callback_addresses[i + 1],
            )
            for i in range(3)
        ),
        (WARP_LEVEL_2_CHECK_ADDRESS, level_2, WARP_LEVEL_4_CHECK_ADDRESS),
        (WARP_LEVEL_4_CHECK_ADDRESS, level_4, WARP_LEVEL_6_CHECK_ADDRESS),
        (
            WARP_LEVEL_6_CHECK_ADDRESS,
            level_6,
            WARP_ITEM_LIMIT_CHECK_ADDRESS,
        ),
        (
            WARP_ITEM_LIMIT_CHECK_ADDRESS,
            item_limit,
            WARP_ITEM_LIMIT_PAGE_ADDRESS,
        ),
        (
            WARP_ITEM_LIMIT_PAGE_ADDRESS,
            item_limit_page,
            UNCLE_RETIRED_SCRIPT_END_ADDRESS,
        ),
    )
    for address, data, limit in spans:
        if address + len(data) > limit:
            raise ValueError(
                f"Town-warp payload at 0x{address:08x} overlaps its next allocation."
            )

    return (
        scene_trampoline,
        (
            _overlay_runtime_to_file_offset(
                UNCLE_SELECTION_FUNCTION_POINTER_ADDRESS
            ),
            variant_guard,
        ),
        (
            _overlay_runtime_to_file_offset(
                UNCLE_DIALOGUE_GETTER_RETURN_INSTRUCTION_ADDRESS
            ),
            getter_return,
        ),
        (
            _overlay_runtime_to_file_offset(UNCLE_PLACEMENT_COORDINATE_ADDRESS),
            struct.pack("<2H", *UNCLE_RELOCATED_COORDINATES),
        ),
        (
            _overlay_runtime_to_file_offset(NADA_PLACEMENT_COORDINATE_ADDRESS),
            struct.pack("<2H", *NADA_RELOCATED_COORDINATES),
        ),
        (
            _dialogue_runtime_to_file_offset(WARP_LEVEL_2_CHECK_ADDRESS),
            level_2,
        ),
        (
            _dialogue_runtime_to_file_offset(WARP_LEVEL_4_CHECK_ADDRESS),
            level_4,
        ),
        (
            _dialogue_runtime_to_file_offset(WARP_LEVEL_6_CHECK_ADDRESS),
            level_6,
        ),
        (
            _dialogue_runtime_to_file_offset(WARP_ITEM_LIMIT_CHECK_ADDRESS),
            item_limit,
        ),
        (
            _dialogue_runtime_to_file_offset(WARP_ITEM_LIMIT_PAGE_ADDRESS),
            item_limit_page,
        ),
        (
            _dialogue_runtime_to_file_offset(UNCLE_REBUILT_SCRIPT_ADDRESS),
            rebuilt_script,
        ),
        (
            _dialogue_runtime_to_file_offset(UNCLE_WARP_TRIGGER_ADDRESS),
            warp_trigger,
        ),
        *(
            (
                _dialogue_runtime_to_file_offset(callback_addresses[i]),
                rebuilt_callbacks[i],
            )
            for i in range(3)
        ),
    )


def iter_tower_floor_bootstrap_file_patches() -> tuple[tuple[int, bytes], ...]:
    """Route only the new-run branch through the one-use marker helper."""

    hook = struct.pack(
        "<I",
        town_shop._j(0x02, patch.TOWER_FLOOR_BOOTSTRAP_HELPER_ADDRESS),
    )
    return tuple(
        (
            copy_offset + TOWER_FLOOR_BOOTSTRAP_HOOK_OFFSET,
            hook,
        )
        for copy_offset in FLOOR_GENERATION_FILE_OFFSETS
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


def iter_town_warp_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        *_iter_mode2_raw_patches(
            town_shop.TOWN_FILE_START_LBA,
            iter_town_warp_file_patches(),
        ),
        *_iter_mode2_raw_patches(
            DUNGEON_FILE_START_LBA,
            iter_tower_floor_bootstrap_file_patches(),
        ),
    )


def append_town_warp_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_town_warp_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
