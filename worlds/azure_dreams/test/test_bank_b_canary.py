"""TESTING - the bank-B tail canary (patch.BANK_B_CANARY_TEST). Delete with it.

Executes the resident fill stub in the R3000A model and checks that it stamps
exactly [START, END) with 0xFF, touches nothing outside, and returns; and that
the section contributes nothing to a seed when the flag is off.
"""

from __future__ import annotations

import struct
import unittest
from unittest import mock

from .. import patch, save_removal
from . import mips_sim


class TestBankBCanaryStub(unittest.TestCase):
    def test_stub_fits_its_donor_string(self) -> None:
        stub = patch.build_bank_b_canary_stub()
        self.assertEqual(len(stub) % 4, 0)
        self.assertLessEqual(len(stub), patch.BANK_B_CANARY_STUB_CAPACITY)

    def test_stub_fills_exactly_the_region_and_returns(self) -> None:
        memory = mips_sim.Memory()
        memory.load_bytes(patch.BANK_B_CANARY_STUB_ADDRESS, patch.build_bank_b_canary_stub())
        # Guard words on both sides must survive untouched.
        memory.write32(patch.BANK_B_CANARY_START - 4, 0x1234_5678)
        memory.write32(patch.BANK_B_CANARY_END, 0x9ABC_DEF0)

        cpu = mips_sim.Cpu(memory)
        cpu.run(patch.BANK_B_CANARY_STUB_ADDRESS, limit=20_000)

        for address in range(patch.BANK_B_CANARY_START, patch.BANK_B_CANARY_END, 4):
            self.assertEqual(memory.read32(address), 0xFFFF_FFFF, hex(address))
        self.assertEqual(memory.read32(patch.BANK_B_CANARY_START - 4), 0x1234_5678)
        self.assertEqual(memory.read32(patch.BANK_B_CANARY_END), 0x9ABC_DEF0)

    def test_region_is_bank_b_mirror_of_the_certified_window(self) -> None:
        bank_a, bank_b = 0x801C_9E40, 0x801D_A714
        window_start, window_end = patch.SEED_BLOCK_ADDRESS, 0x801D_A700
        self.assertEqual(patch.BANK_B_CANARY_START - bank_b, window_start - bank_a)
        self.assertEqual(patch.BANK_B_CANARY_END - bank_b, window_end - bank_a)

    def test_hook_is_the_boot_init_epilogue_jump(self) -> None:
        patches = dict(patch.iter_bank_b_canary_slus_file_patches())
        hook_offset = save_removal.slus_runtime_to_file_offset(patch.BANK_B_CANARY_HOOK_ADDRESS)
        word = struct.unpack("<I", patches[hook_offset])[0]
        self.assertEqual(word >> 26, 0x02)  # j
        self.assertEqual((word & 0x03FF_FFFF) << 2, patch.BANK_B_CANARY_STUB_ADDRESS & 0x0FFF_FFFF)

    def test_flag_off_contributes_nothing(self) -> None:
        with mock.patch.object(patch, "BANK_B_CANARY_TEST", False):
            self.assertEqual(patch.iter_bank_b_canary_slus_file_patches(), ())


if __name__ == "__main__":
    unittest.main()
