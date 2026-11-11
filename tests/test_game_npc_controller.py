"""Actual227A controller roots/foreign9134 -> actual13EB Enforcer volleys.

Manual input/flat collision and nonreplicated controller hosting are explicit
server reconstructions; no native AI acquisition or client acceptance is claimed.
"""

import os
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import test_game_winston_npc as fixtures
import test_server_snapshot as snapshot_tests
from test_game_mover import box, wall

from ow174.game import combat, npc
from ow174.game.collision import World
from ow174.game.script import expr, graph


class EnforcerControllerTests(unittest.TestCase):
    @classmethod
    def clear_graphs(cls):
        fixtures.WinstonNpcTests.clear_graphs()

    @classmethod
    def restore_graphs(cls):
        cls.graph_patch.stop()
        cls.clear_graphs()

    @classmethod
    def setUpClass(cls):
        folder = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        catalog = os.environ.get('ORPHEUS_PVE_NPC_CATALOG')
        if not folder or not catalog:
            raise unittest.SkipTest('requires actual controller-enriched NPC inputs; acceptance OPEN')
        cls.definition = next(d for d in npc.load_catalog(Path(catalog)).values() if d.name == 'Enforcer')
        if cls.definition.controller_definition is None:
            raise unittest.SkipTest('requires original227A controller declaration; acceptance OPEN')
        cls.graph_patch = patch.object(graph, 'GRAPHS_PATH', Path(folder) / 'game_graphs_174.json.gz')
        cls.graph_patch.start()
        cls.clear_graphs()
        cls.addClassCleanup(cls.restore_graphs)
        if graph.entity_body(cls.definition.controller_definition) is None:
            raise unittest.SkipTest('requires original controller graph supplement; acceptance OPEN')

    def create(self, definition=None):
        return fixtures.WinstonNpcTests.create(self, definition or self.definition)

    def emit(self, run, match, bot, held=True):
        fixtures.WinstonNpcTests.emit_primary(self, run, match, bot, held)

    def health(self, match, player):
        return match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is player)).total

    def first_volley(self, run, match, bot):
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        before = weapon.state(14).counter
        peak = 0
        for _ in range(180):
            run.step({})
            self.emit(run, match, bot)
            peak = max(peak, sum(r.shooter is bot for r in match.combat.rockets))
            if weapon.state(14).counter > before:
                break
        self.assertGreater(weapon.state(14).counter, before)
        for _ in range(10):
            run.step({})
            self.emit(run, match, bot)
        return weapon, peak

    def test_original_controller_stack_supplies_foreign_gate_without_forced_predicate(self):
        run, match, player, bot = self.create()
        controller = bot.body_script.controller
        self.assertEqual(bot.controller_definition, 0x040000000000227A)
        self.assertEqual(controller.entity, bot.controller_entity)
        self.assertNotEqual(controller.entity, bot.entity)
        self.assertEqual({i.graph.index for i in controller.instances.values()},
                         {0x142A, 0xE5C, 0xC83, 0x13E9, 0x13EA})
        provider = next(i for i in controller.instances.values() if i.graph.index == 0x13EA)
        self.assertTrue(provider.state(provider.graph.node(31).state).active)
        self.assertIs(controller.entity_var(9134, create=False).value(), True)
        run.step({})
        self.emit(run, match, bot, False)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        selector = weapon.graph.node(1).field('m_condition')['m_configVars'][0]
        self.assertEqual(weapon.evaluate(selector), expr.Entity(controller.entity))
        self.assertIs(weapon.evaluate(weapon.graph.node(1).field('m_condition')), True)
        self.assertIs(match.script_actor_component(controller.entity), controller)
        self.assertFalse(any(t.entity == controller.entity for t in match.combat._targets()))
        self.assertEqual(self.health(match, player), 500)

    def test_twenty_five_physical_pellets_and_four_original_damage_entries_against_armour(self):
        for index, loss in enumerate((25, 50, 75, 100)):
            with self.subTest(difficulty_index=index):
                run, match, player, bot = self.create()
                # Explicit difficulty-array index; no mission difficulty is inferred.
                bot.body_script.component.set(9948, index)
                weapon, peak = self.first_volley(run, match, bot)
                self.assertEqual(peak, 25)
                self.assertEqual(self.health(match, player), 500 - loss)
                self.assertEqual(weapon.vars[53].value(), 3)
                self.assertEqual(len(bot.body_script.component.shots), 0)

    def test_missing_controller_declaration_does_not_fabricate_readiness(self):
        run, match, player, bot = self.create(replace(self.definition, controller_definition=None))
        self.assertIsNone(bot.body_script.controller)
        for _ in range(180):
            run.step({})
            self.emit(run, match, bot)
        self.assertEqual(self.health(match, player), 500)
        self.assertFalse(any(r.shooter is bot for r in match.combat.rockets))

    def test_retiring_real_controller_removes_stack_and_poll_closes_weapon_gate(self):
        run, match, player, bot = self.create()
        controller = bot.body_script.controller
        bot.body_script.retire_controller()
        self.assertTrue(controller.owner_dead)
        self.assertEqual(controller.instances, {})
        for _ in range(180):
            run.step({})
            self.emit(run, match, bot)
        self.assertEqual(self.health(match, player), 500)
        self.assertIsNone(controller.entity_var(9134, create=False).value())

    def test_original_volley_miss_team_and_wall_controls(self):
        for case in ('miss', 'ally', 'wall'):
            with self.subTest(case=case):
                run, match, player, bot = self.create()
                if case == 'miss':
                    bot.yaw = 16384
                elif case == 'ally':
                    bot.team = player.team
                else:
                    match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                  + wall(-10, 2, 10, 2))
                _weapon, peak = self.first_volley(run, match, bot)
                self.assertEqual(peak, 25)
                self.assertEqual(self.health(match, player), 500)

    def test_actual_bubble_consumes_pellets_with_falloff_and_floor_ordering(self):
        # At50m six downward rays of the original4-degree rings hit the ground
        # before the bubble. The other19 each deal2*0.2=0.4 to it. At25m all25
        # enter the bubble first and are also beyond the15m falloff endpoint.
        for distance, expected in ((10, 650), (25, 690), (50, 692.4)):
            with self.subTest(distance=distance):
                run, match, player, bot = self.create()
                bot.position = (0, 1, distance)
                run.step({player: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                bubble = match.combat.barriers.actors[player.body]
                _weapon, peak = self.first_volley(run, match, bot)
                for _ in range(40):
                    run.step({})
                    self.emit(run, match, bot, False)
                self.assertEqual(peak, 25)
                self.assertEqual(self.health(match, player), 500)
                self.assertAlmostEqual(bubble.health, expected, places=5)

    def test_body_and_controller_identities_are_unique_and_bad_definition_is_atomic(self):
        _run, match, _player, first = self.create()
        before = match.combat.next_bot
        with self.assertRaises(ValueError):
            npc.spawn(match, replace(self.definition, controller_definition=0x04000000000FFFFF),
                      (0, 1, 8), team=1)
        self.assertEqual(match.combat.next_bot, before)
        self.assertEqual(match.bots, [first])
        second = npc.spawn(match, self.definition, (0, 1, 8), team=1)
        identities = {first.entity, first.controller_entity, second.entity, second.controller_entity}
        self.assertEqual(len(identities), 4)
        self.assertEqual(match.combat.next_bot, before + 2)
        self.assertEqual(len(match.combat._targets()), 3)

    def test_real_tesla_death_and_explicit_remove_retire_controller_namespace(self):
        run, match, player, bot = self.create()
        controller = bot.body_script.controller
        match.combat.vitals[bot.entity].now = {combat.HEALTH_POOL: .5, combat.ARMOUR_POOL: 0,
                                             combat.SHIELDS_POOL: 0}
        run.step({player: (0, 0, 1)})
        self.assertTrue(bot.dead)
        self.assertTrue(controller.owner_dead)
        self.assertEqual(controller.instances, {})
        self.assertIn(bot, match.bots)
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertIsNone(match.script_actor_component(controller.entity))
        self.assertIsNone(match.script_actor_component(bot.entity))

    def test_release_cancels_subsequent_damage_and_fresh_press_can_shoot_again(self):
        run, match, player, bot = self.create()
        self.first_volley(run, match, bot)
        before = self.health(match, player)
        for _ in range(100):
            run.step({})
            self.emit(run, match, bot, False)
            self.assertEqual(self.health(match, player), before)
        self.first_volley(run, match, bot)
        self.assertEqual(self.health(match, player), before - 25)

    def test_original_ammo_reload_cadence_survives_two_magazines(self):
        run, match, player, bot = self.create()
        durable = combat.Vitals(player.body, 5000, armour=5000)
        match.combat.vitals[player.body] = durable
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        times, ammo = [], []
        previous = 0
        for _ in range(1200):
            run.step({})
            self.emit(run, match, bot)
            self.assertIsNotNone(bot.body_script)
            counter = weapon.state(14).counter
            if counter > previous:
                times.append(run.frame)
            previous = counter
            ammo.append(weapon.vars[53].value())
        self.assertGreaterEqual(len(times), 8)
        self.assertTrue(all(b - a >= 62 for a, b in pairwise(times)))
        self.assertIn(0, ammo)
        empty = ammo.index(0)
        self.assertIn(4, ammo[empty + 1:])
        self.assertEqual(durable.total, 10000 - len(times) * 25)

    def test_projectile_range_limit_and_original_far_damage_floor(self):
        for distance, expected in ((50, 499.8), (120, 500)):
            with self.subTest(distance=distance):
                run, match, player, bot = self.create()
                bot.position = (0, 1, distance)
                self.first_volley(run, match, bot)
                # At50m only the centre of the authored rings intersects the
                # original Winston capsule. Its2 damage is at the20% floor,
                # then armour halves it to0.2. At120m the100m flight ends first.
                for _ in range(100):
                    run.step({})
                    self.emit(run, match, bot, False)
                self.assertAlmostEqual(self.health(match, player), expected)
                self.assertFalse(any(r.shooter is bot for r in match.combat.rockets))

    def test_server_snapshot_observes_private_controller_causal_state(self):
        observer = snapshot_tests.ServerSnapshotTests()
        session, _peers, players = observer.scene()
        match = players[0].match
        bot = npc.spawn(match, self.definition, (0, 1, 4), team=1, auto_target=True)
        controller = bot.body_script.controller
        slot = controller.entity_var(9134, create=False)
        original = slot.base
        baseline = snapshot_tests.snapshot_digest(observer.capture(session))
        bot.spawner.pop('auto_target')
        self.assertNotEqual(snapshot_tests.snapshot_digest(observer.capture(session)), baseline)
        bot.spawner['auto_target'] = True
        self.assertEqual(snapshot_tests.snapshot_digest(observer.capture(session)), baseline)
        # Effective value remains the authored Stack's True; hidden backing
        # storage must still be represented without evaluating/running either VM.
        slot.base = 7
        self.assertNotEqual(snapshot_tests.snapshot_digest(observer.capture(session)), baseline)
        slot.base = original
        self.assertEqual(snapshot_tests.snapshot_digest(observer.capture(session)), baseline)
        bot.body_script.retire_controller()
        self.assertNotEqual(snapshot_tests.snapshot_digest(observer.capture(session)), baseline)


if __name__ == '__main__':
    unittest.main()
