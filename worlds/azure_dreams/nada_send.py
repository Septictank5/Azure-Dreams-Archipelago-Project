"""Nada's dialogue: the town receive NPC.

**Her send machinery was removed on 2026-08-11 (world 0.9.109).** Sending is
a tower feature now, priced in send tokens, one item per token
(docs/tower-send-design.md). Nada predated tokens and sent an unbounded
multi-select in one go, so she could either be re-plumbed to count tokens or
retired from sending entirely. She is retired: the town is a ten-minute stop
in a two-and-a-half-hour session, and a second send UI that has to agree with
the tower's token accounting is a standing correctness risk for a feature
nobody spends time in.

The file keeps its name because it is still the home of the bank-8 rewrite
and the selector pin, and because docs/systems/nada-send.md is the historical
record for the send system that shipped here; the send account there is
history, not behaviour. What is gone, and where it used to live in this file:
the menu-opener descriptor-script, the `Sent.` page, the silent bail page, the
catalog pump (`town_shop.build_send_menu_pump_payload`), the commit routine,
the three per-target caller scripts, the header-restore helper, the
`Send items to?` target page, and the outgoing `ADGS` mailbox in the shop-core
slab. The client stopped polling `ADGS` in the same change; the tower's `ADGT`
mailbox is untouched.

Her fresh-save dialogue script lives in the area dialogue bank at
TOWN.BIN+0x2CE000 (bank id 8), loaded at 0x8001DCD0.  Her script occupies
+0x4..+0x102 (entry 0 of her family's dialogue table at 0x8001B8F8) and the
CHILD's "attack button" script starts at +0x103 - a hard wall this rewrite
must never cross.

The rewrite additionally claims the plaza-gossip region of her own sector
(+0x1EC..+0x7FF).  The state banks are numbered resources that all load at
the same window base and swap wholesale per story state, so every reference
from another bank into this address range is that bank's own content, not
ours.  Bank 8's complete external entry set for sector 0 is:

* +0x004  Nada (variant 0x3D) - ours;
* +0x103  the child (variant 0x3C) - untouched;
* +0x1EC, +0x23C, +0x380  villager variant 0x2D gossip scripts - stubbed to
  a one-byte end-of-conversation, so talking to those villagers is safe;
* +0x5FC  a shared gossip subroutine 0x15-called by the three sector-1
  villager scripts (bank+0x874/+0x939/+0x9FE) - stubbed to a one-byte
  return so the callers continue safely.

Everything between those anchors is freed.  With the send gone the region
holds three small scripts and is otherwise erased; the layout (sector offsets
from 0x8001DCD0) is:

| Offset | Contents |
| --- | --- |
| +0x1EC | villager stub `01` |
| +0x1F0 | the Yes branch: deliver, then report |
| +0x23C | villager stub `01` |
| +0x240 | the delivered page |
| +0x380 | villager stub `01` |
| +0x384 | the no-room page |
| +0x5FC | subroutine stub `16` |
| +0x600 | erased (0x01 fill) - declared expansion space |

When the story state advances past floor 10 the whole bank is replaced and
Nada reverts to vanilla, so her dialogue and its callers disappear together -
nothing dangles.  The selector pin below is what stops that happening.

No cache trampoline is needed: nothing in vanilla ever executes from these
addresses, so the I-cache cannot hold stale lines for them.
"""

from __future__ import annotations

import struct

from . import town_receive, town_shop

NADA_DIALOGUE_RESOURCE_FILE_OFFSET = 0x2C_E000
NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS = 0x8001_DCD0
NADA_SCRIPT_OFFSET = 0x4
NADA_SCRIPT_RUNTIME_ADDRESS = (
    NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS + NADA_SCRIPT_OFFSET
)
# The child's script begins here; byte +0x103 is his `0F 3C 03` prologue.
NADA_SCRIPT_END_OFFSET = 0x103
NADA_SCRIPT_CAPACITY = NADA_SCRIPT_END_OFFSET - NADA_SCRIPT_OFFSET

# Her original script opened with `0F 3D 03 57 3D` - attach actor slot
# 0x3D, then HER PROSE WINDOW MODE.  v102 kept both and the choice grid
# broke: rows rendered but only the last line was selectable, with a
# phantom slot beside it.  Uncle's validated menus run with no 0x57 at all,
# so the attach is kept (it binds her name plate) and the prose mode is
# dropped for the menu.
NADA_SCRIPT_PROLOGUE = bytes((0x0F, 0x3D, 0x03))
NADA_ORIGINAL_PROLOGUE = bytes((0x0F, 0x3D, 0x03, 0x57, 0x3D))
NADA_CHILD_SCRIPT_PROLOGUE = bytes((0x0F, 0x3C, 0x03))

# --- Her dialogue -----------------------------------------------------------
#
# One question, two answers, and three ways the conversation can end. Every
# line is one row of text: `Take no more than 5 items.` (26 characters) is the
# longest string this project has drawn in a town window and it renders, so 26
# is the bound these stay under. No apostrophes anywhere - our encoder maps
# ASCII into full-width CP932 and the game's font is only known to carry the
# letters, digits, space, `.`, `!` and `?` we have already drawn.
NADA_RECEIVE_PROMPT = "Receive your items?"
NADA_YES_ROW = "Yes."
NADA_NO_ROW = "No."
# Nothing waiting. Reached BEFORE anything has been drawn (see
# build_nada_script), which is why its page carries no `0x08` clear.
NADA_NO_ITEMS_PAGE = "Sorry! Nothing yet."
NADA_RECEIVED_PAGE = "Here you go! Take care!"
# Twenty items into eighty slots can genuinely not fit. Saying "received"
# when nothing was received is the kind of lie that costs a debugging
# session, so the no-room case gets its own page - and it says what actually
# happens to the items, which is that the queue keeps them.
NADA_NO_ROOM_PAGE = "No room! These can wait."
NADA_GOODBYE_PAGE = "Come back any time!"

# --- The freed gossip region -------------------------------------------------

MACHINERY_REGION_OFFSET = 0x1EC
MACHINERY_REGION_END_OFFSET = 0x800
# Villager variant 0x2D entry points (state-0 variant table at 0x8001B8F8).
GOSSIP_STUB_OFFSETS = (0x1EC, 0x23C, 0x380)
GOSSIP_STUB_BYTE = 0x01  # end of conversation
# 0x15-called from the three sector-1 villager scripts; must return.
GOSSIP_SUBROUTINE_STUB_OFFSET = 0x5FC
GOSSIP_SUBROUTINE_STUB_BYTE = 0x16  # script return

# The Yes branch and the two pages it can end on. Each sits between two of
# the anchors above with its whole span to itself; the send machinery used to
# fill these gaps, and the spans are now generous on purpose - a page that
# outgrows its slot is a generation error, never a silent truncation.
RECEIVE_SCRIPT_OFFSET = 0x1F0
RECEIVE_SCRIPT_END_OFFSET = 0x23C
RECEIVED_PAGE_OFFSET = 0x240
RECEIVED_PAGE_END_OFFSET = 0x380
NO_ROOM_PAGE_OFFSET = 0x384
NO_ROOM_PAGE_END_OFFSET = 0x5FC

RECEIVE_SCRIPT_ADDRESS = (
    NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS + RECEIVE_SCRIPT_OFFSET
)
RECEIVED_PAGE_ADDRESS = (
    NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS + RECEIVED_PAGE_OFFSET
)
NO_ROOM_PAGE_ADDRESS = (
    NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS + NO_ROOM_PAGE_OFFSET
)

# --- The bank-8 selector pin ------------------------------------------------
#
# The outdoor overlay's per-conversation selection functions raise the current
# story state's event flag once the saved highest floor reaches 10
# (`slti v0,v0,10` at the `jal 0x8001B0C8` return target, then
# `jal 0x80019BC0`).  That flag is what advances the area dialogue bank past
# bank 8 and reverts Nada to vanilla.  The whole chain is event-flag
# bookkeeping, not address computation - see "The selection chain decompiled"
# in docs/reverse-engineering-notes.md - so forcing the comparison result to 1
# makes the branch always taken, the state flag is never raised, and the
# extractor keeps delivering bank 8.  Every ADAP run is a fresh save, so no
# pre-raised flag can exist to fight the pin.
#
# Three byte-identical copies of the check exist in the loaded overlay
# (Nada's row-0 selection, row-4's selection, and one unattributed selection
# function); all three are pinned for town-wide bank consistency.
#
# The overlay's initial image arrives with town construction from
# TOWN.BIN+0x2AB800 (verified: the `slti` word sits at exactly the mapped
# offset of the live-traced check, and Nada's template maps to its known
# file address 0x2B366C).  This region is below the idx-0 dialogue bundle
# at +0x2B5000: the pin is a hook edit outside chunk 5.
OUTDOOR_OVERLAY_RUNTIME_ADDRESS = 0x8001_6000
OUTDOOR_OVERLAY_FILE_OFFSET = 0x2A_B800

FLOOR_CHECK_ADDRESSES = (0x8001_683C, 0x8001_6BD8, 0x8001_8248)
# slti v0,v0,10 / bne v0,zero,+4 / addu a0,s0,zero / jal 0x80019BC0 / nop -
# the full five-word signature, identical at all three sites.
FLOOR_CHECK_ORIGINAL_WORDS = (
    0x2842_000A,
    0x1440_0004,
    0x0200_2021,
    0x0C00_66F0,
    0x0000_0000,
)
# addiu v0,zero,1 - "floor < 10" unconditionally; a 1:1 ALU substitution at
# a jal return target, so no delay-slot hazard at any site.
FLOOR_CHECK_REPLACEMENT_WORD = 0x2402_0001


def outdoor_overlay_runtime_to_file_offset(address: int) -> int:
    if not (
        OUTDOOR_OVERLAY_RUNTIME_ADDRESS
        <= address
        < NADA_DIALOGUE_RESOURCE_RUNTIME_ADDRESS
    ):
        raise ValueError(
            f"Address 0x{address:08x} is outside the outdoor overlay image."
        )
    return (
        OUTDOOR_OVERLAY_FILE_OFFSET + address - OUTDOOR_OVERLAY_RUNTIME_ADDRESS
    )


def iter_selector_pin_file_patches() -> tuple[tuple[int, bytes], ...]:
    return tuple(
        (
            outdoor_overlay_runtime_to_file_offset(address),
            struct.pack("<I", FLOOR_CHECK_REPLACEMENT_WORD),
        )
        for address in FLOOR_CHECK_ADDRESSES
    )


def _encode_text(text: str) -> bytes:
    return town_shop._encode_shop_name(text, max_characters=None)[:-1]


def _closing_page(text: str, clear: bool = True) -> bytes:
    """A page that ends the conversation, dropping the receive lock on the
    way out.

    `clear` emits the leading `0x08`, which every page reached from a choice
    row needs: a choice target is jumped to AFTER its menu page rendered, so
    without the clear its text appends to the menu instead of replacing it.
    It must be OFF for a page reached before anything has been drawn - the
    `0x08` handler calls `ClearImage`, and doing that before the window
    exists is what crashed v7 of the old passive receive path
    (docs/game/dialogue-scripts.md).

    Every exit unlocks, but nothing depends on it: the queue is append-only
    and delivery is bounded by the snapshot `arm` took, so a lock that never
    cleared cannot corrupt or wedge anything. The client times a stale lock
    out on its own. This is belt, and the belt is optional.
    """

    return (
        (bytes((0x08,)) if clear else b"")
        + _encode_text(text)
        + bytes((0x11, 0x4C))
        + struct.pack("<I", town_receive.UNLOCK_ADDRESS)
        + bytes((0x01, 0x01))
    )


def build_receive_script() -> bytes:
    """The Yes row: deliver, then report which of the two things happened.

    The "is anything waiting" question is already answered - her script asks
    it before it draws the prompt, and the arm call froze the answer before
    that - so this runs the delivery and nothing else.

    Everything here runs inside Nada's already-open conversation, on the
    script parser's own frame. That is the entire fix for the Nada / Monster
    hut crash: the game never opens a box on its own any more, so a receive
    can no longer win the native script queue from an NPC talk that is
    halfway through starting.
    """

    script = bytearray()
    # `0x4C` leaves its result in slot 0x0F and `0x3E` branches when that
    # slot is zero - the same pair the validated shop opener protocol uses.
    # Delivery returns 0 when everything landed, 1 when storage filled up.
    script += bytes((0x4C,)) + struct.pack("<I", town_receive.DELIVER_ADDRESS)
    script += bytes((0x3E, 0x0F)) + struct.pack("<I", RECEIVED_PAGE_ADDRESS)
    script += bytes((0x17,)) + struct.pack("<I", NO_ROOM_PAGE_ADDRESS)
    if RECEIVE_SCRIPT_OFFSET + len(script) > RECEIVE_SCRIPT_END_OFFSET:
        raise ValueError("Nada's receive script overruns its span.")
    return bytes(script)


def _encode_choice_rows(choices: list[str]) -> bytes:
    """Uncle's validated menu geometry: column pairs, `0x0B` opening each
    line, a computed full-width gap between the two columns, `0x0A` between
    lines. Native menus pad column 0 plus the gap to a fixed sixteen cells
    and the selection highlight covers that whole region."""

    rows = bytearray()
    for index, choice in enumerate(choices):
        if index % 2 == 0:
            if index > 0:
                rows += bytes((0x0A,))
            rows += bytes((0x0B,))
        else:
            cells = len(_encode_text(choices[index - 1])) // 2 + 2
            rows += bytes((0x81, 0x40)) * max(16 - cells, 2)
        rows += bytes((0x81, 0x6D))
        rows += _encode_text(choice)
        rows += bytes((0x81, 0x6E))
    return bytes(rows)


def build_machinery_region() -> bytes:
    """The whole +0x1EC..+0x7FF rewrite: the stubs, the Yes branch, its two
    pages, and erasure everywhere else.

    Freed gaps are filled with the end-of-script opcode, both to erase the
    retired gossip (the freed-space discipline) and so a stray jump ends
    the conversation instead of misparsing.
    """

    region = bytearray(
        bytes([GOSSIP_STUB_BYTE])
        * (MACHINERY_REGION_END_OFFSET - MACHINERY_REGION_OFFSET)
    )

    def put(offset: int, data: bytes, limit: int, name: str) -> None:
        if offset + len(data) > limit:
            raise ValueError(
                f"Nada machinery: {name} overruns its span "
                f"(+0x{offset:03X}+{len(data)} > +0x{limit:03X})."
            )
        start = offset - MACHINERY_REGION_OFFSET
        region[start:start + len(data)] = data

    for stub_offset in GOSSIP_STUB_OFFSETS:
        put(stub_offset, bytes([GOSSIP_STUB_BYTE]), stub_offset + 1, "stub")
    put(
        GOSSIP_SUBROUTINE_STUB_OFFSET,
        bytes([GOSSIP_SUBROUTINE_STUB_BYTE]),
        GOSSIP_SUBROUTINE_STUB_OFFSET + 1,
        "subroutine stub",
    )
    put(
        RECEIVE_SCRIPT_OFFSET,
        build_receive_script(),
        RECEIVE_SCRIPT_END_OFFSET,
        "receive script",
    )
    for offset, limit, text, name in (
        (RECEIVED_PAGE_OFFSET, RECEIVED_PAGE_END_OFFSET, NADA_RECEIVED_PAGE, "received page"),
        (NO_ROOM_PAGE_OFFSET, NO_ROOM_PAGE_END_OFFSET, NADA_NO_ROOM_PAGE, "no-room page"),
    ):
        put(offset, _closing_page(text), limit, name)
    return bytes(region)


def build_nada_script() -> bytes:
    """Her whole conversation, in her own 255 bytes.

    ```
    0F 3D 03                  attach her name plate
    4C <arm>                  take the lock, snapshot the queue depth
    4C <check>                anything waiting?
    3E 0F <nothing>           no -> the apology, and out
    "Receive your items?"     yes -> the question
    0A [Yes.]      [No.]
    2C 02 1A <deliver> <goodbye>
    nothing: "Sorry! Nothing yet."  11 4C <unlock> 01
    goodbye: 08 "Come back any time!" 11 4C <unlock> 01
    ```

    Three things here are load-bearing:

    **The arm call comes before any page is drawn.** Everything this
    conversation will deliver is fixed at that instant, which is what makes a
    client append racing the lock harmless - it lands beyond the snapshot and
    is simply the next conversation's business.

    **The check comes before the question**, so a player with nothing waiting
    is never offered a Yes that would do nothing. That branch is taken before
    a single glyph has been drawn, which is why its page carries no `0x08`
    clear and the goodbye page (a choice target, reached after the menu
    rendered) does.

    **Both branch targets live in her own script**, past the choice table.
    The `0x2C` block always branches, so those bytes are never fallen into -
    the same shape the old Cancel row used - and keeping them here leaves the
    whole freed bank region for the pages that need real room.

    Every one of the four exits drops the receive lock. Backing out with the
    circle button instead of `No.` may not, and that is survivable by design:
    the queue is append-only and the client times a stale lock out on its own.
    """

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
        labels[name] = len(script)

    script.extend(NADA_SCRIPT_PROLOGUE)
    # Arm before the first page: the snapshot must precede anything the
    # player can react to, so what she offers is exactly what was waiting
    # when they asked.
    emit(0x4C)
    emit_address(town_receive.ARM_ADDRESS)
    # `check` answers 1 when this conversation has something to deliver;
    # `0x3E <slot> <addr>` branches when the slot is zero.
    emit(0x4C)
    emit_address(town_receive.CHECK_ADDRESS)
    emit(0x3E, 0x0F)
    emit_label_address("nothing")

    script.extend(_encode_text(NADA_RECEIVE_PROMPT))
    emit(0x0A)
    script.extend(_encode_choice_rows([NADA_YES_ROW, NADA_NO_ROW]))
    emit(0x2C, 0x02, 0x1A)
    emit_address(RECEIVE_SCRIPT_ADDRESS)
    emit_label_address("goodbye")

    label("nothing")
    script.extend(_closing_page(NADA_NO_ITEMS_PAGE, clear=False))
    label("goodbye")
    script.extend(_closing_page(NADA_GOODBYE_PAGE))

    for position, name in fixups:
        script[position:position + 4] = struct.pack(
            "<I", NADA_SCRIPT_RUNTIME_ADDRESS + labels[name]
        )

    if len(script) > NADA_SCRIPT_CAPACITY:
        raise ValueError(
            "Nada's dialogue overruns her script space "
            f"({len(script)} > {NADA_SCRIPT_CAPACITY} bytes)."
        )
    # Pad to the child's script with the end-of-script opcode: choices always
    # branch, so the padding is unreachable, and if a bug ever reached it the
    # conversation closes instead of misparsing (the save_removal technique).
    script.extend(bytes([0x01]) * (NADA_SCRIPT_CAPACITY - len(script)))
    return bytes(script)


def iter_nada_file_patches() -> tuple[tuple[int, bytes], ...]:
    # Unconditional since 2026-08-01, and no longer parameterised at all:
    # she used to ship only when the room had another Azure Dreams player to
    # send to, then shipped everywhere once she became the receive NPC, and
    # now every seed gets the same bytes. The pin ships with her for the
    # reason it always did - past floor 10 the selector would otherwise swap
    # her bank out and take her dialogue with it.
    return (
        (
            NADA_DIALOGUE_RESOURCE_FILE_OFFSET + NADA_SCRIPT_OFFSET,
            build_nada_script(),
        ),
        (
            NADA_DIALOGUE_RESOURCE_FILE_OFFSET + MACHINERY_REGION_OFFSET,
            build_machinery_region(),
        ),
        *iter_selector_pin_file_patches(),
    )


def append_nada_ppf_records(ppf: bytearray) -> None:
    for file_offset, data in iter_nada_file_patches():
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            within_sector = current % 2_048
            length = min(len(data) - copied, 2_048 - within_sector, 255)
            raw_offset = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA, current
            )
            record = data[copied:copied + length]
            ppf.extend(struct.pack("<IB", raw_offset, len(record)))
            ppf.extend(record)
            copied += length
