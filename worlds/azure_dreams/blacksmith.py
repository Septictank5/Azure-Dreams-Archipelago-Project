"""The blacksmith: an NPC in the equipment shop who upgrades weapons and shields
for town money, up to a cap that ADAP's smithing level raises.

Account: `docs/systems/blacksmith.md` (sections 4-5 are the decoded talk path
and this recipe). Sibling machinery ridden before: `pool_house.py`.

**He takes over Ghosh's scene entry.** The equipment shop's scene table has
three entries - the door/prop run, Barry, and Ghosh - and Ghosh's is chosen by
a variant row array at `0x800189CC` whose row 0 is a "nobody" template gated
on `[0x80013188] == 0x15` (Ghosh's current town section). Rewriting that one
row to `(return_zero, smith_getter, Ghosh's template, flag 0)` makes it the
unconditional first pick, and the template's actor-type word (3 = Ghosh)
becomes a resident-sheet townsperson type. Everything else - the arena copy,
the talk routing by arena record, the getter call - is vanilla and unchanged.

Three homes, all loaded only while the shop is open:

* the shop OVERLAY (`TOWN.BIN+0x617000`, runtime `0x80016000`): the row, the
  template, and the 12-byte getter written over `ghosh_present_here_test`,
  which nothing references once row 0 stops pointing at it;
* the shop DIALOGUE image (`TOWN.BIN+0x61B000`, runtime `0x80018F08`): the
  natives (MIPS, two blocks) and the conversation script (two segments), all in
  spans `town_shop`'s trims made unreachable
  (`town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS`), plus the two menu strings;
* the ADSV journal: two bytes, the weapon and shield temper levels
  (`patch.PERSISTENT_WEAPON_TEMPER_LEVEL_OFFSET` / `_SHIELD_`), raised by the
  client per Red / Blue Sand received;
* and three seams of the town-mode slab / generic menu, keyed on the slab's
  ACTIVE_SHOP marker `town_shop.SMITH_MENU_SHOP_MARKER` (the smith price gate,
  the check-capacity gate, the buy-price resolver's fallback).

The conversation: with temperable gear in the bag, `Temper your gear?` and a
`[Temper.] [Not now.]` pair; `Temper.` opens the generic town menu over the
player's swords, shields and Trained Wand with the TEMPER COST in the price
column and `Temper (x button)` as the header row. **X on a row tempers it on
the spot** - `smith_guard` (reached through the check-capacity gate) debits
the cost, bumps the item's quality and its catalog row, and redraws the list;
rows at the cap are greyed (`0x80`); without the gold the vanilla refusal path
shows `[Not enough gold]`. O closes the menu and the conversation. Every
seam is restored by `smith_after_menu` (docs/systems/blacksmith.md section 5).
"""

from __future__ import annotations

import struct
from pathlib import Path

from . import patch, save_removal, town_shop
from .patch import _MipsBuilder, _i, _j, _r

# --- who he is ---------------------------------------------------------------
#
# A resident-sheet town actor type (docs/game/assets/town-npc-catalogue_resident.png):
# every frame draws from the town-resident half of the NPC pages, so he renders
# in the shop, which streams no NPC sheets of its own. The slot is the type's
# constructor slot (init_npc_actor's second argument) - the dialogue window's
# `57 <slot>` presentation byte and, in keyed entries, the table key. Only
# Barry's 0x10 is taken in this scene.
SMITH_ACTOR_TYPE = 0x10
SMITH_ACTOR_SLOT = 0x13
# Section-local, +Y screen-down, SECTION_BASE (512, 512): Ghosh's own spot,
# left of the carpet, known walkable and visible (global (736, 864); Koh
# enters at about (928, 964)).
SMITH_POSITION = (224, 352)
SMITH_FACING = 1  # down, like Barry

# --- the overlay edits -------------------------------------------------------
GHOSH_ROW0_ADDRESS = 0x8001_89CC
GHOSH_ROW0_ORIGINAL = (0x8001_64E0, 0x8001_82E8, 0x8001_8AB8, 0x0000_0000)
RETURN_ZERO_ADDRESS = 0x8001_82F4          # `jr ra / v0 = 0`
GHOSH_TEMPLATE_ADDRESS = 0x8001_8FAC       # on disc TOWN+0x619FAC (the dialogue image lands here later)
GHOSH_TEMPLATE_ORIGINAL = bytes.fromhex(
    "00400100" "01000000" "00000000" "03000000" "e0006001"  # Ghosh, type 3, (224, 352), facing 1
)
SPAWN_TERMINATOR_ORIGINAL = bytes.fromhex("00800000" "00000000" "00000000" "00000000" "00000000")
SMITH_GETTER_ADDRESS = 0x8001_64E0         # over ghosh_present_here_test (48 bytes)
SMITH_GETTER_ORIGINAL_PREFIX = bytes.fromhex("e8ffbd27" "1000b0af" "1400bfaf")  # addiu sp,-0x18 / sw s0 / sw ra

# --- the dialogue-image homes ------------------------------------------------
# Natives block A: the jump table the slab targets, then everything the A press
# needs (is_upgradable, price, cap, guard, has_equipment); block B: the opener
# and after_menu. Both in spans town_shop's trims freed; the script takes two
# more (docs/systems/blacksmith.md section 5).
NATIVE_BLOCK_ADDRESS = 0x8001_9884          # span 5, 664 B (the fifteenth-birthday chain)
NATIVE_BLOCK_END = 0x8001_9B1C
NATIVE_BLOCK_B_ADDRESS = 0x8001_9704        # span 4, word-aligned start (0x80019702 + 2), 340 B
NATIVE_BLOCK_B_END = 0x8001_9858
SCRIPT_ADDRESS = 0x8001_9414                # span 1, 347 B
SCRIPT_END = 0x8001_956F
SCRIPT_B_ADDRESS = 0x8001_91C1              # span 0, 315 B
SCRIPT_B_END = 0x8001_92FC

# --- what the natives touch --------------------------------------------------
CATALOG_BUFFER_ADDRESS = 0x8001_8AE8       # g_shop_catalog_buffer, zeroed 0x100 by every opener
CATALOG_LEADING_ENTRY = 0x0000_1601        # the menu's leading pseudo-entry word (category 0x16 id 1, the header row)
MENU_HANDLE_ADDRESS = 0x8001_8BE8          # g_shop_menu_handle
ZERO_BYTES_ADDRESS = 0x8001_6944           # zero_bytes(dst, n)
INVENTORY_ORDER_ADDRESS = 0x8001_029C      # g_inventory_order_pointers, null-terminated
TOWN_MONEY_ADDRESS = 0x8001_2D5C           # g_town_money, u32
WEAPON_LEVEL_ADDRESS = patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_WEAPON_TEMPER_LEVEL_OFFSET
SHIELD_LEVEL_ADDRESS = patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_SHIELD_TEMPER_LEVEL_OFFSET
ROW_BUILDER_ADDRESS = 0x800B_0FD4          # rebuilds the five visible rows of a list core (list widget + 0x20)
# The header row's name pointer and the A-press refusal message pointer, both
# town-mode overlay data the smith's opener repoints for the life of his menu
# and after_menu restores (docs/systems/nada-send.md 5.6, docs/game/shops.md 4a).
MENU_HEADER_POINTER_SLOT_ADDRESS = town_shop.MENU_HEADER_POINTER_SLOT_ADDRESS   # 0x800D479C -> "Pay  (△ button)"
VANILLA_MENU_HEADER_TEXT_ADDRESS = town_shop.VANILLA_MENU_HEADER_TEXT_ADDRESS   # 0x80089B48
REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS = 0x800D_1558                              # -> "[The bag is full]"
VANILLA_REFUSAL_MESSAGE_ADDRESS = 0x8008_91A8

# What he tempers: swords and shields, any; wands ONLY the Trained Wand
# (category 0x10 id 2) - quality on any other wand breaks mix magic, which is
# exactly what makes the Trained Wand special.
SWORD_CATEGORY, WAND_CATEGORY, SHIELD_CATEGORY = 0x0F, 0x10, 0x11
TRAINED_WAND_ID = 2

# The caps: two levels 0..3 -> +0 / +10 / +20 / +40, one for weapons (swords
# and the Trained Wand, raised by Red Sands) and one for shields (Blue Sands).
# A table, so the steps need not be even; +40 is the ceiling the price curve
# is tuned to.
CAP_BY_LEVEL = (0, 10, 20, 40)   # 2026-08-16: was 0/15/30/40; the user asked for 0/10/20/40
MAX_LEVEL = len(CAP_BY_LEVEL) - 1
MAX_QUALITY = CAP_BY_LEVEL[-1]

# Price of one +1 from quality q (clamped at 0): quadratic to the ceiling with
# the last step (+39 -> +40) costing exactly 500 and a floor of 1 gold:
#     cost(q) = max(1, round(500 * ((q + 1) / 40) ** 2)) = max(1, (5 * (q+1)^2 + 8) >> 4)
# +0->+1 costs 1, +9->+10 31, +19->+20 125, +29->+30 281; all forty steps
# together about 6.9k gold. Cheap early on purpose: death empties the bag, and
# the smith is the "back to decent power" relief.
FINAL_STEP_COST = 500


def temper_cost(quality: int) -> int:
    """The price of the next +1 for an item at `quality` (the native's exact arithmetic)."""

    q1 = max(quality, 0) + 1
    return max(1, (5 * q1 * q1 + 8) >> 4)


assert temper_cost(MAX_QUALITY - 1) == FINAL_STEP_COST
assert temper_cost(0) == 1 and temper_cost(-5) == 1
assert NATIVE_BLOCK_ADDRESS % 4 == 0
assert NATIVE_BLOCK_ADDRESS == town_shop.SMITH_NATIVE_TABLE_ADDRESS
assert (NATIVE_BLOCK_ADDRESS, NATIVE_BLOCK_END) == (
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[5][0],
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[5][1],
)
assert (SCRIPT_ADDRESS, SCRIPT_END) == (
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[1][0],
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[1][1],
)
assert (SCRIPT_B_ADDRESS, SCRIPT_B_END) == (
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[0][0],
    town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[0][1],
)
assert NATIVE_BLOCK_B_ADDRESS % 4 == 0
assert town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[4][0] <= NATIVE_BLOCK_B_ADDRESS
assert NATIVE_BLOCK_B_END == town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[4][1]

# The two strings the menu shows while it is his: the header row and the
# refusal message. Each in a small freed span of the dialogue image.
HEADER_TEXT_ADDRESS = town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[3][0]    # 0x8001969B, 61 B
REFUSAL_TEXT_ADDRESS = town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[2][0]   # 0x8001965E, 54 B


def build_header_text() -> bytes:
    """`Temper (x button)` in the vanilla header's own shape: full-width text,
    the x glyph (CP932 0x817E; the triangle is 0x81A2), a plain 0x20 before `button`."""

    return (
        town_shop._encode_shop_name("Temper (", max_characters=None)[:-1]
        + bytes((0x81, 0x7E, 0x20))
        + town_shop._encode_shop_name("button)", max_characters=None)
    )


def build_refusal_text() -> bytes:
    return town_shop._encode_shop_name("[Not enough gold]", max_characters=None)


assert HEADER_TEXT_ADDRESS + len(build_header_text()) <= town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[3][1]
assert REFUSAL_TEXT_ADDRESS + len(build_refusal_text()) <= town_shop.EQUIPMENT_DIALOGUE_FREE_SPANS[2][1]


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


class _Natives:
    """The smith's natives, assembled as two blocks behind a two-entry jump table.

    Block A (`code`, at `base`): +0 `j price` (the slab's SMITH_PRICE_ENTRY),
    +8 `j guard` (SMITH_GUARD_ENTRY), then is_upgradable, price, cap,
    has_equipment, the cap table and guard. Block B (`code_b`, at `base_b`):
    after_menu and open. Two passes: labels are only known after a first
    assembly, and the internal `jal`s need absolute targets. All routines use
    caller-saved registers only and pad every load delay.
    """

    ENTRIES = ("price", "guard", "is_upgradable", "cap_weapon", "cap_shield", "cap_for", "open", "has_equipment", "after_menu")

    def __init__(self, base: int, base_b: int) -> None:
        self.base = base
        self.base_b = base_b
        self.addresses: dict[str, int] = {}
        self.code, self.code_b = self._assemble()
        self.code, self.code_b = self._assemble()

    def _adr(self, name: str) -> int:
        return self.addresses.get(name, self.base)

    def _assemble(self) -> tuple[bytes, bytes]:
        b = _MipsBuilder()
        adr = self._adr

        # ---- the jump table the slab targets
        b.emit(_j(0x02, adr("price")), 0)
        b.emit(_j(0x02, adr("guard")), 0)

        # ---- is_upgradable(a0 = descriptor) -> v0. Leaf; clobbers v1, a1 only.
        b.label("is_upgradable")
        b.emit(_i(0x24, _A0, _V0, 1))                          # lbu v0, category
        b.emit(_i(0x24, _A0, _V1, 0))                          # lbu v1, id
        b.emit(_i(0x09, _V0, _V0, -SWORD_CATEGORY))            # v0 = category - 0x0F
        b.branch(0x04, _V0, 0, "upg_yes")                      # sword
        b.emit(_i(0x09, 0, _A1, SHIELD_CATEGORY - SWORD_CATEGORY))  # (delay) a1 = 2
        b.branch(0x04, _V0, _A1, "upg_yes")                    # shield
        b.emit(_i(0x09, 0, _A1, WAND_CATEGORY - SWORD_CATEGORY))    # (delay) a1 = 1
        b.branch(0x05, _V0, _A1, "upg_no")                     # not a wand either
        b.emit(_i(0x09, 0, _A1, TRAINED_WAND_ID))              # (delay) a1 = 2
        b.branch(0x05, _V1, _A1, "upg_no")                     # a wand that is not the Trained Wand
        b.emit(0)
        b.label("upg_yes")
        b.emit(_r(_RA, 0, 0, 0, 0x08), _i(0x09, 0, _V0, 1))    # jr ra / v0 = 1
        b.label("upg_no")
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21)) # jr ra / v0 = 0

        # ---- price(a0 = descriptor) -> v0 = temper cost. Leaf; clobbers t0-t3.
        b.label("price")
        b.emit(_i(0x20, _A0, _T0, 2))                          # lb t0, quality
        b.emit(0)
        b.emit(_r(_T0, 0, _T1, 0, 0x2A))                       # slt t1, t0, zero
        b.branch(0x04, _T1, 0, "price_q_ok")
        b.emit(0)
        b.emit(_r(0, 0, _T0, 0, 0x21))                         # t0 = 0
        b.label("price_q_ok")
        b.emit(_i(0x09, _T0, _T1, 1))                          # t1 = q + 1
        b.emit(_r(0, 0, _V0, 0, 0x21))                         # v0 = 0 (acc)
        b.emit(_r(_T1, 0, _T2, 0, 0x21))                       # t2 = q + 1 (count)
        b.label("price_square")
        b.emit(_r(_V0, _T1, _V0, 0, 0x21))                     # acc += q1
        b.emit(_i(0x09, _T2, _T2, -1))
        b.branch(0x05, _T2, 0, "price_square")
        b.emit(0)
        b.emit(_r(0, _V0, _T3, 2, 0x00))                       # t3 = acc << 2
        b.emit(_r(_V0, _T3, _V0, 0, 0x21))                     # acc * 5
        b.emit(_i(0x09, _V0, _V0, 8))                          # + 8
        b.emit(_r(0, _V0, _V0, 4, 0x02))                       # >> 4
        b.branch(0x05, _V0, 0, "price_done")
        b.emit(0)
        b.emit(_i(0x09, 0, _V0, 1))                            # floor 1
        b.label("price_done")
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- cap_weapon() / cap_shield() -> v0 = CAP_BY_LEVEL[min(level, MAX)];
        #      cap_for(a0 = descriptor) -> whichever the category wants.
        #      Leaves; clobber t0-t1, v1. One shared tail: t0 = the level byte's address.
        b.label("cap_weapon")
        b.emit(_i(0x0F, 0, _T0, _hi(WEAPON_LEVEL_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(WEAPON_LEVEL_ADDRESS)))
        b.branch(0x04, 0, 0, "cap_common")
        b.emit(0)
        b.label("cap_for")
        b.emit(_i(0x24, _A0, _V0, 1))                              # lbu v0, category
        b.emit(_i(0x09, 0, _V1, SHIELD_CATEGORY))                  # (delay) v1 = 0x11
        b.emit(_i(0x0F, 0, _T0, _hi(WEAPON_LEVEL_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(WEAPON_LEVEL_ADDRESS)))
        b.branch(0x05, _V0, _V1, "cap_common")                     # not a shield: weapon level
        b.emit(0)
        b.label("cap_shield")                                      # (cap_for falls through for shields)
        b.emit(_i(0x0F, 0, _T0, _hi(SHIELD_LEVEL_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(SHIELD_LEVEL_ADDRESS)))
        b.label("cap_common")
        b.emit(_i(0x24, _T0, _T0, 0))                              # lbu t0, level
        b.emit(_i(0x0F, 0, _T1, _hi(adr("cap_table"))))            # (delay) t1 = hi(table)
        b.emit(_i(0x0B, _T0, _V1, MAX_LEVEL + 1))                  # sltiu v1, t0, MAX+1
        b.branch(0x05, _V1, 0, "cap_in_range")
        b.emit(0)
        b.emit(_i(0x09, 0, _T0, MAX_LEVEL))
        b.label("cap_in_range")
        b.emit(_r(_T1, _T0, _T1, 0, 0x21))                         # t1 = hi + level
        b.emit(_i(0x24, _T1, _V0, _lo(adr("cap_table"))))          # lbu v0, table[level]
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- has_equipment() -> v0: any upgradable item in the bag
        b.label("has_equipment")
        b.emit(_i(0x09, _SP, _SP, -0x18), _i(0x2B, _SP, _RA, 0x14))
        b.emit(_i(0x0F, 0, _T4, _hi(INVENTORY_ORDER_ADDRESS)))
        b.emit(_i(0x09, _T4, _T4, _lo(INVENTORY_ORDER_ADDRESS)))
        b.label("has_loop")
        b.emit(_i(0x23, _T4, _A0, 0))                          # lw a0, order[i]
        b.emit(0)
        b.branch(0x04, _A0, 0, "has_none")
        b.emit(0)
        b.emit(_j(0x03, adr("is_upgradable")), 0)
        b.branch(0x05, _V0, 0, "has_done")                     # found one: v0 = 1
        b.emit(0)
        b.emit(_i(0x09, _T4, _T4, 4))
        b.branch(0x04, 0, 0, "has_loop")
        b.emit(0)
        b.label("has_none")
        b.emit(_r(0, 0, _V0, 0, 0x21))
        b.label("has_done")
        b.emit(_i(0x23, _SP, _RA, 0x14), _i(0x09, _SP, _SP, 0x18))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        # ---- guard(a0 = menu+0x20) -> v0: the A press. Purchase or refuse.
        # Frame: +0x18 entry, +0x1C cap, +0x20 a0, +0x24 ra.
        b.label("guard")
        b.emit(_i(0x09, _SP, _SP, -0x28), _i(0x2B, _SP, _RA, 0x24), _i(0x2B, _SP, _A0, 0x20))
        b.emit(_i(0x23, _A0, _T0, 4))                          # lw t0, selected row index
        b.emit(_i(0x23, _A0, _T1, 0x20))                       # lw t1, catalog base
        b.emit(_r(0, _T0, _T0, 2, 0x00))                       # t0 <<= 2
        b.emit(_r(_T1, _T0, _T2, 0, 0x21))                     # t2 = entry
        b.emit(_i(0x2B, _SP, _T2, 0x18))
        b.emit(_r(_T2, 0, _A0, 0, 0x21))                       # a0 = entry
        b.emit(_j(0x03, adr("cap_for")), 0)                    # v0 = the cap for this row's kind
        b.emit(_i(0x2B, _SP, _V0, 0x1C))                       # cap
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
        b.emit(_i(0x23, _SP, _A0, 0x20))
        b.emit(0)
        b.emit(_i(0x23, _A0, _T0, 4))                          # t0 = row index (1-based over the equipment)
        b.emit(_i(0x0F, 0, _T1, _hi(INVENTORY_ORDER_ADDRESS)))
        b.emit(_i(0x09, _T1, _T1, _lo(INVENTORY_ORDER_ADDRESS)))
        b.emit(_r(0, 0, _T4, 0, 0x21))                         # t4 = matches so far
        b.label("guard_walk")
        b.emit(_i(0x23, _T1, _T5, 0))                          # lw t5, order[i]
        b.emit(0)
        b.branch(0x04, _T5, 0, "guard_rebuild")                # ran out (should not happen)
        b.emit(_r(_T5, 0, _A0, 0, 0x21))                       # (delay) a0 = descriptor
        b.emit(_j(0x03, adr("is_upgradable")), 0)
        b.branch(0x04, _V0, 0, "guard_walk_next")
        b.emit(0)
        b.emit(_i(0x09, _T4, _T4, 1))
        b.branch(0x05, _T4, _T0, "guard_walk_next")            # not this row yet
        b.emit(0)
        # found: bump the item and its catalog row
        b.emit(_i(0x20, _T5, _T7, 2))                          # lb t7, quality
        b.emit(_i(0x23, _SP, _T2, 0x18))                       # (delay filler) t2 = entry
        b.emit(_i(0x09, _T7, _T7, 1))
        b.emit(_i(0x28, _T5, _T7, 2))                          # inventory quality + 1
        b.emit(_i(0x28, _T2, _T7, 2))                          # catalog row quality + 1
        b.emit(_i(0x23, _SP, _T3, 0x1C))                       # t3 = cap
        b.emit(_i(0x24, _T2, _T8, 3))                          # lbu t8, row flags
        b.emit(_r(_T7, _T3, _T9, 0, 0x2A))                     # slt t9, new quality, cap
        b.branch(0x04, _T9, 0, "guard_at_cap")
        b.emit(0)
        b.emit(_i(0x0D, _T8, _T8, 0x20))                       # pre-set 0x20: the toggle clears it
        b.branch(0x04, 0, 0, "guard_store")
        b.emit(0)
        b.label("guard_at_cap")
        # 0xA0, NOT 0x80. The A-press handler XORs 0x20 after this returns and
        # then recolours from the result (`recolour_shop_row`, 0x800B09EC):
        # 0x20 set means CHECKED, which paints the row green and stamps the
        # mode's BUY tag on it. Writing 0x80 alone leaves 0x20 clear, so the
        # toggle SETS it and the row that just hit its ceiling lights up as if
        # it were selected for purchase. Setting 0x20 here as well means the
        # toggle clears it and the row lands on 0x80 - grey and unselectable,
        # which is what "no more of this one" should look like.
        b.emit(_i(0x0D, _T8, _T8, 0xA0))                       # 0xA0: the toggle leaves 0x80
        b.label("guard_store")
        b.emit(_i(0x28, _T2, _T8, 3))
        b.branch(0x04, 0, 0, "guard_rebuild")
        b.emit(0)
        b.label("guard_walk_next")
        b.emit(_i(0x09, _T1, _T1, 4))
        b.branch(0x04, 0, 0, "guard_walk")
        b.emit(0)
        b.label("guard_rebuild")
        b.emit(_i(0x23, _SP, _A0, 0x20))                       # a0 = menu+0x20
        b.emit(0)
        b.emit(_i(0x23, _A0, _A0, 0x28))                       # a0 = list widget
        b.emit(0)
        b.emit(_i(0x09, _A0, _A0, 0x20))                       # a0 = list core
        b.emit(_j(0x03, ROW_BUILDER_ADDRESS), 0)               # redraw the five rows (name +N, price)
        b.emit(_i(0x23, _SP, _RA, 0x24), _i(0x09, _SP, _SP, 0x28))
        b.emit(_r(_RA, 0, 0, 0, 0x08), _i(0x09, 0, _V0, 1))    # v0 = 1: allowed (the toggle runs)

        # ---- data: the cap table (one byte per level, zero-padded to a word)
        b.label("cap_table")
        table = bytes(CAP_BY_LEVEL).ljust((len(CAP_BY_LEVEL) + 3) // 4 * 4, b"\0")
        b.emit(*struct.unpack(f"<{len(table) // 4}I", table))


        code_a = b.build()
        labels_a = dict(b.labels)

        # ================= block B =================
        b = _MipsBuilder()

        # ---- after_menu() -> 0: restore the two pointers, drop the marker
        b.label("after_menu")
        b.emit(_i(0x0F, 0, _T0, _hi(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T1, _hi(VANILLA_MENU_HEADER_TEXT_ADDRESS)))
        b.emit(_i(0x09, _T1, _T1, _lo(VANILLA_MENU_HEADER_TEXT_ADDRESS)))
        b.emit(_i(0x2B, _T0, _T1, _lo(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T0, _hi(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T1, _hi(VANILLA_REFUSAL_MESSAGE_ADDRESS)))
        b.emit(_i(0x09, _T1, _T1, _lo(VANILLA_REFUSAL_MESSAGE_ADDRESS)))
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T0, _hi(town_shop.SHOP_CORE_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(town_shop.SHOP_CORE_ADDRESS)))
        b.emit(_i(0x09, 0, _T1, 0xFF))
        b.emit(_i(0x28, _T0, _T1, town_shop.ACTIVE_SHOP_OFFSET))  # ACTIVE_SHOP = 0xFF
        b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, 0, _V0, 0, 0x21))

        # ---- open() -> v0 = menu handle (0 = retry, per the opener protocol)
        b.label("open")
        b.emit(_i(0x09, _SP, _SP, -0x20), _i(0x2B, _SP, _RA, 0x1C))
        b.emit(_i(0x0F, 0, _A0, _hi(CATALOG_BUFFER_ADDRESS)))
        b.emit(_i(0x09, _A0, _A0, _lo(CATALOG_BUFFER_ADDRESS)))
        b.emit(_j(0x03, ZERO_BYTES_ADDRESS))
        b.emit(_i(0x09, 0, _A1, 0x100))                        # (delay) a1 = 0x100
        b.emit(_i(0x0F, 0, _T0, _hi(CATALOG_BUFFER_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(CATALOG_BUFFER_ADDRESS)))
        b.emit(_i(0x09, 0, _T1, CATALOG_LEADING_ENTRY))
        b.emit(_i(0x2B, _T0, _T1, 0))                          # the header row
        b.emit(_i(0x09, _T0, _T3, 4))                          # t3 = catalog cursor
        b.emit(_i(0x0F, 0, _T2, _hi(INVENTORY_ORDER_ADDRESS)))
        b.emit(_i(0x09, _T2, _T2, _lo(INVENTORY_ORDER_ADDRESS)))
        b.label("open_loop")
        b.emit(_i(0x23, _T2, _T4, 0))                          # lw t4, order[i]
        b.emit(0)
        b.branch(0x04, _T4, 0, "open_done")
        b.emit(_r(_T4, 0, _A0, 0, 0x21))                       # (delay) a0 = descriptor
        b.emit(_j(0x03, adr("is_upgradable")), 0)
        b.branch(0x04, _V0, 0, "open_next")
        b.emit(_r(_T4, 0, _A0, 0, 0x21))                       # (delay) a0 = descriptor
        b.emit(_j(0x03, adr("cap_for")), 0)                    # v0 = this item's cap (clobbers t0/t1, v1)
        b.emit(_r(_V0, 0, _T9, 0, 0x21))                       # t9 = cap
        b.emit(_i(0x23, _T4, _T7, 0))                          # lw t7, descriptor word
        b.emit(_i(0x0F, 0, _T8, 0x00FF))
        b.emit(_i(0x0D, _T8, _T8, 0xFFFF))
        b.emit(_r(_T7, _T8, _T7, 0, 0x24))                     # clear the flags byte
        b.emit(_i(0x20, _T4, _T5, 2))                          # lb t5, quality
        b.emit(0)
        b.emit(_r(_T5, _T9, _T6, 0, 0x2A))                     # slt t6, quality, cap
        b.branch(0x05, _T6, 0, "open_store")
        b.emit(0)
        b.emit(_i(0x0F, 0, _T8, 0x8000))                       # at the cap: 0x80
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
        b.emit(_i(0x0F, 0, _T0, _hi(town_shop.SHOP_CORE_ADDRESS)))
        b.emit(_i(0x09, _T0, _T0, _lo(town_shop.SHOP_CORE_ADDRESS)))
        b.emit(_i(0x09, 0, _T1, town_shop.SMITH_MENU_SHOP_MARKER))
        b.emit(_i(0x28, _T0, _T1, town_shop.ACTIVE_SHOP_OFFSET))
        b.emit(_i(0x09, 0, _T1, 1))
        b.emit(_i(0x28, _T0, _T1, town_shop.ARMED_MENU_OFFSET))
        # header and refusal pointers -> ours
        b.emit(_i(0x0F, 0, _T0, _hi(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T1, _hi(HEADER_TEXT_ADDRESS)))
        b.emit(_i(0x09, _T1, _T1, _lo(HEADER_TEXT_ADDRESS)))
        b.emit(_i(0x2B, _T0, _T1, _lo(MENU_HEADER_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T0, _hi(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        b.emit(_i(0x0F, 0, _T1, _hi(REFUSAL_TEXT_ADDRESS)))
        b.emit(_i(0x09, _T1, _T1, _lo(REFUSAL_TEXT_ADDRESS)))
        b.emit(_i(0x2B, _T0, _T1, _lo(REFUSAL_MESSAGE_POINTER_SLOT_ADDRESS)))
        # the generic menu, buy shape (mode 0: prices through the resolver -> smith price gate)
        b.emit(_r(0, 0, _A0, 0, 0x21))
        b.emit(_r(0, 0, _A1, 0, 0x21))
        b.emit(_i(0x09, 0, _A2, 2))
        b.emit(_i(0x0F, 0, _A3, _hi(CATALOG_BUFFER_ADDRESS)))
        b.emit(_i(0x09, _A3, _A3, _lo(CATALOG_BUFFER_ADDRESS)))
        b.emit(_j(0x03, town_shop.MENU_CONSTRUCTOR_ADDRESS), 0)
        b.emit(_i(0x0F, 0, _T0, _hi(MENU_HANDLE_ADDRESS)))
        b.emit(_i(0x2B, _T0, _V0, _lo(MENU_HANDLE_ADDRESS)))
        b.emit(_i(0x23, _SP, _RA, 0x1C), _i(0x09, _SP, _SP, 0x20))
        b.emit(_r(_RA, 0, 0, 0, 0x08), 0)

        code_b = b.build()
        self.addresses = {name: self.base + offset * 4 for name, offset in labels_a.items()}
        self.addresses.update({name: self.base_b + offset * 4 for name, offset in b.labels.items()})
        return code_a, code_b


def build_natives() -> _Natives:
    natives = _Natives(NATIVE_BLOCK_ADDRESS, NATIVE_BLOCK_B_ADDRESS)
    for name, base, code, end in (
        ("A", NATIVE_BLOCK_ADDRESS, natives.code, NATIVE_BLOCK_END),
        ("B", NATIVE_BLOCK_B_ADDRESS, natives.code_b, NATIVE_BLOCK_B_END),
    ):
        if base + len(code) > end:
            raise ValueError(
                f"Blacksmith natives block {name} is {len(code)} bytes; its span holds {end - base}."
            )
    return natives


def build_getter() -> bytes:
    """`lui v0,hi; jr ra; addiu v0,v0,lo` - returns the script address."""

    return struct.pack(
        "<III",
        _i(0x0F, 0, _V0, _hi(SCRIPT_ADDRESS)),
        _r(_RA, 0, 0, 0, 0x08),
        _i(0x09, _V0, _V0, _lo(SCRIPT_ADDRESS)),
    )


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

def _text(text: str) -> bytes:
    return town_shop._encode_shop_name(text, max_characters=None)[:-1]


def _choice_gap(previous: str) -> bytes:
    """docs/game/dialogue-scripts.md section 6: column 0 plus the gap pads to 16 cells."""

    cells = len(_text(previous)) // 2 + 2
    return bytes((0x81, 0x40)) * max(16 - cells, 2)


SCRIPT_SEGMENTS = (
    # (start, end exclusive): span 1 for the greeting/choice/close, span 0 for
    # the two refusal pages and the opener stub.
    (SCRIPT_ADDRESS, SCRIPT_END),
    (SCRIPT_B_ADDRESS, SCRIPT_B_END),
)


def build_script(natives: _Natives | None = None) -> tuple[tuple[int, bytes], ...]:
    """The conversation, as (runtime address, bytes) segments.

    Branch targets are absolute, so the script can straddle free spans; the
    first segment starts at SCRIPT_ADDRESS (what the getter returns) and each
    `segment()` call continues in the next span.

        start:      4C has_equipment 3E 0F -> no_gear
                    57 <slot> "Temper your gear?" 0A
                    "Weapon max +" 4C cap_weapon FD 0F ", shield max +" 4C cap_shield FD 0F 0A
                    57 01 [Temper.] gap [Not now.] 2C 02 1A -> pick, bye
        pick:       08 15 <opener> 4C after_menu 01 01     (silent close after the menu)
        bye:        08 57 <slot> "Come back any time." 11 01 01
        no_gear:    57 <slot> "Bring me a sword, wand or" 0A "shield and I will temper it." 11 01 01
        opener:     30 34 0E <own 16> 4C open 3E 0F <opener> 23 16
    """

    natives = natives or build_natives()
    segments: list[bytearray] = []
    labels: dict[str, tuple[int, int]] = {}
    fixups: list[tuple[int, int, str]] = []
    current = -1

    def segment() -> None:
        nonlocal current
        current += 1
        if current >= len(SCRIPT_SEGMENTS):
            raise ValueError("The blacksmith script needs more free spans than it has.")
        segments.append(bytearray())

    def emit(*values: int) -> None:
        segments[current].extend(value & 0xFF for value in values)

    def emit_ref(name: str) -> None:
        fixups.append((current, len(segments[current]), name))
        segments[current].extend(bytes(4))

    def label(name: str) -> None:
        if name in labels:
            raise ValueError(f"duplicate label {name}")
        labels[name] = (current, len(segments[current]))

    def native(address: int) -> None:
        emit(0x4C)
        segments[current].extend(struct.pack("<I", address))

    def text(value: str) -> None:
        segments[current].extend(_text(value))

    def choice_pair(first: str, second: str, targets: tuple[str, str]) -> None:
        emit(0x57, 0x01, 0x0B, 0x81, 0x6D)
        text(first)
        emit(0x81, 0x6E)
        segments[current].extend(_choice_gap(first))
        emit(0x81, 0x6D)
        text(second)
        emit(0x81, 0x6E)
        emit(0x2C, 0x02, 0x1A)
        for target in targets:
            emit_ref(target)

    segment()
    label("start")
    native(natives.addresses["has_equipment"])
    emit(0x3E, 0x0F)
    emit_ref("no_gear")
    emit(0x57, SMITH_ACTOR_SLOT)
    text("Temper your gear?")
    emit(0x0A)
    # "Weapon max +XX, shield max +YY": each number is a native's return
    # rendered by FD 0F (the way the shop prints "That'll be <num>G").
    text("Weapon max +")
    native(natives.addresses["cap_weapon"])
    emit(0xFD, 0x0F)
    text(", shield max +")
    native(natives.addresses["cap_shield"])
    emit(0xFD, 0x0F)
    emit(0x0A)
    choice_pair("Temper.", "Not now.", ("pick", "bye"))

    label("pick")
    emit(0x08, 0x15)
    emit_ref("opener")
    native(natives.addresses["after_menu"])
    emit(0x01, 0x01)

    label("bye")
    emit(0x08, 0x57, SMITH_ACTOR_SLOT)
    text("Come back any time.")
    emit(0x11, 0x01, 0x01)

    segment()
    label("no_gear")
    emit(0x57, SMITH_ACTOR_SLOT)
    text("Bring me a sword, wand or")
    emit(0x0A)
    text("shield and I will temper it.")
    emit(0x11, 0x01, 0x01)

    # The menu-opener stub, the six-opcode protocol copied verbatim
    # (docs/game/dialogue-scripts.md section 6): yield, stage slot 0x0E with
    # the stub's own return address, call the opener, retry while it returns
    # 0, 0x23, return.
    label("opener")
    emit(0x30, 0x34, 0x0E)
    emit_ref("opener_return")
    native(natives.addresses["open"])
    emit(0x3E, 0x0F)
    emit_ref("opener")
    emit(0x23)
    label("opener_return")
    emit(0x16)

    def address_of(name: str) -> int:
        seg, offset = labels[name]
        return SCRIPT_SEGMENTS[seg][0] + offset

    for seg, offset, name in fixups:
        struct.pack_into("<I", segments[seg], offset, address_of(name))
    for index, body in enumerate(segments):
        start, end = SCRIPT_SEGMENTS[index]
        if start + len(body) > end:
            raise ValueError(
                f"Blacksmith script segment {index} is {len(body)} bytes; its span "
                f"holds {end - start}."
            )
    return tuple((SCRIPT_SEGMENTS[i][0], bytes(body)) for i, body in enumerate(segments))


# ---------------------------------------------------------------------------
# Disc records
# ---------------------------------------------------------------------------

def build_spawn_record() -> bytes:
    """Ghosh's record with the smith's type, position and facing; the +4 word's
    low byte is the facing, the rest of the record is copied."""

    record = bytearray(GHOSH_TEMPLATE_ORIGINAL)
    record[4] = SMITH_FACING
    struct.pack_into("<I", record, 0x0C, SMITH_ACTOR_TYPE)
    struct.pack_into("<hh", record, 0x10, *SMITH_POSITION)
    return bytes(record)


def iter_overlay_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN file offset, bytes) inside the shop overlay."""

    row = struct.pack(
        "<IIII", RETURN_ZERO_ADDRESS, SMITH_GETTER_ADDRESS, GHOSH_TEMPLATE_ADDRESS, 0
    )
    return (
        (town_shop.equipment_runtime_to_file_offset(GHOSH_ROW0_ADDRESS), row),
        (town_shop.equipment_runtime_to_file_offset(GHOSH_TEMPLATE_ADDRESS), build_spawn_record()),
        (town_shop.equipment_runtime_to_file_offset(SMITH_GETTER_ADDRESS), build_getter()),
    )


def iter_dialogue_file_patches() -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN file offset, bytes) inside the shop dialogue image."""

    natives = build_natives()
    return (
        (town_shop.equipment_dialogue_runtime_to_file_offset(NATIVE_BLOCK_ADDRESS), natives.code),
        (town_shop.equipment_dialogue_runtime_to_file_offset(NATIVE_BLOCK_B_ADDRESS), natives.code_b),
        (town_shop.equipment_dialogue_runtime_to_file_offset(HEADER_TEXT_ADDRESS), build_header_text()),
        (town_shop.equipment_dialogue_runtime_to_file_offset(REFUSAL_TEXT_ADDRESS), build_refusal_text()),
        *(
            (town_shop.equipment_dialogue_runtime_to_file_offset(address), body)
            for address, body in build_script(natives)
        ),
    )


def iter_blacksmith_raw_patches() -> tuple[tuple[int, bytes], ...]:
    return save_removal._iter_mode2_raw_patches(
        town_shop.TOWN_FILE_START_LBA,
        iter_overlay_file_patches() + iter_dialogue_file_patches(),
    )


def append_blacksmith_ppf_records(ppf: bytearray) -> None:
    for raw_offset, data in iter_blacksmith_raw_patches():
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)


def _town_bin_path() -> Path:
    return Path(__file__).resolve().parents[3] / "extracted" / "TOWN.BIN"
