"""Town item delivery: the NPC-drained receive queue.

Passive town delivery is RETIRED (2026-08-01). The old design published a
native dialogue from a per-frame dispatcher whenever four guards - town
modal state, modal root, CD queue, pending transition - all read idle. They
all DO read idle in the frames between an NPC talk button going down and
that NPC's modal existing, so the receive owned the native new-script queue
the NPC then staged over. That is the Nada crash and the Monster hut crash,
and no frame-level guard closes it: the race is against a modal that does
not exist yet. `docs/town-receive-implementation.md` has the full account.

The replacement inverts the ownership. The game never opens anything on its
own. The client appends items to a queue in the town core, and Nada drains
it from *inside* her own conversation, through ordinary `0x4C` script calls -
so the only modal that can exist across a delivery is the one the player
deliberately opened, and delivery happens on the script parser's own frame
with no frame-safety question at all.

The queue's correctness does not depend on the conversation lock:

* the client only ever APPENDS, never compacts or reorders;
* `arm` snapshots `count` into `limit`, and delivery is bounded by `limit`;
* so a client append that races the lock lands at an index this
  conversation already decided not to read. It stays in the client's
  pending list and is offered at the next talk.

The lock therefore only makes `Items received!` honest about what was
waiting when the player asked. A stuck lock cannot corrupt or wedge
anything, which is why no frame-hook watchdog guards it - the client simply
times a stale lock out.

Free-running byte cursors, sixteen slots: occupancy is `(count - head) &
0xFF` and the slot index is `value & 15`. Sixteen divides 256, so the
counters may wrap forever without the index math drifting - there is no
reset step, no compaction, and exactly ONE writer per byte (client: count;
game: lock, head, limit, result).

The acknowledgement is per entry, and as of 2026-08-02 the GAME DOES NOT
RECORD IT. Each entry is `[native descriptor:4][token:4]`; the token
identifies the item to the client and the game never interprets it.

The game's only report is `head` advancing past an entry, which means "this
one reached storage". The client owns the delivered-through count outright
and persists it in its checkpoint metadata, so inventory and delivered-count
roll back together on a restore.

That replaced a durable cursor at `0x80015FDC` written by BOTH the town
delivery routine and the tower dispatcher and read as truth by the client -
two game-side writers of one client-facing number, which is how town and
tower came to disagree. No game code ever read it. Removing it also retires
the gift sign-bit guard: there is no cursor left for a gift to clobber.
"""

from __future__ import annotations

import struct

from . import town_shop


TOWN_RECEIVE_REGION_START_OFFSET = town_shop.SHOP_DATA_END_OFFSET

# --- The ADTR beacon (retained) ----------------------------------------------
# The record's receive fields are retired with the dispatcher, but the record
# itself stays exactly where it was, at protocol 2, for two unrelated reasons:
# its magic is the client's "the town core is resident" test (the branch that
# picks town delivery over the tower path), and bytes +0x1E/+0x1F are the
# intro-restore handshake, which has nothing to do with receives. Retired
# fields are left zero and are documented as free in FREE_SLAB_SPANS.
MAILBOX_OFFSET = 0xD80
MAILBOX_ADDRESS = town_shop.SHOP_CORE_ADDRESS + MAILBOX_OFFSET
MAILBOX_SIZE = 0x20
MAILBOX_MAGIC = int.from_bytes(b"ADTR", "little")
MAILBOX_VERSION = 2

RETIRED_RECEIVE_FIELDS_OFFSET = 0x08
RETIRED_RECEIVE_FIELDS_END_OFFSET = 0x1E
INTRO_RESTORE_STATE_OFFSET = 0x1E
INTRO_RESTORE_PROTOCOL_OFFSET = 0x1F

INTRO_RESTORE_PROTOCOL_VERSION = 1
INTRO_RESTORE_STATE_FIRST_RUN = 0
INTRO_RESTORE_STATE_NAME_READY = 1
INTRO_RESTORE_STATE_PROBE_REQUEST = 2
INTRO_RESTORE_STATE_APPLY_REQUEST = 4
INTRO_RESTORE_STATE_APPLY_COMPLETE = 5
INTRO_RESTORE_STATE_CAPTURE_REQUEST = 6
INTRO_RESTORE_STATE_CAPTURE_COMPLETE = 7

# --- The receive queue (ADRQ) ------------------------------------------------
# Lives in the span the retired dispatcher vacated. Sixteen slots, not the
# twenty the sketch asked for, because sixteen divides 256: it is what lets
# the byte cursors free-run instead of needing a reset, and a reset is a
# second writer on a byte, which is the whole race we are removing.
# 2026-08-02: the queue LEFT the town slab. It used to live at slab +0xDA0,
# which is reloaded from disc on every town entry - so a queue filled in town
# was wiped by a tower trip, and "the record vanished" was indistinguishable
# from "the game consumed it". That ambiguity is what the gift re-offer path
# and the head-distance check existed to paper over.
#
# It now sits in the resident block, immediately above the AP mailbox, in the
# slack the carve retraction left. That region is canary-certified and
# survived a full town+tower session untouched, so the record persists across
# mode changes and a reset can no longer be mistaken for a delivery.
#
# This address was unreachable before last night: the mailbox it follows was
# at 0x801FFF00, which is vanilla stack territory, and that is exactly why
# town and tower had separate receive systems in the first place.
QUEUE_ADDRESS = 0x801D_A640
QUEUE_OFFSET = None  # no longer slab-relative
QUEUE_MAGIC = int.from_bytes(b"ADRQ", "little")
QUEUE_VERSION = 1
QUEUE_SLOTS = 16
QUEUE_ENTRY_SIZE = 8
QUEUE_ENTRIES_OFFSET = 0x18
QUEUE_SIZE = QUEUE_ENTRIES_OFFSET + QUEUE_SLOTS * QUEUE_ENTRY_SIZE

QUEUE_MAGIC_OFFSET = 0x00
QUEUE_VERSION_OFFSET = 0x04
QUEUE_STRUCT_SIZE_OFFSET = 0x06
# One writer per byte. Game: lock, head, limit, result. Client: count.
QUEUE_LOCK_OFFSET = 0x08
QUEUE_COUNT_OFFSET = 0x09
QUEUE_HEAD_OFFSET = 0x0A
QUEUE_LIMIT_OFFSET = 0x0B
QUEUE_RESULT_OFFSET = 0x0C

QUEUE_RESULT_IDLE = 0
QUEUE_RESULT_DELIVERED = 1
QUEUE_RESULT_NO_ROOM = 2

QUEUE_ENTRY_DESCRIPTOR_OFFSET = 0x00
QUEUE_ENTRY_TOKEN_OFFSET = 0x04

# The four script-callable routines, in the notification machinery's vacated
# span. Nada's script reaches every one through an ordinary `0x4C`.
ARM_OFFSET = 0xCD0
CHECK_OFFSET = 0xD00
UNLOCK_OFFSET = 0xD28
ARM_ADDRESS = town_shop.SHOP_CORE_ADDRESS + ARM_OFFSET
CHECK_ADDRESS = town_shop.SHOP_CORE_ADDRESS + CHECK_OFFSET
UNLOCK_ADDRESS = town_shop.SHOP_CORE_ADDRESS + UNLOCK_OFFSET
# The record's vacated slab span is now the delivery routine's home.
DELIVER_OFFSET = 0xDA0
DELIVER_ADDRESS = town_shop.SHOP_CORE_ADDRESS + DELIVER_OFFSET
DELIVER_END_OFFSET = town_shop.SHOP_CORE_SIZE

# Retired with the dispatcher and zero-filled by the payload builder. Declared
# so the next feature can claim them instead of guessing, and so a test can
# prove the build actually erased them rather than leaving something that
# looks live. (0xD40..0xD80 is the tail of the routine span above it.)
FREE_SLAB_SPANS = (
    (0x3F0, 0x420, "retired receive movement-stop check"),
    # 0xA00..0xA40, the retired movement-stop body, was claimed by
    # town_shop's send-menu capacity gate on 2026-08-05 - exactly the
    # "declared so the next feature can claim them" handover this tuple
    # exists for.
    (0xA78, 0xA7C, "retired receive movement-stop ra scratch"),
    (0xD40, 0xD80, "retired notification state, message and stop machinery"),
    (
        MAILBOX_OFFSET + RETIRED_RECEIVE_FIELDS_OFFSET,
        MAILBOX_OFFSET + RETIRED_RECEIVE_FIELDS_END_OFFSET,
        "retired ADTR receive request/ack/status/destination fields",
    ),
)

# The angel helpers occupy verified-zero tails inside existing town-shop
# function spans. The first-run Pita/capture wrapper has a dedicated fixed
# reservation immediately before the shared unfamiliar-item description. All
# three spans are explicitly overlap-checked when the combined payload is
# built.
# Moved 0x304 -> 0xA60 (door-script slack) in v112 so the intro-state
# writer could take the buy-price slack; everything derives from this
# constant.
INTRO_RESTORE_HELPER_OFFSET = 0xA60
INTRO_RESTORE_HELPER_ADDRESS = town_shop.SHOP_CORE_ADDRESS + INTRO_RESTORE_HELPER_OFFSET
INTRO_RESTORE_PROBE_OFFSET = 0x3D0
INTRO_RESTORE_PROBE_ADDRESS = town_shop.SHOP_CORE_ADDRESS + INTRO_RESTORE_PROBE_OFFSET
INTRO_CAPTURE_WRAPPER_OFFSET = town_shop.INTRO_CAPTURE_WRAPPER_OFFSET
INTRO_CAPTURE_WRAPPER_ADDRESS = (
    town_shop.SHOP_CORE_ADDRESS + INTRO_CAPTURE_WRAPPER_OFFSET
)
INTRO_RESTORE_STATE_ADDRESS = MAILBOX_ADDRESS + INTRO_RESTORE_STATE_OFFSET
INTRO_RESTORE_MARKER_ADDRESS = (
    town_shop.PERSISTENT_STATE_ADDRESS + 0x1A
)
INTRO_RESTORE_MARKER_VALUE = 1
INTRO_FIRST_RUN_READY_ADDRESS = (
    town_shop.PERSISTENT_STATE_ADDRESS + 0x1B
)
INTRO_FIRST_RUN_READY_VALUE = 1
ORIGINAL_HOUSE_PITA_FNO_ADDRESS = 0x800C_8120

INVENTORY_POINTERS_ADDRESS = 0x8001_029C
SAFE_DESCRIPTORS_ADDRESS = 0x8001_1F80
PERSISTENT_RECEIVED_COUNT_ADDRESS = 0x8001_5FDC

TOWN_MODAL_STATE_ADDRESS = 0x8008_1EB0
# Canonical in town_shop: the door freeze service polls the same root.
TOWN_MODAL_ROOT_ADDRESS = town_shop.TOWN_MODAL_ROOT_ADDRESS
TOWN_TRANSITION_PENDING_ADDRESS = 0x8008_2E6E
CD_QUEUE_HEAD_ADDRESS = 0x8008_14D0
CD_QUEUE_TAIL_ADDRESS = 0x8008_14D1

FIND_UNUSED_DESCRIPTOR_ADDRESS = 0x800B_2280
VANILLA_TOWN_FRAME_SERVICE_ADDRESS = 0x8004_3EB8
TOWN_FRAME_HOOK_ADDRESS = 0x8004_3E0C
RESIDENT_WRAPPER_ADDRESS = 0x8007_9CD0

# Canonical definitions live in town_shop, which shares this seam for the
# Monster Shop door gate's `Door is locked.` message.
TOWN_DIALOGUE_DESCRIPTOR_ADDRESS = town_shop.TOWN_DIALOGUE_DESCRIPTOR_ADDRESS
TOWN_DIALOGUE_CURSOR_ADDRESS = 0x8008_2AE0
TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET = town_shop.TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET
TOWN_DIALOGUE_COLUMN_OFFSET = town_shop.TOWN_DIALOGUE_COLUMN_OFFSET
TOWN_DIALOGUE_ROW_OFFSET = town_shop.TOWN_DIALOGUE_ROW_OFFSET

SAFE_CAPACITY_TABLE_ADDRESS = 0x8008_9268
SAFE_CAPACITY = 60

# Retired 2026-08-01 with the notification: the movement stop, its hold stub
# and the walk-resume interception existed only to keep a spontaneously
# opened receive box from being walked through. Nothing opens spontaneously
# any more. The walk-resume continuation slot is left entirely alone, so the
# standing state's native recovery is vanilla again.

SLUS_START_LBA = 24
SLUS_LOAD_ADDRESS = 0x8002_D000
SLUS_HEADER_SIZE = 0x800


def _build_resident_wrapper() -> bytes:
    # This replaces the first town frame-service call. The displaced vanilla
    # call still runs first, unchanged.
    #
    # With the dispatcher retired, the ONLY reason this hook still exists is
    # the durable seed-state initializer, which the dispatcher used to call
    # from its prologue on every ordinary town frame. The initializer is a
    # leaf, so the hook now calls it directly and nothing else runs per frame:
    # no delivery, no notification, nothing that can open a window. The mode
    # and ADTR-magic guards are kept exactly as they were, because the
    # initializer reads the slab and the magic is the proof it is loaded.
    #
    # This routine must remain within the 0x58-byte copyright string slot;
    # bytes from 0x80079D28 onward are live resident globals.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x09, 29, 29, -0x18),
        town_shop._i(0x2B, 29, 31, 0x14),
        town_shop._j(0x03, VANILLA_TOWN_FRAME_SERVICE_ADDRESS),
        0,
        town_shop._i(0x0F, 0, 8, 0x8008),
        town_shop._i(0x24, 8, 8, 0x2E6A),
        town_shop._i(0x09, 0, 9, 1),
    )
    b.branch(0x05, 8, 9, "return")
    b.emit(0)
    town_shop._load_address(b, 8, MAILBOX_ADDRESS)
    b.emit(town_shop._i(0x23, 8, 10, 0))
    b.emit(
        town_shop._i(0x0F, 0, 9, (MAILBOX_MAGIC >> 16) & 0xFFFF),
        town_shop._i(0x0D, 9, 9, MAILBOX_MAGIC & 0xFFFF),
    )
    b.branch(0x05, 10, 9, "return")
    b.emit(0)
    b.emit(town_shop._j(0x03, town_shop.STATE_INITIALIZER_ADDRESS), 0)

    b.label("return")
    b.emit(
        town_shop._i(0x23, 29, 31, 0x14),
        town_shop._i(0x09, 29, 29, 0x18),
        town_shop._r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _build_intro_restore_helper() -> bytes:
    # Script variable 1 is passed as a1. The three protocol phases use even
    # request states 2/4/6, selected by setting variable 1 to 0/2/4. The game
    # must return to the frame loop immediately after publishing a request:
    # DuckStation exposes external-memory changes only across that boundary.
    # The client polls the request while the surrounding cutscene still owns
    # input and publishes 5 or 7 after the restore/capture is complete.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(
            0x0F,
            0,
            3,
            town_shop._upper(INTRO_RESTORE_STATE_ADDRESS),
        ),
        town_shop._i(0x09, 5, 9, INTRO_RESTORE_STATE_PROBE_REQUEST),
        town_shop._i(
            0x28,
            3,
            9,
            town_shop._lower(INTRO_RESTORE_STATE_ADDRESS),
        ),
        town_shop._i(0x09, 0, 2, 0),
        town_shop._r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _build_intro_restore_probe() -> bytes:
    # Kept as a harmless compatibility payload for older development patches.
    # New patches leave angel FNO 0x7E untouched because the client now stages
    # a returning script proactively as soon as the generated town is visible.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x0F, 0, 8, 0x8008),
        town_shop._i(0x25, 8, 2, 0x34B6),
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._i(0x0C, 2, 2, 0x2000),
    )
    return b.build()


def _build_intro_capture_wrapper() -> bytes:
    # ProGrammar's trusted wake-up script already calls house FNO 0x79 to add
    # the story Pita. A proactive checkpoint restore sets a one-use byte in the
    # save-backed ADSV padding. Consume that marker and skip the duplicate Pita
    # on the returning path. A true first run retains marker zero, calls the
    # original Pita function, then publishes a separate save-backed ready byte
    # so the client can release synchronization for stable-town capture.
    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x09, 29, 29, -0x18),
        town_shop._i(0x2B, 29, 31, 0x14),
        town_shop._i(
            0x0F,
            0,
            3,
            town_shop._upper(INTRO_RESTORE_MARKER_ADDRESS),
        ),
        town_shop._i(
            0x24,
            3,
            2,
            town_shop._lower(INTRO_RESTORE_MARKER_ADDRESS),
        ),
        # Fill the R3000 load delay before testing the one-use marker.
        0,
    )
    b.branch(0x04, 2, 0, "grant")
    b.emit(0)
    b.emit(
        town_shop._i(
            0x28,
            3,
            0,
            town_shop._lower(INTRO_RESTORE_MARKER_ADDRESS),
        ),
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)
    b.label("grant")
    b.emit(
        town_shop._j(0x03, ORIGINAL_HOUSE_PITA_FNO_ADDRESS),
        0,
        town_shop._i(
            0x0F,
            0,
            3,
            town_shop._upper(INTRO_FIRST_RUN_READY_ADDRESS),
        ),
        town_shop._i(0x09, 0, 8, INTRO_FIRST_RUN_READY_VALUE),
        town_shop._i(
            0x28,
            3,
            8,
            town_shop._lower(INTRO_FIRST_RUN_READY_ADDRESS),
        ),
    )
    b.label("return")
    b.emit(
        town_shop._i(0x23, 29, 31, 0x14),
        town_shop._i(0x09, 29, 29, 0x18),
        town_shop._r(31, 0, 0, 0, 0x08),
        0,
    )
    result = b.build()
    if len(result) > town_shop.INTRO_CAPTURE_WRAPPER_SIZE:
        raise ValueError("Intro capture wrapper exceeds its fixed town-core reservation.")
    return result.ljust(town_shop.INTRO_CAPTURE_WRAPPER_SIZE, b"\0")


def _build_queue_arm() -> bytes:
    """`0x4C` target run at the very top of Nada's script, before her first
    page is drawn: take the conversation lock and snapshot `count` into
    `limit`.

    The snapshot is the load-bearing half. Everything this conversation will
    ever deliver is fixed here, so a client append that races the lock lands
    beyond `limit` and is simply not this conversation's business - it stays
    in the client's pending list and is offered at the next talk. That is why
    nothing downstream has to synchronise with the client at all."""

    b = town_shop._MipsBuilder()
    town_shop._load_address(b, 8, QUEUE_ADDRESS)
    b.emit(
        town_shop._i(0x24, 8, 9, QUEUE_COUNT_OFFSET),
        # Load delay for t1: this constant is needed either way.
        town_shop._i(0x09, 0, 10, 1),
        town_shop._i(0x28, 8, 9, QUEUE_LIMIT_OFFSET),
        town_shop._i(0x28, 8, 10, QUEUE_LOCK_OFFSET),
        town_shop._i(0x28, 8, 0, QUEUE_RESULT_OFFSET),
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._i(0x09, 0, 2, 0),
    )
    result = b.build()
    if ARM_OFFSET + len(result) > CHECK_OFFSET:
        raise ValueError("The receive-queue arm routine overruns the check routine.")
    return result


def _build_queue_check() -> bytes:
    """`0x4C` target: 1 when this conversation has anything to deliver, 0
    otherwise, for the script's `0x3E` branch to the `No items are waiting.`
    page.

    Measured against `limit`, not `count`, so the answer cannot disagree with
    what the delivery pass will actually do. Occupancy is `(limit - head) &
    0xFF`: both cursors free-run as bytes and sixteen divides 256, so
    wraparound never breaks the subtraction."""

    b = town_shop._MipsBuilder()
    town_shop._load_address(b, 8, QUEUE_ADDRESS)
    b.emit(
        town_shop._i(0x24, 8, 9, QUEUE_HEAD_OFFSET),
        town_shop._i(0x24, 8, 10, QUEUE_LIMIT_OFFSET),
        # R3000 load delay before t2 is readable.
        0,
        town_shop._r(10, 9, 11, 0, 0x23),
        town_shop._i(0x0C, 11, 11, 0xFF),
        town_shop._r(31, 0, 0, 0, 0x08),
        # sltu v0,zero,t3 - the delay slot doubles as "pending != 0".
        town_shop._r(0, 11, 2, 0, 0x2B),
    )
    result = b.build()
    if CHECK_OFFSET + len(result) > UNLOCK_OFFSET:
        raise ValueError("The receive-queue check routine overruns the unlock routine.")
    return result


def _build_queue_unlock() -> bytes:
    """`0x4C` target on every page that ends Nada's conversation: drop the
    lock so the client may append again.

    Missing an exit path costs nothing structural - the queue is safe under a
    stuck lock - so this needs no frame-hook watchdog. The client treats a
    lock it has held too long as stale and appends anyway."""

    b = town_shop._MipsBuilder()
    town_shop._load_address(b, 8, QUEUE_ADDRESS)
    b.emit(
        town_shop._i(0x28, 8, 0, QUEUE_LOCK_OFFSET),
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._i(0x09, 0, 2, 0),
    )
    result = b.build()
    if UNLOCK_OFFSET + len(result) > 0xD40:
        raise ValueError("The receive-queue unlock routine overruns the free tail.")
    return result


def _build_queue_deliver() -> bytes:
    """`0x4C` target: drain `head`..`limit` into the inventory, then the safe.
    Returns 0 when everything landed and 1 when storage filled up, which is
    the script's `0x3E` branch between `Items received!` and the no-room page.

    Runs entirely inside Nada's conversation, on the script parser's own
    frame, so none of the old dispatcher's frame guards apply: there is no
    stable-frame count, no modal test, no CD-queue test. What IS kept from
    the dispatcher is every rule about mutating native item state, because
    those were paid for in live crashes:

    * a `-1` in the display-order table is the shop compactor's transient
      deletion marker. The dispatcher deferred a frame; a script call cannot
      defer, so this falls through to the safe instead - degrade the
      destination, never walk a table mid-compaction.
    * an inventory append writes a fresh zero terminator after the new
      pointer, mirroring the native Equipment insert at `0x80016F90`.
      Compaction leaves stale pointers past the terminator, so overwriting
      the old zero alone exposes that tail to the Items walk (V22's crash).
    * `head` advances only AFTER an entry is safely stored, so an interrupted
      pass re-offers exactly what it failed to deliver.
    * the durable receive cursor is committed per ordinary entry and skipped
      for gifts by `bltz` on the token's sign bit."""

    b = town_shop._MipsBuilder()
    b.emit(
        town_shop._i(0x09, 29, 29, -0x28),
        town_shop._i(0x2B, 29, 31, 0x24),
        town_shop._i(0x2B, 29, 16, 0x20),
        town_shop._i(0x2B, 29, 17, 0x1C),
        town_shop._i(0x2B, 29, 18, 0x18),
        town_shop._i(0x2B, 29, 19, 0x14),
        town_shop._i(0x2B, 29, 20, 0x10),
    )
    # s0 = queue base, s1 = head, s2 = limit.
    town_shop._load_address(b, 16, QUEUE_ADDRESS)
    b.emit(
        town_shop._i(0x24, 16, 17, QUEUE_HEAD_OFFSET),
        town_shop._i(0x24, 16, 18, QUEUE_LIMIT_OFFSET),
        0,
    )

    b.label("next_entry")
    b.emit(town_shop._r(18, 17, 8, 0, 0x23), town_shop._i(0x0C, 8, 8, 0xFF))
    b.branch(0x04, 8, 0, "delivered_all")
    # s3 = &entries[head & 15]; the AND and shift fill the delay slots.
    b.emit(town_shop._i(0x0C, 17, 19, QUEUE_SLOTS - 1))
    b.emit(
        town_shop._r(0, 19, 19, 3, 0x00),
        town_shop._r(16, 19, 19, 0, 0x21),
        town_shop._i(0x09, 19, 19, QUEUE_ENTRIES_OFFSET),
    )
    # s4 = descriptor, s3 stays the entry pointer for the token load.
    b.emit(
        town_shop._i(0x23, 19, 20, QUEUE_ENTRY_DESCRIPTOR_OFFSET),
        0,
    )
    # Reject a descriptor with a zero item id or a zero category: the same
    # validity rule the dispatcher used, and the same reason - a zero
    # category is the game's own "slot is free" marker, so storing one
    # creates an invisible item that the next allocator hands out twice.
    b.emit(town_shop._i(0x0C, 20, 8, 0xFF))
    b.branch(0x04, 8, 0, "skip_entry")
    b.emit(town_shop._r(0, 20, 9, 8, 0x02))
    b.emit(town_shop._i(0x0C, 9, 9, 0xFF))
    b.branch(0x04, 9, 0, "skip_entry")
    b.emit(0)

    # Count the live display-order pointers, bounded at twenty. A null is the
    # append position; a -1 marker means the table is mid-compaction.
    town_shop._load_address(b, 8, INVENTORY_POINTERS_ADDRESS)
    b.emit(town_shop._i(0x09, 0, 12, 0))
    b.label("order_scan")
    b.emit(town_shop._i(0x23, 8, 9, 0), 0)
    b.branch(0x04, 9, 0, "inventory_available")
    b.emit(town_shop._i(0x09, 0, 10, -1))
    b.branch(0x04, 9, 10, "safe_scan_start")
    b.emit(town_shop._i(0x09, 12, 12, 1))
    b.emit(town_shop._i(0x0B, 12, 10, 20))
    b.branch(0x05, 10, 0, "order_scan")
    b.emit(town_shop._i(0x09, 8, 8, 4))
    b.branch(0x04, 0, 0, "safe_scan_start")
    b.emit(0)

    b.label("inventory_available")
    # The native free-descriptor allocator, called exactly as the dispatcher
    # called it. t4 (the live pointer count) survives because it is the
    # append index; a0-a2 and v0/t* do not.
    b.emit(town_shop._i(0x2B, 29, 12, 0x0C))
    town_shop._load_address(b, 4, INVENTORY_POINTERS_ADDRESS)
    b.emit(
        town_shop._i(0x09, 0, 5, 0),
        town_shop._i(0x09, 0, 6, 20),
        town_shop._j(0x03, FIND_UNUSED_DESCRIPTOR_ADDRESS),
        0,
        town_shop._i(0x23, 29, 12, 0x0C),
        town_shop._r(2, 0, 8, 0, 0x21),
        town_shop._i(0x2B, 8, 20, 0),
    )
    town_shop._load_address(b, 9, INVENTORY_POINTERS_ADDRESS)
    b.emit(
        town_shop._r(0, 12, 10, 2, 0x00),
        town_shop._r(9, 10, 9, 0, 0x21),
        town_shop._i(0x2B, 9, 8, 0),
    )
    b.branch(0x04, 0, 0, "stored")
    # The fresh terminator, in the branch delay slot.
    b.emit(town_shop._i(0x2B, 9, 0, 4))

    b.label("safe_scan_start")
    town_shop._load_address(b, 8, SAFE_DESCRIPTORS_ADDRESS)
    b.emit(town_shop._i(0x09, 0, 9, SAFE_CAPACITY))
    b.label("safe_scan")
    # Byte +1 is the category: zero means the physical slot is unused.
    b.emit(town_shop._i(0x24, 8, 10, 1), 0)
    b.branch(0x04, 10, 0, "safe_available")
    b.emit(0)
    b.emit(
        town_shop._i(0x09, 8, 8, 4),
        town_shop._i(0x09, 9, 9, -1),
    )
    b.branch(0x05, 9, 0, "safe_scan")
    b.emit(0)
    b.branch(0x04, 0, 0, "no_room")
    b.emit(0)

    b.label("safe_available")
    b.emit(town_shop._i(0x2B, 8, 20, 0))

    b.label("stored")
    # No durable-cursor commit. The client owns that number as of
    # 2026-08-02: the game reports what it did (head advancing past an
    # entry) and nothing more. Two game-side writers of one client-facing
    # value is what let town and tower disagree, and it bought nothing -
    # no game code ever READ that cursor.
    #
    # The gift sign-bit guard goes with it. There is no cursor here for a
    # gift to clobber, so the whole class is gone rather than guarded.

    b.label("skip_entry")
    # Publish the advance only now: an entry that was never stored is still
    # this conversation's to deliver, and an invalid one is consumed so it
    # cannot wedge the queue forever.
    b.emit(
        town_shop._i(0x09, 17, 17, 1),
        town_shop._i(0x0C, 17, 17, 0xFF),
        town_shop._i(0x28, 16, 17, QUEUE_HEAD_OFFSET),
    )
    b.branch(0x04, 0, 0, "next_entry")
    b.emit(0)

    b.label("no_room")
    b.emit(
        town_shop._i(0x09, 0, 8, QUEUE_RESULT_NO_ROOM),
        town_shop._i(0x28, 16, 8, QUEUE_RESULT_OFFSET),
        town_shop._i(0x09, 0, 2, 1),
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    b.label("delivered_all")
    b.emit(
        town_shop._i(0x09, 0, 8, QUEUE_RESULT_DELIVERED),
        town_shop._i(0x28, 16, 8, QUEUE_RESULT_OFFSET),
        town_shop._i(0x09, 0, 2, 0),
    )

    b.label("return")
    b.emit(
        town_shop._i(0x23, 29, 31, 0x24),
        town_shop._i(0x23, 29, 16, 0x20),
        town_shop._i(0x23, 29, 17, 0x1C),
        town_shop._i(0x23, 29, 18, 0x18),
        town_shop._i(0x23, 29, 19, 0x14),
        town_shop._i(0x23, 29, 20, 0x10),
        town_shop._r(31, 0, 0, 0, 0x08),
        town_shop._i(0x09, 29, 29, 0x28),
    )
    result = b.build()
    if DELIVER_OFFSET + len(result) > DELIVER_END_OFFSET:
        raise ValueError(
            f"The receive-queue delivery routine is {len(result)} bytes and "
            "exceeds the span the retired dispatcher freed."
        )
    return result


def _build_resident_payload() -> bytes:
    return _build_resident_wrapper()


def build_town_receive_payload(base_payload: bytes | None = None) -> bytes:
    if base_payload is None:
        payload = bytearray(town_shop.SHOP_CORE_SIZE)
    else:
        if len(base_payload) != town_shop.SHOP_CORE_SIZE:
            raise ValueError("Town core payload must be exactly 4 KiB.")
        payload = bytearray(base_payload)

    if any(payload[TOWN_RECEIVE_REGION_START_OFFSET:]):
        raise ValueError("Town receive payload overlaps existing town-core data.")

    # The ADTR beacon, header only: the receive fields it used to carry are
    # retired and stay zero.
    struct.pack_into(
        "<IHH",
        payload,
        MAILBOX_OFFSET,
        MAILBOX_MAGIC,
        MAILBOX_VERSION,
        MAILBOX_SIZE,
    )
    payload[MAILBOX_OFFSET + INTRO_RESTORE_PROTOCOL_OFFSET] = (
        INTRO_RESTORE_PROTOCOL_VERSION
    )

    # The receive queue record is NOT in this image any more - it lives in
    # the resident block, outside the slab, so it survives a town reload.
    # The client initialises it, which is consistent with the client owning
    # the delivery state: nothing game-side needs it before Nada is talked to,
    # and `check` reads an all-zero record as "nothing waiting".

    for offset, data, name in (
        (
            INTRO_RESTORE_HELPER_OFFSET,
            _build_intro_restore_helper(),
            "intro-restore handshake helper",
        ),
        (
            INTRO_RESTORE_PROBE_OFFSET,
            _build_intro_restore_probe(),
            "intro-restore angel probe",
        ),
        (
            INTRO_CAPTURE_WRAPPER_OFFSET,
            _build_intro_capture_wrapper(),
            "intro first-run Pita/capture wrapper",
        ),
        (ARM_OFFSET, _build_queue_arm(), "receive-queue arm"),
        (CHECK_OFFSET, _build_queue_check(), "receive-queue check"),
        (UNLOCK_OFFSET, _build_queue_unlock(), "receive-queue unlock"),
        (DELIVER_OFFSET, _build_queue_deliver(), "receive-queue delivery"),
    ):
        if any(payload[offset : offset + len(data)]):
            raise ValueError(f"Town {name} overlaps the town-shop payload.")
        payload[offset : offset + len(data)] = data

    # Erase, rather than merely stop using, everything the retirement freed.
    # A retired region that still holds plausible-looking code is how a later
    # reader concludes a feature is live when it is not.
    for start, end, _ in FREE_SLAB_SPANS:
        for index in range(start, end):
            payload[index] = 0

    return bytes(payload)


def slus_address_to_file_offset(address: int) -> int:
    if address < SLUS_LOAD_ADDRESS:
        raise ValueError(f"Address 0x{address:08x} is below the SLUS load address.")
    return SLUS_HEADER_SIZE + address - SLUS_LOAD_ADDRESS


def _iter_mode2_raw_patches(
    start_lba: int,
    patches: tuple[tuple[int, bytes], ...],
) -> tuple[tuple[int, bytes], ...]:
    result: list[tuple[int, bytes]] = []
    for file_offset, data in patches:
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            within_sector = current % 2_048
            length = min(len(data) - copied, 2_048 - within_sector)
            raw_offset = town_shop.mode2_file_offset_to_raw_offset(start_lba, current)
            result.append((raw_offset, data[copied : copied + length]))
            copied += length
    return tuple(result)


def iter_town_receive_raw_patches(payload: bytes) -> tuple[tuple[int, bytes], ...]:
    if len(payload) != town_shop.SHOP_CORE_SIZE:
        raise ValueError("Town receive payload must be exactly 4 KiB.")

    town_patches = (
        (town_shop.SHOP_CORE_FILE_OFFSET, payload),
        (
            town_shop.town_runtime_to_file_offset(SAFE_CAPACITY_TABLE_ADDRESS),
            bytes((SAFE_CAPACITY, SAFE_CAPACITY, SAFE_CAPACITY)),
        ),
    )
    resident_patches = (
        (
            slus_address_to_file_offset(TOWN_FRAME_HOOK_ADDRESS),
            struct.pack("<I", town_shop._j(0x03, RESIDENT_WRAPPER_ADDRESS)),
        ),
        (slus_address_to_file_offset(RESIDENT_WRAPPER_ADDRESS), _build_resident_payload()),
    )
    return (
        *_iter_mode2_raw_patches(town_shop.TOWN_FILE_START_LBA, town_patches),
        *_iter_mode2_raw_patches(SLUS_START_LBA, resident_patches),
    )


def append_town_receive_ppf_records(ppf: bytearray, payload: bytes) -> None:
    for raw_offset, data in iter_town_receive_raw_patches(payload):
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
