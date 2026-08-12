"""Send tokens: a tower send costs one, and without one the gift stays put.

Build 3 (world 0.9.97). Two earlier builds refused in the PLAYERS MENU and
both softlocked - the first returned a junk `v0`, the second returned 0,
which the dispatcher read as "not handled" and called again every frame.
Both were argued from a disassembly of the wrong overlay: the menu row
handlers' addresses hold the FLOOR-GENERATION overlay in the RAM dumps
this project has, so none of that reasoning described the running code.

The check now lives in the commit, on its own already-shipped refusal
path, and nothing in the send path draws a message. `docs/systems/
nada-send.md` owns the account.
"""

import unittest

from .. import items, locations, patch, tower_send
from . import mips_sim
from .bases import AzureDreamsTestBase


class _CheckRun:
    """`send_token_check() -> v0 = tokens`, seeding on first touch."""

    def __init__(self, tokens: int, seeded: bool = True) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.SEND_TOKEN_CHECK_ADDRESS, patch._build_send_token_check()
        )
        memory.write32(patch.SEND_TOKEN_COUNT_ADDRESS, tokens)
        if seeded:
            memory.write32(
                patch.SEND_TOKEN_COUNT_ADDRESS + patch.SEND_TOKEN_MAGIC_OFFSET,
                patch.SEND_TOKEN_MAGIC,
            )
        self.memory = memory
        self.cpu = mips_sim.Cpu(memory)
        self.result = self.cpu.run(patch.SEND_TOKEN_CHECK_ADDRESS)

    @property
    def tokens(self) -> int:
        return self.memory.read32(patch.SEND_TOKEN_COUNT_ADDRESS)

    @property
    def magic(self) -> int:
        return self.memory.read32(
            patch.SEND_TOKEN_COUNT_ADDRESS + patch.SEND_TOKEN_MAGIC_OFFSET
        )


class _SpendRun:
    def __init__(self, tokens: int) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.SEND_TOKEN_SPEND_ADDRESS, patch._build_send_token_spend()
        )
        memory.write32(patch.SEND_TOKEN_COUNT_ADDRESS, tokens)
        self.memory = memory
        mips_sim.Cpu(memory).run(patch.SEND_TOKEN_SPEND_ADDRESS)

    @property
    def tokens(self) -> int:
        return self.memory.read32(patch.SEND_TOKEN_COUNT_ADDRESS)


class TestSendTokenCheck(unittest.TestCase):
    def test_it_reports_the_count(self) -> None:
        self.assertEqual(_CheckRun(tokens=3).result, 3)
        self.assertEqual(_CheckRun(tokens=0).result, 0)

    def test_an_unseeded_save_is_granted_its_starting_token(self) -> None:
        """"Never initialized" and "spent them all" are the same zero.

        The town initializer seeds the pair, but only on the frame it
        decides the save is new - and build 1 reached the tower first, so
        the count read zero and the send was refused on a fresh run.
        """

        run = _CheckRun(tokens=0, seeded=False)
        self.assertEqual(run.result, patch.SEND_TOKEN_STARTING_COUNT)
        self.assertEqual(run.tokens, patch.SEND_TOKEN_STARTING_COUNT)
        self.assertEqual(run.magic, patch.SEND_TOKEN_MAGIC)

    def test_a_seeded_empty_save_stays_empty(self) -> None:
        # The witness is what keeps "spent them all" refusing instead of
        # being re-granted a token every time it is asked.
        run = _CheckRun(tokens=0, seeded=True)
        self.assertEqual(run.result, 0)
        self.assertEqual(run.tokens, 0)

    def test_it_preserves_the_commits_live_registers(self) -> None:
        """The bug that broke sending: this runs mid-commit.

        Between the controller read and the mailbox publish, `t2` carries
        the controller pointer and `t3` the confirmed TARGET INDEX. The
        first version of this routine used `t2`/`t3` for its magic
        constant, so the gift went out with `ADST` in the recipient field
        and the client dropped it. Only `v0`, `v1` and `at` may be touched.
        """

        for routine in (
            patch._build_send_token_check(),
            patch._build_send_token_spend(),
        ):
            memory = mips_sim.Memory()
            memory.load_bytes(0x8010_0000, routine)
            memory.write32(
                patch.SEND_TOKEN_COUNT_ADDRESS + patch.SEND_TOKEN_MAGIC_OFFSET,
                patch.SEND_TOKEN_MAGIC,
            )
            memory.write32(patch.SEND_TOKEN_COUNT_ADDRESS, 2)
            cpu = mips_sim.Cpu(memory)
            witness = 0x1234_0000
            for register in range(4, 31):        # a0..t9, s0..s7, k0/k1, gp/sp/fp
                cpu.registers[register] = witness | register
            cpu.run(0x8010_0000)
            for register in range(4, 31):
                if register == 29:               # sp is the harness's own
                    continue
                self.assertEqual(
                    cpu.registers[register],
                    witness | register,
                    f"register {register} was clobbered; the commit needs "
                    "t2 (controller) and t3 (target index) intact.",
                )

    def test_it_makes_no_calls(self) -> None:
        """It runs inside `put_item_into_bag`'s classifier.

        The simulator raises on an unstubbed call, so reaching the end
        with no stubs registered IS the assertion - anything this routine
        called would be running in a context it was never checked against,
        which is how the last two builds went wrong.
        """

        _CheckRun(tokens=1)  # would raise on any jal


class TestSendTokenSpend(unittest.TestCase):
    def test_a_send_spends_one(self) -> None:
        self.assertEqual(_SpendRun(tokens=3).tokens, 2)
        self.assertEqual(_SpendRun(tokens=1).tokens, 0)

    def test_the_counter_never_wraps(self) -> None:
        # The commit's check is the guard; this is the backstop. An
        # unsigned wrap to 0xFFFFFFFF would read as "sends stopped costing".
        self.assertEqual(_SpendRun(tokens=0).tokens, 0)


class _CompleteRun:
    """`send_complete()`: take the token, then draw `Sent!`."""

    def __init__(self, tokens: int) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.SEND_COMPLETE_ROUTINE_ADDRESS, patch._build_send_complete()
        )
        # The real spend, not a stub - the ordering assertion below is only
        # worth anything if the count it reads was moved by the real code.
        memory.load_bytes(
            patch.SEND_TOKEN_SPEND_ADDRESS, patch._build_send_token_spend()
        )
        memory.write32(patch.SEND_TOKEN_COUNT_ADDRESS, tokens)
        self.memory = memory
        self.drawn: list[tuple[int, int]] = []
        self.cpu = mips_sim.Cpu(
            memory, {patch.SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS: self._draw}
        )
        self.cpu.run(patch.SEND_COMPLETE_ROUTINE_ADDRESS)

    def _draw(self, cpu: mips_sim.Cpu) -> None:
        self.drawn.append(
            (cpu.registers[4], self.memory.read32(patch.SEND_TOKEN_COUNT_ADDRESS))
        )

    @property
    def tokens(self) -> int:
        return self.memory.read32(patch.SEND_TOKEN_COUNT_ADDRESS)


class TestSendComplete(unittest.TestCase):
    def test_it_spends_and_then_announces(self) -> None:
        run = _CompleteRun(tokens=3)
        self.assertEqual(run.tokens, 2)
        self.assertEqual(len(run.drawn), 1)
        text, count_when_drawn = run.drawn[0]
        self.assertEqual(text, patch.SEND_COMPLETE_MESSAGE_ADDRESS)
        # Announcing before taking would show `Sent!` for a send that then
        # failed to be paid for. The spend comes first.
        self.assertEqual(count_when_drawn, 2)

    def test_it_returns_to_the_commit(self) -> None:
        """It is `jal`ed, so it owes the commit a return and its stack back.

        `ra` is expendable in the commit only because the epilogue restores
        it from the frame - but this routine calls out twice, so it has to
        keep its own, and it must hand `sp` back where it found it or the
        epilogue unwinds into nothing.
        """

        run = _CompleteRun(tokens=1)
        self.assertEqual(run.cpu.registers[29], 0x801F_C000)

    def test_the_sent_text_is_battle_encoded(self) -> None:
        """The confirmation and the refusal use DIFFERENT drawers.

        `show_simple_action_message` is the tower's bottom message box and
        takes the compact battle encoding; the players-menu drawer the
        refusal uses takes full-width CP932. Encoding one for the other
        renders garbage.
        """

        block = patch.build_seed_block(
            b"12345678",
            [
                patch.LocationPlacement("Gold", "Koh", False)
                for _ in range(patch.LOCATION_COUNT)
            ],
        )
        expected = patch.encode_battle_message(patch.SEND_COMPLETE_TEXT)
        self.assertEqual(
            block[
                patch.SEND_COMPLETE_MESSAGE_OFFSET :
                patch.SEND_COMPLETE_MESSAGE_OFFSET + len(expected)
            ],
            expected,
        )
        self.assertNotEqual(
            expected, patch.encode_menu_message(patch.SEND_COMPLETE_TEXT)
        )


class TestSendTokenWiring(unittest.TestCase):
    def test_the_menu_row_jumps_to_the_gate(self) -> None:
        """The row's slot is 32 bytes; the gate is the real handler.

        A plain `j` hands the gate both things the row is entered with -
        `a0` (the menu state) and the dispatcher's `ra`.
        """

        handler = tower_send.build_send_handler(3)
        self.assertEqual(len(handler), 8)
        self.assertEqual(
            handler[:4],
            patch._j(0x02, patch.SEND_TOKEN_GATE_ADDRESS).to_bytes(4, "little"),
        )

    def test_the_refusal_returns_the_modal_object_not_a_status(self) -> None:
        """The contract both softlocked builds got wrong.

        A row handler returns the OBJECT that now owns the screen, not a
        success flag. Vanilla's "You need 2 familiars" allocates a menu
        object, installs the dismiss callback and returns the object
        pointer; the menu then suspends until that object dies. Returning
        0 means "nothing took over", which is why the dispatcher called
        the row again every frame and the message re-printed forever.
        """

        gate = patch._build_send_token_gate()
        # `addu v0,s1,zero` - s1 holds the allocated object.
        self.assertIn(
            patch._r(17, 0, 2, 0, 0x21).to_bytes(4, "little"), gate
        )
        for address in (
            patch.MENU_OBJECT_ALLOCATE_ADDRESS,
            patch.MENU_MESSAGE_BEGIN_ADDRESS,
            patch.MENU_MESSAGE_PREPARE_ADDRESS,
            patch.MENU_MESSAGE_DRAW_ADDRESS,
        ):
            with self.subTest(routine=hex(address)):
                self.assertIn(
                    patch._j(0x03, address).to_bytes(4, "little"), gate
                )
        # Vanilla's own dismiss-on-Cross callback, installed at +0x10.
        self.assertIn(
            patch.MENU_MESSAGE_DISMISS_CALLBACK.to_bytes(4, "little")[2:],
            gate,
        )

    def test_the_refusal_text_is_full_width_cp932(self) -> None:
        """Byte-identical in form to the game's own menu strings.

        Validated against `You need 2 familiars` at `0x8002E924` in the
        menu save state - the menu's drawer wants full-width CP932, NOT
        the compact battle encoding the floor text uses.
        """

        self.assertEqual(
            patch.encode_menu_message("You need 2 familiars"),
            bytes.fromhex(
                "82788 28f8295814082 8e828582858284814082518140"
                "8286828182 8d82898 28c828982818292829300".replace(" ", "")
            ),
        )

    def test_the_commit_checks_before_it_publishes(self) -> None:
        commit = tower_send.build_send_commit(3)
        call = patch._j(0x03, patch.SEND_TOKEN_CHECK_ADDRESS).to_bytes(4, "little")
        spend = patch._j(
            0x03, patch.SEND_COMPLETE_ROUTINE_ADDRESS
        ).to_bytes(4, "little")
        self.assertIn(call, commit)
        self.assertIn(spend, commit)
        # The check has to come before the publish, and the publish before
        # the spend - a spend that ran first would charge for a refusal.
        self.assertLess(commit.index(call), commit.index(spend))
        # And the bare spend is no longer reachable from the commit: it is
        # the confirmation's first act now, so a `Sent!`-less spend would
        # mean two paths that take a token.
        self.assertNotIn(
            patch._j(0x03, patch.SEND_TOKEN_SPEND_ADDRESS).to_bytes(4, "little"),
            commit,
        )
        self.assertLessEqual(len(commit), 0x320 - 0x180)

    def test_the_action_message_primitive_stays_out_of_the_menu(self) -> None:
        """The refusal draws through the MENU's drawer, not the tower's.

        `show_simple_action_message` is what the locked-elevator refusal
        uses from an action context; called from a modal menu it closed
        the menu to draw itself, which is how the first softlock started.
        The menu has its own drawer and its own object lifecycle, and the
        gate uses those.

        The CONFIRMATION is the mirror image and deliberately excluded
        here: it runs from the commit, by which point confirming a target
        has already closed the menu system and returned control to the
        dungeon - an action context, where this is the right primitive.
        """

        show = patch._j(
            0x03, patch.SHOW_SIMPLE_ACTION_MESSAGE_ADDRESS
        ).to_bytes(4, "little")
        for name, code in (
            ("gate", patch._build_send_token_gate()),
            ("check", patch._build_send_token_check()),
            ("spend", patch._build_send_token_spend()),
            ("handler", tower_send.build_send_handler(3)),
            ("commit", tower_send.build_send_commit(3)),
        ):
            with self.subTest(routine=name):
                self.assertNotIn(show, code)
        self.assertIn(show, patch._build_send_complete())


class TestSendTokenPlacement(unittest.TestCase):
    def test_a_fresh_save_starts_with_one_token(self) -> None:
        self.assertEqual(patch.SEND_TOKEN_STARTING_COUNT, 1)
        initializer = patch._build_seed_state_initializer()
        store = patch._i(
            0x2B, 11, 12,
            patch.SEND_TOKEN_COUNT_ADDRESS - patch.PERSISTENT_STATE_ADDRESS,
        )
        self.assertIn(store.to_bytes(4, "little"), initializer)

    def test_the_counter_sits_in_the_checkpointed_save_tail(self) -> None:
        self.assertGreaterEqual(
            patch.SEND_TOKEN_COUNT_ADDRESS,
            patch.SHORTCUT_PENDING_LEVEL_ADDRESS + 4,
        )
        self.assertLessEqual(
            patch.SEND_TOKEN_COUNT_ADDRESS + patch.SEND_TOKEN_MAGIC_OFFSET + 4,
            0x8001_6000,
        )
        self.assertGreaterEqual(
            patch.SEND_TOKEN_COUNT_ADDRESS,
            patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_STATE_SIZE,
        )

    def test_the_block_rides_every_floor_page_sector(self) -> None:
        """It is code in the window, so it must be byte-identical per floor."""

        placements = [
            patch.LocationPlacement("Gold", "Koh", False)
            for _ in range(patch.LOCATION_COUNT)
        ]
        block = patch.build_seed_block(b"12345678", placements)
        check = patch._build_send_token_check()
        self.assertEqual(
            block[
                patch.SEND_TOKEN_CHECK_OFFSET :
                patch.SEND_TOKEN_CHECK_OFFSET + len(check)
            ],
            check,
        )
        start = patch.SEND_TOKEN_BLOCK_OFFSET
        end = start + patch.SEND_TOKEN_BLOCK_CAPACITY
        resident = block[start:end]
        window_start = start - patch.FLOOR_PAGE_WINDOW_OFFSET
        window_end = end - patch.FLOOR_PAGE_WINDOW_OFFSET
        for sector in patch.build_floor_page_sectors(block, placements):
            self.assertEqual(sector[window_start:window_end], resident)


class TestSendTokenItem(AzureDreamsTestBase):
    """The pool item. Five of them, and nowhere they may not go."""

    def test_a_solo_azure_dreams_seed_gets_NO_tokens(self) -> None:
        """A token you can never spend is a dead check.

        The tower's Send row is built from the other Azure Dreams slots and
        does not exist in a solo room; Nada's menu drops out the same way. So
        the five become ordinary draws - and therefore trap candidates like
        every other slot - rather than five wasted locations.
        """

        self.assertEqual(items.SEND_TOKEN_COUNT, 5)
        self.assertEqual(items.send_token_count(self.world), 0)
        self.assertFalse(
            [item for item in self.multiworld.itempool
             if item.name == items.SEND_TOKEN]
        )

    def test_the_pool_is_still_exactly_full_without_them(self) -> None:
        """The five slots go back to native draws, not nowhere."""

        self.assertEqual(
            len(self.multiworld.itempool),
            len(self.multiworld.get_unfilled_locations(self.player)),
        )

    def test_a_shop_shelf_accepts_one(self) -> None:
        """Unlike gold and traps, which every shelf refuses.

        A token for sale is a sensible thing to buy, and a token in another
        Azure Dreams world is a sensible thing to find - so this asserts
        the ABSENCE of the rule that gold and traps carry.
        """

        token = self.world.create_item(items.SEND_TOKEN)
        for slot in range(locations.SHOP_LOCATION_COUNT):
            location = self.world.get_location(locations.shop_location_name(slot))
            with self.subTest(slot=slot):
                self.assertTrue(location.item_rule(token))

    def test_it_is_not_local_only(self) -> None:
        """Traps are pinned to their own world; tokens are not.

        Tokens are granted through the client into a plain counter, with
        no tower-side machinery to be resident, so another player's world
        is a perfectly good home for one.
        """

        self.assertNotIn(
            items.SEND_TOKEN,
            self.multiworld.worlds[self.player].options.local_items.value,
        )


class TestSendTokenItemWithASecondAzureDreamsPlayer(unittest.TestCase):
    """Two AD slots: now a token has somewhere to go, so all five are in."""

    def setUp(self) -> None:
        from Fill import distribute_items_restrictive
        from test.general import setup_multiworld
        from worlds.generic.Rules import locality_rules

        from ..world import AzureDreamsWorld

        self.multiworld = setup_multiworld([AzureDreamsWorld, AzureDreamsWorld])
        locality_rules(self.multiworld)
        distribute_items_restrictive(self.multiworld)

    def test_each_player_gets_five(self) -> None:
        for player in self.multiworld.get_game_players(items.GAME_NAME):
            with self.subTest(player=player):
                self.assertEqual(
                    items.send_token_count(self.multiworld.worlds[player]),
                    items.SEND_TOKEN_COUNT,
                )
        placed = [
            location.item
            for location in self.multiworld.get_locations()
            if location.item is not None and location.item.name == items.SEND_TOKEN
        ]
        self.assertEqual(
            len(placed),
            items.SEND_TOKEN_COUNT * 2,
            "two Azure Dreams players should contribute five tokens each",
        )


class TestSendTokenBanking(unittest.TestCase):
    """The durable count of tokens delivered from the multiworld."""

    def test_the_banked_counter_is_zeroed_on_a_fresh_save(self) -> None:
        """Zero, while the count itself starts at one.

        The starting token is the initializer's gift, not a delivery. If
        this were seeded to one as well, the client would compare a
        history with one token against a bank of one and conclude the
        first pool token had already been handed over.
        """

        initializer = patch._build_seed_state_initializer()
        self.assertIn(
            patch._i(
                0x2B, 11, 0,
                patch.SEND_TOKEN_BANKED_ADDRESS - patch.PERSISTENT_STATE_ADDRESS,
            ).to_bytes(4, "little"),
            initializer,
        )

    def test_it_sits_past_the_magic_and_inside_the_checkpoint(self) -> None:
        # Past ADSV and past the count/magic pair, so no version bump; and
        # below 0x80016000, so a checkpoint restore rolls the bank back
        # with the count and the receive cursor together.
        self.assertGreaterEqual(
            patch.SEND_TOKEN_BANKED_ADDRESS,
            patch.SEND_TOKEN_COUNT_ADDRESS + patch.SEND_TOKEN_MAGIC_OFFSET + 4,
        )
        self.assertLessEqual(patch.SEND_TOKEN_BANKED_ADDRESS + 4, 0x8001_6000)
        self.assertGreaterEqual(
            patch.SEND_TOKEN_BANKED_ADDRESS,
            patch.PERSISTENT_STATE_ADDRESS + patch.PERSISTENT_STATE_SIZE,
        )

    def test_the_adsv_record_was_not_grown(self) -> None:
        """0.9.84's burn: a new ADSV field landed on the shortcut carrier.

        The bank is a neighbour of the token count rather than a new
        journal field precisely so this stays true - no version bump, no
        re-initialized saves, no chance of overrunning into a carrier that
        something else zero-writes.
        """

        self.assertEqual(patch.PERSISTENT_STATE_SIZE, 0x2C)
        self.assertEqual(patch.PERSISTENT_STATE_VERSION, 3)


if __name__ == "__main__":
    unittest.main()
