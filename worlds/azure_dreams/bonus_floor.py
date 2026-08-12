"""The go-up bonus floor.

A go-up trap throws Koh into a small custom floor holding three items of one
category. Taking one collapses the floor and drops him back on the SAME floor
number, freshly generated - so a go-up trap can no longer skip a locked
elevator, and the player gets a minigame plus a second pass at the floor
instead. `docs/bonus-floor-implementation.md` is the ground truth for the
mechanism; this module is only the delivery.

Three homes, and every one of them has to agree about where the state lives:

* **SLUS resident** - the item-name guard, extended to veil our loot.
* **Gameplay payload** - the AP floor-location spawner is suppressed on the
  bonus floor so multiworld checks never spawn there.
* **DUNGEON.BIN overlay** - the forced-floor hook, the per-frame executor, the
  earthquake trampoline, and the go-up trap's arming stub.

The runtime code itself is NOT in any of those. It is a 500-byte block loaded
from its own sector into `0x801D9EF0` (carve retraction 2026-08-01; it used
to sit at 0x801FEDF0 in carved space above memory-top) that a
full-tower RAM watch proved the game never writes (see
`docs/adap-memory-safe-regions.md`). The seed page only carries an 84-byte stub
that issues that read once per boot - it deliberately does not live in the
page's pooled message region, which real 2P seeds fill to 60-80%.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from random import Random

# The loader stub lands in the seed page's one structurally fixed gap: the
# appender trampoline sits at +0x140 and the locked-elevator message at +0x1B0,
# so +0x148..+0x1B0 cannot move with seed content.
_PPF_HEADER_SIZE = 56

READ_STUB_OFFSET = 0x148
READ_STUB_SPAN = 104

# The bonus-floor elevator (2026-08-08, v2). The bonus room's elevator must
# never advance the floor counter - a go-up trap is not a keycard bypass -
# and the ride's native visual rises, which reads wrong for a floor that only
# goes back down.  So the seed page's elevator gate handler jumps here on its
# clearance-gate path, and the routine (CODE_SECTOR +0x240) branches on
# bonus-active `0x801DA101`:
#
# * clear - restore `addiu v0,v0,1` at the elevator commit's two floor
#   increments and jump back into the handler's gate body: a completely
#   normal, keycard-gated ascent.
# * set - answering Yes never rides at all.  The routine patches the
#   increments to +0 anyway (belt and suspenders should a ride ever start
#   while bonus-active), then ARMS THE EARTHQUAKE COLLAPSE - the executor's
#   own arm block (0x800C6078) minus the loot unveil, guarded on quake-active
#   so a running collapse is not re-armed - and exits through the elevator
#   dispatcher's cancelled-action cleanup (0x800913DC), the same contract the
#   locked-elevator path uses.  Koh stands on the pad, the floor collapses,
#   and he falls with it back onto floor N: the ride DOWN, in the same
#   presentation the loot exit already ships.
#
# Self-healing on every elevator use, RAM-only - the commit code is reachable
# on disc but patching at ride time needs no disc edit and no
# dual-overlay-copy bookkeeping.  patch.py asserts the baked gate-body return
# address still matches the handler it builds.
ELEVATOR_SAME_FLOOR_POKE_ADDRESS = 0x801D_A130
ELEVATOR_GATE_BODY_ADDRESS = 0x801D_8388
# `addiu v0,v0,1` at both elevator-commit branches (mode 1 / mode 0),
# decompiled 2026-08-08 from the tower overlay ride state machine.
ELEVATOR_FLOOR_INCREMENT_ADDRESSES = (0x8009_329C, 0x8009_32B8)

# The locked-elevator orb (2026-08-08, cosmetic). The pad's colour-cycling
# centre is a CLUT animation: a per-frame object (callback 0x800B1E80,
# dungeon overlay) re-uploads one of TWELVE 16-colour palettes - a hue
# rotation at 0x800DF068, 32 bytes each, frame 0 = RED - to the orb CLUT at
# VRAM (0,461) (clut id 0x7340) every 4 frames via 0x8003F80C.  The guard at
# CODE_SECTOR +0x2D0 interposes on that upload: at a clearance ceiling
# (floor % 5 == 4) with the next keycard missing - the exact test the
# elevator gate handler uses - it pins the source to the red frame, so the
# orb sits red while the elevator is locked and resumes cycling within four
# frames of the keycard arriving.  The bonus floor is exempt (its pad always
# works).  One word on disc retargets the animator's jal (0x800B1EE8, raw
# 0x1C87640, verified uncompressed against the original image).
ORB_LOCK_GUARD_ADDRESS = 0x801D_A1C0
ORB_ANIMATOR_JAL_ADDRESS = 0x800B_1EE8
ORB_PALETTE_BANK_ADDRESS = 0x800D_F068

# Loot sectors. 10 outcomes x 4 arrangements, rolled per seed.
VARIANT_LBA = 0x1EF45
VARIANT_COUNT = 40
ITEM_ROW_OFFSETS = (0x2C, 0x32, 0x38)

# Equipment carries status 0x81 - the 0x80 keeps the +N quality out of the
# veiled name, which would otherwise leak the tier of a gamble set.
EQUIPMENT_STATUS = 0x81
PLAIN_STATUS = 0x01

FIXED_SETS = (
    ((2, 4, 0), (2, 3, 0), (2, 7, 0)),        # fruits: Leva / Tumna / Roche
    ((15, 1, 0), (15, 1, 5), (15, 1, 20)),    # gold gamble
    ((15, 5, 5), (15, 6, 5), (15, 7, 5)),     # sword elements
    ((15, 14, 0), (15, 15, 0), (15, 13, 0)),  # troll weapons
    ((16, 1, 0), (16, 3, 0), (16, 2, 10)),    # wands: wooden / life / trained
    ((16, 7, 0), (16, 6, 0), (16, 8, 0)),     # wand elements
    ((17, 8, 0), (17, 9, 0), (17, 10, 0)),    # shield elements
    ((17, 1, 0), (17, 2, 0), (17, 3, 0)),     # wood / leather / mirror
    ((17, 7, 0), (17, 7, 6), (17, 7, 12)),    # diamond gamble
)
# Eggs: one evolved plus two of anything else. Ultimate (1) and Kewne (2) are
# excluded by construction rather than by filtering.
EVOLVED_EGGS = tuple(range(3, 20, 2))
OTHER_EGGS = tuple(range(4, 19, 2)) + tuple(range(20, 46))
EGG_CATEGORY = 18
PERMUTATIONS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


# --- the room template, and every patch site --------------------------------

# The room, anchored so the arrival chamber's centre is the universal
# climb-arrival tile (31,57) - that tile is fixed for this floor class and is
# NOT derived from any elevator or waypoint record, so the shape is built
# around it rather than the other way round.
#
#       x25..37    3D/3E floor  I item  K Koh  E elevator  64..69 fence  . void
#   y47  .  .  .  . 64 68 68 68 65  .  .  .  .
#   y48  .  .  .  . 66 3E 3D 3E 66  .  .  .  .
#   y49  .  .  .  . 66 3D  E 3D 66  .  .  .  .
#   y50  .  .  .  . 66 3E 3D 3E 66  .  .  .  .
#   y51 64 68 68 68 69 3D 3E 3D 67 68 68 68 65
#   y52 66 3E 3D 3E 3D 3E 3D 3E 3D 3E 3D 3E 66
#   y53 66 3D  I 3D 66 3D  I 3D 66 3D  I 3D 66
#   y54 66 3E 3D 3E 3D 3E 3D 3E 3D 3E 3D 3E 66
#   y55 67 68 68 68 65 3D 3E 3D 64 68 68 68 69
#   y56  .  .  .  . 66 3E 3D 3E 66  .  .  .  .
#   y57  .  .  .  . 66 3D  K 3D 66  .  .  .  .
#   y58  .  .  .  . 66 3E 3D 3E 66  .  .  .  .
#   y59  .  .  .  . 67 68 68 68 69  .  .  .  .
#
# 55 walkable tiles (the elevator pad included), 50 fence.  Three item alcoves
# off one open floor; the centre opens south into Koh's arrival chamber and
# north into its mirror image, a matching chamber with the elevator centred in
# it at (31,49).  The corner pieces are named for the directions the fence run
# leaves by, so the vertical mirror maps 0x65 (west+south) to 0x69
# (west+north) and 0x64 (east+south) to 0x67 (east+north).
#
# **The elevator pad is BAKED into the grid**: appearance 1, one 32-step below
# the floor, status 0, exactly as every stock layout authors it.  The
# predefined-floor parser does stamp appearance 1 onto a runtime-type-2
# elevator's tile (0x800186B4, the derandomizer's `predefElevatorAppear`
# site), but the RLE grid decode runs AFTER the elevator list and overwrites
# the stamp, so only the authored grid tile ever displays.  The debug floor
# omits the bake - that is the whole "technical reason" ProGrammar's survival
# floor shows no elevator.  The elevator record's sector type stays 0; the
# parser adds 2, and runtime type 2 is the normal up elevator.
#
# **Only the item row is divided.**  The dividers are lone posts at (29,53) and
# (33,53); the cells above them are floor.  That is deliberate - a divider that
# reached the top run would make (29,51) a T-junction, and there is no T piece:
# across all six stock layouts only eight fence cells have three fence
# neighbours and they just reuse 0x66/0x67/0x69.  Dangling ends are normal
# though (88 of them in stock), which is what these posts are.
#
# Reaching an item still costs a step off the item row each way.  Items 4 apart
# was not enough on its own: with the run button and the collapse timer, a
# clear run along the item row was just short enough to sweep two.
#
# **The fence is why it does not look paper-thin.**  0x3D/0x3E are *interior*
# floor: they draw a flat top and nothing else, because vanilla only ever uses
# them enclosed - across three stock variants, 1294 of 1296 sit inside a fence
# and 2 touch bare void.  The pre-fence room was 39 of 39 touching bare void, so
# nothing drew a side face anywhere.  0x64..0x69 draw the 3D edge, one 64-step
# above the floor, carrying status 0x8000 (every fence tile in every stock
# layout has it).  That low +64 step is what makes floor 1 read as fencing
# rather than as the tall walls a procedural floor gets.
#
# **Pieces are chosen by which way the FENCE run continues, never by which
# floor a cell happens to touch.**  0x64/0x65/0x67/0x69 are corners named for
# the pair of directions the run leaves by - east+south, west+south, east+north,
# west+north - so the inner hooks at (29,55) and (33,55), where the run turns
# down into Koh's chamber, are 0x65 and 0x64 rather than straight 0x68.
#
# The checkerboard parity is flipped from the obvious one so the beige half
# meets the fence bases, which are beige.
#
# This is floor 2's own vocabulary, so it needs no theme change and no
# layout-id change.
#
# **One room record, four waypoints, one elevator.**  ITEM_ROW_OFFSETS depends
# on it: the per-seed loot splice writes the three item rows at 0x2C/0x32/0x38,
# which is only where they land if the records above them keep these counts.
BASE_ROOM = bytes.fromhex(
    "060006001a0030000b000b001a340000243400001e3a0000203a00000000000000000000"
    "00001f31000000001b35030201001f350702010023350402010063000000000000006300"
    "0000000000006300000000000000aa000010ff0010ff0010ffaa0010ff0010ff0010ff00"
    "10ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff00"
    "10ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff00"
    "10ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa00"
    "10ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010"
    "ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010"
    "ff0010ffaa0010ff0010ff0010ff0010ffaa0010ff0010ff0010ff0010ff3000102c6400"
    "8005e48068006f654b0611e0ff001061661683583e00400c3d5f0600c401841511e0ff18"
    "006717a40100aa2018671800ff3000ff945e80e7693009186a67aa6180ff2e806600c02f"
    "3180ffaa5e8043156a01803514e0ffaa3000ff0010b15d001d16e011aa63001d14e0ff90"
    "00ff6180e7aa11e0ff3000ff6180f95e80ffaa0010ff0010ff0010ff0010ff0a0010ff00"
    "10b900000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

EDITS = (
    # slus_name_guard: slus 0x8004EBD0 (40 B)
    (0x35128, bytes.fromhex(
        "000020ae2130e0031d80013c007f218c5344023c414442341cf0221400000000f8670708"
        "00000000"
    )),
    # stub_v3: dungeon 0x800C5FB0 (56 B)
    #
    # Wraps the floor-construction dispatcher `0x80018E80`, which returns a
    # predefined-layout id per floor.  Floor 2 is the Ghosh/Selfi event floor,
    # so its id is rewritten to 0 ("no predefined layout") and the floor builds
    # procedurally like floors 3-39.
    #
    # **Rewriting the id is not enough.** The dispatcher's floor-2 case at
    # `0x80018FCC` does two things before returning:
    #
    #     addiu s3,zero,0x0002      ; the id we rewrite
    #     lw    v1,0x296c(v0)       ; [0x800E296C]
    #     or    v1,v1,0x10000000    ; and this, which we used to leave armed
    #     sw    v1,0x296c(v0)
    #
    # Bit 0x10000000 is the event-floor flag, and the monster machinery tests
    # it - `0x800A08B0` inside the monster-leveling routine at `0x800A08A0`,
    # and `0x8001CFC8` in the floor-generation overlay.  Left set, the floor
    # builds procedurally but spawns nothing and refuses abduct: every
    # monster-related system is gated off.  That is the same trap the id-9
    # tutorial branch documents in `docs/reverse-engineering-notes.md` -
    # "swapping the id does NOT bypass the tutorial entry sequence".
    #
    # The first-entry/subsequent-entry split players report is the case's own
    # `[0x8001022C] == 1` guard: on the first visit that branch skips both the
    # id and the flag, so monsters spawn; afterwards it takes the arming path.
    #
    # So a rewritten id now also calls the resident helper at `0x801DA0C4`,
    # which clears the flag and returns 0 in v0.  The helper lives in the
    # resident sector because clearing a bit costs six instructions and only
    # four words are free here.
    (0x1C9E7B8, bytes.fromhex(
        "a063000c0000000002000a2403004a14000000003168070c00000000d667070c21204000"
        "0863000800000000000000000000000000000000"
    )),
    # executor: dungeon 0x800C5FE8 (232 B)
    (0x1C9E7F0, bytes.fromhex(
        "1e80083c04a11fadf925020c000000001e80083c02a109910ea10a950a00201100804a31"
        "0500401500000000da82030c000000001a1803080000000002a100a11a18030800000000"
        "01a10991000000000b002011000000000e800a3c48354a2581000e2403004ea107004ea1"
        "0b004ea1c067070c0000000005004014000000001e80083c04a11f8d0800e00300000000"
        "03004d91000000007e00ad3103004da11e80083c01a100a110a10b2501000d2400006da5"
        "04006da5ffff0e2406006ea512006da5feff60a502a10da1200004247b06010c08000524"
        "b895020c180804241a18030800000000"
    )),
    # quake_trampoline: dungeon 0x800E0B68 (80 B)
    (0x1CBD260, bytes.fromhex(
        "c8ffbd273400bfaf3000beaf2c00b7af2800b6af2400b5af2000b4af1c00b3af1800b2af"
        "1400b1af1000b0af0880133c60317326dc01758edc017e261e80163c10a1d6260000c386"
        "c919030800000000"
    )),
    # throw_deltas: dungeon 0x800CBAD0 (8 B)
    (0x1CA4FE8, bytes.fromhex(
        "0000422400006324"
    )),
    # goup_call: dungeon 0x800CBD3C (4 B)
    (0x1CA5254, bytes.fromhex(
        "582f030c"
    )),
    # goup_seal: dungeon 0x800CBD58 (4 B)
    (0x1CA5270, bytes.fromhex(
        "10000010"
    )),
    # goup_stub: dungeon 0x800CBD60 (32 B)
    (0x1CA5278, bytes.fromhex(
        "0880083ca814088d010009240200e8141e80083c00a109a1e62e030800000000"
    )),
    # frame_hook: dungeon 0x80089558 (4 B)
    (0x1C58C80, bytes.fromhex(
        "fa17030c"
    )),
    # lift_call: dungeon 0x8009B708 (4 B)
    (0x1C6D8F0, bytes.fromhex(
        "1e68070c"
    )),
    # orb_lock: dungeon 0x800B1EE8 (4 B) - the elevator-orb CLUT animator's
    # `jal 0x8003F80C` retargeted through the locked-orb guard at
    # ORB_LOCK_GUARD_ADDRESS (see the constant's comment block).
    (0x1C87640, bytes.fromhex(
        "7068070c"
    )),
    # payload_wrapper: RETIRED 2026-08-09. This record rewrote the wrapper's
    # `jal spawn_floor_locations` to the spawner-suppression gate at
    # 0x801D9B54 - and because `_append` edits overlapping records in place,
    # it silently swallowed the floor-page loader's interposition at the same
    # word (Rebuild-AdapGameplayPayload.py) and pickups on floors 2+ lost
    # their text. The chain is now wrapper -> page loader -> gate -> spawner:
    # the base patch owns the wrapper word (jal FLOOR_PAGE_LOADER), and the
    # loader calls the gate below, which still suppresses on bonus-active.
    # patch.py asserts at import that no EDITS record covers the wrapper word.
    # payload_loader: payload 0x801FEA4C (4 B)
    (0x1CC1E44, bytes.fromhex(
        "12600708"
    )),
    # payload_spawner: payload 0x801FEA54 (28 B)
    (0x1CC1E4C, bytes.fromhex(
        "1e80083c01a10991000000000fff2011000000000800e00300000000"
    )),
)
DISPATCHER_CALL_SITES = (0x1EA6F60, 0x2180CB0)
DISPATCHER_CALL_WORD  = 0x0C0317EC
CODE_LBA = 0x1EF6D
# Loads at 0x801D9EF0 in the resident block. Routines: +0x68 (0x801D9F58) is
# the bonus-floor arm/variant-roll the stub tail-calls; +0x1D4 (0x801DA0C4) is
# the event-floor flag clear stub_v3 calls on a rewritten floor-2 id. Content
# ends at +0x1B8, so +0x1D4 onward was free sector padding.
CODE_SECTOR = bytes.fromhex(
        "8274828e828b828e828f8297828e000008800b3ca8146b8d000000000200601101800a3c"
        "f00060ad48024a2550004c2503004d9101004e910100ad310500a01113000f240300cf11"
        "000000000800e0030100022404004a25f5ff4c15000000000800e003211000001e80083c"
        "00a1099108800b3c060020150000000001a100a1b13f0a246c0e6aad1768070821108000"
        "faff80140000000000a100a101000a2401a10aa142240c3c0c800d3c3c6aacade8ffbd27"
        "1000bfaf4c9b020c000000001000bf8f1800bd2728000e241b004e001070000001000a3c"
        "45ef4a3521504e0108800b3c6c0e6aad0800e00302000224db3a010c000000001a004014"
        "000000001e80083c01a109910e800a3c1300201148354a2523588a0000016b2d0f006011"
        "6000ac8f09800d3c9c51ad350b008d1109800d3c9c53ad3508008d11000000001e80023c"
        "f09e42241800bf8f1400b18f1000b08f0800e0032000bd27172b0108000000009b670708"
        "0000000000a100a102000a2402004a1400000000211000000800e00300000000e8ffbd27"
        "1000bfaf399c020c000000001000bf8f1e80083c01a109911800bd270200201100000000"
        "ffff02240800e00300000000000000000000000000000000000000000000000000000000"
        "0e800b3c6c296c8dffef0d3cffffad3524608d016c296cad0800e0032110000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "1e80083c01a1099142240a3c0600201509800b3c01004a359c326aadb8326aade2600708"
        "000000009c326aadb8326aad02a10c9100000000100080150000000001a100a110a10b25"
        "01000d2400006da504006da5ffff0e2406006ea512006da5feff60a502a10da120000424"
        "7b06010c08000524b895020c18080424f744020800000000000000000000000000000000"
        "1e80083c01a1099108800a3c0e0020156c144b9505000a241b006a011060000004000a24"
        "08008a151260000058a50a9101008c252b504c0103004011000000000e80043c68f08424"
        "03fe00080000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000"
    )
READ_STUB_OFFSET = 0x148
READ_STUB = bytes.fromhex(
        "1e80083cf09e0991000000001400201500000000e0ffbd27010004241e80053cf09ea524"
        "1000a6270100073c6defe734b5fd000c00000000060004241000a5273ff9000c00000000"
        "c8fc000c00000000440009242800000c000000002000bd27c065070800000000"
    )



def build_variant_sectors(rng: "Random") -> list[bytes]:
    """The seed's forty rooms: ten loot outcomes, four arrangements each."""

    coords = [(BASE_ROOM[o], BASE_ROOM[o + 1]) for o in ITEM_ROW_OFFSETS]
    sectors: list[bytes] = []
    for outcome in range(10):
        for _ in range(4):
            if outcome < len(FIXED_SETS):
                items = FIXED_SETS[outcome]
            else:
                items = tuple(
                    (EGG_CATEGORY, egg, 0)
                    for egg in [rng.choice(EVOLVED_EGGS)] + rng.sample(OTHER_EGGS, 2)
                )
            room = bytearray(BASE_ROOM)
            for row, index in enumerate(PERMUTATIONS[rng.randrange(6)]):
                category, item, quality = items[index]
                x, y = coords[row]
                status = EQUIPMENT_STATUS if category in (15, 16, 17) else PLAIN_STATUS
                room[ITEM_ROW_OFFSETS[row] : ITEM_ROW_OFFSETS[row] + 6] = bytes(
                    (x, y, item, category, status, quality)
                )
            sectors.append(bytes(room))
    return sectors


def install_read_stub(seed_block: bytearray) -> None:
    """Plant the loader stub in the seed page, before the page is written."""

    if len(READ_STUB) > READ_STUB_SPAN:
        raise ValueError(
            f"The bonus-floor loader stub is {len(READ_STUB)} bytes and only "
            f"{READ_STUB_SPAN} are reserved at seed-page +0x{READ_STUB_OFFSET:X}."
        )
    end = READ_STUB_OFFSET + READ_STUB_SPAN
    if any(seed_block[READ_STUB_OFFSET:end]):
        raise ValueError(
            "The seed page's +0x148 gap is not empty; the bonus-floor loader "
            "stub would overwrite generated content."
        )
    seed_block[READ_STUB_OFFSET : READ_STUB_OFFSET + len(READ_STUB)] = READ_STUB


def append_bonus_floor_ppf_records(ppf: bytearray, rng: "Random") -> None:
    """Every bonus-floor byte outside the seed page."""

    from . import patch

    for raw_offset, payload in EDITS:
        _append(ppf, raw_offset, payload)
    redirect = struct.pack("<I", DISPATCHER_CALL_WORD)
    for site in DISPATCHER_CALL_SITES:
        # The tower overlay is on the disc twice and the floor build can use
        # either copy; hooking one of them fails intermittently.
        _append(ppf, site, redirect)

    patch.append_mode2_form1_sector_ppf_records(ppf, CODE_LBA, CODE_SECTOR)
    for index, user_data in enumerate(build_variant_sectors(rng)):
        patch.append_mode2_form1_sector_ppf_records(
            ppf, VARIANT_LBA + index, user_data
        )


def _append(ppf: bytearray, raw_offset: int, payload: bytes) -> None:
    """Write bytes into a ppf, editing existing records where they overlap.

    Three of our edits land inside the gameplay payload, which the base patch
    already writes as whole 255-byte records. Appending a second record for the
    same offset leaves the file self-contradictory - the appliers take the last
    writer, but verification (and anyone reading the patch) sees a conflict. So
    overlapping bytes are edited in place and only genuinely new ground is
    appended.
    """

    covered = bytearray(len(payload))
    cursor = _PPF_HEADER_SIZE
    while cursor < len(ppf):
        offset, length = struct.unpack_from("<IB", ppf, cursor)
        body = cursor + 5
        low, high = max(offset, raw_offset), min(offset + length, raw_offset + len(payload))
        if low < high:
            ppf[body + low - offset : body + high - offset] = payload[
                low - raw_offset : high - raw_offset
            ]
            for index in range(low - raw_offset, high - raw_offset):
                covered[index] = 1
        cursor = body + length

    index = 0
    while index < len(payload):
        if covered[index]:
            index += 1
            continue
        end = index
        while end < len(payload) and not covered[end]:
            end += 1
        run, written = payload[index:end], 0
        while written < len(run):
            chunk = run[written : written + 255]
            ppf.extend(struct.pack("<IB", raw_offset + index + written, len(chunk)))
            ppf.extend(chunk)
            written += len(chunk)
        index = end
