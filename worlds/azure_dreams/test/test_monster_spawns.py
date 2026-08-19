"""The per-floor spawn planner and the carrier pool.

`docs/game/monster-ai.md` §5 (rosters) and §2c (which species can drop what
they carry) own the design.
"""

import unittest
from random import Random

from .. import monster_spawns as ms
from .. import species_move_fix as smf


# The eleven species whose package links the slim local death routine, which
# never reads the carried slot (monster-ai.md §2c, measured off the disc
# archive 2026-08-15).  Pinned by name so a pool change has to say why.
KID, IFRIT, FLAME, ARACHNE, BATTNEL = 0x04, 0x05, 0x06, 0x0C, 0x0D
UNIVERN, UNICORN, BALOON, KRAKEN, ZU, MANDARA = 0x11, 0x12, 0x19, 0x21, 0x26, 0x27
NO_DROP = {KID, IFRIT, FLAME, ARACHNE, BATTNEL, UNIVERN, UNICORN, BALOON, KRAKEN, ZU, MANDARA}


class TestCarrierPool(unittest.TestCase):
    def test_no_drop_death_species_are_the_measured_eleven(self) -> None:
        self.assertEqual(set(ms.NO_DROP_DEATH_SPECIES), NO_DROP)

    def test_pool_excludes_every_species_that_cannot_drop_or_flee(self) -> None:
        pool = set(ms.CARRIER_POOL)
        for unit in NO_DROP | {ms.GOLEM, ms.STEALTH, ms.MANOEVA, ms.BARONG, ms.TROLL}:
            self.assertNotIn(unit, pool, f"0x{unit:02X} must not be a carrier")

    def test_group_v_species_stay_in_the_pool_because_the_disc_fix_is_on(self) -> None:
        # Nyuel, Pulunpa, Volcano, Cyclone: attackers as retail built them,
        # kept because species_move_fix rewrites their walk on the disc.
        self.assertTrue(smf.CARRIER_GROUP_V_FIX)
        for unit in smf.GROUP_V_SPECIES - {ms.GOLEM}:
            self.assertIn(unit, ms.CARRIER_POOL, f"0x{unit:02X} should carry")

    def test_pool_is_drawn_from_wild_species_only(self) -> None:
        wild = {unit for floor in ms.VANILLA_SPAWN_TABLES for unit, _ in floor}
        self.assertTrue(set(ms.CARRIER_POOL) <= wild)
        # The measured drop-capable wild species: the pool after ride 3, minus
        # Troll (2026-08-17: its constructor pre-fills the carried slot).
        self.assertEqual(
            ms.CARRIER_POOL,
            (0x03, 0x08, 0x0A, 0x0E, 0x10, 0x14, 0x15, 0x17, 0x18, 0x1A,
             0x1B, 0x1C, 0x1D, 0x20, 0x22, 0x24, 0x25, 0x28, 0x29, 0x2A, 0x2B, 0x2D),
        )


class TestPlanFloorSpawns(unittest.TestCase):
    def test_every_carrier_is_from_the_pool_and_off_its_floor(self) -> None:
        for seed in range(50):
            tables, carriers = ms.plan_floor_spawns(Random(seed))
            self.assertEqual(len(tables), ms.SPAWN_TABLE_FLOORS)
            self.assertEqual(len(carriers), ms.SPAWN_TABLE_FLOORS)
            for index, (table, carrier) in enumerate(zip(tables, carriers)):
                self.assertEqual(len(table), 32)
                self.assertIn(carrier, ms.CARRIER_POOL, f"seed {seed} floor {index + 1}")
                native = {unit for unit, _ in ms.VANILLA_SPAWN_TABLES[index]}
                self.assertNotIn(carrier, native, f"seed {seed} floor {index + 1}")

    def test_protected_slots_survive_every_rewrite(self) -> None:
        for seed in range(50):
            tables, _ = ms.plan_floor_spawns(Random(seed))
            for floor, unit in ms.PROTECTED_SLOTS:
                table = tables[floor - 1]
                count = sum(1 for i in range(0, 32, 2) if table[i] == unit)
                self.assertEqual(count, 1, f"seed {seed} floor {floor} 0x{unit:02X}")


if __name__ == "__main__":
    unittest.main()
