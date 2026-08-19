"""The ball charger: the fortune teller's neighbour, who adds charges to spell
balls for town money, a few per town visit, as many as ADAP's White Sands buy.

Account: `docs/systems/fortune-teller.md` section 5. This is the blacksmith's
temper machinery (`blacksmith.py`, ridden) copied onto category 0x04 - the
charge count is the ball's quality byte - with these differences:

* **She lives in the fortune teller's scene**: a third spawn record in
  Shiela's template run (there is a spare terminator), and Shiela's dialogue
  table relocated into the dialogue image with a second row for the charger's
  slot (`fortune_teller.build_layout` places both; the overlay edit is one
  `addiu` immediate). The sprite is the streamed "dancer" type, whose every
  frame the fortune-teller state's VRAM holds byte-identical to outdoors -
  the building loads nothing over that page.
* **The slab seams are untouched.** The blacksmith's price gate and the
  check-capacity gate key on `ACTIVE_SHOP == 4` and jump to a fixed two-word
  table at `0x80019884` - an address inside whichever interior dialogue image
  is loaded. The equipment shop's image puts the smith's natives there; the
  fortune teller's image puts *this* module's `j price / j guard` pair there.
  Same marker, same gates, the loaded image decides.
* **An allowance, not a cap.** `USES_BY_LEVEL = 0/1/2/3` charges **per town
  visit**, level = White Sands received (`patch.BALL_CHARGE_LEVEL_ADDRESS`, a
  byte beside ADSV that the client writes like the temper levels). The charges
  go wherever the player wants them - three into one ball or one into each of
  three - and the only ceiling on a single ball is `MAX_CHARGES = 10`, the
  mix-magic teaching count, at every level.

  The per-ball cap this started as did almost nothing: balls are found with
  more charges than a low level allowed, and by the time the third White Sand
  arrived the run was nearly over. Charges are consumed and replaced by
  picking up fuller balls, so raising a ceiling nobody was against is not a
  service. A small, resetting allowance is: it is worth walking back to town
  for, it never trivialises a floor, and it scales with the sands.

  The spend counter is `patch.BALL_CHARGE_USED_ADDRESS`, the byte beside the
  level, refilled by the tower floor bootstrap helper on every floor build -
  so leaving her building and coming back does not refill it, and one climb
  does.
* **Price** `CHARGE_COST` table, indexed by the charges the ball already
  holds: 500 for the first, quadratically to 3000 for the tenth
  (`charge_cost`). Topping up a nearly full ball is still the expensive end.
* **Not the Acid Rain Ball** (id 17): the game caps it at one charge.
* Her own catalog buffer, menu handle and zeroing loop, because the
  equipment overlay's (`zero_bytes`, `g_shop_catalog_buffer`) are not loaded
  in this building.

The conversation: `Fill your spheres?` / `I can add N more this visit.` /
`[Fill.] [Not now.]`; `Fill.` opens the generic town menu over the chargeable
balls with the next charge's price in the price column and `Charge (x
button)` as the header; X charges a row on the spot. Rows already at ten
charges are greyed, and so is every row once the visit's last charge is
spent. Without the gold the refusal reads `[Not enough gold]`; pressing on
with the allowance spent reads `[Nothing left today]` - the same refusal
slot, repointed at a second string, because the pointer is ours for as long
as the menu is up. With no White Sand yet she says her art sleeps; with the
allowance spent she asks for a climb.
"""

from __future__ import annotations

import struct
from typing import Sequence

from . import patch, town_shop
from .patch import _MipsBuilder, _i, _j, _r

# --- who she is ---------------------------------------------------------------
#
# Type 0x23 (constructor slot 0x27) - the purple-haired dancer of the streamed
# catalogue (docs/game/assets/town-npc-catalogue_streamed.png). Her 27 texture
# rects (all tpage 0x1A, bottom half) and four CLUTs are byte-identical between
# the fortune-teller state, the equipment shop and outdoors (measured), so she
# renders in this building, which never overwrites that page. Types 0x24 and
# 0x47-0x4A share the same animation table under other slots.
CHARGER_ACTOR_TYPE = 0x23
CHARGER_ACTOR_SLOT = 0x27
# Section-local, +Y screen-down, base (512, 512): to Shiela's right (she is at
# (512, 280); Koh enters at about (504, 452) local). Ride-adjustable.
CHARGER_POSITION = (592, 296)
CHARGER_FACING = 1
# Shiela's record shape (hdr 0x6000, presence flag 1) with the type and place
# swapped; the +4 word's low byte is the facing.
CHARGER_RECORD_TEMPLATE = bytes.fromhex("00600100" "01000000" "00000000" "00000000" "00000000")

# --- what the natives touch --------------------------------------------------
BALL_CATEGORY = 0x04
ACID_RAIN_BALL_ID = 17
INVENTORY_ORDER_ADDRESS = 0x8001_029C      # g_inventory_order_pointers, null-terminated
TOWN_MONEY_ADDRESS = 0x8001_2D5C           # g_town_money, u32
LEVEL_ADDRESS = patch.BALL_CHARGE_LEVEL_ADDRESS
USED_ADDRESS = patch.BALL_CHARGE_USED_ADDRESS
ROW_BUILDER_ADDRESS = 0x800B_0FD4          # rebuilds the five visible rows of a list core
MENU_HEADER_POINTER_SLOT_ADDRESS = town_shop.MENU_HEADER_POINTER_SLOT_ADDRESS
VANILLA_MENU_HEADER_TEXT_ADDRESS = town_shop.VANILLA_MENU_HEADER_TEXT_ADDRESS
REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS = 0x800D_1558
VANILLA_REFUSAL_MESSAGE_ADDRESS = 0x8008_91A8
CATALOG_LEADING_ENTRY = 0x0000_1601
CATALOG_BUFFER_SIZE = 0x100
# The slab's two entry points, shared with the blacksmith (town_shop owns them).
JUMP_TABLE_ADDRESS = town_shop.SMITH_NATIVE_TABLE_ADDRESS
MENU_SHOP_MARKER = town_shop.SMITH_MENU_SHOP_MARKER

# Charges she will add per TOWN VISIT, by White Sands received. Not a per-ball
# cap and not per-ball at all: three at level 3 means three charges total,
# wherever the player puts them.
USES_BY_LEVEL = (0, 1, 2, 3)
MAX_LEVEL = len(USES_BY_LEVEL) - 1
MAX_USES = USES_BY_LEVEL[-1]
# The only ceiling on one ball, at every level: mix magic's teaching count.
MAX_CHARGES = 10
FIRST_CHARGE_COST = 500
LAST_CHARGE_COST = 3000


def charge_cost(charges: int) -> int:
    """The price of the next charge for a ball holding `charges` (clamped to
    0..MAX-1): 500 for the first, quadratic to 3000 for the tenth."""

    q = min(max(charges, 0), MAX_CHARGES - 1)
    span = MAX_CHARGES - 1
    return FIRST_CHARGE_COST + round((LAST_CHARGE_COST - FIRST_CHARGE_COST) * q * q / (span * span))


CHARGE_COST = tuple(charge_cost(q) for q in range(MAX_CHARGES))
assert CHARGE_COST[0] == FIRST_CHARGE_COST and CHARGE_COST[-1] == LAST_CHARGE_COST
assert all(a < b for a, b in zip(CHARGE_COST, CHARGE_COST[1:]))
assert max(CHARGE_COST) < 0x10000


def build_header_text() -> bytes:
    return (
        town_shop._encode_shop_name("Charge (", max_characters=None)[:-1]
        + bytes((0x81, 0x7E, 0x20))
        + town_shop._encode_shop_name("button)", max_characters=None)
    )


def build_refusal_text() -> bytes:
    return town_shop._encode_shop_name("[Not enough gold]", max_characters=None)


def build_spent_refusal_text() -> bytes:
    """What the refusal box says when the gold is there but the visit's
    allowance is not. Same slot, second string - the guard repoints it."""

    return town_shop._encode_shop_name("[Nothing left today]", max_characters=None)


# ---------------------------------------------------------------------------
# MIPS
# ---------------------------------------------------------------------------

_ZERO, _AT, _V0, _V1, _A0, _A1, _A2, _A3 = range(8)
_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7 = range(8, 16)
_T8, _T9 = 24, 25
_SP, _RA = 29, 31


def _hi(address: int) -> int:
    return ((address + 0x8000) >> 16) & 0xFFFF


def _lo(address: int) -> int:
    return address & 0xFFFF


class Natives:
    """The charger's natives, in one block, plus the two-word jump table the
    slab targets (built separately at JUMP_TABLE_ADDRESS by `build_jump_table`).

    Entries: is_chargeable, price, cap, has_balls, guard, after_menu, open,
    the cost table and the cap table. Caller-saved registers only, every load
    delay padded. `catalog`, `handle`, `header_text` and `refusal_text` are
    addresses the layout hands in (all inside the fortune-teller image).
    """

    ENTRIES = (
        "is_chargeable", "price", "allowance", "uses_left",
        "has_balls", "guard", "after_menu", "open",
    )

    def __init__(
        self,
        base: int,
        catalog: int,
        handle: int,
        header_text: int,
        refusal_text: int,
        refusal_spent_text: int = 0,
    ) -> None:
        self.base = base
        self.catalog = catalog
        self.handle = handle
        self.header_text = header_text
        self.refusal_text = refusal_text
        # Shown when the visit's allowance is spent. Defaulted so a caller that
        # predates the second string still assembles (and just repeats itself).
        self.refusal_spent_text = refusal_spent_text or refusal_text
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

        # ---- is_chargeable(a0 = descriptor) -> v0. Leaf; clobbers v1, a1.
        b.label("is_chargeable")
        b.emit(_i(0x24, _A0, _V0, 1))                          # lbu v0, category
        b.emit(_i(0x24, _A0, _V1, 0))                          # lbu v1, id
        b.emit(_i(0x09, 0, _A1, BALL_CATEGORY))                # a1 = 4
        b.branch(0x05, _V0, _A1, "chg_no")                     # not a ball
        b.emit(_i(0x09, 0, _A1, ACID_RAIN_BALL_ID))            # (delay) a1 = 17
        b.branch(0x04, _V1, _A1, "chg_no")                     # Acid Rain: never
        b.emit(0)
        b.emit(_r(_RA, 0, 0, 0, 0x08), _i(0x09, 0, _V0, 1))    # jr ra / v0 = 1
        b.label("chg_no")
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21)) # jr ra / v0 = 0

        # ---- price(a0 = descriptor) -> v0 = cost of the next charge. Leaf; t0-t2.
        b.label("price")
        b.emit(_i(0x20, _A0, _T0, 2))                          # lb t0, charges
        b.emit(0)
        b.emit(_r(_T0, 0, _T1, 0, 0x2A))                       # slt t1, t0, zero
        b.branch(0x04, _T1, 0, "price_floor_ok")
        b.emit(0)
        b.emit(_r(0, 0, _T0, 0, 0x21))                         # negative -> 0
        b.label("price_floor_ok")
        b.emit(_i(0x0B, _T0, _T1, MAX_CHARGES))                # sltiu t1, t0, MAX
        b.branch(0x05, _T1, 0, "price_in_range")
        b.emit(0)
        b.emit(_i(0x09, 0, _T0, MAX_CHARGES - 1))              # at/over the max: the last price
        b.label("price_in_range")
        b.emit(_r(0, _T0, _T0, 1, 0x00))                       # t0 = q * 2 (halfword table)
        la(_T2, adr("cost_table"))
        b.emit(_r(_T2, _T0, _T2, 0, 0x21))
        b.emit(_i(0x25, _T2, _V0, 0))                          # lhu v0, cost[q]
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- allowance() -> v0 = USES_BY_LEVEL[min(level, MAX)]: the charges
        # she will add this town visit, before any are spent. Leaf; t0-t1, v1.
        b.label("allowance")
        la(_T0, LEVEL_ADDRESS)
        b.emit(_i(0x24, _T0, _T0, 0))                          # lbu t0, level
        b.emit(_i(0x0F, 0, _T1, _hi(adr("uses_table"))))       # (delay)
        b.emit(_i(0x0B, _T0, _V1, MAX_LEVEL + 1))              # sltiu v1, level, MAX+1
        b.branch(0x05, _V1, 0, "allowance_in_range")
        b.emit(0)
        b.emit(_i(0x09, 0, _T0, MAX_LEVEL))
        b.label("allowance_in_range")
        b.emit(_r(_T1, _T0, _T1, 0, 0x21))
        b.emit(_i(0x24, _T1, _V0, _lo(adr("uses_table"))))     # lbu v0, table[level]
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- uses_left() -> v0 = allowance - spent, floored at 0. Calls
        # allowance, so t0/t1/v1 are gone on return; keeps its own t2/t3.
        b.label("uses_left")
        b.emit(_i(0x09, _SP, _SP, -0x18), _i(0x2B, _SP, _RA, 0x14))
        b.emit(_j(0x03, adr("allowance")), 0)
        la(_T2, USED_ADDRESS)
        b.emit(_i(0x24, _T2, _T2, 0))                          # lbu t2, spent this visit
        b.emit(0)
        b.emit(_r(_T2, _V0, _T3, 0, 0x2B))                     # sltu t3, spent, allowance
        b.branch(0x05, _T3, 0, "uses_left_some")
        b.emit(0)
        b.branch(0x04, 0, 0, "uses_left_done")
        b.emit(_r(0, 0, _V0, 0, 0x21))                         # (delay) none left
        b.label("uses_left_some")
        b.emit(_r(_V0, _T2, _V0, 0, 0x23))                     # subu v0, allowance, spent
        b.label("uses_left_done")
        b.emit(_i(0x23, _SP, _RA, 0x14), _i(0x09, _SP, _SP, 0x18))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)


        # ---- has_balls() -> v0: any chargeable ball in the bag
        b.label("has_balls")
        b.emit(_i(0x09, _SP, _SP, -0x18), _i(0x2B, _SP, _RA, 0x14))
        la(_T4, INVENTORY_ORDER_ADDRESS)
        b.label("has_loop")
        b.emit(_i(0x23, _T4, _A0, 0))                          # lw a0, order[i]
        b.emit(0)
        b.branch(0x04, _A0, 0, "has_none")
        b.emit(0)
        b.emit(_j(0x03, adr("is_chargeable")), 0)
        b.branch(0x05, _V0, 0, "has_done")
        b.emit(0)
        b.emit(_i(0x09, _T4, _T4, 4))
        b.branch(0x04, 0, 0, "has_loop")
        b.emit(0)
        b.label("has_none")
        b.emit(_r(0, 0, _V0, 0, 0x21))
        b.label("has_done")
        b.emit(_i(0x23, _SP, _RA, 0x14), _i(0x09, _SP, _SP, 0x18))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- guard(a0 = menu+0x20) -> v0: the A press. Charge or refuse.
        # Frame: +0x18 entry, +0x1C allowance, +0x20 a0, +0x24 ra.
        b.label("guard")
        b.emit(_i(0x09, _SP, _SP, -0x28), _i(0x2B, _SP, _RA, 0x24), _i(0x2B, _SP, _A0, 0x20))
        # The visit's allowance first: with none left the refusal slot is
        # repointed at the second string, so the player is told WHY rather than
        # being shown a gold message with the gold in hand. The slot is ours
        # until after_menu hands it back, so writing it here is safe.
        b.emit(_j(0x03, adr("uses_left")), 0)
        b.emit(_i(0x0F, 0, _T0, _hi(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.branch(0x05, _V0, 0, "guard_has_uses")
        b.emit(0)
        la(_T1, self.refusal_spent_text)
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21)) # spent: v0 = 0
        b.label("guard_has_uses")
        la(_T1, self.refusal_text)
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x23, _SP, _A0, 0x20))
        b.emit(0)
        b.emit(_i(0x23, _A0, _T0, 4))                          # lw t0, selected row index
        b.emit(_i(0x23, _A0, _T1, 0x20))                       # lw t1, catalog base
        b.emit(_r(0, _T0, _T0, 2, 0x00))                       # t0 <<= 2
        b.emit(_r(_T1, _T0, _T2, 0, 0x21))                     # t2 = entry
        b.emit(_i(0x2B, _SP, _T2, 0x18))
        b.emit(_j(0x03, adr("allowance")), 0)                  # v0 = this visit's allowance
        b.emit(_i(0x2B, _SP, _V0, 0x1C))
        # A row already at ten charges is greyed, so this is defence only.
        b.emit(_i(0x23, _SP, _A0, 0x18))                       # a0 = entry
        b.emit(0)
        b.emit(_i(0x20, _A0, _T0, 2))                          # lb t0, charges
        b.emit(_i(0x09, 0, _T1, MAX_CHARGES))                  # (delay)
        b.emit(_r(_T0, _T1, _T2, 0, 0x2A))                     # slt t2, charges, MAX
        b.branch(0x05, _T2, 0, "guard_price")
        b.emit(0)
        b.emit(_i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21)) # already full: v0 = 0
        b.label("guard_price")
        b.emit(_i(0x23, _SP, _A0, 0x18))                       # a0 = entry
        b.emit(_j(0x03, adr("price")), 0)
        b.emit(_r(_V0, 0, _T6, 0, 0x21))                       # t6 = cost
        b.emit(_i(0x0F, 0, _T8, _hi(TOWN_MONEY_ADDRESS)))
        b.emit(_i(0x23, _T8, _T9, _lo(TOWN_MONEY_ADDRESS)))    # lw t9, money
        b.emit(0)
        b.emit(_r(_T9, _T6, _T4, 0, 0x2B))                     # sltu t4, money, cost
        b.branch(0x04, _T4, 0, "guard_pay")
        b.emit(0)
        b.emit(_i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21)) # refuse: v0 = 0
        b.label("guard_pay")
        b.emit(_r(_T9, _T6, _T9, 0, 0x23))                     # money -= cost
        b.emit(_i(0x2B, _T8, _T9, _lo(TOWN_MONEY_ADDRESS)))
        # Spend one of the visit's charges. Paid for and counted before the bag
        # walk, so a walk that somehow matches no row costs the charge it has
        # already taken payment for rather than handing out a free one.
        la(_T8, USED_ADDRESS)
        b.emit(_i(0x24, _T8, _T9, 0))                          # lbu t9, spent this visit
        b.emit(0)
        b.emit(_i(0x09, _T9, _T9, 1))
        b.emit(_i(0x28, _T8, _T9, 0))
        b.emit(_i(0x23, _SP, _A0, 0x20))
        b.emit(0)
        b.emit(_i(0x23, _A0, _T0, 4))                          # t0 = row index (1-based over the balls)
        la(_T1, INVENTORY_ORDER_ADDRESS)
        b.emit(_r(0, 0, _T4, 0, 0x21))                         # t4 = matches so far
        b.label("guard_walk")
        b.emit(_i(0x23, _T1, _T5, 0))                          # lw t5, order[i]
        b.emit(0)
        b.branch(0x04, _T5, 0, "guard_spent_check")
        b.emit(_r(_T5, 0, _A0, 0, 0x21))                       # (delay) a0 = descriptor
        b.emit(_j(0x03, adr("is_chargeable")), 0)
        b.branch(0x04, _V0, 0, "guard_walk_next")
        b.emit(0)
        b.emit(_i(0x09, _T4, _T4, 1))
        b.branch(0x05, _T4, _T0, "guard_walk_next")
        b.emit(0)
        # found: bump the ball and its catalog row
        b.emit(_i(0x20, _T5, _T7, 2))                          # lb t7, charges
        b.emit(_i(0x23, _SP, _T2, 0x18))                       # (delay filler) t2 = entry
        b.emit(_i(0x09, _T7, _T7, 1))
        b.emit(_i(0x28, _T5, _T7, 2))                          # inventory charges + 1
        b.emit(_i(0x28, _T2, _T7, 2))                          # catalog row charges + 1
        b.emit(_i(0x09, 0, _T3, MAX_CHARGES))                  # the one ceiling, at every level
        b.emit(_i(0x24, _T2, _T8, 3))                          # lbu t8, row flags
        b.emit(_r(_T7, _T3, _T9, 0, 0x2A))                     # slt t9, new charges, MAX
        b.branch(0x04, _T9, 0, "guard_at_cap")
        b.emit(0)
        b.emit(_i(0x0D, _T8, _T8, 0x20))                       # pre-set 0x20: the toggle clears it
        b.branch(0x04, 0, 0, "guard_store")
        b.emit(0)
        b.label("guard_at_cap")
        # 0xA0, NOT 0x80 - see the same line in blacksmith.py. Vanilla XORs
        # 0x20 after we return and recolours from the result, so a row left
        # with 0x20 clear goes GREEN with the BUY tag on it. This is the
        # sphere that just reached ten charges: it should go grey.
        b.emit(_i(0x0D, _T8, _T8, 0xA0))                       # 0xA0: the toggle leaves 0x80
        b.label("guard_store")
        b.emit(_i(0x28, _T2, _T8, 3))
        b.branch(0x04, 0, 0, "guard_spent_check")
        b.emit(0)
        b.label("guard_walk_next")
        b.emit(_i(0x09, _T1, _T1, 4))
        b.branch(0x04, 0, 0, "guard_walk")
        b.emit(0)
        # If that was the visit's last charge, grey every row: the list then
        # says so itself instead of answering the next press with a message.
        b.label("guard_spent_check")
        la(_T8, USED_ADDRESS)
        b.emit(_i(0x24, _T8, _T8, 0))                          # lbu t8, spent
        b.emit(_i(0x23, _SP, _T9, 0x1C))                       # lw t9, allowance
        b.emit(0)
        b.emit(_r(_T8, _T9, _T0, 0, 0x2B))                     # sltu t0, spent, allowance
        b.branch(0x05, _T0, 0, "guard_rebuild")
        b.emit(0)
        b.emit(_i(0x23, _SP, _A0, 0x20))
        b.emit(0)
        b.emit(_i(0x23, _A0, _T0, 0x20))                       # t0 = catalog base
        b.emit(0)
        b.emit(_i(0x09, _T0, _T0, 4))                          # past the header row
        # Every row goes unselectable. The SELECTED row keeps the 0x20 the
        # store above left on it, so vanilla's toggle takes it back off and the
        # row settles on 0x80 like its neighbours instead of flashing green.
        b.label("guard_grey")
        b.emit(_i(0x23, _T0, _T1, 0))                          # lw t1, row
        b.emit(0)
        b.branch(0x04, _T1, 0, "guard_rebuild")
        b.emit(0)
        b.emit(_i(0x24, _T0, _T1, 3))                          # lbu t1, row flags
        b.emit(0)
        b.emit(_i(0x0D, _T1, _T1, 0x80))
        b.emit(_i(0x28, _T0, _T1, 3))
        b.branch(0x04, 0, 0, "guard_grey")
        b.emit(_i(0x09, _T0, _T0, 4))                          # (delay) next row
        b.label("guard_rebuild")
        b.emit(_i(0x23, _SP, _A0, 0x20))                       # a0 = menu+0x20
        b.emit(0)
        b.emit(_i(0x23, _A0, _A0, 0x28))                       # a0 = list widget
        b.emit(0)
        b.emit(_i(0x09, _A0, _A0, 0x20))                       # a0 = list core
        b.emit(_j(0x03, ROW_BUILDER_ADDRESS), 0)
        b.emit(_i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _i(0x09, 0, _V0, 1))    # v0 = 1: allowed


        # ---- after_menu() -> 0: restore the two pointers, drop the marker
        b.label("after_menu")
        b.emit(_i(0x0F, 0, _T0, _hi(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        la(_T1, VANILLA_MENU_HEADER_TEXT_ADDRESS)
        b.emit(_i(0x2B, _T0, _T1, _lo(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T0, _hi(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        la(_T1, VANILLA_REFUSAL_MESSAGE_ADDRESS)
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        la(_T0, town_shop.SHOP_CORE_ADDRESS)
        b.emit(_i(0x09, 0, _T1, 0xFF))
        b.emit(_i(0x28, _T0, _T1, town_shop.ACTIVE_SHOP_OFFSET))  # ACTIVE_SHOP = 0xFF
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21))

        # ---- open() -> v0 = menu handle (0 = retry, per the opener protocol)
        b.label("open")
        b.emit(_i(0x09, _SP, _SP, -0x20), _i(0x2B, _SP, _RA, 0x1C))
        # The ceiling every row is measured against, computed once: ten charges
        # while the visit has an allowance left, and ZERO when it does not -
        # which greys the whole list, because no row can hold fewer than zero.
        # Before the catalog cursors claim t2/t3, which uses_left clobbers.
        b.emit(_j(0x03, adr("uses_left")), 0)
        b.emit(_i(0x09, 0, _T9, MAX_CHARGES))                   # (delay) t9 = the ceiling
        b.branch(0x05, _V0, 0, "open_ceiling_ready")
        b.emit(0)
        b.emit(_r(0, 0, _T9, 0, 0x21))                          # nothing left this visit
        b.label("open_ceiling_ready")
        # zero the catalog buffer ourselves (the equipment overlay's zero_bytes is not loaded here)
        la(_T0, self.catalog)
        b.emit(_i(0x09, _T0, _T1, CATALOG_BUFFER_SIZE))         # t1 = end
        b.label("open_zero")
        b.emit(_i(0x09, _T0, _T0, 4))
        b.branch(0x05, _T0, _T1, "open_zero")
        b.emit(_i(0x2B, _T0, 0, -4))                            # (delay) sw zero,-4(t0)
        la(_T0, self.catalog)
        b.emit(_i(0x09, 0, _T1, CATALOG_LEADING_ENTRY))
        b.emit(_i(0x2B, _T0, _T1, 0))                          # the header row
        b.emit(_i(0x09, _T0, _T3, 4))                          # t3 = catalog cursor
        la(_T2, INVENTORY_ORDER_ADDRESS)
        b.label("open_loop")
        b.emit(_i(0x23, _T2, _T4, 0))                          # lw t4, order[i]
        b.emit(0)
        b.branch(0x04, _T4, 0, "open_done")
        b.emit(_r(_T4, 0, _A0, 0, 0x21))                       # (delay) a0 = descriptor
        b.emit(_j(0x03, adr("is_chargeable")), 0)
        b.branch(0x04, _V0, 0, "open_next")
        b.emit(0)
        b.emit(_i(0x23, _T4, _T7, 0))                          # lw t7, descriptor word
        b.emit(_i(0x0F, 0, _T8, 0x00FF))
        b.emit(_i(0x0D, _T8, _T8, 0xFFFF))
        b.emit(_r(_T7, _T8, _T7, 0, 0x24))                     # clear the flags byte
        b.emit(_i(0x20, _T4, _T5, 2))                          # lb t5, charges
        b.emit(0)
        b.emit(_r(_T5, _T9, _T6, 0, 0x2A))                     # slt t6, charges, ceiling
        b.branch(0x05, _T6, 0, "open_store")
        b.emit(0)
        b.emit(_i(0x0F, 0, _T8, 0x8000))                       # at the ceiling: 0x80
        b.emit(_r(_T7, _T8, _T7, 0, 0x25))
        b.label("open_store")
        b.emit(_i(0x2B, _T3, _T7, 0))
        b.emit(_i(0x09, _T3, _T3, 4))
        b.label("open_next")
        b.emit(_i(0x09, _T2, _T2, 4))
        b.branch(0x04, 0, 0, "open_loop")
        b.emit(0)
        b.label("open_done")
        b.emit(_i(0x2B, _T3, 0, 0))                            # terminator
        # marker + arm the constructor guard
        la(_T0, town_shop.SHOP_CORE_ADDRESS)
        b.emit(_i(0x09, 0, _T1, MENU_SHOP_MARKER))
        b.emit(_i(0x28, _T0, _T1, town_shop.ACTIVE_SHOP_OFFSET))
        b.emit(_i(0x09, 0, _T1, 1))
        b.emit(_i(0x28, _T0, _T1, town_shop.ARMED_MENU_OFFSET))
        # header and refusal pointers -> ours
        b.emit(_i(0x0F, 0, _T0, _hi(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        la(_T1, self.header_text)
        b.emit(_i(0x2B, _T0, _T1, _lo(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T0, _hi(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        la(_T1, self.refusal_text)
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        # the generic menu, buy shape (mode 0: prices through the resolver -> the price gate -> our price)
        b.emit(_r(0, 0, _A0, 0, 0x21))
        b.emit(_r(0, 0, _A1, 0, 0x21))
        b.emit(_i(0x09, 0, _A2, 2))
        la(_A3, self.catalog)
        b.emit(_j(0x03, town_shop.MENU_CONSTRUCTOR_ADDRESS), 0)
        b.emit(_i(0x0F, 0, _T0, _hi(self.handle)))
        b.emit(_i(0x2B, _T0, _V0, _lo(self.handle)))
        b.emit(_i(0x23, _SP, _RA, 0x1C), _i(0x09, _SP, _SP, 0x20))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- data: the cost table (halfwords) and the per-visit allowance table
        b.label("cost_table")
        costs = struct.pack(f"<{MAX_CHARGES}H", *CHARGE_COST)
        costs = costs.ljust((len(costs) + 3) // 4 * 4, b"\0")
        b.emit(*struct.unpack(f"<{len(costs) // 4}I", costs))
        b.label("uses_table")
        uses = bytes(USES_BY_LEVEL).ljust((len(USES_BY_LEVEL) + 3) // 4 * 4, b"\0")
        b.emit(*struct.unpack(f"<{len(uses) // 4}I", uses))

        code = b.build()
        self.addresses = {name: self.base + offset * 4 for name, offset in b.labels.items()}
        return code


def build_jump_table(natives: Natives) -> bytes:
    """`j price / nop / j guard / nop` at JUMP_TABLE_ADDRESS - what the slab's
    smith price gate and check-capacity gate jump to while ACTIVE_SHOP == 4."""

    return struct.pack(
        "<4I", _j(0x02, natives.addresses["price"]), 0, _j(0x02, natives.addresses["guard"]), 0
    )


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

def _text(text: str) -> bytes:
    return town_shop._encode_shop_name(text, max_characters=None)[:-1]


def _choice_gap(previous: str) -> bytes:
    cells = len(_text(previous)) // 2 + 2
    return bytes((0x81, 0x40)) * max(16 - cells, 2)


GREETING = "Fill your spheres?"
# The allowance, not a cap: what is left of THIS visit, so the number falls as
# she works and the last page she shows is the spent one.
CAP_LINE_HEAD, CAP_LINE_TAIL = "I can add ", " more this visit."
CHOICE_FILL, CHOICE_NOT_NOW = "Fill.", "Not now."
BYE_PAGE = ("Come back when they run dry.",)
NO_BALLS_PAGE = ("Bring me a spent sphere and I", "will fill it again.")
ASLEEP_PAGE = ("My art sleeps until the white", "sands find their way to me.")
SPENT_PAGE = ("I have given all I can today.", "Climb, and I will have more.")


def build_script(base: int, natives: Natives) -> tuple[bytes, dict[str, int]]:
    """The conversation as one byte string at `base`, plus every label's
    absolute address. The blacksmith's shape:

        start:   4C has_balls 3E 0F -> no_balls
                 4C allowance 3E 0F -> asleep
                 4C uses_left 3E 0F -> spent
                 57 <slot> "Fill your spheres?" 0A "I can add " 4C uses_left FD 0F
                     " more this visit." 0A
                 57 01 [Fill.] gap [Not now.] 2C 02 1A -> pick, bye
        pick:    08 15 <opener> 4C after_menu 01 01
        bye:     08 57 <slot> "Come back when they run dry." 11 01 01
        no_balls / asleep / spent: 57 <slot> <two rows> 11 01 01
        opener:  30 34 0E <own 16> 4C open 3E 0F <opener> 23 16

    The two level tests are distinct on purpose: `allowance` is zero only with
    no White Sand at all (she is asleep), while `uses_left` is zero when this
    visit's charges are used up (she asks for a climb).
    """

    script = bytearray()
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str]] = []

    def emit(*values: int) -> None:
        script.extend(value & 0xFF for value in values)

    def emit_ref(name: str) -> None:
        fixups.append((len(script), name))
        script.extend(bytes(4))

    def label(name: str) -> None:
        if name in labels:
            raise ValueError(f"duplicate ball-charger label {name}")
        labels[name] = len(script)

    def native(name: str) -> None:
        emit(0x4C)
        script.extend(struct.pack("<I", natives.addresses[name]))

    def text(value: str) -> None:
        script.extend(_text(value))

    label("start")
    native("has_balls")
    emit(0x3E, 0x0F)
    emit_ref("no_balls")
    native("allowance")
    emit(0x3E, 0x0F)
    emit_ref("asleep")
    native("uses_left")
    emit(0x3E, 0x0F)
    emit_ref("spent")
    emit(0x57, CHARGER_ACTOR_SLOT)
    text(GREETING)
    emit(0x0A)
    text(CAP_LINE_HEAD)
    native("uses_left")
    emit(0xFD, 0x0F)
    text(CAP_LINE_TAIL)
    emit(0x0A)
    emit(0x57, 0x01, 0x0B, 0x81, 0x6D)
    text(CHOICE_FILL)
    emit(0x81, 0x6E)
    script.extend(_choice_gap(CHOICE_FILL))
    emit(0x81, 0x6D)
    text(CHOICE_NOT_NOW)
    emit(0x81, 0x6E)
    emit(0x2C, 0x02, 0x1A)
    emit_ref("pick")
    emit_ref("bye")

    label("pick")
    emit(0x08, 0x15)
    emit_ref("opener")
    native("after_menu")
    emit(0x01, 0x01)

    label("bye")
    emit(0x08, 0x57, CHARGER_ACTOR_SLOT)
    text(BYE_PAGE[0])
    emit(0x11, 0x01, 0x01)

    for name, lines in (
        ("no_balls", NO_BALLS_PAGE),
        ("asleep", ASLEEP_PAGE),
        ("spent", SPENT_PAGE),
    ):
        label(name)
        emit(0x57, CHARGER_ACTOR_SLOT)
        text(lines[0])
        emit(0x0A)
        text(lines[1])
        emit(0x11, 0x01, 0x01)

    # The menu-opener stub, the six-opcode protocol copied verbatim
    # (docs/game/dialogue-scripts.md section 6).
    label("opener")
    emit(0x30, 0x34, 0x0E)
    emit_ref("opener_return")
    native("open")
    emit(0x3E, 0x0F)
    emit_ref("opener")
    emit(0x23)
    label("opener_return")
    emit(0x16)

    for offset, name in fixups:
        struct.pack_into("<I", script, offset, base + labels[name])
    return bytes(script), {name: base + offset for name, offset in labels.items()}


# ---------------------------------------------------------------------------
# Disc records (the overlay side; the dialogue image is laid out by fortune_teller)
# ---------------------------------------------------------------------------

def build_spawn_record() -> bytes:
    record = bytearray(CHARGER_RECORD_TEMPLATE)
    record[4] = CHARGER_FACING
    struct.pack_into("<I", record, 0x0C, CHARGER_ACTOR_TYPE)
    struct.pack_into("<hh", record, 0x10, *CHARGER_POSITION)
    return bytes(record)


def build_dialogue_table(teller_slot: int, teller_script: int, charger_script: int) -> bytes:
    """Two `(u16 key, u16 state index, u32 script)` rows and a zero terminator
    (the row search stops on a zero `+4` word). State index 0 for both: the
    scene's one flag triple, the same bookkeeping Shiela's row did."""

    return struct.pack("<HHI", teller_slot, 0, teller_script) + struct.pack(
        "<HHI", CHARGER_ACTOR_SLOT, 0, charger_script
    ) + bytes(8)
