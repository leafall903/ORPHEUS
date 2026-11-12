"""Explicit actual227A host and reconstructed Enforcer projectile pattern."""

import math
import unittest
from unittest.mock import patch

import test_baptiste_field as field_fixture
import test_baptiste_pve as pve_fixture
import test_server_snapshot as snapshot_fixture
from test_baptiste_matrix import deploy_matrix
from test_game_combat import aim

from ow174.game import npc
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.collision import World
from ow174.game.script import expr, graph
from ow174.game.script.bot import BotScript


class BaptisteNpcControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pve_fixture.BaptistePveTests.setUpClass()
        record = graph.entity_body(0x0400000000002279)
        if record.get('controller') != '0x040000000000227A':
            raise RuntimeError('requires explicit controller-runtime six graphs/227A/2279 association')
        cls.definition = next(d for d in pve_fixture.BaptistePveTests.catalog.values()
                              if d.name == 'Enforcer')

    @classmethod
    def tearDownClass(cls):
        pve_fixture.BaptistePveTests.tearDownClass()

    def arena(self, *, distance=8, team=1):
        self.run, self.bap, self.bot, self.fight, _ = pve_fixture.BaptistePveTests().make(
            self.definition, team=team, distance=distance)
        self.script = self.bot.body_script
        self.controller = self.script.controller
        self.weapon = next(i for i in self.script.component.instances.values() if i.graph.index == 0x13EB)

    def gate(self, weapon=None):
        weapon = self.weapon if weapon is None else weapon
        return weapon.evaluate(weapon.graph.node(1).field('m_condition'))

    def fire(self, frames=100):
        shots = 0
        self.launched = []
        for delta in range(1, frames + 1):
            frame = self.run.frame + delta
            self.fight.tick = frame
            self.script.advance_live(frame, self.bot.position, edges=[(20, True)] if delta == 1 else ())
            for state, number, when, activation in list(self.script.component.shots):
                shots += 1
                before = len(self.fight.rockets)
                self.fight._shoot(self.bot, None, state, number, when, activation=activation)
                self.launched.extend(self.fight.rockets[before:])
            self.script.component.shots.clear()
            for rocket in list(self.fight.rockets):
                self.fight._fly(rocket)
        return shots

    def test_spawn_uses_authored_five_roots_and_separate_reserved_entity(self):
        self.arena()
        self.assertNotEqual(self.controller.entity, self.bot.entity)
        self.assertNotIn(self.controller.entity, [b.entity for b in self.run.match.bots])
        roots = [i.graph.index for i in self.controller.instances.values() if i.parent is None]
        self.assertEqual(roots, [0x142A, 0xE5C, 0xC83, 0x13E9, 0x13EA])
        self.assertTrue(self.controller.entity_var(9134).value())
        self.assertTrue(self.gate())
        cfg = {'$': 'STU_F7262B36', 'm_801B298F': {'$': 'STU_B5A0CAF0'}}
        self.assertEqual(self.weapon.evaluate(cfg), expr.Entity(self.controller.entity))

    def test_ordinary_input_launches_three_authored_rings_and_deals_close_damage(self):
        self.arena(distance=3)
        self.bot.yaw = -32768
        self.assertEqual(self.fire(), 1)
        self.assertEqual(self.fight.vitals[self.bap.body].total, 150)
        self.assertEqual(len({r.direction for r in self.launched}), 25)
        angles = [math.degrees(math.acos(max(-1, min(1, -r.direction[2])))) for r in self.launched]
        self.assertLess(min(angles), 2)
        self.assertGreater(max(angles), 2)
        self.assertLess(max(angles), 6)
        for rocket in self.launched:
            self.assertAlmostEqual(math.hypot(*rocket.direction), 1, places=6)
        self.assertTrue(all(r.shot is None for r in self.launched))

    def test_projectile_pattern_loses_hits_and_damage_at_distance(self):
        self.arena(distance=20)
        self.bot.yaw = -32768
        with patch.object(self.fight, '_apply_hit', wraps=self.fight._apply_hit) as hits:
            self.assertEqual(self.fire(), 1)
        self.assertGreater(hits.call_count, 0)
        self.assertLess(hits.call_count, 25)
        for hit in hits.call_args_list:
            self.assertAlmostEqual(hit.args[3], .4, places=6)
        self.assertAlmostEqual(self.fight.vitals[self.bap.body].total, 200 - .4 * hits.call_count, places=5)

    def test_allied_player_is_ignored_by_projectile_pattern(self):
        self.arena(distance=3, team=0)
        self.bot.yaw = -32768
        target = next(t for t in self.fight._targets() if t.owner is self.bap)
        vitals = self.fight.vitals_of(target)
        self.assertEqual(self.fire(), 1)
        self.assertEqual(vitals.total, 200)

    def test_authored_range_retires_pellets_before_reaching_player(self):
        self.arena(distance=102)
        self.bot.yaw = -32768
        target = next(t for t in self.fight._targets() if t.owner is self.bap)
        vitals = self.fight.vitals_of(target)
        self.assertEqual(self.fire(), 1)
        self.assertEqual(len(self.launched), 25)
        self.fight.tick += 80
        for rocket in list(self.fight.rockets):
            self.fight._fly(rocket)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(vitals.total, 200)

    def test_wall_blocks_selected_projectile(self):
        self.arena()
        self.bot.yaw = -32768
        self.run.match.collision_world = World([(-10, -10, 4, 10, -10, 4, 0, 10, 4)])
        self.assertGreater(self.fire(), 0)
        self.assertEqual(self.fight.vitals.get(self.bap.body).total, 200)

    def test_actual_field_holds_its_floor_against_enforcer_volley(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.arena(distance=3)
                field = field_fixture.BaptisteFieldTests().cast(self.run, self.bap, self.fight)
                if not protected:
                    self.fight.destroy_deployable(field.entity)
                target = next(t for t in self.fight._targets() if t.owner is self.bap)
                vitals = self.fight.vitals_of(target)
                vitals.damage(160)  # Place the fixture below one volley of remaining health.
                self.bot.yaw = -32768
                self.assertEqual(self.fire(), 1)
                self.assertEqual(len(self.launched), 25)
                self.assertAlmostEqual(vitals.total, 20 if protected else 0)
                self.assertEqual(vitals.dead, not protected)

    def test_actual_matrix_amplifies_each_crossing_allied_pellet(self):
        self.arena(team=0)
        matrix = deploy_matrix(self.run, self.bap)
        self.bap.mover.position[0] = 20
        x, _, z = matrix.position
        self.bot.position = (x, 1, z - 1.5)
        trooper = next(d for d in pve_fixture.BaptistePveTests.catalog.values() if d.name == 'Trooper')
        enemy = npc.spawn(self.run.match, trooper, (x, 1, z + 1.5), team=1)
        self.bot.yaw, self.bot.pitch = aim((x, 2.55, z - 1.5), (x, 1.85, z + 1.5))
        self.assertEqual(self.fire(), 1)
        self.assertEqual(len(self.launched), 25)
        self.assertTrue(all(r.amplified for r in self.launched))
        self.assertEqual(self.fight.vitals[enemy.entity].total, 100)

    def test_control_aborts_active_charge_even_if_it_expires_before_launch(self):
        self.arena(distance=3)
        self.bap.client.bot_stream = lambda entity: None
        self.bot.yaw = -32768
        self.bot.firing = True
        advance_live = self.script.advance_live
        start = self.run.frame

        def advance(frame, position, *, edges=()):
            offset = frame - start
            if offset in (1, 2, 100, 101):
                edges = (*edges, (20, offset in (1, 100)))
            return advance_live(frame, position, edges=edges)

        target = next(item for item in self.fight._targets() if item.owner is self.bot)
        with patch.object(self.script, 'advance_live', side_effect=advance):
            for _ in range(7):
                self.run.step({})
            charge = self.weapon.states[self.weapon.graph.node(11).state]
            self.assertTrue(charge.active)
            self.fight.stun(target, .8)
            self.run.step({})
            self.assertFalse(charge.active)
            while self.run.frame < start + 99:
                self.run.step({})
            self.assertFalse(self.fight.is_stunned(self.bot))
            self.assertEqual(self.fight.vitals[self.bap.body].total, 200)
            while self.run.frame < start + 175:
                self.run.step({})
        self.assertIs(self.bot.body_script, self.script)
        self.assertEqual(self.fight.vitals[self.bap.body].total, 150)

    def test_controller_retirement_updates_input_and_cancels_weapon_gate(self):
        self.arena()
        self.script.retire_controller()
        self.script.component.run_now()
        self.assertTrue(self.controller.owner_dead)
        self.assertFalse(self.controller.instances)
        self.assertFalse(self.gate())
        self.assertEqual(self.fire(), 0)

    def test_death_and_manual_removal_retire_controller(self):
        for removal in (False, True):
            with self.subTest(removal=removal):
                self.arena()
                if removal:
                    npc.remove_spawned(self.run.match)
                else:
                    target = next(t for t in self.fight._targets() if t.owner is self.bot)
                    self.fight._damage(self.bap, target, 10000, False, None, self.run.frame)
                self.assertTrue(self.controller.owner_dead)
                self.assertFalse(self.controller.instances)
                self.assertFalse(self.gate())

    def test_two_npcs_do_not_share_controller_flags_or_identity(self):
        self.arena()
        other = npc.spawn(self.run.match, self.definition, (20, 1, 8), team=1)
        self.assertNotEqual(self.controller.entity, other.body_script.controller.entity)
        other_weapon = next(i for i in other.body_script.component.instances.values()
                            if i.graph.index == 0x13EB)
        self.script.retire_controller()
        self.script.component.run_now()
        self.assertFalse(self.gate())
        self.assertTrue(self.gate(other_weapon))

    def test_local_input_slots_are_hidden_from_wire_and_component_export_has_no_foreign_vm(self):
        self.arena()
        self.assertTrue(self.script.component.world.controller_inputs)
        keys = {key for key, _ in self.script.replicated_vars()}
        self.assertFalse(any(key >= 0x10000 for key in keys))
        self.assertTrue(export_component_vm(self.script.component))
        self.assertTrue(export_component_vm(self.controller))

    def test_missing_controller_child_or_identity_is_rejected(self):
        self.arena()
        args = (self.bot.body, self.bot.hero, 0xCFFF0100, self.run.frame,
                self.run.match.game_map.mode_guid, 250)
        with self.assertRaisesRegex(ValueError, 'separate live entity'):
            BotScript(*args)
        lookup = graph.graph
        with (patch.object(graph, 'graph', lambda index: None if index == 0x147B else lookup(index)),
              self.assertRaisesRegex(ValueError, '147B.*missing')):
            BotScript(*args, controller_entity=0xCFFF0101)

    def test_flashbang_weapon_stack_is_stored_without_observer_replication(self):
        from ow174.game.combat import FLASHBANG_EFFECT

        case = snapshot_fixture.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        definition = next(row for row in pve_fixture.BaptistePveTests.catalog.values()
                          if row.name == 'Trooper')
        bot = npc.spawn(player.match, definition, (0, 1, 8), team=1)
        script = bot.body_script
        fight = player.match.combat
        weapon = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == 0x102E)
        for offset in range(1, 30):
            edges = ((20, offset == 1),) if offset <= 2 else ()
            script.advance_live(script.frame + 1, bot.position, edges=edges)
            active = weapon.states.get(weapon.graph.node(44).state)
            if active is not None and active.active and active.top:
                break
        else:
            self.fail('Trooper did not enter its weapon stack')
        fight.tick = script.frame
        before = snapshot_fixture.snapshot_digest(case.capture(session))
        target = next(item for item in fight._targets() if item.owner is bot)
        fight._hit_effect(target, FLASHBANG_EFFECT)
        effect = script.flashbang_weapon_stack
        self.assertIsNotNone(effect)
        self.assertTrue(effect.state(11).top)
        self.assertNotIn(effect.id, script.graphs())
        self.assertNotEqual(snapshot_fixture.snapshot_digest(case.capture(session)), before)
        script.advance_live(fight.stuns[bot.entity], bot.position)
        self.assertIsNone(script.flashbang_weapon_stack)
        self.assertNotIn(effect.id, script.component.instances)

    def test_full_server_digest_covers_controller_clock_and_cached_inputs(self):
        case = snapshot_fixture.ServerSnapshotTests()
        session, _, players = case.scene()
        bot = npc.spawn(players[0].match, self.definition, (0, 1, 8), team=1)
        script = bot.body_script
        before = snapshot_fixture.snapshot_digest(case.capture(session))
        script.controller.frame += 1
        clock = snapshot_fixture.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, clock)
        script.controller.frame -= 1
        proxy = script.component.world.controller_inputs[9134]
        script.component.vars[proxy].base = False
        changed = snapshot_fixture.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, changed)

        fight = players[0].match.combat
        for offset in range(1, 101):
            script.advance_live(script.frame + 1, bot.position, edges=[(20, True)] if offset == 1 else ())
            if script.component.shots:
                break
        (state, number, when, activation), = script.component.shots
        script.component.shots.clear()
        fight.tick = script.frame
        fight._shoot(bot, None, state, number, when, activation=activation)
        self.assertEqual(len(fight.rockets), 25)
        in_flight = snapshot_fixture.snapshot_digest(case.capture(session))
        fight.rockets[0].distance_falloff = False
        self.assertNotEqual(in_flight, snapshot_fixture.snapshot_digest(case.capture(session)))
