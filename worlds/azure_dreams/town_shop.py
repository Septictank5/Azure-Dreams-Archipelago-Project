from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import patch
from .pool_house import POOL_HOUSE_OPEN as _POOL_HOUSE_OPEN


SHOP_COUNT = 3
SLOTS_PER_SHOP = 10
SHOP_SLOT_COUNT = SHOP_COUNT * SLOTS_PER_SHOP
IMPLEMENTED_SHOP_COUNT = 2
IMPLEMENTED_SHOP_SLOT_COUNT = IMPLEMENTED_SHOP_COUNT * SLOTS_PER_SHOP

TOWN_FILE_START_LBA = 3_077
TOWN_MODE_FILE_OFFSET = 0xB000
TOWN_MODE_RUNTIME_ADDRESS = 0x8008_8760

# This exact 4 KiB range is part of the town mode image and ends immediately
# before vanilla state at 0x800fc418. It is restored whenever town mode is
# loaded and survives transitions among town buildings.
SHOP_CORE_ADDRESS = 0x800F_B418
SHOP_CORE_SIZE = 0x1000
SHOP_CORE_FILE_OFFSET = TOWN_MODE_FILE_OFFSET + (
    SHOP_CORE_ADDRESS - TOWN_MODE_RUNTIME_ADDRESS
)

SHOP_CORE_MAGIC = b"ADAPSHOP"
SHOP_CORE_VERSION = 5

ACTIVE_SHOP_OFFSET = 0x0C
ARMED_MENU_OFFSET = 0x0D
ACTIVE_SLOT_MAP_OFFSET = 0x10
ACTIVE_SLOT_MAP_SIZE = 0x10
SEED_SIGNATURE_OFFSET = 0x20
SEED_SIGNATURE_SIZE = 8
ACTIVE_CATALOG_OFFSET = 0x28

MONSTER_BUILDER_OFFSET = 0x30
EQUIPMENT_BUILDER_OFFSET = 0x40
GENERIC_BUILDER_OFFSET = 0x50
MONSTER_COMMIT_WRAPPER_OFFSET = 0x160
COMMIT_PURCHASES_OFFSET = 0x180
SHOP_TEXT_LOADER_OFFSET = 0x210
BUY_PRICE_OFFSET = 0x280
ITEM_NAME_OFFSET = 0x340
# The intro-state writer and its table live in the slack of the two
# resolver regions (the slab is otherwise full).
INTRO_STATE_WRITER_OFFSET = 0x308  # buy-price slack, ends < ITEM_NAME
INTRO_STATE_TABLE_OFFSET = 0x3E0   # item-name slack, after the intro probe
DESCRIPTION_RESOLVER_OFFSET = 0x420
MENU_CONSTRUCTOR_OFFSET = 0x540
STATE_INITIALIZER_OFFSET = 0x570
MANIFEST_OFFSET = 0x620
MANIFEST_RECORD_SIZE = 12
NAME_DATA_OFFSET = MANIFEST_OFFSET + SHOP_SLOT_COUNT * MANIFEST_RECORD_SIZE
# Thirty unique ten-character names occupy at most 630 bytes, ending at
# 0x9FE. Reserve the final eighty bytes before the shared description for the
# intro's native Pita/capture wrapper without reducing the supported catalog.
INTRO_CAPTURE_WRAPPER_OFFSET = 0xC40
INTRO_CAPTURE_WRAPPER_SIZE = 0x50
UNFAMILIAR_ITEM_DESCRIPTION_OFFSET = 0xC90
SHOP_DATA_END_OFFSET = 0xCD0
# The send-menu UI gates live in the spare span between the ADGS gift
# mailbox (ends 0xBD4) and the door-gate latch word (0xC3C); everything
# from SHOP_DATA_END_OFFSET up belongs to town_receive.
# The smith price gate (2026-08-16): the buy-price resolver's fallback jumps
# here instead of straight to the vanilla buy price; SMITH marker -> the
# smith's temper-cost native, anything else -> vanilla. It takes the slot of
# the RETIRED send-menu price-visibility gate (0xBD4..0xC04): that gate's hook
# site 0x800B1090 is written back to its vanilla `jal 0x800B0F94` - the
# deliberate cleanup docs/systems/nada-send.md 5.6 anticipated.
SMITH_PRICE_GATE_OFFSET = 0xBD4
SMITH_PRICE_GATE_END_OFFSET = 0xC04
CHECKED_TAG_GATE_OFFSET = 0xC04
SEND_HEADER_TEXT_OFFSET = 0xC2C
SEND_UI_GATES_END_OFFSET = 0xC3C
MAX_SHOP_NAME_CHARACTERS = 10

# The Equipment Shop overlay leaves this complete sector untouched for its
# active lifetime.  Its source is the third-to-last non-EOF Form-1 sector in
# STR/DUMMY_.STR, immediately before the two sectors already owned by the
# tower seed page.  Future shops can use the preceding sectors while reusing
# this same mutually-exclusive RAM bank.
SHOP_TEXT_BANK_ADDRESS = 0x8001_D000
SHOP_TEXT_BANK_SIZE = 0x800
SHOP_TEXT_SECTOR_LBAS = (31_448, 31_447, 31_446)
SHOP_TEXT_MAGIC = b"ADST"
SHOP_TEXT_VERSION = 2
SHOP_TEXT_OFFSET_TABLE = 0x14
SHOP_TEXT_DATA_OFFSET = 0x40
SHOP_TEXT_END_MARKER_OFFSET = SHOP_TEXT_BANK_SIZE - 8
SHOP_TEXT_END_MARKER = b"ADSTEND\0"
REMOTE_ITEM_DISPLAY_NAME = "Strange..."
MAX_SHOP_DESCRIPTION_CHARACTERS = 51
MAX_SHOP_DESCRIPTION_ITEM_CHARACTERS = 30
MAX_SHOP_DESCRIPTION_PLAYER_CHARACTERS = 16

UNFAMILIAR_ITEM_DESCRIPTION = "This doesn't belong here..."
UNFAMILIAR_ITEM_DESCRIPTION_ADDRESS = (
    SHOP_CORE_ADDRESS + UNFAMILIAR_ITEM_DESCRIPTION_OFFSET
)
# Cream is excluded from the AP item pool and supplies the native gift icon.
# Purchases never insert this display-only descriptor into inventory.
UNFAMILIAR_ITEM_PROXY_DESCRIPTOR = bytes((1, 11, 0, 0))
UNFAMILIAR_ITEM_DESCRIPTION_POINTER_ADDRESS = 0x8007_2350

# The original Cream description is the only reference to this 100-byte
# resident string slot. The shop patch already redirects Cream's description
# pointer, so reclaim the slot for a lifetime guard plus a resident fallback
# string. Resident item-description wrappers run in both town and tower modes;
# they must never jump directly into the transient town payload.
RESIDENT_DESCRIPTION_SLOT_ADDRESS = 0x8002_F5DC
RESIDENT_DESCRIPTION_SLOT_SIZE = 0x64
RESIDENT_DESCRIPTION_GATE_SIZE = 0x28
RESIDENT_DESCRIPTION_GATE_ADDRESS = RESIDENT_DESCRIPTION_SLOT_ADDRESS
RESIDENT_UNFAMILIAR_ITEM_DESCRIPTION_ADDRESS = (
    RESIDENT_DESCRIPTION_SLOT_ADDRESS + RESIDENT_DESCRIPTION_GATE_SIZE
)
ORIGINAL_CREAM_DESCRIPTION = "Beauty cream that makes your\nskin really smooth. "

SLUS_FILE_START_LBA = 24
SLUS_LOAD_ADDRESS = 0x8002_D000
SLUS_HEADER_SIZE = 0x800

MONSTER_BUILDER_ADDRESS = SHOP_CORE_ADDRESS + MONSTER_BUILDER_OFFSET
EQUIPMENT_BUILDER_ADDRESS = SHOP_CORE_ADDRESS + EQUIPMENT_BUILDER_OFFSET
GENERIC_BUILDER_ADDRESS = SHOP_CORE_ADDRESS + GENERIC_BUILDER_OFFSET
MONSTER_COMMIT_WRAPPER_ADDRESS = SHOP_CORE_ADDRESS + MONSTER_COMMIT_WRAPPER_OFFSET
COMMIT_PURCHASES_ADDRESS = SHOP_CORE_ADDRESS + COMMIT_PURCHASES_OFFSET
SHOP_TEXT_LOADER_ADDRESS = SHOP_CORE_ADDRESS + SHOP_TEXT_LOADER_OFFSET
BUY_PRICE_ADDRESS = SHOP_CORE_ADDRESS + BUY_PRICE_OFFSET
ITEM_NAME_ADDRESS = SHOP_CORE_ADDRESS + ITEM_NAME_OFFSET
DESCRIPTION_RESOLVER_ADDRESS = SHOP_CORE_ADDRESS + DESCRIPTION_RESOLVER_OFFSET
MENU_CONSTRUCTOR_ADDRESS = SHOP_CORE_ADDRESS + MENU_CONSTRUCTOR_OFFSET
SMITH_PRICE_GATE_ADDRESS = SHOP_CORE_ADDRESS + SMITH_PRICE_GATE_OFFSET
CHECKED_TAG_GATE_ADDRESS = SHOP_CORE_ADDRESS + CHECKED_TAG_GATE_OFFSET
SEND_HEADER_TEXT_ADDRESS = SHOP_CORE_ADDRESS + SEND_HEADER_TEXT_OFFSET
INTRO_STATE_WRITER_ADDRESS = SHOP_CORE_ADDRESS + INTRO_STATE_WRITER_OFFSET
INTRO_STATE_TABLE_ADDRESS = SHOP_CORE_ADDRESS + INTRO_STATE_TABLE_OFFSET
STATE_INITIALIZER_ADDRESS = SHOP_CORE_ADDRESS + STATE_INITIALIZER_OFFSET
MANIFEST_ADDRESS = SHOP_CORE_ADDRESS + MANIFEST_OFFSET

EQUIPMENT_CATALOG_ADDRESS = 0x8001_8AE8
EQUIPMENT_LEADING_ENTRY_ADDRESS = 0x8001_8A98
MONSTER_LEADING_ENTRY_ADDRESS = 0x8001_8880
SHOP_LEADING_ENTRY_WORD = 0x0000_1601
# The menu's "this row is checked" bit, in the descriptor's flags byte. It is
# the game's own "equipped" flag, which is safe to borrow in town because the
# game unequips on town return. Byte +3 of a catalog entry, so bit 29 of the
# word.
# A carried monster. Shares its ids with the eggs it hatched from - id 4 is a
# Kid egg at category 18 and a Kid familiar at 19 - and behaves like an item
# except that the low five bits of its flags byte are its monster-hut index.
FAMILIAR_CATEGORY = 0x13

SHOP_CHECKED_ENTRY_FLAG = 0x20
# What a row carries to refuse selection. 0x80 is the game's "unidentified"
# flag, and the town clears it - alongside 0x20 - on every return, so no item
# in a town menu owns it and we can spend it. The equipment shop proved the
# menu honours it: while our manifest was leaking item flags into byte +3,
# every 0x80/0xC0 row went unselectable.
SHOP_DISABLED_ENTRY_FLAG = 0x80
SHOP_CHECKED_ENTRY_FLAG_WORD = SHOP_CHECKED_ENTRY_FLAG << 24

# The ADSV record. patch.py owns the layout; these used to be a second copy
# "because of the import cycle", but there is no cycle - patch imports only
# bonus_floor - and the copy went stale on the 2026-08-15 v4 re-lay while the
# suite's agreement check missed it. Aliases only, from here on.
PERSISTENT_STATE_ADDRESS = patch.PERSISTENT_STATE_ADDRESS
PERSISTENT_STATE_MAGIC = patch.PERSISTENT_STATE_MAGIC
PERSISTENT_STATE_VERSION = patch.PERSISTENT_STATE_VERSION
PERSISTENT_STATE_SIZE = patch.PERSISTENT_STATE_SIZE
PERSISTENT_GOLD_GRANTED_OFFSET = patch.PERSISTENT_GOLD_GRANTED_OFFSET
PERSISTENT_KEYCARD_LEVEL_OFFSET = patch.PERSISTENT_KEYCARD_LEVEL_OFFSET
# The town half of the unified location mask; the shop's whole-word store
# covers its first 32 bits, of which twenty are checks today.
PERSISTENT_SHOP_MASK_OFFSET = patch.PERSISTENT_SHOP_MASK_OFFSET
PERSISTENT_SHOP_MASK_ADDRESS = PERSISTENT_STATE_ADDRESS + PERSISTENT_SHOP_MASK_OFFSET

VANILLA_BUY_PRICE_ADDRESS = 0x8004_A638
VANILLA_ITEM_TEXT_ADDRESS = 0x8004_AC3C
VANILLA_ITEM_DESCRIPTION_ADDRESS = 0x8004_9374
SHOW_ITEM_DESCRIPTION_ADDRESS = 0x8004_DD2C
VANILLA_MENU_CONSTRUCTOR_ADDRESS = 0x800A_E1AC
COMPACT_INVENTORY_POINTER_TABLE_ADDRESS = 0x8001_6D78
MONSTER_COMPACT_INVENTORY_POINTER_TABLE_ADDRESS = 0x8001_6CA8
BUILD_CD_READ_DESCRIPTOR_ADDRESS = 0x8003_F6D4
ENQUEUE_CD_COMMAND_ADDRESS = 0x8003_E4FC
WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS = 0x8003_F320

# The Monster Shop is the lshop.c overlay loaded from TOWN.BIN+0x5ff000.
# Its vanilla buy branch is present but unreachable: the dialogue apologizes
# for having no stock instead. A separately loaded four-sector dialogue block
# contains a confirmed-zero 0x26b-byte tail at runtime 0x8001aaad. The custom
# transaction script fits there without allocating another disc sector or a
# town-global RAM range.
MONSTER_OVERLAY_FILE_OFFSET = 0x5FF000
MONSTER_BUILDER_HOOK_ADDRESS = 0x8001_6694
MONSTER_BUY_CHOICE_POINTER_FILE_OFFSET = 0x604B59
MONSTER_BUY_CHOICE_ORIGINAL_ADDRESS = 0x8001_A1B0
MONSTER_BUY_SCRIPT_FILE_OFFSET = 0x605595
MONSTER_BUY_SCRIPT_ADDRESS = 0x8001_AAAD
MONSTER_BUY_SCRIPT_CAPACITY = 0x26B
MONSTER_BUY_MENU_SCRIPT_ADDRESS = 0x8001_8D1C
MONSTER_CATALOG_ADDRESS = 0x8001_88D0
MONSTER_SUM_SELECTED_BUY_ADDRESS = 0x8001_6770
MONSTER_CAN_AFFORD_ADDRESS = 0x8001_67C8
MONSTER_SUBTRACT_TOTAL_ADDRESS = 0x8001_67F0

# This lshop.c overlay begins at TOWN.BIN+0x617000. The preceding 0x16000
# bytes belong to another packed town resource; runtime 0x80016000 maps to
# this overlay start, as also confirmed by the old randoshop hook at +0x658.
# The Equipment dialogue resource is a SEPARATE load from the overlay, exactly
# as Monster's is (TOWN+0x603800 -> 0x80018D18).  Its base was found the same
# way the Monster one was confirmed: each shop's dialogue resource opens with
# three "menu opener" stubs of the form
#
#     30 yield | 34 0E <addr> | 4C <native opener> | 3E 0F <addr> | 16 return
#
# whose `3E` operand is the stub's OWN runtime address.  That self-reference
# gives the mapping with no tracing at all.  Applying it to the Monster blob
# reproduces 0x80018D18 exactly, which is what validates it for Equipment.
EQUIPMENT_DIALOGUE_FILE_OFFSET = 0x61_B000
EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS = 0x8001_8F08

# The three Equipment menu-opener stubs, for reference:
#   0x80018F0C -> native 0x8001661C   (Buy)
#   0x80018F20 -> native 0x8001667C   (Sell)
#   0x80018F34 -> native 0x800166E8
EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS = 0x8001_9130

# Barry's five greeting variants, from the (flag, script) table at runtime
# 0x80018954-0x80018978 in the overlay.  Every one of them eventually reaches
# the Buy/Sell/Leave menu; three of the five already do it with the exact
# `15 <menu> | 01` idiom this module writes over all five.
EQUIPMENT_GREETING_ENTRY_ADDRESSES = (
    0x8001_96FC,  # "Welco...  Oh, it's you <player>." + the No-I'm-not choice
    0x8001_9B20,  # "Oh, it's you <player>. I really can't expect business..."
    0x8001_9BB8,  # "Hello <player>."
    0x8001_9C5C,  # "Welcome <player>."
    0x8001_9EB0,  # "Welcome. I'm naturally expecting some business."
)

# Where the Buy and Sell menus are actually opened, past each one's preamble.
# Both are `08 clear | 15 call <opener stub>`.
EQUIPMENT_BUY_OPEN_ADDRESS = 0x8001_92FD
EQUIPMENT_SELL_OPEN_ADDRESS = 0x8001_9570

# The shared tail every transaction path falls into.  In vanilla it is the text
# `Are you here for anything else?` followed by `11 wait | 17 goto <menu>`.
#
# **The `11` is load-bearing.** `Thanks very much.` has no wait of its own - the
# buy and sell confirmations both `17 goto` here and borrow this one.  Replacing
# the whole tail with a bare goto would flash the confirmation past the player,
# so the replacement keeps the wait and drops only the text.
EQUIPMENT_TRANSACTION_TAIL_ADDRESS = 0x8001_9695

# Retargeting the *references* rather than planting a stub at each old block
# start is what keeps the freed space in large contiguous runs: a five-byte stub
# in the middle of a dead region splits it in two.  Every entry here is a
# four-byte absolute target inside an existing branch table or jump operand.
EQUIPMENT_DIALOGUE_RETARGETS = (
    (0x8001_91B5, EQUIPMENT_BUY_OPEN_ADDRESS, "menu row 1, skip the Buy preamble"),
    (0x8001_91B9, EQUIPMENT_SELL_OPEN_ADDRESS, "menu row 2, skip the Sell preamble"),
    (0x8001_9305, EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS, "Buy cancelled, skip the exit page"),
    (0x8001_9373, EQUIPMENT_BUY_OPEN_ADDRESS, "'You don't have enough money.' retry"),
    (0x8001_93D7, EQUIPMENT_BUY_OPEN_ADDRESS, "'My mistake.' (Buy)"),
    (0x8001_93DB, EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS, "'Not buying.'"),
    (0x8001_9578, EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS, "Sell cancelled, skip the exit page"),
    (0x8001_95FF, EQUIPMENT_SELL_OPEN_ADDRESS, "'My mistake.' (Sell)"),
    (0x8001_9603, EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS, "'Not selling.'"),
)

# What all of this frees, computed by walking the script graph from every root
# before and after the edits; see `docs/town-shop-implementation.md`.
# **Free inside the Equipment shop only** - this resource is only resident while
# that shop is open, and the Monster overlay loads over the top of it.
EQUIPMENT_DIALOGUE_FREE_SPANS = (
    (0x8001_91C1, 0x8001_92FC, "Buy preamble: ancient ruins / not much to sell / what are you looking for"),
    (0x8001_9414, 0x8001_956F, "Buy exit page, and the Sell preamble"),
    (0x8001_965E, 0x8001_9694, "Sell exit page: 'I'm sorry to hear that.'"),
    (0x8001_969B, 0x8001_96D8, "'Are you here for anything else?'"),
    (0x8001_9702, 0x8001_9858, "Barry greeting 1 body + the No/Right choice"),
    (0x8001_9884, 0x8001_9B1C, "the 'As I recall...' fifteenth-birthday chain"),
    (0x8001_9B26, 0x8001_9B96, "Barry greeting 2 body"),
    (0x8001_9BBE, 0x8001_9BD0, "Barry greeting 3 body"),
    (0x8001_9C62, 0x8001_9C78, "Barry greeting 4 body"),
    (0x8001_9EB6, 0x8001_9F14, "Barry greeting 5 body"),
)

# Monster's dialogue resource, established by trace long before the Equipment
# one; the opener-stub derivation reproduces it exactly.
MONSTER_DIALOGUE_FILE_OFFSET = 0x60_3800
MONSTER_DIALOGUE_RUNTIME_ADDRESS = 0x8001_8D18
MONSTER_SHOP_MENU_SCRIPT_ADDRESS = 0x8001_9FE8
MONSTER_SELL_OPEN_ADDRESS = 0x8001_A0CE  # `08 clear | 15 call 0x80018D30`

# **Only four of the merchant's sixteen table entries are his shop greeting.**
# The overlay table at 0x80018754 has sixteen (flag, script) pairs in four
# groups; the twelve with flags 0x10034/0x20035/0x30036 are story dialogue for
# the scene and **never reach the shop menu**.  Only the four flagged 0x26 do.
# Equipment had no such split, so this is the trap the technique does not warn
# you about - reachability to the menu is what tells them apart.
MONSTER_GREETING_ENTRY_ADDRESSES = (
    0x8001_A4C4,
    0x8001_A805,
    0x8001_A8B3,
    0x8001_AA20,
)

# Only one retarget is needed. Buy already goes to our own AP script, whose
# cancel path is a bare `16 return`; Sell's cancel is already a chain of gotos
# back to the menu. The Sell preamble is the only prose on an entry path.
MONSTER_DIALOGUE_RETARGETS = (
    (
        0x8001_A06D,
        MONSTER_SELL_OPEN_ADDRESS,
        "menu row 1, skip 'What kind of monster is it?  Is it an egg?'",
    ),
)

# **The second-talk lockout.**  Talk to the merchant again after cycling his
# shop once and he refuses to trade, saying only
# `Your father, Guy, was super. / He would frequently come to sell top quality
# monsters.`  Leaving the building and coming back clears it.
#
# The block is at 0x8001A41C and **nothing in TOWN.BIN points at it** - the
# address is computed, which is why a pointer search finds nothing:
#
#     80016144  jal   0x80017A54          ; has this entry been used already?
#     8001614C  beq   v0,zero,0x80016160  ; no -> keep the table's script
#     80016150  addiu v0,zero,0x0026      ; delay slot
#     80016154  bne   s3,v0,0x80016160    ; only for selector 0x26, the shop row
#     80016158  lui   v0,0x8002           ; delay slot
#     8001615C  addiu s2,v0,0xa41c        ; s2 = 0x8001A41C  <- the substitution
#     80016160  jal   0x80018604
#
# One instruction installs it, so one `nop` removes it: `s2` keeps whatever the
# greeting table chose, which is now our `15 <menu> | 01` stub.  The shop is
# therefore never locked out.  The three story selectors (0x34/0x35/0x36) never
# reached this substitution anyway - the `bne` already excluded them - so
# nothing else changes.
MONSTER_GUY_SUBSTITUTION_ADDRESS = 0x8001_615C
MONSTER_GUY_SUBSTITUTION_ORIGINAL = 0x2452_A41C  # addiu s2,v0,0xa41c

# Free inside the Monster shop only. Same caveat as Equipment: this resource is
# resident only while that shop is open.
MONSTER_DIALOGUE_FREE_SPANS = (
    (0x8001_A079, 0x8001_A0CD, "Sell preamble: 'What kind of monster is it?'"),
    (0x8001_A41C, 0x8001_A4C3, "the 'Your father, Guy...' second-talk lockout"),
    (0x8001_A4CA, 0x8001_A568, "merchant greeting 1 body"),
    (0x8001_A80B, 0x8001_A850, "merchant greeting 2 body"),
    (0x8001_A8B9, 0x8001_A902, "merchant greeting 3 body"),
    (0x8001_AA26, 0x8001_AAAC, "merchant greeting 4 body"),
)

# --- The Monster Shop door gate -------------------------------------------
#
# Town doors are actors whose update routine is 0x800A0708, carrying a pointer
# to a 20-byte record in the outdoor-overlay table at 0x8001BEA4: type in the
# record's low half (0x0C), **index in its high half**, then the ARRIVAL X|Y,
# then a handler.  The Monster Shop is index 20, record 0x8001BFF8 - identified
# by read-watching all five candidate records at the shop and walking in; only
# index 20 was touched by the transition.  See the reverse-engineering notes.
#
# An armed door rests in one of two states, decompiled 2026-07-27 from the
# town RAM image (Ghidra project AzureDreamsTownLive; names applied by
# tools/ghidra_scripts/ApplyTownDoorPathNames.java):
#
#   0x800A07E8  armed-delayed:   per frame it runs the direction test
#               0x800A0668, then is_player_ready_for_scene_transition
#               (0x800A0F10, jal at 0x800A083C).  Only when the latter
#               returns nonzero does it commit anything - install the walk
#               state 0x800A0884 and start Koh's walk via 0x8009A674.
#   0x800A0AC8  armed-immediate: the same two tests (readiness jal at
#               0x800A0B1C), then begin_scene_transition_from_actor_entry
#               at once.
#
# In both states s1 holds the actor, and *(actor+0x48) the record.  NOTHING
# is committed before the readiness jal reports success: no door animation,
# no walk, no staging - which is exactly what v88's abort-after-staging
# softlock was missing.  So the gate interposes on both jal sites.  It calls
# the vanilla test, and when the test passes for the Monster Shop record
# with a Keycard below 3, it publishes a `Door is locked.` script through
# the town dialogue queue (town_receive's live-validated notification seam)
# and returns 0.  The state keeps resting; the player walks away, and
# walking into the door again asks again.
#
# The message re-arms through a cooldown that counts down only on frames
# where the readiness test says "not ready".  However dialogue pausing
# behaves, dismissing the modal while still standing in the doorway cannot
# re-fire it in a loop: while the player stays "ready" the cooldown holds,
# and while a modal owns the player the handler check inside the readiness
# test reports "not ready".
#
# A wrong premise here fails BENIGNLY: for every record except 0x8001BFF8
# the gate is a bit-exact pass-through, and if the Monster Shop door never
# runs these states the door simply stays unlocked.  Earlier attempts
# (v88-v93) had no such property, which is why they are dead ends.
MONSTER_DOOR_RECORD_ADDRESS = 0x8001_BFF8
MONSTER_DOOR_RECORD_INDEX = 20
MONSTER_DOOR_TYPE_AND_INDEX = 0x0014_000C  # the record's word 0
MONSTER_DOOR_POSITION_OFFSET = 0x04
MONSTER_DOOR_OPEN_POSITION = 0x0240_01E0  # Y 576, X 480 - the ARRIVAL spawn
MONSTER_SHOP_KEYCARD_REQUIREMENT = 3
# The same save-backed word the elevator clearance and Uncle's shortcut read.
KEYCARD_LEVEL_ADDRESS = (
    PERSISTENT_STATE_ADDRESS + PERSISTENT_KEYCARD_LEVEL_OFFSET
)

# is_player_ready_for_scene_transition and its two jal sites in the armed
# states.  Its contract: player overlaps the door zone, faces within a
# quarter turn of the door, and the live player handler is one of the two
# normal movement handlers - a modal dialogue changes the handler, so the
# test reports "not ready" while a message is up.  Both jal sites carry
# `move a1,s1` in their delay slots, so a0 = a1 = the actor when the gate
# begins, and neither site is itself a delay slot.
DOOR_READY_TEST_ADDRESS = 0x800A_0F10
DOOR_READY_HOOK_ADDRESSES = (0x800A_083C, 0x800A_0B1C)
DOOR_READY_ORIGINAL_WORD = 0x0C02_83C4  # jal 0x800A0F10; asserted in tests

# The town dialogue descriptor and its native new-script queue - the same
# seam town_receive's notification helper uses (docs/town-receive-
# implementation.md).  Publishing a script pointer at +0x34 makes vanilla
# arm the parser, play the sound, and open the modal in its ordinary frame
# phase.  The two halfwords at +0xAC/+0xAE are the controller text
# column/row and must be zeroed at publish (the v6/v8 lessons).
TOWN_DIALOGUE_DESCRIPTOR_ADDRESS = 0x8008_2A38
TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET = 0x34
TOWN_DIALOGUE_COLUMN_OFFSET = 0xAC
TOWN_DIALOGUE_ROW_OFFSET = 0xAE

# The modal window root - zero is the authoritative end-of-window condition
# (the receive dispatcher's validated cursor-restore test).
TOWN_MODAL_ROOT_ADDRESS = 0x8008_2BC0

# The player state.  Word 0 at 0x800834B8 is the live handler - the value
# is_player_ready_for_scene_transition compares against the two normal
# movement handlers - and the structure's fields follow it.  The two words
# after the animation/motion pointers at 0x800834A0/0x800834A4 are the
# second and third arguments every player-state routine receives; the town
# frame loop passes exactly (0x800834B8, [0x800834A0], [0x800834A4]).
PLAYER_STATE_ADDRESS = 0x8008_34B8
PLAYER_MOTION_POINTER_ADDRESS = 0x8008_34A0
PLAYER_AUX_POINTER_ADDRESS = 0x8008_34A4
PLAYER_WALK_HANDLER_ADDRESS = 0x8009_1260
PLAYER_RUN_HANDLER_ADDRESS = 0x8009_1528
PLAYER_STANDING_HANDLER_ADDRESS = 0x8009_7D2C
# Cleared by the vanilla collision-talk path right after entering standing.
PLAYER_TALK_CLEAR_OFFSET = 0x2C

# The vanilla stop-into-standing sequence, decompiled from the walk
# handler's collision-talk branch (bumping into an NPC): face the input
# direction (0x80094C1C), then 0x80098868 = install the standing handler
# 0x80097D2C, zero the three motion words (0x80099754 - the velocity clear),
# set the standing animation, and register the walk-resume continuation.
# NPC dialogues recover the player from exactly this state, so a gate that
# replays the same sequence needs no restore logic of its own.
PLAYER_FACE_INPUT_DIRECTION_ADDRESS = 0x8009_4C1C
PLAYER_ENTER_STANDING_STATE_ADDRESS = 0x8009_8868

# The gate lives in the shop core slab, in the tail of the name-data region:
# thirty maximum-length shop names end at 0x9FE, and the name writer's bound
# is NAME_DATA_END_OFFSET, so these claims cannot collide.  The cooldown
# word is runtime state; the slab is restored on every town load, which
# resets it.
# The name headroom above 0x9FE held town_receive's movement-stop body until
# the 2026-08-01 retirement and is now free (town_receive.FREE_SLAB_SPANS).
# The writer's bound still keeps names out of it.
NAME_DATA_END_OFFSET = 0xA00
# The send menu's capacity gate, claimed 2026-08-05 from the retired
# movement-stop body span that town_receive.FREE_SLAB_SPANS used to erase
# (0xA00..0xA40 - the span was removed from that tuple when this took it).
#
# What it gates: the shop menu's A-press handler calls a guard before the
# row toggle, and that guard is the vanilla BUY rule - it sums the checked
# rows and the occupied inventory slots and refuses the check at twenty
# (`slti v0,s0,0x14`), showing the bag-is-full refusal through
# [0x800D1558]. Nada's send menu borrows the buy machinery, so a
# nineteen-item bag could check exactly ONE row - but a send REMOVES items,
# so while the active AP shop is the send marker the guard answers
# "always allowed" and only familiars (0x80, refused before the guard) stay
# uncheckable. Every real shop falls through to the vanilla guard.
CHECK_CAPACITY_GATE_OFFSET = 0xA00
CHECK_CAPACITY_GATE_END_OFFSET = 0xA40
CHECK_CAPACITY_GATE_ADDRESS = SHOP_CORE_ADDRESS + CHECK_CAPACITY_GATE_OFFSET
# The A-press handler's guard call site and the guard it reaches, both in
# the town-mode overlay (disassembled 2026-08-05 from monster_sell_open.bin;
# the caller is the 0x800ADA1C toggle handler, docs/game/shops.md section 4).
CHECK_CAPACITY_HOOK_ADDRESS = 0x800A_DA60
VANILLA_CHECK_CAPACITY_GUARD_ADDRESS = 0x800A_D99C
DOOR_GATE_SCRIPT_OFFSET = 0xA40
DOOR_GATE_OFFSET = 0xA80
DOOR_GATE_END_OFFSET = 0xB70
# 0xB70..0xC3C: the send-menu pump's first home, then Nada's outgoing `ADGS`
# gift mailbox. Both are gone (2026-08-11) and the slab leaves the span zero;
# it is free.
DOOR_GATE_LATCH_OFFSET = 0xC3C  # one word, ends at INTRO_CAPTURE_WRAPPER
DOOR_GATE_SCRIPT_ADDRESS = SHOP_CORE_ADDRESS + DOOR_GATE_SCRIPT_OFFSET
DOOR_GATE_ADDRESS = SHOP_CORE_ADDRESS + DOOR_GATE_OFFSET
DOOR_GATE_LATCH_ADDRESS = SHOP_CORE_ADDRESS + DOOR_GATE_LATCH_OFFSET
DOOR_GATE_MESSAGE_COOLDOWN_FRAMES = 120
DOOR_LOCKED_MESSAGE = "Door is locked."

# RETIRED 2026-08-11 with Nada's send menu: the send catalog bank at
# 0x800157B8 (0x100 bytes, with its twenty-byte slot map at +0x60 and a
# scratch word at +0xF8). Nothing writes that span now; it stays recorded in
# docs/game/memory-map.md as freed rather than reused, because the 2026-08-05
# session kill was a map overflowing into the seed signature two doors down at
# 0x80015FC0 and the next occupant should read that story first.
#
# ACTIVE_SHOP marker for a send-mode menu; real shops are 0..2. Nothing sets
# it any more - the three UI gates below are keyed on it and now always take
# their vanilla path. They stay hooked until they are removed deliberately;
# see docs/systems/nada-send.md, "What was removed".
SEND_MENU_SHOP_MARKER = 3
# ACTIVE_SHOP marker for the BLACKSMITH's temper menu (2026-08-16,
# docs/systems/blacksmith.md). Set by the smith's opener native (which also
# arms the constructor guard), cleared by his after-menu native. Two of the
# slab pieces below key on it: the check-capacity gate hands the A press to
# the smith's guard (an immediate purchase instead of a row toggle), and the
# buy-price resolver's fallback goes through the smith price gate so the row
# price column shows the temper cost. The smith's natives live in the
# equipment shop's dialogue image, which is only loaded - and this marker only
# set - while that shop is open; the two entry points the slab jumps to are a
# fixed table at the head of the natives block.
SMITH_MENU_SHOP_MARKER = 4
SMITH_NATIVE_TABLE_ADDRESS = 0x8001_9884   # = blacksmith.NATIVE_BLOCK_ADDRESS, asserted there
SMITH_PRICE_ENTRY_ADDRESS = SMITH_NATIVE_TABLE_ADDRESS + 0x0   # a0 = descriptor -> v0 = temper cost
SMITH_GUARD_ENTRY_ADDRESS = SMITH_NATIVE_TABLE_ADDRESS + 0x8   # a0 = menu+0x20 -> purchase, v0 = 1/0
# The physical inventory: twenty four-byte descriptors; a slot is occupied
# exactly when its category byte at +1 is nonzero (the game's own rule,
# validated by the Uncle shortcut count).
INVENTORY_DESCRIPTORS_ADDRESS = 0x8001_0248

EQUIPMENT_OVERLAY_FILE_OFFSET = 0x617000
EQUIPMENT_BUILDER_HOOK_ADDRESS = 0x8001_6630
EQUIPMENT_COMMIT_HOOK_ADDRESS = 0x8001_67E8
GENERIC_ITEM_NAME_HOOK_ADDRESS = 0x800B_1060
GENERIC_DISPLAY_PRICE_HOOK_ADDRESS = 0x800B_10B4
GENERIC_TOTAL_PRICE_HOOK_ADDRESS = 0x800A_D878
TOWN_BUY_PRICE_POINTER_ADDRESS = 0x800D_3D18
TOWN_MENU_CONSTRUCTOR_POINTER_ADDRESS = 0x800D_3D30
SELECTED_DESCRIPTION_HOOK_ADDRESSES = (0x8004_949C, 0x8004_94E0)

# The send-menu UI gates (v108), DORMANT since 2026-08-11 - and the price
# gate RETIRED 2026-08-16 (its slot holds the smith price gate, its hook site
# is vanilla again; the tag gate below is still hooked and dormant).  The generic list
# renderer's row loop asks a per-entry test whether to draw a price
# (`jal 0x800B0F94` - its only call site; categories 0x16/0x19 are the game's
# own priceless rows), and the checked-row colorizer stores a BUY/SELL tag
# pointer (`[0x800D15EC + mode*4]`, mode = constructor arg1) into the row's
# +0x24 object via the jump pair at 0x800B0AB8 (`j 0x800B0BC8` with the store
# in its delay slot).  Both are rewritten to slab gates keyed on
# ACTIVE_SHOP == SEND_MENU_SHOP_MARKER, which only the send menu ever set.
# With the send menu gone every real menu takes the vanilla path through them,
# which is what they always did; they stay because unhooking them is a change
# to played shop code for no player-visible gain, and they come out with a
# deliberate cleanup rather than as a side effect of this one.
GENERIC_PRICE_VISIBILITY_HOOK_ADDRESS = 0x800B_1090
VANILLA_PRICE_VISIBILITY_GATE_ADDRESS = 0x800B_0F94
CHECKED_TAG_JUMP_ADDRESS = 0x800B_0AB8
CHECKED_TAG_STORE_WORD = 0xAC62_0000  # sw v0,0x0(v1), the vanilla delay slot
CHECKED_TAG_RESUME_ADDRESS = 0x800B_0BC8

# The menu header (v109), also dormant.  The generic menu's header line reads
# its text through a pointer slot in a mode-overlay config record:
# `[0x800D479C]` = `Pay  (a-button)` at 0x80089B48.  The send pump swapped the
# slot to a slab-resident `Send` string right before constructing and every
# exit from her menu restored it; with the pump gone nothing writes the slot,
# so it holds the vanilla pointer for the whole run.  The `Send` string still
# sits in the slab at SEND_HEADER_TEXT_OFFSET, unreferenced.
MENU_HEADER_POINTER_SLOT_ADDRESS = 0x800D_479C
VANILLA_MENU_HEADER_TEXT_ADDRESS = 0x8008_9B48

# --- The intro state (v112): Kewne as a hut egg, no tutorial floor, no
# tower-entrance sequence.  Every value below is MEASURED from the
# 2026-07-28 snapshot diffs (Kewne_egg_in_hut / Tutorial_floor_removed /
# Kewne_removed_from_tower_entrance vs the Koh_position baseline): the
# save image lives at 0x80010000, the story-flag bitset at +0x2D70, the
# saved highest floor at +0x2D60.  The writes ride the save-state
# initializer's fresh-save branch (first ordinary town frame), as ORs so
# nothing already set is disturbed.
SAVE_BLOCK_ADDRESS = 0x8001_0000
STORY_FLAG_ARRAY_ADDRESS = 0x8001_2D70
SAVED_HIGHEST_FLOOR_ADDRESS = 0x8001_2D60
MONSTER_HUT_FIRST_SLOT_ADDRESS = 0x8001_0980
KEWNE_EGG_RECORD_WORD = 0x0000_1202  # id 0x02, category 0x12 (egg)

def story_flag_write(*flag_ids: int) -> tuple[int, int]:
    """(address, OR mask) for story flags that share one bitset word."""
    words = {flag_id >> 5 for flag_id in flag_ids}
    if len(words) != 1:
        raise ValueError("story_flag_write takes flags from a single 32-bit word")
    (word,) = words
    mask = 0
    for flag_id in flag_ids:
        mask |= 1 << (flag_id & 31)
    return (STORY_FLAG_ARRAY_ADDRESS + word * 4, mask)


# Wotta's pool house, open from the first visit (shipped as an easter egg -
# see `pool_house.POOL_HOUSE_OPEN`, which also forces every girl's spawn
# record present).
#
# **Measured, not guessed** (m1 ride): the delta between a save taken mid-quest
# and one taken after the Water Medal was actually delivered
# (`Wotta_pool_house_open_quest_complete.sav` -> `Wotta_pool_house_position_check.sav`).
# The pool-house entry script `0x8001D07C` is `if flag(0x0600) is CLEAR goto
# the event chain; else END`, so **0x0600 is the master switch** for having
# control on entry; 0x05FB/0x05FC/0x05FD are the chain's three stage markers
# (stage C at 0x8001F271 is the "Did you get the Water Medal back?" nag), and
# 0x11FC is both "medal delivered" and the scene init's dry-pool gate at
# 0x80016544 - the only place any pool-house code reads it. 0x11F9 is
# "quest started", kept so the state is coherent for anything else that
# reads it.
#
# An earlier revision of this list carried 0x05FE, which was WRONG: that flag
# is Wotta's variant advance, set only by the in-pool delivery script at
# 0x8001985C, and the measured post-quest save has it CLEAR (the delivery the
# player actually took runs 0x8001F8A2, which sets 0x05FD instead).
# docs/systems/pool-house.md owns the account.
POOL_HOUSE_QUEST_DONE_FLAGS = (0x05FB, 0x05FC, 0x05FD, 0x0600, 0x11F9, 0x11FC)

# (address, OR mask) pairs; the trailing zero pair terminates the table.
INTRO_STATE_WRITES = (
    (MONSTER_HUT_FIRST_SLOT_ADDRESS, KEWNE_EGG_RECORD_WORD),
    # Tower-entrance sequence removal: flags 0xDAB + 0xDAC, the entire
    # stable delta of that snapshot pair - v113 verifies this pair in
    # isolation.
    (STORY_FLAG_ARRAY_ADDRESS + 0x1B4, 0x0000_1800),
    # The pool house, open from the first visit - see
    # POOL_HOUSE_QUEST_DONE_FLAGS. Three words: 0x05FB/5FC/5FD share one,
    # 0x0600 opens the next, 0x11F9/0x11FC share a third.
    # Gated by pool_house.POOL_HOUSE_OPEN.
    *(
        (
            story_flag_write(0x05FB, 0x05FC, 0x05FD),
            story_flag_write(0x0600),
            story_flag_write(0x11F9, 0x11FC),
        )
        if _POOL_HOUSE_OPEN
        else ()
    ),
    # RETIRED in v113 (played 2026-07-28: "a lot of things broke, but
    # the tutorial removal didn't actually work") - the flag delta below
    # was the stable diff of the tutorial snapshots but is NOT what
    # selects the tutorial floor, and five flags plus two save words is
    # far too much state to attribute to a load-alternate-floor-1
    # decision.  Kept for the record; the real selector needs a fresh
    # measurement (likely a live trace on the floor-1 build path).
    # (STORY_FLAG_ARRAY_ADDRESS + 0x000, 0x0000_0400),   # flag 0x00A
    # (STORY_FLAG_ARRAY_ADDRESS + 0x010, 0x1018_0000),   # 0x093/094/09C
    # (STORY_FLAG_ARRAY_ADDRESS + 0x1A8, 0x0010_0000),   # flag 0xD54
    # (SAVED_HIGHEST_FLOOR_ADDRESS, 0x0000_0001),
    # (SAVE_BLOCK_ADDRESS + 0x2D48, 0x1000_0000),
)


@dataclass(frozen=True)
class ShopSlot:
    descriptor: bytes
    price: int
    display_name: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if len(self.descriptor) != 4:
            raise ValueError("Shop descriptors must contain exactly four bytes.")
        if self.descriptor[1] == 0:
            raise ValueError("Shop descriptors must have a nonzero category.")
        if not 0 <= self.price <= 0x7FFF_FFFF:
            raise ValueError("Shop prices must fit a positive signed 32-bit value.")
        if (
            self.description is not None
            and len(self.description) > MAX_SHOP_DESCRIPTION_CHARACTERS
        ):
            raise ValueError(
                "Shop descriptions must contain at most "
                f"{MAX_SHOP_DESCRIPTION_CHARACTERS} characters."
            )


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


def _build_equipment_builder() -> bytes:
    # The patched JAL retains `move a0,s0` in its delay slot. Supply shop zero
    # and tail-call the shop-local text loader. The loader preserves the
    # catalog arguments and continues into the generic catalog builder.
    return struct.pack(
        "<4I",
        _i(0x09, 0, 5, 0),
        _j(0x02, SHOP_TEXT_LOADER_ADDRESS),
        0,
        0,
    )


def _build_monster_builder() -> bytes:
    # The patched JAL retains `move a0,s0` in its delay slot. Supply shop one
    # and tail-call the same synchronous text loader/catalog builder used by
    # Equipment. The generic builder enforces Progressive Keycard level 3.
    return struct.pack(
        "<4I",
        _i(0x09, 0, 5, 1),
        _j(0x02, SHOP_TEXT_LOADER_ADDRESS),
        0,
        0,
    )


def _build_monster_commit_wrapper() -> bytes:
    # The dialogue VM invokes native callbacks without catalog arguments.
    # Supply the Monster overlay's shop buffer, preserve the VM return address
    # in t9 across the leaf AP commit, and then tail-call Monster's own native
    # inventory compactor. This preserves the selected-item routine's proven
    # epilogue even though AP suppresses ordinary descriptor insertion. The
    # separate town receiver must still advance the pointer-list terminator
    # when it later inserts a self-return.
    return struct.pack(
        "<8I",
        _i(0x0F, 0, 4, _upper(MONSTER_CATALOG_ADDRESS)),
        _i(0x09, 4, 4, _lower(MONSTER_CATALOG_ADDRESS)),
        _r(31, 0, 25, 0, 0x21),  # move t9,ra
        _j(0x03, COMMIT_PURCHASES_ADDRESS),
        0,
        _r(25, 0, 31, 0, 0x21),  # move ra,t9
        _j(0x02, MONSTER_COMPACT_INVENTORY_POINTER_TABLE_ADDRESS),
        0,
    )


def _build_shop_text_loader() -> bytes:
    # a0 = overlay catalog, a1 = shop index. This is the same legal synchronous
    # command-6 sequence used by the live-proven tower seed loader. Waiting
    # before enqueueing avoids competing with a residual overlay/resource
    # request. The descriptor occupies stack +0x10..+0x17; caller arguments
    # and ra are saved above it and restored before the catalog tail-call.
    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -40),
        _i(0x2B, 29, 31, 0x24),
        _i(0x2B, 29, 4, 0x20),
        _i(0x2B, 29, 5, 0x1C),
        _j(0x03, WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS),
        0,
        _i(0x23, 29, 9, 0x1C),
        _i(0x09, 0, 4, 1),
    )
    _load_address(b, 5, SHOP_TEXT_BANK_ADDRESS)
    b.emit(
        _i(0x09, 29, 6, 0x10),
        _i(0x09, 0, 8, SHOP_TEXT_SECTOR_LBAS[0]),
        _r(8, 9, 7, 0, 0x23),  # a3 = first LBA - shop index
        _j(0x03, BUILD_CD_READ_DESCRIPTOR_ADDRESS),
        0,
        _i(0x09, 0, 4, 6),
        _i(0x09, 29, 5, 0x10),
        _j(0x03, ENQUEUE_CD_COMMAND_ADDRESS),
        _r(0, 0, 6, 0, 0x21),
        _j(0x03, WAIT_FOR_CD_COMMAND_QUEUE_ADDRESS),
        0,
        _i(0x23, 29, 4, 0x20),
        _i(0x23, 29, 5, 0x1C),
        _i(0x23, 29, 31, 0x24),
        _i(0x09, 29, 29, 40),
        _j(0x02, GENERIC_BUILDER_ADDRESS),
        0,
    )
    return b.build()


def _build_catalog_builder() -> bytes:
    # a0 = the overlay's 0x100-byte catalog, a1 = shop index. The first entry
    # is vanilla's pseudo-item; ordinary entries are compacted around checks
    # already present in the durable 30-bit mask.
    b = _MipsBuilder()
    # Equipment can be visited before the first tower trip. Initialize the
    # game-owned save-tail journal from this generated town payload so an
    # offline purchase cannot be erased by the later tower initializer.
    b.emit(
        _i(0x09, 29, 29, -8),
        _i(0x2B, 29, 31, 4),
        _j(0x03, STATE_INITIALIZER_ADDRESS),
        0,
        _i(0x23, 29, 31, 4),
        _i(0x09, 29, 29, 8),
    )
    _load_address(b, 25, SHOP_CORE_ADDRESS)  # t9: core/state base
    b.emit(
        _i(0x28, 25, 5, ACTIVE_SHOP_OFFSET),
        _i(0x2B, 25, 4, ACTIVE_CATALOG_OFFSET),
        _i(0x09, 0, 8, 1),
        _i(0x28, 25, 8, ARMED_MENU_OFFSET),
        _i(0x09, 0, 8, -1),
        _i(0x2B, 25, 8, ACTIVE_SLOT_MAP_OFFSET),
        _i(0x2B, 25, 8, ACTIVE_SLOT_MAP_OFFSET + 4),
        _i(0x2B, 25, 8, ACTIVE_SLOT_MAP_OFFSET + 8),
        _i(0x2B, 25, 8, ACTIVE_SLOT_MAP_OFFSET + 12),
    )
    # Equipment stores this native 01 16 00 00 descriptor at 0x80018A98,
    # while Monster stores the identical word at 0x80018880. Embedding the
    # proven shared value avoids following an overlay-specific data pointer.
    b.emit(_i(0x09, 0, 9, SHOP_LEADING_ENTRY_WORD), _i(0x2B, 4, 9, 0))

    # Required keycard levels are 0, 0, and 6 for shops 0, 1, and 2:
    # (shop & 2) * 3.  The Monster Shop (index 1) is no longer gated here -
    # its lock is the building door (build_monster_door_gate), proven live
    # 2026-07-27, and two gates for one lock would just hide door-gate
    # regressions.  Fur's shop (index 2) keeps this interior guard until it
    # gets a door of its own.
    b.emit(
        _i(0x0C, 5, 8, 2),
        _r(0, 8, 9, 1, 0x00),
        _r(8, 9, 8, 0, 0x21),
    )
    _load_address(b, 9, PERSISTENT_STATE_ADDRESS + PERSISTENT_KEYCARD_LEVEL_OFFSET)
    b.emit(_i(0x23, 9, 10, 0), 0, _r(10, 8, 11, 0, 0x2B))
    b.branch(0x05, 11, 0, "finish_empty")
    b.emit(_i(0x09, 4, 12, 4))  # catalog destination, also safe delay work

    # record = MANIFEST + shop * 120; absolute_slot = shop * 10.
    b.emit(
        _r(0, 5, 8, 7, 0x00),
        _r(0, 5, 9, 3, 0x00),
        _r(8, 9, 8, 0, 0x23),
    )
    _load_address(b, 9, MANIFEST_ADDRESS)
    b.emit(
        _r(9, 8, 13, 0, 0x21),
        _r(0, 5, 14, 3, 0x00),
        _r(0, 5, 15, 1, 0x00),
        _r(14, 15, 14, 0, 0x21),
        _i(0x09, 25, 15, ACTIVE_SLOT_MAP_OFFSET + 1),
    )
    _load_address(b, 8, PERSISTENT_SHOP_MASK_ADDRESS)
    b.emit(_i(0x23, 8, 8, 0), _i(0x09, 0, 24, SLOTS_PER_SHOP), 0)

    b.label("loop")
    b.emit(_i(0x23, 13, 9, 0), 0)
    b.branch(0x04, 9, 0, "skip")
    b.emit(_i(0x09, 0, 10, 1))
    b.emit(
        _r(14, 10, 10, 0, 0x04),
        _r(8, 10, 11, 0, 0x24),
    )
    b.branch(0x05, 11, 0, "skip")
    b.emit(_i(0x2B, 12, 9, 0))
    b.emit(
        _i(0x28, 15, 14, 0),
        _i(0x09, 12, 12, 4),
        _i(0x09, 15, 15, 1),
    )

    b.label("skip")
    b.emit(
        _i(0x09, 13, 13, MANIFEST_RECORD_SIZE),
        _i(0x09, 14, 14, 1),
        _i(0x09, 24, 24, -1),
    )
    b.branch(0x05, 24, 0, "loop")
    b.emit(0)
    b.branch(0x04, 0, 0, "finish")
    b.emit(0)

    b.label("finish_empty")
    b.emit(_i(0x09, 4, 12, 4))
    b.label("finish")
    b.emit(_i(0x2B, 12, 0, 0), _r(31, 0, 0, 0, 0x08), 0)
    return b.build()


def _build_purchase_commit() -> bytes:
    # The equipment overlay has already debited the selected total. Convert
    # each selected visible entry into its original manifest bit and suppress
    # all vanilla inventory insertion.
    b = _MipsBuilder()
    _load_address(b, 8, SHOP_CORE_ADDRESS)
    b.emit(
        _i(0x24, 8, 3, ACTIVE_SHOP_OFFSET),
        _i(0x09, 0, 10, SHOP_COUNT),
        _r(3, 10, 11, 0, 0x2B),
    )
    b.branch(0x04, 11, 0, "return")
    b.emit(_i(0x09, 4, 12, 4))
    b.emit(_i(0x09, 8, 13, ACTIVE_SLOT_MAP_OFFSET + 1))
    _load_address(b, 14, PERSISTENT_SHOP_MASK_ADDRESS)
    b.emit(_i(0x23, 14, 15, 0), 0)

    b.label("loop")
    b.emit(_i(0x24, 12, 9, 1), 0)
    b.branch(0x04, 9, 0, "store")
    b.emit(_i(0x24, 12, 10, 3))
    # The PS1 R3000 exposes a loaded byte only after the following
    # instruction. Without this delay slot, the selection test observes the
    # preceding catalog row's flags, shifting a completed check forward by
    # one row (and dropping the final row entirely).
    b.emit(0)
    b.emit(_i(0x0C, 10, 10, 0x20))
    b.branch(0x04, 10, 0, "next")
    b.emit(_i(0x24, 13, 9, 0))
    b.emit(_i(0x09, 0, 10, 0xFF))
    b.branch(0x04, 9, 10, "next")
    b.emit(_i(0x09, 0, 10, 1))
    b.emit(_r(9, 10, 10, 0, 0x04), _r(15, 10, 15, 0, 0x25))

    b.label("next")
    b.emit(_i(0x09, 12, 12, 4), _i(0x09, 13, 13, 1))
    b.branch(0x04, 0, 0, "loop")
    b.emit(0)

    b.label("store")
    b.emit(_i(0x2B, 14, 15, 0))

    b.label("return")
    # The displaced Equipment routine always finishes by repairing its
    # inventory-order table. Preserve that epilogue for shop zero. Monster's
    # wrapper supplies its overlay-specific compactor after this leaf routine
    # returns; future shop two must do the same once its native seam is known.
    b.branch(0x04, 3, 0, "compact_equipment_inventory")
    b.emit(0, _r(31, 0, 0, 0, 0x08), 0)
    b.label("compact_equipment_inventory")
    b.emit(_j(0x02, COMPACT_INVENTORY_POINTER_TABLE_ADDRESS), 0)
    return b.build()


def _build_state_initializer() -> bytes:
    # Validate magic, version/size, and the generated eight-byte signature.
    # A mismatch creates a fresh ADSV record (patch.PERSISTENT_STATE_SIZE)
    # before the shop reads or mutates its check mask. This is ordinary save data, not client-owned RAM.
    b = _MipsBuilder()
    _load_address(b, 8, PERSISTENT_STATE_ADDRESS)
    _load_address(b, 10, SHOP_CORE_ADDRESS + SEED_SIGNATURE_OFFSET)
    b.emit(_i(0x23, 8, 9, 0))
    b.emit(
        _i(0x0F, 0, 11, (PERSISTENT_STATE_MAGIC >> 16) & 0xFFFF),
        _i(0x0D, 11, 11, PERSISTENT_STATE_MAGIC & 0xFFFF),
    )
    b.branch(0x05, 9, 11, "initialize")
    b.emit(0)
    b.emit(
        _i(0x23, 8, 9, 4),
        _i(0x0F, 0, 12, PERSISTENT_STATE_SIZE),
        _i(0x0D, 12, 12, PERSISTENT_STATE_VERSION),
    )
    b.branch(0x05, 9, 12, "initialize")
    b.emit(0)
    b.emit(_i(0x23, 8, 9, 8), _i(0x23, 10, 12, 0), 0)
    b.branch(0x05, 9, 12, "initialize")
    b.emit(0)
    b.emit(_i(0x23, 8, 9, 12), _i(0x23, 10, 12, 4), 0)
    b.branch(0x05, 9, 12, "initialize")
    b.emit(0)
    b.emit(_r(31, 0, 0, 0, 0x08), 0)

    b.label("initialize")
    b.emit(
        _i(0x2B, 8, 11, 0),
        _i(0x0F, 0, 9, PERSISTENT_STATE_SIZE),
        _i(0x0D, 9, 9, PERSISTENT_STATE_VERSION),
        _i(0x2B, 8, 9, 4),
        _i(0x23, 10, 9, 0),
        _i(0x23, 10, 10, 4),
        0,
        _i(0x2B, 8, 9, 8),
        _i(0x2B, 8, 10, 12),
    )
    # Same zero span as the tower initializer, from the same helper.
    patch._emit_persistent_state_zero_loop(b, base=8, cursor=9, end=10, label="zero")
    b.emit(
        # Fresh save: tail into the intro-state writer (Kewne hut egg, no
        # tutorial floor, no tower-entrance sequence), which returns to
        # the original caller.  The already-initialized path above returns
        # without it.
        _j(0x02, INTRO_STATE_WRITER_ADDRESS),
        0,
    )
    return b.build()


def _emit_catalog_slot_lookup(b: _MipsBuilder, fallback: str) -> None:
    # On success t3 (11) is the absolute manifest slot and t9 (25) is the
    # core base. a0 remains untouched for a vanilla tail-call.
    _load_address(b, 25, SHOP_CORE_ADDRESS)
    b.emit(
        _i(0x24, 25, 8, ACTIVE_SHOP_OFFSET),
        _i(0x09, 0, 9, IMPLEMENTED_SHOP_COUNT),
        0,
        _r(8, 9, 10, 0, 0x2B),
    )
    b.branch(0x04, 10, 0, fallback)
    b.emit(0)
    # The generic builder records its actual a0 catalog pointer. Loading that
    # value here handles Equipment, Monster, and future overlay-specific
    # buffers without hard-coding one shop's address. The NOP is the required
    # R3000 load delay before the descriptor subtraction consumes t0.
    b.emit(_i(0x23, 25, 8, ACTIVE_CATALOG_OFFSET), 0)
    b.emit(
        _r(4, 8, 9, 0, 0x23),
        _i(0x0B, 9, 10, (SLOTS_PER_SHOP + 1) * 4),
    )
    b.branch(0x04, 10, 0, fallback)
    b.emit(_i(0x0C, 9, 10, 3))
    b.branch(0x05, 10, 0, fallback)
    b.emit(_r(0, 9, 9, 2, 0x02))
    b.emit(
        _i(0x09, 25, 10, ACTIVE_SLOT_MAP_OFFSET),
        _r(10, 9, 10, 0, 0x21),
        _i(0x24, 10, 11, 0),
        _i(0x09, 0, 8, 0xFF),
        0,
    )
    b.branch(0x04, 11, 8, fallback)
    b.emit(0)


def _emit_manifest_record_address(b: _MipsBuilder, destination: int) -> None:
    # destination = MANIFEST + slot * 12
    b.emit(
        _r(0, 11, 8, 3, 0x00),
        _r(0, 11, 9, 2, 0x00),
        _r(8, 9, 8, 0, 0x21),
    )
    _load_address(b, 9, MANIFEST_ADDRESS)
    b.emit(_r(9, 8, destination, 0, 0x21))


def _build_buy_price_resolver() -> bytes:
    b = _MipsBuilder()
    _emit_catalog_slot_lookup(b, "fallback")
    _emit_manifest_record_address(b, 8)
    b.emit(_i(0x23, 8, 2, 4), _r(31, 0, 0, 0, 0x08), 0)
    b.label("fallback")
    # Not an AP row: through the smith price gate (temper cost while the
    # blacksmith's menu is open, vanilla buy price otherwise). t9 still holds
    # the slab base from the lookup above; the gate relies on it.
    b.emit(_j(0x02, SMITH_PRICE_GATE_ADDRESS), 0)
    return b.build()


def _build_smith_price_gate() -> bytes:
    """Row price for the blacksmith's temper menu.

    Reached only from the buy-price resolver's fallback (t9 = slab base, a0 =
    the catalog descriptor). While ACTIVE_SHOP is the smith marker the price
    is the smith's temper cost for that descriptor's quality; every other menu
    gets the vanilla buy price it always got. Both targets return to the row
    builder's own `jal`.
    """

    b = _MipsBuilder()
    b.emit(_i(0x24, 25, 8, ACTIVE_SHOP_OFFSET))         # lbu t0, ACTIVE_SHOP(t9)
    b.emit(_i(0x09, 0, 9, SMITH_MENU_SHOP_MARKER))      # li t1, marker (load delay)
    b.branch(0x05, 8, 9, "vanilla")                     # bne -> vanilla price
    b.emit(0)
    b.emit(_j(0x02, SMITH_PRICE_ENTRY_ADDRESS), 0)
    b.label("vanilla")
    b.emit(_j(0x02, VANILLA_BUY_PRICE_ADDRESS), 0)
    return b.build()


def _build_item_name_resolver() -> bytes:
    # a0 = descriptor, a1 = output style/variant word. Return the custom
    # full-width game string only for remote entries; native entries retain
    # the descriptor-derived name and all ordinary formatting.
    b = _MipsBuilder()
    _emit_catalog_slot_lookup(b, "fallback")
    _emit_manifest_record_address(b, 8)
    b.emit(_i(0x23, 8, 2, 8), 0)
    b.branch(0x04, 2, 0, "fallback")
    b.emit(_i(0x2B, 5, 0, 0))
    b.emit(_r(31, 0, 0, 0, 0x08), 0)
    b.label("fallback")
    b.emit(_j(0x02, VANILLA_ITEM_TEXT_ADDRESS), 0)
    return b.build()


def _build_description_resolver() -> bytes:
    # a0 = selected catalog descriptor, a1 = vanilla fallback-text table.
    # The generic shop uses two resident wrappers for active/inactive menus;
    # both call this resolver in place of the vanilla description routine.
    # A generated string is used only when the descriptor maps to an active AP
    # slot and the synchronously loaded bank has a valid header and offset.
    b = _MipsBuilder()
    _emit_catalog_slot_lookup(b, "fallback")

    _load_address(b, 12, SHOP_TEXT_BANK_ADDRESS)  # t4: loaded text bank
    b.emit(_i(0x23, 12, 8, 0))
    magic = int.from_bytes(SHOP_TEXT_MAGIC, "little")
    b.emit(
        _i(0x0F, 0, 9, (magic >> 16) & 0xFFFF),
        _i(0x0D, 9, 9, magic & 0xFFFF),
    )
    b.branch(0x05, 8, 9, "fallback")
    b.emit(0)

    # Validate the format version and ensure that the bank belongs to the
    # currently armed shop. Independent instructions satisfy both load delays.
    b.emit(
        _i(0x25, 12, 8, 4),
        _i(0x09, 0, 9, SHOP_TEXT_VERSION),
    )
    b.branch(0x05, 8, 9, "fallback")
    b.emit(0)
    b.emit(
        _i(0x24, 12, 8, 8),
        _i(0x24, 25, 9, ACTIVE_SHOP_OFFSET),
        0,
    )
    b.branch(0x05, 8, 9, "fallback")
    b.emit(0)

    # Convert the absolute manifest slot supplied by the compact-row map to
    # the sector's shop-relative 0..9 offset-table index.
    b.emit(
        _r(0, 9, 8, 3, 0x00),
        _r(0, 9, 10, 1, 0x00),
        _r(8, 10, 8, 0, 0x21),
        _r(11, 8, 11, 0, 0x23),
        _i(0x0B, 11, 8, SLOTS_PER_SHOP),
    )
    b.branch(0x04, 8, 0, "fallback")
    b.emit(0)
    b.emit(
        _r(0, 11, 11, 1, 0x00),
        _i(0x09, 11, 11, SHOP_TEXT_OFFSET_TABLE),
        _r(12, 11, 11, 0, 0x21),
        _i(0x25, 11, 11, 0),
        0,
    )
    b.branch(0x04, 11, 0, "fallback")
    b.emit(0)

    # Zero means native description. Generated offsets must stay inside the
    # text area and before the end marker even if RAM was unexpectedly stale.
    b.emit(_i(0x0B, 11, 8, SHOP_TEXT_DATA_OFFSET))
    b.branch(0x05, 8, 0, "fallback")
    b.emit(0)
    b.emit(_i(0x0B, 11, 8, SHOP_TEXT_END_MARKER_OFFSET))
    b.branch(0x04, 8, 0, "fallback")
    b.emit(0)
    b.emit(_r(12, 11, 4, 0, 0x21))

    b.emit(
        _i(0x09, 29, 29, -24),
        _i(0x2B, 29, 31, 20),
        _j(0x03, SHOW_ITEM_DESCRIPTION_ADDRESS),
        0,
        _i(0x23, 29, 31, 20),
        _i(0x09, 29, 29, 24),
        _r(31, 0, 0, 0, 0x08),
        0,
    )

    b.label("fallback")
    b.emit(_j(0x02, VANILLA_ITEM_DESCRIPTION_ADDRESS), 0)
    return b.build()


def _build_resident_description_gate() -> bytes:
    # The two patched call sites are resident and are also used by the tower
    # inventory. The full resolver lives in town-mode RAM, so verify that its
    # payload is currently loaded before tail-jumping to it. Loading through a
    # rounded-up LUI lets the signed LW displacement address 0x800fb418 in one
    # instruction. The two magic-constant instructions provide more than the
    # required R3000 load-delay separation before t1 is consumed by BNE.
    b = _MipsBuilder()
    magic = int.from_bytes(SHOP_CORE_MAGIC[:4], "little")
    b.emit(
        _i(0x0F, 0, 8, _upper(SHOP_CORE_ADDRESS)),
        _i(0x23, 8, 9, _lower(SHOP_CORE_ADDRESS)),
        _i(0x0F, 0, 10, (magic >> 16) & 0xFFFF),
        _i(0x0D, 10, 10, magic & 0xFFFF),
    )
    b.branch(0x05, 9, 10, "fallback")
    b.emit(0)
    b.emit(_j(0x02, DESCRIPTION_RESOLVER_ADDRESS), 0)
    b.label("fallback")
    b.emit(_j(0x02, VANILLA_ITEM_DESCRIPTION_ADDRESS), 0)
    result = b.build()
    if len(result) != RESIDENT_DESCRIPTION_GATE_SIZE:
        raise ValueError(
            "Resident description gate must exactly fill its reserved code span."
        )
    return result


def _build_resident_description_slot() -> bytes:
    fallback = _encode_shop_name(
        UNFAMILIAR_ITEM_DESCRIPTION,
        max_characters=None,
    )
    result = _build_resident_description_gate() + fallback
    if len(result) > RESIDENT_DESCRIPTION_SLOT_SIZE:
        raise ValueError("Resident description gate and fallback exceed the Cream slot.")
    return result.ljust(RESIDENT_DESCRIPTION_SLOT_SIZE, b"\0")


def _build_menu_constructor_wrapper() -> bytes:
    # A custom builder arms exactly the next generic menu construction. Any
    # other buy/sell menu clears the active AP marker before tail-calling the
    # original constructor, preventing stale maps from affecting other shops.
    b = _MipsBuilder()
    _load_address(b, 8, SHOP_CORE_ADDRESS)
    b.emit(_i(0x24, 8, 9, ARMED_MENU_OFFSET), 0)
    b.branch(0x05, 9, 0, "tail")
    b.emit(_i(0x28, 8, 0, ARMED_MENU_OFFSET))
    b.emit(_i(0x09, 0, 9, -1), _i(0x28, 8, 9, ACTIVE_SHOP_OFFSET))
    b.label("tail")
    b.emit(_j(0x02, VANILLA_MENU_CONSTRUCTOR_ADDRESS), 0)
    return b.build()


def _build_intro_state_table() -> bytes:
    out = bytearray()
    for address, mask in INTRO_STATE_WRITES:
        out += struct.pack("<II", address, mask)
    out += struct.pack("<II", 0, 0)
    return bytes(out)


def _build_intro_state_writer() -> bytes:
    """OR the measured intro-state values into the fresh save image.

    Tail-called from the save-state initializer's fresh-save branch only,
    so the writes happen exactly once per new save, on the first ordinary
    town frame - before the player can reach the monster hut or the
    tower.  Walks INTRO_STATE_WRITES as (address, mask) pairs.
    """

    b = _MipsBuilder()
    _load_address(b, 8, INTRO_STATE_TABLE_ADDRESS)     # t0 = table cursor
    b.label("loop")
    b.emit(
        _i(0x23, 8, 9, 0),                             # lw t1,0(t0) address
        _i(0x23, 8, 10, 4),                            # lw t2,4(t0) mask
    )
    b.branch(0x04, 9, 0, "done")                       # zero terminator
    b.emit(0)
    b.emit(
        _i(0x23, 9, 11, 0),                            # lw t3,0(t1)
        _i(0x09, 8, 8, 8),                             # t0 += 8 (load delay)
        _r(11, 10, 11, 0, 0x25),                       # or t3,t3,t2
    )
    b.branch(0x04, 0, 0, "loop")
    b.emit(_i(0x2B, 9, 11, 0))                         # (delay) sw t3,0(t1)
    b.label("done")
    b.emit(_r(31, 0, 0, 0, 0x08), 0)                   # jr ra
    return b.build()


def _build_price_visibility_gate() -> bytes:
    """RETIRED 2026-08-16 (kept for the record, not emitted): no price text on
    the send menu's rows. Its slab slot is the smith price gate now and its
    hook site is back to vanilla.

    Interposed on the row loop's only call to the per-entry price test
    (`0x800B0F94`).  Sends cost nothing, so while the active AP shop is
    the send marker every row is priceless; every other menu falls
    through to the vanilla category test.  a0 (the catalog entry) is
    left untouched for the fall-through.
    """

    b = _MipsBuilder()
    _load_address(b, 8, SHOP_CORE_ADDRESS)
    b.emit(_i(0x24, 8, 9, ACTIVE_SHOP_OFFSET), 0)      # lbu t1 (+load delay)
    b.emit(_i(0x09, 0, 10, SEND_MENU_SHOP_MARKER))     # li t2,marker
    b.branch(0x05, 9, 10, "vanilla")                   # bne -> vanilla test
    b.emit(0)
    b.emit(
        _r(31, 0, 0, 0, 0x08),                         # jr ra
        _r(0, 0, 2, 0, 0x21),                          # (delay) v0 = 0
    )
    b.label("vanilla")
    b.emit(_j(0x02, VANILLA_PRICE_VISIBILITY_GATE_ADDRESS), 0)
    return b.build()


def _build_checked_tag_gate() -> bytes:
    """No BUY/SELL tag on the send menu's checked rows; the green stays.

    The checked-row colorizer greens the row's three text objects, then
    stores the mode's tag pointer into the row's +0x24 object through the
    jump pair at `CHECKED_TAG_JUMP_ADDRESS` (`j resume` with the store in
    its delay slot).  The pair is rewritten to jump here with the store
    lifted out, so the send menu skips it and every other menu replays
    it, then resumes exactly where vanilla did.  v0 (tag pointer) and v1
    (tag object) are still live from the colorizer.
    """

    b = _MipsBuilder()
    _load_address(b, 8, SHOP_CORE_ADDRESS)
    b.emit(_i(0x24, 8, 9, ACTIVE_SHOP_OFFSET), 0)      # lbu t1 (+load delay)
    b.emit(_i(0x09, 0, 10, SEND_MENU_SHOP_MARKER))     # li t2,marker
    b.branch(0x04, 9, 10, "skip")                      # beq -> skip the tag
    b.emit(0)
    b.emit(_i(0x2B, 3, 2, 0))                          # sw v0,0x0(v1)
    b.label("skip")
    b.emit(_j(0x02, CHECKED_TAG_RESUME_ADDRESS), 0)
    return b.build()


def _build_check_capacity_gate() -> bytes:
    """The A press on a blacksmith row is a purchase, not a check.

    Interposed on the A-press handler's only call to the check-capacity
    guard (`0x800AD99C`, the vanilla "checked rows + occupied slots under
    twenty" BUY rule; the handler toggles the row's 0x20 bit and recolours it
    when the guard answers nonzero, plays the refusal sound and shows
    `[0x800D1558]` when it answers 0). While the active AP shop is the SMITH
    marker the call is handed to the smith's guard native, which debits the
    temper cost, bumps the item's quality, rebuilds the rows and answers 1 -
    with the row's 0x20 bit pre-set so the toggle that follows clears it
    again - or answers 0 (the opener has repointed the refusal message at
    `Not enough gold`). Every other menu tail-calls the vanilla guard, whose
    `jr ra` returns to the original caller because this gate was reached by
    the caller's own `jal`.

    History: v108-2026-08-11 this gate lifted the limit for Nada's send menu
    (marker 3), which is gone; the send marker is not tested here any more.
    The familiar refusal is upstream of the guard (`0x80` at `0x800ADA54`)
    and is untouched.
    """

    b = _MipsBuilder()
    _load_address(b, 8, SHOP_CORE_ADDRESS)
    b.emit(_i(0x24, 8, 9, ACTIVE_SHOP_OFFSET), 0)      # lbu t1 (+load delay)
    b.emit(_i(0x09, 0, 10, SMITH_MENU_SHOP_MARKER))    # li t2,marker
    b.branch(0x05, 9, 10, "vanilla")                   # bne -> vanilla guard
    b.emit(0)
    b.emit(_j(0x02, SMITH_GUARD_ENTRY_ADDRESS), 0)     # the smith's purchase
    b.label("vanilla")
    b.emit(_j(0x02, VANILLA_CHECK_CAPACITY_GUARD_ADDRESS), 0)
    return b.build()


def _encode_shop_name(
    text: str,
    max_characters: int | None = MAX_SHOP_NAME_CHARACTERS,
) -> bytes:
    # The native item records use full-width CP932 strings. Keep the visible
    # name bounded for the one-line shop layout and use a full-width question
    # mark for characters absent from the game's font.
    if max_characters is not None:
        text = text[:max_characters]
    result = bytearray()
    for character in text:
        if character == " ":
            converted = "\u3000"
        elif "!" <= character <= "~":
            converted = chr(ord(character) + 0xFEE0)
        else:
            converted = character
        try:
            result.extend(converted.encode("cp932"))
        except UnicodeEncodeError:
            result.extend("？".encode("cp932"))
    result.append(0)
    return bytes(result)


def _build_monster_buy_script() -> bytes:
    # Azure Dreams' town dialogue VM uses absolute script pointers. This is a
    # compact form of the live Equipment buy transaction: open the dormant
    # Monster buy menu, total selected rows, check funds, confirm, debit, and
    # call the AP commit wrapper. It occupies only the confirmed-zero tail of
    # the Monster dialogue resource and leaves the native sell branch intact.
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
            raise ValueError(f"Duplicate Monster dialogue label: {name}")
        labels[name] = len(script)

    def emit_text(text: str) -> None:
        script.extend(_encode_shop_name(text, max_characters=None)[:-1])

    def emit_native_call(address: int) -> None:
        emit(0x4C)
        emit_address(address)

    def emit_choice(text: str) -> None:
        emit(0x0B, 0x81, 0x6D)
        emit_text(text)
        emit(0x81, 0x6E)

    label("start")
    emit(0x08, 0x15)
    emit_address(MONSTER_BUY_MENU_SCRIPT_ADDRESS)
    emit(0x3E, 0x0E)
    emit_label_address("end")
    emit(0x08)
    emit_native_call(MONSTER_SUM_SELECTED_BUY_ADDRESS)
    emit_text("That'll be ")
    emit(0xFD, 0x0F)
    emit_text("G.")
    emit(0x0A)
    emit_native_call(MONSTER_CAN_AFFORD_ADDRESS)
    emit(0x3F, 0x0F)
    emit_label_address("enough_money")
    emit(0x57, 0x10)
    emit_text("You don't have enough money.")
    emit(0x11, 0x17)
    emit_label_address("start")

    label("enough_money")
    emit(0x57, 0x01)
    emit_choice("I'll buy.")
    emit(*(0x81, 0x40) * 5)
    emit(0x81, 0x6D)
    emit_text("My mistake.")
    emit(0x81, 0x6E, 0x0A)
    emit_choice("Not buying.")
    emit(0x2C, 0x03, 0x1A)
    emit_label_address("confirm")
    emit_label_address("start")
    emit_label_address("end")

    label("confirm")
    emit(0x08, 0x57, 0x10)
    emit_text("Thanks very much.")
    emit(0x0A)
    emit_native_call(MONSTER_SUBTRACT_TOTAL_ADDRESS)
    emit_native_call(MONSTER_COMMIT_WRAPPER_ADDRESS)
    emit(0x17)
    emit_label_address("start")

    label("end")
    emit(0x16)

    for offset, name in fixups:
        if name not in labels:
            raise ValueError(f"Unknown Monster dialogue label: {name}")
        struct.pack_into("<I", script, offset, MONSTER_BUY_SCRIPT_ADDRESS + labels[name])

    if len(script) > MONSTER_BUY_SCRIPT_CAPACITY:
        raise ValueError("Monster buy transaction exceeds its confirmed-zero dialogue tail.")
    return bytes(script)


def format_shop_description(item_name: str, player_name: str) -> str:
    """`<item>` newline `for <player>`, for **every** shop slot.

    This used to apply only to items from other games, because a native Azure
    Dreams reward already had a native description and its own name. But a shop
    slot in a multiworld is a placement like any other, and the one thing a
    buyer wants to know is what it is and who it is for - including when the
    answer is "you". The tower's floor markers describe themselves the same way.
    """

    description = (
        f"{item_name[:MAX_SHOP_DESCRIPTION_ITEM_CHARACTERS]}\n"
        f"for {player_name[:MAX_SHOP_DESCRIPTION_PLAYER_CHARACTERS]}"
    )
    if len(description) > MAX_SHOP_DESCRIPTION_CHARACTERS:
        raise ValueError(
            "The bounded unfamiliar-item description exceeded its two-line limit."
        )
    return description


def build_shop_text_sector(
    slots: Sequence[ShopSlot | None],
    shop_index: int,
    seed_signature: bytes = bytes(SEED_SIGNATURE_SIZE),
) -> bytes:
    if len(slots) != SHOP_SLOT_COUNT:
        raise ValueError(f"Expected exactly {SHOP_SLOT_COUNT} town shop slots.")
    if not 0 <= shop_index < SHOP_COUNT:
        raise ValueError(f"Invalid town shop index: {shop_index}.")
    if len(seed_signature) != SEED_SIGNATURE_SIZE:
        raise ValueError(f"Town shop seed signature must be {SEED_SIGNATURE_SIZE} bytes.")

    sector = bytearray(SHOP_TEXT_BANK_SIZE)
    sector[0:4] = SHOP_TEXT_MAGIC
    struct.pack_into("<HHBBH", sector, 4, SHOP_TEXT_VERSION, SLOTS_PER_SHOP, shop_index, 0, 0)
    sector[0x0C:0x14] = seed_signature
    sector[SHOP_TEXT_END_MARKER_OFFSET:] = SHOP_TEXT_END_MARKER

    cursor = SHOP_TEXT_DATA_OFFSET
    encoded_descriptions: dict[bytes, int] = {}
    first_slot = shop_index * SLOTS_PER_SHOP
    for relative_slot in range(SLOTS_PER_SHOP):
        slot = slots[first_slot + relative_slot]
        if slot is None or slot.description is None:
            continue
        encoded = _encode_shop_name(slot.description, max_characters=None)
        offset = encoded_descriptions.get(encoded)
        if offset is None:
            offset = cursor
            end = cursor + len(encoded)
            if end > SHOP_TEXT_END_MARKER_OFFSET:
                raise ValueError("Town shop descriptions exceed one 2 KiB text sector.")
            sector[cursor:end] = encoded
            encoded_descriptions[encoded] = offset
            cursor = end
        struct.pack_into(
            "<H",
            sector,
            SHOP_TEXT_OFFSET_TABLE + relative_slot * 2,
            offset,
        )

    struct.pack_into("<H", sector, 0x0A, cursor)
    return bytes(sector)


def _copy_region(payload: bytearray, offset: int, data: bytes, end: int, name: str) -> None:
    if offset < 0 or offset + len(data) > end:
        raise ValueError(f"Town shop {name} does not fit its reserved payload span.")
    payload[offset : offset + len(data)] = data


def build_town_shop_payload(
    slots: Sequence[ShopSlot | None],
    seed_signature: bytes = bytes(SEED_SIGNATURE_SIZE),
) -> bytes:
    if len(slots) != SHOP_SLOT_COUNT:
        raise ValueError(f"Expected exactly {SHOP_SLOT_COUNT} town shop slots.")
    if len(seed_signature) != SEED_SIGNATURE_SIZE:
        raise ValueError(f"Town shop seed signature must be {SEED_SIGNATURE_SIZE} bytes.")

    payload = bytearray(SHOP_CORE_SIZE)
    payload[0:8] = SHOP_CORE_MAGIC
    struct.pack_into("<HH", payload, 8, SHOP_CORE_VERSION, SHOP_SLOT_COUNT)
    payload[ACTIVE_SHOP_OFFSET] = 0xFF
    payload[ACTIVE_SLOT_MAP_OFFSET : ACTIVE_SLOT_MAP_OFFSET + ACTIVE_SLOT_MAP_SIZE] = (
        b"\xFF" * ACTIVE_SLOT_MAP_SIZE
    )
    payload[SEED_SIGNATURE_OFFSET : SEED_SIGNATURE_OFFSET + SEED_SIGNATURE_SIZE] = seed_signature

    encoded_description = _encode_shop_name(
        UNFAMILIAR_ITEM_DESCRIPTION,
        max_characters=None,
    )
    _copy_region(
        payload,
        UNFAMILIAR_ITEM_DESCRIPTION_OFFSET,
        encoded_description,
        SHOP_DATA_END_OFFSET,
        "unfamiliar-item description",
    )

    regions = (
        (MONSTER_BUILDER_OFFSET, _build_monster_builder(), EQUIPMENT_BUILDER_OFFSET, "Monster wrapper"),
        (EQUIPMENT_BUILDER_OFFSET, _build_equipment_builder(), GENERIC_BUILDER_OFFSET, "equipment wrapper"),
        (
            GENERIC_BUILDER_OFFSET,
            _build_catalog_builder(),
            MONSTER_COMMIT_WRAPPER_OFFSET,
            "catalog builder",
        ),
        (
            MONSTER_COMMIT_WRAPPER_OFFSET,
            _build_monster_commit_wrapper(),
            COMMIT_PURCHASES_OFFSET,
            "Monster commit wrapper",
        ),
        (COMMIT_PURCHASES_OFFSET, _build_purchase_commit(), SHOP_TEXT_LOADER_OFFSET, "purchase commit"),
        (SHOP_TEXT_LOADER_OFFSET, _build_shop_text_loader(), BUY_PRICE_OFFSET, "shop-text sector loader"),
        (BUY_PRICE_OFFSET, _build_buy_price_resolver(), ITEM_NAME_OFFSET, "buy-price resolver"),
        (
            ITEM_NAME_OFFSET,
            _build_item_name_resolver(),
            DESCRIPTION_RESOLVER_OFFSET,
            "item-name resolver",
        ),
        (
            DESCRIPTION_RESOLVER_OFFSET,
            _build_description_resolver(),
            MENU_CONSTRUCTOR_OFFSET,
            "selected-description resolver",
        ),
        (
            MENU_CONSTRUCTOR_OFFSET,
            _build_menu_constructor_wrapper(),
            STATE_INITIALIZER_OFFSET,
            "menu constructor wrapper",
        ),
        (STATE_INITIALIZER_OFFSET, _build_state_initializer(), MANIFEST_OFFSET, "save-state initializer"),
        (
            INTRO_STATE_WRITER_OFFSET,
            _build_intro_state_writer(),
            ITEM_NAME_OFFSET,
            "intro-state writer",
        ),
        (
            INTRO_STATE_TABLE_OFFSET,
            _build_intro_state_table(),
            DESCRIPTION_RESOLVER_OFFSET,
            "intro-state table",
        ),
        (
            SMITH_PRICE_GATE_OFFSET,
            _build_smith_price_gate(),
            SMITH_PRICE_GATE_END_OFFSET,
            "smith price gate (the retired send price-visibility gate's slot)",
        ),
        (
            CHECKED_TAG_GATE_OFFSET,
            _build_checked_tag_gate(),
            SEND_HEADER_TEXT_OFFSET,
            "send-menu tag gate",
        ),
        (
            SEND_HEADER_TEXT_OFFSET,
            _encode_shop_name("Send", max_characters=None),
            SEND_UI_GATES_END_OFFSET,
            "send header text",
        ),
        (
            CHECK_CAPACITY_GATE_OFFSET,
            _build_check_capacity_gate(),
            CHECK_CAPACITY_GATE_END_OFFSET,
            "check-capacity gate (smith purchase)",
        ),
    )
    for offset, code, end, name in regions:
        _copy_region(payload, offset, code, end, name)

    encoded_names: dict[bytes, int] = {}
    cursor = NAME_DATA_OFFSET
    for index, slot in enumerate(slots):
        if slot is None:
            continue
        name_address = 0
        if slot.display_name is not None:
            encoded = _encode_shop_name(slot.display_name)
            name_address = encoded_names.get(encoded, 0)
            if name_address == 0:
                if cursor + len(encoded) > NAME_DATA_END_OFFSET:
                    raise ValueError("Town shop names exceed the 4 KiB town-wide payload.")
                payload[cursor : cursor + len(encoded)] = encoded
                name_address = SHOP_CORE_ADDRESS + cursor
                encoded_names[encoded] = name_address
                cursor += len(encoded)

        record_offset = MANIFEST_OFFSET + index * MANIFEST_RECORD_SIZE
        # Byte +3 must not carry item flags. The buy catalog copies this
        # descriptor word straight into the menu's catalog, where byte +3 is
        # the menu's own check flag - the retired send catalog stripped it for
        # exactly this reason, and a row whose flag byte is already set reads as
        # one the menu has dealt with and cannot be selected. Equipment carrying
        # 0x80 (unidentified) or 0xC0 (cursed) made every equipment row in
        # both shops unbuyable.
        #
        # Nothing is lost: this descriptor is display-only - a purchase is
        # delivered by the server, and the row's text comes from display_name -
        # so id, category and quality are all the catalog needs. Quality must
        # survive; the native renderer prints it as a ball's charge count.
        payload[record_offset : record_offset + 3] = slot.descriptor[:3]
        payload[record_offset + 3] = 0
        struct.pack_into("<II", payload, record_offset + 4, slot.price, name_address)

    _copy_region(
        payload,
        DOOR_GATE_SCRIPT_OFFSET,
        build_door_locked_script(),
        DOOR_GATE_OFFSET,
        "door-locked script",
    )
    _copy_region(
        payload,
        DOOR_GATE_OFFSET,
        build_monster_door_gate(),
        DOOR_GATE_END_OFFSET,
        "Monster Shop door gate",
    )
    # 0xB70..0xC3C stays zero: it held the send-menu pump, then Nada's `ADGS`
    # gift mailbox, and holds nothing since her send was removed on 2026-08-11.
    # The cooldown word at DOOR_GATE_LATCH_OFFSET stays zero too: it is armed at
    # runtime and reset by every town reload of this slab.
    return bytes(payload)


def town_runtime_to_file_offset(address: int) -> int:
    if not TOWN_MODE_RUNTIME_ADDRESS <= address < TOWN_MODE_RUNTIME_ADDRESS + 0x8B000:
        raise ValueError(f"Address 0x{address:08x} is outside the town mode image.")
    return TOWN_MODE_FILE_OFFSET + address - TOWN_MODE_RUNTIME_ADDRESS


def equipment_runtime_to_file_offset(address: int) -> int:
    if not 0x8001_6000 <= address < 0x8002_0000:
        raise ValueError(f"Address 0x{address:08x} is outside the equipment shop overlay.")
    return EQUIPMENT_OVERLAY_FILE_OFFSET + address - 0x8001_6000


def equipment_dialogue_runtime_to_file_offset(address: int) -> int:
    if not (
        EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS
        <= address
        < EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS + 0x2000
    ):
        raise ValueError(
            f"Address 0x{address:08x} is outside the Equipment dialogue resource."
        )
    return (
        EQUIPMENT_DIALOGUE_FILE_OFFSET
        + address
        - EQUIPMENT_DIALOGUE_RUNTIME_ADDRESS
    )


def build_equipment_greeting_skip() -> bytes:
    """Open the Buy/Sell/Leave menu straight away.

    `15 <menu>` is a *call*, not a goto, and that is load-bearing: the menu's
    `Just looking around.` row runs `Then good-bye.` and ends with `16 return`,
    which pops the call stack.  Entering the menu with `17 goto` would leave
    that return unmatched.  Three of Barry's five greetings already end with
    exactly `11 wait | 15 <menu> | 01 end`, so this is the game's own
    construction with the preceding text removed rather than a new one.
    """

    return bytes([0x15]) + struct.pack(
        "<I", EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS
    ) + bytes([0x01])


def build_equipment_transaction_tail() -> bytes:
    """Acknowledge the confirmation, then go straight back to the menu.

    Keeps the `11` because the buy and sell `Thanks very much.` pages have no
    wait of their own and borrow this one.
    """

    return bytes([0x11, 0x17]) + struct.pack(
        "<I", EQUIPMENT_SHOP_MENU_SCRIPT_ADDRESS
    )


def iter_equipment_greeting_file_patches() -> tuple[tuple[int, bytes], ...]:
    """Strip Barry's greeting and every Buy/Sell preamble and exit page.

    Three kinds of edit, all inside the Equipment dialogue resource:

      * each greeting entry becomes `15 <menu> | 01`;
      * each transaction-path reference is retargeted past its prose;
      * the shared tail keeps its acknowledgement and loses its text.

    What deliberately stays: `That'll be N.` / `How about N?`, the
    `[I'll buy.] / [My mistake.] / [Not buying.]` rows,
    `You don't have enough money.`, both `Thanks very much.` confirmations,
    `(Received N.)`, and `Then good-bye.` on leaving.  Those carry information
    or are the transaction itself; the rest was atmosphere.
    """

    skip = build_equipment_greeting_skip()
    patches = [
        (equipment_dialogue_runtime_to_file_offset(entry), skip)
        for entry in EQUIPMENT_GREETING_ENTRY_ADDRESSES
    ]
    patches += [
        (
            equipment_dialogue_runtime_to_file_offset(address),
            struct.pack("<I", target),
        )
        for address, target, _ in EQUIPMENT_DIALOGUE_RETARGETS
    ]
    patches.append(
        (
            equipment_dialogue_runtime_to_file_offset(
                EQUIPMENT_TRANSACTION_TAIL_ADDRESS
            ),
            build_equipment_transaction_tail(),
        )
    )
    return tuple(patches)


def build_door_locked_script() -> bytes:
    """`Door is locked.` as a native town dialogue script.

    Full-width CP932 text, then `0x11` (wait for the acknowledgement button),
    then `0x01` (end of script).  Never a trailing zero: zero is the script
    RETURN command, which pops a stale address and continues into unrelated
    dialogue data - the town_receive v5 lesson.
    """

    encoded = bytearray(_encode_shop_name(DOOR_LOCKED_MESSAGE, max_characters=None))
    encoded[-1] = 0x11
    encoded.append(0x01)
    if DOOR_GATE_SCRIPT_OFFSET + len(encoded) > DOOR_GATE_OFFSET:
        raise ValueError("The door-locked script overruns its reserved span.")
    return bytes(encoded)


def build_monster_door_gate() -> bytes:
    """Interpose on the armed door states' readiness test.

    Called via `jal` from both DOOR_READY_HOOK_ADDRESSES, replacing
    `jal is_player_ready_for_scene_transition`.  Contract at entry: a0 = a1 =
    s1 = the door actor (the vanilla delay slots still run), ra = the armed
    state's resume point, and both callers treat v0 == 0 as "keep resting" -
    with nothing committed yet.

    Behaviour:
      - record != Monster Shop        -> vanilla result, bit-exact.
      - Keycard >= 3                  -> vanilla result (enter normally).
      - ready, Keycard < 3, cooldown 0, no modal open -> publish
        `Door is locked.` to the town dialogue queue, stop the player, start
        the cooldown, return 0.
      - ready, cooldown running / queue busy / modal open -> return 0
        silently (busy and modal cases retry next frame - the mailbox's
        receive message always wins those races, matching the receive
        dispatcher's own defer-while-dialogue-active policy).
      - not ready                     -> tick the cooldown down, return 0.
        (Vanilla would also return 0 here; the tick frames are exactly the
        frames the player is NOT pushing into the door.)

    The publish is the town_receive notification sequence: zero the
    controller text column/row halfwords, then store the script pointer into
    the descriptor's native new-script queue at +0x34.

    The stop is the vanilla collision-talk sequence, byte for byte the same
    calls the walk handler makes when the player bumps into an NPC: face the
    input direction, enter the standing state (which zeroes the motion words
    and installs the standing handler and animation), and clear
    player+0x2C.  The standing state is what every NPC conversation runs
    under, so movement, physics, collision, and post-dialogue recovery are
    all the game's own.  v96's custom no-op handler taught the lesson here:
    it stopped input processing but not physics, so the stored running
    velocity integrated with no collision response and the player phased
    through the building.

    A publish can only happen when the readiness test passed, which itself
    requires the live handler to be one of the two normal movement handlers
    - so the stop sequence always acts on a state it understands.
    """

    b = _MipsBuilder()
    b.emit(
        _i(0x09, 29, 29, -0x18),                       # addiu sp,sp,-0x18
        _i(0x2B, 29, 31, 0x10),                        # sw    ra,0x10(sp)
        _j(0x03, DOOR_READY_TEST_ADDRESS),             # jal   readiness test
        0,                                             #  (a0/a1 already set)
        _i(0x23, 17, 8, 0x48),                         # lw    t0,0x48(s1)
        _i(0x0F, 0, 9, MONSTER_DOOR_RECORD_ADDRESS >> 16),      # lui t1
        _i(0x0D, 9, 9, MONSTER_DOOR_RECORD_ADDRESS & 0xFFFF),   # ori t1
    )
    b.branch(0x05, 8, 9, "ret")                        # bne t0,t1,ret
    b.emit(_i(0x0F, 0, 12, _upper(DOOR_GATE_LATCH_ADDRESS)))    # delay: lui t4
    b.branch(0x04, 2, 0, "not_ready")                  # beq v0,zero,not_ready
    b.emit(0)
    b.emit(
        _i(0x0F, 0, 10, _upper(KEYCARD_LEVEL_ADDRESS)),          # lui t2
        _i(0x23, 10, 10, _lower(KEYCARD_LEVEL_ADDRESS)),         # lw  t2,keycard
        0,
        _i(0x0B, 10, 11, MONSTER_SHOP_KEYCARD_REQUIREMENT),      # sltiu t3,t2,3
    )
    b.branch(0x04, 11, 0, "ret")                       # beq t3,zero,ret
    b.emit(0)
    # Blocked. Say why, at most once per approach.
    b.emit(
        _i(0x23, 12, 13, _lower(DOOR_GATE_LATCH_ADDRESS)),       # lw t5,latch
        _i(0x0F, 0, 14, TOWN_DIALOGUE_DESCRIPTOR_ADDRESS >> 16), # lui t6
    )
    b.branch(0x05, 13, 0, "refuse")                    # bne t5,zero,refuse
    b.emit(0)
    b.emit(
        _i(0x23, 14, 15,                               # lw t7,queue
           (TOWN_DIALOGUE_DESCRIPTOR_ADDRESS & 0xFFFF)
           + TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET),
        0,
    )
    b.branch(0x05, 15, 0, "refuse")                    # bne t7,zero,refuse
    b.emit(0)
    # A modal that is already open belongs to someone else (the mailbox);
    # defer to it, exactly as the receive dispatcher defers while a dialogue
    # is active.  Retry next frame.
    b.emit(
        _i(0x23, 14, 15, TOWN_MODAL_ROOT_ADDRESS & 0xFFFF),      # lw t7,root
        0,
    )
    b.branch(0x05, 15, 0, "refuse")                    # bne t7,zero,refuse
    b.emit(0)
    b.emit(
        _i(0x29, 14, 0,                                # sh zero,column
           (TOWN_DIALOGUE_DESCRIPTOR_ADDRESS & 0xFFFF)
           + TOWN_DIALOGUE_COLUMN_OFFSET),
        _i(0x29, 14, 0,                                # sh zero,row
           (TOWN_DIALOGUE_DESCRIPTOR_ADDRESS & 0xFFFF)
           + TOWN_DIALOGUE_ROW_OFFSET),
    )
    _load_address(b, 24, DOOR_GATE_SCRIPT_ADDRESS)     # t8 = script
    b.emit(
        _i(0x2B, 14, 24,                               # sw t8,queue
           (TOWN_DIALOGUE_DESCRIPTOR_ADDRESS & 0xFFFF)
           + TOWN_DIALOGUE_PENDING_SCRIPT_OFFSET),
        _i(0x09, 0, 25, DOOR_GATE_MESSAGE_COOLDOWN_FRAMES),      # addiu t9
        _i(0x2B, 12, 25, _lower(DOOR_GATE_LATCH_ADDRESS)),       # sw t9,latch
    )
    # Stop the player exactly like a vanilla collision NPC talk.  t6 still
    # holds the 0x8008 base from the publish above.
    b.emit(
        _j(0x03, PLAYER_FACE_INPUT_DIRECTION_ADDRESS),           # jal face
        _i(0x09, 14, 4, PLAYER_STATE_ADDRESS & 0xFFFF),          #  a0=state
        _i(0x0F, 0, 14, PLAYER_STATE_ADDRESS >> 16),             # lui t6 again
        _i(0x09, 14, 4, PLAYER_STATE_ADDRESS & 0xFFFF),          # a0 = state
        _i(0x23, 14, 5, PLAYER_MOTION_POINTER_ADDRESS & 0xFFFF), # lw a1
        _i(0x23, 14, 6, PLAYER_AUX_POINTER_ADDRESS & 0xFFFF),    # lw a2
        _j(0x03, PLAYER_ENTER_STANDING_STATE_ADDRESS),           # jal standing
        0,
        _i(0x0F, 0, 14, PLAYER_STATE_ADDRESS >> 16),             # lui t6 again
        _i(0x2B, 14, 0,                                # sw zero,state+0x2C
           (PLAYER_STATE_ADDRESS + PLAYER_TALK_CLEAR_OFFSET) & 0xFFFF),
    )
    b.label("refuse")
    b.emit(_r(0, 0, 2, 0, 0x21))                       # move v0,zero
    b.label("ret")
    b.emit(
        _i(0x23, 29, 31, 0x10),                        # lw ra,0x10(sp)
        0,                                             # (load delay)
        _r(31, 0, 0, 0, 0x08),                         # jr ra
        _i(0x09, 29, 29, 0x18),                        #  addiu sp,sp,0x18
    )
    b.label("not_ready")
    b.emit(
        _i(0x23, 12, 13, _lower(DOOR_GATE_LATCH_ADDRESS)),       # lw t5,latch
        0,
    )
    b.branch(0x04, 13, 0, "refuse")                    # beq t5,zero,refuse
    b.emit(0)
    b.emit(
        _i(0x09, 13, 13, -1),                          # addiu t5,t5,-1
        _i(0x2B, 12, 13, _lower(DOOR_GATE_LATCH_ADDRESS)),       # sw t5,latch
    )
    b.branch(0x04, 0, 0, "refuse")                     # b refuse
    b.emit(0)
    payload = b.build()
    if DOOR_GATE_OFFSET + len(payload) > DOOR_GATE_END_OFFSET:
        raise ValueError("The door gate overruns its reserved span.")
    return payload


def monster_dialogue_runtime_to_file_offset(address: int) -> int:
    if not (
        MONSTER_DIALOGUE_RUNTIME_ADDRESS
        <= address
        < MONSTER_DIALOGUE_RUNTIME_ADDRESS + 0x2800
    ):
        raise ValueError(
            f"Address 0x{address:08x} is outside the Monster dialogue resource."
        )
    return (
        MONSTER_DIALOGUE_FILE_OFFSET
        + address
        - MONSTER_DIALOGUE_RUNTIME_ADDRESS
    )


def iter_monster_greeting_file_patches() -> tuple[tuple[int, bytes], ...]:
    """Strip the Monster merchant's greeting and the Sell preamble.

    Far less to do here than in Equipment: the Buy row already goes to our own
    AP script, whose cancel is a bare `16 return`, and Sell's cancel is already
    a chain of gotos back to the menu. Kept: `Thanks.  Come again.` on leaving,
    `How about for N.`, the choice rows, and `(Receives N.)`.
    """

    skip = bytes([0x15]) + struct.pack(
        "<I", MONSTER_SHOP_MENU_SCRIPT_ADDRESS
    ) + bytes([0x01])
    patches = [
        (monster_dialogue_runtime_to_file_offset(entry), skip)
        for entry in MONSTER_GREETING_ENTRY_ADDRESSES
    ]
    patches += [
        (
            monster_dialogue_runtime_to_file_offset(address),
            struct.pack("<I", target),
        )
        for address, target, _ in MONSTER_DIALOGUE_RETARGETS
    ]
    # Never substitute the "Your father, Guy..." lockout for the shop greeting.
    patches.append(
        (
            monster_runtime_to_file_offset(MONSTER_GUY_SUBSTITUTION_ADDRESS),
            struct.pack("<I", 0),
        )
    )
    return tuple(patches)


def monster_runtime_to_file_offset(address: int) -> int:
    if not 0x8001_6000 <= address < 0x8002_0000:
        raise ValueError(f"Address 0x{address:08x} is outside the Monster Shop overlay.")
    return MONSTER_OVERLAY_FILE_OFFSET + address - 0x8001_6000


def slus_runtime_to_file_offset(address: int) -> int:
    if address < SLUS_LOAD_ADDRESS:
        raise ValueError(f"Address 0x{address:08x} is below the SLUS load address.")
    return SLUS_HEADER_SIZE + address - SLUS_LOAD_ADDRESS


def mode2_file_offset_to_raw_offset(start_lba: int, file_offset: int) -> int:
    sector, within_sector = divmod(file_offset, 2_048)
    return (start_lba + sector) * 2_352 + 24 + within_sector


def iter_town_shop_hook_file_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        (
            monster_runtime_to_file_offset(MONSTER_BUILDER_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, MONSTER_BUILDER_ADDRESS)),
        ),
        (
            MONSTER_BUY_CHOICE_POINTER_FILE_OFFSET,
            struct.pack("<I", MONSTER_BUY_SCRIPT_ADDRESS),
        ),
        (
            MONSTER_BUY_SCRIPT_FILE_OFFSET,
            _build_monster_buy_script(),
        ),
        (
            equipment_runtime_to_file_offset(EQUIPMENT_BUILDER_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, EQUIPMENT_BUILDER_ADDRESS)),
        ),
        (
            equipment_runtime_to_file_offset(EQUIPMENT_COMMIT_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, COMMIT_PURCHASES_ADDRESS)),
        ),
        (
            town_runtime_to_file_offset(GENERIC_ITEM_NAME_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, ITEM_NAME_ADDRESS)),
        ),
        (
            town_runtime_to_file_offset(GENERIC_DISPLAY_PRICE_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, BUY_PRICE_ADDRESS)),
        ),
        (
            # The retired send price-visibility gate's site, restored to its
            # vanilla `jal 0x800B0F94` (an explicit record so a disc built
            # from an older base patch cannot keep the old gate).
            town_runtime_to_file_offset(GENERIC_PRICE_VISIBILITY_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, VANILLA_PRICE_VISIBILITY_GATE_ADDRESS)),
        ),
        (
            # `j resume / sw tag` becomes `j gate / nop`: the store is
            # lifted into the gate so the delay slot stays inert.
            town_runtime_to_file_offset(CHECKED_TAG_JUMP_ADDRESS),
            struct.pack("<2I", _j(0x02, CHECKED_TAG_GATE_ADDRESS), 0),
        ),
        (
            # The A-press handler's `jal <capacity guard>` is retargeted at
            # the send-aware gate; the delay slot (`addu a0,s0,zero`) stays.
            town_runtime_to_file_offset(CHECK_CAPACITY_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, CHECK_CAPACITY_GATE_ADDRESS)),
        ),
        (
            town_runtime_to_file_offset(GENERIC_TOTAL_PRICE_HOOK_ADDRESS),
            struct.pack("<I", _j(0x03, BUY_PRICE_ADDRESS)),
        ),
        (
            town_runtime_to_file_offset(TOWN_BUY_PRICE_POINTER_ADDRESS),
            struct.pack("<I", BUY_PRICE_ADDRESS),
        ),
        (
            town_runtime_to_file_offset(TOWN_MENU_CONSTRUCTOR_POINTER_ADDRESS),
            struct.pack("<I", MENU_CONSTRUCTOR_ADDRESS),
        ),
        *iter_equipment_greeting_file_patches(),
        *iter_monster_greeting_file_patches(),
        # The Monster Shop door gate: interpose on the readiness jal in both
        # armed door states.  See build_monster_door_gate for why this seam,
        # unlike v88-v93's, fails benignly if the premise is ever wrong.
        *(
            (
                town_runtime_to_file_offset(address),
                struct.pack("<I", _j(0x03, DOOR_GATE_ADDRESS)),
            )
            for address in DOOR_READY_HOOK_ADDRESSES
        ),
    )


def iter_town_shop_file_patches(payload: bytes) -> tuple[tuple[int, bytes], ...]:
    if len(payload) != SHOP_CORE_SIZE:
        raise ValueError(f"Town shop payload must be exactly {SHOP_CORE_SIZE} bytes.")
    return ((SHOP_CORE_FILE_OFFSET, payload), *iter_town_shop_hook_file_patches())


def iter_town_shop_resident_file_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        (
            slus_runtime_to_file_offset(
                UNFAMILIAR_ITEM_DESCRIPTION_POINTER_ADDRESS
            ),
            struct.pack("<I", RESIDENT_UNFAMILIAR_ITEM_DESCRIPTION_ADDRESS),
        ),
        (
            slus_runtime_to_file_offset(RESIDENT_DESCRIPTION_SLOT_ADDRESS),
            _build_resident_description_slot(),
        ),
        *(
            (
                slus_runtime_to_file_offset(address),
                struct.pack("<I", _j(0x03, RESIDENT_DESCRIPTION_GATE_ADDRESS)),
            )
            for address in SELECTED_DESCRIPTION_HOOK_ADDRESSES
        ),
    )


def iter_town_shop_raw_patches(payload: bytes) -> tuple[tuple[int, bytes], ...]:
    result: list[tuple[int, bytes]] = []
    for file_offset, data in iter_town_shop_file_patches(payload):
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            within_sector = current % 2_048
            length = min(len(data) - copied, 2_048 - within_sector)
            raw_offset = mode2_file_offset_to_raw_offset(TOWN_FILE_START_LBA, current)
            result.append((raw_offset, data[copied : copied + length]))
            copied += length
    for file_offset, data in iter_town_shop_resident_file_patches():
        raw_offset = mode2_file_offset_to_raw_offset(
            SLUS_FILE_START_LBA,
            file_offset,
        )
        result.append((raw_offset, data))
    return tuple(result)


def iter_town_shop_hook_raw_patches() -> tuple[tuple[int, bytes], ...]:
    result: list[tuple[int, bytes]] = []
    for file_offset, data in iter_town_shop_hook_file_patches():
        raw_offset = mode2_file_offset_to_raw_offset(TOWN_FILE_START_LBA, file_offset)
        result.append((raw_offset, data))
    for file_offset, data in iter_town_shop_resident_file_patches():
        raw_offset = mode2_file_offset_to_raw_offset(
            SLUS_FILE_START_LBA,
            file_offset,
        )
        result.append((raw_offset, data))
    return tuple(result)


def append_town_shop_ppf_records(ppf: bytearray, payload: bytes) -> None:
    for raw_offset, data in iter_town_shop_raw_patches(payload):
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)


def append_town_shop_hook_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_town_shop_hook_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
