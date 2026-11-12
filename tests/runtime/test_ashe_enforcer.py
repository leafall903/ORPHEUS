"""Actual Enforcer AI-root -> weapon eligibility -> 25 ballistic pellets -> Ashe.

The pair lookup and ring geometry are scoped reconstruction. No native engine
predicate is forced true, and this does not claim complete Enforcer AI/specials.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_pve_npcs as fixtures
from test_ashe_impulses import wall_scene

from ow174.game import npc, npc_control
from ow174.game.script import expr

CONTROLLERS = Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", "."))


@unittest.skipUnless(fixtures.CATALOG.is_file() and fixtures.BUNDLE.is_file() and CONTROLLERS.is_file(),
                     "requires actual NPC AI-root metadata")
class EnforcerTests(unittest.TestCase):
    fight = fixtures.AshePveNpcTests.fight
    press = fixtures.AshePveNpcTests.press
    spawn = fixtures.AshePveNpcTests.spawn

    @classmethod
    def setUpClass(cls):
        cls.controller_patch = patch.dict(os.environ, {"ORPHEUS_PVE_GRAPH_EXTENSION": str(CONTROLLERS)})
        cls.controller_patch.start()
        fixtures.AshePveNpcTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass.__func__(cls)
        cls.controller_patch.stop()

    def setup(self, distance=8, *, ally=False):
        definition = next(item for item in self.catalog.values() if item.name == "Enforcer")
        run, player, bot = self.spawn(definition, distance, ally=ally)
        player.mover.yaw, player.mover.pitch = fixtures.fixtures.aim(run.match.combat._eye(player),
                                                                   (0, bot.position[1] + 1, distance))
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        return run, player, bot, run.match.combat.vitals_of(target)

    def fire_one(self, run, player, bot):
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(160):
            run.step({})
            if any(rocket.shooter is bot for rocket in run.match.combat.rockets):
                break
        rockets = [rocket for rocket in run.match.combat.rockets if rocket.shooter is bot]
        self.assertEqual(len(rockets), 25)
        self.assertTrue(all(rocket.speed == 80 for rocket in rockets))
        self.assertTrue(all(rocket.left == 1.25 for rocket in rockets))
        self.assertTrue(all(rocket.direct.amount == 2 for rocket in rockets))
        return rockets

    def test_actual_authored_controller_gate_and_all_25_projectiles_damage_ashe(self):
        run, player, bot, vitals = self.setup()
        context = bot.body_script.control_context
        self.assertNotEqual(context.body, context.controller)
        self.assertEqual(context.definition, 0x040000000000227A)
        component = bot.body_script.component
        weapon = next(item for item in component.instances.values() if item.graph.index == 0x13EB)
        slot = context.controller_component.entity_var(9134, create=False)
        self.assertTrue(slot.value())
        self.assertIn((0x13EA, 31), [(link.state.instance.graph.index, link.state.node.pos)
                                    for link in slot.links])
        self.assertIs(context.native({"$": "STU_B96D3A98", "m_entity": {"$": "STU_B5A0CAF0"}},
                                     weapon), NotImplemented)
        self.fire_one(run, player, bot)
        for _ in range(12):
            run.step({})
        self.assertEqual(200 - vitals.total, 50)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(90):
            run.step({})
        self.assertEqual(vitals.total, 150)
        self.assertFalse(bot.attack_input)
        self.assertNotIn(20, component.held)

    def test_stopping_real_eligibility_stack_closes_weapon_gate(self):
        run, _player, bot, _vitals = self.setup()
        context = bot.body_script.control_context
        weapon = next(item for item in bot.body_script.component.instances.values()
                      if item.graph.index == 0x13EB)
        self.assertTrue(expr.truthy(weapon.evaluate(weapon.graph.node(1).field("m_condition"))))
        slot = context.controller_component.entity_var(9134)
        for link in list(slot.links):
            link.state.end(False)
        bot.body_script.component.run_now()
        self.assertFalse(expr.truthy(weapon.evaluate(weapon.graph.node(1).field("m_condition"))))
        self.assertFalse(weapon.state(weapon.graph.node(2).state).active)
        self.assertGreater(run.frame, 0)

    def test_in_flight_pellets_stop_at_static_wall(self):
        run, player, bot, vitals = self.setup()
        self.fire_one(run, player, bot)
        run.match.collision_world = wall_scene(4)
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(any(rocket.shooter is bot for rocket in run.match.combat.rockets))

    def test_distant_volley_has_spread_misses_and_authored_projectile_falloff(self):
        run, player, bot, vitals = self.setup(distance=35)
        with patch.object(run.match.combat, "_damage", wraps=run.match.combat._damage) as hits:
            self.fire_one(run, player, bot)
            for _ in range(35):
                run.step({})
        amounts = [call.args[2] for call in hits.call_args_list
                   if call.args[0] is bot and call.args[1].owner is player]
        self.assertTrue(amounts)
        for amount in amounts:
            self.assertAlmostEqual(amount, 0.4, places=6)  # authored2 * far floor0.2
        damage = 200 - vitals.total
        self.assertAlmostEqual(damage, sum(amounts))
        self.assertGreater(damage, 0)
        self.assertLess(damage, 10)  # 25*2*.2 is the maximum before spread misses

    def test_allied_enforcer_cannot_be_started_or_damage_ashe(self):
        run, player, bot, vitals = self.setup(ally=True)
        with self.assertRaises(ValueError):
            npc_control.start(run.match, player)
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(bot.firing)

    def test_body_death_and_removal_retire_controller_without_identity_reuse(self):
        run, player, bot, _vitals = self.setup()
        context = bot.body_script.control_context
        root = context.controller_component
        target = next(item for item in run.match.combat._targets() if item.owner is bot)
        run.match.combat._damage(player, target, 1000, False, None, run.frame)
        self.assertTrue(bot.dead)
        self.assertTrue(context.retired)
        self.assertTrue(root.owner_dead)
        self.assertEqual(root.instances, {})
        self.assertIsNone(context.variable(context.controller, 9134, create=True))
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        definition = next(item for item in self.catalog.values() if item.name == "Enforcer")
        fresh = npc.spawn(run.match, definition, (0, 1, 8), team=1)
        new_context = fresh.body_script.control_context
        self.assertGreater(fresh.entity, context.controller)
        self.assertNotIn(new_context.controller, (context.body, context.controller))
        self.assertEqual(run.match.combat.next_bot, new_context.controller + 1)


if __name__ == "__main__":
    unittest.main()
