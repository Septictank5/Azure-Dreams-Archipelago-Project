"""The town status strip: `WEAPON:+20  SHIELD:+40  BALL:+3` in the game's own
banner frame along the top of the screen, everywhere in town - what the three
sand levels currently BUY at the blacksmith and the ball charger.

**It prints the benefit, not the level.** A player who reads `WEAPON: 2` still
has to remember what a second Red Sand was worth; `WEAPON:+20` is the number
they are about to see in the smith's menu. So the gear groups show the temper
ceiling (`blacksmith.CAP_BY_LEVEL`, +0/+10/+20/+40) and the ball group shows
the charges the charger hands out per town visit
(`ball_charger.USES_BY_LEVEL`, +0/+1/+2/+3). The client's tracker keeps
counting sands collected - that is the collection view, this is the shop view,
and they answer different questions.

Account: `docs/systems/fortune-teller.md` section 6. Grew out of an
experiment in the fortune teller's building (m3: bare 8x16 text; m4: the
banner frame + 8x8 caps; m5: shortened clear of the inventory's MONEY box -
all ridden, all worked); m6 (2026-08-17) moves it out of her dialogue image
into the **town-wide free canvas** and spawns it on every town scene.

**How the town draws text outside a window** (decoded 2026-08-17): the
place-name banner (`Town::800BC45C`) is three pool actors - a screen-space
text actor (`+0x10` per-frame callback; transform = screen X/Y + an
ordering-table index; sprite object → a chain of the game's 12-byte glyph
records; registered as a 2D render root with `0x80033CD8(body, 0x80044BB0)`),
the same again for the FRAME (a resident eight-record chain at `0x800782EC`,
tpage `0x17`, CLUT `0x7CC2`), and a translucent fill box (no updater,
registered under the resident tile class `0x80053A88`, which draws
`actor+0x2C/+0x2E/+0x30/+0x32` = x/y/w/h in `actor+0x28` rgb at OT 1). This
module builds the same three, persistent, with the frame widened to 256 px
and shortened to 26 out of the banner art's own pieces, and the text from
the 8x8 UI font (`0x8004E298`, raw ASCII, UPPER CASE only - the atlas maps the
lower-case rows to garbage, measured) at VRAM (960,0), CLUT row 498. Fonts
and banner art are resident VRAM pages (present in every town state read).

**Home**: `0x800FE600..0x801006C0`, the 8,384-byte FF-filled canvas in the
town mode image (`TOWN.BIN+0x80EA0`, `docs/game/memory-map.md`: play-verified
free, disc-deployed like the slab, keep a 4-byte nop guard at the head).
Resident for the whole of town mode; reloaded with the town image on every
town entry; not present in the tower (whose mode init also rebuilds the
actor pool, so nothing of ours outlives a mode switch).

**Spawn, every scene**: `initialize_town_struct_and_run_scene_program`
(`Town::8009D5C0`) runs for every town scene load and ends with `jal
0x80034f88(ctx, entry script)`; that one `jal` is retargeted at a trampoline
here which calls `spawn` (respawn only if the text actor is not still alive -
the mode init zeroes the whole pool, so a live check on `+0x1E`/`+0x10` is
sound) and then tail-jumps to `0x80034f88` with the arguments intact.

**Out of the way of menus** (2026-08-17, after the m6 ride showed the strip
drawn over the top of the equipment shop's buy list): the text actor's
per-frame `update` hides the whole strip while any town window is open and
shows it again when the last one closes. The signal is the **window-list
head `0x80082BC0`** (`town_shop.TOWN_MODAL_ROOT_ADDRESS`, the client's
"modal open" guard, trace-verified: every window creator - dialogue text,
shop menus, the inventory - inserts its object at that head through
`0x8003FF2C`, and it returns to 0 when the last one closes). Hiding is
per-object: the 2D chain drawer `0x80044BB0` skips a sprite whose
`spr+0x14 & 0x80` is set (text and frame); the tile class has no such bit,
so the fill box's WIDTH is set to 0 (a zero-width TILE draws nothing) and
restored. NOT the actor's `+0x1E & 0x800` draw-suspend bit: the draw pass
`0x800402F4` tests it on the HEAD of each class chain only, so setting it on
one of ours would silence every 2D root or tile in town along with it.
"""

from __future__ import annotations

import struct

from . import ball_charger, blacksmith, patch, save_removal, town_shop
from .patch import _MipsBuilder, _i, _j, _r

TOWN_STATUS_STRIP = True   # the switch; rides with the temper system

# --- resident routines (SLUS) ---------------------------------------------------
ALLOCATE_ACTOR_ADDRESS = 0x8003_FC64          # (flags) -> actor, or 0
REGISTER_RENDER_ROOT_ADDRESS = 0x8003_3CD8    # (body, class fn) - only if not yet registered
REGISTER_ACTOR_ROOT_ADDRESS = 0x8004_491C     # (actor, class fn)
RENDER_2D_CLASS_ADDRESS = 0x8004_4BB0         # the screen-space sprite-chain drawer
TILE_CLASS_ADDRESS = 0x8005_3A88              # the banner's translucent fill box drawer
BUILD_ASCII_CHAIN_ADDRESS = 0x8004_E298       # (dest, ascii string, mode) -> dest or 0
RUN_SCRIPT_ADDRESS = 0x8003_4F88              # what the scene runner's tail calls
ACTOR_FLAGS = 0x136                           # town's standard: obj08 = actor+0xDC, obj0C = actor+0xF4
BOX_ACTOR_FLAGS = 0x1                         # what the banner allocates its box with

# --- hiding under menus ------------------------------------------------------------
HIDE_WHILE_WINDOW_OPEN = True                 # drop the strip while any town window / menu is up
WINDOW_LIST_HEAD_ADDRESS = town_shop.TOWN_MODAL_ROOT_ADDRESS   # 0x80082BC0: nonzero = a window is open
SPRITE_HIDE_FLAG = 0x80                       # spr+0x14: the 2D chain drawer skips the sprite
SPRITE_FLAGS_OFFSET = 0x14
ACTOR_SPR_POINTER_OFFSET = 0xC                # actor+0xC -> its sprite object (obj0C)
BOX_WIDTH_OFFSET = 0x30                       # tile actor +0x30 = width; 0 draws nothing

# --- the town-wide home and the one town-image word --------------------------------
CANVAS_ADDRESS = 0x800F_E600
CANVAS_END = 0x8010_06C0
CANVAS_GUARD = 4                              # the nop guard at the head, per memory-map.md
SCENE_RUNNER_TAIL_JAL_ADDRESS = 0x8009_D718   # `jal 0x80034f88` in Town::8009D5C0's tail
SCENE_RUNNER_TAIL_JAL_ORIGINAL = 0x0C00_D3E2  # jal 0x80034f88

# --- what it shows ---------------------------------------------------------------
WEAPON_LEVEL_ADDRESS = blacksmith.WEAPON_LEVEL_ADDRESS
SHIELD_LEVEL_ADDRESS = blacksmith.SHIELD_LEVEL_ADDRESS
BALL_LEVEL_ADDRESS = patch.BALL_CHARGE_LEVEL_ADDRESS
assert SHIELD_LEVEL_ADDRESS == WEAPON_LEVEL_ADDRESS + 1
MAX_LEVEL = blacksmith.MAX_LEVEL               # 3; the ball charger's is the same
assert ball_charger.MAX_LEVEL == MAX_LEVEL

# The text: raw ASCII for the 8x8 builder, one glyph = 8 px, a space = 8 px,
# `0x0C <byte>` advances byte-0x30 px. Three groups spread across the frame.
#
# Each group is a fixed-width label followed by a fixed-width VALUE SLOT that
# rebuild() overwrites from the tables below. Fixed width is what keeps the
# strip from moving as levels rise: a one-digit value pads with a trailing
# space, which advances 8 px and draws nothing, so `+0` and `+40` occupy the
# same cells and the group after it never shifts.
GROUP_TEXTS = ("WEAPON:", "SHIELD:", "BALL:")
GROUP_GAP = 8                                  # px between groups
# The gear groups print the smith's ceiling, the ball group the charger's
# per-visit allowance. Two digits versus one, hence two slot widths.
GEAR_VALUE_WIDTH, BALL_VALUE_WIDTH = 3, 2


def _value_texts(values: tuple[int, ...], width: int) -> tuple[str, ...]:
    texts = tuple(f"+{value}".ljust(width) for value in values)
    assert all(len(text) == width for text in texts), (values, width)
    return texts


GEAR_VALUE_TEXTS = _value_texts(blacksmith.CAP_BY_LEVEL, GEAR_VALUE_WIDTH)
BALL_VALUE_TEXTS = _value_texts(ball_charger.USES_BY_LEVEL, BALL_VALUE_WIDTH)
GROUP_VALUE_TEXTS = (GEAR_VALUE_TEXTS, GEAR_VALUE_TEXTS, BALL_VALUE_TEXTS)
GROUP_VALUE_WIDTHS = tuple(len(texts[0]) for texts in GROUP_VALUE_TEXTS)
assert all(len(texts) == MAX_LEVEL + 1 for texts in GROUP_VALUE_TEXTS)
TEXT_WIDTH = (
    sum(len(label) + width for label, width in zip(GROUP_TEXTS, GROUP_VALUE_WIDTHS)) * 8
    + GROUP_GAP * (len(GROUP_TEXTS) - 1)
)   # 232, the same span m5 rode


def build_text_template() -> bytes:
    """`WEAPON:+0 <gap>SHIELD:+0 <gap>BALL:+0`, each value slot at level 0."""

    out = bytearray()
    for index, (label, texts) in enumerate(zip(GROUP_TEXTS, GROUP_VALUE_TEXTS)):
        if index:
            out += bytes((0x0C, 0x30 + GROUP_GAP))
        out += label.encode("ascii") + texts[0].encode("ascii")
    out += b"\0"
    return bytes(out)


def _slot_offsets() -> tuple[int, ...]:
    offsets = []
    cursor = 0
    for index, (label, width) in enumerate(zip(GROUP_TEXTS, GROUP_VALUE_WIDTHS)):
        if index:
            cursor += 2
        cursor += len(label)
        offsets.append(cursor)
        cursor += width
    return tuple(offsets)


TEXT_SLOT_OFFSETS = _slot_offsets()
TEXT_TEMPLATE_SIZE = len(build_text_template())
# Spaces make no record, so the widest value text is what sizes the buffer.
GLYPH_RECORDS = sum(
    len(label) + max(len(text.rstrip()) for text in texts)
    for label, texts in zip(GROUP_TEXTS, GROUP_VALUE_TEXTS)
)
CHAIN_BUFFER_SIZE = (GLYPH_RECORDS + 1) * 12
assert TEXT_WIDTH <= 248, "a chain's records must stay inside the signed byte"
# Which table each group copies from; the two gear groups share one.
VALUE_TABLE_LABELS = ("gear_values", "gear_values", "ball_values")

# --- geometry -------------------------------------------------------------------
# The frame: 256 px wide (records -128..+128 around its anchor), 26 tall (m5
# rode at 24 to clear the inventory's MONEY box at ~y 32; the user asked for
# the bottom two pixels lower to match the other elements' margins), against
# the top of the screen. Each rail is its top 8-px slice and its bottom 10-px
# slice; the bars sit at 0 and 18. The text actor's anchor is offset so
# 232 px centre in the 240 px between the rails; the 8x8 builder biases
# records by -128, so anchor = (left + 128, top + 128). The fill reaches 1 px
# past the rails' inner edge on each side (m4 showed a one-pixel gap there).
FRAME_WIDTH, FRAME_HEIGHT = 256, 26
FRAME_BAR_Y = FRAME_HEIGHT - 8                                 # the bottom bar's row (18)
RAIL_TOP_SLICE = (88, 8)                                       # (v, h): the rail's top 8 rows, at y 4
RAIL_BOTTOM_SLICE = (102, 10)                                  # its last 10 rows, at y 12 (ending 4 px under the bar, as vanilla's rail does)
FRAME_TOP = 1
FRAME_ANCHOR_X = 160
FRAME_ANCHOR_Y = FRAME_TOP
FRAME_LEFT = FRAME_ANCHOR_X - FRAME_WIDTH // 2                 # 32
TEXT_LEFT = FRAME_ANCHOR_X - TEXT_WIDTH // 2                   # 44
TEXT_TOP = FRAME_TOP + 9                                       # centred in the 10 px between the bars
TEXT_ANCHOR_X, TEXT_ANCHOR_Y = TEXT_LEFT + 128, TEXT_TOP + 128
BOX_X, BOX_Y, BOX_W, BOX_H = FRAME_LEFT + 7, FRAME_TOP + 4, FRAME_WIDTH - 14, FRAME_HEIGHT - 8
BOX_RGB = 0x40_2020                                            # the banner's
STRIP_RGB = 0x80_8080
assert FRAME_LEFT + FRAME_WIDTH <= 320 and TEXT_LEFT >= FRAME_LEFT + 8
assert 4 + RAIL_TOP_SLICE[1] + RAIL_BOTTOM_SLICE[1] == FRAME_BAR_Y + 4

BANNER_TPAGE, BANNER_CLUT = 0x0017, 0x7CC2


def _record(flags: int, ox: int, oy: int, u: int, v: int, w: int, h: int) -> bytes:
    return struct.pack("<BBbbHHBBBB", flags, 0x2C, ox, oy, BANNER_TPAGE, BANNER_CLUT, u, v, w, h)


def build_frame_chain() -> bytes:
    """The widened, shortened banner frame: corner rivets/caps, the two rails
    as a top and a bottom slice (the right ones mirrored, `0x41`, as in the
    vanilla chain), and each bar as cap + 112-px middle + 112-px middle +
    16-px middle + cap. Last record carries 0x80."""

    left, right = -FRAME_WIDTH // 2, FRAME_WIDTH // 2 - 8
    top_v, top_h = RAIL_TOP_SLICE
    bottom_v, bottom_h = RAIL_BOTTOM_SLICE
    records = [
        _record(0x40, left, FRAME_BAR_Y, 16, 40, 8, 8),        # bottom-left rivet
        _record(0x40, right, FRAME_BAR_Y, 16, 40, 8, 8),       # bottom-right rivet
        _record(0x40, right, 0, 24, 32, 8, 8),                 # top-right cap
        _record(0x40, left, 0, 16, 32, 8, 8),                  # top-left cap
        _record(0x41, left, 4, 0, top_v, 8, top_h),            # right rail, top slice (mirrored about the anchor)
        _record(0x41, left, 4 + top_h, 0, bottom_v, 8, bottom_h),   # right rail, bottom slice
        _record(0x40, left, 4, 0, top_v, 8, top_h),            # left rail, top slice
        _record(0x40, left, 4 + top_h, 0, bottom_v, 8, bottom_h),   # left rail, bottom slice
    ]
    for v, y in ((120, FRAME_BAR_Y), (112, 0)):       # bottom bar, then top bar
        records += [
            _record(0x40, left, y, 0, v, 8, 8),
            _record(0x40, left + 8, y, 8, v, 112, 8),
            _record(0x40, left + 120, y, 8, v, 112, 8),
            _record(0x40, left + 232, y, 8, v, 16, 8),
            _record(0x40, right, y, 120, v, 8, 8),
        ]
    chain = bytearray(b"".join(records))
    chain[-12] |= 0x80
    return bytes(chain)


FRAME_CHAIN_SIZE = len(build_frame_chain())

# The state block: the text actor pointer (0 = never spawned / reloaded from
# disc), the 3-level cache and the built flag, then the frame and box actor
# pointers (0 = not allocated) that the text actor's update hides/shows.
STATE_SIZE = 16
STATE_ACTOR = 0
STATE_CACHE = 4                                # 3 level bytes + built flag
STATE_FRAME_ACTOR = 8
STATE_BOX_ACTOR = 12


_ZERO, _AT, _V0, _V1, _A0, _A1, _A2, _A3 = range(8)
_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7 = range(8, 16)
_S0, _S1, _S2 = 16, 17, 18
_T8, _T9 = 24, 25
_SP, _RA = 29, 31


def _hi(address: int) -> int:
    return ((address + 0x8000) >> 16) & 0xFFFF


def _lo(address: int) -> int:
    return address & 0xFFFF


class Natives:
    """`spawn()` (the three actors, unless the text actor is still alive),
    `update(body, xf, spr)` (the text actor's per-frame callback), `rebuild()`,
    `chain_actor(x, y, chain, updater)`, and `trampoline` (what the scene
    runner's retargeted `jal` lands on). Uses s0-s1 with a frame; every load
    delay padded."""

    def __init__(self, base: int, state: int, text: int, chain: int, frame_chain: int) -> None:
        self.base = base
        self.state = state
        self.text = text
        self.chain = chain
        self.frame_chain = frame_chain
        self.addresses: dict[str, int] = {}
        self.code = self._assemble()
        self.code = self._assemble()

    def _adr(self, name: str) -> int:
        return self.addresses.get(name, self.base)

    def _assemble(self) -> bytes:
        b = _MipsBuilder()
        adr = self._adr

        def la(register: int, address: int) -> None:
            b.emit(_i(0x0F, 0, register, _hi(address)), _i(0x09, register, register, _lo(address)))

        # ---- rebuild(): the three level bytes -> the three value slots -> the chain
        #      Each slot is a fixed-width string copied whole out of a table
        #      here in the canvas, so a two-digit value and a one-digit one
        #      write the same number of cells and nothing downstream moves.
        b.label("rebuild")
        b.emit(_i(0x09, _SP, _SP, -0x18), _i(0x2B, _SP, _RA, 0x14))
        la(_T0, WEAPON_LEVEL_ADDRESS)
        la(_T1, BALL_LEVEL_ADDRESS)
        b.emit(_i(0x24, _T0, _T4, 0))                           # lbu t4, weapon
        b.emit(_i(0x24, _T0, _T5, 1))                           # lbu t5, shield
        b.emit(_i(0x24, _T1, _T6, 0))                           # lbu t6, ball
        la(_T2, self.text)
        groups = zip((_T4, _T5, _T6), TEXT_SLOT_OFFSETS, GROUP_VALUE_WIDTHS, VALUE_TABLE_LABELS)
        for register, offset, width, table in groups:
            b.emit(_i(0x0B, register, _T7, MAX_LEVEL + 1))      # sltiu t7, level, MAX+1
            b.branch(0x05, _T7, 0, f"rb_ok{offset}")
            b.emit(0)
            b.emit(_i(0x09, 0, register, MAX_LEVEL))            # out of range reads as the top
            b.label(f"rb_ok{offset}")
            # t7 = level * width, the two widths this strip uses spelled out
            b.emit(_r(0, register, _T7, 1, 0x00))               # sll t7, level, 1
            if width == 3:
                b.emit(_r(_T7, register, _T7, 0, 0x21))         # addu t7, t7, level
            elif width != 2:
                raise ValueError(f"no shift-and-add for a {width}-cell value slot")
            la(_T3, adr(table))
            b.emit(_r(_T3, _T7, _T3, 0, 0x21))                  # t3 = &table[level]
            # Loads first, then stores: the third load covers the first's delay.
            for index, scratch in zip(range(width), (_T7, _T8, _T9)):
                b.emit(_i(0x24, _T3, scratch, index))           # lbu, one cell
            b.emit(0)
            for index, scratch in zip(range(width), (_T7, _T8, _T9)):
                b.emit(_i(0x28, _T2, scratch, offset + index))  # sb into the slot
        la(_A0, self.chain)
        la(_A1, self.text)
        b.emit(_j(0x03, BUILD_ASCII_CHAIN_ADDRESS))
        b.emit(_r(0, 0, _A2, 0, 0x21))                          # (delay) mode 0
        b.emit(_i(0x23, _SP, _RA, 0x14), _i(0x09, _SP, _SP, 0x18))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- update(a0 = body, a1 = xf, a2 = spr): the text actor's per-frame callback
        b.label("update")
        b.emit(_i(0x09, _SP, _SP, -0x18), _i(0x2B, _SP, _RA, 0x14))
        la(_T0, self.state)
        if HIDE_WHILE_WINDOW_OPEN:
            # hidden = any town window open: t1 = 0x80 (hide) or 0 (show)
            b.emit(_i(0x0F, 0, _T1, _hi(WINDOW_LIST_HEAD_ADDRESS)))
            b.emit(_i(0x23, _T1, _T1, _lo(WINDOW_LIST_HEAD_ADDRESS)))   # lw t1, window list head
            b.emit(_i(0x23, _T0, _T2, STATE_FRAME_ACTOR))            # lw t2, frame actor (delay filler)
            b.emit(_i(0x23, _T0, _T3, STATE_BOX_ACTOR))              # lw t3, box actor
            b.emit(_r(0, _T1, _T1, 0, 0x2B))                         # sltu t1, zero, t1
            b.emit(_r(0, _T1, _T1, 7, 0x00))                         # sll t1, t1, 7 -> 0x80 / 0
            # the text sprite (a2 = our spr)
            b.emit(_i(0x25, _A2, _T4, SPRITE_FLAGS_OFFSET))          # lhu t4, flags
            b.emit(_i(0x09, 0, _T5, BOX_W))                          # (delay) t5 = shown width
            b.emit(_i(0x0C, _T4, _T4, 0xFFFF & ~SPRITE_HIDE_FLAG))
            b.emit(_r(_T4, _T1, _T4, 0, 0x25))                       # or t4, t4, t1
            b.emit(_i(0x29, _A2, _T4, SPRITE_FLAGS_OFFSET))
            # the frame sprite
            b.branch(0x04, _T2, 0, "upd_no_frame")
            b.emit(0)
            b.emit(_i(0x23, _T2, _T4, ACTOR_SPR_POINTER_OFFSET))     # lw t4, frame spr
            b.emit(0)
            b.emit(_i(0x25, _T4, _T6, SPRITE_FLAGS_OFFSET))
            b.emit(0)
            b.emit(_i(0x0C, _T6, _T6, 0xFFFF & ~SPRITE_HIDE_FLAG))
            b.emit(_r(_T6, _T1, _T6, 0, 0x25))
            b.emit(_i(0x29, _T4, _T6, SPRITE_FLAGS_OFFSET))
            b.label("upd_no_frame")
            # the box: width BOX_W shown, 0 hidden
            b.branch(0x04, _T1, 0, "upd_box_width")
            b.emit(0)
            b.emit(_r(0, 0, _T5, 0, 0x21))                           # hidden: width 0
            b.label("upd_box_width")
            b.branch(0x04, _T3, 0, "upd_no_box")
            b.emit(0)
            b.emit(_i(0x29, _T3, _T5, BOX_WIDTH_OFFSET))
            b.label("upd_no_box")
        la(_T3, WEAPON_LEVEL_ADDRESS)
        b.emit(_i(0x24, _T3, _T4, 0))
        b.emit(_i(0x24, _T3, _T5, 1))
        la(_T3, BALL_LEVEL_ADDRESS)
        b.emit(_i(0x24, _T3, _T6, 0))
        b.emit(_i(0x24, _T0, _T7, STATE_CACHE + 0))
        b.emit(_i(0x24, _T0, _T8, STATE_CACHE + 1))
        b.emit(_i(0x24, _T0, _T9, STATE_CACHE + 2))
        b.emit(_i(0x24, _T0, _T1, STATE_CACHE + 3))             # built flag
        b.emit(0)
        b.branch(0x04, _T1, 0, "upd_rebuild")
        b.emit(0)
        b.branch(0x05, _T4, _T7, "upd_rebuild")
        b.emit(0)
        b.branch(0x05, _T5, _T8, "upd_rebuild")
        b.emit(0)
        b.branch(0x04, _T6, _T9, "upd_done")
        b.emit(0)
        b.label("upd_rebuild")
        b.emit(_i(0x28, _T0, _T4, STATE_CACHE + 0))
        b.emit(_i(0x28, _T0, _T5, STATE_CACHE + 1))
        b.emit(_i(0x28, _T0, _T6, STATE_CACHE + 2))
        b.emit(_i(0x09, 0, _T1, 1))
        b.emit(_i(0x28, _T0, _T1, STATE_CACHE + 3))
        b.emit(_j(0x03, adr("rebuild")), 0)
        b.label("upd_done")
        b.emit(_i(0x23, _SP, _RA, 0x14), _i(0x09, _SP, _SP, 0x18))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- chain_actor(a0 = anchor x, a1 = anchor y, a2 = chain, a3 = updater or 0) -> v0 = actor
        #      A screen-space sprite-chain actor at OT 0, registered as a 2D root.
        #      Frame: args spilled at +0x28.. (the caller's outgoing area), ra +0x24, s0 +0x20, s1 +0x1C.
        b.label("chain_actor")
        b.emit(_i(0x09, _SP, _SP, -0x28), _i(0x2B, _SP, _RA, 0x24), _i(0x2B, _SP, _S0, 0x20), _i(0x2B, _SP, _S1, 0x1C))
        b.emit(_i(0x2B, _SP, _A0, 0x28), _i(0x2B, _SP, _A1, 0x2C), _i(0x2B, _SP, _A2, 0x30), _i(0x2B, _SP, _A3, 0x34))
        b.emit(_j(0x03, ALLOCATE_ACTOR_ADDRESS))
        b.emit(_i(0x09, 0, _A0, ACTOR_FLAGS))                   # (delay)
        b.branch(0x04, _V0, 0, "ca_done")
        b.emit(_r(_V0, 0, _S0, 0, 0x21))                        # (delay) s0 = actor
        b.emit(_i(0x23, _SP, _T0, 0x34))                        # updater
        b.emit(_i(0x23, _S0, _T1, 0x08))                        # xf
        b.emit(_i(0x23, _S0, _S1, 0x0C))                        # spr
        b.emit(_i(0x2B, _S0, _T0, 0x10))                        # per-frame callback (0 = none)
        b.emit(_i(0x23, _SP, _T2, 0x28))
        b.emit(0)
        b.emit(_i(0x29, _T1, _T2, 0x02))                        # screen X
        b.emit(_i(0x23, _SP, _T2, 0x2C))
        b.emit(0)
        b.emit(_i(0x29, _T1, _T2, 0x06))                        # screen Y
        b.emit(_i(0x29, _T1, 0, 0x0A))                          # OT index 0
        b.emit(_i(0x09, 0, _T2, 0x1000))
        b.emit(_i(0x29, _S1, _T2, 0x1C))                        # scale X
        b.emit(_i(0x29, _S1, _T2, 0x1E))                        # scale Y
        b.emit(_i(0x0F, 0, _T2, STRIP_RGB >> 16))
        b.emit(_i(0x0D, _T2, _T2, STRIP_RGB & 0xFFFF))
        b.emit(_i(0x2B, _S1, _T2, 0x0C))                        # rgb
        b.emit(_i(0x29, _S1, 0, 0x06))                          # OT bias 0
        b.emit(_i(0x29, _S1, 0, 0x10))                          # tpage add 0: records verbatim
        b.emit(_i(0x29, _S1, 0, 0x12))                          # clut add 0
        b.emit(_i(0x29, _S1, 0, 0x14))                          # flags: visible
        b.emit(_i(0x23, _SP, _T2, 0x30))
        b.emit(0)
        b.emit(_i(0x2B, _S1, _T2, 0x08))                        # the chain
        b.emit(_i(0x09, _S0, _A0, 0x20))                        # a0 = body
        b.emit(_i(0x0F, 0, _A1, _hi(RENDER_2D_CLASS_ADDRESS)))
        b.emit(_j(0x03, REGISTER_RENDER_ROOT_ADDRESS))
        b.emit(_i(0x09, _A1, _A1, _lo(RENDER_2D_CLASS_ADDRESS)))  # (delay)
        b.emit(_r(_S0, 0, _V0, 0, 0x21))
        b.label("ca_done")
        b.emit(_i(0x23, _SP, _S1, 0x1C), _i(0x23, _SP, _S0, 0x20), _i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- spawn(): the fill box, the frame, the text - unless the text actor is still alive
        b.label("spawn")
        b.emit(_i(0x09, _SP, _SP, -0x20), _i(0x2B, _SP, _RA, 0x1C), _i(0x2B, _SP, _S0, 0x18))
        la(_T0, self.state)
        b.emit(_i(0x23, _T0, _T1, STATE_ACTOR))                 # lw t1, text actor
        b.emit(0)
        b.branch(0x04, _T1, 0, "spawn_fresh")                   # never spawned (or the image reloaded)
        b.emit(0)
        b.emit(_i(0x25, _T1, _T2, 0x1E))                        # lhu t2, actor flags
        b.emit(_i(0x23, _T1, _T3, 0x10))                        # lw t3, actor callback
        b.emit(_i(0x0C, _T2, _T2, 0x4000))                      # live bit
        b.branch(0x04, _T2, 0, "spawn_fresh")
        b.emit(0)
        la(_T4, adr("update"))
        b.branch(0x04, _T3, _T4, "spawn_done")                  # alive and ours: nothing to do
        b.emit(0)
        b.label("spawn_fresh")
        b.emit(_i(0x2B, _T0, 0, STATE_ACTOR))
        b.emit(_i(0x2B, _T0, 0, STATE_CACHE))                   # cache cleared, built = 0
        b.emit(_i(0x2B, _T0, 0, STATE_FRAME_ACTOR))
        b.emit(_i(0x2B, _T0, 0, STATE_BOX_ACTOR))
        # the fill box: no updater, drawn by the resident tile class from its fields
        b.emit(_j(0x03, ALLOCATE_ACTOR_ADDRESS))
        b.emit(_i(0x09, 0, _A0, BOX_ACTOR_FLAGS))               # (delay)
        b.branch(0x04, _V0, 0, "spawn_frame")
        b.emit(_r(_V0, 0, _S0, 0, 0x21))                        # (delay) s0 = actor
        la(_T1, self.state)
        b.emit(_i(0x2B, _T1, _S0, STATE_BOX_ACTOR))             # remember the box actor
        b.emit(_i(0x09, 0, _T0, BOX_X))
        b.emit(_i(0x29, _S0, _T0, 0x2C))
        b.emit(_i(0x09, 0, _T0, BOX_Y))
        b.emit(_i(0x29, _S0, _T0, 0x2E))
        b.emit(_i(0x09, 0, _T0, BOX_W))
        b.emit(_i(0x29, _S0, _T0, 0x30))
        b.emit(_i(0x09, 0, _T0, BOX_H))
        b.emit(_i(0x29, _S0, _T0, 0x32))
        b.emit(_i(0x09, 0, _T0, 1))
        b.emit(_i(0x29, _S0, _T0, 0x36))                        # semi-transparent
        b.emit(_i(0x29, _S0, 0, 0x34))
        b.emit(_i(0x0F, 0, _T0, BOX_RGB >> 16))
        b.emit(_i(0x0D, _T0, _T0, BOX_RGB & 0xFFFF))
        b.emit(_i(0x2B, _S0, _T0, 0x28))
        b.emit(_r(_S0, 0, _A0, 0, 0x21))
        b.emit(_i(0x0F, 0, _A1, _hi(TILE_CLASS_ADDRESS)))
        b.emit(_j(0x03, REGISTER_ACTOR_ROOT_ADDRESS))
        b.emit(_i(0x09, _A1, _A1, _lo(TILE_CLASS_ADDRESS)))     # (delay)
        b.label("spawn_frame")
        # the frame: a chain actor over the static frame records, no updater
        b.emit(_i(0x09, 0, _A0, FRAME_ANCHOR_X))
        b.emit(_i(0x09, 0, _A1, FRAME_ANCHOR_Y))
        la(_A2, self.frame_chain)
        b.emit(_j(0x03, adr("chain_actor")))
        b.emit(_r(0, 0, _A3, 0, 0x21))                          # (delay) no updater
        la(_T0, self.state)
        b.emit(_i(0x2B, _T0, _V0, STATE_FRAME_ACTOR))           # remember the frame actor (0 if none)
        # the text: build the chain first so it is valid before the first draw,
        # and cache the levels it was built from
        b.emit(_j(0x03, adr("rebuild")), 0)
        la(_T0, self.state)
        la(_T2, WEAPON_LEVEL_ADDRESS)
        b.emit(_i(0x24, _T2, _T3, 0))
        b.emit(_i(0x24, _T2, _T4, 1))
        la(_T2, BALL_LEVEL_ADDRESS)
        b.emit(_i(0x24, _T2, _T5, 0))
        b.emit(_i(0x09, 0, _T1, 1))
        b.emit(_i(0x28, _T0, _T3, STATE_CACHE + 0))
        b.emit(_i(0x28, _T0, _T4, STATE_CACHE + 1))
        b.emit(_i(0x28, _T0, _T5, STATE_CACHE + 2))
        b.emit(_i(0x28, _T0, _T1, STATE_CACHE + 3))             # built
        b.emit(_i(0x09, 0, _A0, TEXT_ANCHOR_X))
        b.emit(_i(0x09, 0, _A1, TEXT_ANCHOR_Y))
        la(_A2, self.chain)
        la(_A3, adr("update"))
        b.emit(_j(0x03, adr("chain_actor")), 0)
        la(_T0, self.state)
        b.emit(_i(0x2B, _T0, _V0, STATE_ACTOR))                 # remember the text actor (0 if none)
        b.label("spawn_done")
        b.emit(_i(0x23, _SP, _S0, 0x18), _i(0x23, _SP, _RA, 0x1C), _i(0x09, _SP, _SP, 0x20))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21))

        # ---- trampoline: what the scene runner's `jal 0x80034f88` becomes.
        #      Keep its a0/a1, spawn, then tail-jump to the real target so it
        #      returns to the runner's own ra.
        b.label("trampoline")
        b.emit(_i(0x09, _SP, _SP, -0x20), _i(0x2B, _SP, _RA, 0x1C), _i(0x2B, _SP, _A0, 0x18), _i(0x2B, _SP, _A1, 0x14))
        b.emit(_j(0x03, adr("spawn")), 0)
        b.emit(_i(0x23, _SP, _A0, 0x18), _i(0x23, _SP, _A1, 0x14), _i(0x23, _SP, _RA, 0x1C))
        b.emit(_j(0x02, RUN_SCRIPT_ADDRESS))
        b.emit(_i(0x09, _SP, _SP, 0x20))                        # (delay)

        # ---- data: the value strings, one fixed-width entry per level
        for label, texts in dict(zip(VALUE_TABLE_LABELS, GROUP_VALUE_TEXTS)).items():
            b.label(label)
            blob = "".join(texts).encode("ascii")
            blob = blob.ljust((len(blob) + 3) // 4 * 4, b"\0")
            b.emit(*struct.unpack(f"<{len(blob) // 4}I", blob))

        code = b.build()
        self.addresses = {name: self.base + offset * 4 for name, offset in b.labels.items()}
        return code


# ---------------------------------------------------------------------------
# Layout in the canvas, and the disc records
# ---------------------------------------------------------------------------

def _align(value: int, alignment: int = 4) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


class Layout:
    def __init__(self) -> None:
        self.state_address = CANVAS_ADDRESS + 16                 # after the 4-byte nop guard, 16-aligned
        self.text_address = self.state_address + STATE_SIZE
        self.chain_address = _align(self.text_address + TEXT_TEMPLATE_SIZE, 4)
        self.frame_chain_address = _align(self.chain_address + CHAIN_BUFFER_SIZE, 4)
        self.natives_address = _align(self.frame_chain_address + FRAME_CHAIN_SIZE, 4)
        self.natives = Natives(
            self.natives_address, self.state_address, self.text_address, self.chain_address, self.frame_chain_address
        )
        self.end = self.natives_address + len(self.natives.code)
        if self.end > CANVAS_END:
            raise ValueError(f"The status strip runs to 0x{self.end:08X}, past the canvas end 0x{CANVAS_END:08X}.")


def build_layout() -> Layout:
    return Layout()


def build_scene_runner_jal(layout: Layout) -> bytes:
    return struct.pack("<I", _j(0x03, layout.natives.addresses["trampoline"]))


def iter_town_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN file offset, bytes): the canvas pieces and the runner's jal."""

    layout = build_layout()
    pieces = (
        (CANVAS_ADDRESS, bytes(CANVAS_GUARD)),                    # the nop guard
        (layout.state_address, bytes(STATE_SIZE)),
        (layout.text_address, build_text_template()),
        (layout.chain_address, bytes(CHAIN_BUFFER_SIZE)),
        (layout.frame_chain_address, build_frame_chain()),
        (layout.natives_address, layout.natives.code),
        (SCENE_RUNNER_TAIL_JAL_ADDRESS, build_scene_runner_jal(layout)),
    )
    return tuple((town_shop.town_runtime_to_file_offset(address), body) for address, body in pieces)


def iter_town_status_strip_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return save_removal._iter_mode2_raw_patches(town_shop.TOWN_FILE_START_LBA, iter_town_file_patches())


def append_town_status_strip_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_town_status_strip_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
