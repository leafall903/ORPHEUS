"""Explicit prepared Talon mover capsules; skeletal hitboxes/muzzles remain open."""

import json
import os
import unittest

import test_orisa_combat as combat_fixture
import test_orisa_npc as fixture
from test_game_combat import aim

from ow174.game.commands import FIRE
from ow174.game.mover import mover_data
from ow174.paths import DATA_DIR

DIMENSIONS = {"Trooper": (1.7, 0.5), "Enforcer": (1.9, 0.5), "Sniper": (1.75, 0.5),
              "Assassin": (1.5, 0.5), "Heavy Assault": (2.3, 0.7)}


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_MOVERS_REQUIRED") == "1",
                     "explicit43-entry NPC mover runtime required")
class OrisaNpcGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.OrisaTalonTests.setUpClass()
        cls.catalog = fixture.OrisaTalonTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixture.OrisaTalonTests.tearDownClass()

    def test_all_five_capsules_have_explicit_runtime_rows_instead_of_default_dimensions(self):
        rows = json.loads((DATA_DIR / "game_movers_174.json").read_text(encoding="utf-8"))
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                key = f"0x{definition.body:016X}"
                self.assertIn(key, rows)
                run, _, bot = fixture.OrisaTalonTests().arena(definition)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                height, radius = DIMENSIONS[definition.name]
                self.assertAlmostEqual(target.height, height)
                self.assertAlmostEqual(target.radius, radius)
                self.assertAlmostEqual(mover_data(bot.body).stand_height, height)

    def shoot(self, definition, *, x_offset=0, height=1, wall=False):
        run, player, bot = fixture.OrisaTalonTests().arena(definition)
        x, y, z = player.mover.position
        bot.position = (x + x_offset, y, z + 8)
        if wall:
            combat_fixture.OrisaCombatTests().wall(run, player, 4)
        before = run.match.combat.vitals[bot.entity].total
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + height, z + 8))
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(8):
            run.step({})
        return before - run.match.combat.vitals[bot.entity].total

    def test_actual_primary_lateral_boundary_uses_heavy_point_seven_radius(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                loss = self.shoot(definition, x_offset=0.6)
                if definition.name == "Heavy Assault":
                    self.assertGreater(loss, 0)
                else:
                    self.assertEqual(loss, 0)

    def test_actual_primary_high_boundary_uses_authored_capsule_height(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                loss = self.shoot(definition, height=2.05)
                if definition.name == "Heavy Assault":
                    self.assertGreater(loss, 0)
                else:
                    self.assertEqual(loss, 0)

    def test_static_wall_blocks_actual_primary_for_all_five_prepared_capsules(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.assertEqual(self.shoot(definition, wall=True), 0)
