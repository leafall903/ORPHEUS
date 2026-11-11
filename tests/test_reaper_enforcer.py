"""Actual Enforcer rings/context/projectile-wall evidence; manual policy only."""

import math
import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_npc as npc_tests

from ow174.game import npc_context
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.script import runtime


@unittest.skipUnless(npc_tests.PACK, "set ORPHEUS_NPC_RUNTIME_DIR for actual Enforcer")
class ReaperEnforcerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_tests.ReaperNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        npc_tests.ReaperNpcTests.tearDownClass()

    def scene(self):
        fixture = npc_tests.ReaperNpcTests()
        return fixture, fixture.scene(fixture.catalog["2"])

    def test_actual_twenty_five_projectile_rays_and_wall_after_emission(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                fixture, (run, owner, _, bot) = self.scene()
                fight = run.match.combat
                fixture.engage(run, owner, bot)
                for _ in range(180):
                    run.step({})
                    if fight.rockets:
                        break
                rays = [r for r in fight.rockets if r.shooter is bot]
                self.assertEqual(len(rays), 25)
                self.assertEqual(len({r.direction for r in rays}), 25)
                self.assertTrue(all(r.speed == 80 and r.direct.amount == 2 for r in rays))
                axis = rays[0].direction
                dots = [sum(a*b for a, b in zip(axis, r.direction, strict=True)) for r in rays]
                angles = [math.degrees(math.acos(min(1, dot))) for dot in dots]
                self.assertEqual(sum(abs(a) < 0.00001 for a in angles), 1)
                self.assertEqual(sum(abs(a - 2) < 0.00001 for a in angles), 8)
                self.assertEqual(sum(abs(a - 4) < 0.00001 for a in angles), 16)
                if wall:
                    x, y, z = owner.mover.position
                    middle = (z + bot.position[2]) / 2
                    floor = [(-500, y, -500, -500, y, 500, 500, y, 500),
                             (-500, y, -500, 500, y, 500, 500, y, -500)]
                    sides = [(x-5, y, middle, x+5, y, middle, x+5, y+5, middle),
                             (x-5, y, middle, x+5, y+5, middle, x-5, y+5, middle),
                             (x-5, y, middle, x+5, y+5, middle, x+5, y, middle),
                             (x-5, y, middle, x-5, y+5, middle, x+5, y+5, middle)]
                    run.match.collision_world = World(floor + sides, name="Enforcer in-flight wall")
                    owner.mover.use(world=run.match.collision_world)
                for _ in range(5):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250 if wall else 200)
                self.assertFalse(any(r.shooter is bot for r in fight.rockets))

    def test_aggregate_health_query_tracks_actual_reaper_shells_and_death(self):
        _, (run, owner, victim, bot) = self.scene()
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
        cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(component.world.native(cfg, instance), 350)
        for expected in (230, 110, 0):
            reaper_tests.ReaperTests().fire(run, owner, victim)
            self.assertEqual(component.world.native(cfg, instance), expected)
            for _ in range(40):
                run.step({})
        self.assertTrue(bot.dead)
        wrong = {"$": "STU_9B7648C3", "m_entity": {"$": "STUConfigVarInt", "m_value": 9}}
        self.assertIs(npc_context.native(component.world, wrong, instance), NotImplemented)

    def test_declared_controller_bits_are_passively_causal_in_component_snapshot(self):
        _, (_run, _owner, _, bot) = self.scene()
        component = bot.body_script.component
        self.assertEqual(component.world.controller_components, (75,))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(export_component_vm(component))
            component.world.controller_components = (75, 92)
            after = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(before, after)
        self.assertFalse(npc_context.controller_predicate(component.world.controller_components))
        self.assertTrue(npc_context.controller_predicate((75,)))

    def test_long_range_projectile_hits_use_authored_falloff_floor(self):
        fixture, (run, owner, _, bot) = self.scene()
        x, y, z = bot.position
        owner.mover.position[:] = [x, y, z - 30]
        owner.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        fixture.engage(run, owner, bot)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(180):
                run.step({})
                if fight.vitals[owner.body].total < 250:
                    break
            amounts = [call.args[2] for call in damage.call_args_list if call.args[0] is bot]
        self.assertTrue(amounts)
        self.assertTrue(all(abs(amount - 0.4) < 0.00001 for amount in amounts))
        self.assertAlmostEqual(250 - fight.vitals[owner.body].total, sum(amounts))
