"""Retire the memory card.

New Game is ADAP's only load path - the client restores a checkpoint at the
angel - so the game's own save is not merely redundant, it is wrong: a card
save records a moment the checkpoint system is authoritative over and will
immediately contradict.

Three routes reach the card, one per public entry of the resident driver:

| Route | Caller | Driver entry |
| --- | --- | --- |
| Title screen `Continue` | `0x80088940` | `0x8004ee30` |
| Town save, Mom's `SAVE DATA` | `0x800c1b08` | `0x8004ee50` |
| Tower elevator `QUIT?` -> yes | `0x800211d4` | `0x8004ee70` |

`Continue` is removed from the menu outright, because a load that succeeds is
worse than one that never starts and a visible option implies it works.  The
two save routes are disabled at the driver instead of at their callers.  That is not just tidier - the elevator caller lives
inside an LZ-compressed stream, and patching it would mean re-encoding stream 2
together with the AP HUD payload it already carries.  The driver is plain
resident code in `SLUS_006.14`.

See `docs/save-system-removal.md` for how each address was established.
"""

from __future__ import annotations

import struct

from . import town_shop


# MAIN.BIN's first sector is LBA 1817; the title module is 68 sectors in, at
# MAIN.BIN+0x21800 (LBA 1884), and loads to 0x80088760.
MAIN_FILE_START_LBA = 1_817
TITLE_MODULE_FILE_OFFSET = 0x2_1800
TITLE_MODULE_RUNTIME_ADDRESS = 0x8008_8760
TITLE_MODULE_SIZE = 14 * 2_048

# The resident driver in SLUS_006.14.  Each public entry is eight words that
# call create_memory_card_screen_actor with a mode selector.
SLUS_FILE_START_LBA = 24
SLUS_LOAD_ADDRESS = 0x8002_D000
SLUS_HEADER_SIZE = 0x800
CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS = 0x8004_EE50
CARD_DRIVER_TOWER_SAVE_ENTRY_ADDRESS = 0x8004_EE70
CARD_DRIVER_ENTRY_SIZE = 0x20
CARD_SCREEN_DONE_FLAG_OFFSET = 0x1E
CARD_SCREEN_DONE_BIT = 0x2000

# --- row 0 reads `Continue` when the client says a checkpoint exists ---------
#
# New Game *is* the load path, so the row always dispatches to New Game; only
# the label changes.  The `CONTINUE` graphic is still on the disc and still
# loaded, because only the row was removed.
#
# **The client cannot write the label word itself.**  A live trace
# (`tools/pcsx-redux/trace-title-module.lua`, 2026-07-26) showed nothing at all
# between the title module's CD read and the constructor consuming the
# descriptors - no other read, no mode load - and the traced breakpoint fires
# when the read is *issued*, so the real gap is shorter still.  A 16 ms poll
# cannot be relied on to land in it.
#
# So the client writes one byte to resident RAM instead, at any time and as
# often as it likes, and the ROM reads it during construction.  The byte lives
# in the retired card driver: `SLUS_006.14`, resident from boot, and never
# covered by an overlay load - unlike everything at 0x80088760.
#
# Generation zeroes the flag, so a disc with no client shows `NEW GAME`, and a
# reset reloads SLUS and clears it, which is the same correct default.
TITLE_CONTINUE_FLAG_ADDRESS = 0x8004_EB3C
TITLE_LABEL_HELPER_ADDRESS = 0x8004_EB40
TITLE_STAGE_NEW_GAME_LABEL_ADDRESS = 0x8008_8C18
TITLE_STAGE_RESUME_ADDRESS = 0x8008_8C20
TITLE_CONTINUE_LABEL_DESCRIPTOR = 0x8008_B170
TITLE_STAGE_NEW_GAME_LABEL_ORIGINAL = 0xAFA4_0014  # sw a0,0x14(sp)
# The retired driver runs to 0x8004eebb; the two disabled save entries start at
# 0x8004ee50, so everything below that is free.
RETIRED_CARD_DRIVER_END_ADDRESS = CARD_DRIVER_TOWN_SAVE_ENTRY_ADDRESS

# The title screen is a state machine at 0x800890ec, `lh v1,0x20(s0)` selecting
# the handler and `0x22(s0)` counting down to the attract mode.  State 4 is
# browsing: it reads the pad, moves the cursor at `0x24(s0)`, and on a confirm
# press plays a sound, sets a 0x60 frame delay and moves to state 0x20.  State
# 0x20 waits out the delay and dispatches on the index.
#
# **State 0xff hangs the machine.**  V67 disabled Continue by jumping the
# index-0 branch to the dispatcher's own allocation-failure exit, which sets
# `0xff` into `0x20(s0)`.  That is the next *state*, not a result, and
# `0x800891b4` reads `slti v0,v1,0xff` / `beq v0,zero,return` - every state at
# or above `0xff` returns unhandled, forever.  Vanilla cannot produce an
# out-of-range index, so it is unreachable dead code, which is why it looked
# like a safe landing.  Nothing below may write `0xff` to `0x20(s0)`.
#
# The menu is three rows built by one loop at 0x80088c7c, `s2` counting down
# from 2.  Each row takes its label from `[sp + 0x10 + s2 * 4]`, staged at the
# top of the constructor from three descriptors at 0x80088760; its Y from `s6`,
# starting at 200 and stepping -16; and its entry-animation delay from `s7`,
# starting at 0x3c and stepping -0x1e.  Row index is the dispatch index, and
# the cursor sprite puts itself at `index * 16 + 0xa8` (0x80089b44), so row 0 is
# the top one at Y=168 - Continue.
#
# Removing it is therefore: build two rows instead of three, start them one
# label later so they are New Game and Options, start Y and the stagger one step
# in so they land where rows 0 and 1 always did, and remap the dispatcher.  The
# cursor formula already agrees with the new positions and needs no change.
TITLE_MENU_STATE_OBJECT_INDEX_OFFSET = 0x24
TITLE_ROW_HEIGHT = 16
TITLE_FIRST_ROW_Y = 0xA8
TITLE_LABEL_DESCRIPTOR_TABLE_ADDRESS = 0x8008_8760
TITLE_NEW_GAME_BRANCH_ADDRESS = 0x8008_94D8
TITLE_OPTIONS_BRANCH_ADDRESS = 0x8008_9508
TITLE_BROWSING_STATE = 0x04
TITLE_DISPATCH_EXIT_ADDRESS = 0x8008_95F0
TITLE_CURSOR_MOVE_APPLY_ADDRESS = 0x8008_9344

# Every title-module word this patch touches, with the untouched original so a
# wrong address fails the build rather than shipping. `None` means "leave it to
# the builder", used where the replacement is computed.
TITLE_PATCH_SITES = (
    (0x8008_8C7C, 0x2412_0002, "rows: s2 = 2 -> 1"),
    (0x8008_8C80, 0x2417_003C, "stagger: s7 = 0x3c -> 0x1e"),
    (0x8008_8C84, 0x2416_00C8, "first row Y: s6 = 200 -> 184"),
    (0x8008_8CFC, 0x8C42_0010, "label: [sp+0x10+s4] -> [sp+0x14+s4]"),
    (0x8008_919C, 0x2403_0002, "state 0x13 fade count: 3 rows -> 2"),
    (0x8008_9328, 0x2484_0002, "cursor down: +2 (mod 3) -> +1"),
    (0x8008_9344, 0x0082_0018, "cursor wrap: mod 3 -> and 1"),
    (0x8008_9348, 0x0004_1FC3, "(mod 3 remainder)"),
    (0x8008_934C, 0x0000_4010, "(mod 3 remainder)"),
    (0x8008_9350, 0x0103_1823, "(mod 3 remainder)"),
    (0x8008_9354, 0x0003_1040, "(mod 3 remainder)"),
    (0x8008_9358, 0x0043_1021, "(mod 3 remainder)"),
    (0x8008_935C, 0x0082_2023, "(mod 3 remainder)"),
    (0x8008_93B0, 0x1C40_008F, "attract timeout never fires"),
    (0x8008_9480, 0x1062_0015, "dispatch: index 0 -> New Game"),
    (0x8008_948C, 0x2402_0002, "dispatch: compare against 1"),
    (0x8008_9490, 0x1060_0007, "dispatch: index 1 -> Options"),
    (0x8008_9494, 0x2402_00FF, "unreachable index -> browsing, not 0xff"),
    (0x8008_94A4, 0x2402_00FF, "unreachable index -> browsing, not 0xff"),
    (
        TITLE_STAGE_NEW_GAME_LABEL_ADDRESS,
        TITLE_STAGE_NEW_GAME_LABEL_ORIGINAL,
        "row 0's label goes through the Continue helper",
    ),
)

# V68 refused the press on row 0 instead of removing the row. Row 0 is New Game
# now, so the refusal has to go back to vanilla or New Game becomes unusable.
TITLE_V68_REVERTED_SITES = (
    (0x8008_9368, 0x0000_0000),  # nop - was `lh v1,0x24(s0)`
    (0x8008_9378, 0x8603_0024),  # lh v1,0x24(s0) - was the refusal branch
)


# --- the elevator's QUIT? prompt is never raised ------------------------------
#
# `update_normal_elevator_ascent` at 0x800930f0 is an eighteen-state machine,
# the state byte at `s3+0x9b` indexing a jump table at 0x80088a10.  Only five
# states do anything; the rest are the do-nothing tail at 0x80093414.
#
# | State | Handler | Role |
# | 0 | 0x80093144 | start the ascent |
# | 1 | 0x800931d8 | after 61 frames: save floor state, bump the floor, **request the prompt** |
# | 2 | 0x800932f4 | wait for 0x800a613c, stash its result at 0x800dcf64 |
# | 3 | 0x80093310 | call whatever is registered at 0x800e4938, keep the object at s3+0xc8 |
# | 4 | 0x80093348 | wait for bit 0x8000 at object+0x1e, then copy the floor and finish |
#
# State 1 ended by storing `create_quit_prompt` (0x80021268) into the generic
# builder slot 0x800e4938 and raising request bit 0x2000 at 0x800e296c.  States
# 2-4 are shared prompt machinery, so the removal belongs at the request.
#
# **State 1 now runs a verbatim copy of state 4's tail** - the eleven words at
# 0x80093360-0x80093388, which are exactly the size of the request block - and
# states 2, 3 and 4 are retargeted to the idle tail the table already uses for
# states 5-15 and 17.  State 1 still ends `j 0x80093404`, so the state byte
# advances to 2 and parks there; which of 2-5 it parks in does not matter once
# they all point at the same do-nothing handler.
#
# **Copying verbatim is the point.**  The first attempt hand-wrote the same work
# and filled the `jal 0x80040aa0` delay slot with `lui v1,0x8001` instead of
# vanilla's `nop`.  `v1` is caller-saved, so the call clobbered it and the
# following `lhu v1,0x234(v1)` read from a garbage base, writing nonsense into
# the active floor at 0x8008146c just before 0x800481e0 loaded it.  The
# persistent floor at 0x80010234 was already correct by then, so the crash
# landed after the client had seen the floor advance.  Vanilla puts that `lui`
# after the call deliberately; do not move a live value across a call to fill a
# delay slot.
ELEVATOR_PROMPT_REQUEST_ADDRESS = 0x8009_32C8
ELEVATOR_STATE_4_TAIL_ADDRESS = 0x8009_3360
ELEVATOR_IDLE_HANDLER_ADDRESS = 0x8009_3414
ELEVATOR_STATE_TABLE_ADDRESS = 0x8008_8A10
ELEVATOR_PROMPT_STATES = (2, 3, 4)
CREATE_QUIT_PROMPT_ADDRESS = 0x8002_1268
PROMPT_BUILDER_SLOT_ADDRESS = 0x800E_4938
PERSISTENT_FLOOR_ADDRESS = 0x8001_0234
ACTIVE_FLOOR_ADDRESS = 0x8008_146C

# DUNGEON.BIN's gameplay overlay is +0xa3000 loaded at 0x80088760; unlike the
# floor-generation package it exists once.
DUNGEON_FILE_START_LBA = 12_310
GAMEPLAY_OVERLAY_FILE_OFFSET = 0xA_3000
GAMEPLAY_OVERLAY_RUNTIME_ADDRESS = 0x8008_8760

# Byte-verified against the untouched disc; see the test.
ELEVATOR_PROMPT_REQUEST_ORIGINALS = (
    0x3C03_800E,  # lui   v1,0x800e
    0x3C02_8002,  # lui   v0,0x8002
    0x2442_1268,  # addiu v0,v0,0x1268   -> create_quit_prompt
    0x3C04_800E,  # lui   a0,0x800e
    0xAC62_4938,  # sw    v0,0x4938(v1)  -> register the builder
    0x8C82_296C,  # lw    v0,0x296c(a0)
    0x3C03_8008,  # lui   v1,0x8008
    0xAC60_2EB8,  # sw    zero,0x2eb8(v1)
    0x3442_2000,  # ori   v0,v0,0x2000   -> raise the request
    0x0802_4D01,  # j     0x80093404     -> advance to state 2
    0xAC82_296C,  # sw    v0,0x296c(a0)
)

# State 4's tail, copied rather than rewritten. Same length as the block above.
ELEVATOR_STATE_4_TAIL_WORDS = (
    0x3C02_8008,  # lui   v0,0x8008
    0x9044_2E6B,  # lbu   a0,0x2e6b(v0)
    0x0C01_02A8,  # jal   0x80040aa0
    0x0000_0000,  # nop                  <- vanilla's delay slot; leave it alone
    0x3C03_8001,  # lui   v1,0x8001
    0x9463_0234,  # lhu   v1,0x0234(v1)  <- persistent floor
    0x3C02_8008,  # lui   v0,0x8008
    0x0C01_2078,  # jal   0x800481e0
    0xA443_146C,  # sh    v1,0x146c(v0)  <- active floor
    0x0802_4D01,  # j     0x80093404
    0x0000_0000,  # nop
)

ELEVATOR_STATE_TABLE_ORIGINALS = {
    2: 0x8009_32F4,
    3: 0x8009_3310,
    4: 0x8009_3348,
}

# The resident prompt string, `QUIT?` full-width plus a newline. Nothing else
# passes it to build_yes_no_prompt, so it goes with the request.
QUIT_PROMPT_STRING_ADDRESS = 0x8007_16E8
QUIT_PROMPT_STRING_ORIGINAL = bytes.fromhex("8270827482688273814800")


def _dungeon_runtime_to_file_offset(address: int) -> int:
    if address < GAMEPLAY_OVERLAY_RUNTIME_ADDRESS:
        raise ValueError(f"Address 0x{address:08x} is below the gameplay overlay.")
    return (
        GAMEPLAY_OVERLAY_FILE_OFFSET
        + address
        - GAMEPLAY_OVERLAY_RUNTIME_ADDRESS
    )


def build_elevator_without_quit_prompt() -> bytes:
    """State 4's tail, verbatim, in place of the prompt request."""

    if len(ELEVATOR_STATE_4_TAIL_WORDS) != len(ELEVATOR_PROMPT_REQUEST_ORIGINALS):
        raise ValueError(
            "State 4's tail no longer matches the request block's length."
        )
    return struct.pack(
        f"<{len(ELEVATOR_STATE_4_TAIL_WORDS)}I", *ELEVATOR_STATE_4_TAIL_WORDS
    )


def iter_elevator_file_patches() -> tuple[tuple[int, bytes], ...]:
    """The request block, plus the three prompt states retargeted to idle."""

    patches = [
        (
            _dungeon_runtime_to_file_offset(ELEVATOR_PROMPT_REQUEST_ADDRESS),
            build_elevator_without_quit_prompt(),
        )
    ]
    for state in ELEVATOR_PROMPT_STATES:
        patches.append(
            (
                _dungeon_runtime_to_file_offset(
                    ELEVATOR_STATE_TABLE_ADDRESS + state * 4
                ),
                struct.pack("<I", ELEVATOR_IDLE_HANDLER_ADDRESS),
            )
        )
    return tuple(patches)


def iter_quit_string_slus_patches() -> tuple[tuple[int, bytes], ...]:
    """Erase the prompt string now that nothing passes it to the builder."""

    return (
        (
            slus_runtime_to_file_offset(QUIT_PROMPT_STRING_ADDRESS),
            bytes(len(QUIT_PROMPT_STRING_ORIGINAL)),
        ),
    )


# Koh's house overlay is TOWN.BIN+0x3e5000 loaded at 0x80016000, established by
# solving for the base that puts every choice-branch target in the overlay on a
# token boundary - nine of the eleven land exactly on an `08 57 xx` page
# prologue.
#
# Mom offers the same three-choice menu three times, differing only in the
# wording of the third option.  The dialogue VM's opcodes are documented in
# `docs/reverse-engineering-notes.md`; a menu is
#
#     08 | 57 01 | 0B [choice] 0A 0B [choice] 0A 0B [choice] | 19 <count> | 1A t0 t1 t2
#
# Removing the middle choice frees its 22 text bytes, its `0A` separator and
# its four-byte branch entry: 28 bytes each time.  **The block is rewritten to
# exactly its original length** - the freed bytes become trailing padding -
# because the VM's branch targets are absolute, and shortening the block would
# move every byte after it.  A scan of the whole overlay for words pointing
# into these three blocks found none, so nothing jumps into what moves.
#
# The padding is `0x01`, the end-of-script opcode. It is unreachable: a choice
# always branches through its table and never falls past it. If some path did
# reach it, Mom's conversation would close normally rather than misparse.
MOM_OVERLAY_FILE_OFFSET = 0x3E_5000
MOM_OVERLAY_RUNTIME_ADDRESS = 0x8001_6000
MOM_OVERLAY_SIZE = 0x2000
DIALOGUE_END_OPCODE = 0x01
DIALOGUE_ROW_BREAK_OPCODE = 0x0A
DIALOGUE_GOTO_OPCODE = 0x17  # `17 <u32>`: unconditional, handler 0x8003965c
DIALOGUE_CHOICE_OPCODE = 0x19
DIALOGUE_BRANCH_TABLE_OPCODE = 0x1A
DIALOGUE_SET_SLOT_OPCODE = 0x34  # `34 <slot> <u32>`: context + 0x48 + slot * 4
DIALOGUE_BRANCH_IF_SLOT_OPCODE = 0x3F  # `3f <slot> <u32>`: jump when non-zero
DIALOGUE_NATIVE_CALL_OPCODE = 0x4C  # `4c <u32>`: slots 0-3 in a0-a3, result -> 0x0f
DIALOGUE_RESULT_SLOT = 0x0F  # context + 0x84, where 0x4c stores its return

# Which page Mom starts on is chosen by her selector at 0x8001c884, and the
# choice comes down to one test:
#
#     8001cbbc  jal   0x8001e670          ; get_event_flag(0x16)
#     8001cbc0  addiu a0,zero,0x0016
#     8001cbc4  bne   v0,zero,0x8001cbd4  ; set   -> `What is it?`  0x80016359
#     8001cbd0  addiu v0,v0,0x602a        ; clear -> the greeting   0x8001602a
#
# **Story flag 0x16 means "the Pita Fruit has been taken."**  Her Pita
# subroutine sets it with `0C 16 00` - `0x0c <u16>` calls set_story_flag at
# 0x80033aa8 - immediately before granting the item, and
# `initialize_new_tower_run_state` calls clear_story_flag(0x16) at 0x80033ae8,
# which is why the tower row comes back after a tower trip.
EVENT_FLAG_PITA_TAKEN = 0x16
GET_EVENT_FLAG_ADDRESS = 0x8001_E670
# `08 clear` / `57 01` immediately before each menu's rows, so a jump here
# redraws the rows without reprinting the page's question.
MOM_GREETING_MENU_PAGE_ADDRESS = 0x8001_6082
MOM_SECOND_ENTRY_MENU_PAGE_ADDRESS = 0x8001_6376

# Choice rows, `0B` row-start opcode included, exactly as they appear on the
# disc. They are copied rather than re-encoded: the game's apostrophe is CP932
# 0x8166, which is not what the ASCII-to-full-width encoder produces.
_CHOICE_OPEN_THE_SAFE = bytes.fromhex(
    "0b816d826e82908285828e81408294828882858140829382818286828581408286828f"
    "82928140828d82858144816e"
)
_CHOICE_SAVE_DATA = bytes.fromhex("0b816d827282608275826481408263826082738260816e")
_CHOICE_NO_ITS_NOTHING = bytes.fromhex(
    "0b816d826d828f8143814082898294816682938140828e828f829482888289828e8287"
    "8144816e"
)
_CHOICE_NO_NOTHING_MORE = bytes.fromhex(
    "0b816d826d828f8143814082948288828582928285816682938140828e828f82948288"
    "8289828e82878140828d828f829282858144816e"
)
_CHOICE_IM_OFF = bytes.fromhex("0b816d82688166828d8140828f828682868144816e")
_CHOICE_IM_OFF_TO_THE_TOWER = bytes.fromhex(
    "0b816d82688166828d8140828f8286828681408294828f814082948288828581408294"
    "828f8297828582928144816e"
)
_CHOICE_IVE_GOT_A_FAVOR = bytes.fromhex(
    "0b816d826881668296828581408287828f829481408281814082868281829682"
    "8f829281408294828f814082818293828b8144816e"
)
_CHOICE_IM_NOT_LEAVING_YET = bytes.fromhex(
    "0b816d82688166828d8140828e828f82948140828c8285828182968289828e82878140"
    "8299828582948144816e"
)

_OPEN_THE_SAFE_TARGET = 0x8001_6401
MOM_SAVE_DATA_TARGET = 0x8001_6491
# `[I've got a favor to ask.]` led here: a `What is it?` page carrying nothing
# but the sub-menu that SAVE DATA needed. With SAVE DATA gone the sub-menu is
# `[Open the safe for me.]` and a cancel that returns to the greeting, so the
# greeting now offers the safe directly and this page is orphaned.
MOM_FAVOR_SUBMENU_TARGET = 0x8001_61F9


class DialogueMenu:
    """One choice block, and what it becomes.

    The replacement is assembled to the original's exact length. The VM's
    branch targets are absolute, so a block that shrinks would move every byte
    after it; the difference becomes trailing `0x01` padding instead.
    """

    def __init__(self, file_offset, length, original_rows, replacement_rows):
        self.file_offset = file_offset
        self.length = length
        self.original_rows = tuple(original_rows)
        self.replacement_rows = tuple(replacement_rows)

    @property
    def original(self) -> bytes:
        return self._assemble(self.original_rows, padding=0)

    @property
    def replacement(self) -> bytes:
        packed = self._assemble(self.replacement_rows, padding=0)
        return self._assemble(
            self.replacement_rows,
            padding=self.length - len(packed),
        )

    @property
    def freed_bytes(self) -> int:
        return self.length - len(self._assemble(self.replacement_rows, padding=0))

    def _assemble(self, rows, padding: int) -> bytes:
        block = bytearray()
        for index, (choice, _) in enumerate(rows):
            if index:
                block.append(DIALOGUE_ROW_BREAK_OPCODE)
            block.extend(choice)
        block.extend(
            (DIALOGUE_CHOICE_OPCODE, len(rows), DIALOGUE_BRANCH_TABLE_OPCODE)
        )
        for _, target in rows:
            block.extend(struct.pack("<I", target))
        block.extend(bytes((DIALOGUE_END_OPCODE,)) * padding)
        return bytes(block)


class PitaAwareReturn(DialogueMenu):
    """The page the safe returns to, re-asking which menu the player is on.

    Vanilla answered `Is there anything else?` with a third menu that belonged
    to neither entry, which is why SAVE DATA lived there. With SAVE DATA gone
    the honest answer is to send the player back to the rows they arrived
    through, and the selector's own test says which those are.

    Re-running the selector wholesale is not an option - it latches a flag on
    its first call - so this repeats only the one test it ends on.

    Getting this wrong in either direction is a real bug, not a cosmetic one:
    the Pita grant is gated purely by the greeting page being reachable, so
    returning there while the flag is clear is correct and returning there
    while it is set is an unlimited Pita source.
    """

    def __init__(self, file_offset, length, original_rows):
        super().__init__(file_offset, length, original_rows, ())

    @property
    def replacement(self) -> bytes:
        block = bytearray()
        # slot 0 becomes a0 for the call that follows.
        block.append(DIALOGUE_SET_SLOT_OPCODE)
        block.append(0)
        block.extend(struct.pack("<I", EVENT_FLAG_PITA_TAKEN))
        block.append(DIALOGUE_NATIVE_CALL_OPCODE)
        block.extend(struct.pack("<I", GET_EVENT_FLAG_ADDRESS))
        # Taken: the two rows of `What is it?`, which has no tower row.
        block.append(DIALOGUE_BRANCH_IF_SLOT_OPCODE)
        block.append(DIALOGUE_RESULT_SLOT)
        block.extend(struct.pack("<I", MOM_SECOND_ENTRY_MENU_PAGE_ADDRESS))
        # Not taken: the greeting's three rows, where the tower row still has
        # a Pita to hand over.
        block.append(DIALOGUE_GOTO_OPCODE)
        block.extend(struct.pack("<I", MOM_GREETING_MENU_PAGE_ADDRESS))
        return bytes(block) + bytes((DIALOGUE_END_OPCODE,)) * (
            self.length - len(block)
        )

    @property
    def freed_bytes(self) -> int:
        return self.length - 22


def _drop_save_data(file_offset, length, cancel_choice, cancel_target):
    return DialogueMenu(
        file_offset,
        length,
        original_rows=(
            (_CHOICE_OPEN_THE_SAFE, _OPEN_THE_SAFE_TARGET),
            (_CHOICE_SAVE_DATA, MOM_SAVE_DATA_TARGET),
            (cancel_choice, cancel_target),
        ),
        replacement_rows=(
            (_CHOICE_OPEN_THE_SAFE, _OPEN_THE_SAFE_TARGET),
            (cancel_choice, cancel_target),
        ),
    )


MOM_MENUS = (
    # Koh's greeting. The middle row asked for a favour and led to a page whose
    # only content was a second menu; that menu's other row came straight back
    # here. Offer the safe from the greeting instead.
    DialogueMenu(
        0x3E_5085,
        162,
        original_rows=(
            (_CHOICE_IM_OFF_TO_THE_TOWER, 0x8001_6171),
            (_CHOICE_IVE_GOT_A_FAVOR, MOM_FAVOR_SUBMENU_TARGET),
            (_CHOICE_IM_NOT_LEAVING_YET, 0x8001_6127),
        ),
        replacement_rows=(
            (_CHOICE_IM_OFF_TO_THE_TOWER, 0x8001_6171),
            (_CHOICE_OPEN_THE_SAFE, _OPEN_THE_SAFE_TARGET),
            (_CHOICE_IM_NOT_LEAVING_YET, 0x8001_6127),
        ),
    ),
    # Orphaned by the greeting change above, but rewritten anyway so no copy of
    # SAVE DATA survives anywhere.
    _drop_save_data(0x3E_5217, 126, _CHOICE_NO_ITS_NOTHING, 0x8001_63E5),
    # Where the safe returns: back to whichever rows the player came in
    # through, decided by the same story flag the entry selector uses.
    #
    # V70 jumped here to the greeting unconditionally and that was an unlimited
    # Pita Fruit source. The grant at `0x80016181` is gated only by the greeting
    # page being reachable, never by an "already given" test - what looks like
    # the test, `2E 78` then `42 0F` at `0x80016174`, is FNO 0x78 in the house
    # table `0x800D3CC8`, which is `0x8009F6E4`: it counts Koh's inventory-order
    # table and returns -1 only when all twenty slots are full, so it means
    # "hand it over if there is room".
    PitaAwareReturn(
        0x3E_52CB,
        142,
        original_rows=(
            (_CHOICE_OPEN_THE_SAFE, _OPEN_THE_SAFE_TARGET),
            (_CHOICE_SAVE_DATA, MOM_SAVE_DATA_TARGET),
            (_CHOICE_NO_NOTHING_MORE, 0x8001_63E5),
        ),
    ),
    # The house's second entry point, `What is it?` at 0x80016359. Reached from
    # the resource header rather than from the greeting, so it keeps its own
    # rows; only SAVE DATA goes.
    _drop_save_data(0x3E_5379, 108, _CHOICE_IM_OFF, 0x8001_64B2),
)


def iter_mom_menu_file_patches() -> tuple[tuple[int, bytes], ...]:
    patches = []
    for menu in MOM_MENUS:
        for name, block in (("replacement", menu.replacement), ("original", menu.original)):
            if len(block) != menu.length:
                raise ValueError(
                    f"The {name} for the menu at 0x{menu.file_offset:06x} is "
                    f"{len(block)} bytes against {menu.length}; the recorded "
                    "layout is wrong."
                )
        patches.append((menu.file_offset, menu.replacement))
    return tuple(patches)


def _title_runtime_to_file_offset(address: int) -> int:
    if not (
        TITLE_MODULE_RUNTIME_ADDRESS
        <= address
        < TITLE_MODULE_RUNTIME_ADDRESS + TITLE_MODULE_SIZE
    ):
        raise ValueError(f"Address 0x{address:08x} is outside the title module.")
    return (
        TITLE_MODULE_FILE_OFFSET + address - TITLE_MODULE_RUNTIME_ADDRESS
    )


def slus_runtime_to_file_offset(address: int) -> int:
    if address < SLUS_LOAD_ADDRESS:
        raise ValueError(f"Address 0x{address:08x} is below the SLUS load address.")
    return SLUS_HEADER_SIZE + address - SLUS_LOAD_ADDRESS


def _branch(opcode: int, rs: int, rt: int, source: int, target: int) -> int:
    return town_shop._i(opcode, rs, rt, (target - (source + 4)) // 4)


def build_title_menu_without_continue() -> dict[int, int]:
    """Two rows - New Game and Options - and no attract mode.

    Returns {runtime address: replacement word}. Every address appears in
    TITLE_PATCH_SITES or TITLE_V68_REVERTED_SITES, whose recorded originals a
    test checks against the untouched disc.
    """

    words = {
        # --- the row-building loop at 0x80088c7c ---
        0x8008_8C7C: town_shop._i(0x09, 0, 18, 1),  # addiu s2,zero,1
        0x8008_8C80: town_shop._i(0x09, 0, 23, 0x1E),  # addiu s7,zero,0x1e
        0x8008_8C84: town_shop._i(
            0x09, 0, 22, TITLE_FIRST_ROW_Y + TITLE_ROW_HEIGHT
        ),  # addiu s6,zero,184 - so the two rows land on rows 0 and 1's Y
        # Start one descriptor later: row 0 becomes New Game, row 1 Options.
        0x8008_8CFC: town_shop._i(0x23, 2, 2, 0x14),  # lw v0,0x14(sp+s4)
        # --- state 0x13 fades the rows in; there are two of them now ---
        0x8008_919C: town_shop._i(0x09, 0, 3, 1),  # addiu v1,zero,1
        # --- the cursor wraps over two rows, so both directions step by one ---
        0x8008_9328: town_shop._i(0x09, 4, 4, 1),  # addiu a0,a0,1
        0x8008_9344: town_shop._i(0x0C, 4, 4, 1),  # andi a0,a0,1
        # --- no attract mode: the countdown never reaches state 0xfe ---
        0x8008_93B0: _branch(0x04, 0, 0, 0x8008_93B0, TITLE_DISPATCH_EXIT_ADDRESS),
        # --- dispatch on the two surviving rows ---
        0x8008_9480: _branch(
            0x04, 3, 0, 0x8008_9480, TITLE_NEW_GAME_BRANCH_ADDRESS
        ),  # beq v1,zero -> New Game
        0x8008_948C: town_shop._i(0x09, 0, 2, 1),  # addiu v0,zero,1
        0x8008_9490: _branch(
            0x04, 3, 2, 0x8008_9490, TITLE_OPTIONS_BRANCH_ADDRESS
        ),  # beq v1,v0 -> Options
        # An index above 1 cannot happen once the cursor is masked, but the
        # vanilla fallthrough writes 0xff, and 0xff is the hang. Send it back
        # to browsing instead so no reachable path can ever park there.
        0x8008_9494: town_shop._i(0x09, 0, 2, TITLE_BROWSING_STATE),
        0x8008_94A4: town_shop._i(0x09, 0, 2, TITLE_BROWSING_STATE),
        # Row 0's label is chosen by the helper in the retired card driver.
        TITLE_STAGE_NEW_GAME_LABEL_ADDRESS: town_shop._j(
            0x02, TITLE_LABEL_HELPER_ADDRESS
        ),
    }
    # The mod-3 sequence is seven instructions; the ANDI replaces the first and
    # the rest become NOPs.
    for address in range(
        TITLE_CURSOR_MOVE_APPLY_ADDRESS + 4, TITLE_CURSOR_MOVE_APPLY_ADDRESS + 0x1C, 4
    ):
        words[address] = 0
    words.update(TITLE_V68_REVERTED_SITES)

    recorded = {address for address, _, _ in TITLE_PATCH_SITES}
    recorded |= {address for address, _ in TITLE_V68_REVERTED_SITES}
    if set(words) != recorded:
        raise ValueError(
            "The title patch touches words with no recorded original: "
            f"{sorted(hex(a) for a in set(words) ^ recorded)}"
        )
    for address, word in words.items():
        if word == town_shop._i(0x09, 0, 2, 0xFF):
            raise ValueError(f"0x{address:08x} would write the hanging state 0xff.")
    return words


def build_completed_save_entry() -> bytes:
    """Report an instantly finished save without opening the card screen.

    Vanilla clears the caller's done bit, builds the card-screen actor, and the
    completion routine sets the bit again when the card operation finishes.
    Both callers are written around that handshake: the town FNO polls the bit
    at 0x800c1b20, and the elevator installs the wait state at 0x80048d60,
    which polls the same bit and then closes the prompt.  Setting the bit here
    and returning non-zero satisfies both on their first poll, so each route
    converges on the path it already runs when a save completes - the town
    script continues, and the elevator resumes its ascent.
    """

    words = (
        town_shop._i(0x25, 4, 2, CARD_SCREEN_DONE_FLAG_OFFSET),  # lhu v0,0x1e(a0)
        0,  # R3000 load delay
        town_shop._i(0x0D, 2, 2, CARD_SCREEN_DONE_BIT),  # ori v0,v0,0x2000
        town_shop._i(0x29, 4, 2, CARD_SCREEN_DONE_FLAG_OFFSET),  # sh v0,0x1e(a0)
        town_shop._r(31, 0, 0, 0, 0x08),  # jr ra
        town_shop._i(0x09, 0, 2, 1),  # addiu v0,zero,1 - delay slot
        0,  # erase the rest of the retired entry
        0,
    )
    payload = struct.pack(f"<{len(words)}I", *words)
    assert len(payload) == CARD_DRIVER_ENTRY_SIZE
    return payload


def build_title_label_helper() -> bytes:
    """Stage `CONTINUE` instead of `NEW GAME` when the client's flag is set.

    Reached by `j` from 0x80088c18, which displaces `sw a0,0x14(sp)`; that
    instruction is performed here instead. The jump's delay slot at 0x80088c1c
    is the vanilla `sw a1,0x18(sp)` and still runs, which is wanted - it stages
    the Options label. `sp` is untouched by a jump, so the displaced store
    writes exactly where it always did.

    t0 and t1 are free here: the constructor reloads v0, v1 and a0 immediately
    after the resume point and never reads a t register across it.
    """

    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x0F, 0, 8, town_shop._upper(TITLE_CONTINUE_FLAG_ADDRESS)),
        town_shop._i(0x24, 8, 8, town_shop._lower(TITLE_CONTINUE_FLAG_ADDRESS)),
        # R3000 load delay, spent building the candidate rather than on a nop.
        town_shop._i(0x0F, 0, 9, town_shop._upper(TITLE_CONTINUE_LABEL_DESCRIPTOR)),
        town_shop._i(
            0x09, 9, 9, town_shop._lower(TITLE_CONTINUE_LABEL_DESCRIPTOR)
        ),
    )
    b.branch(0x04, 8, 0, "keep")  # beq t0,zero,keep
    b.emit(
        0,
        town_shop._r(9, 0, 4, 0, 0x21),  # move a0,t1
    )
    b.label("keep")
    b.emit(
        town_shop._i(0x2B, 29, 4, 0x14),  # sw a0,0x14(sp) - the displaced store
        town_shop._j(0x02, TITLE_STAGE_RESUME_ADDRESS),
        0,
    )
    payload = b.build()
    end = TITLE_LABEL_HELPER_ADDRESS + len(payload)
    if end > RETIRED_CARD_DRIVER_END_ADDRESS:
        raise ValueError(
            f"The title label helper ends at 0x{end:08x}, past the retired "
            f"driver space at 0x{RETIRED_CARD_DRIVER_END_ADDRESS:08x}."
        )
    return payload


def iter_title_label_slus_patches() -> tuple[tuple[int, bytes], ...]:
    """The flag byte and the helper, both in the retired card driver."""

    return (
        # Zeroed, so a disc with no client shows NEW GAME. Without this the
        # flag would read as the retired driver's first instruction, which is
        # very much not zero.
        (slus_runtime_to_file_offset(TITLE_CONTINUE_FLAG_ADDRESS), bytes(4)),
        (
            slus_runtime_to_file_offset(TITLE_LABEL_HELPER_ADDRESS),
            build_title_label_helper(),
        ),
    )


def iter_title_file_patches() -> tuple[tuple[int, bytes], ...]:
    words = build_title_menu_without_continue()
    return tuple(
        (_title_runtime_to_file_offset(address), struct.pack("<I", word))
        for address, word in sorted(words.items())
    )


def iter_slus_file_patches() -> tuple[tuple[int, bytes], ...]:
    # The two "already done" entry stubs are NO LONGER WRITTEN. The
    # tower-resume warp body occupies 0x8004EE50-0x8004EEBB, which spans both
    # entries and the driver tail. They were belt and braces for routes that no
    # longer exist: an entry's only job is to call
    # create_memory_card_screen_actor, and an execution breakpoint on that,
    # held from boot through a full session on the 0.9.100 disc, never fired -
    # so anything that could reach a stub could reach the actor.
    # `build_completed_save_entry` is kept for the record and for tests.
    return (
        *iter_title_label_slus_patches(),
        *iter_quit_string_slus_patches(),
        # The tower-resume warp stub, in this driver's dead tail. Imported
        # rather than defined here because the code belongs with the warp it
        # tail-calls; the SLUS patch list belongs here.
        *_iter_resume_warp_slus_patches(),
    )


def _iter_resume_warp_slus_patches() -> tuple[tuple[int, bytes], ...]:
    from . import town_warp

    return town_warp.iter_resume_warp_slus_patches()


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


def iter_save_removal_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        *_iter_mode2_raw_patches(MAIN_FILE_START_LBA, iter_title_file_patches()),
        *_iter_mode2_raw_patches(SLUS_FILE_START_LBA, iter_slus_file_patches()),
        *_iter_mode2_raw_patches(
            town_shop.TOWN_FILE_START_LBA,
            iter_mom_menu_file_patches(),
        ),
        *_iter_mode2_raw_patches(
            DUNGEON_FILE_START_LBA,
            iter_elevator_file_patches(),
        ),
    )


def append_save_removal_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_save_removal_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
