"""The forced trap while Koh has something in hand.

A trap picked up while holding an item did not spring until the item left his
hands, because the game swaps Koh's control handler while `unit+0x1C &
0x100000` is set and the stub's only call site lives in the OTHER one. These
run the trampoline that fixes it in the R3000A model: it must call the stub
when a request is pending, must NOT reach the receive dispatcher when one is
not (that is what keeps incoming items paused while holding, which is
deliberate), and must return the load it displaced either way.
"""

from __future__ import annotations

import struct
import unittest

from .. import patch, save_removal
from . import mips_sim


class TestCarryingTrapTrampoline(unittest.TestCase):
    WINDOW_FLAGS = 0x1234

    def _run(self, request: int) -> tuple[int, list[int]]:
        """Returns (v0 at the return site, the addresses it called)."""

        memory = mips_sim.Memory()
        memory.load_bytes(
            patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS,
            patch.build_carrying_trap_trampoline(),
        )
        memory.write8(patch.FORCED_TRAP_REQUEST_ADDRESS, request)
        # The halfword the displaced `lhu v0,0x2(s0)` reads.
        memory.write8(patch.CARRYING_TRAP_DISPLACED_ADDRESS, self.WINDOW_FLAGS & 0xFF)
        memory.write8(
            patch.CARRYING_TRAP_DISPLACED_ADDRESS + 1, self.WINDOW_FLAGS >> 8
        )

        called: list[int] = []
        cpu = mips_sim.Cpu(memory)

        def call(address: int):
            def stub(_cpu: mips_sim.Cpu) -> None:
                called.append(address)

            return stub

        def arrive(_cpu: mips_sim.Cpu) -> None:
            called.append(patch.CARRYING_TRAP_RETURN_ADDRESS)
            # End the harness run here: this is the caller's own code.
            _cpu.registers[31] = 0xDEAD_0000

        cpu.stubs[patch.FORCED_TRAP_STUB_ADDRESS] = call(patch.FORCED_TRAP_STUB_ADDRESS)
        cpu.stubs[patch.CARRYING_TRAP_RETURN_ADDRESS] = arrive
        value = cpu.run(patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS, limit=200)
        return value, called

    def test_it_fits_its_donor_string(self) -> None:
        trampoline = patch.build_carrying_trap_trampoline()
        self.assertEqual(len(trampoline) % 4, 0)
        self.assertLessEqual(
            len(trampoline), patch.CARRYING_TRAP_TRAMPOLINE_CAPACITY
        )

    def test_no_request_returns_the_displaced_load_and_calls_nothing(self) -> None:
        value, called = self._run(request=0)
        self.assertEqual(called, [patch.CARRYING_TRAP_RETURN_ADDRESS])
        self.assertEqual(value & 0xFFFF, self.WINDOW_FLAGS)

    def test_a_pending_request_calls_the_stub_then_returns(self) -> None:
        value, called = self._run(request=11)  # poison
        self.assertEqual(
            called,
            [patch.FORCED_TRAP_STUB_ADDRESS, patch.CARRYING_TRAP_RETURN_ADDRESS],
        )
        self.assertEqual(value & 0xFFFF, self.WINDOW_FLAGS)

    def test_it_never_reaches_the_receive_dispatcher(self) -> None:
        """The reason the request check is here and not delegated to the stub.

        Incoming items pause while Koh holds something - emergent from the
        hook's placement, and deliberate now. The stub answers "no request" by
        tail-jumping to the receive dispatcher, so entering it unconditionally
        from this handler would start delivering into a held hand.
        """

        trampoline = patch.build_carrying_trap_trampoline()
        words = struct.unpack(f"<{len(trampoline) // 4}I", trampoline)
        targets = {
            (word & 0x03FF_FFFF) << 2 | 0x8000_0000
            for word in words
            if word >> 26 in (0x02, 0x03)
        }
        self.assertNotIn(patch.RECEIVE_ITEM_DISPATCHER_ADDRESS, targets)
        self.assertEqual(
            targets,
            {patch.FORCED_TRAP_STUB_ADDRESS, patch.CARRYING_TRAP_RETURN_ADDRESS},
        )


class TestCarryingTrapHookSites(unittest.TestCase):
    def test_the_hook_is_a_jal_to_the_trampoline(self) -> None:
        (offset, data), = patch.iter_carrying_trap_dungeon_file_patches()
        self.assertEqual(
            offset,
            save_removal._dungeon_runtime_to_file_offset(
                patch.CARRYING_TRAP_HOOK_ADDRESS
            ),
        )
        word = struct.unpack("<I", data)[0]
        self.assertEqual(word >> 26, 0x03)  # jal
        self.assertEqual(
            (word & 0x03FF_FFFF) << 2,
            patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS & 0x0FFF_FFFF,
        )

    def test_the_hook_site_is_the_carrying_handler_load(self) -> None:
        """0x8008ECCC is `lhu v0,0x2(s0)`, and the word after it is the nop
        the jal needs for its delay slot. Both verified against the original
        disc 2026-08-18 (raw 0x1C5F104)."""

        self.assertEqual(patch.CARRYING_TRAP_HOOK_ORIGINAL_WORD, 0x9602_0002)
        self.assertEqual(
            patch.CARRYING_TRAP_RETURN_ADDRESS, patch.CARRYING_TRAP_HOOK_ADDRESS + 8
        )

    def test_the_trampoline_lands_on_its_donor_string(self) -> None:
        (offset, data), = patch.iter_carrying_trap_slus_file_patches()
        self.assertEqual(
            offset,
            save_removal.slus_runtime_to_file_offset(
                patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS
            ),
        )
        self.assertLessEqual(len(data), patch.CARRYING_TRAP_TRAMPOLINE_CAPACITY)

    def test_it_does_not_share_the_canary_string(self) -> None:
        """Two dead RCS stamps, two tenants. Sharing one would make either
        change silently overwrite the other."""

        self.assertNotEqual(
            patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS, patch.BANK_B_CANARY_STUB_ADDRESS
        )
        self.assertGreaterEqual(
            abs(
                patch.CARRYING_TRAP_TRAMPOLINE_ADDRESS
                - patch.BANK_B_CANARY_STUB_ADDRESS
            ),
            patch.CARRYING_TRAP_TRAMPOLINE_CAPACITY,
        )


if __name__ == "__main__":
    unittest.main()
