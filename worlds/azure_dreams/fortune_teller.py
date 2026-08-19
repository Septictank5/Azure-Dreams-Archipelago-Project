"""The fortune teller: Mademoiselle Shiela reads the tower for gold.

Account: `docs/systems/fortune-teller.md` (section 1 is the decoded scene,
section 2 the hint model, section 3 this recipe). Sibling machinery ridden
before: `blacksmith.py` (natives + a conversation in an interior's dialogue
image), `pool_house.py` (spawn records).

**What she does.** For 1000 gold she looks at ONE tower floor that still holds
un-collected Archipelago checks and describes, one page per check, what kind
of thing is waiting there - vaguely, in her own idiom, never by name: a herb
is "a leaf, green and bitter", a ball is "a sphere within my sphere,
reflections without end", another player's item is "something not of this
world". She offers the lowest `HINT_FLOOR_CHOICES` floors with anything left
(`Look. / Higher. / Not now.`), and tells the third check apart from the two
ground checks ("Upon the cold stone floor," / "Clutched by a restless
beast,") because slot 2 is the carrier monster, not an item lying about.

**Where the truth comes from.**

* *Which checks are still open* is the ADSV tower journal
  (`patch.PERSISTENT_STATE_ADDRESS + PERSISTENT_LOCATION_MASK_OFFSET`, one byte per
  floor, bit = slot; `docs/systems/third-floor-check.md` section 2). Resident,
  save-backed, written by the collect hook and merged by the client - the one
  read a town NPC needs.
* *What kind of thing each check is* exists nowhere in the game: every marker
  renders as the same gift ("Strange...") by design, and the per-floor text
  bank is only paged in during a tower floor. So the generator writes a
  **hint-class table** - one byte per tower location, `HINT_CLASS_*` - into
  the fortune teller's own dialogue image on the disc. That image is loaded
  only inside her building, so the table costs no resident RAM anywhere; it is
  per-seed data in a per-player ppf, exactly like the shop manifests.

**Homes.** Everything lives in the dialogue image (bundle 32,
`TOWN.BIN+0x62BFC0`, runtime `0x80017668`), which is 45 KB of her vanilla
fortune quiz reachable ONLY through her one dialogue-table row (key `0x4F`,
script `0x800176D0`): the natives (MIPS, leaf routines), the class-page
pointer table, the per-seed class table, a 16-byte state block, and the whole
conversation. Nothing in the overlay is edited. The vanilla quiz bytes beyond
what we use are left in place, unreachable.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence

from . import ball_charger, item_manifest, items, patch, save_removal, town_shop
from .patch import _MipsBuilder, _i, _r

# --- the scene (docs/systems/fortune-teller.md section 1) ---------------------
#
# Scene 0x17. Overlay TOWN.BIN+0x629000 -> 0x80016000 (three sectors); the
# dialogue image (record 32, TOWN+0x62A800, image at +0x17C0 like the pool's)
# lands at 0x80017668 and runs to 0x800226A8. Her one dialogue-table row is at
# overlay 0x80017408: key 0x4F (her actor slot), script 0x800176D0. The scene
# table entry 1 (0x8001743C) has a single variant row (0x80017410) whose
# template run 0x800176FC = [type 0x6A @ (512,280) facing 1, prop 0x5D @
# (512,368) carrying her slot 0x4F in its +4 word] - two spare terminators
# follow it (0x80017724, 0x80017738): room for one more record.
OVERLAY_FILE_OFFSET = 0x62_9000
OVERLAY_RUNTIME_ADDRESS = 0x8001_6000
OVERLAY_SIZE = 0x1800
DIALOGUE_FILE_OFFSET = 0x62_BFC0
DIALOGUE_RUNTIME_ADDRESS = 0x8001_7668
DIALOGUE_RUNTIME_END = 0x8002_26A8
DIALOGUE_SCRATCH_ADDRESS = 0x8002_2364      # 836 zero bytes the overlay's entry 1 references (+0xC); untouched
SCRIPT_ENTRY_ADDRESS = 0x8001_76D0          # what her dialogue-table row points at
TELLER_ACTOR_SLOT = 0x4F                    # her `57` presentation byte and table key
TELLER_ACTOR_TYPE = 0x6A
TELLER_TEMPLATE_ADDRESS = 0x8001_76FC       # overlay bytes (TOWN+0x62A6FC); read only
TELLER_POSITION = (512, 280)                # section-local, base (512, 512)
# The two overlay bytes-worth we do edit (the ball charger, section 5 of the
# doc): the run's terminator becomes her record (the spare terminator after it
# closes the run), and the getter's `addiu a0,a0,0x7408` - the dialogue table
# pointer - is repointed at the two-row table in the dialogue image.
CHARGER_RECORD_ADDRESS = TELLER_TEMPLATE_ADDRESS + 40         # 0x80017724, was the terminator
OVERLAY_TABLE_ADDIU_ADDRESS = 0x8001_60D8
OVERLAY_TABLE_ADDIU_ORIGINAL = 0x2484_7408                   # addiu a0,a0,0x7408
VANILLA_DIALOGUE_TABLE_ADDRESS = 0x8001_7408
DIALOGUE_TABLE_SIZE = 24                                     # two rows + terminator

# The vanilla script text her entry point opens with, pinned by the tests
# against the disc so a wrong image base fails the build instead of shipping.
VANILLA_ENTRY_TEXT = "Welcome to the Fortune Telling"

# Our region of the image: from her entry point to the scene scratch. Laid out
# by `build_layout()`; the build asserts it fits.
REGION_START = SCRIPT_ENTRY_ADDRESS
REGION_END = DIALOGUE_SCRATCH_ADDRESS

# --- what the natives touch ---------------------------------------------------
JOURNAL_ADDRESS = patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_LOCATION_MASK_OFFSET
JOURNAL_FLOORS = patch.PERSISTENT_TOWER_MASK_FLOORS      # 39
SLOTS_PER_FLOOR = patch.MARKER_SLOT_COUNT                 # 3
CARRIER_SLOT = patch.MARKER_CARRIER_SLOT                  # 2
ADSV_MAGIC_ADDRESS = patch.PERSISTENT_STATE_ADDRESS
ADSV_MAGIC = patch.PERSISTENT_STATE_MAGIC
TOWN_MONEY_ADDRESS = 0x8001_2D5C                          # g_town_money, u32 (the blacksmith debits it too)

HINT_PRICE = 1000
HINT_FLOOR_CHOICES = 3       # the lowest N floors with anything left are offered
LOCATION_COUNT = patch.LOCATION_COUNT
assert LOCATION_COUNT == JOURNAL_FLOORS * SLOTS_PER_FLOOR
assert HINT_PRICE < 0x8000 and HINT_FLOOR_CHOICES <= 3

# --- hint classes: one byte per tower location -------------------------------
#
# What she "sees". Native categories keep their own numbers where they have
# one so a reader of the table can recognise them; the AP-only kinds follow.
HINT_CLASS_UNKNOWN = 0
HINT_CLASS_HERB = 1
HINT_CLASS_FRUIT = 2
HINT_CLASS_SEED = 3
HINT_CLASS_BALL = 4
HINT_CLASS_SCROLL = 5
HINT_CLASS_CRYSTAL = 6
HINT_CLASS_BELL = 7
HINT_CLASS_GLASSES = 8
HINT_CLASS_LOUPE = 9
HINT_CLASS_RED_SAND = 10
HINT_CLASS_BLUE_SAND = 11
HINT_CLASS_WHITE_SAND = 12
HINT_CLASS_SPECIAL = 13
HINT_CLASS_SWORD = 14
HINT_CLASS_WAND = 15
HINT_CLASS_SHIELD = 16
HINT_CLASS_EGG = 17
HINT_CLASS_KEYCARD = 18
HINT_CLASS_GOLD = 19
HINT_CLASS_SEND_TOKEN = 20
HINT_CLASS_TRAP = 21               # this world's own trap (wears a keycard's face in-game)
HINT_CLASS_REMOTE = 22             # another player's item
HINT_CLASS_REMOTE_PROGRESSION = 23 # ...that their world's logic needs
HINT_CLASS_REMOTE_TRAP = 24        # ...that is a trap for them
HINT_CLASS_COUNT = 25

_NATIVE_CATEGORY_TO_CLASS = {
    1: HINT_CLASS_HERB,
    2: HINT_CLASS_FRUIT,
    3: HINT_CLASS_SEED,
    item_manifest.BALL_CATEGORY: HINT_CLASS_BALL,
    5: HINT_CLASS_SCROLL,
    6: HINT_CLASS_CRYSTAL,
    7: HINT_CLASS_BELL,
    8: HINT_CLASS_GLASSES,
    9: HINT_CLASS_LOUPE,
    12: HINT_CLASS_SPECIAL,
    item_manifest.SWORD_CATEGORY: HINT_CLASS_SWORD,
    item_manifest.WAND_CATEGORY: HINT_CLASS_WAND,
    item_manifest.SHIELD_CATEGORY: HINT_CLASS_SHIELD,
    item_manifest.EGG_CATEGORY: HINT_CLASS_EGG,
}
_SAND_ID_TO_CLASS = {1: HINT_CLASS_RED_SAND, 2: HINT_CLASS_BLUE_SAND, 3: HINT_CLASS_WHITE_SAND}


def hint_class_for_item(item, own_player: int) -> int:
    """The class byte for an Archipelago `Item` placed at a tower location.

    Another player's item is REMOTE (progression / trap variants by its
    classification); this world's own trap is TRAP (the crystal shows the
    keycard disguise, but the mist around it is dark - the reading warns
    without naming); the AP-only kinds by name; a native reward by its
    descriptor's category (sands by id). Anything else is UNKNOWN, which has
    its own page, so the table can never send the script somewhere it is not.
    """

    if item.player != own_player:
        if item.trap:
            return HINT_CLASS_REMOTE_TRAP
        if item.advancement:
            return HINT_CLASS_REMOTE_PROGRESSION
        return HINT_CLASS_REMOTE
    return hint_class_for_own_item_name(item.name)


def hint_class_for_own_item_name(name: str) -> int:
    if items.is_trap_name(name):
        return HINT_CLASS_TRAP
    if name == items.PROGRESSIVE_KEYCARD:
        return HINT_CLASS_KEYCARD
    if name == items.GOLD_PACKAGE:
        return HINT_CLASS_GOLD
    if name == items.SEND_TOKEN:
        return HINT_CLASS_SEND_TOKEN
    reward = item_manifest.REWARD_BY_NAME.get(name)
    if reward is None:
        return HINT_CLASS_UNKNOWN
    if reward.category == item_manifest.SAND_CATEGORY:
        return _SAND_ID_TO_CLASS.get(reward.native_item_id, HINT_CLASS_UNKNOWN)
    return _NATIVE_CATEGORY_TO_CLASS.get(reward.category, HINT_CLASS_UNKNOWN)


# --- her lines ----------------------------------------------------------------
#
# The window here shows three rows; vanilla's longest row in this building is
# 31 full-width characters ("I'm even giving away a prize of"), so every prose
# line is capped at LINE_LIMIT and every page at three rows. Apostrophes are
# the game's own 0x8166 and "..." is the single 0x8163 glyph, both counted as
# one cell.
LINE_LIMIT = 31

# What she says before the class line, per where the check is: two ground
# slots and the carrier. One row, then the class's two rows.
WHERE_GROUND = "Upon the cold stone floor,"
WHERE_CARRIER = "Clutched by a restless beast,"

# Two rows each; the page is `<where> / <line 1> / <line 2>`.
CLASS_LINES: dict[int, tuple[str, str]] = {
    HINT_CLASS_UNKNOWN: ("something the mist will not", "let me name. Curious."),
    HINT_CLASS_HERB: ("a leaf, green and bitter, that", "mends what the tower breaks."),
    HINT_CLASS_FRUIT: ("something round and sweet,", "ripe, and eager to be eaten."),
    HINT_CLASS_SEED: ("a tiny thing, dreaming of the", "tree it will one day become."),
    HINT_CLASS_BALL: ("a sphere within my sphere... I", "see reflections without end."),
    HINT_CLASS_SCROLL: ("words rolled up tight, waiting", "for the one who unrolls them."),
    HINT_CLASS_CRYSTAL: ("a shard that hums with the", "voice of an element."),
    HINT_CLASS_BELL: ("a bell that longs to be rung,", "and something listens for it."),
    HINT_CLASS_GLASSES: ("two eyes that are not eyes.", "Through them, all is revealed."),
    HINT_CLASS_LOUPE: ("a single glass eye. It sees", "what the naked eye cannot."),
    HINT_CLASS_RED_SAND: ("grains red as the forge fire,", "restless in a smith's hand."),
    HINT_CLASS_BLUE_SAND: ("grains blue as a calm sea,", "restless in a smith's hand."),
    HINT_CLASS_WHITE_SAND: ("grains white as bone, that", "hunger for a spent sphere."),
    HINT_CLASS_SPECIAL: ("something rare and strange, not", "born of this tower at all."),
    HINT_CLASS_SWORD: ("a keen edge, thirsty for the", "hand that will wield it."),
    HINT_CLASS_WAND: ("a slender rod, humming with a", "will that is not its own."),
    HINT_CLASS_SHIELD: ("a broad face that turns harm", "aside. It waits for an arm."),
    HINT_CLASS_EGG: ("a shell, and inside it, a", "small heartbeat. Patient."),
    HINT_CLASS_KEYCARD: ("a thin card, cold to the touch.", "Locked doors dream of it."),
    HINT_CLASS_GOLD: ("the glint of gold. A great deal", "of it, if my eyes are honest."),
    HINT_CLASS_SEND_TOKEN: ("a token that lets a gift cross", "any distance in a heartbeat."),
    HINT_CLASS_TRAP: ("a thin card, cold to the touch...", "but the mist around it is dark."),
    HINT_CLASS_REMOTE: ("something not of this world. It", "belongs to another's story."),
    HINT_CLASS_REMOTE_PROGRESSION: ("something not of this world. A", "stranger's fate turns on it."),
    HINT_CLASS_REMOTE_TRAP: ("something not of this world...", "and it wishes someone ill."),
}
assert set(CLASS_LINES) == set(range(HINT_CLASS_COUNT))

GREETING = ("Welcome, child. Mademoiselle", "Shiela sees what the tower", "keeps from you.")
PRICE_PAGE = ("For 1000 gold my crystal will", "show you one floor's forgotten", "things.")
OFFER_LINE = "Floor "        # + FD 0F + OFFER_TAIL, then the choice rows
OFFER_TAIL = ", child?"
CHOICE_LOOK, CHOICE_HIGHER, CHOICE_NOT_NOW = "Look.", "Higher.", "Not now."
READ_PAGE = ("I gaze into the crystal...", "Floor ")     # + FD 0F + READ_TAIL
READ_TAIL = " shows itself to me."
DONE_PAGE = ("That is all the crystal shows", "me. Go carefully, child.")
POOR_PAGE = ("Your purse is too light for my", "sight. 1000 gold, no less.")
NO_MORE_PAGE = ("My sight reaches no higher", "today. Come again.")
NOTHING_PAGE = ("The crystal is dark... the tower", "keeps nothing more from you.")
BYE_PAGE = ("Come back when fate calls.",)
# With the hint system OFF but the ball charger on, her quiz bytes are still
# overwritten by the charger's pieces, so she needs *a* script: one page.
CLOSED_PAGE = ("The mists are still today.", "I see nothing in the crystal.")


def _text(text: str) -> bytes:
    """Full-width CP932 through the shop encoder, with the game's own
    apostrophe (0x8166) and ellipsis (0x8163) glyphs."""

    out = bytearray()
    index = 0
    while index < len(text):
        if text.startswith("...", index):
            out.extend((0x81, 0x63))
            index += 3
            continue
        character = text[index]
        if character == "'":
            out.extend((0x81, 0x66))
        else:
            out.extend(town_shop._encode_shop_name(character, max_characters=None)[:-1])
        index += 1
    return bytes(out)


def _cells(text: str) -> int:
    return len(_text(text)) // 2


def _check_line(text: str) -> str:
    if _cells(text) > LINE_LIMIT:
        raise ValueError(f"Fortune-teller line is {_cells(text)} cells, limit {LINE_LIMIT}: {text!r}")
    return text


def _choice_gap(previous: str) -> bytes:
    """docs/game/dialogue-scripts.md section 6: column 0 plus the gap pads to 16 cells."""

    cells = _cells(previous) + 2
    return bytes((0x81, 0x40)) * max(16 - cells, 2)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
#
# Fixed order inside the region, all absolute: the entry stub (a goto), the
# relocated two-row dialogue table, the state block, the natives, the
# class-page pointer table, the class table, the script; then, from the fixed
# slab entry table at 0x80019884 upward, the ball charger's pieces (her jump
# table, natives, catalog buffer, menu handle, menu strings, script).
# Addresses fall out of the sizes; two passes settle the cross references
# (natives name pages, the script names natives).

STATE_SIZE = 16
# state block layout (bytes)
STATE_CANDIDATES = 0     # HINT_FLOOR_CHOICES floor numbers, zero-terminated (4 bytes)
STATE_CURSOR = 4         # next candidate to offer
STATE_CHOSEN = 5         # the floor being offered / read
STATE_SLOT_CURSOR = 6    # next slot to look at during a reading
STATE_CURRENT_SLOT = 7   # the slot the current page describes


def _align(value: int, alignment: int = 4) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


@dataclass(frozen=True)
class Layout:
    entry_stub_address: int
    dialogue_table_address: int
    state_address: int
    natives_address: int
    page_table_address: int
    class_table_address: int
    script_address: int
    natives: bytes
    native_addresses: dict[str, int]
    page_addresses: dict[str, int]
    script: bytes
    # the ball charger's half of the image
    charger_jump_table_address: int
    charger_natives_address: int
    charger_catalog_address: int
    charger_handle_address: int
    charger_header_text_address: int
    charger_refusal_text_address: int
    charger_refusal_spent_text_address: int
    charger_script_address: int
    charger_natives: "ball_charger.Natives"
    charger_script: bytes
    charger_labels: dict[str, int]

    @property
    def end(self) -> int:
        """End of the fortune teller's own pieces (below the slab entry table)."""

        return self.script_address + len(self.script)

    @property
    def charger_end(self) -> int:
        return self.charger_script_address + len(self.charger_script)


_ZERO, _AT, _V0, _V1, _A0, _A1, _A2, _A3 = range(8)
_T0, _T1, _T2, _T3, _T4, _T5, _T6, _T7 = range(8, 16)
_T8, _T9 = 24, 25
_RA = 31


def _hi(address: int) -> int:
    return ((address + 0x8000) >> 16) & 0xFFFF


def _lo(address: int) -> int:
    return address & 0xFFFF


class _Natives:
    """Five leaf routines, caller-saved registers only, every load delay padded.

    scan()   -> v0 = number of candidate floors (0..HINT_FLOOR_CHOICES). Refuses
                (0) unless the ADSV magic is present. Fills the state's
                candidate list with the lowest floors whose journal byte
                lacks any of this seed's slot bits, resets the cursor.
    next()   -> v0 = the next candidate floor (advancing the cursor and
                recording it as chosen), or 0 past the last.
    pay()    -> v0 = the chosen floor after debiting HINT_PRICE and resetting
                the slot cursor, or 0 (nothing debited) when the purse is short.
    where()  -> v0 = the address of the next page to run: the ground or carrier
                "where" page for the next un-collected slot of the chosen floor
                (recorded as current, cursor advanced past it), or the done page.
    what()   -> v0 = the class page for the current slot's class byte
                (out-of-range bytes read as UNKNOWN).
    """

    ENTRIES = ("scan", "next", "pay", "where", "what")

    def __init__(
        self,
        base: int,
        state: int,
        class_table: int,
        page_table: int,
        pages: dict[str, int],
        slots_per_floor: int = SLOTS_PER_FLOOR,
    ) -> None:
        self.base = base
        self.state = state
        self.class_table = class_table
        self.page_table = page_table
        self.pages = pages
        # Two or three; the width of scan()'s all-collected mask.
        self.slots_per_floor = slots_per_floor
        self.addresses: dict[str, int] = {}
        self.code = self._assemble()
        self.code = self._assemble()

    def _assemble(self) -> bytes:
        b = _MipsBuilder()
        pages = self.pages

        def la(register: int, address: int) -> None:
            b.emit(_i(0x0F, 0, register, _hi(address)), _i(0x09, register, register, _lo(address)))

        def ret(value_register: int | None = None) -> None:
            if value_register is None:
                b.emit(_r(_RA, 0, 0, 0, 0x08), 0)
            else:
                b.emit(_r(_RA, 0, 0, 0, 0x08), _r(0, value_register, _V0, 0, 0x21))

        # ---- scan()
        b.label("scan")
        la(_T0, ADSV_MAGIC_ADDRESS)
        b.emit(_i(0x23, _T0, _T1, 0))                          # lw t1, magic
        b.emit(_i(0x0F, 0, _T2, ADSV_MAGIC >> 16))             # (delay) lui t2, hi(magic)
        b.emit(_i(0x0D, _T2, _T2, ADSV_MAGIC & 0xFFFF))        # ori t2, lo(magic)
        b.branch(0x05, _T1, _T2, "scan_none")                  # bne: no journal -> 0
        b.emit(0)
        la(_T3, self.state)
        la(_T4, JOURNAL_ADDRESS)
        b.emit(_i(0x09, 0, _T5, 1))                            # t5 = floor 1
        b.emit(_r(0, 0, _T6, 0, 0x21))                         # t6 = count 0
        b.emit(_i(0x09, 0, _T8, (1 << self.slots_per_floor) - 1))   # t8 = all-collected mask
        b.label("scan_loop")
        b.emit(_i(0x24, _T4, _T7, 0))                          # lbu t7, journal[floor-1]
        b.emit(0)
        b.emit(_i(0x0C, _T7, _T7, (1 << self.slots_per_floor) - 1)) # andi t7, mask
        b.branch(0x04, _T7, _T8, "scan_next")                  # every slot collected: skip
        b.emit(0)
        b.emit(_r(_T3, _T6, _T9, 0, 0x21))                     # t9 = &candidates[count]
        b.emit(_i(0x28, _T9, _T5, STATE_CANDIDATES))           # sb floor
        b.emit(_i(0x09, _T6, _T6, 1))                          # count += 1
        b.emit(_i(0x09, 0, _T7, HINT_FLOOR_CHOICES))
        b.branch(0x04, _T6, _T7, "scan_done")                  # enough candidates
        b.emit(0)
        b.label("scan_next")
        b.emit(_i(0x09, _T4, _T4, 1))
        b.emit(_i(0x09, _T5, _T5, 1))
        b.emit(_i(0x0B, _T5, _T7, JOURNAL_FLOORS + 1))         # sltiu t7, floor, 40
        b.branch(0x05, _T7, 0, "scan_loop")
        b.emit(0)
        b.label("scan_done")
        b.emit(_r(_T3, _T6, _T9, 0, 0x21))                     # candidates[count] = 0
        b.emit(_i(0x28, _T9, 0, STATE_CANDIDATES))
        b.emit(_i(0x28, _T3, 0, STATE_CURSOR))                 # cursor = 0
        ret(_T6)
        b.label("scan_none")
        ret(_ZERO)

        # ---- next()
        b.label("next")
        la(_T3, self.state)
        b.emit(_i(0x24, _T3, _T0, STATE_CURSOR))               # lbu t0, cursor
        b.emit(0)
        b.emit(_i(0x0B, _T0, _T1, HINT_FLOOR_CHOICES))         # sltiu t1, cursor, N
        b.branch(0x04, _T1, 0, "next_none")
        b.emit(_r(_T3, _T0, _T2, 0, 0x21))                     # (delay) t2 = &candidates[cursor]
        b.emit(_i(0x24, _T2, _V0, STATE_CANDIDATES))           # lbu v0, candidate
        b.emit(0)
        b.branch(0x04, _V0, 0, "next_none")                    # zero terminator: none left
        b.emit(_i(0x09, _T0, _T0, 1))                          # (delay) cursor + 1
        b.emit(_i(0x28, _T3, _T0, STATE_CURSOR))
        b.emit(_i(0x28, _T3, _V0, STATE_CHOSEN))               # chosen = floor
        ret()
        b.label("next_none")
        ret(_ZERO)

        # ---- pay()
        b.label("pay")
        la(_T0, TOWN_MONEY_ADDRESS)
        b.emit(_i(0x23, _T0, _T1, 0))                          # lw t1, money
        b.emit(_i(0x09, 0, _T2, HINT_PRICE))                   # (delay) t2 = price
        b.emit(_r(_T1, _T2, _T4, 0, 0x2B))                     # sltu t4, money, price
        b.branch(0x05, _T4, 0, "pay_poor")
        b.emit(0)
        b.emit(_r(_T1, _T2, _T1, 0, 0x23))                     # money -= price
        b.emit(_i(0x2B, _T0, _T1, 0))
        la(_T3, self.state)
        b.emit(_i(0x28, _T3, 0, STATE_SLOT_CURSOR))            # slot cursor = 0
        b.emit(_i(0x24, _T3, _V0, STATE_CHOSEN))               # lbu v0, chosen
        ret()
        b.label("pay_poor")
        ret(_ZERO)

        # ---- where()
        b.label("where")
        la(_T3, self.state)
        b.emit(_i(0x24, _T3, _T0, STATE_CHOSEN))               # lbu t0, floor
        b.emit(_i(0x24, _T3, _T1, STATE_SLOT_CURSOR))          # lbu t1, slot cursor
        la(_T4, JOURNAL_ADDRESS - 1)                           # journal[floor-1] = base-1 + floor
        b.emit(_r(_T4, _T0, _T4, 0, 0x21))
        b.emit(_i(0x24, _T4, _T5, 0))                          # lbu t5, journal byte
        b.emit(0)
        b.label("where_loop")
        b.emit(_i(0x0B, _T1, _T6, SLOTS_PER_FLOOR))            # sltiu t6, slot, 3
        b.branch(0x04, _T6, 0, "where_done")
        b.emit(_i(0x09, 0, _T7, 1))                            # (delay) t7 = 1
        b.emit(_r(_T1, _T7, _T7, 0, 0x04))                     # sllv t7, 1, slot
        b.emit(_r(_T7, _T5, _T7, 0, 0x24))                     # and t7, journal
        b.branch(0x04, _T7, 0, "where_found")                  # bit clear: still open
        b.emit(0)
        b.branch(0x04, 0, 0, "where_loop")
        b.emit(_i(0x09, _T1, _T1, 1))                          # (delay) slot += 1
        b.label("where_found")
        b.emit(_i(0x28, _T3, _T1, STATE_CURRENT_SLOT))         # current = slot
        b.emit(_i(0x09, _T1, _T7, 1))
        b.emit(_i(0x28, _T3, _T7, STATE_SLOT_CURSOR))          # cursor = slot + 1
        b.emit(_i(0x09, 0, _T8, CARRIER_SLOT))
        la(_V0, pages["where_ground"])
        b.branch(0x05, _T1, _T8, "where_ret")
        b.emit(0)
        la(_V0, pages["where_carrier"])
        b.label("where_ret")
        ret()
        b.label("where_done")
        la(_V0, pages["done"])
        ret()

        # ---- what()
        b.label("what")
        la(_T3, self.state)
        b.emit(_i(0x24, _T3, _T0, STATE_CHOSEN))               # lbu t0, floor
        b.emit(_i(0x24, _T3, _T1, STATE_CURRENT_SLOT))         # lbu t1, slot
        b.emit(_i(0x09, _T0, _T0, -1))                         # floor - 1
        b.emit(_r(0, _T0, _T2, 1, 0x00))                       # t2 = (floor-1) << 1
        b.emit(_r(_T2, _T0, _T2, 0, 0x21))                     # t2 = (floor-1) * 3
        b.emit(_r(_T2, _T1, _T2, 0, 0x21))                     # + slot
        la(_T4, self.class_table)
        b.emit(_r(_T4, _T2, _T4, 0, 0x21))
        b.emit(_i(0x24, _T4, _T5, 0))                          # lbu t5, class
        b.emit(0)
        b.emit(_i(0x0B, _T5, _T6, HINT_CLASS_COUNT))           # sltiu t6, class, COUNT
        b.branch(0x05, _T6, 0, "what_ok")
        b.emit(0)
        b.emit(_r(0, 0, _T5, 0, 0x21))                         # unknown
        b.label("what_ok")
        b.emit(_r(0, _T5, _T5, 2, 0x00))                       # class * 4
        la(_T6, self.page_table)
        b.emit(_r(_T6, _T5, _T6, 0, 0x21))
        b.emit(_i(0x23, _T6, _V0, 0))                          # lw v0, page
        ret()

        code = b.build()
        self.addresses = {name: self.base + offset * 4 for name, offset in b.labels.items()}
        return code


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

def _build_script(base: int, natives: dict[str, int]) -> tuple[bytes, dict[str, int]]:
    """The conversation as one byte string at `base`, plus every page label's
    absolute address (the natives return some of them).

        main:      <greeting, 3 rows> 11
                   4C scan 3E 0F -> nothing
                   08 57 4F <price page, 3 rows> 11
        offer:     08 4C next 3E 0F -> no_more
                   "Floor " FD 0F ", child?" 0A
                   0B [Look.] gap [Higher.] 0A 0B [Not now.] 2C 03 1A -> read, offer, bye
        read:      4C pay 3E 0F -> poor
                   08 57 4F "I gaze into the crystal..." 0A "Floor " FD 0F " shows itself to me." 11
        loop:      4C where 48 0F
        where_*:   08 57 4F <where line> 0A 4C what 48 0F
        class_k:   <line 1> 0A <line 2> 11 17 -> loop
        done / poor / no_more / nothing / bye: 08 57 4F <rows> 11 01 01

    The greeting carries no clear (nothing is drawn yet - the vanilla script
    opens the same way, text first); every later page clears. Prose and the
    choice rows share the offer page under the persisting `57 4F` mode, the
    shape of her own vanilla quiz pages. FD 0F prints slot 0x0F, which the
    preceding native left holding the floor number.
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
            raise ValueError(f"duplicate fortune-teller label {name}")
        labels[name] = len(script)

    def native(name: str) -> None:
        emit(0x4C)
        script.extend(struct.pack("<I", natives[name]))

    def text(value: str) -> None:
        script.extend(_text(_check_line(value)))

    def rows(lines: Sequence[str]) -> None:
        for index, line in enumerate(lines):
            if index:
                emit(0x0A)
            text(line)

    def prose_page(name: str, lines: Sequence[str]) -> None:
        """A cleared page under her plate that ends the conversation."""

        label(name)
        emit(0x08, 0x57, TELLER_ACTOR_SLOT)
        rows(lines)
        emit(0x11, 0x01, 0x01)

    def choice(value: str, *, row_start: bool) -> None:
        if row_start:
            emit(0x0B)
        emit(0x81, 0x6D)
        text(value)
        emit(0x81, 0x6E)

    label("main")
    rows(GREETING)
    emit(0x11)
    native("scan")
    emit(0x3E, 0x0F)
    emit_ref("nothing")
    emit(0x08, 0x57, TELLER_ACTOR_SLOT)
    rows(PRICE_PAGE)
    emit(0x11)

    label("offer")
    emit(0x08)
    native("next")
    emit(0x3E, 0x0F)
    emit_ref("no_more")
    text(OFFER_LINE)
    emit(0xFD, 0x0F)
    text(OFFER_TAIL)
    emit(0x0A)
    choice(CHOICE_LOOK, row_start=True)
    script.extend(_choice_gap(CHOICE_LOOK))
    choice(CHOICE_HIGHER, row_start=False)
    emit(0x0A)
    choice(CHOICE_NOT_NOW, row_start=True)
    emit(0x2C, 0x03, 0x1A)
    emit_ref("read")
    emit_ref("offer")
    emit_ref("bye")

    label("read")
    native("pay")
    emit(0x3E, 0x0F)
    emit_ref("poor")
    emit(0x08, 0x57, TELLER_ACTOR_SLOT)
    text(READ_PAGE[0])
    emit(0x0A)
    text(READ_PAGE[1])
    emit(0xFD, 0x0F)
    text(READ_TAIL)
    emit(0x11)

    label("loop")
    native("where")
    emit(0x48, 0x0F)

    for name, line in (("where_ground", WHERE_GROUND), ("where_carrier", WHERE_CARRIER)):
        label(name)
        emit(0x08, 0x57, TELLER_ACTOR_SLOT)
        text(line)
        emit(0x0A)
        native("what")
        emit(0x48, 0x0F)

    for hint_class in range(HINT_CLASS_COUNT):
        label(f"class_{hint_class}")
        rows(CLASS_LINES[hint_class])
        emit(0x11, 0x17)
        emit_ref("loop")

    prose_page("done", DONE_PAGE)
    prose_page("poor", POOR_PAGE)
    prose_page("no_more", NO_MORE_PAGE)
    prose_page("nothing", NOTHING_PAGE)
    prose_page("bye", BYE_PAGE)

    for offset, name in fixups:
        struct.pack_into("<I", script, offset, base + labels[name])
    return bytes(script), {name: base + offset for name, offset in labels.items()}


def _build_closed_script(base: int) -> tuple[bytes, dict[str, int]]:
    """`main: <two rows> 11 01 01` - the whole conversation with hints off.
    Text first (nothing is drawn yet), no clear."""

    script = bytearray()
    for index, line in enumerate(CLOSED_PAGE):
        if index:
            script.append(0x0A)
        script.extend(_text(_check_line(line)))
    script.extend((0x11, 0x01, 0x01))
    return bytes(script), {"main": base}


def build_layout(
    class_table: bytes | None = None,
    hints: bool = True,
    slots_per_floor: int = SLOTS_PER_FLOOR,
) -> Layout:
    """Place everything in the region and resolve the cross references.
    `hints=False` swaps the conversation for the one closed page (the natives
    and tables are still laid out - unreferenced bytes - so nothing moves).

    `slots_per_floor` is how many checks a floor of THIS seed really has - two
    with the carrier system off. It is only ever the width of her
    "is this floor finished?" mask; the class table stays the seed page's full
    three-a-floor length so the journal index arithmetic is the same either
    way. Getting this wrong is not a crash, it is worse: she would offer a
    reading on a floor whose only remaining check does not exist.
    """

    if slots_per_floor not in (2, 3):
        raise ValueError(f"A tower floor has two or three slots, got {slots_per_floor}.")

    if class_table is None:
        class_table = bytes(LOCATION_COUNT)
    if len(class_table) != LOCATION_COUNT:
        raise ValueError(f"The hint-class table must hold {LOCATION_COUNT} bytes, got {len(class_table)}.")
    if any(value >= HINT_CLASS_COUNT for value in class_table):
        raise ValueError("A hint class is out of range.")

    entry_stub_address = REGION_START                       # 17 <main>, 5 bytes
    dialogue_table_address = _align(entry_stub_address + 5, 8)  # three 8-byte rows
    state_address = _align(dialogue_table_address + DIALOGUE_TABLE_SIZE, 16)
    natives_address = state_address + STATE_SIZE
    # Sizes first: natives with dummy pages, then everything falls into place.
    probe = _Natives(
        natives_address,
        state_address,
        0,
        0,
        {"where_ground": 0, "where_carrier": 0, "done": 0},
        slots_per_floor,
    )
    page_table_address = _align(natives_address + len(probe.code))
    class_table_address = page_table_address + HINT_CLASS_COUNT * 4
    script_address = _align(class_table_address + LOCATION_COUNT)

    native_addresses = dict(probe.addresses)
    script, page_addresses = _build_script(script_address, native_addresses)
    if not hints:
        script, page_addresses = _build_closed_script(script_address)
        # the natives still name the where/done pages; give them the closed page
        page_addresses.update({name: script_address for name in ("where_ground", "where_carrier", "done")})
    natives = _Natives(
        natives_address,
        state_address,
        class_table_address,
        page_table_address,
        {name: page_addresses[name] for name in ("where_ground", "where_carrier", "done")},
        slots_per_floor,
    )
    if len(natives.code) != len(probe.code) or natives.addresses != native_addresses:
        raise AssertionError("The fortune-teller natives changed size between passes.")

    # ---- the ball charger, from the slab's fixed entry table upward
    charger_jump_table_address = ball_charger.JUMP_TABLE_ADDRESS
    charger_natives_address = charger_jump_table_address + 16
    charger_probe = ball_charger.Natives(charger_natives_address, 0, 0, 0, 0, 0)
    charger_catalog_address = _align(charger_natives_address + len(charger_probe.code), 16)
    charger_handle_address = charger_catalog_address + ball_charger.CATALOG_BUFFER_SIZE
    charger_header_text_address = charger_handle_address + 4
    charger_refusal_text_address = _align(
        charger_header_text_address + len(ball_charger.build_header_text()) + 1, 4
    )
    charger_refusal_spent_text_address = _align(
        charger_refusal_text_address + len(ball_charger.build_refusal_text()) + 1, 4
    )
    charger_script_address = _align(
        charger_refusal_spent_text_address + len(ball_charger.build_spent_refusal_text()) + 1, 4
    )
    charger_natives = ball_charger.Natives(
        charger_natives_address,
        charger_catalog_address,
        charger_handle_address,
        charger_header_text_address,
        charger_refusal_text_address,
        charger_refusal_spent_text_address,
    )
    if len(charger_natives.code) != len(charger_probe.code):
        raise AssertionError("The ball-charger natives changed size between passes.")
    charger_script, charger_labels = ball_charger.build_script(charger_script_address, charger_natives)

    layout = Layout(
        entry_stub_address=entry_stub_address,
        dialogue_table_address=dialogue_table_address,
        state_address=state_address,
        natives_address=natives_address,
        page_table_address=page_table_address,
        class_table_address=class_table_address,
        script_address=script_address,
        natives=natives.code,
        native_addresses=native_addresses,
        page_addresses=page_addresses,
        script=script,
        charger_jump_table_address=charger_jump_table_address,
        charger_natives_address=charger_natives_address,
        charger_catalog_address=charger_catalog_address,
        charger_handle_address=charger_handle_address,
        charger_header_text_address=charger_header_text_address,
        charger_refusal_text_address=charger_refusal_text_address,
        charger_refusal_spent_text_address=charger_refusal_spent_text_address,
        charger_script_address=charger_script_address,
        charger_natives=charger_natives,
        charger_script=charger_script,
        charger_labels=charger_labels,
    )
    if layout.end > charger_jump_table_address:
        raise ValueError(
            f"The fortune teller's pieces run to 0x{layout.end:08X}, over the slab entry "
            f"table at 0x{charger_jump_table_address:08X}."
        )
    if layout.charger_end > REGION_END:
        raise ValueError(
            f"The ball charger's pieces run to 0x{layout.charger_end:08X}, past the region "
            f"end 0x{REGION_END:08X}."
        )
    return layout


def build_dialogue_table(layout: Layout) -> bytes:
    """Shiela's row and the charger's, at the address the overlay's getter is
    repointed to (`OVERLAY_TABLE_ADDIU_ADDRESS`)."""

    return ball_charger.build_dialogue_table(
        TELLER_ACTOR_SLOT, layout.page_addresses["main"], layout.charger_labels["start"]
    )


def build_page_table(layout: Layout) -> bytes:
    return b"".join(
        struct.pack("<I", layout.page_addresses.get(f"class_{hint_class}", layout.page_addresses["main"]))
        for hint_class in range(HINT_CLASS_COUNT)
    )


def build_entry_stub(layout: Layout) -> bytes:
    """`17 <main>`: her dialogue-table row keeps pointing at 0x800176D0 (the
    relocated table does too, so the vanilla-shaped entry survives)."""

    return bytes((0x17,)) + struct.pack("<I", layout.page_addresses["main"])


# ---------------------------------------------------------------------------
# Disc records
# ---------------------------------------------------------------------------

def dialogue_runtime_to_file_offset(address: int) -> int:
    if not DIALOGUE_RUNTIME_ADDRESS <= address < DIALOGUE_RUNTIME_END:
        raise ValueError(f"Address 0x{address:08x} is outside the fortune-teller dialogue image.")
    return DIALOGUE_FILE_OFFSET + address - DIALOGUE_RUNTIME_ADDRESS


def build_class_table(hint_classes: Sequence[int]) -> bytes:
    if len(hint_classes) != LOCATION_COUNT:
        raise ValueError(f"Expected {LOCATION_COUNT} hint classes, got {len(hint_classes)}.")
    return bytes(hint_classes)


def iter_dialogue_file_patches(
    hint_classes: Sequence[int],
    hints: bool = True,
    charger: bool = True,
    slots_per_floor: int = SLOTS_PER_FLOOR,
) -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN file offset, bytes) inside her dialogue image.

    Both off: nothing at all - vanilla Shiela, vanilla quiz. Hints on: her
    pieces. Charger on: its pieces plus the relocated dialogue table (and, if
    hints are off, the entry stub with the closed page, because the charger's
    bytes land inside her vanilla quiz).
    """

    if not hints and not charger:
        return ()
    class_table = build_class_table(hint_classes)
    layout = build_layout(class_table, hints=hints, slots_per_floor=slots_per_floor)
    pieces: list[tuple[int, bytes]] = [
        (layout.entry_stub_address, build_entry_stub(layout)),
        (layout.script_address, layout.script),
    ]
    if hints:
        pieces += [
            (layout.state_address, bytes(STATE_SIZE)),
            (layout.natives_address, layout.natives),
            (layout.page_table_address, build_page_table(layout)),
            (layout.class_table_address, class_table),
        ]
    if charger:
        pieces += [
            (layout.dialogue_table_address, build_dialogue_table(layout)),
            (layout.charger_jump_table_address, ball_charger.build_jump_table(layout.charger_natives)),
            (layout.charger_natives_address, layout.charger_natives.code),
            (layout.charger_catalog_address, bytes(ball_charger.CATALOG_BUFFER_SIZE + 4)),   # catalog + handle
            (layout.charger_header_text_address, ball_charger.build_header_text()),
            (layout.charger_refusal_text_address, ball_charger.build_refusal_text()),
            (
                layout.charger_refusal_spent_text_address,
                ball_charger.build_spent_refusal_text(),
            ),
            (layout.charger_script_address, layout.charger_script),
        ]
    return tuple((dialogue_runtime_to_file_offset(address), body) for address, body in pieces)


def overlay_runtime_to_file_offset(address: int) -> int:
    if not OVERLAY_RUNTIME_ADDRESS <= address < OVERLAY_RUNTIME_ADDRESS + OVERLAY_SIZE:
        raise ValueError(f"Address 0x{address:08x} is outside the fortune-teller overlay.")
    return OVERLAY_FILE_OFFSET + address - OVERLAY_RUNTIME_ADDRESS


def iter_overlay_file_patches(charger: bool = True) -> tuple[tuple[int, bytes], ...]:
    """(TOWN.BIN file offset, bytes) inside the interior overlay: the getter's
    table immediate and the charger's spawn record - both only for the charger."""

    if not charger:
        return ()
    layout = build_layout()
    if not 0 <= _lo(layout.dialogue_table_address) < 0x8000:
        raise ValueError("The relocated dialogue table must sit where a positive addiu reaches it.")
    addiu = _i(0x09, _A0, _A0, _lo(layout.dialogue_table_address))
    patches = [
        (overlay_runtime_to_file_offset(OVERLAY_TABLE_ADDIU_ADDRESS), struct.pack("<I", addiu)),
        (overlay_runtime_to_file_offset(CHARGER_RECORD_ADDRESS), ball_charger.build_spawn_record()),
    ]
    return tuple(patches)


def iter_fortune_teller_raw_patches(
    hint_classes: Sequence[int],
    hints: bool = True,
    charger: bool = True,
    slots_per_floor: int = SLOTS_PER_FLOOR,
) -> tuple[tuple[int, bytes], ...]:
    return save_removal._iter_mode2_raw_patches(
        town_shop.TOWN_FILE_START_LBA,
        iter_overlay_file_patches(charger)
        + iter_dialogue_file_patches(hint_classes, hints, charger, slots_per_floor),
    )


def append_fortune_teller_ppf_records(
    ppf: bytearray,
    hint_classes: Sequence[int],
    hints: bool = True,
    charger: bool = True,
    slots_per_floor: int = SLOTS_PER_FLOOR,
) -> None:
    for raw_offset, data in iter_fortune_teller_raw_patches(
        hint_classes, hints, charger, slots_per_floor
    ):
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
