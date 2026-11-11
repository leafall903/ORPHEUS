"""NPC mover-data revalidation with the parent's immutable43-row prepared table.

Only extracted height/radius/speed inputs are covered; static fixtures do not
establish native hitboxes, animations, full AI or deployed map collision.
"""

import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_healing_status as status_tests
import test_game_mercy_npc as npc_tests

from ow174.game import mover

NPC_MOVERS = Path("D:/overwatch 1.7.4/verification/retribution/npc-navigation-runtime/data") / (
    "game_movers_174.json"
)
EXPECTED_SHA256 = "e3943bc4299d43fd5478d5f074d449a1ca70d817a10a66c36466ff3dd33de84c"
EXPECTED = {"Trooper": (1.7, 0.5, 5.75), "Enforcer": (1.9, 0.5, 5.0),
            "Sniper": (1.75, 0.5, 4.5), "Assassin": (1.5, 0.5, 14.0),
            "Heavy Assault": (2.3, 0.7, 2.2)}


@unittest.skipUnless(NPC_MOVERS.is_file(), "parent's extracted NPC mover input required")
class MercyNpcGeometryTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(hashlib.sha256(NPC_MOVERS.read_bytes()).hexdigest(), EXPECTED_SHA256)
        self.enterContext(patch.object(mover, "MOVERS_PATH", NPC_MOVERS))
        mover._movers.cache_clear()
        self.addCleanup(mover._movers.cache_clear)

    def npc_fixture(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def status_fixture(self):
        fixture = status_tests.MercyHealingStatusTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_actual_target_capsules_use_all_five_extracted_mover_profiles(self):
        fixture = self.npc_fixture()
        for definition in fixture.catalog.values():
            with self.subTest(npc=definition.name):
                _, _, _, target = fixture.spawn(definition, 0)
                data = mover.mover_data(definition.body)
                self.assertIs(data, mover.loaded_mover_table()[definition.body])
                self.assertEqual((target.height, target.radius, data.run_forward), EXPECTED[definition.name])

    def test_staff_heals_each_npc_with_its_extracted_capsule(self):
        self.npc_fixture().test_each_friendly_npc_receives_real_staff_healing()

    def test_blaster_hits_and_hostile_staff_rejection_with_extracted_capsules(self):
        self.npc_fixture().test_each_hostile_npc_rejects_staff_and_takes_blaster_projectiles()

    def test_actual_melee_and_static_wall_occlusion_with_extracted_capsules(self):
        self.npc_fixture().test_each_npc_takes_actual_melee_only_without_an_intervening_static_wall()

    def test_guardian_targeting_and_faction_with_extracted_capsules(self):
        self.npc_fixture().test_guardian_angel_reaches_each_friendly_npc_and_rejects_hostile_npcs()

    def test_antiheal_and_expiry_with_extracted_npc_capsules(self):
        self.status_fixture().test_all_five_npc_bodies_reject_staff_healing_during_original_antiheal_then_recover()

    def test_amp_and_removal_with_extracted_npc_capsules(self):
        self.status_fixture().test_all_five_npc_bodies_receive_original_amp_and_manual_removal_retires_status()


if __name__ == "__main__":
    unittest.main()
