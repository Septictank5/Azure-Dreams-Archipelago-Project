from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from . import bonus_floor


SEED_BLOCK_SIZE = 0x1800
# Stage-1 carve retraction (2026-07-30): the suite moved out of the
# game's effect pool (old home 0x801FD600, memory-top-carved) into the
# hand-certified free run at 0x801C8E40; the memory-top record now
# reads 0x001FE600. Base-patch binary references are rewritten by
# tools/Rebuild-AdapSeedRelocation.py - run it after touching the
# base ppf; docs/seed-relocation-plan.md holds the verified site list.
# Moved and grown 2026-08-02. Its old home 0x801C8E40 was inside the floor
# arena's reach: a high-30s floor load overwrote the whole page - which
# carries resident CODE, not just data - and running into it is the tower
# loading crash. The new home is the top of the gap beneath the
# carve-retraction block, 1,820 bytes clear of the highest arena byte ever
# measured. docs/adap-memory-safe-regions.md, docs/seed-relocation-plan.md.
SEED_BLOCK_ADDRESS = 0x801D_7F00
# Carve retraction (2026-08-01): the payload, the bonus block and the high
# mailbox moved out of the space above the lowered memory top, so the ceiling
# could go back to vanilla 0x00200000 and SP to 0x801FFFF0. The effect pool
# was filling to ceiling-8 at the mix-magic crash; the carve was starving it.
# Destination is canary-certified against a uboat+blume mix-magic dragon -
# docs/adap-memory-safe-regions.md, docs/carve-retraction-plan.md.
# Binary references are rewritten by tools/Apply-AdapCarveRetraction*.py.
TOWER_GAMEPLAY_BASE_ADDRESS = 0x801D_9700
TOWER_GAMEPLAY_END_ADDRESS = 0x801D_9EF0
# The AP protocol mailbox. It moved with its own delta rather than the
# payload's: its old home 0x801FFF00 is where vanilla's stack lives
# (PS-EXE SP 0x801FFFF0, growing down), which is precisely why it had to
# move before the ceiling could be restored.
HIGH_MAILBOX_ADDRESS = 0x801D_A540
# Where the client asks for the pickup presentation.
#
# This used to be bit 31 of the receive descriptor word - bit 7 of its flags
# byte - which is also the game's own "unidentified" flag. The dispatcher read
# it with `srl s6,t7,31` and then cleared it with an `sll 1 / srl 1` pair before
# storing the descriptor, so no item could ever be delivered unidentified: the
# protocol was overwriting a field that belongs to the item.
#
# It lives in its own mailbox word now. The descriptor's flags byte is the
# item's alone, and no strip is needed. 0xA8 (status) was the last defined
# field of a 0x100-byte mailbox, so 0xAC was free.
MAILBOX_RECEIVE_PRESENTATION_OFFSET = 0xAC
MAILBOX_FINALIZE_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS
FLOOR_LOCATION_HOOK_WRAPPER_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x80
SPAWN_FLOOR_LOCATIONS_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0xA0
# The bonus floor's spawner-suppression gate (bonus_floor.EDITS, 28 bytes of
# payload code): returns via ra when bonus-active, branches into the spawner
# otherwise. The floor-page loader calls THIS, not the spawner, so bonus
# suppression survives the paging interposition.
BONUS_SPAWNER_GATE_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x454
COLLECT_FLOOR_LOCATION_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x2B0
SEED_RUNTIME_LOADER_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x400
RECEIVE_ITEM_DISPATCHER_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x470
# The dispatcher's delivered-path return branch (`beq zero,zero,+0x634` in the
# pristine payload) and the common register-restore return it rejoins.
#
# From 2026-07-30 to 2026-08-05 the branch was retargeted through a
# cursor-commit stub that stored the delivered request sequence into the
# durable receive cursor. Its justification - "the tower request sequence IS
# cursor + 1 by construction, so it cannot disagree" - is true at STAGING time
# and false at DELIVERY time: a request left in the resident mailbox across a
# town round-trip (pending, or wedged on inventory-full) delivers on the next
# tower entry AFTER Nada's queue has advanced the cursor past it, and the
# stub's absolute `sw` rolled the cursor back by up to a full queue. Every item
# Nada had handed over then re-delivered natively - the Nada duplication bug.
# The stub is removed and the branch is native again; the client's ack fold
# (`FoldUnrecordedDeliveries`, which now also reads the resident mailbox in
# town) records deliveries instead. Rebuild-AdapGameplayPayload.py owns the
# restore and asserts both forms.
RECEIVE_DISPATCHER_DELIVERED_RETURN_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x608
RECEIVE_DISPATCHER_COMMON_RETURN_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x634

# --- the earthquake disable ---------------------------------------------------
#
# The dungeon overlay's per-turn earthquake callback (dump-traced 2026-07-30;
# docs/reverse-engineering-notes.md "The earthquake system"). Vanilla forces
# the player to the next floor when a floor's earthquake sequence completes,
# which bypasses the progressive-keycard elevator gate, so ADAP disables the
# whole system at its choke point: the callback's prologue becomes
# `jr ra / addu v0,zero,zero`, matching its own guard-failed exits. Warnings,
# screen shake, and the collapse all live behind this one entry; go-up traps
# use a separate handler and are untouched.
EARTHQUAKE_UPDATE_ADDRESS = 0x800C_5FA8
EARTHQUAKE_UPDATE_RAW_OFFSET = 0x1C9_E7B0  # DUNGEON.BIN +0xE0848, LBA 12759
EARTHQUAKE_VANILLA_PROLOGUE = (0x27BD_FFC8, 0xAFB6_0028)
EARTHQUAKE_DISABLED_PROLOGUE = (0x03E0_0008, 0x0000_1021)
SEED_MAGIC = 0x4453_4441  # "ADSD" in little-endian memory
# 3: pooled placement text. Per-floor paging (2026-08-09) kept version 3
# deliberately: everything the client reads - the header, the remote and
# keycard masks, the journal - is at unchanged offsets, and the paged text is
# game-side only. See FLOOR_PAGE_WINDOW_OFFSET below.
SEED_VERSION = 3
# 117 = 39 floors x MARKER_SLOT_COUNT. Two are placed on the floor; the third
# is carried by that floor's forced monster spawn.
LOCATION_COUNT = 117

# --- the AP marker descriptor -------------------------------------------------
#
# `[id, category, quality, status]`. The identity is a real Gift row - Cream -
# so that every vanilla table lookup keyed on (category, id) lands somewhere
# valid instead of indexing out of bounds, and so the two lookups that are
# *not* hooked still do the right thing by accident: category 0x0B has no
# suffix in the per-category name table at `0x800DD784`, where 0x06 had
# `　crystal`, and it draws the gift icon in menus. The town shop reached the
# same conclusion independently - see `UNFAMILIAR_ITEM_PROXY_DESCRIPTOR`.
#
# Quality carries the floor's check slot. It never renders, because status bit
# 0x80 is the game's *unidentified* flag and `append_item_display_name` skips
# the `+N` modifier for unidentified items.
#
# **0x8D, not 0xAD (2026-08-15).** The status was 0xAD from the first marker,
# and bit 0x20 of the flags byte is the game's *equipped* bit. On a ground
# marker that only ever printed "was taken off" on pickup; on the carrier's
# marker it was fatal - the death drop at 0x800AD24C skips any carried item
# with 0x20 set (that is how a Troll keeps its bow gun), so the first ride's
# carriers held the check and never dropped it. 0x8D is 0xAD minus that bit
# and nothing else: still unidentified, still a value no real item carries.
# Every emitter derives from this constant; the two binary compares in the
# gameplay payload are rewritten by tools/Rebuild-AdapGameplayPayload.py.
MARKER_ID = 0x01
MARKER_CATEGORY = 0x0B
MARKER_STATUS = 0x8D
assert not MARKER_STATUS & 0x20, "the equipped bit suppresses the carrier's death drop"
assert MARKER_STATUS & 0x80, "the unidentified bit hides the slot byte from the name"
MARKER_SLOT_COUNT = 3
# How the three slots are delivered: the payload's spawner places slots
# 0..MARKER_GROUND_SLOT_COUNT-1 on the floor; the carrier monster holds
# MARKER_CARRIER_SLOT and drops it on death (docs/game/monster-ai.md).
MARKER_GROUND_SLOT_COUNT = 2
MARKER_CARRIER_SLOT = 2
assert MARKER_GROUND_SLOT_COUNT + 1 == MARKER_SLOT_COUNT
assert MARKER_CARRIER_SLOT == MARKER_GROUND_SLOT_COUNT


def marker_descriptor_word(slot: int) -> int:
    """The four-byte AP marker `[id, category, slot, status]` as one LE word."""

    if slot not in range(MARKER_SLOT_COUNT):
        raise ValueError(f"marker slot {slot} out of range")
    return MARKER_ID | (MARKER_CATEGORY << 8) | (slot << 16) | (MARKER_STATUS << 24)

REMOTE_LOCATION_MASK_OFFSET = 0x10
FLOOR_KEYCARD_MASK_OFFSET = 0x20
ELEVATOR_RETURN_DESCRIPTOR_OFFSET = 0x28
ELEVATOR_RETURN_REQUEST_OFFSET = 0x2C

PERSISTENT_STATE_ADDRESS = 0x8001_5F94
PERSISTENT_STATE_MAGIC = 0x5653_4441  # "ADSV" in little-endian memory
# 3 (2026-08-05): the gold-granted counter - how many 5000-gold packages this
# save has banked. It is what lets gold cut the delivery line the way keycards
# do: eager granting needs a durable count to reconcile against the history,
# because gold is cumulative and cannot be re-derived the way a clearance level
# can.
# 4 (2026-08-15, the third floor check): the 10-byte packed tower mask becomes
# a 40-byte one-byte-per-floor journal, the separate 4-byte shop mask becomes
# the 16-byte town half of one unified location mask, the two intro-handshake
# bytes that squatted in the old mask's slack (+0x1A/+0x1B) get a field of
# their own, and the block moved DOWN to make room. Version 3 saves
# re-initialize on first boot; pre-release, with rooms regenerated per build,
# that costs nothing.
#
# **Down, not up.** The 64 bytes above `0x8001_5FC0` are fully allocated - ADSV,
# the shortcut carrier, the send-token trio, one spare word - and growing up is
# what burned 0.9.84. `0x8001_5F00..0x8001_5FBF` was 192 free bytes below (the
# bonus-floor state that once lived there moved to `0x801FF000`), so the base
# drops to `0x8001_5F94` at size `0x58` and ADSV still **ends** at
# `0x8001_5FEC`. Nothing above it moves. 148 bytes remain free below.
#
# Layout (docs/systems/third-floor-check.md §2 owns the table):
#   +0x00 magic            +0x04 version:size (lo/hi halves)
#   +0x08 seed signature   +0x10 tower journal, 40 B, byte = floor-1, bit = slot
#   +0x38 town journal, 16 B packed (bit = town check index)
#   +0x48 received count   +0x4C keycard level      +0x50 gold granted
#   +0x54 intro restore marker (byte)  +0x55 first-run ready (byte)  +0x56 spare
PERSISTENT_STATE_VERSION = 4
PERSISTENT_STATE_SIZE = 0x58
PERSISTENT_LOCATION_MASK_OFFSET = 0x10
# **One byte per floor, bit = slot.** Not a packed bit array. Every emitter that
# touches the journal used to compute `(floor - 1) * slots + slot` into a bit
# index and then split that into a byte and a bit - arithmetic that is unrolled
# in MIPS and therefore does NOT follow MARKER_SLOT_COUNT. Raising the count
# from two to three on 2026-08-15 silently broke the put-in guard's bound
# because of it. A byte per floor removes the multiply *and* the split: the byte
# index is `floor - 1` and the bit is the slot. Fewer instructions than before,
# at every site, and it cannot drift when the slot count changes again.
#
# Ceiling: 8 checks per floor, 39 floors = 312. We will not approach it.
PERSISTENT_TOWER_MASK_BYTES = 40        # 39 floors, one byte each, one spare
PERSISTENT_TOWER_MASK_FLOORS = 39
PERSISTENT_TOWN_MASK_BYTES = 16         # 128 town checks, bit-packed
PERSISTENT_LOCATION_MASK_BYTES = PERSISTENT_TOWER_MASK_BYTES + PERSISTENT_TOWN_MASK_BYTES
# The town half of the unified mask. Still called the shop mask because the shop
# is its only tenant today; it is no longer a separate field.
PERSISTENT_SHOP_MASK_OFFSET = PERSISTENT_LOCATION_MASK_OFFSET + PERSISTENT_TOWER_MASK_BYTES
PERSISTENT_RECEIVED_ITEM_COUNT_OFFSET = 0x48
PERSISTENT_KEYCARD_LEVEL_OFFSET = 0x4C
PERSISTENT_GOLD_GRANTED_OFFSET = 0x50
# The intro-restore handshake's two one-byte flags (town_receive owns their
# meaning). They lived at +0x1A/+0x1B - the slack after the old 10-byte mask -
# which the 40-byte journal now covers, so they have a word of their own.
PERSISTENT_INTRO_RESTORE_MARKER_OFFSET = 0x54
PERSISTENT_INTRO_FIRST_RUN_READY_OFFSET = 0x55
# The blacksmith's two temper levels (0..3 each), the two spare bytes of the
# intro-flags word: how far the equipment-shop smith may temper a WEAPON
# (swords, the Trained Wand) and a SHIELD - cap = blacksmith.CAP_BY_LEVEL[level]
# (0/10/20/40). Zeroed with the word at init. Raised by the client from the
# received-item history: every Red Sand is one weapon level, every Blue Sand
# one shield level (they never enter the bag) - docs/systems/blacksmith.md.
PERSISTENT_WEAPON_TEMPER_LEVEL_OFFSET = 0x56
PERSISTENT_SHIELD_TEMPER_LEVEL_OFFSET = 0x57
# The ball charger's level (0..3, one per White Sand received; how many charges
# she will add PER TOWN VISIT - docs/systems/fortune-teller.md section 5). NOT
# inside ADSV: the record is full and growing it re-initializes every save (the
# send-token trio sits beside ADSV for the same reason). The word immediately
# below the base, in the free durable run `0x80015F00..0x80015F93`; byte 0 is
# the level, byte 1 is how many of this visit's charges are already spent, the
# other two are spare.
# Both ADSV initializers zero it with the record (`_emit_persistent_state_zero_loop`)
# and the client writes it eagerly from the received history like the two
# temper levels. Any future ADSV growth (base moving down) absorbs it.
BALL_CHARGE_LEVEL_ADDRESS = PERSISTENT_STATE_ADDRESS - 4
# Charges bought at the charger since the last time Koh was in the tower. The
# allowance is per TOWN VISIT, so the counter is reset by the tower floor
# bootstrap helper - the same every-floor write that clears the shortcut
# carrier - rather than by anything in town: a player who walks in and out of
# her building keeps spending the same allowance, and one climb refills it.
BALL_CHARGE_USED_ADDRESS = BALL_CHARGE_LEVEL_ADDRESS + 1
assert BALL_CHARGE_USED_ADDRESS < PERSISTENT_STATE_ADDRESS
PERSISTENT_STATE_END_ADDRESS = PERSISTENT_STATE_ADDRESS + PERSISTENT_STATE_SIZE
assert PERSISTENT_STATE_END_ADDRESS == 0x8001_5FEC, (
    "ADSV's END is what the tenants above it (shortcut carrier, send tokens) "
    "are laid out against; grow it downward by moving the base."
)
assert PERSISTENT_STATE_ADDRESS >= 0x8001_5F00, "ADSV left the save-backed span."
assert PERSISTENT_SHOP_MASK_OFFSET % 4 == 0, "The town half must stay word-aligned."
assert (
    PERSISTENT_LOCATION_MASK_OFFSET + PERSISTENT_LOCATION_MASK_BYTES
    <= PERSISTENT_RECEIVED_ITEM_COUNT_OFFSET
), "The unified location mask overruns the received-item count."
assert PERSISTENT_INTRO_FIRST_RUN_READY_OFFSET < PERSISTENT_STATE_SIZE
# Everything the initializer zeroes, as whole words: the two journals, the
# three counters and the intro flags. Magic, version and signature are written
# explicitly.
PERSISTENT_ZEROED_WORD_OFFSETS = tuple(
    range(PERSISTENT_LOCATION_MASK_OFFSET, PERSISTENT_STATE_SIZE, 4)
)
assert PERSISTENT_KEYCARD_LEVEL_OFFSET in PERSISTENT_ZEROED_WORD_OFFSETS
assert PERSISTENT_INTRO_RESTORE_MARKER_OFFSET & ~3 in PERSISTENT_ZEROED_WORD_OFFSETS

SEED_INIT_ADDRESS = SEED_BLOCK_ADDRESS + 0x40
APPEND_LOCATION_MESSAGE_ADDRESS = SEED_BLOCK_ADDRESS + 0x140
RESOLVE_LOCATION_RENDER_ADDRESS = SEED_BLOCK_ADDRESS + 0x1E0
ELEVATOR_PROMPT_CALLBACK_ADDRESS = SEED_BLOCK_ADDRESS + 0x280
ELEVATOR_CHOICE_CALLBACK_ADDRESS = SEED_BLOCK_ADDRESS + 0x380
ELEVATOR_GATE_HANDLER_ADDRESS = SEED_BLOCK_ADDRESS + 0x430
ELEVATOR_RETURN_PROMPT_ADDRESS = SEED_BLOCK_ADDRESS + 0x4F0

# Shown when an ascent is refused at a clearance ceiling. It lives in the tail
# of the message-appender region, which is a bare eight-byte trampoline to the
# pooled composer at +0x630 and has been unused since pooling landed.
#
# **It used to point at `0x801FFF40` in the AP protocol mailbox and nothing ever
# wrote it**, so every refused ascent showed an empty dialogue box. The
# reverse-engineering notes flagged that as "locked feedback remains before the
# hook is final-quality"; this is that feedback. Keeping it in the generated
# page rather than the mailbox means it needs nobody to populate it at runtime.
ELEVATOR_LOCKED_MESSAGE_OFFSET = 0x1B0
ELEVATOR_LOCKED_MESSAGE_ADDRESS = SEED_BLOCK_ADDRESS + ELEVATOR_LOCKED_MESSAGE_OFFSET
ELEVATOR_LOCKED_MESSAGE_TEXT = "The elevator is locked."

# Per-floor placement-text paging (2026-08-09, replaces the pooled layout).
#
# Placement text used to be pooled: every distinct item and player name in the
# room, deduplicated, in a variable message region whose worst case (a
# long-name-heavy multiworld) could overflow the seed page and fail generation
# (docs/archive/future-updates.md measured 76% on a stress room). Now the seed
# page holds only the CURRENT floor's text in fixed-size slots, and each
# floor's slots are read from a per-seed 39-sector bank on disc during the
# floor build. Generation can never fail on text, any number of players fits
# (the pooled 16-recipient cap is gone), and most of the old pooled span comes
# back.
#
# The landing zone is the WINDOW itself: a CD read lands whole 2048-byte
# sectors, and [0x540, 0xD40) of the seed page is exactly 2048 bytes. Every
# page sector carries the window's static content (the composer code, the
# fragments, any future static tenants) byte-identical, plus that floor's
# header/records/slots - so the read "clobbers" the window with the same bytes
# it already held, and no external landing zone has to be certified. The one
# rule this creates: **space inside the window is for code and constants
# only** - anything mutable stored there would be reset by the next floor's
# read. The game itself never writes the seed page (RAM-watch proven,
# docs/adap-memory-safe-regions.md), so the only writer is our own loader.
#
# Everything from 0x540 to the tower-floor bootstrap helper is generator-owned,
# so these are free choices; no game-side address depends on them.
FLOOR_PAGE_WINDOW_OFFSET = 0x540
FLOOR_PAGE_WINDOW_SIZE = 0x800
FLOOR_PAGE_WINDOW_END = FLOOR_PAGE_WINDOW_OFFSET + FLOOR_PAGE_WINDOW_SIZE
FLOOR_PAGE_WINDOW_ADDRESS = SEED_BLOCK_ADDRESS + FLOOR_PAGE_WINDOW_OFFSET
FLOOR_PAGE_MAGIC = 0x5046_4441          # "ADFP" in little-endian memory
FLOOR_PAGE_VERSION = 1
FLOOR_PAGE_HEADER_OFFSET = 0x540        # magic u32, floor u16, version u16
FLOOR_PAGE_RECORDS_OFFSET = 0x548       # 2 x (item slot, player slot, form)
FLOOR_PAGE_FRAGMENTS_OFFSET = 0x554     # five fixed 12-byte fragment slots
FLOOR_PAGE_FRAGMENT_SLOT_SIZE = 0x0C
# An Archipelago slot name is capped at 16 characters upstream, so a player
# slot never truncates: 16 full-width chars * 2 B + terminator, rounded to
# the Send row's proven 0x24.
FLOOR_PAGE_PLAYER_SLOTS_OFFSET = 0x590  # 3 x 0x24
FLOOR_PAGE_PLAYER_SLOT_SIZE = 0x24
FLOOR_PAGE_PLAYER_SLOT_COUNT = 3
# Item names are whatever the multiworld produced and DO truncate, on encoded
# bytes (a full-width glyph is 2 B), never mid-glyph. The 25-character budget
# is inherited from the paging design note; the tower dialogue column width
# has not been measured empirically - revisit after a ride with long names.
FLOOR_PAGE_ITEM_SLOTS_OFFSET = 0x5FC    # 3 x 0x38
FLOOR_PAGE_ITEM_SLOT_SIZE = 0x38
FLOOR_PAGE_COMPOSER_OFFSET = 0x6A8      # composer body, reached by a trampoline
FLOOR_PAGE_COMPOSER_ADDRESS = SEED_BLOCK_ADDRESS + FLOOR_PAGE_COMPOSER_OFFSET
# 0x138 (2026-08-15, was 0x150): the composer is 312 bytes and the 24 spare
# were the cheapest room in the window for the carrier's awake call - every
# tenant after this offset is derived, so trimming the reservation repacked
# the forced-trap stub, the send-token block and the carrier region down 24
# bytes with one constant. The build asserts the body fits.
FLOOR_PAGE_COMPOSER_CAPACITY = 0x138
# 0x7E0..0xD40 is the window's free static space - the reclaimed room this
# design exists to produce. Claim from FLOOR_PAGE_FREE_OFFSET. First tenant
# (2026-08-09): the forced-trap stub (see FORCED_TRAP_STUB_OFFSET below); free
# space now starts at its end.
FLOOR_PAGE_FREE_OFFSET = FLOOR_PAGE_COMPOSER_OFFSET + FLOOR_PAGE_COMPOSER_CAPACITY
# The per-floor page loader lives OUTSIDE the window (the read must never
# rewrite the routine that is waiting on it, identical bytes or not).
FLOOR_PAGE_LOADER_OFFSET = 0xD40
FLOOR_PAGE_LOADER_ADDRESS = SEED_BLOCK_ADDRESS + FLOOR_PAGE_LOADER_OFFSET
# The debugging trace block that lived at +0xE3C during the 2026-08-09
# floor-1 hang investigation is retired (it convicted the construction-hook
# interposition and confirmed the animator-hook loader; nothing functional
# ever depended on it).
FLOOR_PAGE_LOADER_CAPACITY = 0x90  # exact fit; the build asserts overflow
# 0xD40+0x90 = 0xDD0 .. 0xE48: freed UNRESTRICTED space (mutable state OK),
# 120 bytes. The window's free static space is separate and larger (see
# FLOOR_PAGE_FREE_OFFSET). Claim from 0xDD0 upward.

# The loader is interposed on the ELEVATOR-ORB ANIMATOR's callback, not the
# construction hook: the fifth test build proved that adding even a minimal
# call frame inside the construction hook chain hangs the floor load (the
# loader itself traced clean - skip path, no read - and the hang persisted
# until the wrapper was restored to the direct bonus-gate call). The
# animator object is created by every floor build (construction list entry
# 0x800B1F00) and its callback runs per frame during gameplay; the loader
# gates its read on game mode 2 (tower gameplay, not a transition), so the
# read fires a few frames after the load completes - long before any item
# can be reached - in a context where the game itself streams freely.
FLOOR_PAGE_ANIMATOR_CALLBACK_ADDRESS = 0x800B_1E80
# The creator's `lui v0,0x800B / addiu v0,v0,0x1E80` pair at 0x800B1F18,
# raw disc offset derived from the bonus orb guard's proven mapping
# (raw 0x1C87640 <-> RAM 0x800B1EE8, single uncompressed site, ridden).
FLOOR_PAGE_ANIMATOR_HOOK_RAW_OFFSET = 0x1C8_7670
FLOOR_PAGE_ANIMATOR_HOOK_ORIGINAL = (0x3C02_800B, 0x2442_1E80)
FLOOR_PAGE_FLOOR_COUNT = 39             # floors 1..39; floor 40 has no checks
# The bank: one sector per floor, immediately after the bonus floor's code
# sector (0x1EF6D). Sectors 0x1EF6E..0x1EF94 are zero on the original disc
# (verified 2026-08-09; the disc has 126,946 sectors).
FLOOR_PAGE_BANK_LBA = 0x1EF6E

# The record's `form` byte. Bit 0 picks the sentence ("Found " / "Sent ");
# bit 7 marks one of this world's own disguised traps, which the pickup-
# message gate in the forced-trap stub reads to suppress the "Found ..."
# box for that placement ONLY. The two are independent: a trap is always
# local, so its form is 0x80, and the composer masks bit 0 to decide the
# sentence.
FLOOR_PAGE_FORM_REMOTE = 0x01
FLOOR_PAGE_FORM_TRAP = 0x80

# Slot order matches the record's `form` byte: 0 local, 1 remote.
FLOOR_PAGE_FRAGMENT_FOUND = FLOOR_PAGE_FRAGMENTS_OFFSET
FLOOR_PAGE_FRAGMENT_SENT = FLOOR_PAGE_FRAGMENTS_OFFSET + FLOOR_PAGE_FRAGMENT_SLOT_SIZE
FLOOR_PAGE_FRAGMENT_TO = FLOOR_PAGE_FRAGMENTS_OFFSET + FLOOR_PAGE_FRAGMENT_SLOT_SIZE * 2
FLOOR_PAGE_FRAGMENT_PERIOD = FLOOR_PAGE_FRAGMENTS_OFFSET + FLOOR_PAGE_FRAGMENT_SLOT_SIZE * 3
# The item-description builder's second line, matching the town shop's
# `<item> \n for <player>`. A bare 0x0A is the description renderer's line
# break - `append_encoded_text` copies any byte below 0x80 straight through.
FLOOR_PAGE_FRAGMENT_FOR = FLOOR_PAGE_FRAGMENTS_OFFSET + FLOOR_PAGE_FRAGMENT_SLOT_SIZE * 4

# Layout sanity: every piece inside the window, in order, no overlap.
assert FLOOR_PAGE_RECORDS_OFFSET + 3 * MARKER_SLOT_COUNT <= FLOOR_PAGE_FRAGMENTS_OFFSET
assert FLOOR_PAGE_FRAGMENTS_OFFSET + 5 * FLOOR_PAGE_FRAGMENT_SLOT_SIZE <= FLOOR_PAGE_PLAYER_SLOTS_OFFSET
assert (
    FLOOR_PAGE_PLAYER_SLOTS_OFFSET
    + FLOOR_PAGE_PLAYER_SLOT_COUNT * FLOOR_PAGE_PLAYER_SLOT_SIZE
    <= FLOOR_PAGE_ITEM_SLOTS_OFFSET
)
assert (
    FLOOR_PAGE_ITEM_SLOTS_OFFSET + MARKER_SLOT_COUNT * FLOOR_PAGE_ITEM_SLOT_SIZE
    <= FLOOR_PAGE_COMPOSER_OFFSET
)
assert FLOOR_PAGE_COMPOSER_OFFSET + FLOOR_PAGE_COMPOSER_CAPACITY <= FLOOR_PAGE_FREE_OFFSET
assert FLOOR_PAGE_FREE_OFFSET <= FLOOR_PAGE_WINDOW_END
assert FLOOR_PAGE_LOADER_OFFSET >= FLOOR_PAGE_WINDOW_END

# --- the forced trap ----------------------------------------------------------
#
# Multiworld trap items are tower-side effects on the RECEIVING player, so
# they cannot ride the ordinary item pipeline; instead the client asks the
# game to spring a trap on Koh by writing the trap id into a mailbox byte.
# This block is the game-side machinery only - nothing in the pool creates
# trap items yet, and nothing game-side ever writes the request byte except
# to consume it, so the whole system is inert until the byte is poked (the
# manual test) or a future client build writes it.
#
# The game's own machinery (disassembled 2026-08-09 from DUMPSK0802.ram.bin;
# docs/systems/forced-trap.md holds the full account):
#
#   `trigger_trap(a0 = trap id, a1 = actor, a2 = trap slot, a3 = forced)` at
#   0x800B627C dispatches through the handler table at 0x800DF2A8 (ids 0..19)
#   and is the single choke point all three vanilla trigger paths use
#   (walk-on at 0x80096000 and 0x800ADB5C, the deliberate menu Step at
#   0x800989DC). a3 is stored to the byte 0x800E3D40 before the handler runs;
#   nonzero means "deliberate step" and every handler then SKIPS its trigger-
#   chance roll - the Step command passes 1, and so do we: a forced trap
#   always fires. On a nonzero handler return the dispatcher itself stops
#   Koh's movement and raises the redraw flags; on zero it did nothing.
#
#   Handlers receive (actor, slot) into the floor's two parallel trap arrays:
#   descriptors at 0x800E3648 (32 slots x 4: id, category 0x15, quality,
#   status - 0x80 hidden) and records at 0x800E39C8 (32 slots x 0x18). The
#   trap roller's own record recipe (disassembled at 0x8001F0F8+): +0x6/+0x7
#   tile x/y, +0x10 and +0x12 the ground height from 0x800BCB04(x*64+32,
#   y*64+32), +0x8 a sprite handle, +0x14 flags, and a "trap here" mark - bit
#   0x20 ORed into the tile's status halfword in the 6-byte-per-tile grid -
#   via 0x8009A21C(x, y, 0x20). The sprite comes from the per-id table at
#   0x800DF258: a NEGATIVE value is an animation the roller attaches with
#   0x8003DB94(record, value, 0) and flags 0x0840 (0x0940 when the value
#   carries bit 0x20000000; the trigger path's record maintenance re-attaches
#   it and clears the hidden bit 0x800); a positive value is a plain sprite
#   handle stored to +0x8 as value|0x80000000 with flags 0x0800; zero (dud,
#   monster den) means no sprite. Bomb (7) reads the explosion's x/y/height
#   from the record through the slot argument - which is why a garbage record
#   detonates somewhere unseen - and monster den (19) reads its spawn count
#   from the descriptor's quality byte.
#
# The stub PLANTS A REAL TRAP at Koh's own tile - first free slot, the
# roller's exact field recipe, tile-grid mark and sprite included - then
# calls trigger_trap on it, forced, and leaves it there. The trap persists
# on the floor and re-triggers on a later step exactly like a native one
# (the next floor build clears it with the rest of the arrays). Planting
# rather than faking-and-scrubbing is what makes the bomb work: its
# explosion object resolves damage and visuals on LATER frames, reading the
# record after the handler returned - the first build scrubbed the record
# one instruction after the dispatcher call and the explosion evaporated.
# Presentation details: the descriptor is planted pre-revealed (status 0)
# and handle-type sprites get flags 0 (visible immediately) - the
# same-frame forced trigger makes the hidden->revealed dance pointless -
# while animated sprites are planted in the roller's hidden state 0x840 and
# revealed by the trigger's own native tail.
#
# The stub interposes on the receive dispatcher's hook (the base-patch `jal
# 0x801D9B70` at DUNGEON.BIN raw 0x1C5A970, runtime 0x8008AEB8, end of the
# neutral input handler's early-out gauntlet): request byte clear -> tail-jump
# to the dispatcher unchanged. Request pending -> guard (bonus floor inactive,
# Koh's action state ordinary idle 0x0E - the dispatcher's own delivery
# guard - and a free trap slot), plant, fire, and return the hook's displaced
# load `lhu v1,0xA2(s1)` directly, skipping item delivery on the frame a trap
# fired. Guard failures leave the byte set and retry next idle frame. Once
# planted the request byte is consumed REGARDLESS of the trigger's return:
# the trap is on the floor either way, armed underfoot, which is the
# delivery. An out-of-range id is dropped without planting.
#
# Trap ids (AD-DeRandomizer's table, matching the handler pointers): 1
# reversal, 2 slow, 3 warp, 4 go-up, 5 chaos, 6 dud, 7 bomb, 8 slam, 9 sleep,
# 10 blinder, 11 poison, 12 prison, 13 frog, 14 bump, 15 crack, 16 upheaval,
# 17 seal, 18 rust, 19 monster den. Which of these a multiworld pool should
# use is a generation-side decision for the item wiring, not this stub's;
# id 4 (go-up) additionally arms the ADAP bonus floor exactly as a native
# go-up trap does.
TRAP_DISPATCH_ADDRESS = 0x800B_627C
TRAP_HANDLER_TABLE_ADDRESS = 0x800D_F2A8
TRAP_SPRITE_TABLE_ADDRESS = 0x800D_F258
TRAP_DESCRIPTOR_ARRAY_ADDRESS = 0x800E_3648
TRAP_RECORD_ARRAY_ADDRESS = 0x800E_39C8
TRAP_SLOT_COUNT = 32
TRAP_RECORD_SIZE = 0x18
# The roller's helpers, reused by the plant: ground height at a world
# position, the tile-grid "trap here" mark, and the animation attach (the
# same call the trigger path's record maintenance makes mid-gameplay).
TRAP_GROUND_HEIGHT_ADDRESS = 0x800B_CB04
TRAP_TILE_MARK_ADDRESS = 0x8009_A21C
TRAP_SPRITE_ATTACH_ADDRESS = 0x8003_DB94
TRAP_TILE_MARK_BIT = 0x20
# `ground_height(world_x, world_y, z_reference)` takes THREE arguments, and
# the third is not optional: it lands in scratchpad `0x1F80014C` and selects
# which surface the probe resolves to. All 122 call sites in the overlay set
# it - either `-1024` ("from below everything", what the floor builder and
# the TRAP ROLLER itself use) or `actor_height - 32` for probes near a
# specific actor. **Leaving it to whatever the caller happened to hold is
# what made the bomb trap fire into nowhere for two builds**: the bomb is
# the only handler that reads the record's height back (`+0x12`), so a junk
# height put its explosion object off the play field - no sprite, and the
# actor lookup at that position found nobody, so no damage. The sound is
# emitted unconditionally afterwards, which is why the trap still announced
# itself. Match the roller exactly.
TRAP_GROUND_HEIGHT_PROBE_Z = -1024
# The record array must stay clear of the dispatcher's forced-step byte.
assert TRAP_RECORD_ARRAY_ADDRESS + TRAP_SLOT_COUNT * TRAP_RECORD_SIZE <= 0x800E_3D40
# The planted trap's descriptor quality (monster den reads it as its spawn
# count; the native roller gives its traps `(rand & 7) | 4` = 4..7).
FORCED_TRAP_QUALITY = 4
# The request byte: client-writable. Trap id 1..19; zero means no request.
# Consumed by the stub on a successful trigger.
#
# **It is NOT in the ADAP mailbox.** It was, at `+0xB0`, on the reasoning
# that `+0xAC` (presentation) was the last defined field - but `+0xB0..
# +0x100` is the **ADGT tower-gift record**, and `+0xB0` is its magic. The
# two destroyed each other: a queued trap id overwrote the magic's first
# byte, so the commit routine saw no record and re-initialized over an
# unacked send; and a committed send put `'A'` (0x41) in the request byte,
# which the stub read as an out-of-range id and cleared - corrupting the
# magic right back. Shipped in 0.9.89-0.9.93 and never seen, because every
# trap test seed was SOLO and the Send row is skipped entirely when the
# room holds no other Azure Dreams player.
#
# Home now: the certified window's one genuinely unclaimed tail, past both
# the mailbox and the town receive queue (docs/game/memory-map.md §2a).
FORCED_TRAP_REQUEST_ADDRESS = 0x801D_A6D8
# The receive dispatcher's hook word in DUNGEON.BIN (a 4-byte base-patch
# record), retargeted per-seed at the stub. Raw offset verified against the
# packaged base ppf by build_player_ppf before it edits.
RECEIVE_HOOK_RAW_OFFSET = 0x1C5_A970
RECEIVE_HOOK_ORIGINAL_WORD = 0x0C07_66DC  # jal 0x801D9B70
# The stub itself is code-and-constants only, so it lives in the floor-page
# window's free static space; every page sector carries it byte-identically,
# exactly like the composer.
FORCED_TRAP_STUB_OFFSET = FLOOR_PAGE_FREE_OFFSET
FORCED_TRAP_STUB_ADDRESS = SEED_BLOCK_ADDRESS + FORCED_TRAP_STUB_OFFSET
# Trimmed 0x300 -> 0x2B0 on 2026-08-14. The stub assembles to 0x2A8; the live
# seed block showed 88 zero bytes at +0xA48 and the tower had nowhere else to go.
# Everything after this in the page is derived from it, so the trim moves the
# send-token block and the floor-page free space down together - which is the
# point: it returns 80 bytes to FLOOR_PAGE free static. Keep >= 0x2A8.
FORCED_TRAP_STUB_CAPACITY = 0x2B0
assert FORCED_TRAP_STUB_OFFSET + FORCED_TRAP_STUB_CAPACITY <= FLOOR_PAGE_WINDOW_END
# Koh's action-state offset and the ordinary-idle id, shared with the receive
# dispatcher's guard (Rebuild-AdapGameplayPayload.py documents why 0x17, the
# post-fidget idle, is deliberately NOT accepted).
# --- the forced trap while Koh has something in hand -------------------------
#
# Reported from play 2026-08-18: a trap picked up while HOLDING an item (the
# front menu's Have verb, or a lifted monster) did not spring until the item
# left his hands. The obvious reading - "the idle-state guard is too strict" -
# is wrong, and worth writing down because it cost an afternoon: the guard
# passes. What actually happens is that the game swaps Koh's control handler.
#
#   * `player_control_on_foot` (0x8008ACDC) is the state-0x0E handler, and it
#     is the ONLY function containing the receive dispatcher's call site at
#     0x8008AEB8 - which is where the forced-trap stub is interposed.
#   * While `unit+0x1C & 0x100000` is set (something in hand; the held object
#     is at `unit+0x124`, the descriptor at 0x80081484 - see
#     `cancel_pending_in_hand_item`), the handler at `unit+0x8C` is
#     `player_control_carrying` (0x8008EAC8) instead: an almost line-for-line
#     twin with no such call. It even forces `+0x9A = 0x0E` at its top, so the
#     action-state guard would have passed if anything had reached it.
#
# So the stub never ran, and the request byte sat pending until Koh put the
# item down. The same accident is why INCOMING ITEMS pause while holding -
# emergent behaviour, not a designed rule, and one the player likes (a full
# bag can hold an item out, take a floor item with Put in, and let the
# delivery land afterwards). Keeping it is the whole reason this trampoline
# checks the request byte itself instead of just calling the stub: the stub's
# no-request path tail-jumps to the receive dispatcher, and reaching THAT from
# here would start delivering items into a held hand.
#
# The hook is the carrying handler's exact analogue of the on-foot site: same
# position in the frame (past the hit-reaction, suspend, sleep and level-up
# gates), `s1` still Koh's actor, and a `nop` already in the delay slot.
#
#   0x8008ECCC  lhu v0,0x2(s0)   <- becomes `jal trampoline`; s0 = 0x80083460
#   0x8008ECD0  nop              <- the jal's delay slot, unchanged
#   0x8008ECD4  andi v0,v0,0x4   <- what the trampoline returns to
#
# The displaced load is reproduced absolutely (s0 is set at 0x8008EB84 and
# never rewritten before the site), so the trampoline depends on no register
# but its own.
#
# Resident home: the SDK's dead RCS stamp `"$Id: bios.c,v 1.86 ..."` at
# 0x80033578, 52 bytes of .rdata whose only reference is its own unreferenced
# `rcsid` pointer at 0x8007B1A8 - the same donor class as the intr.c string
# under BANK_B_CANARY_TEST, and SLUS-resident, so it is present whether or not
# an overlay is loaded. It is only ever REACHED from tower code, and it only
# calls into the seed page when the request byte is nonzero, which the client
# only ever writes during a tower trip.
CARRYING_TRAP_TRAMPOLINE_ADDRESS = 0x8003_3578
CARRYING_TRAP_TRAMPOLINE_CAPACITY = 52
CARRYING_TRAP_TRAMPOLINE_DONOR_PREFIX = b"$Id: bios.c,v 1.86"
CARRYING_TRAP_HOOK_ADDRESS = 0x8008_ECCC
CARRYING_TRAP_HOOK_ORIGINAL_WORD = 0x9602_0002   # lhu v0,0x2(s0)
CARRYING_TRAP_RETURN_ADDRESS = 0x8008_ECD4
# What the displaced load reads: the halfword at s0+2, s0 = 0x80083460.
CARRYING_TRAP_DISPLACED_ADDRESS = 0x8008_3462

ACTOR_ACTION_STATE_OFFSET = 0x9A
ACTOR_ACTION_STATE_IDLE = 0x0E
ACTOR_HEIGHT_OFFSET = 0x88
ACTOR_PICKUP_FLAG_OFFSET = 0xA2
# The descriptor of the item this pickup is about, set on EVERY pickup (the
# payload's collect hook points it at its own copy for AP markers). Its
# readers have no null guard - `0x8008D8DC` does `lw v0,0xBC` then
# `lbu v1,0x1(v0)` - so it is read here, never cleared.
ACTOR_PICKUP_DESCRIPTOR_OFFSET = 0xBC
# `+0xA2` is the actor's PENDING-EVENT halfword, not just the pickup flag.
# The neutral input handler dispatches it - and the dispatch order is what
# makes forcing a trap from idle delicate (disassembled 2026-08-09 from
# `0x8008AD10`):
#
#     lbu v1,0x9A(s1)          action state
#     beq v1,0x0E -> 0x8008ADA0    ordinary idle: LEAVES, never sees +0xA2
#     beq v1,0x17 -> 0x8008AD90    post-fidget idle: same
#     lhu v0,0xA2(s1) / andi 0x100 -> the BUMP dispatch (0x8008D94C)
#     ...otherwise reset state to 0x0E and CLEAR bit 0x100
#
# So a deferred event armed while Koh is in `0x0E` is never dispatched and
# never cleared. Bump (14) is the only pooled trap that works this way: it
# sets bit `0x100` and increments the outstanding-animation counter at
# `0x8008346A`, expecting the handler to run the animation and decrement it.
# Forced from idle, the counter goes up and never comes down - the game
# waits forever for an animation that was never started, which is the
# semi-softlock reported 2026-08-09 (input mostly ignored; forcing some
# other animation clears it). A natural walk-on bump works because it fires
# mid-movement, when the state is neither idle value.
ACTOR_EVENT_PICKUP = 0x0080
ACTOR_EVENT_BUMP = 0x0100
# The state the bump handler itself installs (`0x8008D94C`: `sb 0x24,0x9A`,
# then it clears bit 0x100 and starts the animation). Writing it is what
# gets the handler to look at `+0xA2` at all; the handler then takes over
# and the value becomes true a frame later. Self-healing if the flag is
# somehow gone by then - the `0x8008AD50` path just resets to idle.
ACTOR_ACTION_STATE_BUMPED = 0x24

# The actor's linked display object, holding his tile coordinates - the same
# `[actor-0x14] -> +0x24/+0x25` read the Step command and the go-up handler
# make.
ACTOR_LINKED_OBJECT_OFFSET = -0x14
LINKED_OBJECT_X_OFFSET = 0x24
LINKED_OBJECT_Y_OFFSET = 0x25
# The bonus floor's active byte (bonus_floor.py's elevator commentary; the
# collapse machinery owns the floor while it is set). A forced trap there
# could ascend, warp or throw Koh mid-collapse, so the stub defers - the
# request survives and fires on the next ordinary floor.
BONUS_ACTIVE_FLAG_ADDRESS = 0x801D_A101

# --- the tower players-menu Send row's label strip ---------------------------
#
# The seventh menu row (docs/tower-send-design.md, `tower_send.py`) needs a
# 56x16 4bpp label strip in VRAM. The sheet page that holds the six vanilla
# strips has no free 56x16 window at the two V values the row's record forces,
# so the record points at free space in the icon page instead - measured free
# in two independent tower states - and the strip is uploaded there on every
# menu open.
#
# **This is a rented address, not a home.** It costs the pooled message region
# 576 bytes, which is dynamic space that scales with the number of distinct
# item and player names in the room. The region keeps its loud overflow guard
# (see `store()` below), so a room that no longer fits fails generation instead
# of truncating a name. `docs/tower-send-strip-relocation.md` is the recipe for
# moving both blocks somewhere permanent if that guard ever fires.
# Send-mode machinery (content built by `tower_send`, which may import both
# this module and alternate_pickup; the region is reserved here because this is
# where the seed page's layout lives).
# Grown 2026-08-07 for the commit routine (0x120 -> 0x320); the region ends
# flush against the name table, so growth comes out of the pooled message
# region above it - the overflow guard below keeps that honest.
SEND_ROW_CODE_OFFSET = 0xE48
SEND_ROW_CODE_CAPACITY = 0x320
SEND_ROW_CODE_ADDRESS = SEED_BLOCK_ADDRESS + SEND_ROW_CODE_OFFSET
assert SEND_ROW_CODE_OFFSET + SEND_ROW_CODE_CAPACITY == 0x1168  # names follow
# An Archipelago slot name can be sixteen characters; full-width CP932 is two
# bytes each plus a terminator, so a slot has to be 33 bytes. Rounded to 36 to
# keep every entry word-aligned, which the label routine's index multiply wants.
SEND_ROW_NAME_SLOT_SIZE = 0x24
SEND_ROW_NAMES_OFFSET = 0x1168
SEND_ROW_NAMES_CAPACITY = 3 * SEND_ROW_NAME_SLOT_SIZE
SEND_ROW_NAMES_ADDRESS = SEED_BLOCK_ADDRESS + SEND_ROW_NAMES_OFFSET

# The rail's foot cap, baked from the vanilla sheet. It has to be re-uploaded
# rather than blitted from V=0x50, because the row below it is where the cap
# now goes: on the second menu open a blit would copy the SHAFT it had already
# written there, and the rail lost its cap - which is exactly what the first
# rail build did.
SEND_RAIL_CAP_OFFSET = 0x11D4
SEND_RAIL_CAP_ADDRESS = SEED_BLOCK_ADDRESS + SEND_RAIL_CAP_OFFSET
SEND_RAIL_CAP_SIZE = 128

SEND_ROW_UPLOAD_OFFSET = 0x1254
SEND_ROW_UPLOAD_ADDRESS = SEED_BLOCK_ADDRESS + SEND_ROW_UPLOAD_OFFSET
# Shrunk 0x110 -> 0xB0 on 2026-08-08 (the single-target uploader is 176
# bytes) to give the assigner the room the row swap needs.
SEND_ROW_UPLOAD_CAPACITY = 0xB0
SEND_ROW_ASSIGNER_OFFSET = 0x1304
SEND_ROW_ASSIGNER_ADDRESS = SEED_BLOCK_ADDRESS + SEND_ROW_ASSIGNER_OFFSET
SEND_ROW_ASSIGNER_CAPACITY = 0x88
assert SEND_ROW_UPLOAD_OFFSET + SEND_ROW_UPLOAD_CAPACITY == SEND_ROW_ASSIGNER_OFFSET
assert SEND_ROW_ASSIGNER_OFFSET + SEND_ROW_ASSIGNER_CAPACITY == 0x138C
SEND_ROW_STRIP_OFFSET = 0x138C
SEND_ROW_STRIP_ADDRESS = SEED_BLOCK_ADDRESS + SEND_ROW_STRIP_OFFSET
SEND_ROW_STRIP_SIZE = 448
# The pooled message region this used to bound is gone (per-floor paging,
# 2026-08-09); the Send-row code block is now bounded by the floor-page
# loader above it instead.

# Send mode: set when the player enters the item list through the Send row,
# cleared every time the players menu is built, when the Items or Feet rows
# dispatch (the relocated handler table routes them through flag-clearing
# stubs), and when a send commits. Two words in the retired card driver's
# tail, above everything alternate_pickup and the row record use. The commit
# routine relies on CONTROLLER being FLAG + 4 to clear both with one base.
SEND_MODE_FLAG_ADDRESS = 0x8004_EE44
SEND_CONTROLLER_ADDRESS = 0x8004_EE48
assert SEND_CONTROLLER_ADDRESS == SEND_MODE_FLAG_ADDRESS + 4

# --- the tower gift mailbox (`ADGT`) -----------------------------------------
#
# The tower twin of Nada's `ADGS` record, same field layout, distinct magic so
# the client can never mistake one for the other (the town address is stale
# bytes in tower mode and vice versa). It lives in the unused tail of the
# `ADAP` protocol mailbox's 0x100-byte structure - the last defined field is
# `+0xAC`, so `+0xB0..+0x100` (80 bytes) was reserved-but-idle certified space
# (docs/tower-send-design.md, "Mailbox placement"). Written by the seed-page
# commit routine, polled and acked by the client; the commit initializes the
# sequence/ack pair itself the first time it finds the magic absent, and
# refuses to overwrite an unacked send (the item stays in the bag).
TOWER_GIFT_MAILBOX_ADDRESS = 0x801D_A5F0
TOWER_GIFT_MAILBOX_MAGIC = int.from_bytes(b"ADGT", "little")
TOWER_GIFT_MAILBOX_SEQUENCE_OFFSET = 0x04
TOWER_GIFT_MAILBOX_ACK_OFFSET = 0x08
TOWER_GIFT_MAILBOX_TARGET_OFFSET = 0x0C
TOWER_GIFT_MAILBOX_COUNT_OFFSET = 0x10
TOWER_GIFT_MAILBOX_ITEMS_OFFSET = 0x14
TOWER_GIFT_MAILBOX_MAX_ITEMS = 15
TOWER_GIFT_MAILBOX_SIZE = (
    TOWER_GIFT_MAILBOX_ITEMS_OFFSET + 4 * TOWER_GIFT_MAILBOX_MAX_ITEMS
)
# The record must stay inside the ADAP mailbox structure (whose own fields end
# at +0xAC) and must not spill into the town receive queue at 0x801DA640.
assert TOWER_GIFT_MAILBOX_ADDRESS >= HIGH_MAILBOX_ADDRESS + 0xB0
assert (
    TOWER_GIFT_MAILBOX_ADDRESS + TOWER_GIFT_MAILBOX_SIZE
    <= HIGH_MAILBOX_ADDRESS + 0x100
)
# ...and nothing else may sit inside it. The forced-trap request byte did,
# on its magic, for four world versions - see FORCED_TRAP_REQUEST_ADDRESS.
assert not (
    TOWER_GIFT_MAILBOX_ADDRESS
    <= FORCED_TRAP_REQUEST_ADDRESS
    < TOWER_GIFT_MAILBOX_ADDRESS + TOWER_GIFT_MAILBOX_SIZE
), "The forced-trap request byte is back inside the ADGT record."
# Past the mailbox AND past the town receive queue's 0x98 bytes behind it,
# inside the certified window (which ends at 0x801DA700).
assert FORCED_TRAP_REQUEST_ADDRESS >= HIGH_MAILBOX_ADDRESS + 0x100 + 0x98
assert FORCED_TRAP_REQUEST_ADDRESS < 0x801D_A700

# The display-order pointer table the item list renders from; freeing a bag
# slot without dropping its pointer is the stale-pointer crash Nada's send
# commit already learned the hard way. (That commit was deleted with her send
# menu on 2026-08-11 and carried these same two values; the hazard it hit is
# recorded in docs/systems/nada-send.md §7 step 5.)
ORDER_TABLE_ADDRESS = 0x8001_029C
ORDER_TABLE_WORDS = 21
# The physical bag: twenty 4-byte descriptors. The send guards use the range
# to tell a bag item from the in-hand/at-feet/ground descriptors, which keep
# their vanilla verbs (town_shop and town_warp carry the same base address).
INVENTORY_DESCRIPTORS_ADDRESS = 0x8001_0248
INVENTORY_DESCRIPTOR_COUNT = 20

ITEMS_SESSION_OPENER_ADDRESS = 0x8001_8AB0
# Verb-menu controller fields, measured off the dispatch at 0x8001D73C:
#   `lw v0,0x1C(a0)` then `lbu v0,0x54(v0+a0)` - selection indexes the row
#   array, so a confirmed row is [controller+0x1C] and its id [+0x54+that].
VERB_SELECTION_OFFSET = 0x1C
VERB_ROW_COUNT_OFFSET = 0x28
VERB_ROW_ARRAY_OFFSET = 0x54
VERB_DESCRIPTOR_OFFSET = 0x68
# Descriptor byte +1 is the category and +3 the flags; 0x13 is a familiar and
# 0x20 is the game's own "equipped" bit. Neither may be sent - a familiar is
# not inventory, and unequipping is the player's job.
FAMILIAR_CATEGORY = 0x13
EQUIPPED_FLAG = 0x20

# Where the strip lands.
#
# **Measured the hard way (2026-08-07), understood later the same day.** A
# build that moved the whole record to free space in the icon page - tpage
# 0x000F, U=0xA0 - drew the label 110 pixels left of the scroll. That was
# read at the time as "texture coordinates feed screen position"; the real
# mechanism (see SEND_ROW_VRAM_TARGETS below) is that the record's +2/+3
# pair IS the screen offset, sign-extended - 0xA0 reads as -96 - and the
# move rewrote it along with the texture pair. The record therefore keeps
# +2/+3 = (0x04,0x30), row 6's screen slot, and only the TEXTURE pair names
# pixels: the sheet page (448,256) at U=0x90/V=0x60 - the static slot the
# JP eighth row (転送) would have owned. We overwrite that leftover.
#
# 56 texels = 14 halfwords at 4bpp; a page's U=n texel is x = base + n/4.
SEND_ROW_SHEET_PAGE_X = 448
SEND_ROW_SHEET_PAGE_Y = 256
SEND_ROW_VRAM_HALFWORDS = 56 // 4
SEND_ROW_VRAM_ROWS = 16
# (x, y) in VRAM halfword/scanline coordinates: ONE slot, the record's texture
# pair (+8/+9). The record's OTHER pair (+2/+3) is not a texture coordinate at
# all and must not be painted.
#
# Decoded 2026-08-07 from the resident sprite-record renderer at 0x800453E0
# (the routine that consumes every 12-byte record in this family, menu rows
# and the item-menu reticule alike): bytes +2/+3 are SIGN-EXTENDED
# (`sll 24/sra 24`) into vertex X/Y - they are the sprite's screen offset,
# negated under the kind byte's mirror flags - and only +8/+9 ever reach the
# GPU as texture UV. The reticule's records prove it structurally: its four
# corner records share one 8x8 texture cell at +8/+9 and differ only in
# +2/+3 offsets (F0,F0)/(00,F0)/(F0,00)/(00,00) and mirror bits. The sheet
# page confirms it pictorially: there is NO second, brighter label strip at
# U=0x04 for any vanilla row - the "pop-out" is the same quad slid and
# brightened by the per-frame drive (RGB popout*5+0x58 + slide-X).
#
# Every earlier experiment changed both pairs at once (whole records were
# borrowed or moved), which is how "+2/+3 is the pop-out strip's UV" survived
# three builds: both models fit every observation until the corner offsets
# and the renderer were read. The uploads to (0x04,0x30) that this constant
# used to carry painted texels NOTHING samples - their only visible effect
# was distorting the reticule's corner cells (U=0x08..0x48, V=0x30..0x38)
# and the U=0..8 UI strips, which live there as ordinary texture referenced
# by their own records' +8/+9. Dropping that upload IS the reticule fix.
SEND_ROW_VRAM_TARGETS = (
    (SEND_ROW_SHEET_PAGE_X + 0x90 // 4, SEND_ROW_SHEET_PAGE_Y + 0x60),  # texture
)
# The vanilla players-menu creator, and the call site we displace to reach it.
PLAYERS_MENU_CREATOR_ADDRESS = 0x8005_0A00
PLAYERS_MENU_CREATOR_CALL_ADDRESS = 0x8005_0C1C
LOAD_IMAGE_ADDRESS = 0x8006_72D8
MOVE_IMAGE_ADDRESS = 0x8006_73A0

# --- the cursor rail ---------------------------------------------------------
#
# The gold rod down the menu's left edge - the rail the cog cursor rides - is
# ONE sprite: slot 0's record at 0x80077E30, sheet UV (0x80,0x00), 16 x 96,
# i.e. exactly six rows. Its art is a curled top, a repeatable shaft, and a
# foot cap in the last row, so a seventh row leaves our label railless.
#
# Extending it is two VRAM blits plus one byte: move the foot cap down a row,
# refill the row it left with a slice of plain shaft, and grow the record's
# height from 0x60 to 0x70. The rail is 16 texels wide at U=0x80, which is a
# whole number of halfwords (4), so MoveImage can address it exactly.
RAIL_RECORD_ADDRESS = 0x8007_7E30
RAIL_HEIGHT_OFFSET = 11               # the record's height byte
RAIL_VANILLA_HEIGHT = 0x60            # six rows
RAIL_EXTENDED_HEIGHT = 0x70           # seven
RAIL_VRAM_X = SEND_ROW_SHEET_PAGE_X + 0x80 // 4
RAIL_VRAM_HALFWORDS = 16 // 4
# (source y, destination y) - cap first, then the shaft slice into its old row.
# Only the shaft slice is a blit, and it reads V=0x40 which nothing writes, so
# it is idempotent across every menu open. The cap is uploaded from the baked
# asset instead - see SEND_RAIL_CAP_OFFSET for why blitting it was wrong.
RAIL_BLITS = (
    (SEND_ROW_SHEET_PAGE_Y + 0x40, SEND_ROW_SHEET_PAGE_Y + 0x50),
)
RAIL_CAP_VRAM_Y = SEND_ROW_SHEET_PAGE_Y + 0x60
# The record the assigner installs, and the call the stub displaces.
SEND_ROW_RECORD_ADDRESS = 0x8004_EE38
SEND_ROW_ASSIGNER_CONTINUATION = 0x8004_FFF4


def build_send_row_upload() -> bytes:
    """Upload the Send strip to its VRAM home, then run the creator.

    Reached by displacing the `jal 0x80050A00` that builds the players menu,
    so it runs once per menu open - which also means a transient overwrite of
    that VRAM heals on the next open. The creator's own two arguments are
    preserved across the GPU calls and it is tail-jumped with `ra` intact, so
    it still returns to its original caller.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -0x28),                       # addiu sp,sp,-0x28
        _i(0x2B, 29, 31, 0x20),                        # sw ra,0x20(sp)
        _i(0x2B, 29, 4, 0x24),                         # sw a0,0x24(sp)
        _i(0x2B, 29, 5, 0x18),                         # sw a1,0x18(sp)
    )
    # RECT is four shorts: x, y, w, h - two words on the stack.
    size_word = (SEND_ROW_VRAM_ROWS << 16) | SEND_ROW_VRAM_HALFWORDS
    for x, y in SEND_ROW_VRAM_TARGETS:
        position_word = (y << 16) | x
        b.emit(
            _i(0x0F, 0, 8, position_word >> 16),        # lui t0,y
            _i(0x0D, 8, 8, position_word & 0xFFFF),     # ori t0,t0,x
            _i(0x2B, 29, 8, 0),                         # sw t0,0x00(sp)
            _i(0x0F, 0, 9, size_word >> 16),            # lui t1,h
            _i(0x0D, 9, 9, size_word & 0xFFFF),         # ori t1,t1,w
            _i(0x2B, 29, 9, 4),                         # sw t1,0x04(sp)
            _i(0x09, 29, 4, 0),                         # addiu a0,sp,0
        )
        _load_address(b, 5, SEND_ROW_STRIP_ADDRESS)     # a1 = strip
        b.emit(_j(0x03, LOAD_IMAGE_ADDRESS), 0)         # jal LoadImage

    # The rail's new foot cap, from the baked asset (idempotent by nature).
    cap_position = (RAIL_CAP_VRAM_Y << 16) | RAIL_VRAM_X
    cap_size = (SEND_ROW_VRAM_ROWS << 16) | RAIL_VRAM_HALFWORDS
    b.emit(
        _i(0x0F, 0, 8, cap_position >> 16),
        _i(0x0D, 8, 8, cap_position & 0xFFFF),
        _i(0x2B, 29, 8, 0),
        _i(0x0F, 0, 9, cap_size >> 16),
        _i(0x0D, 9, 9, cap_size & 0xFFFF),
        _i(0x2B, 29, 9, 4),
        _i(0x09, 29, 4, 0),
    )
    _load_address(b, 5, SEND_RAIL_CAP_ADDRESS)
    b.emit(_j(0x03, LOAD_IMAGE_ADDRESS), 0)

    # Extend the cursor rail by one row: shaft into the row the cap left.
    rail_size_word = (SEND_ROW_VRAM_ROWS << 16) | RAIL_VRAM_HALFWORDS
    for source_y, destination_y in RAIL_BLITS:
        source_word = (source_y << 16) | RAIL_VRAM_X
        b.emit(
            _i(0x0F, 0, 8, source_word >> 16),          # lui t0,srcY
            _i(0x0D, 8, 8, source_word & 0xFFFF),       # ori t0,t0,srcX
            _i(0x2B, 29, 8, 0),                         # sw t0,0x00(sp)
            _i(0x0F, 0, 9, rail_size_word >> 16),       # lui t1,h
            _i(0x0D, 9, 9, rail_size_word & 0xFFFF),    # ori t1,t1,w
            _i(0x2B, 29, 9, 4),                         # sw t1,0x04(sp)
            _i(0x09, 29, 4, 0),                         # addiu a0,sp,0
            _i(0x09, 0, 5, RAIL_VRAM_X),                # addiu a1,zero,dstX
            _j(0x03, MOVE_IMAGE_ADDRESS),               # jal MoveImage
            _i(0x09, 0, 6, destination_y),              # (delay) a2 = dstY
        )
    # Send mode lasts exactly one trip through the item list. Clearing it here
    # - on every players-menu build - means choosing Items after a send shows
    # ordinary verbs again, without needing to find where the item session
    # ends.
    _load_address(b, 8, SEND_MODE_FLAG_ADDRESS)
    b.emit(_i(0x2B, 8, 0, 0))                          # sw zero,0(t0)
    b.emit(
        _i(0x23, 29, 31, 0x20),                        # lw ra,0x20(sp)
        _i(0x23, 29, 4, 0x24),                         # lw a0,0x24(sp)
        _i(0x23, 29, 5, 0x18),                         # lw a1,0x18(sp)
        _j(0x02, PLAYERS_MENU_CREATOR_ADDRESS),        # j creator (tail call)
        _i(0x09, 29, 29, 0x28),                        # (delay) addiu sp,sp,0x28
    )
    payload = b.build()
    if len(payload) > SEND_ROW_UPLOAD_CAPACITY:
        raise ValueError(
            f"The Send-row upload routine needs {len(payload)} bytes and has "
            f"{SEND_ROW_UPLOAD_CAPACITY}."
        )
    return payload


# The window-object slots the seventh row uses, as array indices.
SEND_ROW_TEXT_SLOT_OFFSET = 0x1C          # slot 7, the label object
SEND_ROW_CURSOR_SLOT_OFFSET = 0x20        # slot 8, the cursor - chain anchor
# Per-row pop-out animation bytes live at state+0xA4, one per row, and the
# per-frame drive turns them into BOTH the label colour (`b*5 + 0x58`) and its
# X offset (`b + 0x1E`, stored at the paired transform's +0x08). Row 6's byte
# is state+0xAA, which the vanilla creator never initialises - it held garbage,
# so the label appeared at a wild X and drifted as the ramp converged. This is
# the only piece of eighth-row state Konami left uninitialised.
SEND_ROW_ANIMATION_BYTE_OFFSET = 0xA4 + 6


# The Feet/Hand label records the vanilla assigner swaps into slot 6 on the
# held-item flag (measured at 0x8005073C: flag NONZERO -> the Hand record).
# Since the 2026-08-08 row swap they belong to SLOT 7 - the bottom row -
# and tower_send patches their position byte (+3) from 0x20 to 0x30 so the
# label draws on row 6's line. Their texture pairs are untouched.
FEET_LABEL_RECORD_ADDRESS = 0x8007_7F80
HAND_LABEL_RECORD_ADDRESS = 0x8007_7F8C
HELD_ITEM_FLAG_ADDRESS = 0x8008_1485
SEND_ROW_FEET_SLOT_OFFSET = 0x18          # slot 6, the row-5 label object
assert (FEET_LABEL_RECORD_ADDRESS >> 16) == (HAND_LABEL_RECORD_ADDRESS >> 16)


def build_send_row_assigner_stub() -> bytes:
    """Swap the bottom two rows' records, splice slot 7 into the render
    chain, zero its animation byte, then run the displaced call.

    Runs in place of the assigner's `jal 0x8004FFF4` at `0x8005078C`, where
    `s0` is the window-object pointer array and `s1` is the menu state -
    AFTER the vanilla assigner stored the Feet/Hand record into slot 6, so
    both bottom rows are (re)assigned here:

    * slot 6 (row 5, the wrap-to-bottom-minus-one line) gets the SEND
      record - the row order the speedrun habit expects, Feet last;
    * slot 7 (row 6, the bottom line) gets the Feet/Hand record, chosen by
      the same held-item flag test the displaced vanilla logic used
      (0x80081485 nonzero -> Hand).

    Window objects draw only if reachable through the `+0x0C` back-link
    chain; slot 7 ships unlinked, so it is spliced between the cursor
    (slot 8) and slot 16 - the vanilla rows' own layering. Every menu open
    allocates fresh objects and a fresh chain, so neither the splice nor
    the slot-6 overwrite can double-apply. Touches only t0-t5.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x23, 16, 8, SEND_ROW_TEXT_SLOT_OFFSET),     # lw t0,0x1C(s0)
        _i(0x23, 16, 10, SEND_ROW_CURSOR_SLOT_OFFSET),  # lw t2,0x20(s0)
    )
    _load_address(b, 9, SEND_ROW_RECORD_ADDRESS)        # t1 = Send record
    b.emit(
        _i(0x23, 16, 12, SEND_ROW_FEET_SLOT_OFFSET),    # lw t4,0x18(s0)
        _i(0x0F, 0, 11, FEET_LABEL_RECORD_ADDRESS >> 16),  # lui t3
        _i(0x2B, 12, 9, 0),                             # slot6.record = Send
        _i(0x0F, 0, 9, HELD_ITEM_FLAG_ADDRESS >> 16),   # lui t1
        _i(0x24, 9, 9, HELD_ITEM_FLAG_ADDRESS & 0xFFFF),  # lbu t1,(held)
        _i(0x09, 11, 13, HAND_LABEL_RECORD_ADDRESS & 0xFFFF),  # t5 = Hand
    )
    b.branch(0x05, 9, 0, "held")                        # bne t1,zero
    b.emit(0)
    b.emit(_i(0x09, 11, 13, FEET_LABEL_RECORD_ADDRESS & 0xFFFF))  # t5 = Feet
    b.label("held")
    b.emit(
        _i(0x2B, 8, 13, 0),                             # slot7.record = t5
        _i(0x23, 10, 11, 0x0C),                         # lw t3,0x0C(t2)
        _i(0x2B, 10, 8, 0x0C),                          # sw t0,0x0C(t2)
        _i(0x28, 17, 0, SEND_ROW_ANIMATION_BYTE_OFFSET),  # sb zero,0xAA(s1)
        _j(0x02, SEND_ROW_ASSIGNER_CONTINUATION),       # j 0x8004FFF4
        _i(0x2B, 8, 11, 0x0C),                          # (delay) sw t3,0x0C(t0)
    )
    payload = b.build()
    if len(payload) > SEND_ROW_ASSIGNER_CAPACITY:
        raise ValueError(
            f"The Send-row assigner stub needs {len(payload)} bytes and has "
            f"{SEND_ROW_ASSIGNER_CAPACITY}."
        )
    return payload


def load_send_rail_cap() -> bytes:
    cap = (
        resources.files(__package__)
        .joinpath("data", "send_rail_cap.bin")
        .read_bytes()
    )
    if len(cap) != SEND_RAIL_CAP_SIZE:
        raise ValueError(
            f"send_rail_cap.bin must be {SEND_RAIL_CAP_SIZE} bytes, got {len(cap)}."
        )
    return cap


def load_send_row_strip() -> bytes:
    strip = (
        resources.files(__package__)
        .joinpath("data", "send_row_strip.bin")
        .read_bytes()
    )
    if len(strip) != SEND_ROW_STRIP_SIZE:
        raise ValueError(
            f"send_row_strip.bin must be {SEND_ROW_STRIP_SIZE} bytes, "
            f"got {len(strip)}."
        )
    return strip


# Uncle's marked shortcut floor is consumed by the floor-generation overlay.
# The helper occupies the final free message-area gap immediately before the
# fixed inventory-HUD code. Pooling freed enough of the message region to widen
# this from the original 64 bytes so the helper can also grant the shortcut's
# starting levels; generation still fails loudly if the text no longer fits.
TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET = 0x154C
SHORTCUT_LEVEL_GRANT_OFFSET = 0x15D0

# --- the marker's own presentation --------------------------------------------
#
# The retired card driver that carries the rest of the alternate-pickup code has
# eight bytes left and the seed page's tail is the inventory HUD, so these live
# in the tower gameplay payload's own unused runs: 48 zero bytes at
# `0x801FE650` (the mailbox finalizer's padding) for the two strings, and 416 at
# `0x801FEC50` running exactly to `TOWER_GAMEPLAY_END_ADDRESS` for the code.
# Both are inside the 0x1FC words the installer at `0x800E5170` copies.
#
# Reachable only after the marker test has passed, which is what proves the seed
# page is loaded.
MARKER_TEXT_SLOT_SIZE = 0x18
MARKER_DISPLAY_NAME_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x50
MARKER_SEND_LABEL_ADDRESS = MARKER_DISPLAY_NAME_ADDRESS + MARKER_TEXT_SLOT_SIZE
MARKER_CODE_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x650
MARKER_PRESENTATION_END_ADDRESS = TOWER_GAMEPLAY_END_ADDRESS

# Assigned by `resolve_marker_code_layout`, which packs the three blocks in
# order.  Hand-maintained offsets overlapped by sixteen bytes the first time
# this was written, silently, because a jump into the middle of a routine is
# perfectly legal MIPS.
MARKER_TEXT_BUILDER_ADDRESS = MARKER_CODE_ADDRESS
MARKER_NAME_ENTRY_ADDRESS = MARKER_CODE_ADDRESS
MARKER_DESCRIBE_ENTRY_ADDRESS = MARKER_CODE_ADDRESS

# The item-name *field* is the one place a real multiworld name cannot go: it is
# a single narrow line shared with every native item. The town shop already hit
# this and answered it with a fixed placeholder plus a description carrying the
# detail. Same answer here, and deliberately the same words.
MARKER_DISPLAY_NAME = "Strange..."
MARKER_SEND_LABEL = "Send"

# ...but the *messages* have a whole box, so they name the item outright:
# `You're on Big Key (Thieves' Town).` The two are told apart by return address.
# `FUN_800992E8` is the only caller of the name lookup that belongs to the
# message system, and its `jal` sits at 0x80099324.
MESSAGE_NAME_CALLER_RETURN_ADDRESS = 0x8009_932C

# `show_item_description`. The routine the description hook displaces returns
# **void** - it resolves a pointer and displays it itself - so the replacement
# has to make this call rather than hand a string back. Getting that wrong is
# why V80 had no description box at all.
SHOW_ITEM_DESCRIPTION_ADDRESS = 0x8004_DD2C

# Where built text lands. Inside the save-backed block, in the span
# `0x800157B8`-`0x80015FBF` that a per-bucket live write watch found untouched
# across a reload, a town visit, a tower entry and a floor load. It is rewritten
# on every request, so nothing depends on its contents surviving.
#
# 256 bytes is roughly 120 full-width characters. Pooled item names are whatever
# the multiworld produced and are not otherwise bounded, so the builder stops
# appending once the cursor passes half of it.
MARKER_DESCRIPTION_BUFFER_ADDRESS = 0x8001_5E00
MARKER_DESCRIPTION_BUFFER_SIZE = 0x100

# --- Shortcut starting levels -------------------------------------------------
#
# Warping to floor 10 at level 1 is not survivable, so the shortcut grants the
# levels the climb would have. Raising a level means calling the game's own
# level_up: HP, ATK and DEF live in separate UnitStats fields that only change
# when it applies the growth table at 0x800DDCBC, so writing the level byte
# alone produces a high-level Koh with level-1 stats.
KOH_UNIT_STATS_ADDRESS = 0x8008_34B8
KOH_UNIT_STATS_LEVEL_OFFSET = 0x11
LEVEL_UP_ADDRESS = 0x800A_1D4C

# floor -> level the shortcut should start Koh at.
SHORTCUT_START_LEVELS = {10: 10, 20: 15, 30: 20}

# The grant cannot happen while the floor is being built. A live trace of a
# floor-10 warp showed the bootstrap helper taking Koh from 4 to 10 correctly,
# and then two later writes throwing it away: Koh's actor is allocated and
# templated after floor-state init (0x8003DB54 from the allocator's zero-fill,
# 0x8003DB80 from a 19-word template copy).
#
# The same trace showed what runs after that: the routine at 0x800A08A0, which
# levels every monster the floor just spawned. Its call at 0x800A0E28 is the
# last instruction of its caller before the epilogue, so a wrapper there runs
# with Koh fully initialised. That is also where the "is level_up noisy?"
# question was answered - roughly fifty monster level-ups a floor, silent.
LEVEL_MONSTERS_ADDRESS = 0x800A_08A0

# 0x800A08A0 has three call sites and a live trace settled which ones matter:
# floor entry uses 0x800A6A10 and 0x800A6A38, both inside loops, and never
# 0x800A0E28. All three are wrapped - a redundant wrapper costs a few
# instructions because the carrier is cleared on first use, whereas guessing
# wrong costs another build.
#
# Both real sites pass their own argument in the delay slot (`sra a0,s1,16` and
# `addiu a0,zero,1`), which runs before the wrapper is entered. The wrapper must
# therefore forward a0 untouched rather than supplying one.
LEVEL_MONSTERS_CALL_DUNGEON_OFFSETS = (0xBB6C8, 0xC12B0, 0xC12D8)
DUNGEON_BIN_BASE_LBA = 12_310

# --- TEMPORARY EXPERIMENT (2026-08-14): recolour every monster that spawns -----
#
# Throwaway probe of the medal Picket's appearance store, to be deleted once it
# has answered its question. `roll_carried_item` (`0x800A9230`, the routine that
# fills a unit's carried-item slot at spawn) turns the floor-25 medal Picket
# white with a single `sh 0x000C, 0x12([unit-0x14])` at `0x800A92E0`, buried at
# the bottom of the medal's gate chain. This overwrites that routine's Picket
# species test with the same three words, so every monster the spawner creates
# takes the store:
#
#     0x800A9244  lw    v1, -0x14(s0)      ; the unit's actor record
#     0x800A9248  addiu v0, zero, 0x000C
#     0x800A924C  sh    v0, 0x12(v1)
#
# They are copied verbatim from `0x800A92D8`..`0x800A92E0`, so the probe cannot
# mis-encode the thing it is testing. `v0`/`v1` are scratch at this point and the
# `lw` load delay is covered by the `addiu`.
#
# One on-disc site, not two: `DUNGEON_GAMEPLAY_OVERLAY.bin` and
# `TOWER_GAMEPLAY_OVERLAY_80088760.bin` are overlapping slices of the same
# `DUNGEON.BIN` bytes (both deltas `0x88760`), so
# `DUNGEON.BIN offset = runtime - 0x80000000 + 0x1A8A0`. **Measured**: the disc
# words at `+0xC3AE4` are the `92030013 24020020 14620026` this replaces.
#
# Side effect, accepted for a probe: losing the species test drops every monster
# into the latch/floor/story chain, which still bottoms out at the floor-25 test
# everywhere else. On floor 25 with Watta's quest live, the first monster spawned
# would carry the water medal whatever species it is.
#
# What it answers: whether `+0x12` of the actor record is an appearance field at
# all, and whether `0x0C` reads as "white" on species other than Picket. **A
# crash is a real result** - it means `[unit-0x14]` is not reliably populated by
# the time the roll runs, which the design in `docs/game/floor-generation.md`
# would have to route around.
MONSTER_RECOLOUR_EXPERIMENT = False  # answered 2026-08-14; left off, not deleted
MONSTER_RECOLOUR_DUNGEON_OFFSET = 0xC3AE4
MONSTER_RECOLOUR_WORDS = (0x8E03_FFEC, 0x2402_000C, 0xA462_0012)

# --- TEMPORARY EXPERIMENT (2026-08-14): give every enemy Picket's AI ----------
#
# Second throwaway probe, same spirit as the recolour one. Monster behaviour is
# dispatched through a 36-entry jump table at `0x80089088`, indexed
# `species - 9`, covering species `0x09`..`0x2C` (`docs/game/monster-ai.md` §1).
# This overwrites every entry with Picket's handler `0x800AEFEC`, so every enemy
# runs Picket's logic: no held item -> a once-ever 50/50 thief latch and a steal
# when it faces Koh; holding something -> the exit-seeking branch that ignores
# Koh inside a room.
#
# **Pure data. No instruction is touched**, which is why this is worth doing
# before any of the carrier machinery: it tests the one thing that machinery
# cannot be designed around, and it cannot fail to assemble.
#
# What it answers, in order of how much it would cost to learn later:
#   1. Does Picket's steal/flee animation survive on a sprite that is not a
#      Picket? The user's stated concern, and the reason to test it broadly
#      rather than on one unit.
#   2. Does the exit-seeking branch read as "ignores Koh, heads for a door" on
#      arbitrary species, or does it degrade into something unreadable?
#   3. Does any of it crash.
#
# Species below 9 and above 0x2C bypass the table entirely, so Koh (0) and the
# scripted actors (Ghosh 0x31, Selfi 0x32, Beldo 0x38) are untouched by
# construction. Species 9-0x2C that are *collared familiars* would be covered,
# but they carry the player-side flag `unit+0x14` bit 0x4000 and are expected not
# to reach this dispatch at all - **unverified**, and worth watching for.
#
# DELETE THIS BLOCK AND THE FLAG once the answer is recorded.
PICKET_AI_EXPERIMENT = False  # answered 2026-08-14; left off, not deleted
PICKET_AI_TABLE_DUNGEON_OFFSET = 0xA3928          # RAM 0x80089088
PICKET_AI_TABLE_ENTRIES = 36
PICKET_AI_HANDLER_ADDRESS = 0x800A_EFEC           # Picket's handler

# --- TESTING (2026-08-15): the bank-B tail canary -----------------------------
#
# Delete this whole section AND the matching `if BANK_B_CANARY_TEST:` block in
# build_player_ppf to stop it carrying into new seeds. Nothing else refers to
# it. Setting the flag False has the same effect for one generation.
#
# The question (docs/game/tower-space-census.md section 4): the GPU display
# list is two constant-placed banks, A at 0x801C9E40 and B at 0x801DA714, each
# 0x108D4 long, alternating frames. ADAP's certified window - the seed page and
# the retracted-carve block, 0x801D7F00..0x801DA700 - is bank A's tail from
# offset 0xE0C0. Bank B's tail from the SAME offset is
# 0x801E87D4..0x801EAFD4: same size, same distance past the same measured
# peak (bank A wrote to 0x801D7548 and bank B to 0x801E7E40 on floor 40 under
# mix magic), same distance below the game's own per-bank cursor limit
# (bank + 0xEF3A). Nobody has ever stamped it. If it survives, it is ~10 KB of
# persistent RAM with an argument identical to the one the seed page rests on.
#
# The test behaves exactly like a real tenant would: a 44-byte resident
# routine, hooked off the once-per-boot init's return, fills the region with
# 0xFF and never touches it again. Then play - floor 40 mix magic is the
# stressor that matters. Read the answer with
# `py -3 tools/Check-AdapBankBCanary.py <ram dump>`: any non-FF byte is a
# game write, a crash at boot or first tower entry means the game
# initialises something there. All-FF after the stressor set is the
# certification; a real tenant then replaces the FFs and moves the stamp
# into its own init.
#
# Resident home: the SDK's RCS stamp `"$Id: intr.c,v 1.76 ..."` at
# 0x800332C8 (52 bytes of .rdata), referenced only by its own dead `rcsid`
# pointer variable at 0x8007AD90 - the same donor class as the PushMatrix
# diagnostic string under the resident validator at 0x8007BEF0. Hook: the
# `jr ra` closing initialize_game_systems_and_display_banks (0x8003D5A4,
# called once from main; the same routine that stores the two bank limits)
# becomes `j stub`, its delay slot untouched; the stub ends with `jr ra`. It
# clobbers a0/a1/v0 only - all caller-saved, and the function returns void.
# Neither site is covered by a base-patch record (checked 2026-08-15).
BANK_B_CANARY_TEST = True
BANK_B_CANARY_START = 0x801E_87D4   # bank B + 0xE0C0, the seed page's offset
BANK_B_CANARY_END = 0x801E_AFD4     # bank B + 0x108C0, exclusive; 0x14 short of the actor pool
BANK_B_CANARY_FILL_WORD = 0xFFFF_FFFF
BANK_B_CANARY_STUB_ADDRESS = 0x8003_32C8
BANK_B_CANARY_STUB_CAPACITY = 52
BANK_B_CANARY_STUB_ORIGINAL_PREFIX = b"$Id: intr.c,v 1.76"
BANK_B_CANARY_HOOK_ADDRESS = 0x8003_D758
BANK_B_CANARY_HOOK_ORIGINAL_WORD = 0x03E0_0008   # jr ra
assert BANK_B_CANARY_START % 4 == 0 and BANK_B_CANARY_END % 4 == 0
assert BANK_B_CANARY_END - BANK_B_CANARY_START == 0x2800
assert BANK_B_CANARY_END <= 0x801E_AFE8  # the actor pool starts here


def build_bank_b_canary_stub() -> bytes:
    """Fill [START, END) with the sentinel word, then return to the hooked caller.

        lui   a0, hi(START)     ; a0 = cursor
        ori   a0, a0, lo(START)
        lui   a1, hi(END)       ; a1 = end (exclusive)
        ori   a1, a1, lo(END)
        addiu v0, zero, -1      ; the sentinel
    loop:
        sw    v0, 0(a0)
        addiu a0, a0, 4
        bne   a0, a1, loop
        nop
        jr    ra
        nop
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x0F, 0, 4, BANK_B_CANARY_START >> 16),
        _i(0x0D, 4, 4, BANK_B_CANARY_START & 0xFFFF),
        _i(0x0F, 0, 5, BANK_B_CANARY_END >> 16),
        _i(0x0D, 5, 5, BANK_B_CANARY_END & 0xFFFF),
        _i(0x09, 0, 2, -1),
    )
    b.label("loop")
    b.emit(_i(0x2B, 4, 2, 0), _i(0x09, 4, 4, 4))
    b.branch(0x05, 4, 5, "loop")
    b.emit(0, 0x03E0_0008, 0)
    stub = b.build()
    if len(stub) > BANK_B_CANARY_STUB_CAPACITY:
        raise ValueError(
            f"The bank-B canary stub is {len(stub)} bytes; the RCS string is "
            f"{BANK_B_CANARY_STUB_CAPACITY}."
        )
    return stub


def iter_bank_b_canary_slus_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(SLUS file offset, bytes) for the stub and the hook. Empty when off."""

    if not BANK_B_CANARY_TEST:
        return ()
    from . import save_removal

    return (
        (
            save_removal.slus_runtime_to_file_offset(BANK_B_CANARY_STUB_ADDRESS),
            build_bank_b_canary_stub(),
        ),
        (
            save_removal.slus_runtime_to_file_offset(BANK_B_CANARY_HOOK_ADDRESS),
            struct.pack("<I", _j(0x02, BANK_B_CANARY_STUB_ADDRESS)),
        ),
    )

# --- TESTING (2026-08-16): force every random floor to the single-room layout -
#
# Crash-repro knob for the v1.0.1 field report (FelixFire, Discord 2026-08-16):
# floor 3 came up as "one large room", and once 7-8 monsters and the floor's
# items were on screen together the game fell over. Nobody has a savestate;
# the layout is a 1-in-256 roll, so this makes the roll certain to reproduce
# it on demand. Delete this section AND the matching `if FORCE_SINGLE_ROOM_TEST:`
# block in build_player_ppf once the crash is understood; nothing else refers
# to either. Setting the flag False has the same effect for one generation.
#
# The mechanism (docs/game/floor-generation.md section 5):
# `generate_procedural_floor_layout` (FloorGen 0x80019AF8) rolls
# `random_range_inclusive(0, 0x200)` and takes the single-room fallback at
# 0x8001A120 when the (sign-extended) result is below 2 - `slti v0,v0,2` at
# 0x80019BE0, `beqz` past the jump at 0x80019BE4. Raising the immediate to
# 0x7FFF makes every roll qualify: one 60x60 room at (2,2), no bigger map,
# every random floor (3+; 1 and 2 are predefined layouts, 31 and 40 are
# special cases). This is exactly the community force-single-room patch. The
# floor-generation package ships in two disc copies, verified identical over
# this range; both are written, as the spawn-weight edits are.
FORCE_SINGLE_ROOM_TEST = False  # ridden 2026-08-16: no crash reproduced; left off, not deleted
FORCE_SINGLE_ROOM_COMPARE_ADDRESS = 0x8001_9BE0
FORCE_SINGLE_ROOM_ORIGINAL_WORD = 0x2842_0002    # slti v0,v0,2
FORCE_SINGLE_ROOM_REPLACEMENT_WORD = 0x2842_7FFF  # slti v0,v0,0x7FFF
assert 0x8001_6000 <= FORCE_SINGLE_ROOM_COMPARE_ADDRESS < 0x8002_0000  # the verified-identical span
assert FORCE_SINGLE_ROOM_COMPARE_ADDRESS % 4 == 0


def iter_force_single_room_dungeon_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(DUNGEON.BIN file offset, instruction word) for BOTH package copies. Empty when off."""

    if not FORCE_SINGLE_ROOM_TEST:
        return ()
    from . import floor_item_pool

    return tuple(
        (
            copy_offset + FORCE_SINGLE_ROOM_COMPARE_ADDRESS - 0x8000_0000,
            struct.pack("<I", FORCE_SINGLE_ROOM_REPLACEMENT_WORD),
        )
        for copy_offset in floor_item_pool.FLOOR_GENERATION_FILE_OFFSETS
    )

# Carrier between the two. Inside the save-backed block, past ADSV's defined
# bytes, inside the span the bucket map confirmed unwritten. It is not
# zero-initialised, so the consumer accepts only the three valid levels and
# clears anything else; the helper also writes zero on every ordinary ascent so
# a stale value cannot survive into one.
#
# **Moved 0x8001_5FE8 -> 0x8001_5FEC on 2026-08-06.** ADSV v3 grew to 0x2C
# and its gold-granted counter took 0x8001_5FE8 - and the collision was found
# the expensive way: the helper's every-ascent zero write wiped the counter
# each floor, so the client re-banked every gold package per climb (1.5M gold
# in one session). The assert below is that incident made structural: the
# carrier must always sit past ADSV's end, whatever size ADSV grows to.
SHORTCUT_PENDING_LEVEL_ADDRESS = 0x8001_5FEC
assert (
    PERSISTENT_STATE_ADDRESS + PERSISTENT_STATE_SIZE
    <= SHORTCUT_PENDING_LEVEL_ADDRESS
    <= 0x8001_5FFC
), "The shortcut pending-level carrier overlaps the ADSV journal."

# --- tower resume: putting a saved run back into Koh ---------------------------
#
# The client can already snapshot everything a tower run needs EXCEPT Koh
# himself: inventory, the familiar (its `0x20` collar bit is on its inventory
# descriptor) and the floor number are all inside the 24 KiB checkpoint block.
# Koh's stats are not - `docs/systems/tower-continue.md` has the measurements.
#
# The save block does hold a copy at `0x80012194`, and it is USELESS as a
# source: measured 2026-08-10, the game writes live -> mirror on tower entry
# and on every floor change and never reads it back (a hand-edited level byte
# came back as 1 both times). Whatever consumed it died with the memory-card
# save. So by the time anything could apply a resume, the mirror already holds
# the fresh level-1 Koh, and the record has to come from somewhere the game
# does not write.
#
# That is this carrier, in the ADAP scratch span the bucket write-watch
# certified untouched across a reload, a town visit, a tower entry and a floor
# load. It is inside the checkpoint region on purpose - it rides in the
# client's snapshot for free - and clear of the marker description buffer at
# 0x80015E00.
TOWER_RESUME_CARRIER_ADDRESS = 0x8001_5800
TOWER_RESUME_MAGIC = int.from_bytes(b"ADRS", "little")
# Koh's UnitStats, the extent that is actually state: the live struct's first
# 0x20 bytes are handler pointers (which is why the game's own mirror starts
# where this does), and from +0x4C it is pointers again. Level is +0x11,
# current HP +0x28 and max HP +0x29.
TOWER_RESUME_RECORD_SIZE = 0x4C
TOWER_RESUME_RECORD_WORDS = TOWER_RESUME_RECORD_SIZE // 4
# The record comes FIRST and the magic sits after it, which buys two things
# for no code at all. The copy walks its cursor from the base and finishes
# with the cursor sitting exactly on the magic, so the one-shot clear needs no
# second address (that saved word is why the whole thing fits the wrapper's
# 220-byte slot). And because the magic is the last thing written, a client
# that dies mid-write leaves a record with no witness, which reads as "no
# resume pending" rather than as a half-applied Koh - so the CLIENT MUST WRITE
# THE RECORD BEFORE THE MAGIC.
TOWER_RESUME_MAGIC_OFFSET = TOWER_RESUME_RECORD_SIZE
# The floor to resume onto, and a SECOND one-shot - it cannot share the magic
# above, because the two are consumed in different places at different times:
# the warp request is spent in town (the greeting's own scene transition, see
# town_warp._build_resume_warp_wrapper) and the stats magic is spent later, at
# the floor build. Nonzero means pending; the value is the floor number itself,
# so 0 is naturally "nothing to do".
TOWER_RESUME_FLOOR_REQUEST_OFFSET = TOWER_RESUME_MAGIC_OFFSET + 4
TOWER_RESUME_CARRIER_SIZE = TOWER_RESUME_FLOOR_REQUEST_OFFSET + 4
assert TOWER_RESUME_RECORD_SIZE % 4 == 0
assert TOWER_RESUME_CARRIER_ADDRESS >= 0x8001_57B8, (
    "The tower-resume carrier must sit inside the certified ADAP scratch span."
)
assert (
    TOWER_RESUME_CARRIER_ADDRESS + TOWER_RESUME_CARRIER_SIZE
) <= 0x8001_5E00, "The tower-resume carrier runs into the marker description buffer."

# --- send tokens --------------------------------------------------------------
#
# A send costs a token. The counter is save-backed and lives in the tail of
# the 0x40-byte save extension, immediately past the shortcut carrier -
# **verified unclaimed rather than assumed**: nothing in the generator or
# the client names this range, and it reads zero in all three live tower
# dumps. (The forced-trap byte's collision with the ADGT record came from
# trusting a "last defined field" note instead of checking occupancy.)
#
# Inside 0x80010000..0x80016000, so a checkpoint restore rolls the token
# count back together with gold, the receive cursor and the location masks -
# which is what makes "restore, then send again" cost the token again
# instead of duplicating a gift.
SEND_TOKEN_COUNT_ADDRESS = 0x8001_5FF0
assert SEND_TOKEN_COUNT_ADDRESS >= SHORTCUT_PENDING_LEVEL_ADDRESS + 4
assert SEND_TOKEN_COUNT_ADDRESS + 4 <= 0x8001_6000
# What a fresh save starts with. Deliberately one, not zero: it makes the
# whole token path testable on a new run without collecting anything, which
# is the point of this build. The pool item that grants more comes later.
SEND_TOKEN_STARTING_COUNT = 1
# A witness beside the count, so the gate can tell "new save" from "spent
# them all" and seed itself on first touch. Build 1 relied on the town
# initializer alone and reached the tower with a zero count.
SEND_TOKEN_MAGIC_OFFSET = 4
SEND_TOKEN_MAGIC = int.from_bytes(b"ADST", "little")
assert SEND_TOKEN_COUNT_ADDRESS + SEND_TOKEN_MAGIC_OFFSET + 4 <= 0x8001_6000

# How many `Send Token` items this save has banked from the multiworld.
#
# The client needs this for the same reason gold does: a token count is
# CUMULATIVE and spent by the game, so it cannot be re-derived from the
# server history the way a keycard level can. Every poll compares the
# history's token count against this and adds the difference.
#
# It sits beside the count rather than inside ADSV deliberately. Growing
# ADSV is what burned 0.9.84 (a new field landed on the shortcut carrier,
# which a floor-ascent helper zero-writes), and it would re-initialize
# every existing save. Here it needs no version bump, and the same
# dataflow scan that cleared the count covers it: nothing in the game's
# resident code writes `0x80015FC0..0x80016000`.
#
# It IS inside the checkpoint region (`0x80010000..0x80016000`), which is
# the point - a death rollback reverts the banked count, the token count
# and the receive cursor together, so a rolled-back token is simply
# re-granted instead of being lost or double-granted.
SEND_TOKEN_BANKED_ADDRESS = 0x8001_5FF8
assert SEND_TOKEN_BANKED_ADDRESS >= (
    SEND_TOKEN_COUNT_ADDRESS + SEND_TOKEN_MAGIC_OFFSET + 4
)
assert SEND_TOKEN_BANKED_ADDRESS + 4 <= 0x8001_6000

# `show_simple_action_message(a0 = encoded text)` - the tower's own bottom
# message box, the same primitive the locked-elevator refusal uses.
#
# This is the RIGHT primitive for the send confirmation and the WRONG one
# for the send refusal, and the difference is which context each runs in.
# The refusal answers a players-menu row, with the menu still owning the
# screen - that needs the menu's own drawer and its object lifecycle (see
# the modal block below). The confirmation runs from the commit, which is
# not in the menu at all: confirming a target closes the whole menu system
# and hands control back to the dungeon before the gameplay dispatcher
# calls `put_item_into_bag`, where the commit is planted. Confirmed in
# play - "after clicking the target player for the send, the whole menu
# system closes and you are back to being able to interact with the
# dungeon environment."
SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS = 0x8009_97FC

# --- the players-menu refusal modal -------------------------------------------
#
# Read out of `tower_menu_open_main.sav`, a save state taken with the tower
# players menu OPEN - which is the only way to get the front-menu overlay
# into a dump. The same addresses in an ordinary tower dump hold the
# FLOOR-GENERATION overlay, and two builds softlocked because they were
# argued from that. The tell: there, Items (`0x80018AB0`) and Feet
# (`0x80018AD0`) sit 32 bytes apart with code running straight through
# between them; here each is a proper function with its own prologue.
#
# Vanilla's "You need 2 familiars" is the model, and its shape is:
#
#   1. allocate a menu object, `alloc(a0 = 0, a1 = menu state)`;
#   2. `s0 = object + 0x20`, store the menu state at `s0 + 0x8C`;
#   3. install a per-frame callback at `object + 0x10`;
#   4. present: `begin(1)`, `prepare()`, `draw(text)`;
#   5. **return the OBJECT POINTER.**
#
# Step 5 is the contract both softlocked builds got wrong. A row handler
# does not return a status - it returns the object that now owns the
# screen, and the menu suspends itself until that object dies. Returning 0
# means "nothing took over", which is why the dispatcher called the row
# again on the very next frame and the refusal re-printed forever.
#
# The callback is vanilla's own (`0x8001E70C`): it polls button 2 each
# frame, redraws while it is up, and on the press consumes the input and
# tears the modal down - which is exactly the "acknowledge with Cross and
# fall back to the row selection" behaviour asked for.
MENU_OBJECT_ALLOCATE_ADDRESS = 0x8003_FD64
MENU_OBJECT_PREPARE_ADDRESS = 0x8004_DDC4
MENU_OBJECT_REGISTER_ADDRESS = 0x800D_BF38
MENU_MESSAGE_BEGIN_ADDRESS = 0x8004_DCE0
MENU_MESSAGE_PREPARE_ADDRESS = 0x8004_DCEC
MENU_MESSAGE_DRAW_ADDRESS = 0x8004_DDE4
# Vanilla's dismiss-on-Cross callback, reused as-is.
MENU_MESSAGE_DISMISS_CALLBACK = 0x8001_E70C
MENU_OBJECT_STATE_OFFSET = 0x8C          # from object + 0x20
MENU_OBJECT_CALLBACK_OFFSET = 0x10       # from the object itself

# The two strings and the gate routine live in the floor-page window's free
# static space, which is exactly what it is for: every page sector carries
# these bytes identically, so the per-floor read rewrites them with
# themselves. Nothing here may become mutable.
SEND_TOKEN_BLOCK_OFFSET = FORCED_TRAP_STUB_OFFSET + FORCED_TRAP_STUB_CAPACITY
NO_SEND_TOKENS_TEXT = "You have no send tokens."
SEND_COMPLETE_TEXT = "Sent!"
NO_SEND_TOKENS_MESSAGE_OFFSET = SEND_TOKEN_BLOCK_OFFSET
SEND_COMPLETE_MESSAGE_OFFSET = SEND_TOKEN_BLOCK_OFFSET + 0x38
SEND_TOKEN_GATE_OFFSET = SEND_TOKEN_BLOCK_OFFSET + 0x48
SEND_TOKEN_CHECK_OFFSET = SEND_TOKEN_BLOCK_OFFSET + 0x130
# The spend-and-confirm routine lives here rather than inline in the commit
# for a hard reason: the commit's slot is 416 bytes and it was already using
# 380, so the twelve words this needs overran it. The commit calls in - `ra`
# is expendable at that point by its own contract (both its exits restore
# `ra` from the frame).
SEND_TOKEN_SPEND_OFFSET = SEND_TOKEN_BLOCK_OFFSET + 0x168
# Spend-and-say-so, the commit's success tail. Out of line for the same
# reason the spend is: the commit's slot has twelve bytes left. Calling it
# instead of the bare spend costs the commit nothing - it is the same one
# word, retargeted - which is also what makes the confirmation revertible
# by pointing that word back at SEND_TOKEN_SPEND_ADDRESS.
SEND_COMPLETE_ROUTINE_OFFSET = SEND_TOKEN_BLOCK_OFFSET + 0x190
# Repacked 0x240 -> 0x1A0 on 2026-08-14, returning 160 bytes to the floor-page
# window. The six sub-blocks were laid out on round numbers during development,
# which is the right way to build - it leaves room for the second iteration -
# but nothing did the end pass. Measured sizes: messages 48 and 6, gate 228,
# check 52, spend 36, complete 44 - plus a few bytes of headroom each, because
# the round numbers existed for a reason: a first iteration is rarely the last.
# The build raises on overflow (it caught a message terminator this pack missed),
# so growing any of them is loud rather than silent.
SEND_TOKEN_BLOCK_CAPACITY = 0x1C0

# --- EXPERIMENT (2026-08-14): the location-check carrier, first ride ----------
#
# One monster per floor holds an item and runs Picket's AI. Scoped down from the
# design in `docs/game/monster-ai.md` §5 so that a single build proves the parts
# that cannot be proven statically; the forced off-band spawn is deliberately
# NOT here.
#
# **Which monster.** The first one the floor spawns. No new spawn code: the
# carried-item roll already runs once per monster at floor build, and its caller
# only calls it when the slot is empty, so claiming the first caller wins the
# race for free. Cost: the carrier is an ordinary band species at its ordinary
# level, which is exactly what a first ride wants.
#
# **The item** is a Wind Crystal (`0x0603`) - the same literal the vanilla
# generator writes at `0x8001E3A4`, so the descriptor shape is already proven,
# and unmistakable on the floor when it drops.
#
# **Where the code lives.** The window's free static space, which is 96 B, not
# the 672 B `docs/systems/floor-text-paging.md` claims - the send-token block
# took 0x240 from 0xAA0. The two stubs are 88 B. Window space is code-only (the
# per-floor read rewrites it with identical bytes), so the mutable pair lives in
# the 120-byte unrestricted run after the loader.
#
# **The ordering guess this build encodes.** Window code is live from boot
# (floor 1's page is baked into the seed block) and every later rewrite restores
# identical bytes, so it cannot be absent when a hook fires - the loader's
# position relative to monster spawning should not matter. The forced-trap stub
# is the standing precedent, in the same window and ridden since 0.9.89. If that
# reasoning is wrong the failure is diagnostic rather than silent: a hook
# jumping into not-yet-written window bytes lands in zeros and crashes on the
# first floor build, which points straight at ordering. A carrier that simply
# never appears would instead mean the claim never ran, and a carrier that
# appears but behaves normally would mean the AI hook missed - three failures,
# three different meanings.
#
# **No per-floor clear.** The pair holds (floor, unit*), and the claim fires
# whenever the stored floor differs from `g_current_floor_number`. That needs no
# floor-build hook. Known gap: leaving the tower from floor N and re-entering to
# reach N again as the first floor would skip one carrier. Benign, and visible.
CARRIER_PROBE = True
CARRIER_CODE_OFFSET = SEND_TOKEN_BLOCK_OFFSET + SEND_TOKEN_BLOCK_CAPACITY
CARRIER_CODE_CAPACITY = FLOOR_PAGE_WINDOW_END - CARRIER_CODE_OFFSET
CARRIER_ROLL_STUB_ADDRESS = SEED_BLOCK_ADDRESS + CARRIER_CODE_OFFSET
# The claim grew from 9 words to 20 on 2026-08-15: it hands out the real
# marker (slot MARKER_CARRIER_SLOT) and reads the journal first, so a floor
# whose third check is already banked spawns its carrier empty-handed - the
# "suppress the item, not the spawn" gate from docs/game/monster-ai.md.
CARRIER_CLAIM_STUB_SIZE = 0x84
# 0x20 (2026-08-15, was 0x28): the per-turn palette re-stamp is gone - the
# carrier is already the one monster that does not belong on the floor, the
# colour is a bonus, and a wind seed reverting it costs the player nothing
# but a fireball. The claim stamps once at spawn instead.
CARRIER_AI_STUB_SIZE = 0x20
CARRIER_DRAW_STUB_SIZE = 0x24
CARRIER_AI_STUB_ADDRESS = CARRIER_ROLL_STUB_ADDRESS + CARRIER_CLAIM_STUB_SIZE
CARRIER_DRAW_STUB_ADDRESS = CARRIER_AI_STUB_ADDRESS + CARRIER_AI_STUB_SIZE
# Mutable pair, outside the window: +0x00 halfword last-claimed floor,
# +0x04 word carrier unit pointer.
CARRIER_STATE_OFFSET = FLOOR_PAGE_LOADER_OFFSET + FLOOR_PAGE_LOADER_CAPACITY
CARRIER_STATE_ADDRESS = SEED_BLOCK_ADDRESS + CARRIER_STATE_OFFSET
# The carried item: the AP marker for the floor's third slot. It was 0x0603
# (a Wind Crystal) for the two probe rides; the caller stores the whole word
# into unit+0x48 (`sw v0,0x48(s0)` at 0x800A0B48), and the death drop puts
# all four bytes on the ground, so the collect hook sees an ordinary marker.
CARRIER_ITEM_DESCRIPTOR = marker_descriptor_word(MARKER_CARRIER_SLOT)
# On a floor whose third check is already banked the carrier spawns holding
# NOTHING (the claim returns 0): a plain level-1 monster of an out-of-place
# species, running its own species AI - it may fight, it drops nothing, and
# there is no item on the floor for a player to confuse with a check.
#
# History, because both alternatives were tried and both failed on a ride:
#
# * Empty-handed was the ORIGINAL behaviour, and it looked broken while the AI
#   dispatch keyed on the unit POINTER: the empty carrier still got Picket's
#   handler, took the THIEF branch, lost its once-ever 50/50 and fell through
#   to the default aggressive AI (docs/game/monster-ai.md §2b, the "Nyuel"
#   reports). The dispatch keys on the HELD MARKER now (2026-08-17), so an
#   empty-handed unit simply is not a carrier any more and that failure
#   cannot recur.
# * From 2026-08-15 to 2026-08-17 it held the marker WITH the equipped bit
#   (0xAD020B01, `+0x4B & 0x20`): holding branch, so it fled, and the
#   overlay's death drop (`0x800AD24C`) skips an equipped carried item. But
#   the drop is PER SPECIES PACKAGE, and two pool species return whatever
#   they hold from their own death routine with no equipped-bit test - Picket
#   (`0x80162D48`, the "give the stolen item back" fly-out; a stolen equipped
#   item has to come back) and Viper (the same code inline). A floor-22 Picket
#   carrier on a banked floor dropped the phantom (`Creamcrash.sav`); the
#   collect hook rejected 0xAD, so it entered the inventory as an
#   unidentified, EQUIPPED-flagged Cream, "have" opened the equip dialogue,
#   and throwing it crashed the game. An item no player can be handed is the
#   only safe phantom, and that is no item at all.
DESCRIPTOR_EQUIPPED_BIT = 0x2000_0000
assert CARRIER_ITEM_DESCRIPTOR & DESCRIPTOR_EQUIPPED_BIT == 0, (
    "The carried marker must not carry the equipped bit: the death drop would "
    "skip it (the first ride's whole bug)."
)
# What the AI dispatch keys on (2026-08-17): the [id, category] halfword of
# the unit's carried item at unit+0x48. Both the real marker and the banked
# phantom read [MARKER_ID, MARKER_CATEGORY] there; a monster spawned into a
# dead carrier's actor slot reads zero (the pool zeroes a slot on
# allocation), which is what retired the pointer test - see the dispatch
# docstring in _build_carrier_stubs and docs/game/monster-ai.md §2d.
#
# **Standing conflict, recorded so it is not re-discovered the hard way:**
# category 0x0B is the GIFT category, and this test makes ANY monster
# holding a category-0x0B item run Picket's flee AI. Today only two things
# put one there: this claim, and Koh throwing a gift at a monster
# (monster_receives_thrown_item - it then flees; harmless). But the Viper's
# handler hunts a ground-item CATEGORY (`0x800A6E8C(actor, 0x12, ...)`, eggs)
# and picks it up into +0x48 - if that 0x12 is ever retargeted at 0x0B to
# make Vipers chase AP markers, a Viper that picked one up would pass this
# test and switch to Picket's flee AI (on top of whatever the Viper does with
# what it holds - eating it would lose the check outright). Any such feature
# must widen this test (e.g. the quality byte +0x4A == MARKER_CARRIER_SLOT,
# or the status byte) first.
UNIT_CARRIED_ITEM_OFFSET = 0x48
CARRIER_HELD_MARKER_HALFWORD = MARKER_ID | (MARKER_CATEGORY << 8)
assert CARRIER_HELD_MARKER_HALFWORD == CARRIER_ITEM_DESCRIPTOR & 0xFFFF
assert 0 < CARRIER_HELD_MARKER_HALFWORD < 0x8000, "must fit an addiu immediate unsigned"
assert UNIT_CARRIED_ITEM_OFFSET % 2 == 0, "lhu needs a halfword-aligned offset"
# The actor's palette word (+0x12) is a SIGNED CLUT DELTA in units of one
# 16-colour palette, added by the sprite renderer (SLUS 0x8004595C) to the
# current animation cell's own CLUT id. Each of the six graphics slots owns
# three CLUT rows in VRAM (y = 466 + 3*slot is the base row; y-2 holds the
# translucent twins the game reaches with -128; y-1 a third set).
#
# A species' base row is FOUR GROUPS OF FOUR palettes (x = 0..15): one group
# per form (wild + the three familiar elements - the medal Picket's 0x0C is
# group 3, sub-slot 0), and within a group sub-slots 0 and 1 are populated
# on every species while sub-slots 2 and 3 are all-zero - i.e. TRANSPARENT -
# on about half of them (Troll, Nyuel, Barong, Glacier, Unicorn, ...;
# surveyed over 16 species in the tower save states, 2026-08-15). A cell's
# own CLUT can already sit on sub-slot 1 (a Unicorn's normal frame does; a
# Troll's hit-flash frame does), so a delta that is not a multiple of four
# can land a frame on an empty sub-slot and draw NOTHING but the shadow. That
# was the floor-3 invisible Unicorn (delta +2, `invisible_carrier.sav`);
# the floor-2 Golem with the same delta was visible only because Golem fills
# all sixteen. So the choices are the three other form groups and nothing
# else - which is also the strongest tell, a monster wearing another
# element's colours. The world draws one per seed.
CARRIER_PALETTE = 0x000C                # the medal Picket's white (group 3); the default
CARRIER_PALETTE_CHOICES = (4, 8, 12)
assert all(choice % 4 == 0 for choice in CARRIER_PALETTE_CHOICES), (
    "A carrier palette delta that is not a multiple of four can land on an "
    "empty (transparent) CLUT sub-slot on some species."
)
# The unit's talent bitfield (unit+0x54) and the sleep-proof talent bit, read
# by the thrown-sleep path's `has_talent` helper at 0x800C8408 (tower
# overlay). It does NOT gate the spawn-time sleep - every species constructor
# rolls that itself (50%, effect 1, 32..95 turns, e.g. 0x80170CB0 in Pulunpa's
# package) straight into apply_status - so the claim also clears the effect.
UNIT_TALENTS_OFFSET = 0x54
TALENT_SLEEP_PROOF = 0x0200_0000
# Resident SLUS: `clear_timed_effect(unit, effect_id)` - walks the four timed
# status slots at unit+0x2C.. and runs the effect's expiry handler if it is
# present. Effect 1 is sleep (bit 0x200 of unit+0x1C; its expiry handler at
# 0x80042C2C clears the bit exactly the way a natural wake-up does).
CLEAR_TIMED_EFFECT_ADDRESS = 0x8004_2B68
SLEEP_EFFECT_ID = 1
# Hook sites, DUNGEON.BIN offsets (= runtime - 0x80000000 + 0x1A8A0).
CARRIER_ROLL_HOOK_DUNGEON_OFFSET = 0xBB3DC   # jal roll_carried_item @0x800A0B3C
CARRIER_SPAWN_HOOK_DUNGEON_OFFSET = 0xC129C  # jal random_range     @0x800A69FC
CARRIER_DRAW_HOOK_DUNGEON_OFFSET = 0xBB234   # jal rand            @0x800A0994
CARRIER_AI_HOOK_DUNGEON_OFFSET = 0xC916C     # lbu species        @0x800AE8CC
ROLL_CARRIED_ITEM_ADDRESS = 0x800A_9230
AI_DISPATCH_RESUME_ADDRESS = 0x800A_E8D4     # the addiu that turns v0 into an index
SPAWN_MONSTER_ADDRESS = 0x800A_08A0
RANDOM_RANGE_ADDRESS = 0x800A_6DA4
MONSTER_TABLE_POINTER_ADDRESS = 0x8008_3478
# The carrier is forced through the sixteenth band slot, which is borrowed for
# the duration of one spawn and put back before the vanilla loops ever read it -
# so no disc table is edited, no slot range is narrowed, and the respawn draw is
# untouched. See docs/game/monster-ai.md.
CARRIER_SLOT_INDEX = 15
CARRIER_SLOT_BYTE_OFFSET = CARRIER_SLOT_INDEX * 2
# **Not** `CARRIER_SLOT_INDEX + 2`. A live breakpoint (2026-08-14) showed that
# `0x800A08A0` gives the levelling loop and the carried-item roll only to
# `a0 < 2` spawns; the sixteen slot-driven ones skip both. So the forced spawn
# goes in as `a0 = 1` - which picks its band slot from `rand & 0xF` instead of
# from `a0`, and is why the draw stub exists.
CARRIER_SPAWN_ARG = 1
LCG_ADDRESS = 0x800A_6D30
CARRIER_LEVEL = 1
# One byte per floor, 1..39: the species that floor's carrier spawns as,
# chosen per seed by `monster_spawns.plan_floor_spawns` from outside that
# floor's own roster. The level is always CARRIER_LEVEL, so the table needs
# no second byte. Biased by -1 in the stub so `floor` indexes it directly.
CARRIER_SPECIES_TABLE_OFFSET = (
    CARRIER_CODE_OFFSET
    + CARRIER_CLAIM_STUB_SIZE
    + CARRIER_AI_STUB_SIZE
    + CARRIER_DRAW_STUB_SIZE
)
CARRIER_SPECIES_TABLE_ADDRESS = SEED_BLOCK_ADDRESS + CARRIER_SPECIES_TABLE_OFFSET
CARRIER_SPECIES_TABLE_FLOORS = 39
assert (
    CARRIER_SPECIES_TABLE_OFFSET + CARRIER_SPECIES_TABLE_FLOORS
    <= FLOOR_PAGE_WINDOW_END
), "The carrier species table overruns the floor-page window."
# The forced-spawn stub is the one piece that cannot live in the floor-page
# window: it needs no per-floor restore, and the window is down to 4 spare bytes.
# State: +0x00 carrier unit pointer, +0x04 "claiming now" flag.
CARRIER_CLAIMING_STATE_OFFSET = 4
CARRIER_FORCED_STUB_OFFSET = CARRIER_STATE_OFFSET + 8
CARRIER_FORCED_STUB_ADDRESS = SEED_BLOCK_ADDRESS + CARRIER_FORCED_STUB_OFFSET
CARRIER_UNRESTRICTED_END = 0xE48
NO_SEND_TOKENS_MESSAGE_ADDRESS = SEED_BLOCK_ADDRESS + NO_SEND_TOKENS_MESSAGE_OFFSET
SEND_COMPLETE_MESSAGE_ADDRESS = SEED_BLOCK_ADDRESS + SEND_COMPLETE_MESSAGE_OFFSET
SEND_TOKEN_GATE_ADDRESS = SEED_BLOCK_ADDRESS + SEND_TOKEN_GATE_OFFSET
SEND_TOKEN_CHECK_ADDRESS = SEED_BLOCK_ADDRESS + SEND_TOKEN_CHECK_OFFSET
SEND_TOKEN_SPEND_ADDRESS = SEED_BLOCK_ADDRESS + SEND_TOKEN_SPEND_OFFSET
SEND_COMPLETE_ROUTINE_ADDRESS = SEED_BLOCK_ADDRESS + SEND_COMPLETE_ROUTINE_OFFSET
assert (
    SEND_TOKEN_BLOCK_OFFSET + SEND_TOKEN_BLOCK_CAPACITY <= FLOOR_PAGE_WINDOW_END
), "The send-token block overruns the floor-page window."

# Test builds only: bake a starting Progressive Keycard level into the seed so a
# shortcut can be exercised without the server granting keycards first. Never
# set for a real seed - generation prints a warning when it is.
TEST_STARTING_KEYCARD_LEVEL = int(os.environ.get("ADAP_TEST_STARTING_KEYCARD_LEVEL", "0"))
TOWER_FLOOR_BOOTSTRAP_HELPER_ADDRESS = (
    SEED_BLOCK_ADDRESS + TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET
)
SHORTCUT_LEVEL_GRANT_ADDRESS = SEED_BLOCK_ADDRESS + SHORTCUT_LEVEL_GRANT_OFFSET

# The compact inventory HUD code occupies the fixed tail of the seed page.
# Its render nodes, transforms, and label buffers live in the verified
# pre-workspace tail of the resident block at 0x801fec50-0x801fedef.
INVENTORY_HUD_CODE_OFFSET = 0x16AC
INVENTORY_HUD_POST_REGISTRATION_ADDRESS = SEED_BLOCK_ADDRESS + INVENTORY_HUD_CODE_OFFSET
INVENTORY_HUD_REFRESH_ADDRESS = SEED_BLOCK_ADDRESS + 0x175C
INVENTORY_HUD_NODE_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x650
INVENTORY_HUD_TRANSFORM_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x690
INVENTORY_HUD_KEYCARD_LABEL_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x6D0
INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x760

# Legacy resident-HUD layouts remain documented for compatibility with older
# diagnostic builds.  The current base instead appends the six-node HUD to the
# front-menu module, whose lifetime exactly matches the inventory UI.
RESTORED_INVENTORY_HUD_POST_REGISTRATION_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x208
RESTORED_INVENTORY_HUD_KEYCARD_LABEL_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x590
RESTORED_INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x620

DIRECT_INVENTORY_MODULE_BASE_ADDRESS = 0x8002_4B20
DIRECT_INVENTORY_MODULE_END_ADDRESS = 0x8002_4FF0
DIRECT_INVENTORY_HUD_WRAPPER_ADDRESS = DIRECT_INVENTORY_MODULE_BASE_ADDRESS
DIRECT_INVENTORY_HUD_KEYCARD_LABEL_ADDRESS = 0x8002_4E10
DIRECT_INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS = 0x8002_4EA0
DIRECT_INVENTORY_CONSTRUCTOR_HOOK_ADDRESS = 0x8001_B088

_INVENTORY_REGISTRATION_CALLER_RETURN = 0x8001_B088

_ORIGINAL_ELEVATOR_PROMPT_CALLBACK_ADDRESS = 0x8002_45A8
_ALLOCATE_UI_OBJECT_ADDRESS = 0x8003_FC64
_INITIALIZE_UI_TEXTURES_ADDRESS = 0x8004_DDC4
_RESET_UI_SELECTION_ADDRESS = 0x8004_DCE0
_INITIALIZE_UI_SELECTION_ADDRESS = 0x8004_DCEC
_BUILD_YES_NO_PROMPT_ADDRESS = 0x8004_DF8C
_POLL_UI_SELECTION_ADDRESS = 0x8004_E0AC
_CLOSE_UI_SELECTION_ADDRESS = 0x8004_E130
_UPDATE_UI_OBJECT_ADDRESS = 0x8002_423C
_WIND_CRYSTAL_USE_ADDRESS = 0x800C_0230
_BEGIN_ASCENDING_ELEVATOR_ADDRESS = 0x8008_D9F0
_SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS = 0x8009_97FC

# --- the players-menu refusal modal -------------------------------------------
#
# Read out of `tower_menu_open_main.sav`, a save state taken with the tower
# players menu OPEN - which is the only way to get the front-menu overlay
# into a dump. The same addresses in an ordinary tower dump hold the
# FLOOR-GENERATION overlay, and two builds softlocked because they were
# argued from that. The tell: there, Items (`0x80018AB0`) and Feet
# (`0x80018AD0`) sit 32 bytes apart with code running straight through
# between them; here each is a proper function with its own prologue.
#
# Vanilla's "You need 2 familiars" is the model, and its shape is:
#
#   1. allocate a menu object, `alloc(a0 = 0, a1 = menu state)`;
#   2. `s0 = object + 0x20`, store the menu state at `s0 + 0x8C`;
#   3. install a per-frame callback at `object + 0x10`;
#   4. present: `begin(1)`, `prepare()`, `draw(text)`;
#   5. **return the OBJECT POINTER.**
#
# Step 5 is the contract both softlocked builds got wrong. A row handler
# does not return a status - it returns the object that now owns the
# screen, and the menu suspends itself until that object dies. Returning 0
# means "nothing took over", which is why the dispatcher called the row
# again on the very next frame and the refusal re-printed forever.
#
# The callback is vanilla's own (`0x8001E70C`): it polls button 2 each
# frame, redraws while it is up, and on the press consumes the input and
# tears the modal down - which is exactly the "acknowledge with Cross and
# fall back to the row selection" behaviour asked for.
MENU_OBJECT_ALLOCATE_ADDRESS = 0x8003_FD64
MENU_OBJECT_PREPARE_ADDRESS = 0x8004_DDC4
MENU_OBJECT_REGISTER_ADDRESS = 0x800D_BF38
MENU_MESSAGE_BEGIN_ADDRESS = 0x8004_DCE0
MENU_MESSAGE_PREPARE_ADDRESS = 0x8004_DCEC
MENU_MESSAGE_DRAW_ADDRESS = 0x8004_DDE4
# Vanilla's dismiss-on-Cross callback, reused as-is.
MENU_MESSAGE_DISMISS_CALLBACK = 0x8001_E70C
MENU_OBJECT_STATE_OFFSET = 0x8C          # from object + 0x20
MENU_OBJECT_CALLBACK_OFFSET = 0x10       # from the object itself
_CANCELLED_ACTION_CLEANUP_ADDRESS = 0x8009_13DC

# The final two non-EOF sectors in STR/DUMMY_.STR are zeroed Form-1 sectors.
# They are replaced by the generated 4 KiB seed block and read through the
# game's ordinary command-6 CD path when tower mode starts.
SEED_SECTOR_LBA = 31_449
RAW_SECTOR_SIZE = 2_352
FORM1_USER_SIZE = 2_048
# Three since the 2 KB growth. LBAs 31446-31451 are zero dummy sectors on
# the original disc, so there is room for six if the page grows again.
SEED_SECTOR_COUNT = SEED_BLOCK_SIZE // FORM1_USER_SIZE

PPF_HEADER_SIZE = 56


_BATTLE_GLYPHS = {
    " ": 0x01,
    ",": 0x1B,
    ".": 0x0E,
    ":": 0x2E,
    "!": 0x18,
    "'": 0x16,
    "-": 0x2C,
    "1": 0x32,
    "A": 0x20,
    "B": 0x1D,
    "C": 0x30,
    "D": 0x26,
    "E": 0x22,
    "F": 0x1F,
    "H": 0x23,
    "I": 0x2D,
    "K": 0x33,
    "L": 0x27,
    "M": 0x24,
    "N": 0x28,
    "P": 0x21,
    "R": 0x36,
    "S": 0x35,
    "T": 0x1C,
    "U": 0x31,
    "W": 0x34,
    "X": 0x2A,
    "Y": 0x29,
    "a": 0x03,
    "b": 0x14,
    "c": 0x0C,
    "d": 0x0A,
    "e": 0x02,
    "f": 0x13,
    "g": 0x12,
    "h": 0x0B,
    "i": 0x08,
    "j": 0x2B,
    "k": 0x19,
    "l": 0x0D,
    "m": 0x15,
    "n": 0x05,
    "o": 0x07,
    "p": 0x10,
    "q": 0x2F,
    "r": 0x09,
    "s": 0x06,
    "t": 0x04,
    "u": 0x0F,
    "v": 0x17,
    "w": 0x11,
    "x": 0x1E,
    "y": 0x1A,
    "z": 0x25,
}


def _full_width_cp932(text: str) -> bytes:
    result = bytearray()
    for character in text:
        if character == " ":
            encoded_character = "\u3000"
        elif "!" <= character <= "~":
            encoded_character = chr(ord(character) + 0xFEE0)
        else:
            encoded_character = character
        try:
            result.extend(encoded_character.encode("cp932"))
        except UnicodeEncodeError:
            result.extend("？".encode("cp932"))
    return bytes(result)


_COMPACT_MODE_ENTER = 0x51

# Encoder modes, matching the decoder's own `a2` compact flag.
_RAW = 0
_COMPACT = 1


def encode_battle_message(text: str) -> bytes:
    """Encode one game-owned action message, as cheaply as the format allows.

    The decoder is `append_encoded_text` at `0x80099194`. Read carefully, it
    permits far more than the mod used to emit:

    * `0x51` outside compact mode enters compact mode;
    * a zero byte *inside* compact mode leaves it and keeps reading;
    * a zero byte *outside* compact mode ends the string;
    * outside compact mode, bytes are copied verbatim - a CP932 lead byte and
      its trail byte as a pair.

    So a message may switch between the compact alphabet and full-width CP932
    as often as it likes. The old encoder switched once, at the first
    unsupported glyph, and never switched back - which made a single `(` in
    `Small Key (Palace of Darkness)` cost 23 extra bytes, because every
    character after it doubled.

    Byte costs: entering compact mode 1, one compact glyph 1, leaving compact
    mode 1, one full-width character 2, and the terminator 1 - or 2 while
    compact mode is still open, since it must be closed first.

    This walks the string once from the back keeping the cheapest tail for
    each mode, so the encoding is optimal rather than merely better. That
    matters: escaping an isolated unsupported character costs 4 bytes, so
    naive interleaving is *worse* than the old scheme for a name like
    `Blinder Ball (10)`. The search picks whichever wins per string.
    """

    length = len(text)
    wide = [_full_width_cp932(character) for character in text]
    glyphs = [_BATTLE_GLYPHS.get(character) for character in text]

    # cost[i][mode] is the cheapest way to encode text[i:] and terminate,
    # starting in `mode`. `stay` values are needed separately so the two
    # mode-switch options below cannot refer to each other in a cycle:
    # switching modes twice with nothing emitted between is never optimal.
    infinity = float("inf")
    cost: list[list[float]] = [[0.0, 0.0] for _ in range(length + 1)]
    cost[length][_RAW] = 1.0
    cost[length][_COMPACT] = 2.0
    stay: list[list[float]] = [[0.0, 0.0] for _ in range(length + 1)]

    for index in range(length - 1, -1, -1):
        stay_raw = len(wide[index]) + cost[index + 1][_RAW]
        stay_compact = (
            infinity if glyphs[index] is None else 1 + cost[index + 1][_COMPACT]
        )
        stay[index][_RAW] = stay_raw
        stay[index][_COMPACT] = stay_compact
        cost[index][_RAW] = min(stay_raw, 1 + stay_compact)
        cost[index][_COMPACT] = min(stay_compact, 1 + stay_raw)

    out = bytearray()
    mode = _RAW
    index = 0
    while index < length:
        if mode == _RAW and cost[index][_RAW] != stay[index][_RAW]:
            out.append(_COMPACT_MODE_ENTER)
            mode = _COMPACT
            continue
        if mode == _COMPACT and cost[index][_COMPACT] != stay[index][_COMPACT]:
            out.append(0)
            mode = _RAW
            continue
        if mode == _COMPACT:
            out.append(glyphs[index])
        else:
            out.extend(wide[index])
        index += 1

    if mode == _COMPACT:
        out.append(0)
    out.append(0)
    return bytes(out)


def _r(rs: int, rt: int, rd: int, shift: int, function: int) -> int:
    return (
        ((rs & 0x1F) << 21)
        | ((rt & 0x1F) << 16)
        | ((rd & 0x1F) << 11)
        | ((shift & 0x1F) << 6)
        | (function & 0x3F)
    )


def _i(opcode: int, rs: int, rt: int, immediate: int) -> int:
    return (
        ((opcode & 0x3F) << 26)
        | ((rs & 0x1F) << 21)
        | ((rt & 0x1F) << 16)
        | (immediate & 0xFFFF)
    )


def _j(opcode: int, target: int) -> int:
    return ((opcode & 0x3F) << 26) | ((target >> 2) & 0x03FF_FFFF)


def _upper(address: int) -> int:
    return ((address + 0x8000) >> 16) & 0xFFFF


def _lower(address: int) -> int:
    return address & 0xFFFF


class _MipsBuilder:
    def __init__(self) -> None:
        self.words: list[int] = []
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, int, int, int, str]] = []

    def emit(self, *words: int) -> None:
        self.words.extend(word & 0xFFFF_FFFF for word in words)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"Duplicate MIPS label: {name}")
        self.labels[name] = len(self.words)

    def branch(self, opcode: int, rs: int, rt: int, target: str) -> None:
        self.fixups.append((len(self.words), opcode, rs, rt, target))
        self.words.append(0)

    def build(self) -> bytes:
        for index, opcode, rs, rt, target in self.fixups:
            if target not in self.labels:
                raise ValueError(f"Unknown MIPS label: {target}")
            displacement = self.labels[target] - (index + 1)
            if not -0x8000 <= displacement <= 0x7FFF:
                raise ValueError(f"Branch to {target} is out of range.")
            self.words[index] = _i(opcode, rs, rt, displacement)
        return struct.pack(f"<{len(self.words)}I", *self.words)


def _load_address(builder: _MipsBuilder, register: int, address: int) -> None:
    builder.emit(
        _i(0x0F, 0, register, _upper(address)),
        _i(0x09, register, register, _lower(address)),
    )


def encode_inventory_hud_text(text: str) -> bytes:
    """Encode ASCII into the game's chained 8x16 inventory glyph format."""

    glyph_count = sum(character != " " for character in text)
    result = bytearray(glyph_count * 12)
    x = 0
    glyph_index = 0
    for character in text:
        if character == " ":
            x += 4
            continue
        if not "!" <= character <= "~":
            raise ValueError(
                f"Unsupported inventory HUD character U+{ord(character):04X}."
            )

        offset = glyph_index * 12
        result[offset : offset + 12] = bytes(
            (
                0x00,
                0x2C,
                (x + 0x80) & 0xFF,
                0x80,
                0x1F,
                0x00,
                0x84,
                0x7C,
                (ord(character) & 0x0F) << 3,
                (ord(character) & 0xF0) - 0x20,
                8,
                16,
            )
        )
        x += 8
        glyph_index += 1

    if result:
        result[-12] |= 0x80
    return bytes(result)


def _build_inventory_hud_post_registration_hook() -> bytes:
    """Attach the four game-owned HUD nodes to a new inventory render root."""

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -16),
        _i(0x2B, 29, 8, 0),
        _i(0x2B, 29, 31, 4),
        _i(0x2B, 29, 2, 8),
    )
    _load_address(b, 8, _INVENTORY_REGISTRATION_CALLER_RETURN)
    b.branch(0x05, 31, 8, "unrelated")
    b.emit(0)
    b.branch(0x04, 2, 0, "unrelated")
    b.emit(
        0,
        _j(0x03, INVENTORY_HUD_REFRESH_ADDRESS),
        0,
        _i(0x09, 7, 8, 0x50),  # inventory dynamic-fill asset
    )
    _load_address(b, 9, INVENTORY_HUD_NODE_ADDRESS)
    b.emit(
        _i(0x2B, 9, 8, 0x00),
        _i(0x2B, 9, 8, 0x10),
        _i(0x09, 7, 8, 0xB4),  # root-owned render context
        _i(0x2B, 9, 8, 0x08),
        _i(0x2B, 9, 8, 0x18),
        _i(0x2B, 9, 8, 0x28),
        _i(0x2B, 9, 8, 0x38),
        _i(0x23, 7, 8, 0xD0),  # render-entry pointer array
        0,
    )
    b.branch(0x04, 8, 0, "matched_return")
    b.emit(
        0,
        _i(0x23, 8, 8, 8),  # vanilla fill-left entry
        0,
    )
    b.branch(0x04, 8, 0, "matched_return")
    b.emit(0)
    _load_address(b, 9, INVENTORY_HUD_NODE_ADDRESS + 0x30)
    b.emit(_i(0x2B, 8, 9, 0x0C))

    b.label("matched_return")
    b.emit(
        _i(0x23, 29, 2, 8),
        _i(0x23, 29, 31, 4),
        _i(0x23, 29, 8, 0),
        _i(0x09, 29, 29, 16),
        _r(31, 0, 0, 0, 0x08),
        0,
    )

    b.label("unrelated")
    b.emit(
        _i(0x23, 29, 31, 4),
        _i(0x23, 29, 8, 0),
        _i(0x09, 29, 29, 16),
        _r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _build_inventory_hud_refresh() -> bytes:
    """Refresh the variable digits from the save-backed Keycard level."""

    b = _MipsBuilder()
    _load_address(
        b,
        8,
        PERSISTENT_STATE_ADDRESS + PERSISTENT_KEYCARD_LEVEL_OFFSET,
    )
    b.emit(
        _i(0x24, 8, 11, 0),
        0,
        _i(0x0B, 11, 12, 9),
    )
    b.branch(0x05, 12, 0, "valid_level")
    b.emit(0, _i(0x09, 0, 11, 8))

    b.label("valid_level")
    _load_address(b, 8, INVENTORY_HUD_KEYCARD_LABEL_ADDRESS)
    b.emit(
        _r(0, 11, 9, 3, 0x00),
        _i(0x28, 8, 9, 140),
        _r(0, 11, 13, 2, 0x00),
        _r(13, 11, 13, 0, 0x21),
        _i(0x09, 13, 13, 4),
        _i(0x0B, 13, 12, 41),
    )
    b.branch(0x05, 12, 0, "valid_floor")
    b.emit(0, _i(0x09, 0, 13, 40))

    b.label("valid_floor")
    _load_address(b, 8, INVENTORY_HUD_MAX_FLOOR_LABEL_ADDRESS)
    b.emit(_i(0x0B, 13, 12, 10))
    b.branch(0x04, 12, 0, "two_digits")
    b.emit(
        0,
        _r(0, 13, 12, 3, 0x00),
        _i(0x28, 8, 12, 116),
        _i(0x09, 0, 12, 0x80),
        _i(0x28, 8, 12, 108),
        _r(31, 0, 0, 0, 0x08),
        0,
    )

    b.label("two_digits")
    b.emit(
        _i(0x09, 0, 12, 10),
        _r(13, 12, 0, 0, 0x1B),
        _r(0, 0, 14, 0, 0x12),
        _r(0, 0, 15, 0, 0x10),
        _r(0, 14, 14, 3, 0x00),
        _r(0, 15, 15, 3, 0x00),
        _i(0x28, 8, 14, 116),
        _i(0x28, 8, 15, 128),
        _i(0x28, 8, 0, 108),
        _r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _emit_persistent_state_zero_loop(
    b: "_MipsBuilder", *, base: int, cursor: int, end: int, label: str
) -> None:
    """Zero ADSV from the location mask to the end of the record.

    `base` holds PERSISTENT_STATE_ADDRESS; `cursor` and `end` are scratch.
    Both initializers (this page's and the town core's) call this, so the two
    can never disagree about which fields a fresh save starts with. Five words
    instead of one store per word - the town core's slot is 176 bytes and the
    v4 record has eighteen words to clear. The 0.9.84 burn was a field nobody
    zeroed landing on a neighbour; enumerating the span from the layout is
    what stops that recurring.
    """
    first = PERSISTENT_ZEROED_WORD_OFFSETS[0]
    b.emit(
        _i(0x09, base, cursor, first),                  # addiu cursor,base,first
        _i(0x09, base, end, PERSISTENT_STATE_SIZE),     # addiu end,base,size
        # The ball charger's level word sits just below the record and starts
        # at zero with it (BALL_CHARGE_LEVEL_ADDRESS).
        _i(0x2B, base, 0, BALL_CHARGE_LEVEL_ADDRESS - PERSISTENT_STATE_ADDRESS),
    )
    b.label(label)
    b.emit(_i(0x09, cursor, cursor, 4))                 # addiu cursor,cursor,4
    b.branch(0x05, cursor, end, label)                  # bne cursor,end,label
    b.emit(_i(0x2B, cursor, 0, -4))                     # (delay) sw zero,-4(cursor)


def _build_seed_state_initializer() -> bytes:
    # Registers: t0 seed/mailbox, t1/t2 scratch, t3 save extension,
    # t4/t5/t6 values. The save extension is the tail of the 0x6000-byte save
    # buffer, beginning at PERSISTENT_STATE_ADDRESS.
    b = _MipsBuilder()
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x23, 8, 9, 0),
        _i(0x0F, 0, 10, SEED_MAGIC >> 16),
        _i(0x0D, 10, 10, SEED_MAGIC),
    )
    b.branch(0x05, 9, 10, "return")
    b.emit(0)

    _load_address(b, 11, PERSISTENT_STATE_ADDRESS)
    b.emit(
        _i(0x23, 11, 12, 0),
        _i(0x0F, 0, 13, 0x5653),
        _i(0x0D, 13, 13, 0x4441),  # "ADSV"
    )
    b.branch(0x05, 12, 13, "initialize")
    b.emit(
        0,
        _i(0x25, 11, 12, 4),
        _i(0x09, 0, 13, PERSISTENT_STATE_VERSION),
    )
    b.branch(0x05, 12, 13, "initialize")
    b.emit(
        0,
        _i(0x23, 11, 12, 8),
        _i(0x23, 8, 13, 8),
        0,
    )
    b.branch(0x05, 12, 13, "initialize")
    b.emit(
        0,
        _i(0x23, 11, 12, 12),
        _i(0x23, 8, 13, 12),
        0,
    )
    b.branch(0x05, 12, 13, "initialize")
    b.emit(0)
    b.branch(0x04, 0, 0, "sync_mailbox")
    b.emit(0)

    b.label("initialize")
    b.emit(
        _i(0x0F, 0, 12, 0x5653),
        _i(0x0D, 12, 12, 0x4441),
        _i(0x2B, 11, 12, 0),
        _i(0x0F, 0, 12, PERSISTENT_STATE_SIZE),
        _i(0x0D, 12, 12, PERSISTENT_STATE_VERSION),
        _i(0x2B, 11, 12, 4),
        _i(0x23, 8, 12, 8),
        0,
        _i(0x2B, 11, 12, 8),
        _i(0x23, 8, 12, 12),
        0,
        _i(0x2B, 11, 12, 12),
    )
    # Every field after the signature starts at zero: both journals, the
    # received count, the keycard level (overridden below on test builds), the
    # gold counter and the intro flags.
    _emit_persistent_state_zero_loop(b, base=11, cursor=12, end=13, label="zero")
    b.emit(
        # The send-token count. Outside ADSV, so it needs no
        # version bump (and cannot repeat the 0.9.84 burn, where growing the
        # record landed a field on the shortcut carrier); it is initialized
        # here because this is the routine that runs exactly once per fresh
        # save, which is the definition of "a new run starts with one".
        _i(0x09, 0, 12, SEND_TOKEN_STARTING_COUNT),
        _i(0x2B, 11, 12, SEND_TOKEN_COUNT_ADDRESS - PERSISTENT_STATE_ADDRESS),
        # Banked-from-the-multiworld starts at zero: the starting token is a
        # gift from the initializer, not a delivery, so counting it here
        # would make the client believe one pool token had already arrived
        # and swallow the first real one.
        _i(0x2B, 11, 0, SEND_TOKEN_BANKED_ADDRESS - PERSISTENT_STATE_ADDRESS),
    )
    if TEST_STARTING_KEYCARD_LEVEL:
        # Test builds only. Seeds the keycard level so a shortcut is reachable
        # without the server having granted any keycards.
        b.emit(
            _i(0x09, 0, 12, TEST_STARTING_KEYCARD_LEVEL),
            _i(0x2B, 11, 12, PERSISTENT_KEYCARD_LEVEL_OFFSET),
        )
    else:
        b.emit(_i(0x2B, 11, 0, PERSISTENT_KEYCARD_LEVEL_OFFSET))

    b.label("sync_mailbox")
    # The mailbox mirror. Its upper and the store offset used to be hard-coded
    # around `lui t0,0x8020`, which is why the 2026-08-01 carve retraction's
    # constant sweep missed it entirely and the whole-disc rescan had to find
    # it: an encoded immediate matches no address grep. Derived from
    # HIGH_MAILBOX_ADDRESS now, so it moves with the mailbox.
    #
    # Only the keycard level (+0x18) is mirrored. The three words this also
    # copied into the mailbox's "collected floor-location request mirror"
    # (+0x90..+0x9B) went with ADSV v4: no game code ever read that mirror -
    # the spawner and the collect hook read the journal itself - and the
    # 40-byte journal would not fit in a 16-byte field that ends at the request
    # sequence word.
    _mailbox_upper = _upper(HIGH_MAILBOX_ADDRESS)

    def _mailbox_store(register: int, field: int) -> int:
        offset = HIGH_MAILBOX_ADDRESS + field - (_mailbox_upper << 16)
        if not -0x8000 <= offset <= 0x7FFF:
            raise ValueError(
                f"The mailbox mirror cannot reach +0x{field:X} from its lui."
            )
        return _i(0x2B, 8, register, offset)

    b.emit(
        _i(0x0F, 0, 8, _mailbox_upper),
        _i(0x23, 11, 9, PERSISTENT_KEYCARD_LEVEL_OFFSET),
        0,
        _mailbox_store(9, 0x18),
    )

    b.label("return")
    b.emit(_r(31, 0, 0, 0, 0x08), 0)
    return b.build()


_APPEND_ENCODED_TEXT_ADDRESS = 0x8009_9194


def _build_message_appender() -> bytes:
    """Trampoline at the hooked address into the pooled composer.

    The pickup path calls APPEND_LOCATION_MESSAGE_ADDRESS, so that entry point
    is fixed. Composing from fragments does not fit in the 160 bytes reserved
    there, and the reservation cannot grow without moving the render resolver,
    whose address is patched into the dungeon overlay. Two instructions here,
    the body somewhere with room.
    """

    b = _MipsBuilder()
    b.emit(_j(0x02, FLOOR_PAGE_COMPOSER_ADDRESS), 0)
    return b.build()


def _build_pooled_message_composer() -> bytes:
    """Compose one placement's message from the current floor's page slots.

    a0 is the four-byte AP marker descriptor, a1 the destination cursor; the
    return value is the updated cursor, matching what the single-string version
    returned (append_encoded_text's own v0).

    append_encoded_text clobbers a0-a3, v0, v1 and t0 and saves nothing, so
    everything that has to survive a call lives in s-registers:

        s0  running destination cursor
        s1  this placement's three-byte record (in the floor page)
        s2  seed page base

    Record layout: item slot, player slot, form (0 local, 1 remote). Local
    renders "Found " + item + "."; remote "Sent " + item + " to " + name + ".".

    Validation is the page header: the loaded page's floor must equal the
    current floor (which also covers floor 40, whose page is never loaded),
    the page magic must be present, and the marker's slot byte must be 0 or 1.
    A failed check hands back the untouched cursor, same as before.

    Every load is followed by an instruction independent of its destination
    register. The R3000A has no load interlock and this project has been bitten
    by that more than once.
    """

    b = _MipsBuilder()
    # Prologue. 40 bytes of frame: ra plus the three saved s-registers.
    b.emit(
        _i(0x09, 29, 29, -40),      # addiu sp,sp,-40
        _i(0x2B, 29, 31, 16),       # sw ra,16(sp)
        _i(0x2B, 29, 16, 20),       # sw s0,20(sp)
        _i(0x2B, 29, 17, 24),       # sw s1,24(sp)
        _i(0x2B, 29, 18, 28),       # sw s2,28(sp)
        _i(0x2B, 29, 5, 32),        # sw a1,32(sp)   - cursor, for the invalid exit
    )

    # Validate the page: magic, then page floor == current floor, then slot.
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x23, 8, 9, FLOOR_PAGE_HEADER_OFFSET),       # lw t1,HEADER(t0)
        _i(0x0F, 0, 10, FLOOR_PAGE_MAGIC >> 16),        # lui t2,...
        _i(0x0D, 10, 10, FLOOR_PAGE_MAGIC),             # ori t2,t2,...
    )
    b.branch(0x05, 9, 10, "invalid")                    # bne t1,t2
    b.emit(
        0,
        _i(0x0F, 0, 9, 0x8008),                         # lui t1,0x8008
        _i(0x25, 9, 10, 0x146C),                        # lhu t2,0x146C(t1)  current floor
        _i(0x25, 8, 9, FLOOR_PAGE_HEADER_OFFSET + 4),   # lhu t1,page floor
        0,
    )
    b.branch(0x05, 9, 10, "invalid")                    # bne t1,t2
    b.emit(
        _i(0x24, 4, 11, 2),                             # lbu t3,2(a0)   marker slot
        0,
        _i(0x0B, 11, 10, MARKER_SLOT_COUNT),            # sltiu t2,t3,SLOT_COUNT
    )
    b.branch(0x04, 10, 0, "invalid")
    b.emit(0)

    # s2 = seed base, s1 = record base + slot * 3, s0 = cursor.
    b.emit(
        _r(8, 0, 18, 0, 0x21),                  # addu s2,t0,zero
        _r(0, 11, 10, 1, 0x00),                 # sll t2,t3,1        slot*2
        _r(10, 11, 10, 0, 0x21),                # addu t2,t2,t3      slot*3
        _r(18, 10, 17, 0, 0x21),                # addu s1,s2,t2
        _r(5, 0, 16, 0, 0x21),                  # addu s0,a1,zero
        _i(0x09, 17, 17, FLOOR_PAGE_RECORDS_OFFSET),  # addiu s1,s1,RECORDS
    )

    # Sentence prefix: "Found " when the form's remote bit is clear,
    # otherwise "Sent ". Masked rather than tested whole, because bit 0x80
    # of the same byte now carries the trap disguise (FLOOR_PAGE_FORM_TRAP)
    # and a trap is always local - unmasked, every trap would read "Sent ".
    b.emit(
        _i(0x24, 17, 9, 2),                     # lbu t1,2(s1)   form
        _i(0x09, 18, 4, FLOOR_PAGE_FRAGMENT_FOUND),  # addiu a0,s2,FOUND
        _i(0x0C, 9, 9, FLOOR_PAGE_FORM_REMOTE),  # andi t1,t1,1
    )
    b.branch(0x04, 9, 0, "prefix_ready")        # beq t1,zero
    b.emit(
        0,
        _i(0x09, 18, 4, FLOOR_PAGE_FRAGMENT_SENT),  # addiu a0,s2,SENT
    )
    b.label("prefix_ready")
    b.emit(
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),                  # (delay) addu a1,s0,zero
        _r(2, 0, 16, 0, 0x21),                  # addu s0,v0,zero
    )

    # The item name: slot address = base + ITEM_SLOTS + index * SLOT_SIZE.
    # Slot size 0x38 = 56 has no single-shift form; 56x = (x<<3)*7 costs more
    # than (x<<5)+(x<<4)+(x<<3), so compute it as x*8*7 via two shifts and a
    # subtract: (x<<6)-(x<<3).
    b.emit(
        _i(0x24, 17, 9, 0),                     # lbu t1,0(s1)   item slot
        0,
        _r(0, 9, 10, 6, 0x00),                  # sll t2,t1,6    x*64
        _r(0, 9, 9, 3, 0x00),                   # sll t1,t1,3    x*8
        _r(10, 9, 9, 0, 0x23),                  # subu t1,t2,t1  x*56
        _r(9, 18, 4, 0, 0x21),                  # addu a0,s2,t1
        _i(0x09, 4, 4, FLOOR_PAGE_ITEM_SLOTS_OFFSET),  # addiu a0,a0,ITEM_SLOTS
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),                  # (delay) addu a1,s0,zero
        _r(2, 0, 16, 0, 0x21),                  # addu s0,v0,zero
    )

    # Remote placements continue " to " + recipient.
    b.emit(_i(0x24, 17, 9, 2), 0)               # lbu t1,2(s1)   form
    b.branch(0x04, 9, 0, "finish")              # beq t1,zero
    b.emit(
        0,
        _i(0x09, 18, 4, FLOOR_PAGE_FRAGMENT_TO),  # addiu a0,s2,TO
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),
        _r(2, 0, 16, 0, 0x21),
        _i(0x24, 17, 9, 1),                     # lbu t1,1(s1)   player slot
        0,
        # Player slot size 0x24 = 36 = (x<<5)+(x<<2).
        _r(0, 9, 10, 5, 0x00),                  # sll t2,t1,5    x*32
        _r(0, 9, 9, 2, 0x00),                   # sll t1,t1,2    x*4
        _r(10, 9, 9, 0, 0x21),                  # addu t1,t2,t1  x*36
        _r(9, 18, 4, 0, 0x21),                  # addu a0,s2,t1
        _i(0x09, 4, 4, FLOOR_PAGE_PLAYER_SLOTS_OFFSET),  # addiu a0,a0,PLAYER_SLOTS
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),
        _r(2, 0, 16, 0, 0x21),
    )

    # Closing period. Its return value is already the value we return.
    b.label("finish")
    b.emit(
        _i(0x09, 18, 4, FLOOR_PAGE_FRAGMENT_PERIOD),
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    # Nothing was appended, so hand back the cursor we were given.
    b.label("invalid")
    b.emit(_i(0x23, 29, 2, 32))                 # lw v0,32(sp)

    b.label("return")
    b.emit(
        _i(0x23, 29, 31, 16),
        _i(0x23, 29, 16, 20),
        _i(0x23, 29, 17, 24),
        _i(0x23, 29, 18, 28),
        _r(31, 0, 0, 0, 0x08),                  # jr ra
        _i(0x09, 29, 29, 40),                   # (delay) addiu sp,sp,40
    )
    return b.build()


# The CD helpers the per-floor page loader calls: the same four calls, in the
# same order, as the bonus floor's proven boot-time read stub (seed page
# +0x148, ridden in game). All four are resident SLUS from boot.
_BUILD_CD_READ_DESCRIPTOR_ADDRESS = 0x8003_F6D4
_ENQUEUE_CD_COMMAND_ADDRESS = 0x8003_E4FC
_WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS = 0x8003_F320
_BIOS_A_DISPATCH_ADDRESS = 0x8000_00A0
_BIOS_FLUSH_CACHE = 0x44


def _build_floor_page_loader() -> bytes:
    """Refresh the seed page's floor-text window from the animator tick.

    Interposed on the elevator-orb animator's per-frame callback (the creator
    at `0x800B1F00` registers this routine instead; the tail jumps into the
    real animator with everything it expects intact). Construction hooks are
    OFF LIMITS: the fifth test build proved that adding even a minimal call
    frame inside the construction hook chain hangs the floor load, with the
    loader itself tracing clean - so the read happens here, gated on game
    mode 2 (tower gameplay, never a load screen or transition), a few frames
    after the floor fades in and long before any item can be reached.

    When the current floor is 1..39 and the loaded page is not already this
    floor's, one sector of the per-seed bank is read straight over the
    window. The window's static bytes (composer code, fragments) are
    identical in every sector, so the read only ever *changes* the per-floor
    header, records and name slots - which is also why no FlushCache is
    needed: a stale i-cache line can never hold wrong instructions.

    Floor 40 and out-of-range floors skip the read and leave the previous
    page resident; both composers reject it on the page-floor-vs-current-
    floor compare and fall back, which is the intended presentation there.

    a0 (the animator object) is preserved across the read; the descriptor
    buffer lives at sp+0x10.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -0x28),                # addiu sp,sp,-0x28
        _i(0x2B, 29, 31, 0x24),                 # sw ra,0x24(sp)
        _i(0x2B, 29, 4, 0x20),                  # sw a0,0x20(sp)  animator obj
    )
    # Only in settled tower gameplay (mode byte 2): never during a load
    # screen, transition (0xFF) or any other mode.
    b.emit(
        _i(0x0F, 0, 8, 0x8008),                 # lui t0,0x8008
        _i(0x24, 8, 9, 0x2E6A),                 # lbu t1,game mode
        _i(0x09, 0, 10, 2),                     # addiu t2,zero,2
    )
    b.branch(0x05, 9, 10, "out")                # bne t1,t2
    b.emit(0)
    b.emit(
        _i(0x25, 8, 9, 0x146C),                 # lhu t1,current floor
    )
    _load_address(b, 10, SEED_BLOCK_ADDRESS)    # (fills t1's load delay)
    b.emit(
        _i(0x25, 10, 13, FLOOR_PAGE_HEADER_OFFSET + 4),  # lhu t5,page floor
        _i(0x09, 9, 11, -1),                    # addiu t3,t1,-1
        _i(0x0B, 11, 12, FLOOR_PAGE_FLOOR_COUNT),  # sltiu t4,t3,39
    )
    b.branch(0x04, 12, 0, "out")                # beq t4,zero
    b.emit(0)
    b.branch(0x04, 13, 9, "out")                # beq t5,t1 - already loaded
    b.emit(0)
    b.emit(
        _i(0x09, 0, 4, 1),                      # addiu a0,zero,1   one sector
        _i(0x09, 10, 5, FLOOR_PAGE_WINDOW_OFFSET),  # addiu a1,seed,WINDOW
        _i(0x09, 29, 6, 0x10),                  # addiu a2,sp,0x10
        _i(0x0F, 0, 7, FLOOR_PAGE_BANK_LBA >> 16),   # lui a3
        _i(0x0D, 7, 7, FLOOR_PAGE_BANK_LBA),    # ori a3
        _r(7, 11, 7, 0, 0x21),                  # addu a3,a3,t3   + floor-1
        _j(0x03, _BUILD_CD_READ_DESCRIPTOR_ADDRESS),
        0,
        _i(0x09, 0, 4, 6),                      # addiu a0,zero,6  read command
        _j(0x03, _ENQUEUE_CD_COMMAND_ADDRESS),
        _i(0x09, 29, 5, 0x10),                  # (delay) addiu a1,sp,0x10
        _j(0x03, _WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS),
        0,
        # No FlushCache: every page sector carries the window's code bytes
        # IDENTICALLY (test-asserted), so a stale i-cache line can never hold
        # wrong instructions.
    )
    b.label("out")
    b.emit(
        _i(0x23, 29, 31, 0x24),                 # lw ra,0x24(sp)
        _i(0x23, 29, 4, 0x20),                  # lw a0,0x20(sp)
        _i(0x09, 29, 29, 0x28),                 # addiu sp,sp,0x28
        _j(0x02, FLOOR_PAGE_ANIMATOR_CALLBACK_ADDRESS),  # the real animator
        0,
    )
    return b.build()


def _build_carrier_stubs(palette: int = CARRIER_PALETTE) -> bytes:
    """Claim, AI-dispatch and RNG-draw stubs, packed back to back.

    All three key off the same pair of words at `CARRIER_STATE_ADDRESS`:
    `+0x00` the carrier unit pointer, `+0x04` a "claiming now" flag the
    forced-spawn stub raises around its single call.

    **Claim** (retargets the `jal` at `0x800A0B3C`; `a0` = unit; returns the
    descriptor the caller stores into `unit+0x48`):

        if (!claiming) tail-jump to the real roll
        carrier = unit
        unit->talents |= SLEEP_PROOF          ; thrown sleep cannot stall it
        clear_timed_effect(unit, SLEEP)       ; the constructor's 50% spawn-sleep
        actor->palette = CARRIER_PALETTE      ; one-shot; a re-skin may undo it
        banked = journal[floor-1] & (1 << CARRIER_SLOT)
        return banked ? 0 : CARRIER_ITEM_DESCRIPTOR
             ; = the slot-2 marker, or nothing at all when the floor's third
             ;   check is already banked

    The sleep is cleared here and not prevented, because every species
    constructor rolls it itself (50%, effect 1, straight into apply_status -
    the sleep-proof talent is only consulted by the thrown-sleep path), and the
    roll caller is the first hook that runs after the constructor with the
    unit in hand.

    Keying on the flag rather than on the species lets the carrier share a
    species with the floor's own monsters without being confused for one, and it
    retires both the floor-number latch of the first ride and the species compare
    of the second. The journal read is the "suppress the item, not the spawn"
    gate: a collected floor still gets its carrier, so a player never sees a
    floor where the carrier is simply absent - but it spawns EMPTY-HANDED
    there, a plain level-1 monster on its own species AI (the dispatch below
    keys on the held marker, so with nothing in hand it is not a carrier).
    Between 2026-08-15 and 2026-08-17 it held an *equipped* copy of the marker
    instead, so that it would flee and the overlay death drop would skip it;
    Picket's and Viper's own death routines drop what they hold regardless,
    and a player was handed the phantom (see the note above
    DESCRIPTOR_EQUIPPED_BIT). The floor is bounded by the forced-spawn stub
    before claiming is ever raised, so the journal index here cannot leave the
    record.

    **AI dispatch** (replaces `lbu v0, 0x13(s5)` at `0x800AE8CC`; must leave the
    species in `v0` and resume at `0x800AE8D4`):

        v0 = unit->species
        if (unit->carried[id, category] == [MARKER_ID, MARKER_CATEGORY])
            v0 = 0x20 (Picket)

    It runs on every think (`ai_decide` is what the species packages call
    each turn), so it answers "is this unit a carrier *right now*" - and the
    answer is "it holds the floor's marker". Until 2026-08-17 it compared the
    unit pointer against the one the claim recorded, and that pointer was
    never cleared at death: units are LIFO actor-pool slots (`allocate_actor`
    0x8003FC64 pops the head, `sweep_deleted_actors` 0x800401FC pushes dead
    ones back), so the next monster spawned after the carrier was killed
    usually landed on the same address and inherited Picket's handler -
    empty-handed, so the thief branch: 50/50, then its own species ability
    aimed at Koh whenever it faced him (a floor-4 Baloon casting Fly at Koh;
    docs/game/monster-ai.md §2d). Keying on the held item has no such
    lifetime: a reused slot holds nothing, a banked floor's carrier is spawned
    holding nothing (and so runs its own AI), a carrier that lost its marker
    (a Troll thrown a weapon) reverts to its own AI, and a Manoeva copy of the
    carrier is a fresh unit that never copies +0x48. The pointer is still
    written by the claim (debug state); nothing reads it.

    Only two things can put a category-0x0B (gift) item into a monster's
    +0x48 besides us: Koh throwing a gift at it (it then flees - fine), and a
    Viper if its egg-hunt category (`0x800A6E8C(actor, 0x12, ...)`) were ever
    retargeted at 0x0B, the AP marker category - **that idea would collide
    with this test**, see the note at CARRIER_HELD_MARKER_HALFWORD.

    The palette used to be re-stamped here every turn so it survived a re-skin;
    it is stamped once at claim now (2026-08-15). The carrier is already the
    one monster that does not belong on its floor - the colour is a bonus - and
    a wind seed reverting it costs the player nothing but a fireball. And a
    sleeping unit never reaches this dispatch, which is why the stamp had to
    move to the claim in the first place.

    **RNG draw** (retargets the `jal` to the LCG at `0x800A0994`, the band-slot
    draw on the `a0 < 2` path; the caller masks the result with `0xF`):

        if (claiming) return CARRIER_SLOT_INDEX
        else tail-jump to the real LCG

    This is what makes an `a0 = 1` spawn - the only kind that gets levelled and
    rolled - land on a slot we control instead of a random one. While claiming it
    consumes no RNG state, so vanilla draws are not shifted.
    """
    state_lo = _lower(CARRIER_STATE_ADDRESS)
    claiming_lo = state_lo + CARRIER_CLAIMING_STATE_OFFSET
    journal = PERSISTENT_STATE_ADDRESS + PERSISTENT_LOCATION_MASK_OFFSET
    cb = _MipsBuilder()
    cb.emit(
        _i(0x0F, 0, 8, _upper(CARRIER_STATE_ADDRESS)),   # lui   t0, hi(state)
        _i(0x23, 8, 9, claiming_lo),                     # lw    t1, claiming
        _i(0x09, 0, 5, SLEEP_EFFECT_ID),                 # addiu a1, zero, 1  (harmless on the real path)
    )
    cb.branch(0x04, 9, 0, "real")                        # beq   t1, zero, real
    cb.emit(
        _i(0x0F, 0, 12, TALENT_SLEEP_PROOF >> 16),       # (delay) lui t4, hi(sleep-proof)
        # Claiming. A frame for ra and the unit: the effect clear is a real
        # call and clobbers a0 and every t-register.
        _i(0x09, 29, 29, -0x10),                         # addiu sp, sp, -0x10
        _i(0x2B, 29, 31, 0x0C),                          # sw    ra, 0xC(sp)
        _i(0x2B, 8, 4, state_lo),                        # sw    a0, carrier
        _i(0x23, 4, 11, UNIT_TALENTS_OFFSET),            # lw    t3, unit->talents
        _i(0x2B, 29, 4, 0x08),                           # sw    a0, 8(sp)
        _r(11, 12, 11, 0, 0x25),                         # or    t3, t3, t4   sleep-proof
        _j(0x03, CLEAR_TIMED_EFFECT_ADDRESS),            # jal   clear_timed_effect(unit, 1)
        _i(0x2B, 4, 11, UNIT_TALENTS_OFFSET),            # (delay) sw t3, unit->talents
        # Awake. One-shot palette stamp on the actor, then the journal gate.
        _i(0x23, 29, 4, 0x08),                           # lw    a0, 8(sp)   unit
        _i(0x0F, 0, 9, 0x8008),                          # lui   t1, 0x8008
        _i(0x23, 4, 11, 0xFFEC),                         # lw    t3, unit[-0x14]  actor
        _i(0x25, 9, 9, 0x146C),                          # lhu   t1, floor
        _i(0x09, 0, 12, palette & 0xFFFF),               # addiu t4, zero, palette
        _i(0x0F, 0, 10, _upper(journal - 1)),            # lui   t2, hi(journal-1)
        _i(0x29, 11, 12, 0x0012),                        # sh    t4, actor->palette
        _r(10, 9, 10, 0, 0x21),                          # addu  t2, t2, t1
        _i(0x0F, 0, 2, CARRIER_ITEM_DESCRIPTOR >> 16),   # lui   v0, hi(item)
        _i(0x24, 10, 10, _lower(journal - 1)),           # lbu   t2, journal[floor-1]
        _i(0x0D, 2, 2, CARRIER_ITEM_DESCRIPTOR & 0xFFFF),  # ori v0, v0, lo(item)
        _i(0x0C, 10, 10, 1 << MARKER_CARRIER_SLOT),      # andi  t2, t2, slot bit (banked?)
        _i(0x0B, 10, 10, 1),                             # sltiu t2, t2, 1    -> 1 if not banked, else 0
        _r(0, 10, 10, 0, 0x23),                          # subu  t2, zero, t2 -> all ones, or 0
        _i(0x23, 29, 31, 0x0C),                          # lw    ra, 0xC(sp)
        _r(2, 10, 2, 0, 0x24),                           # and   v0, v0, t2   marker, or nothing
        _r(31, 0, 0, 0, 0x08),                           # jr    ra
        _i(0x09, 29, 29, 0x10),                          # (delay) addiu sp, sp, 0x10
    )
    cb.label("real")
    cb.emit(
        _j(0x02, ROLL_CARRIED_ITEM_ADDRESS),             # j     real roll
        0,                                               # nop
    )
    claim = list(struct.unpack(f"<{len(cb.words)}I", cb.build()))
    dispatch = [
        _i(0x25, 21, 10, UNIT_CARRIED_ITEM_OFFSET),      # lhu   t2, s5->carried[id,category]
        _i(0x24, 21, 2, 0x0013),                         # lbu   v0, s5->species
        _i(0x09, 0, 11, CARRIER_HELD_MARKER_HALFWORD),   # addiu t3, zero, [MARKER_ID, MARKER_CATEGORY]
        _i(0x05, 10, 11, 2),                             # bne   t2, t3, resume
        0,                                               # nop
        _i(0x09, 0, 2, 0x0020),                          # addiu v0, zero, Picket
        _j(0x02, AI_DISPATCH_RESUME_ADDRESS),            # resume: j 0x800AE8D4
        0,                                               # nop
    ]
    draw = [
        _i(0x0F, 0, 8, _upper(CARRIER_STATE_ADDRESS)),   # lui   t0, hi(state)
        _i(0x23, 8, 9, claiming_lo),                     # lw    t1, claiming
        _i(0x09, 0, 2, CARRIER_SLOT_INDEX),              # addiu v0, zero, 15
        _i(0x05, 9, 0, 3),                               # bne   t1, zero, forced
        0,                                               # nop
        _j(0x02, LCG_ADDRESS),                           # j     real LCG
        0,                                               # nop
        _r(31, 0, 0, 0, 0x08),                           # forced: jr ra
        0,                                               # nop
    ]
    for body, size, name in (
        (claim, CARRIER_CLAIM_STUB_SIZE, "claim"),
        (dispatch, CARRIER_AI_STUB_SIZE, "AI dispatch"),
        (draw, CARRIER_DRAW_STUB_SIZE, "RNG draw"),
    ):
        if len(body) * 4 != size:
            raise ValueError(f"The carrier {name} stub is not its declared size.")
    return struct.pack(
        f"<{len(claim) + len(dispatch) + len(draw)}I", *claim, *dispatch, *draw
    )


def _build_carrier_forced_spawn() -> bytes:
    """Force the carrier spawn before the floor ordinary population runs.

    Retargets the `jal random_range` at `0x800A69FC`, the first thing the
    population routine does - before both spawn loops, and before `s0`/`s1`/`s2`
    are assigned, all three of which the routine has already saved. So the stub
    owns those registers and needs a frame only for `ra`.

        carrier  = 0
        claiming = 1
        base     = [0x80083478]              ; the floor live 16-slot band table
        saved    = base[15]
        base[15] = (CARRIER_SPECIES, level 1)
        spawn(a0 = 1)                        ; the path that levels and rolls;
                                             ; the draw stub pins its slot to 15
        base[15] = saved
        claiming = 0
        tail-call random_range(4, 8)         ; what the hooked jal was for

    Reliability, in layers: `0x800A08A0` retries coordinate selection sixteen
    times internally (`0x800A0B4C` back to `0x800A0914`); running before the
    sixteen ordinary spawns means the 32-unit cap cannot be reached; and `a0 = 1`
    skips the minimum-distance-from-Koh rule, which only applies to `a0 = 0`.
    """
    state_lo = _lower(CARRIER_STATE_ADDRESS)
    claiming_lo = state_lo + CARRIER_CLAIMING_STATE_OFFSET
    slot = CARRIER_SLOT_BYTE_OFFSET
    # Floors 1..CARRIER_SPECIES_TABLE_FLOORS only. Floor 40 has no check and no
    # species-table entry (the byte after the table is whatever follows it in
    # the window), so its population runs untouched: the carrier pointer is
    # cleared and the tail-call happens with claiming still zero, which also
    # bounds the claim stub's journal read.
    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, 0xFFF0),                        # addiu sp, sp, -0x10
        _i(0x2B, 29, 31, 0x000C),                        # sw    ra, 0xC(sp)
        _i(0x0F, 0, 8, 0x8008),                          # lui   t0, 0x8008
        _i(0x21, 8, 8, 0x146C),                          # lh    t0, floor
        _i(0x0F, 0, 16, _upper(CARRIER_STATE_ADDRESS)),  # lui   s0, hi(state)
        _i(0x0B, 8, 9, CARRIER_SPECIES_TABLE_FLOORS + 1),  # sltiu t1, t0, 40
    )
    b.branch(0x04, 9, 0, "skip")                         # beq   t1, zero, skip
    b.emit(
        _i(0x2B, 16, 0, state_lo),                       # (delay) sw zero, carrier
        _i(0x09, 0, 9, 1),                               # addiu t1, zero, 1
        _i(0x2B, 16, 9, claiming_lo),                    # sw    t1, claiming
        _i(0x0F, 0, 17, _upper(MONSTER_TABLE_POINTER_ADDRESS)),
        _i(0x23, 17, 17, _lower(MONSTER_TABLE_POINTER_ADDRESS)),  # lw s1, table
        _i(0x0F, 0, 11, _upper(CARRIER_SPECIES_TABLE_ADDRESS - 1)),      # lui   t3, hi(table-1)
        _r(11, 8, 11, 0, 0x21),                          # addu  t3, t3, t0
        _i(0x24, 11, 8, _lower(CARRIER_SPECIES_TABLE_ADDRESS - 1)),      # lbu   t0, table[floor-1]
        0,                                               # nop (load delay)
        _i(0x0D, 8, 8, CARRIER_LEVEL << 8),              # ori   t0, t0, level<<8
        _i(0x25, 17, 18, slot),                          # lhu   s2, base[15]
        _i(0x29, 17, 8, slot),                           # sh    t0, base[15]
        _j(0x03, SPAWN_MONSTER_ADDRESS),                 # jal   spawn
        _i(0x09, 0, 4, CARRIER_SPAWN_ARG),               # addiu a0, zero, 1
        _i(0x29, 17, 18, slot),                          # sh    s2, base[15]
        _i(0x2B, 16, 0, claiming_lo),                    # sw    zero, claiming
    )
    b.label("skip")
    b.emit(
        _i(0x23, 29, 31, 0x000C),                        # lw    ra, 0xC(sp)
        _i(0x09, 0, 4, 4),                               # addiu a0, zero, 4
        _i(0x09, 29, 29, 0x0010),                        # addiu sp, sp, 0x10
        _j(0x02, RANDOM_RANGE_ADDRESS),                  # j     random_range
        _i(0x09, 0, 5, 8),                               # addiu a1, zero, 8
    )
    return b.build()


def _build_forced_trap_stub() -> bytes:
    """Plant the requested trap under Koh, spring it, and leave it there.

    Interposed on the receive dispatcher's hook word (`jal 0x801D9B70` at
    DUNGEON.BIN runtime 0x8008AEB8, retargeted here by build_player_ppf).
    The site's contract, inherited whole: `s1` is Koh's actor, the call runs
    only between actions at the end of the neutral input handler's early-out
    gauntlet, `t`-registers and `a`/`v` registers are clobberable, and the
    caller consumes `v1` as the displaced load `lhu v1,0xA2(s1)` (the pickup
    flag) immediately after the call.

    No request: one tail-jump to the receive dispatcher, which returns to
    the neutral handler on the original `ra` - byte-for-byte the vanilla-hook
    behaviour. Request pending: guard, then build a REAL trap in the first
    free slot at Koh's tile using the roller's own recipe (descriptor,
    record x/y and ground height, tile-grid mark, sprite), call
    `trigger_trap(id, koh, slot, forced=1)`, and consume the request byte.
    The trap persists on the floor - it is not scrubbed - which is both what
    lets the bomb's deferred explosion object find its target and what makes
    the effect native: a revealed trap sits where it fired and re-triggers
    on a later step until the next floor build clears the arrays. Guard
    failures (bonus floor active, Koh not in ordinary idle, no free slot)
    keep the request byte set and retry on a later frame; an out-of-range id
    is dropped without planting.
    """

    b = _MipsBuilder()

    # --- the pickup-message gate ------------------------------------------
    #
    # Runs FIRST, and independently of whether a trap request is pending:
    # the player picks the marker up before the client can possibly have
    # written the request byte, so this cannot be keyed on it.
    #
    # `+0xA2` bit 0x80 is the queued pickup presentation, and THIS hook site
    # is its only dispatcher - the site's next instruction is
    # `andi v0,v1,0x0080` on the value this stub returns, and the vanilla
    # `lhu v1,0xA2(s1)` that fed it was displaced into our payload. So
    # clearing the bit here (in memory; every exit re-reads it) skips the
    # presentation handler `0x8008D7D0` entirely.
    #
    # Nothing is stranded by skipping it: that handler is self-contained -
    # it sets action state 0x23, clears `+0x9B`/`+0x8C` (which the collect
    # hook never sets), clears this same bit, and installs the animation.
    # The collection itself already happened in the payload's collect hook,
    # which sets the journal bit, clears the ground descriptor and clears
    # the tile's item bit BEFORE queueing the presentation. Koh is left in
    # `0x0E`, which is exactly the state the spring below requires.
    #
    # Scope, deliberately narrow: the picked-up item is identified through
    # `+0xBC` (the pickup's own descriptor, set on every pickup), which must
    # be an AP marker (status 0xAD) whose CURRENT floor page marks that slot
    # as one of our traps. The at-feet name, the description box, the
    # `Strange...` shop presentation and the step-on message are all
    # different code and are untouched - only the box that appears once the
    # player has committed to taking the item is suppressed.
    b.emit(
        _i(0x25, 17, 10, ACTOR_PICKUP_FLAG_OFFSET),  # lhu t2,0xA2(s1)
        _i(0x09, 0, 11, ACTOR_EVENT_PICKUP),         # addiu t3,zero,0x80
        _r(10, 11, 11, 0, 0x24),                     # and t3,t2,t3
    )
    b.branch(0x04, 11, 0, "gate_done")          # beq - no pickup queued
    b.emit(0)
    b.emit(
        _i(0x23, 17, 11, ACTOR_PICKUP_DESCRIPTOR_OFFSET),  # lw t3,0xBC(s1)
        0,
    )
    b.branch(0x04, 11, 0, "gate_done")          # beq - no descriptor
    b.emit(0)
    b.emit(
        _i(0x24, 11, 12, 3),                    # lbu t4,3(t3)   status
        _i(0x09, 0, 13, MARKER_STATUS),         # addiu t5,zero,0xAD
    )
    b.branch(0x05, 12, 13, "gate_done")         # bne - not our marker
    b.emit(0)
    b.emit(
        _i(0x24, 11, 12, 2),                    # lbu t4,2(t3)   marker slot
        0,
        _i(0x0B, 12, 13, MARKER_SLOT_COUNT),    # sltiu t5,t4,SLOTS
    )
    b.branch(0x04, 13, 0, "gate_done")          # beq - slot out of range
    b.emit(0)
    # The page must be THIS floor's, or its form bytes describe someone
    # else's placements. Same two checks the composer makes; a failure
    # falls through to the vanilla presentation rather than guessing.
    _load_address(b, 13, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x23, 13, 14, FLOOR_PAGE_HEADER_OFFSET),  # lw t6,magic
        _i(0x0F, 0, 15, FLOOR_PAGE_MAGIC >> 16),     # lui t7
        _i(0x0D, 15, 15, FLOOR_PAGE_MAGIC & 0xFFFF), # ori t7
    )
    b.branch(0x05, 14, 15, "gate_done")         # bne - not a page
    b.emit(0)
    b.emit(
        _i(0x0F, 0, 15, 0x8008),                # lui t7,0x8008
        _i(0x25, 15, 15, 0x146C),               # lhu t7,current floor
        _i(0x25, 13, 14, FLOOR_PAGE_HEADER_OFFSET + 4),  # lhu t6,page floor
        0,
    )
    b.branch(0x05, 14, 15, "gate_done")         # bne - stale page
    b.emit(0)
    b.emit(
        # record = seed + RECORDS + slot * 3, then its form byte.
        _r(0, 12, 14, 1, 0x00),                 # sll t6,t4,1
        _r(14, 12, 14, 0, 0x21),                # addu t6,t6,t4
        _r(14, 13, 14, 0, 0x21),                # addu t6,t6,t5
        _i(0x24, 14, 14, FLOOR_PAGE_RECORDS_OFFSET + 2),  # lbu t6,form
        0,
        _i(0x0C, 14, 14, FLOOR_PAGE_FORM_TRAP),  # andi t6,t6,0x80
    )
    b.branch(0x04, 14, 0, "gate_done")          # beq - an honest placement
    b.emit(0)
    b.emit(
        # Suppress: drop the queued presentation. Every exit below re-reads
        # `+0xA2` for the displaced load, so the caller sees it cleared too.
        _i(0x0C, 10, 10, (~ACTOR_EVENT_PICKUP) & 0xFFFF),  # andi t2,t2,0xFF7F
        _i(0x29, 17, 10, ACTOR_PICKUP_FLAG_OFFSET),        # sh t2,0xA2(s1)
    )
    b.label("gate_done")

    # --- the forced trap ---------------------------------------------------
    # t0 = mailbox page; the request byte, the bonus flag and their clears
    # all reach from one lui (both 0x801DAxxx sign-extend from 0x801E0000).
    b.emit(
        _i(0x0F, 0, 8, _upper(FORCED_TRAP_REQUEST_ADDRESS)),   # lui t0
        _i(0x24, 8, 9, _lower(FORCED_TRAP_REQUEST_ADDRESS)),   # lbu t1,request
        0,
    )
    b.branch(0x04, 9, 0, "dispatcher")          # beq t1,zero - no request
    b.emit(0)
    b.emit(
        _i(0x24, 8, 10, _lower(BONUS_ACTIVE_FLAG_ADDRESS)),    # lbu t2,bonus
        0,
    )
    b.branch(0x05, 10, 0, "defer")              # bne t2,zero - collapse owns it
    b.emit(0)
    b.emit(
        _i(0x24, 17, 10, ACTOR_ACTION_STATE_OFFSET),  # lbu t2,0x9A(s1)
        _i(0x09, 0, 11, ACTOR_ACTION_STATE_IDLE),     # addiu t3,zero,0x0E
    )
    b.branch(0x05, 10, 11, "defer")             # bne - not ordinary idle
    b.emit(0)
    # Never spring a trap on a frame that already has a pending actor event.
    # The obvious one is the pickup presentation the marker itself queued
    # (bit 0x80, which the hook site consumes immediately after this stub
    # returns): interleaving a trap with it puts two events in one halfword
    # and lets one clobber the other. Deferring costs a frame - the trap
    # simply fires once the pickup has presented, which is the order the
    # player already sees.
    b.emit(
        _i(0x25, 17, 10, ACTOR_PICKUP_FLAG_OFFSET),  # lhu t2,0xA2(s1)
        _i(0x09, 0, 11, ACTOR_EVENT_PICKUP | ACTOR_EVENT_BUMP),  # addiu t3
        _r(10, 11, 10, 0, 0x24),                # and t2,t2,t3
    )
    b.branch(0x05, 10, 0, "defer")              # bne - an event is pending
    b.emit(0)
    b.emit(_i(0x0B, 9, 10, 20))                 # sltiu t2,t1,20
    b.branch(0x04, 10, 0, "drop")               # beq t2,zero - id out of range
    b.emit(0)
    # Koh's tile from his linked display object: [s1-0x14] -> +0x24/+0x25.
    b.emit(
        _i(0x23, 17, 10, ACTOR_LINKED_OBJECT_OFFSET),  # lw t2,-0x14(s1)
        0,
        _i(0x24, 10, 13, LINKED_OBJECT_X_OFFSET),      # lbu t5,x
        _i(0x24, 10, 14, LINKED_OBJECT_Y_OFFSET),      # lbu t6,y
        # First free descriptor slot (category byte zero). t3 walks the
        # descriptors, t7 counts.
        _i(0x0F, 0, 12, _upper(TRAP_DESCRIPTOR_ARRAY_ADDRESS)),  # lui t4
        _i(0x09, 12, 11, _lower(TRAP_DESCRIPTOR_ARRAY_ADDRESS)),  # addiu t3
        _r(0, 0, 15, 0, 0x21),                  # addu t7,zero,zero
    )
    b.label("scan")
    b.emit(
        _i(0x24, 11, 10, 1),                    # lbu t2,category(t3)
        0,
    )
    b.branch(0x04, 10, 0, "found")              # beq t2,zero - free slot
    b.emit(0)
    b.emit(
        _i(0x09, 15, 15, 1),                    # addiu t7,t7,1
        _i(0x0B, 15, 10, TRAP_SLOT_COUNT),      # sltiu t2,t7,32
    )
    b.branch(0x05, 10, 0, "scan")               # bne - next slot
    b.emit(_i(0x09, 11, 11, 4))                 # (delay) addiu t3,t3,4
    b.branch(0x04, 0, 0, "defer")               # all 32 occupied - retry later
    b.emit(0)
    b.label("found")
    b.emit(
        # t8 = record = TRAP_RECORD_ARRAY + slot*24
        _r(0, 15, 10, 1, 0x00),                 # sll t2,t7,1
        _r(10, 15, 10, 0, 0x21),                # addu t2,t2,t7
        _r(0, 10, 10, 3, 0x00),                 # sll t2,t2,3
        _i(0x09, 12, 24, _lower(TRAP_RECORD_ARRAY_ADDRESS)),  # addiu t8,t4
        _r(24, 10, 24, 0, 0x21),                # addu t8,t8,t2
        # Frame: ra plus the values that must survive the helper calls.
        _i(0x09, 29, 29, -0x28),                # addiu sp,sp,-0x28
        _i(0x2B, 29, 31, 0x24),                 # sw ra,0x24(sp)
        _i(0x2B, 29, 9, 0x10),                  # sw t1,0x10(sp)  id
        _i(0x2B, 29, 13, 0x14),                 # sw t5,0x14(sp)  x
        _i(0x2B, 29, 14, 0x18),                 # sw t6,0x18(sp)  y
        _i(0x2B, 29, 15, 0x1C),                 # sw t7,0x1C(sp)  slot
        _i(0x2B, 29, 24, 0x20),                 # sw t8,0x20(sp)  record
        # Record: zeroed, then the roller's fields.
        _i(0x2B, 24, 0, 0x00),                  # sw zero x6
        _i(0x2B, 24, 0, 0x04),
        _i(0x2B, 24, 0, 0x08),
        _i(0x2B, 24, 0, 0x0C),
        _i(0x2B, 24, 0, 0x10),
        _i(0x2B, 24, 0, 0x14),
        _i(0x28, 24, 13, 0x06),                 # sb t5,x
        _i(0x28, 24, 14, 0x07),                 # sb t6,y
        # Descriptor word: id | category 0x15 | quality, status 0 (revealed -
        # the same-frame forced trigger makes the hidden dance pointless).
        _i(0x0F, 0, 10, FORCED_TRAP_QUALITY),   # lui t2,quality
        _i(0x0D, 10, 10, 0x1500),               # ori t2,t2,0x1500
        _r(10, 9, 10, 0, 0x25),                 # or t2,t2,t1
        _i(0x2B, 11, 10, 0),                    # sw t2,descriptor(t3)
        # Ground height at the tile centre -> record +0x10 and +0x12.
        # a2 is the probe's Z reference and MUST be set (see
        # TRAP_GROUND_HEIGHT_PROBE_Z - omitting it broke the bomb twice).
        _r(0, 13, 4, 6, 0x00),                  # sll a0,t5,6
        _i(0x0D, 4, 4, 0x20),                   # ori a0,a0,0x20
        _r(0, 14, 5, 6, 0x00),                  # sll a1,t6,6
        _i(0x09, 0, 6, TRAP_GROUND_HEIGHT_PROBE_Z),  # addiu a2,zero,-1024
        _j(0x03, TRAP_GROUND_HEIGHT_ADDRESS),   # jal
        _i(0x0D, 5, 5, 0x20),                   # (delay) ori a1,a1,0x20
        _i(0x23, 29, 24, 0x20),                 # lw t8,record
        0,
        _i(0x29, 24, 2, 0x10),                  # sh v0,+0x10
        _i(0x29, 24, 2, 0x12),                  # sh v0,+0x12
        # "Trap here" bit into the tile grid.
        _i(0x23, 29, 4, 0x14),                  # lw a0,x
        _i(0x23, 29, 5, 0x18),                  # lw a1,y
        _j(0x03, TRAP_TILE_MARK_ADDRESS),       # jal
        _i(0x09, 0, 6, TRAP_TILE_MARK_BIT),     # (delay) addiu a2,0x20
        # The per-id sprite table entry decides the sprite mechanism.
        _i(0x23, 29, 9, 0x10),                  # lw t1,id
        _i(0x0F, 0, 12, _upper(TRAP_SPRITE_TABLE_ADDRESS)),  # lui t4
        _r(0, 9, 10, 2, 0x00),                  # sll t2,t1,2
        _i(0x09, 12, 11, _lower(TRAP_SPRITE_TABLE_ADDRESS)),  # addiu t3
        _r(11, 10, 11, 0, 0x21),                # addu t3,t3,t2
        _i(0x23, 11, 25, 0),                    # lw t9,table entry
        _i(0x23, 29, 24, 0x20),                 # lw t8,record (fills delay)
    )
    b.branch(0x01, 25, 1, "handle_or_none")     # bgez t9 - not an animation
    b.emit(_i(0x2B, 29, 25, 0x0C))              # (delay) sw t9,0x0C(sp)
    b.emit(
        # Animated: attach it (the roller's call), plant hidden 0x840/0x940;
        # the forced trigger's native tail re-attaches and reveals it.
        _r(24, 0, 4, 0, 0x21),                  # addu a0,t8,zero
        _r(25, 0, 5, 0, 0x21),                  # addu a1,t9,zero
        _j(0x03, TRAP_SPRITE_ATTACH_ADDRESS),   # jal
        _r(0, 0, 6, 0, 0x21),                   # (delay) addu a2,zero,zero
        _i(0x23, 29, 25, 0x0C),                 # lw t9,table entry
        _i(0x23, 29, 24, 0x20),                 # lw t8,record
        _i(0x0F, 0, 10, 0x2000),                # lui t2,0x2000
        _r(10, 25, 10, 0, 0x24),                # and t2,t2,t9
        _i(0x09, 0, 11, 0x0840),                # addiu t3,0x0840
    )
    b.branch(0x04, 10, 0, "set_flags")          # beq - plain animation
    b.emit(0)
    b.emit(_i(0x09, 0, 11, 0x0940))             # addiu t3,0x0940
    b.label("set_flags")
    b.emit(_i(0x29, 24, 11, 0x14))              # sh t3,flags
    b.branch(0x04, 0, 0, "fire")
    b.emit(0)
    b.label("handle_or_none")
    b.branch(0x04, 25, 0, "fire")               # zero: no sprite, flags stay 0
    b.emit(_i(0x0F, 0, 10, 0x8000))             # (delay) lui t2,0x8000
    b.emit(
        # Plain handle: +0x8 = value|0x80000000, flags 0 (visible now - the
        # post-reveal state, since the trigger is this same frame).
        _r(10, 25, 10, 0, 0x25),                # or t2,t2,t9
        _i(0x2B, 24, 10, 0x08),                 # sw t2,+0x8
    )
    b.label("fire")
    b.emit(
        _i(0x23, 29, 4, 0x10),                  # lw a0,id
        _r(17, 0, 5, 0, 0x21),                  # addu a1,s1,zero
        _i(0x23, 29, 6, 0x1C),                  # lw a2,slot
        _j(0x03, TRAP_DISPATCH_ADDRESS),        # jal trigger_trap
        _i(0x09, 0, 7, 1),                      # (delay) addiu a3,zero,1
        # Did the handler arm a DEFERRED actor event? Only the bump does.
        # Koh is in `0x0E` by our own guard, and the neutral handler skips
        # the event dispatch entirely in that state, so the event would sit
        # armed forever with the animation counter already incremented.
        # Nudge the state so the next pass dispatches it (see
        # ACTOR_ACTION_STATE_BUMPED); the handler then owns the actor.
        _i(0x25, 17, 10, ACTOR_PICKUP_FLAG_OFFSET),  # lhu t2,0xA2(s1)
        _i(0x09, 0, 11, ACTOR_EVENT_BUMP),      # addiu t3,zero,0x100
        _r(10, 11, 10, 0, 0x24),                # and t2,t2,t3
    )
    b.branch(0x04, 10, 0, "consume")            # beq t2,zero - nothing armed
    b.emit(0)
    b.emit(
        _i(0x09, 0, 10, ACTOR_ACTION_STATE_BUMPED),  # addiu t2,zero,0x24
        _i(0x28, 17, 10, ACTOR_ACTION_STATE_OFFSET),  # sb t2,0x9A(s1)
    )
    b.label("consume")
    b.emit(
        # Planted and sprung: the delivery is complete whatever the handler
        # returned - the trap is armed on the floor either way.
        _i(0x0F, 0, 8, _upper(FORCED_TRAP_REQUEST_ADDRESS)),   # lui t0
        _i(0x28, 8, 0, _lower(FORCED_TRAP_REQUEST_ADDRESS)),   # sb zero
        _i(0x23, 29, 31, 0x24),                 # lw ra,0x24(sp)
        _i(0x09, 29, 29, 0x28),                 # addiu sp,sp,0x28
        _i(0x25, 17, 3, ACTOR_PICKUP_FLAG_OFFSET),  # lhu v1,0xA2(s1)
        _r(31, 0, 0, 0, 0x08),                  # jr ra (v1 lands in the slot)
        0,
    )
    b.label("drop")
    b.emit(
        _i(0x0F, 0, 8, _upper(FORCED_TRAP_REQUEST_ADDRESS)),   # lui t0
        _i(0x28, 8, 0, _lower(FORCED_TRAP_REQUEST_ADDRESS)),   # sb zero,request
    )
    b.label("defer")
    b.emit(
        _i(0x25, 17, 3, ACTOR_PICKUP_FLAG_OFFSET),  # lhu v1,0xA2(s1)
        _r(31, 0, 0, 0, 0x08),                  # jr ra (v1 lands in the slot)
        0,
    )
    b.label("dispatcher")
    b.emit(
        _j(0x02, RECEIVE_ITEM_DISPATCHER_ADDRESS),  # j - ra stays the site's
        0,
    )
    return b.build()


def build_carrying_trap_trampoline() -> bytes:
    """Spring a pending forced trap from the CARRYING control handler.

    See CARRYING_TRAP_TRAMPOLINE_ADDRESS for why this exists at all. Eleven
    words, in 52 bytes of dead SDK string:

        lui   t0, hi(request)
        lbu   t1, lo(request)(t0)
        nop                          ; load delay
        beq   t1, zero, ret          ; nothing pending: just the displaced load
        nop
        jal   forced_trap_stub       ; gate, guards, plant, fire; returns jr ra
        nop
    ret:
        lui   v0, 0x8008             ; the displaced `lhu v0,0x2(s0)`, absolute
        lhu   v0, 0x3462(v0)
        j     0x8008ECD4             ; past the hook's delay slot
        nop

    The request check is the trampoline's own and is NOT delegated to the
    stub: the stub answers "no request" by tail-jumping to the receive
    dispatcher, and delivering items into a held hand is exactly the
    behaviour this must not change. `ra` is not preserved because nothing
    needs it - the return is a `j` to a fixed address in the caller.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x0F, 0, 8, _upper(FORCED_TRAP_REQUEST_ADDRESS)),   # lui t0
        _i(0x24, 8, 9, _lower(FORCED_TRAP_REQUEST_ADDRESS)),   # lbu t1,request
        0,
    )
    b.branch(0x04, 9, 0, "ret")                 # beq t1,zero - nothing pending
    b.emit(0)
    b.emit(
        _j(0x03, FORCED_TRAP_STUB_ADDRESS),     # jal the stub
        0,
    )
    b.label("ret")
    b.emit(
        _i(0x0F, 0, 2, _upper(CARRYING_TRAP_DISPLACED_ADDRESS)),   # lui v0
        _i(0x25, 2, 2, _lower(CARRYING_TRAP_DISPLACED_ADDRESS)),   # lhu v0
        _j(0x02, CARRYING_TRAP_RETURN_ADDRESS),                    # j back
        0,
    )
    trampoline = b.build()
    if len(trampoline) > CARRYING_TRAP_TRAMPOLINE_CAPACITY:
        raise ValueError(
            f"The carrying-handler trap trampoline is {len(trampoline)} bytes; "
            f"the donor string is {CARRYING_TRAP_TRAMPOLINE_CAPACITY}."
        )
    return trampoline


def iter_carrying_trap_slus_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(SLUS file offset, bytes) for the trampoline over its donor string."""

    from . import save_removal

    return (
        (
            save_removal.slus_runtime_to_file_offset(CARRYING_TRAP_TRAMPOLINE_ADDRESS),
            build_carrying_trap_trampoline(),
        ),
    )


def iter_carrying_trap_dungeon_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(DUNGEON.BIN file offset, word) for the carrying handler's hook."""

    from . import save_removal

    return (
        (
            save_removal._dungeon_runtime_to_file_offset(CARRYING_TRAP_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, CARRYING_TRAP_TRAMPOLINE_ADDRESS)),
        ),
    )


def encode_menu_message(text: str) -> bytes:
    """Full-width CP932, the encoding the players menu's drawer expects.

    NOT `encode_battle_message`, which is the compact battle encoding used
    for floor text. Validated byte-for-byte against the game's own
    `You need 2 familiars` at `0x8002E924` (from the menu save state):
    every printable ASCII maps to its full-width form, space to U+3000,
    and a bare 0x0A stays a line break, terminated by a zero byte.
    """

    out = bytearray()
    for character in text:
        if character == "\n":
            out.append(0x0A)
            continue
        if character == " ":
            wide = "　"
        elif "!" <= character <= "~":
            wide = chr(ord(character) - 0x21 + 0xFF01)
        else:
            wide = character
        out += wide.encode("cp932")
    out.append(0)
    return bytes(out)


def _build_send_token_gate() -> bytes:
    """The Send row: refuse with a modal when the player has no tokens.

    A faithful copy of vanilla's "You need 2 familiars" refusal, read from
    a save state with the players menu open (see the constants above). It
    is entered as a row handler - `a0` is the menu state, `ra` the
    dispatcher's - and it obeys the contract both earlier builds broke:

    * with a token, tail-jump into the items opener exactly as the vanilla
      row did, and let ITS return value stand;
    * with none, put up a modal and **return the object pointer**, so the
      menu suspends until the player acknowledges with Cross. The
      dismiss callback is vanilla's own, so the acknowledgement lands back
      on the row selection.
    * if the object cannot be allocated, return 0 and do nothing - the row
      is inert for that press rather than pretending a modal exists.
    """

    b = _MipsBuilder()
    # s0/s1 are the caller's; this routine keeps its own frame and saves
    # them, exactly as the vanilla row handler does.
    b.emit(
        _i(0x09, 29, 29, -0x20),                # addiu sp,sp,-0x20
        _i(0x2B, 29, 31, 0x1C),                 # sw ra,0x1C(sp)
        _i(0x2B, 29, 16, 0x18),                 # sw s0,0x18(sp)
        _i(0x2B, 29, 17, 0x14),                 # sw s1,0x14(sp)
        _i(0x2B, 29, 18, 0x10),                 # sw s2,0x10(sp)
        _r(4, 0, 18, 0, 0x21),                  # addu s2,a0,zero  menu state
    )
    _load_address(b, 3, SEND_TOKEN_COUNT_ADDRESS)
    b.emit(
        _i(0x23, 3, 2, SEND_TOKEN_MAGIC_OFFSET),
        _i(0x0F, 0, 1, SEND_TOKEN_MAGIC >> 16),
        _i(0x0D, 1, 1, SEND_TOKEN_MAGIC & 0xFFFF),
    )
    b.branch(0x04, 2, 1, "counted")
    b.emit(0)
    b.emit(
        _i(0x09, 0, 2, SEND_TOKEN_STARTING_COUNT),
        _i(0x2B, 3, 2, 0),
        _i(0x2B, 3, 1, SEND_TOKEN_MAGIC_OFFSET),
    )
    b.label("counted")
    b.emit(_i(0x23, 3, 2, 0), 0)                # lw v0,(tokens)
    b.branch(0x04, 2, 0, "refuse")              # beq v0,zero
    b.emit(0)

    # Has a token: the vanilla body, then out through this frame so the
    # opener's return value is what the menu sees.
    _load_address(b, 8, SEND_MODE_FLAG_ADDRESS)
    b.emit(
        _i(0x09, 0, 9, 1),
        _i(0x2B, 8, 9, 0),                      # sw t1,(send mode)
        _j(0x03, ITEMS_SESSION_OPENER_ADDRESS),
        _r(18, 0, 4, 0, 0x21),                  # (delay) a0 = menu state
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    b.label("refuse")
    b.emit(
        _r(0, 0, 4, 0, 0x21),                   # addu a0,zero,zero
        _j(0x03, MENU_OBJECT_ALLOCATE_ADDRESS),
        _r(18, 0, 5, 0, 0x21),                  # (delay) a1 = menu state
        _r(2, 0, 17, 0, 0x21),                  # s1 = object
    )
    b.branch(0x04, 17, 0, "return")             # allocation failed: v0 = 0
    b.emit(0)
    b.emit(
        _i(0x09, 17, 16, 0x20),                 # addiu s0,s1,0x20
        _j(0x03, MENU_OBJECT_PREPARE_ADDRESS),
        _r(0, 0, 4, 0, 0x21),                   # (delay) a0 = 0
        _j(0x03, MENU_OBJECT_REGISTER_ADDRESS),
        _i(0x2B, 16, 18, MENU_OBJECT_STATE_OFFSET),  # (delay) s0[0x8C] = state
    )
    _load_address(b, 8, MENU_MESSAGE_DISMISS_CALLBACK)
    b.emit(_i(0x2B, 17, 8, MENU_OBJECT_CALLBACK_OFFSET))  # object[0x10]
    # Present, the same three calls vanilla's refusal makes.
    b.emit(
        _j(0x03, MENU_MESSAGE_BEGIN_ADDRESS),
        _i(0x09, 0, 4, 1),                      # (delay) a0 = 1
        _j(0x03, MENU_MESSAGE_PREPARE_ADDRESS),
        0,
    )
    _load_address(b, 4, NO_SEND_TOKENS_MESSAGE_ADDRESS)
    b.emit(
        _j(0x03, MENU_MESSAGE_DRAW_ADDRESS),
        0,
        # **The object pointer is the return value.** Not a status - this
        # is what tells the menu something took over the screen.
        _r(17, 0, 2, 0, 0x21),                  # addu v0,s1,zero
    )

    b.label("return")
    b.emit(
        _i(0x23, 29, 31, 0x1C),
        _i(0x23, 29, 16, 0x18),
        _i(0x23, 29, 17, 0x14),
        _i(0x23, 29, 18, 0x10),
        _i(0x09, 29, 29, 0x20),
        _r(31, 0, 0, 0, 0x08),                  # jr ra
        0,
    )
    return b.build()


def _build_send_token_check() -> bytes:
    """`v0 = tokens remaining`, seeding the counter on first touch.

    **Clobbers only `v0`, `v1` and `at`.** That restriction is the whole
    point: this is called from the middle of the send commit, where `t3`
    is carrying the confirmed target index between the controller read and
    the mailbox publish, and `t2` the controller pointer. The first version
    of this routine used `t2`/`t3` for its magic constant and destroyed the
    target - the mailbox went out with `ADST` where the recipient should
    be, and the client dropped the gift. `v0`/`v1` are dead here (the
    displaced ground-descriptor compare is long done) and `at` is the
    assembler temporary.

    Seeding matters because "never initialized" and "spent them all" are
    the same zero: the town initializer seeds the pair, but only on the
    frame it decides the save is new, and the tower can get there first.
    """

    b = _MipsBuilder()
    _load_address(b, 3, SEND_TOKEN_COUNT_ADDRESS)   # v1 = &count
    b.emit(
        _i(0x23, 3, 2, SEND_TOKEN_MAGIC_OFFSET),    # lw v0,(magic)
        _i(0x0F, 0, 1, SEND_TOKEN_MAGIC >> 16),     # lui at
        _i(0x0D, 1, 1, SEND_TOKEN_MAGIC & 0xFFFF),  # ori at
    )
    b.branch(0x04, 2, 1, "counted")                 # beq v0,at
    b.emit(0)
    b.emit(
        _i(0x09, 0, 2, SEND_TOKEN_STARTING_COUNT),
        _i(0x2B, 3, 2, 0),                          # sw v0,(count)
        _i(0x2B, 3, 1, SEND_TOKEN_MAGIC_OFFSET),    # sw at,(magic)
    )
    b.label("counted")
    b.emit(
        _i(0x23, 3, 2, 0),                          # lw v0,(count)
        0,
        _r(31, 0, 0, 0, 0x08),                      # jr ra
        0,
    )
    return b.build()


def _build_send_token_spend() -> bytes:
    """Take one token. Clobbers only `v0`, `v1`.

    Called from the commit once a gift is published. Floored at zero: the
    check is the guard, this is the backstop, and an unsigned wrap would
    read as "sends stopped costing anything".
    """

    b = _MipsBuilder()
    _load_address(b, 3, SEND_TOKEN_COUNT_ADDRESS)   # v1 = &count
    b.emit(_i(0x23, 3, 2, 0), 0)                    # lw v0,(count)
    b.branch(0x04, 2, 0, "done")                    # beq v0,zero
    b.emit(0)
    b.emit(
        _i(0x09, 2, 2, -1),                         # addiu v0,v0,-1
        _i(0x2B, 3, 2, 0),                          # sw v0,(count)
    )
    b.label("done")
    b.emit(_r(31, 0, 0, 0, 0x08), 0)                # jr ra
    return b.build()


def _build_send_complete() -> bytes:
    """Take the token, then say so. The commit's success tail.

    Reached only from the one point that knows a gift was published -
    every bail above it in the commit leaves through the vanilla epilogue
    with the item untouched, so nothing that did not send can land here.

    `show_simple_action_message` is safe from here and would not be from
    the Send row: confirming a target tears the whole menu system down and
    returns control to the dungeon before the gameplay dispatcher calls
    `put_item_into_bag`, so this runs in the same ACTION context the
    locked-elevator refusal draws from. That is why the text is
    battle-encoded and the refusal's is full-width CP932 - two different
    drawers, two different encodings.

    Register-wise this is the loose end of the commit rather than the
    middle of it: the mailbox is published and the order table compacted,
    and the commit's next act is the epilogue, which restores the frame.
    `ra` is spent here (the frame holds the real one), so the routine
    keeps its own in a small one-word frame of its own.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -8),                       # addiu sp,sp,-8
        _i(0x2B, 29, 31, 0),                        # sw ra,0(sp)
        _j(0x03, SEND_TOKEN_SPEND_ADDRESS),         # jal spend
        0,
    )
    _load_address(b, 4, SEND_COMPLETE_MESSAGE_ADDRESS)
    b.emit(
        _j(0x03, SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS),
        0,
        _i(0x23, 29, 31, 0),                        # lw ra,0(sp)
        _i(0x09, 29, 29, 8),                        # (load delay) sp += 8
        _r(31, 0, 0, 0, 0x08),                      # jr ra
        0,
    )
    return b.build()


def _build_marker_text_builder() -> bytes:
    """`build_marker_text(a0 = descriptor, a1 = with_owner) -> v0 = string`.

    Replays this placement's pooled text into a buffer as full-width CP932,
    which costs almost nothing because of one fact about `append_encoded_text`
    at `0x80099194`: outside compact mode it copies bytes through, and *inside*
    compact mode it expands each glyph to a two-byte entry from the table at
    `[0x800DCF60]`. Its output is therefore already the encoding both the name
    and description lookups return, so the seed page stores the text only once,
    in the compact form the pickup message already uses.

    `with_owner` adds the town shop's second line - a bare `0x0A` newline, then
    `for <player>` - and is set for the description box and clear for the item
    name. It does not depend on whether the placement is local: the box always
    says who the item is for.

    An ordinary subroutine: its own frame, `s0`-`s2` preserved, `jr ra`.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -0x18),  # addiu sp,sp,-0x18
        _i(0x2B, 29, 31, 0),  # sw ra,0(sp)
        _i(0x2B, 29, 16, 4),  # sw s0,4(sp)
        _i(0x2B, 29, 17, 8),  # sw s1,8(sp)
        _i(0x2B, 29, 18, 12),  # sw s2,12(sp)
        _i(0x2B, 29, 5, 16),  # sw a1,16(sp)   with_owner
    )

    # Validation is the page header: the loaded page must be for the current
    # floor (the loader refreshes it every floor build), and the marker's slot
    # byte must be 0 or 1. A marker examined on a floor whose page is not
    # loaded - floor 40, or a mid-transition edge - takes the fallback name.
    #
    # No page-magic compare here, deliberately: a window that holds no page
    # reads floor 0, which never equals a current floor of 1..39, so the
    # floor-equality check subsumes it - and this routine's size is pinned by
    # the bonus floor's baked `j MARKER_NAME_ENTRY` (see the assert under
    # resolve_marker_code_layout); the five words the magic check would cost
    # are exactly the budget that keeps that address where the sector expects.
    _load_address(b, 18, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x0F, 0, 9, 0x8008),                         # lui t1,0x8008
        _i(0x25, 9, 10, 0x146C),                        # lhu t2,current floor
        _i(0x25, 18, 9, FLOOR_PAGE_HEADER_OFFSET + 4),  # lhu t1,page floor
        0,
    )
    b.branch(0x05, 9, 10, "fallback")                   # bne t1,t2
    b.emit(
        _i(0x24, 4, 11, 2),                             # lbu t3,2(a0)   slot
        0,
        _i(0x0B, 11, 10, MARKER_SLOT_COUNT),            # sltiu t2,t3,SLOT_COUNT
    )
    b.branch(0x04, 10, 0, "fallback")
    b.emit(0)

    # s2 = seed page, s1 = this floor-slot's three-byte record, s0 = cursor.
    b.emit(
        _r(0, 11, 10, 1, 0x00),  # sll t2,t3,1
        _r(10, 11, 10, 0, 0x21),  # addu t2,t2,t3     slot * 3
        _r(18, 10, 17, 0, 0x21),  # addu s1,s2,t2
        _i(0x09, 17, 17, FLOOR_PAGE_RECORDS_OFFSET),
    )
    _load_address(b, 16, MARKER_DESCRIPTION_BUFFER_ADDRESS)

    # The item name: the whole answer for the message system, and the first
    # line of the description box. Slot size 0x38: x*56 = (x<<6)-(x<<3).
    b.emit(
        _i(0x24, 17, 9, 0),  # lbu t1,0(s1)   item slot
        0,
        _r(0, 9, 10, 6, 0x00),  # sll t2,t1,6
        _r(0, 9, 9, 3, 0x00),   # sll t1,t1,3
        _r(10, 9, 9, 0, 0x23),  # subu t1,t2,t1
        _r(9, 18, 4, 0, 0x21),  # addu a0,s2,t1
        _i(0x09, 4, 4, FLOOR_PAGE_ITEM_SLOTS_OFFSET),
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),  # (delay) addu a1,s0,zero
        _r(2, 0, 16, 0, 0x21),  # addu s0,v0,zero
    )

    # The owner line is asked for or not; a local placement gets one too,
    # because "for <you>" is still the answer to who this is for.
    b.emit(_i(0x23, 29, 9, 16), 0)  # lw t1,16(sp)   with_owner
    b.branch(0x04, 9, 0, "finish")
    b.emit(0)

    # Item slot text is size-capped by construction now, but keep the cursor
    # check: the buffer is shared and the guard is one compare.
    _load_address(b, 8, MARKER_DESCRIPTION_BUFFER_ADDRESS)
    b.emit(
        _r(16, 8, 10, 0, 0x23),  # subu t2,s0,t0
        _i(0x0B, 10, 11, MARKER_DESCRIPTION_BUFFER_SIZE // 2),
    )
    b.branch(0x04, 11, 0, "finish")
    b.emit(
        0,
        _i(0x09, 18, 4, FLOOR_PAGE_FRAGMENT_FOR),
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),
        _r(2, 0, 16, 0, 0x21),
        _i(0x24, 17, 9, 1),  # lbu t1,1(s1)   player slot
        0,
        # Player slot size 0x24: x*36 = (x<<5)+(x<<2).
        _r(0, 9, 10, 5, 0x00),  # sll t2,t1,5
        _r(0, 9, 9, 2, 0x00),   # sll t1,t1,2
        _r(10, 9, 9, 0, 0x21),  # addu t1,t2,t1
        _r(9, 18, 4, 0, 0x21),  # addu a0,s2,t1
        _i(0x09, 4, 4, FLOOR_PAGE_PLAYER_SLOTS_OFFSET),
        _j(0x03, _APPEND_ENCODED_TEXT_ADDRESS),
        _r(16, 0, 5, 0, 0x21),
        _r(2, 0, 16, 0, 0x21),
    )

    b.label("finish")
    b.emit(_i(0x28, 16, 0, 0))  # sb zero,0(s0)
    _load_address(b, 2, MARKER_DESCRIPTION_BUFFER_ADDRESS)
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    # Anything that fails the range test still needs a printable answer.
    b.label("fallback")
    _load_address(b, 2, MARKER_DISPLAY_NAME_ADDRESS)

    b.label("return")
    b.emit(
        _i(0x23, 29, 31, 0),
        _i(0x23, 29, 16, 4),
        _i(0x23, 29, 17, 8),
        _i(0x23, 29, 18, 12),
        _r(31, 0, 0, 0, 0x08),  # jr ra
        _i(0x09, 29, 29, 0x18),  # (delay) addiu sp,sp,0x18
    )
    return b.build()


def _emit_inherited_epilogue(b: _MipsBuilder) -> None:
    """Unwind the frame the displaced routine had already built.

    `0x8004AC3C` and `0x80049374` happen to be built identically -
    `addiu sp,sp,-0x20` with `s0` at 0x10, `s1` at 0x14 and `ra` at 0x18 - so
    both entry points leave the same way.
    """

    b.emit(
        _i(0x23, 29, 31, 0x18),  # lw ra,0x18(sp)
        _i(0x23, 29, 17, 0x14),  # lw s1,0x14(sp)
        _i(0x23, 29, 16, 0x10),  # lw s0,0x10(sp)
        _r(31, 0, 0, 0, 0x08),  # jr ra
        _i(0x09, 29, 29, 0x20),  # (delay) addiu sp,sp,0x20
    )


def _build_marker_name_entry() -> bytes:
    """Name a marker, differently depending on who asked.

    `a0` is the descriptor and `a2` is the return address the resident guard
    captured before its own `jal` destroyed it.

    The message system gets the real item name, because `You're on ...` has a
    whole box to put it in. The item-name *field* gets `Strange...`: it is one
    narrow line shared with every native item, and a multiworld name will not
    fit. That is the same split the town shop already makes.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x0F, 0, 9, MESSAGE_NAME_CALLER_RETURN_ADDRESS >> 16),
        _i(0x0D, 9, 9, MESSAGE_NAME_CALLER_RETURN_ADDRESS & 0xFFFF),
    )
    b.branch(0x05, 6, 9, "placeholder")
    b.emit(
        0,
        _j(0x03, MARKER_TEXT_BUILDER_ADDRESS),
        _r(0, 0, 5, 0, 0x21),  # (delay) a1 = 0, no owner line
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    b.label("placeholder")
    _load_address(b, 2, MARKER_DISPLAY_NAME_ADDRESS)

    b.label("return")
    _emit_inherited_epilogue(b)
    return b.build()


def _build_marker_describe_entry() -> bytes:
    """Show a marker's description, in the town shop's two-line form.

    **The routine this replaces returns void.** It resolves a description
    pointer and calls `show_item_description` itself; handing a string back
    instead is why V80 drew no description box at all.
    """

    b = _MipsBuilder()
    b.emit(
        _j(0x03, MARKER_TEXT_BUILDER_ADDRESS),
        _i(0x09, 0, 5, 1),  # (delay) a1 = 1, with the owner line
        _j(0x03, SHOW_ITEM_DESCRIPTION_ADDRESS),
        _r(2, 0, 4, 0, 0x21),  # (delay) move a0,v0
    )
    _emit_inherited_epilogue(b)
    return b.build()


# The receive cursor-commit stub (`_build_receive_cursor_commit`) lived here
# from 2026-07-30 to 2026-08-05. It stored the delivered request sequence into
# the durable receive cursor with an absolute `sw`, which rolled the cursor
# BACK whenever a request stranded in the resident mailbox across a town
# round-trip delivered after Nada had advanced the cursor past it - the root
# cause of the Nada receive duplication. See the comment above
# RECEIVE_DISPATCHER_DELIVERED_RETURN_ADDRESS; the removal returns 20 bytes to
# the marker-code tail.
_MARKER_CODE_BLOCKS = (
    ("MARKER_TEXT_BUILDER_ADDRESS", "_build_marker_text_builder"),
    ("MARKER_NAME_ENTRY_ADDRESS", "_build_marker_name_entry"),
    ("MARKER_DESCRIBE_ENTRY_ADDRESS", "_build_marker_describe_entry"),
)


def resolve_marker_code_layout() -> tuple[tuple[int, bytes], ...]:
    """Pack the payload-side marker code and hand back (address, bytes).

    Two passes, for the same reason `alternate_pickup` uses two: a builder has
    to be run to be measured, and a jump target changes an instruction's bits
    but never its length.
    """

    globals_ = globals()
    for _ in range(2):
        address = MARKER_CODE_ADDRESS
        built: list[tuple[int, bytes]] = []
        for name, builder in _MARKER_CODE_BLOCKS:
            globals_[name] = address
            payload = globals_[builder]()
            built.append((address, payload))
            address += len(payload)

    if address > MARKER_PRESENTATION_END_ADDRESS:
        raise ValueError(
            f"The marker code needs {address - MARKER_CODE_ADDRESS} bytes and "
            f"the gameplay payload's tail has "
            f"{MARKER_PRESENTATION_END_ADDRESS - MARKER_CODE_ADDRESS}. It ends "
            f"at 0x{address:08x}."
        )
    return tuple(built)


# Resolve at import, so the three address constants are never read stale. This
# is the same trap `alternate_pickup` has: until this runs they all still say
# `MARKER_CODE_ADDRESS`, and `alternate_pickup` builds its resident guards from
# two of them at *its* import time. Generation happened to call the resolver
# first and was correct; `Verify-AdapDisc.py` did not, and reported a false
# mismatch against the very disc it had just checked byte for byte.
resolve_marker_code_layout()

# The bonus floor's item-name veil (CODE_SECTOR +0x164, hand-assembled hex
# with no MIPS source) ends with a baked `j 0x801D9E6C` back into the marker
# span - it predates a name for the routine it targets. Any size change to a
# block packed BEFORE the name entry moves this address and the veil jumps
# mid-instruction-stream: that shipped once (2026-08-09, the floor-paging
# marker-builder rewrite grew by 20 bytes and pickups crashed at
# 0x801D9FE8). Fail generation instead.
_BONUS_VEIL_NAME_ENTRY_ADDRESS = TOWER_GAMEPLAY_BASE_ADDRESS + 0x76C
if MARKER_NAME_ENTRY_ADDRESS != _BONUS_VEIL_NAME_ENTRY_ADDRESS:
    raise AssertionError(
        f"MARKER_NAME_ENTRY_ADDRESS is 0x{MARKER_NAME_ENTRY_ADDRESS:08X} but the "
        f"bonus floor's veil jumps to 0x{_BONUS_VEIL_NAME_ENTRY_ADDRESS:08X} "
        "(bonus_floor.CODE_SECTOR +0x164). A marker-span routine changed size; "
        "restore the packing or rebuild the bonus sector."
    )


def _assert_bonus_edits_leave_the_wrapper_alone() -> None:
    """The wrapper's spawner call word belongs to the base patch.

    It carries `jal FLOOR_PAGE_LOADER` (Rebuild-AdapGameplayPayload.py), and
    `bonus_floor._append` EDITS overlapping bytes IN PLACE - a bonus record
    over this word replaces the interposition without any conflict surfacing,
    which is exactly how the floor-page loader silently dropped out of the
    chain on the first paging ride (floors 2+ lost their pickup text). The
    retired record must stay retired.
    """

    wrapper_call_file_offset = 0x0F_F220 + (
        FLOOR_LOCATION_HOOK_WRAPPER_ADDRESS + 8 - TOWER_GAMEPLAY_BASE_ADDRESS
    )
    sector, within = divmod(wrapper_call_file_offset, FORM1_USER_SIZE)
    raw = (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
    for record_offset, payload in bonus_floor.EDITS:
        if record_offset < raw + 4 and raw < record_offset + len(payload):
            raise AssertionError(
                f"bonus_floor.EDITS record at 0x{record_offset:X} covers the "
                f"wrapper's spawner call word (raw 0x{raw:X}); it would silently "
                "replace the floor-page loader interposition."
            )


_assert_bonus_edits_leave_the_wrapper_alone()


def _build_render_resolver() -> bytes:
    # Return the gift render asset for every valid AP marker. Ownership remains
    # seed data for Found/Sent text only and must not leak through the model.
    # This resolver is used by both the ground entity and the held-item object,
    # so validate the complete marker before interpreting byte 2 as a location
    # slot. Every ordinary descriptor still tail-calls the vanilla resolver.
    b = _MipsBuilder()
    b.emit(
        _i(0x24, 4, 9, 0),
        _i(0x09, 0, 10, MARKER_ID),
    )
    b.branch(0x05, 9, 10, "local")
    b.emit(
        _i(0x24, 4, 9, 1),
        _i(0x09, 0, 10, MARKER_CATEGORY),
    )
    b.branch(0x05, 9, 10, "local")
    b.emit(
        _i(0x24, 4, 9, 3),
        _i(0x09, 0, 10, MARKER_STATUS),
    )
    b.branch(0x05, 9, 10, "local")
    b.emit(0)
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x23, 8, 9, 0),
        _i(0x0F, 0, 10, SEED_MAGIC >> 16),
        _i(0x0D, 10, 10, SEED_MAGIC),
    )
    b.branch(0x05, 9, 10, "local")
    b.emit(
        0,
        _i(0x0F, 0, 9, 0x8008),
        _i(0x25, 9, 10, 0x146C),
        0,
        _i(0x09, 10, 10, -1),
        _i(0x0B, 10, 11, 39),
    )
    b.branch(0x04, 11, 0, "local")
    # The slot byte, bounded by the slot count. This used to fold floor and
    # slot into a bit index (`(floor-1)*2 + slot < LOCATION_COUNT`) - the
    # unrolled arithmetic that does not follow MARKER_SLOT_COUNT; the floor is
    # already bounded above, so bounding the slot on its own is the same test
    # without the multiply.
    b.emit(
        _i(0x24, 4, 11, 2),
        0,
        _i(0x0B, 11, 11, MARKER_SLOT_COUNT),
    )
    b.branch(0x04, 11, 0, "local")
    b.emit(
        _i(0x0F, 0, 2, 0x8007),
        _i(0x0D, 2, 2, 0x7950),
        _r(31, 0, 0, 0, 0x08),
        0,
    )
    b.label("local")
    b.emit(_j(0x02, 0x800A_7A38), 0)
    return b.build()


def encode_elevator_return_prompt() -> bytes:
    """Encode the two-page locked-elevator return prompt.

    The native elevator menu appends its own Yes/No choices. Byte 0x11 is the
    standard dialogue page break, so the locked explanation is acknowledged
    before the return question and choices appear.
    """

    return (
        _full_width_cp932("The elevator is locked.")
        + b"\x11\x0A"
        + _full_width_cp932("Return to Town?")
        + b"\x0A\x00"
    )


def _build_elevator_prompt_callback() -> bytes:
    """Replace the native elevator prompt according to what the floor allows.

    Three cases, and the floor's own generated contents decide which:

    | Below the clearance ceiling | native prompt; Yes ascends |
    | At the ceiling, no keycard here | `The elevator is locked.` then `Return to Town?` |
    | At the ceiling, keycard here | native prompt; Yes is refused with `The elevator is locked.` |

    The third is the true-max-floor rule: a keycard on this floor means the
    player *could* still progress, so the escape hatch is withheld and they
    keep playing or spend a Wind Crystal. Suppressing its prompt entirely is
    wanted but not yet possible; see the note at the branch.
    """

    b = _MipsBuilder()
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x23, 8, 9, 0),
        _i(0x0F, 0, 10, SEED_MAGIC >> 16),
        _i(0x0D, 10, 10, SEED_MAGIC),
    )
    b.branch(0x05, 9, 10, "native")
    b.emit(
        0,
        _i(0x25, 8, 9, 4),
        _i(0x09, 0, 10, SEED_VERSION),
    )
    b.branch(0x05, 9, 10, "native")
    b.emit(0)

    _load_address(b, 8, 0x8008_146C)
    b.emit(_i(0x25, 8, 9, 0))
    _load_address(b, 8, HIGH_MAILBOX_ADDRESS + 0x18)
    b.emit(
        _i(0x24, 8, 10, 0),
        0,
        _r(0, 10, 11, 2, 0x00),  # clearance * 4
        _r(11, 10, 11, 0, 0x21),  # clearance * 5
        _i(0x09, 11, 11, 4),
    )
    b.branch(0x05, 9, 11, "native")
    b.emit(0)

    # A floor bit is set when either of its two generated rewards is a
    # Progressive Keycard. Only the exact current floor is considered.
    b.emit(
        _i(0x09, 9, 12, -1),
        _r(0, 12, 13, 3, 0x02),
    )
    _load_address(b, 8, SEED_BLOCK_ADDRESS + FLOOR_KEYCARD_MASK_OFFSET)
    b.emit(
        _r(8, 13, 8, 0, 0x21),
        _i(0x24, 8, 9, 0),
        _i(0x09, 0, 10, 1),
        _i(0x0C, 12, 12, 7),
        _r(12, 10, 10, 0, 0x04),
        _r(9, 10, 9, 0, 0x24),
    )
    # A keycard here means the player can still progress, so no Return to Town
    # is offered and the native prompt runs. Answering yes reaches the gate
    # handler, which refuses the ascent and shows the locked message.
    #
    # **Do not make this return zero to suppress the prompt.** 0.9.36 tried it
    # and softlocked: whatever consumes the request retries a null constructor
    # every frame, so the message appended forever. Suppressing the prompt needs
    # an object that closes itself, and nothing found so far signals "the player
    # acknowledged a page with no choices" - `poll_ui_selection` only reports
    # story flags 3/4/5, which a choice sets, and `update_ui_object` self-closes
    # only on button bit 0x0020, which is cancel.
    b.branch(0x05, 9, 0, "native")
    b.emit(0)

    b.emit(
        _i(0x09, 29, 29, -24),
        _i(0x2B, 29, 31, 20),
        _i(0x2B, 29, 16, 16),
        _j(0x03, _ALLOCATE_UI_OBJECT_ADDRESS),
        _r(0, 0, 4, 0, 0x21),
        _r(2, 0, 16, 0, 0x21),
    )
    b.branch(0x04, 16, 0, "finish")
    b.emit(
        0,
        _j(0x03, _INITIALIZE_UI_TEXTURES_ADDRESS),
        0,
        _j(0x03, _RESET_UI_SELECTION_ADDRESS),
        _r(0, 0, 4, 0, 0x21),
        _j(0x03, _INITIALIZE_UI_SELECTION_ADDRESS),
        0,
    )
    _load_address(b, 4, ELEVATOR_RETURN_PROMPT_ADDRESS)
    b.emit(_j(0x03, _BUILD_YES_NO_PROMPT_ADDRESS), 0)
    _load_address(b, 8, ELEVATOR_CHOICE_CALLBACK_ADDRESS)
    b.emit(
        _i(0x2B, 16, 8, 0x10),
        _r(16, 0, 2, 0, 0x21),
    )

    b.label("finish")
    b.emit(
        _i(0x23, 29, 31, 20),
        _i(0x23, 29, 16, 16),
        _i(0x09, 29, 29, 24),
        _r(31, 0, 0, 0, 0x08),
        0,
    )

    b.label("native")
    b.emit(_j(0x02, _ORIGINAL_ELEVATOR_PROMPT_CALLBACK_ADDRESS), 0)
    return b.build()


def _build_elevator_choice_callback() -> bytes:
    """Mirror the native Yes/No callback, tagging Yes as a tower return."""

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -24),
        _i(0x2B, 29, 31, 20),
        _i(0x2B, 29, 16, 16),
        _r(4, 0, 16, 0, 0x21),
        _j(0x03, _POLL_UI_SELECTION_ADDRESS),
        0,
    )
    b.branch(0x04, 2, 0, "pending")
    b.emit(_i(0x09, 0, 8, 1))
    b.branch(0x05, 2, 8, "close")
    b.emit(0)

    b.emit(_j(0x03, _CLOSE_UI_SELECTION_ADDRESS), 0)
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(
        _i(0x09, 0, 9, 1),
        _i(0x2B, 8, 9, ELEVATOR_RETURN_REQUEST_OFFSET),
    )
    _load_address(b, 8, 0x8008_2EB0)
    b.emit(
        _i(0x2B, 8, 0, 0),
        _i(0x09, 0, 9, 0x13),
        _i(0x2B, 8, 9, 8),
    )
    b.branch(0x04, 0, 0, "closed")
    b.emit(0)

    b.label("close")
    b.emit(_j(0x03, _CLOSE_UI_SELECTION_ADDRESS), 0)

    b.label("closed")
    b.emit(
        _i(0x25, 16, 8, -2),
        0,
        _i(0x0D, 8, 8, 0x8000),
        _i(0x29, 16, 8, -2),
    )
    _load_address(b, 9, 0x8008_14A0)
    b.emit(
        _i(0x23, 9, 8, 0),
        0,
        _i(0x0D, 8, 8, 0x8000),
        _i(0x2B, 9, 8, 0),
    )
    b.branch(0x04, 0, 0, "return")
    b.emit(0)

    b.label("pending")
    b.emit(
        _j(0x03, _UPDATE_UI_OBJECT_ADDRESS),
        _r(16, 0, 4, 0, 0x21),
    )

    b.label("return")
    b.emit(
        _i(0x23, 29, 31, 20),
        _i(0x23, 29, 16, 16),
        _i(0x09, 29, 29, 24),
        _r(31, 0, 0, 0, 0x08),
        0,
    )
    return b.build()


def _build_elevator_gate_handler() -> bytes:
    """Keep the clearance gate and turn an accepted return into Wind Crystal use."""

    b = _MipsBuilder()
    _load_address(b, 8, SEED_BLOCK_ADDRESS)
    b.emit(_i(0x23, 8, 9, ELEVATOR_RETURN_REQUEST_OFFSET), 0)
    b.branch(0x04, 9, 0, "gate")
    b.emit(_i(0x2B, 8, 0, ELEVATOR_RETURN_REQUEST_OFFSET))

    # The generic item dispatcher increments this count before ordinary Wind
    # Crystal use. Its asynchronous cleanup decrements it, so synthesize the
    # same bookkeeping before entering the native action.
    _load_address(b, 8, 0x8008_346A)
    b.emit(
        _i(0x25, 8, 9, 0),
        0,
        _i(0x09, 9, 9, 1),
        _i(0x29, 8, 9, 0),
    )
    _load_address(b, 8, 0x800E_3D7C)
    b.emit(_i(0x23, 8, 4, 0))
    _load_address(b, 5, SEED_BLOCK_ADDRESS + ELEVATOR_RETURN_DESCRIPTOR_OFFSET)
    b.emit(
        _i(0x09, 0, 6, 3),
        # Tail-call the native action so its return reaches the original
        # elevator dispatcher. A JAL here would replace that caller's RA.
        _j(0x02, _WIND_CRYSTAL_USE_ADDRESS),
        _r(0, 0, 7, 0, 0x21),
    )

    b.label("gate")
    # On the bonus floor a Yes must not ride at all - the routine in the
    # bonus block arms the earthquake collapse instead (Koh falls with the
    # floor back onto the same floor number; see the comment block above
    # bonus_floor.ELEVATOR_SAME_FLOOR_POKE_ADDRESS). On every other floor it
    # restores the commit's floor increments and re-enters at "gate_body"
    # for the normal clearance gate. The routine bakes that return address,
    # so the layout is asserted after build.
    b.emit(_j(0x02, bonus_floor.ELEVATOR_SAME_FLOOR_POKE_ADDRESS), 0)
    b.label("gate_body")
    _load_address(b, 8, 0x8008_146C)
    b.emit(
        _i(0x25, 8, 9, 0),
        _i(0x09, 0, 10, 5),
        _r(9, 10, 0, 0, 0x1B),
        _r(0, 0, 11, 0, 0x10),
        _i(0x09, 0, 10, 4),
    )
    b.branch(0x05, 11, 10, "allowed")
    b.emit(_r(0, 0, 11, 0, 0x12))
    _load_address(b, 8, HIGH_MAILBOX_ADDRESS + 0x18)
    b.emit(
        _i(0x24, 8, 10, 0),
        _i(0x09, 11, 11, 1),
        _r(10, 11, 8, 0, 0x2B),
    )
    b.branch(0x04, 8, 0, "allowed")
    b.emit(0)
    _load_address(b, 4, ELEVATOR_LOCKED_MESSAGE_ADDRESS)
    b.emit(
        _j(0x03, _SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS),
        0,
        _j(0x02, _CANCELLED_ACTION_CLEANUP_ADDRESS),
        0,
    )

    b.label("allowed")
    b.emit(_j(0x02, _BEGIN_ASCENDING_ELEVATOR_ADDRESS), 0)
    code = b.build()
    gate_body = ELEVATOR_GATE_HANDLER_ADDRESS + b.labels["gate_body"] * 4
    if gate_body != bonus_floor.ELEVATOR_GATE_BODY_ADDRESS:
        raise ValueError(
            f"The elevator gate body moved to {gate_body:#x}; the same-floor "
            f"poke routine in bonus_floor.CODE_SECTOR jumps back to "
            f"{bonus_floor.ELEVATOR_GATE_BODY_ADDRESS:#x} and must be "
            "regenerated to match."
        )
    return code


@dataclass(frozen=True)
class LocationPlacement:
    item_name: str
    recipient_name: str
    remote: bool
    progressive_keycard: bool = False
    # One of this world's own trap items, wearing the Progressive Keycard
    # disguise. Rides the floor-page record's form byte as bit 0x80 (see
    # FLOOR_PAGE_FORM_TRAP) so the game can suppress the pickup message for
    # it without any other placement being affected.
    trap: bool = False


def make_seed_signature(seed_name: str, player: int, player_name: str) -> bytes:
    identity = f"{seed_name}\0{player}\0{player_name}".encode("utf-8")
    return hashlib.blake2s(identity, digest_size=8, person=b"ADAPv1").digest()


def _build_tower_floor_bootstrap_helper() -> bytes:
    """Consume a one-use Uncle floor marker without changing vanilla entries.

    Also refills the ball charger's per-visit allowance - see
    `BALL_CHARGE_USED_ADDRESS`.
    """

    # The overlay hook at 0x80016448 executes its original LUI a0,0x8001 in the
    # jump delay slot.  A signed LH makes bit 15 directly testable by BLTZ.
    # Unmarked values return to the exact vanilla new-run continuation.  Marked
    # values are stripped, copied to current floor, and skip only the hardcoded
    # floor-1 store before rejoining the original counter/setup sequence.
    # a0 is the save-data base (0x80010000) the whole way through - the hook's
    # own delay slot loads it and the marked path stores the floor through it -
    # so every save-block byte this helper writes addresses off a0 rather than
    # rebuilding a base register. That is what pays for the allowance write
    # below without the helper leaving its slot.
    save_data_base = 0x8001_0000
    used_offset = BALL_CHARGE_USED_ADDRESS - save_data_base
    carrier_offset = SHORTCUT_PENDING_LEVEL_ADDRESS - save_data_base
    assert 0 <= used_offset < 0x8000 and 0 <= carrier_offset < 0x8000, (
        "The helper addresses both bytes off a0; they must be positive offsets."
    )

    b = _MipsBuilder()
    b.emit(
        _i(0x21, 4, 8, 0x0234),  # lh t0,0x234(a0)
        _i(0x0F, 0, 5, 0x8008),  # load delay; lui a1,0x8008
        # Refill the ball charger's per-town-visit allowance. This is the only
        # seam that runs on BOTH ascents and only ever inside the tower, which
        # is exactly the rule: the allowance is spent in town and one climb -
        # any climb - hands it back.
        _i(0x28, 4, 0, used_offset),  # sb zero,used(a0)
    )
    b.branch(0x01, 8, 0, "marked")  # bltz t0,marked
    b.emit(0)
    # Ordinary ascent: clear the carrier, then the exact vanilla continuation.
    b.emit(
        _i(0x28, 4, 0, carrier_offset),  # sb zero,carrier(a0)
        _j(0x02, 0x8001_6450),
        0,
    )

    b.label("marked")
    b.emit(
        _i(0x0C, 8, 8, 0x7FFF),  # andi t0,t0,0x7fff
        _i(0x29, 4, 8, 0x0234),  # sh t0,persistent floor
        _i(0x29, 5, 8, 0x146C),  # sh t0,current floor
    )

    # Stage the level the shortcut should start Koh at. Granting it here does
    # not work - his actor is allocated and templated after this runs - so the
    # wrapper at LEVEL_MONSTERS_CALL_SITE consumes this byte once Koh exists.
    #
    # Zero is written on every ordinary ascent too, so a value left over from a
    # previous shortcut cannot fire on a normal run.
    b.emit(_r(0, 0, 10, 0, 0x21))  # addu t2,zero,zero
    for floor, level in sorted(SHORTCUT_START_LEVELS.items()):
        b.emit(_i(0x09, 0, 9, floor))  # addiu t1,zero,floor
        b.branch(0x05, 8, 9, f"not_{floor}")  # bne t0,t1
        b.emit(
            0,
            _i(0x09, 0, 10, level),  # addiu t2,zero,level
        )
        b.label(f"not_{floor}")
    b.emit(_i(0x28, 4, 10, carrier_offset))  # sb t2,carrier(a0)

    b.label("resume")
    # Rebuild what the vanilla continuation expects: a0 is the save-data base
    # and v0 the new-run counter it was about to read.
    b.emit(
        _i(0x0F, 0, 4, 0x8001),  # lui a0,0x8001
        _i(0x23, 4, 2, 0x022C),  # lw v0,0x22c(a0)
        _j(0x02, 0x8001_645C),
        0,
    )
    return b.build()


def _emit_tower_resume_apply(b: _MipsBuilder) -> None:
    """Copy a staged run record into Koh, once, and consume the carrier.

    Appended to the monster-levelling wrapper rather than given a hook of its
    own, because it wants the exact seam that wrapper already occupies: Koh's
    actor is allocated and templated AFTER floor-state init, so anything
    written during generation is thrown away, and this is the first point at
    which Koh reliably exists. Reusing it also means no new PPF records - and
    therefore no Mode-2 EDC/ECC regeneration and no overlap audit - for a
    feature that would otherwise need three more hooks.

    The wrapper runs many times per floor (both real call sites are loop
    bodies), so this clears the magic on its first pass and every later pass
    falls straight through, exactly as the pending-level byte does.

    Registers: `t3` source cursor, `t4` destination cursor, `t5` counter,
    `t6` the word in flight, `t2`/`at` the magic test. All of them are rebuilt
    by the level-grant code that follows, so nothing here has to be preserved.
    """

    _load_address(b, 11, TOWER_RESUME_CARRIER_ADDRESS)    # t3 = &record
    b.emit(
        _i(0x23, 11, 10, TOWER_RESUME_MAGIC_OFFSET),      # lw t2,0x4c(t3)
        # These two fill the load delay AND build the comparand, so the slot
        # costs nothing.
        _i(0x0F, 0, 1, TOWER_RESUME_MAGIC >> 16),         # lui at
        _i(0x0D, 1, 1, TOWER_RESUME_MAGIC & 0xFFFF),      # ori at
    )
    b.branch(0x05, 10, 1, "no_resume")                    # bne t2,at
    b.emit(
        # The delay slot carries the first half of the destination address:
        # harmless when the branch is taken, free when it is not.
        _i(0x0F, 0, 12, _upper(KOH_UNIT_STATS_ADDRESS)),  # (delay) lui t4
        _i(0x09, 12, 12, _lower(KOH_UNIT_STATS_ADDRESS)),
        _i(0x09, 0, 13, TOWER_RESUME_RECORD_WORDS),       # addiu t5,zero,19
    )
    b.label("resume_copy")
    b.emit(
        _i(0x23, 11, 14, 0),                              # lw t6,0(t3)
        _i(0x09, 11, 11, 4),
        _i(0x09, 13, 13, -1),
        _i(0x2B, 12, 14, 0),                              # sw t6,0(t4)
    )
    b.branch(0x05, 13, 0, "resume_copy")
    b.emit(
        _i(0x09, 12, 12, 4),                              # (delay) t4 += 4
        # The cursor has walked the whole record, so it is now sitting on the
        # magic - no second address needed. One-shot: a record applied twice
        # would undo whatever the player did on the floor it resumed onto.
        _i(0x2B, 11, 0, 0),                               # sw zero,0(t3)
    )
    b.label("no_resume")


def _build_shortcut_level_grant() -> bytes:
    """Wrapper for the floor's monster-levelling call that also levels Koh.

    Installed over the `jal 0x800A08A0` at 0x800A0E28. That call is the last
    instruction of its caller before the epilogue, and the live trace put it
    after Koh's actor is allocated and templated - which is the whole reason the
    grant lives here rather than in the bootstrap helper.

    The original call runs first, unchanged, with the argument its delay slot
    already supplied. Then the carrier byte is consumed: only the three levels
    a shortcut can ask for are honoured, and it is cleared either way, so a
    stale or garbage value costs one ignored floor rather than a wrong Koh.

    level_up clobbers the caller-saved registers, so every pointer is rebuilt
    from scratch on each pass of the loop.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -16),  # addiu sp,sp,-16
        _i(0x2B, 29, 31, 0),  # sw ra,0(sp)
        _j(0x03, LEVEL_MONSTERS_ADDRESS),  # jal the call we displaced
        0,  # (delay) nothing: a0 is the caller's, set by its own delay slot
    )

    # A staged tower-resume record, if one is waiting. First, because a resume
    # supplies the level the shortcut grant would otherwise be asked for, and
    # the two are never both pending in practice - but if they ever were, the
    # grant's own "already at or above the target" test then sees the resumed
    # level and declines rather than levelling on top of it.
    _emit_tower_resume_apply(b)

    b.label("loop")
    _load_address(b, 11, SHORTCUT_PENDING_LEVEL_ADDRESS)
    b.emit(
        _i(0x24, 11, 10, 0),  # lbu t2,0(t3)   requested level
        0,  # load delay
    )
    for level in sorted(set(SHORTCUT_START_LEVELS.values())):
        b.emit(_i(0x09, 0, 9, level))  # addiu t1,zero,level
        b.branch(0x04, 10, 9, "requested")  # beq t2,t1
        b.emit(0)
    b.branch(0x04, 0, 0, "clear")  # not one of ours
    b.emit(0)

    b.label("requested")
    _load_address(b, 12, KOH_UNIT_STATS_ADDRESS)
    b.emit(
        _i(0x24, 12, 13, KOH_UNIT_STATS_LEVEL_OFFSET),  # lbu t5,0x11(t4)
        0,  # load delay
    )
    b.branch(0x04, 13, 0, "clear")  # uninitialised UnitStats
    b.emit(_r(13, 10, 14, 0, 0x2B))  # (delay) sltu t6,t5,t2
    b.branch(0x04, 14, 0, "clear")  # already at or above the target
    b.emit(
        0,
        _r(12, 0, 4, 0, 0x21),  # addu a0,t4,zero
        _j(0x03, LEVEL_UP_ADDRESS),
        _r(0, 0, 5, 0, 0x21),  # (delay) addu a1,zero,zero
    )
    b.branch(0x04, 0, 0, "loop")
    b.emit(0)

    b.label("clear")
    _load_address(b, 11, SHORTCUT_PENDING_LEVEL_ADDRESS)
    b.emit(
        _i(0x28, 11, 0, 0),  # sb zero,0(t3)
        _i(0x23, 29, 31, 0),  # lw ra,0(sp)
        0,  # load delay
        _r(31, 0, 0, 0, 0x08),  # jr ra
        _i(0x09, 29, 29, 16),  # (delay) addiu sp,sp,16
    )
    return b.build()


def build_seed_block(
    signature: bytes,
    placements: Sequence[LocationPlacement],
    send_targets: Sequence[str] = (),
    carrier_palette: int = CARRIER_PALETTE,
) -> bytes:
    if len(signature) != 8:
        raise ValueError("Azure Dreams seed signatures must be exactly eight bytes.")
    if len(placements) != LOCATION_COUNT:
        raise ValueError(f"Expected {LOCATION_COUNT} tower placements, got {len(placements)}.")

    block = bytearray(SEED_BLOCK_SIZE)
    struct.pack_into("<IHH", block, 0, SEED_MAGIC, SEED_VERSION, LOCATION_COUNT)
    block[8:16] = signature

    if TEST_STARTING_KEYCARD_LEVEL:
        print(
            "*** ADAP TEST BUILD: seeding Progressive Keycard level "
            f"{TEST_STARTING_KEYCARD_LEVEL} into the save state. "
            "This is not a real seed. ***"
        )

    initializer = _build_seed_state_initializer()
    appender = _build_message_appender()
    resolve_marker_code_layout()
    resolver = _build_render_resolver()
    elevator_prompt_callback = _build_elevator_prompt_callback()
    elevator_choice_callback = _build_elevator_choice_callback()
    elevator_gate_handler = _build_elevator_gate_handler()
    code_regions = (
        (0x40, initializer, APPEND_LOCATION_MESSAGE_ADDRESS - SEED_INIT_ADDRESS, "state initializer"),
        (0x140, appender, RESOLVE_LOCATION_RENDER_ADDRESS - APPEND_LOCATION_MESSAGE_ADDRESS, "message appender"),
        (
            0x1E0,
            resolver,
            ELEVATOR_PROMPT_CALLBACK_ADDRESS - RESOLVE_LOCATION_RENDER_ADDRESS,
            "render resolver",
        ),
        (
            0x280,
            elevator_prompt_callback,
            ELEVATOR_CHOICE_CALLBACK_ADDRESS - ELEVATOR_PROMPT_CALLBACK_ADDRESS,
            "elevator prompt callback",
        ),
        (
            0x380,
            elevator_choice_callback,
            ELEVATOR_GATE_HANDLER_ADDRESS - ELEVATOR_CHOICE_CALLBACK_ADDRESS,
            "elevator choice callback",
        ),
        (
            0x430,
            elevator_gate_handler,
            ELEVATOR_RETURN_PROMPT_ADDRESS - ELEVATOR_GATE_HANDLER_ADDRESS,
            "elevator gate handler",
        ),
    )
    for offset, code, capacity, name in code_regions:
        if len(code) > capacity:
            raise ValueError(f"Seed {name} is {len(code)} bytes but only {capacity} bytes are reserved.")
        block[offset : offset + len(code)] = code

    # The send-token strings and gate routine, in the window's static space.
    for offset, limit, payload, name in (
        # Full-width CP932, byte-identical in form to the game's own
        # `You need 2 familiars` at 0x8002E924 (verified against the menu
        # save state) - this is what the menu's message drawer expects,
        # NOT the compact battle encoding.
        (NO_SEND_TOKENS_MESSAGE_OFFSET, SEND_COMPLETE_MESSAGE_OFFSET,
         encode_menu_message(NO_SEND_TOKENS_TEXT), "no-tokens message"),
        (SEND_COMPLETE_MESSAGE_OFFSET, SEND_TOKEN_GATE_OFFSET,
         encode_battle_message(SEND_COMPLETE_TEXT), "sent message"),
        (SEND_TOKEN_GATE_OFFSET, SEND_TOKEN_CHECK_OFFSET,
         _build_send_token_gate(), "send-token gate"),
        (SEND_TOKEN_CHECK_OFFSET, SEND_TOKEN_SPEND_OFFSET,
         _build_send_token_check(), "send-token check"),
        (SEND_TOKEN_SPEND_OFFSET, SEND_COMPLETE_ROUTINE_OFFSET,
         _build_send_token_spend(), "send-token spend"),
        (SEND_COMPLETE_ROUTINE_OFFSET,
         SEND_TOKEN_BLOCK_OFFSET + SEND_TOKEN_BLOCK_CAPACITY,
         _build_send_complete(), "send-complete routine"),
    ):
        if offset + len(payload) > limit:
            raise ValueError(
                f"The {name} needs {len(payload)} bytes and has {limit - offset}."
            )
        block[offset : offset + len(payload)] = payload

    # EXPERIMENT - see CARRIER_PROBE. Delete with it.
    if CARRIER_PROBE:
        carrier_stubs = _build_carrier_stubs(carrier_palette)
        if len(carrier_stubs) > CARRIER_CODE_CAPACITY:
            raise ValueError(
                f"The carrier stubs need {len(carrier_stubs)} bytes and the "
                f"window has {CARRIER_CODE_CAPACITY}."
            )
        block[CARRIER_CODE_OFFSET : CARRIER_CODE_OFFSET + len(carrier_stubs)] = (
            carrier_stubs
        )
        forced = _build_carrier_forced_spawn()
        if CARRIER_FORCED_STUB_OFFSET + len(forced) > CARRIER_UNRESTRICTED_END:
            raise ValueError(
                f"The carrier forced-spawn stub needs {len(forced)} bytes and the "
                f"unrestricted run has "
                f"{CARRIER_UNRESTRICTED_END - CARRIER_FORCED_STUB_OFFSET}."
            )
        block[CARRIER_FORCED_STUB_OFFSET : CARRIER_FORCED_STUB_OFFSET + len(forced)] = (
            forced
        )

    locked = encode_battle_message(ELEVATOR_LOCKED_MESSAGE_TEXT)
    if (
        ELEVATOR_LOCKED_MESSAGE_OFFSET + len(locked)
        > RESOLVE_LOCATION_RENDER_ADDRESS - SEED_BLOCK_ADDRESS
    ):
        raise ValueError("The locked-elevator message overruns the render resolver.")
    block[
        ELEVATOR_LOCKED_MESSAGE_OFFSET : ELEVATOR_LOCKED_MESSAGE_OFFSET + len(locked)
    ] = locked

    prompt = encode_elevator_return_prompt()
    prompt_offset = ELEVATOR_RETURN_PROMPT_ADDRESS - SEED_BLOCK_ADDRESS
    if prompt_offset + len(prompt) > FLOOR_PAGE_WINDOW_OFFSET:
        raise ValueError("Elevator return prompt overlaps the floor-page window.")
    block[prompt_offset : prompt_offset + len(prompt)] = prompt
    block[ELEVATOR_RETURN_DESCRIPTOR_OFFSET : ELEVATOR_RETURN_DESCRIPTOR_OFFSET + 4] = (
        b"\x03\x06\x00\x0D"
    )

    hud_post = _build_inventory_hud_post_registration_hook()
    hud_refresh = _build_inventory_hud_refresh()
    hud_post_offset = (
        INVENTORY_HUD_POST_REGISTRATION_ADDRESS - SEED_BLOCK_ADDRESS
    )
    hud_refresh_offset = INVENTORY_HUD_REFRESH_ADDRESS - SEED_BLOCK_ADDRESS
    if hud_post_offset + len(hud_post) > hud_refresh_offset:
        raise ValueError("Inventory HUD registration hook exceeds its seed-page span.")
    if hud_refresh_offset + len(hud_refresh) > SEED_BLOCK_SIZE:
        raise ValueError("Inventory HUD refresh routine exceeds the seed page.")
    block[hud_post_offset : hud_post_offset + len(hud_post)] = hud_post
    block[hud_refresh_offset : hud_refresh_offset + len(hud_refresh)] = hud_refresh

    # The Send row's label strip and the routine that uploads it.
    #
    # With no other Azure Dreams player in the room the row does not exist, so
    # none of this is generated - the seed page keeps those bytes as zeros and
    # `tower_send` emits no records at all.
    if send_targets:
        send_upload = build_send_row_upload()
        if SEND_ROW_UPLOAD_OFFSET + len(send_upload) > SEND_ROW_ASSIGNER_OFFSET:
            raise ValueError(
                "The Send-row upload routine overruns the assigner stub."
            )
        block[
            SEND_ROW_UPLOAD_OFFSET : SEND_ROW_UPLOAD_OFFSET + len(send_upload)
        ] = send_upload
        send_assigner = build_send_row_assigner_stub()
        if SEND_ROW_ASSIGNER_OFFSET + len(send_assigner) > SEND_ROW_STRIP_OFFSET:
            raise ValueError("The Send-row assigner stub overruns its strip.")
        block[
            SEND_ROW_ASSIGNER_OFFSET :
            SEND_ROW_ASSIGNER_OFFSET + len(send_assigner)
        ] = send_assigner
        rail_cap = load_send_rail_cap()
        block[
            SEND_RAIL_CAP_OFFSET : SEND_RAIL_CAP_OFFSET + len(rail_cap)
        ] = rail_cap
        send_strip = load_send_row_strip()
        if (
            SEND_ROW_STRIP_OFFSET + len(send_strip)
            > TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET
        ):
            raise ValueError("The Send-row strip overruns the bootstrap helper.")
        block[
            SEND_ROW_STRIP_OFFSET : SEND_ROW_STRIP_OFFSET + len(send_strip)
        ] = send_strip

    floor_bootstrap = _build_tower_floor_bootstrap_helper()
    if (
        TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET + len(floor_bootstrap)
        > INVENTORY_HUD_CODE_OFFSET
    ):
        raise ValueError("Tower-floor bootstrap helper overlaps the inventory HUD.")
    block[
        TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET :
        TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET + len(floor_bootstrap)
    ] = floor_bootstrap

    level_grant = _build_shortcut_level_grant()
    if TOWER_FLOOR_BOOTSTRAP_HELPER_OFFSET + len(floor_bootstrap) > SHORTCUT_LEVEL_GRANT_OFFSET:
        raise ValueError("The tower-floor bootstrap helper overruns the level-grant routine.")
    if SHORTCUT_LEVEL_GRANT_OFFSET + len(level_grant) > INVENTORY_HUD_CODE_OFFSET:
        raise ValueError(
            "The shortcut level-grant routine overlaps the inventory HUD: "
            f"{len(level_grant)} bytes into "
            f"{INVENTORY_HUD_CODE_OFFSET - SHORTCUT_LEVEL_GRANT_OFFSET}. "
            "It carries the tower-resume apply as well, and the two together "
            "fill this slot EXACTLY - there is no spare word left here. The "
            "next thing that needs room takes it from the floor-page window's "
            "static tail (see SEND_COMPLETE_ROUTINE_OFFSET) or grows the seed "
            "page; do not silently shrink either routine to make space."
        )
    block[
        SHORTCUT_LEVEL_GRANT_OFFSET :
        SHORTCUT_LEVEL_GRANT_OFFSET + len(level_grant)
    ] = level_grant

    # Diagnostic hook for the message-region blocker. Setting
    # ADAP_DUMP_PLACEMENTS to a path appends one JSON line per world holding the
    # placements it would compile. It never changes what is generated; it exists
    # because pricing an encoding change needs a real fill, and a fill that
    # overflows the region aborts before anything is written.
    # tools/price-message-schemes.py consumes the file.
    _dump_path = os.environ.get("ADAP_DUMP_PLACEMENTS")
    if _dump_path:
        with open(_dump_path, "a", encoding="utf-8") as _dump_file:
            _dump_file.write(json.dumps([
                {
                    "item": placement.item_name,
                    "recipient": placement.recipient_name,
                    "remote": placement.remote,
                }
                for placement in placements
            ]) + "\n")

    composer = _build_pooled_message_composer()
    if len(composer) > FLOOR_PAGE_COMPOSER_CAPACITY:
        raise ValueError(
            f"Floor-page message composer is {len(composer)} bytes but only "
            f"{FLOOR_PAGE_COMPOSER_CAPACITY} are reserved."
        )
    block[FLOOR_PAGE_COMPOSER_OFFSET : FLOOR_PAGE_COMPOSER_OFFSET + len(composer)] = composer

    page_loader = _build_floor_page_loader()
    if len(page_loader) > FLOOR_PAGE_LOADER_CAPACITY:
        raise ValueError(
            f"Floor-page loader is {len(page_loader)} bytes but only "
            f"{FLOOR_PAGE_LOADER_CAPACITY} are reserved."
        )
    if FLOOR_PAGE_LOADER_OFFSET + len(page_loader) > SEND_ROW_CODE_OFFSET:
        raise ValueError("The floor-page loader overruns the Send-row code block.")
    block[FLOOR_PAGE_LOADER_OFFSET : FLOOR_PAGE_LOADER_OFFSET + len(page_loader)] = page_loader

    forced_trap_stub = _build_forced_trap_stub()
    if len(forced_trap_stub) > FORCED_TRAP_STUB_CAPACITY:
        raise ValueError(
            f"Forced-trap stub is {len(forced_trap_stub)} bytes but only "
            f"{FORCED_TRAP_STUB_CAPACITY} are reserved."
        )
    block[
        FORCED_TRAP_STUB_OFFSET : FORCED_TRAP_STUB_OFFSET + len(forced_trap_stub)
    ] = forced_trap_stub

    # The masks are per-placement and stay resident; only the TEXT is paged.
    for index, placement in enumerate(placements):
        if placement.remote:
            block[REMOTE_LOCATION_MASK_OFFSET + index // 8] |= 1 << (index & 7)
        if placement.progressive_keycard:
            # Per FLOOR, not per check. Hardcoded // 2 until 2026-08-15 - the
            # same unrolled slots-per-floor assumption that broke the put-in
            # guard, in Python this time. See docs/systems/third-floor-check.md.
            floor_index = index // MARKER_SLOT_COUNT
            block[FLOOR_KEYCARD_MASK_OFFSET + floor_index // 8] |= 1 << (floor_index & 7)

    for slot, text in (
        (FLOOR_PAGE_FRAGMENT_FOUND, "Found "),
        (FLOOR_PAGE_FRAGMENT_SENT, "Sent "),
        (FLOOR_PAGE_FRAGMENT_TO, " to "),
        (FLOOR_PAGE_FRAGMENT_PERIOD, "."),
        (FLOOR_PAGE_FRAGMENT_FOR, "\nfor "),
    ):
        encoded = encode_battle_message(text)
        if len(encoded) > FLOOR_PAGE_FRAGMENT_SLOT_SIZE:
            raise ValueError(f"Sentence fragment {text!r} does not fit its slot.")
        block[slot : slot + len(encoded)] = encoded

    # Bake floor 1's page so the first floor of a fresh boot has its text
    # before the loader has ever run (the loader then sees page floor == 1 and
    # skips its read).
    _write_floor_page_content(block, 1, placements)

    # The bonus floor's runtime code lives outside the seed page; only its
    # loader stub goes here, in the one gap that cannot move with seed content.
    bonus_floor.install_read_stub(block)

    return bytes(block)


def encode_item_slot_text(text: str) -> bytes:
    """Encode an item name into a fixed slot, truncating on ENCODED bytes.

    The compact battle encoding costs one byte per compact-alphabet character
    and two per full-width fallback, so a character budget would lie about an
    all-full-width name. The budget is applied to the ENCODED form: take the
    longest prefix whose optimal encoding (terminator included) fits the
    slot. Prefix-by-prefix rather than byte-slicing because the encoder is a
    mode-switching DP - a byte cut could land mid-glyph or strand an open
    compact-mode run.
    """

    for end in range(len(text), -1, -1):
        encoded = encode_battle_message(text[:end])
        if len(encoded) <= FLOOR_PAGE_ITEM_SLOT_SIZE:
            return encoded
    raise ValueError("An empty string does not fit the item slot; impossible.")


def _floor_placements(
    placements: Sequence[LocationPlacement], floor: int
) -> Sequence[LocationPlacement]:
    start = (floor - 1) * MARKER_SLOT_COUNT
    return placements[start : start + MARKER_SLOT_COUNT]


def _write_floor_page_content(
    block: bytearray, floor: int, placements: Sequence[LocationPlacement]
) -> None:
    """Write one floor's header, records and name slots into a window image.

    `block` is either the whole seed page (baking floor 1) or a bare window
    image shifted to offset -FLOOR_PAGE_WINDOW_OFFSET... it is simplest to
    always pass a buffer indexed by seed-page offset; the page-sector builder
    wraps accordingly.
    """

    struct.pack_into(
        "<IHH", block, FLOOR_PAGE_HEADER_OFFSET,
        FLOOR_PAGE_MAGIC, floor, FLOOR_PAGE_VERSION,
    )
    floor_slots = _floor_placements(placements, floor)
    player_names: list[str] = []
    for slot, placement in enumerate(floor_slots):
        if placement.recipient_name not in player_names:
            player_names.append(placement.recipient_name)
        name_index = player_names.index(placement.recipient_name)
        struct.pack_into(
            "<BBB", block, FLOOR_PAGE_RECORDS_OFFSET + slot * 3,
            slot, name_index,
            (FLOOR_PAGE_FORM_REMOTE if placement.remote else 0)
            | (FLOOR_PAGE_FORM_TRAP if placement.trap else 0),
        )
        item_encoded = encode_item_slot_text(placement.item_name)
        item_offset = FLOOR_PAGE_ITEM_SLOTS_OFFSET + slot * FLOOR_PAGE_ITEM_SLOT_SIZE
        block[item_offset : item_offset + FLOOR_PAGE_ITEM_SLOT_SIZE] = item_encoded.ljust(
            FLOOR_PAGE_ITEM_SLOT_SIZE, b"\0"
        )
    if len(player_names) > FLOOR_PAGE_PLAYER_SLOT_COUNT:
        raise ValueError("A floor has more recipients than placements; impossible.")
    for name_index in range(FLOOR_PAGE_PLAYER_SLOT_COUNT):
        text = player_names[name_index] if name_index < len(player_names) else ""
        encoded = encode_battle_message(text)
        if len(encoded) > FLOOR_PAGE_PLAYER_SLOT_SIZE:
            raise ValueError(
                f"Recipient name {text!r} encodes to {len(encoded)} bytes; an "
                "Archipelago slot name is capped at 16 characters and cannot "
                "exceed the 0x24-byte slot. Something upstream is wrong."
            )
        name_offset = FLOOR_PAGE_PLAYER_SLOTS_OFFSET + name_index * FLOOR_PAGE_PLAYER_SLOT_SIZE
        block[name_offset : name_offset + FLOOR_PAGE_PLAYER_SLOT_SIZE] = encoded.ljust(
            FLOOR_PAGE_PLAYER_SLOT_SIZE, b"\0"
        )


def build_floor_page_sectors(
    seed_block: bytes, placements: Sequence[LocationPlacement]
) -> tuple[bytes, ...]:
    """The per-seed bank: one 2048-byte sector per floor, 1..39.

    Every sector is the seed page's window - static content byte-identical to
    what boots resident - with that floor's header, records and name slots
    swapped in. The loader lands a sector directly over the window, so
    anything that differs between sectors other than the per-floor fields
    would be a generation bug; the paging test asserts the invariant.
    """

    if len(seed_block) != SEED_BLOCK_SIZE:
        raise ValueError("Seed block must be built before the floor pages.")
    if len(placements) != LOCATION_COUNT:
        raise ValueError(f"Expected {LOCATION_COUNT} tower placements, got {len(placements)}.")
    sectors: list[bytes] = []
    for floor in range(1, FLOOR_PAGE_FLOOR_COUNT + 1):
        image = bytearray(seed_block[:FLOOR_PAGE_WINDOW_END])
        _write_floor_page_content(image, floor, placements)
        sectors.append(bytes(image[FLOOR_PAGE_WINDOW_OFFSET:FLOOR_PAGE_WINDOW_END]))
    return tuple(sectors)


def _bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def _edc_lut() -> tuple[int, ...]:
    table = []
    for value in range(256):
        edc = value
        for _ in range(8):
            edc = (edc >> 1) ^ (0xD801_8001 if edc & 1 else 0)
        table.append(edc & 0xFFFF_FFFF)
    return tuple(table)


def _ecc_luts() -> tuple[tuple[int, ...], tuple[int, ...]]:
    forward = []
    backward = [0] * 256
    for value in range(256):
        doubled = value << 1
        if doubled & 0x100:
            doubled ^= 0x11D
        forward.append(doubled)
        backward[value ^ doubled] = value
    return tuple(forward), tuple(backward)


_EDC_LUT = _edc_lut()
_ECC_FORWARD, _ECC_BACKWARD = _ecc_luts()


def _compute_edc(source: bytes | bytearray) -> int:
    edc = 0
    for value in source:
        edc = (edc >> 8) ^ _EDC_LUT[(edc ^ value) & 0xFF]
    return edc


def _compute_ecc(
    source: bytes | bytearray,
    major_count: int,
    minor_count: int,
    major_multiplier: int,
    minor_increment: int,
) -> bytes:
    size = major_count * minor_count
    result = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_multiplier + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            value = source[index]
            index += minor_increment
            if index >= size:
                index -= size
            ecc_a ^= value
            ecc_b ^= value
            ecc_a = _ECC_FORWARD[ecc_a]
        ecc_a = _ECC_BACKWARD[_ECC_FORWARD[ecc_a] ^ ecc_b]
        result[major] = ecc_a
        result[major + major_count] = ecc_a ^ ecc_b
    return bytes(result)


def build_mode2_form1_sector(lba: int, user_data: bytes, subheader: bytes = bytes(8)) -> bytes:
    if len(user_data) != FORM1_USER_SIZE:
        raise ValueError(f"Mode-2/Form-1 user data must be {FORM1_USER_SIZE} bytes.")
    if len(subheader) != 8 or subheader[:4] != subheader[4:]:
        raise ValueError("A CD-XA subheader must contain two identical four-byte copies.")

    sector = bytearray(RAW_SECTOR_SIZE)
    sector[0:12] = b"\x00" + b"\xFF" * 10 + b"\x00"
    absolute_frame = lba + 150
    minute, remainder = divmod(absolute_frame, 75 * 60)
    second, frame = divmod(remainder, 75)
    sector[12:16] = bytes((_bcd(minute), _bcd(second), _bcd(frame), 2))
    sector[16:24] = subheader
    sector[24:2072] = user_data
    struct.pack_into("<I", sector, 2072, _compute_edc(sector[16:2072]))

    # Mode-2/Form-1 ECC treats the address/mode bytes as zero but covers the
    # subheader, user data, and EDC. Restore the physical address afterward.
    address = sector[12:16]
    sector[12:16] = bytes(4)
    sector[2076:2248] = _compute_ecc(sector[12:2076], 86, 24, 2, 86)
    sector[2248:2352] = _compute_ecc(sector[12:2248], 52, 43, 86, 88)
    sector[12:16] = address
    return bytes(sector)


def _add_ppf_records(patch: bytearray, raw_offset: int, data: bytes) -> None:
    copied = 0
    while copied < len(data):
        record = data[copied : copied + 255]
        patch.extend(struct.pack("<IB", raw_offset + copied, len(record)))
        patch.extend(record)
        copied += len(record)


def append_mode2_form1_sector_ppf_records(
    patch: bytearray,
    lba: int,
    user_data: bytes,
) -> None:
    sector = build_mode2_form1_sector(lba, user_data)
    _add_ppf_records(patch, lba * RAW_SECTOR_SIZE, sector)


def build_player_ppf(
    base_ppf: bytes,
    seed_block: bytes,
    description: str,
    floor_pages: Sequence[bytes] = (),
    spawn_tables: Sequence[bytes] = (),
    carrier_system: bool = True,
) -> bytes:
    """`carrier_system=False` drops ONE word: the retarget of the floor
    population routine's first `jal random_range`, which is what forces the
    carrier spawn. Nothing else needs removing, and that is the point of taking
    this switch here rather than anywhere else. The claim, AI and draw stubs
    all key on state the forced spawn raises (`claiming`, slot 15, the held
    marker halfword), so with no forced spawn they run their ordinary-monster
    paths - the same paths they already run for every non-carrier on a
    carrier seed. Leaving them installed keeps the two builds one word apart
    instead of four, which is a much smaller thing to reason about."""

    if len(base_ppf) < PPF_HEADER_SIZE or base_ppf[:4] != b"PPF1":
        raise ValueError("The packaged Azure Dreams base patch is not a PPF1 patch.")
    if len(seed_block) != SEED_BLOCK_SIZE:
        raise ValueError(f"Seed block must be exactly {SEED_BLOCK_SIZE} bytes.")
    if floor_pages and len(floor_pages) != FLOOR_PAGE_FLOOR_COUNT:
        raise ValueError(
            f"Expected {FLOOR_PAGE_FLOOR_COUNT} floor pages, got {len(floor_pages)}."
        )

    patch = bytearray(base_ppf)
    encoded_description = description.encode("ascii", "replace")[:50]
    patch[6:56] = encoded_description.ljust(50, b"\0")
    for sector_index in range(SEED_SECTOR_COUNT):
        user_data = seed_block[sector_index * FORM1_USER_SIZE : (sector_index + 1) * FORM1_USER_SIZE]
        append_mode2_form1_sector_ppf_records(
            patch,
            SEED_SECTOR_LBA + sector_index,
            user_data,
        )
    for page_index, page in enumerate(floor_pages):
        append_mode2_form1_sector_ppf_records(
            patch,
            FLOOR_PAGE_BANK_LBA + page_index,
            page.ljust(FORM1_USER_SIZE, b"\0"),
        )

    # Redirect the floor's monster-levelling call to the wrapper that also
    # levels Koh. Four bytes inside one Mode-2 Form-1 sector's user data; the
    # project's other code hooks are written the same way.
    redirect = struct.pack("<I", _j(0x03, SHORTCUT_LEVEL_GRANT_ADDRESS))
    for dungeon_offset in LEVEL_MONSTERS_CALL_DUNGEON_OFFSETS:
        sector, within = divmod(dungeon_offset, FORM1_USER_SIZE)
        raw_offset = (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
        patch.extend(struct.pack("<IB", raw_offset, 4) + redirect)

    # The per-floor monster spawn tables, when the seed randomised them.
    # 32 bytes each, one sector apart, well inside a Form-1 sector.
    for floor_index, table in enumerate(spawn_tables):
        if len(table) != 32:
            raise ValueError("A monster spawn table must be exactly 32 bytes.")
        dungeon_offset = 0x7A_4800 + floor_index * 2048
        sector, within = divmod(dungeon_offset, FORM1_USER_SIZE)
        raw_offset = (
            (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
        )
        patch.extend(struct.pack("<IB", raw_offset, len(table)) + table)

    # EXPERIMENT - see CARRIER_PROBE. Delete with it. Two words:
    # retarget the carried-item roll's only caller at 0x800A0B3C, and replace
    # the AI dispatch's species load at 0x800AE8CC with a jump to our stub.
    if CARRIER_PROBE:
        carrier_hooks = [
            (CARRIER_ROLL_HOOK_DUNGEON_OFFSET, _j(0x03, CARRIER_ROLL_STUB_ADDRESS)),
            (CARRIER_AI_HOOK_DUNGEON_OFFSET, _j(0x02, CARRIER_AI_STUB_ADDRESS)),
            (CARRIER_DRAW_HOOK_DUNGEON_OFFSET,
             _j(0x03, CARRIER_DRAW_STUB_ADDRESS)),
        ]
        if carrier_system:
            carrier_hooks.append(
                (CARRIER_SPAWN_HOOK_DUNGEON_OFFSET,
                 _j(0x03, CARRIER_FORCED_STUB_ADDRESS))
            )
        for dungeon_offset, word in carrier_hooks:
            sector, within = divmod(dungeon_offset, FORM1_USER_SIZE)
            raw_offset = (
                (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
            )
            patch.extend(struct.pack("<IB", raw_offset, 4) + struct.pack("<I", word))

    # TEMPORARY EXPERIMENT - see PICKET_AI_EXPERIMENT. Delete with it.
    if PICKET_AI_EXPERIMENT:
        payload = struct.pack(
            f"<{PICKET_AI_TABLE_ENTRIES}I",
            *([PICKET_AI_HANDLER_ADDRESS] * PICKET_AI_TABLE_ENTRIES),
        )
        sector, within = divmod(PICKET_AI_TABLE_DUNGEON_OFFSET, FORM1_USER_SIZE)
        if within + len(payload) > FORM1_USER_SIZE:
            raise ValueError("The Picket-AI probe straddles a sector boundary.")
        raw_offset = (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
        _add_ppf_records(patch, raw_offset, payload)

    # TEMPORARY EXPERIMENT - see MONSTER_RECOLOUR_EXPERIMENT. Delete with it.
    if MONSTER_RECOLOUR_EXPERIMENT:
        payload = struct.pack("<3I", *MONSTER_RECOLOUR_WORDS)
        sector, within = divmod(MONSTER_RECOLOUR_DUNGEON_OFFSET, FORM1_USER_SIZE)
        if within + len(payload) > FORM1_USER_SIZE:
            raise ValueError("The monster-recolour probe straddles a sector boundary.")
        raw_offset = (DUNGEON_BIN_BASE_LBA + sector) * RAW_SECTOR_SIZE + 24 + within
        patch.extend(struct.pack("<IB", raw_offset, len(payload)) + payload)

    # Register the floor-page loader as the elevator-orb animator's callback:
    # the creator's `lui/addiu` pair now materializes the loader's address,
    # and the loader tail-jumps into the real animator. Same single-site
    # mechanism as the bonus orb guard's jal retarget, one sector over.
    loader_hi = (FLOOR_PAGE_LOADER_ADDRESS + 0x8000) >> 16
    loader_lo = FLOOR_PAGE_LOADER_ADDRESS & 0xFFFF
    animator_hook = struct.pack(
        "<II", 0x3C02_0000 | loader_hi, 0x2442_0000 | loader_lo
    )
    patch.extend(
        struct.pack("<IB", FLOOR_PAGE_ANIMATOR_HOOK_RAW_OFFSET, 8) + animator_hook
    )

    # Interpose the forced-trap stub on the receive dispatcher's hook. The
    # base patch carries the hook as its own 4-byte record; verify it still
    # says `jal receive_dispatcher` (anything else means the payload moved
    # and this retarget would corrupt an unknown site), then edit the record
    # in place - bonus_floor._append documents why appending a second record
    # for covered ground is wrong.
    current = _read_ppf_word(patch, RECEIVE_HOOK_RAW_OFFSET)
    if current != RECEIVE_HOOK_ORIGINAL_WORD:
        raise ValueError(
            f"The receive hook at raw 0x{RECEIVE_HOOK_RAW_OFFSET:X} reads "
            f"0x{current:08X}, not the expected jal to the receive "
            "dispatcher. The payload has moved; re-derive the hook."
        )
    bonus_floor._append(
        patch,
        RECEIVE_HOOK_RAW_OFFSET,
        struct.pack("<I", _j(0x03, FORCED_TRAP_STUB_ADDRESS)),
    )

    # The carrying handler's own forced-trap hook: one SLUS record for the
    # trampoline over the dead bios.c RCS string, one DUNGEON record for the
    # call site. Both sites are vanilla on the original disc (verified
    # 2026-08-18: SLUS raw 0x15980 is the string, DUNGEON raw 0x1C5F104 is
    # `lhu v0,0x2(s0)` = 0x96020002) and uncovered by the base patch;
    # _append edits in place if that ever changes. Installed unconditionally:
    # with no traps in the pool the request byte stays zero and the
    # trampoline is one load and a jump.
    from . import save_removal as _save_removal

    for raw_offset, data in _save_removal._iter_mode2_raw_patches(
        _save_removal.SLUS_FILE_START_LBA,
        iter_carrying_trap_slus_file_patches(),
    ):
        bonus_floor._append(patch, raw_offset, data)
    for raw_offset, data in _save_removal._iter_mode2_raw_patches(
        _save_removal.DUNGEON_FILE_START_LBA,
        iter_carrying_trap_dungeon_file_patches(),
    ):
        bonus_floor._append(patch, raw_offset, data)

    # TESTING - see BANK_B_CANARY_TEST. Delete with it. Two SLUS records: the
    # 44-byte fill routine over the dead "$Id: intr.c" string, and the boot
    # init's `jr ra` retargeted at it. Both sites are vanilla in the base
    # patch; _append edits in place if that ever changes.
    if BANK_B_CANARY_TEST:
        from . import save_removal

        for raw_offset, data in save_removal._iter_mode2_raw_patches(
            save_removal.SLUS_FILE_START_LBA,
            iter_bank_b_canary_slus_file_patches(),
        ):
            bonus_floor._append(patch, raw_offset, data)

    # TESTING - see FORCE_SINGLE_ROOM_TEST. Delete with it. One instruction
    # word in each floor-generation package copy; the site is vanilla in the
    # base patch (verified 2026-08-16), _append edits in place if that changes.
    if FORCE_SINGLE_ROOM_TEST:
        from . import save_removal

        for raw_offset, data in save_removal._iter_mode2_raw_patches(
            save_removal.DUNGEON_FILE_START_LBA,
            iter_force_single_room_dungeon_file_patches(),
        ):
            bonus_floor._append(patch, raw_offset, data)

    return bytes(patch)


def _read_ppf_word(ppf: bytes, raw_offset: int) -> int:
    """Read the last-writer value of one word from a PPF1's records."""

    value = bytearray(4)
    seen = 0
    cursor = PPF_HEADER_SIZE
    while cursor < len(ppf):
        offset, length = struct.unpack_from("<IB", ppf, cursor)
        body = cursor + 5
        low = max(offset, raw_offset)
        high = min(offset + length, raw_offset + 4)
        if low < high:
            value[low - raw_offset : high - raw_offset] = ppf[
                body + low - offset : body + high - offset
            ]
            for index in range(low - raw_offset, high - raw_offset):
                seen |= 1 << index
        cursor = body + length
    if seen != 0xF:
        raise ValueError(
            f"No PPF record fully covers raw 0x{raw_offset:X}; the base "
            "patch does not carry the expected hook."
        )
    return struct.unpack("<I", value)[0]
