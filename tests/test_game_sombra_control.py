"""Real Flashbang input against Sombra; reconstructed control is not native immunity proof."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_sombra_combat as sombra
import test_game_sombra_npc as actual_npcs
import test_mccree_flashbang as flashbang
from test_game_combat import in_world

from ow174.game import combat, mover, npc, npc_control, reinhardt_barrier, shot_guards, translocators
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import graph, nodes
from ow174.game.server_snapshot import _Graph
from ow174.game.world import HEALTH_POOL, OP_DESTROY

MCCREE = 0x02E0000000000042


class SombraControlTests(unittest.TestCase):
    def make(self, distance=3):
        f = sombra.SombraCombatTests()
        f.setUp()
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[MCCREE]))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 50)
        f.a.body_script.ult_seconds = 0
        return f

    def step(self, f, *, enemy=0, buttons=0, forward=0):
        flashbang.FlashbangTests.step(
            self, f.run, f.b, f.a, cast=enemy, target_buttons=buttons, forward=forward,
        )

    def stun(self, f, *, buttons=0):
        self.step(f, enemy=16, buttons=buttons)
        for _ in range(50):
            if f.run.match.combat.is_stunned(f.a):
                self.assertEqual(f.health(f.a), 175)
                return f.run.match.combat.stuns[f.a.body]
            self.step(f, buttons=buttons)
        self.fail("Actual enemy Flashbang did not stun Sombra")

    @staticmethod
    def emp(f):
        return next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0B31)

    @staticmethod
    def enemy_effects(f):
        return [i for i in f.b.body_script.component.instances.values() if i.graph.index in (0x0B1F, 0x0B32)]

    def test_actual_flashbang_arc_expires_in_range_of_sombra(self):
        f = self.make(distance=8)
        fight = f.run.match.combat
        self.step(f, enemy=16)
        for _ in range(30):
            if fight.rockets:
                break
            self.step(f)
        self.assertEqual(len(fight.rockets), 1)
        rocket = fight.rockets[0]
        self.assertEqual(rocket.gravity, 20)
        self.assertTrue(rocket.detonate_on_expiry)
        vertical_velocity = rocket.velocity[1]
        self.step(f)
        self.assertAlmostEqual(rocket.velocity[1], vertical_velocity - 20 * combat.TICK, places=5)
        for _ in range(25):
            if not fight.rockets:
                break
            self.step(f)
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(rocket.flown, 7 / 30, places=5)
        self.assertEqual(f.health(f.a), 175)
        self.assertTrue(fight.is_stunned(f.a))

    def test_wall_blocks_arcing_flashbang_damage_and_stun(self):
        f = self.make()
        f.wall()
        self.step(f, enemy=16)
        for _ in range(40):
            self.step(f)
        self.assertEqual(f.health(f.a), 200)
        self.assertFalse(f.run.match.combat.is_stunned(f.a))
        self.assertFalse(f.run.match.combat.rockets)

    def test_stun_blocks_pistol_melee_and_movement_then_recovers_at_deadline(self):
        f = self.make()
        end = self.stun(f)
        position = tuple(f.a.mover.position)
        before = f.health(f.b)
        while f.run.frame < end - 1:
            self.step(f, buttons=FIRE | sombra.MELEE, forward=127)
            self.assertEqual(tuple(f.a.mover.position), position)
            self.assertEqual(f.health(f.b), before)
            self.assertEqual(f.weapon().vars[53].value(), 60)
            self.assertFalse(f.a.body_script.component.held)
        self.step(f, buttons=FIRE, forward=127)
        self.assertFalse(f.run.match.combat.is_stunned(f.a))
        self.assertNotEqual(tuple(f.a.mover.position), position)
        self.assertLess(f.health(f.b), before)
        self.assertLess(f.weapon().vars[53].value(), 60)

    def test_stun_rejects_emp_without_spending_charge_then_allows_actual_cast(self):
        f = self.make()
        end = self.stun(f)
        f.a.body_script.set_ult_charge(1400)
        while f.run.frame < end - 1:
            self.step(f, buttons=0x20)
            self.assertEqual(f.a.body_script.component.entity_var(342).value(), 1400)
            self.assertFalse(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
            self.assertEqual(self.enemy_effects(f), [])
        self.step(f)  # exact expiry; release input before a new cast
        self.step(f, buttons=0x20)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        for _ in range(30):
            self.step(f)
        self.assertTrue(self.enemy_effects(f))
        self.assertIn(f.b.body, f.run.match.combat.hacks)

    def test_flashbang_interrupts_running_hack_without_application(self):
        f = self.make()
        self.step(f, buttons=2)
        self.assertTrue(f.weapon().state(53).flags & nodes.Ability.RUNNING)
        end = self.stun(f, buttons=2)
        self.assertFalse(f.weapon().state(53).flags & nodes.Ability.RUNNING)
        self.assertGreater(f.weapon().state(53).remaining(), 1.5)
        self.assertLessEqual(f.weapon().state(53).remaining(), 2)
        while f.run.frame < end:
            self.step(f, buttons=2)
        self.assertEqual(self.enemy_effects(f), [])

    def test_flashbang_ends_running_camo_and_preserves_authored_cooldown(self):
        f = self.make()
        self.step(f, buttons=8)
        for _ in range(60):
            self.step(f)
        ability = f.camo().state(13)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        end = self.stun(f)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        while f.run.frame < end:
            self.step(f, buttons=8)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(round(ability.remaining() / combat.TICK) + 3):
            self.step(f)
        self.step(f, buttons=8)
        for _ in range(60):
            self.step(f)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_actual_flashbang_interrupts_emp_windup_without_refund_or_late_pulse(self):
        f = self.make()
        f.a.body_script.set_ult_charge(1400)
        self.step(f, enemy=16, buttons=0x20)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        for _ in range(20):
            if f.run.match.combat.is_stunned(f.a):
                break
            self.step(f)
        self.assertTrue(f.run.match.combat.is_stunned(f.a))
        self.assertFalse(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
        for _ in range(80):
            self.step(f)
        self.assertEqual(self.enemy_effects(f), [])
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(f.health(f.b), 225)

    def test_in_flight_flashbang_interrupts_paid_emp_during_world_only_update(self):
        f = self.make()
        f.a.body_script.set_ult_charge(1400)
        self.step(f, enemy=16, buttons=0x20)
        cast = f.run.frame
        self.assertTrue(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        tick = cast + 30
        f.run.match.update(tick * combat.TICK, tick)
        self.assertTrue(f.run.match.combat.is_stunned(f.a))
        self.assertFalse(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(f.health(f.a), 175)
        self.assertEqual(f.health(f.b), 225)
        self.assertEqual(self.enemy_effects(f), [])
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        tick = cast + 80
        f.run.match.update(tick * combat.TICK, tick)
        self.assertEqual(self.enemy_effects(f), [])

    def deadeye_trackers(self, f):
        script = f.b.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x022F)
        self.step(f, enemy=32)
        for _ in range(25):
            self.step(f)
        states = tuple(root.node_state(root.graph.node(pos)) for pos in (26, 86))
        self.assertTrue(all(state.active for state in states))
        return states

    def test_actual_deadeye_trackers_observe_sombra_range_angle_team_and_cover(self):
        f = self.make()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        states = self.deadeye_trackers(f)

        def expect(broad, visible=None):
            if visible is None:
                visible = broad
            for state, selected in zip(states, (broad, visible), strict=True):
                expected = (f.a.body,) if selected else ()
                self.assertEqual(state.targets, expected)
                self.assertEqual(tuple(entity.id for entity in state.target_var.value()), expected)

        expect(True)
        f.wall()
        self.step(f)
        expect(True, False)
        f.geometry()
        self.step(f)
        expect(True)
        f.run.step({f.b: (0, 0, 0)})
        expect(False)
        self.step(f)
        expect(True)
        f.a.team = 1
        self.step(f)
        expect(False)
        f.a.team = 0

        f.floor = [(-10, 0, -250, -10, 0, 100, 10, 0, 100),
                   (-10, 0, -250, 10, 0, 100, 10, 0, -250)]
        f.geometry()
        for distance, visible in ((198, True), (202, False)):
            f.a.mover.position[:] = (0, 0, 3 - distance)
            f.a.mover.body = None
            self.step(f)
            expect(visible)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_deadeye_trackers_drop_camo_and_reacquire_near_or_after_cancel(self):
        f = self.make(distance=4)
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        states = self.deadeye_trackers(f)
        self.assertTrue(all(not state.targets for state in states))
        f.a.mover.position[:] = (0, 0, 2.5)
        f.a.mover.body = None
        self.step(f)
        self.assertTrue(all(state.targets == (f.a.body,) for state in states))
        f.a.mover.position[:] = (0, 0, 0)
        f.a.mover.body = None
        self.step(f)
        self.assertTrue(all(not state.targets for state in states))

        f.press(8)
        for _ in range(65):
            self.step(f)
            if not camo.flags & camo.RUNNING and all(state.targets for state in states):
                break
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertTrue(all(state.targets == (f.a.body,) for state in states))

    def mount_deadeye_graphs(self):
        path = os.environ.get("ORPHEUS_DEADEYE_GRAPHS")
        if not path:
            self.skipTest("requires the external Deadeye target effect graph")
        graph_patch = patch.object(graph, "GRAPHS_PATH", Path(path))
        graph_patch.start()
        self.addCleanup(actual_npcs.clear_graphs)
        self.addCleanup(graph_patch.stop)
        actual_npcs.clear_graphs()
        self.assertIsNotNone(graph.graph(0x01EE))

    def test_actual_deadeye_release_uses_target_effect_damage_and_late_visibility(self):
        self.mount_deadeye_graphs()
        for case in ("early", "lethal", "wall", "camo", "volley-range"):
            with self.subTest(case=case):
                f = self.make()
                if case == "volley-range":
                    f.floor = [(-10, 0, -130, -10, 0, 100, 10, 0, 100),
                               (-10, 0, -130, 10, 0, 100, 10, 0, -130)]
                    f.geometry()
                    f.a.mover.position[:] = (0, 0, -99)
                    f.a.mover.body = None
                states = self.deadeye_trackers(f)
                root = states[0].instance
                script = f.b.body_script
                effects = [i for i in script.component.instances.values() if i.graph.index == 0x01EE]
                effect, = effects
                self.assertEqual(effect.vars[536].value().id, f.a.body)
                self.assertEqual(effect.source_entity(), f.b.body)
                if case == "early":
                    encoder = _Graph({"match": f.run.match}, None)
                    encoder.value(f.a.body_script.component)
                    encoder.value(script.component)
                    self.assertIn("external_vm", str(encoder.finish()))
                for _ in range(20 if case == "early" else 100):
                    self.step(f)
                if case == "wall":
                    f.wall()
                    self.step(f)
                    self.assertEqual(states[0].targets, (f.a.body,))
                    self.assertEqual(states[1].targets, ())
                    self.assertIn(effect.id, script.component.instances)
                elif case == "camo":
                    self.step(f, buttons=8)
                    for _ in range(60):
                        self.step(f)
                    self.assertTrue(f.camo().state(13).flags & nodes.Ability.RUNNING)
                    self.assertEqual(states[0].targets, ())
                    self.assertEqual(states[1].targets, ())
                    self.assertNotIn(effect.id, script.component.instances)

                # Release with the crosshair off the capsule but inside the search cone.
                f.run.step({f.b: (32768 + 4096, 0, FIRE)})
                for _ in range(60):
                    self.step(f)
                hits = f.b.client.messages(combat.HIT)
                if case in ("wall", "camo"):
                    self.assertTrue(root.state(0).flags & nodes.Ability.RUNNING)
                    self.step(f, enemy=2)
                    f.press(0, 30)
                self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
                self.assertFalse(any(i.graph.index == 0x01EE for i in script.component.instances.values()))
                self.assertFalse(script.component.entity_var(2551).value())
                self.assertFalse(script.component.entity_var(5874).value())
                if case in ("early", "lethal"):
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
                    if case == "early":
                        self.assertAlmostEqual(200 - f.health(f.a), 52.80044174194336, places=4)
                    else:
                        self.assertEqual(f.health(f.a), 0)
                        self.assertEqual(f.run.match.combat.vitals[f.a.body].killer, f.b.body)
                else:
                    self.assertFalse(hits)
                    self.assertEqual(f.health(f.a), 200)

    def charge_short_deadeye(self, f):
        self.deadeye_trackers(f)
        for _ in range(20):
            self.step(f)

    def test_actual_deadeye_hit_during_paid_emp_windup_gates_pulse_on_survival(self):
        self.mount_deadeye_graphs()
        baseline = self.make()
        self.charge_short_deadeye(baseline)
        start = baseline.run.frame
        self.step(baseline, enemy=FIRE)
        for _ in range(35):
            if baseline.health(baseline.a) < 200:
                break
            self.step(baseline)
        self.assertLess(baseline.health(baseline.a), 200)
        contact = baseline.run.frame - start
        self.assertGreater(contact, 2)

        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.make()
                fight = f.run.match.combat
                self.charge_short_deadeye(f)
                body = f.a.body
                health = next(fight.vitals_of(t) for t in fight._targets() if t.entity == body)
                script = f.a.body_script
                script.set_ult_charge(1400)
                root = self.emp(f)
                cast = root.state(1)
                pulse = root.node_state(root.graph.node(40))
                self.step(f, enemy=FIRE)
                for _ in range(contact - 3):
                    self.step(f)
                self.step(f, buttons=32)
                self.assertTrue(cast.flags & cast.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(pulse.counter, 0)
                self.assertEqual(health.total, 200)
                if lethal:
                    health.now[HEALTH_POOL] = 1
                self.step(f)
                self.assertAlmostEqual(health.total, 0 if lethal else baseline.health(baseline.a), places=4)
                self.assertEqual(pulse.counter, 0)
                if lethal:
                    self.assertEqual(health.death_tick, f.run.frame)
                    self.assertEqual(health.killer, f.b.body)
                f.press(0, 85)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(f.b.body in fight.hacks, not lethal)
                if not lethal:
                    effect, = fight.hacks[f.b.body]
                    self.assertEqual(effect.application.source_entity(), body)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_lethal_deadeye_retires_settled_translocator_on_the_hit_frame(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        self.mount_deadeye_graphs()
        f = self.make()
        fight = f.run.match.combat
        f.press(16, 20)
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        self.assertGreater(beacon.position[2], f.b.mover.position[2])
        self.assertEqual(fight.vitals[beacon.entity].total, 5)
        self.charge_short_deadeye(f)
        health = next(fight.vitals_of(t) for t in fight._targets() if t.entity == f.a.body)
        health.now[HEALTH_POOL] = 1
        self.step(f, enemy=FIRE)
        for _ in range(35):
            if health.dead:
                break
            self.assertIn(beacon.entity, fight.translocators.entries)
            self.step(f)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, f.b.body)
        self.assertEqual(health.death_tick, f.run.frame)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertNotIn(beacon.entity, fight.vitals)
        self.assertFalse(beacon.body_script.component.instances)
        self.assertEqual(sum(u.entity == beacon.entity and u.op == OP_DESTROY
                             for u in f.a.client.entities), 1)
        f.press(0, 70)
        self.assertEqual(sum(u.entity == beacon.entity and u.op == OP_DESTROY
                             for u in f.a.client.entities), 1)

    def test_actual_translocator_recall_escapes_pending_deadeye_across_static_cover(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        self.mount_deadeye_graphs()
        for recall in (False, True):
            with self.subTest(recall=recall):
                f = self.make()
                fight = f.run.match.combat
                f.press(16, 20)
                f.press(0, 180)
                beacon, = fight.translocators.entries.values()
                self.assertTrue(beacon.settled)
                z = (beacon.position[2] + f.b.mover.position[2]) / 2
                f.geometry((-8, 0, z, 8, 0, z, 8, 5, z), (-8, 0, z, 8, 5, z, -8, 5, z))
                self.charge_short_deadeye(f)
                script = f.b.body_script
                self.assertEqual(tuple(e.id for e in script.component.entity_var(5874).value()), (f.a.body,))
                self.step(f, enemy=FIRE)
                self.assertEqual(f.health(f.a), 200)
                self.step(f, buttons=16 if recall else 0)
                self.assertEqual(f.health(f.a), 200)
                for _ in range(60):
                    self.step(f)
                hits = f.b.client.messages(combat.HIT)
                if recall:
                    self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], places=2)
                    self.assertNotIn(beacon.entity, fight.translocators.entries)
                    self.assertFalse(hits)
                    self.assertEqual(f.health(f.a), 200)
                else:
                    self.assertIn(beacon.entity, fight.translocators.entries)
                    self.assertEqual(len(hits), 1)
                    self.assertAlmostEqual(200 - f.health(f.a), 52.80044174194336, places=4)

    def test_actual_deadeye_damage_interrupts_sombra_hack_on_another_target(self):
        self.mount_deadeye_graphs()
        f = self.make()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        hero = f.b.hero
        target = match.add_player(3, "Hack target", hero.guid, 1, False)
        in_world(match, target)
        f.run.players.append(target)
        self.assertTrue(match.switch_hero(target, hero))
        target.team = 1
        target.mover.position[:] = (5, 0, 0)
        target.mover.body = None
        f.press(0, 30)
        self.charge_short_deadeye(f)
        angles = sombra.aim(fight._eye(f.a), (5, 1, 0))

        def step(enemy=0):
            f.run.step({f.a: (*angles, 2), f.b: (-32768, 0, enemy)})

        for _ in range(12):
            step()
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & hack.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value().id, target.body)
        step(enemy=FIRE)
        for _ in range(30):
            if f.health(f.a) < 200:
                break
            step()
        self.assertGreater(f.health(f.a), 0)
        self.assertLess(f.health(f.a), 200)
        impact_frame = f.run.frame
        for _ in range(35):
            if not hack.flags & hack.RUNNING:
                break
            step()
        self.assertFalse(hack.flags & hack.RUNNING)
        elapsed = (f.run.frame - impact_frame) * combat.TICK
        self.assertAlmostEqual(hack.remaining() + elapsed, 2, delta=0.04)
        self.assertNotIn(target.body, fight.hacks)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertEqual(len(f.b.client.messages(combat.HIT)), 1)

    def test_actual_deadeye_damage_cancels_camo_at_visible_proximity(self):
        self.mount_deadeye_graphs()
        f = self.make(distance=1.5)
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        tracker = f.camo().node_state(f.camo().graph.node(128))
        self.assertTrue(camo.flags & camo.RUNNING)
        self.assertEqual(tracker.targets, (f.b.body,))
        self.charge_short_deadeye(f)
        self.step(f, enemy=FIRE)
        for _ in range(35):
            self.step(f)
            if f.health(f.a) < 200:
                break
            self.assertTrue(tracker.active)
        self.assertGreater(f.health(f.a), 0)
        self.assertLess(f.health(f.a), 200)
        f.press(0, 65)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertFalse(tracker.active)
        self.assertFalse(tracker.targets)
        self.assertGreater(camo.remaining(), 0)
        self.assertEqual(len(f.b.client.messages(combat.HIT)), 1)

    def test_actual_deadeye_orders_sombra_and_all_five_npcs_and_drops_late_hidden_targets(self):
        self.mount_deadeye_graphs()
        runtime = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime) / "data" / "pve_npcs_174.json")
        for case in ("clear", "hidden", "intercept"):
            with self.subTest(case=case):
                self.check_deadeye_multi_target_release(catalog, case)

    def check_deadeye_multi_target_release(self, catalog, case):
        f = self.make()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 1, 0
        for player in f.run.players:
            player.client.bot_stream = lambda body: None
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        positions = ((-5, 0, -3), (1.4, 0, -3), (-1.2, 0, -3), (4.5, 0, -3), (-3, 0, -3))
        victims = {}
        for key, position in zip(("1", "2", "3", "4", "5"), positions, strict=True):
            definition = catalog[key]
            self.assertIn(definition.body, mover.loaded_mover_table())
            victims[key] = npc.spawn(match, definition, position, team=1)
        f.press(0, 5)
        actors = {f.a.body: f.a, **{victim.entity: victim for victim in victims.values()}}
        order = (f.a.body, *(victims[key].entity for key in ("3", "2", "5", "4", "1")))
        self.assertEqual(f.health(f.a), 200)
        before = {entity: fight.vitals[entity].total for entity in actors}
        broad, visible = self.deadeye_trackers(f)
        root = broad.instance
        effects = root.node_state(root.graph.node(11))
        self.assertEqual(broad.targets, order)
        self.assertEqual(visible.targets, order)
        self.assertEqual(set(effects.children), set(order))
        for _ in range(145):
            self.step(f)

        expected = order
        if case != "clear":
            self.step(f, buttons=8)
            for _ in range(60):
                self.step(f)
            z = 0.65
            f.geometry((-.82, 0, z, -.2, 0, z, -.2, 4, z),
                       (-.82, 0, z, -.2, 4, z, -.82, 4, z))
            if case == "intercept":
                f.a.mover.position[:] = (0.7, 0, 0)
                f.a.mover.body = None
            f.run.step({f.b: (-32768, 0, 0)})
            expected = tuple(entity for entity in order if entity not in (f.a.body, victims["3"].entity))
            self.assertNotIn(f.a.body, effects.children)
            self.assertIn(victims["3"].entity, effects.children)
            self.assertEqual(visible.targets, expected)

        fire = fight._shoot
        shots = []

        def report(shooter, command, state, *args, **kwargs):
            if state.instance.graph.index == 0x01EE:
                shots.append((state.instance.vars[536].value().id, state.instance.vars[476].value()))
            return fire(shooter, command, state, *args, **kwargs)

        with patch.object(fight, "_shoot", side_effect=report):
            f.run.step({f.b: (-32768, 0, FIRE)})
            for _ in range(100):
                f.run.step({f.b: (-32768, 0, 0)})
        self.assertEqual(tuple(entity for entity, _ in shots), expected)
        actual_targets = (f.a.body, *expected[1:]) if case == "intercept" else expected
        hits = f.b.client.messages(combat.HIT)
        self.assertEqual(tuple(hit["+0x78"]["+0x0"]["+0x0"] for hit in hits), actual_targets)
        damage = {entity: amount for entity, (_, amount) in zip(actual_targets, shots, strict=True)}
        for entity, actor in actors.items():
            vitals = fight.vitals[entity]
            expected_loss = min(before[entity], damage.get(entity, 0))
            self.assertAlmostEqual(vitals.total, before[entity] - expected_loss, places=4)
            if vitals.dead:
                self.assertEqual(vitals.killer, f.b.body)
                if actor is not f.a:
                    self.assertIn(actor, match.bots)
        self.assertGreater(fight.vitals[victims["5"].entity].total, 0)
        self.assertFalse(effects.children)
        self.assertFalse(broad.targets)
        self.assertFalse(visible.targets)

    def test_actual_deadeye_volley_resolves_zarya_reinhardt_genji_and_sigma_defenses(self):
        self.mount_deadeye_graphs()
        scenarios = (
            (reinhardt_barrier.ZARYA, 16, 0, 44), (reinhardt_barrier.ZARYA, 16, 0, 135),
            (reinhardt_barrier.HERO, 2, 0, 44), (reinhardt_barrier.HERO, 2, -32768, 44),
            (shot_guards.GENJI, 16, 0, 44), (shot_guards.GENJI, 16, -32768, 44),
            (shot_guards.SIGMA, 8, 0, 44), (shot_guards.SIGMA, 8, -32768, 44),
        )
        for hero, button, facing, charge_frames in scenarios:
            with self.subTest(hero=hero, facing=facing, charge_frames=charge_frames):
                self.check_deadeye_volley_defense(hero, button, facing, charge_frames)

    def add_rifle_defender(self, f, hero, off_axis_z):
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 1, 0
        defender = match.add_player(3, "Defender", hero, 1, False)
        in_world(match, defender)
        f.run.players.append(defender)
        self.assertTrue(match.switch_hero(defender, heroes()[hero]))
        defender.team = 1
        projected = hero == reinhardt_barrier.ZARYA
        defender.mover.position[:] = (6 if projected else 0, 0, 0)
        defender.mover.body = None
        if not projected:
            f.a.mover.position[:] = (5, 0, off_axis_z)
            f.a.mover.body = None
        return defender, f.a if projected else defender

    def check_deadeye_volley_defense(self, hero, button, facing, charge_frames):
        f = self.make()
        fight = f.run.match.combat
        defender, target = self.add_rifle_defender(f, hero, 3)
        projected = target is f.a
        for _ in range(50):
            f.run.step({f.b: (-32768, 0, 0)})
        before = f.health(target)
        script = f.b.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x022F)
        f.run.step({f.b: (-32768, 0, 32)})
        for _ in range(charge_frames):
            f.run.step({f.b: (-32768, 0, 0)})
        for pos in (26, 86):
            self.assertEqual(root.node_state(root.graph.node(pos)).targets, (target.body,))

        angles = sombra.aim(fight._eye(defender), (0, 1.3, 0)) if projected else (facing, 0)
        f.run.step({f.b: (-32768, 0, 0), defender: (*angles, button)})
        barrier = guard = None
        if hero == reinhardt_barrier.HERO:
            for _ in range(30):
                if fight.barriers:
                    break
                f.run.step({f.b: (-32768, 0, 0), defender: (*angles, 2)})
        if hero in (reinhardt_barrier.HERO, reinhardt_barrier.ZARYA):
            barrier, = fight.barriers.values()
            if projected:
                self.assertIs(barrier.protected, f.a)
            elif not barrier.mesh:
                self.skipTest("requires the extracted Reinhardt shield mesh")
        else:
            guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
            self.assertIsNotNone(guard)

        held = 2 if hero == reinhardt_barrier.HERO else 0
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            f.run.step({f.b: (-32768, 0, FIRE), defender: (*angles, held)})
            for _ in range(35):
                if hitscan.call_count:
                    break
                f.run.step({f.b: (-32768, 0, 0), defender: (*angles, held)})
        self.assertGreaterEqual(hitscan.call_count, 1)
        self.assertIs(hitscan.call_args_list[0].args[0], f.b)
        amount = hitscan.call_args_list[0].args[3].amount
        self.assertGreater(amount, 0)
        blocked = facing == 0
        if blocked:
            self.assertEqual(f.health(target), before)
            if projected:
                absorbed = min(amount, 200)
                self.assertAlmostEqual(barrier.vitals.total, 200 - absorbed, places=4)
                self.assertAlmostEqual(defender.body_script.component.entity_var(87).value(),
                                       absorbed * 0.2, places=4)
                if charge_frames == 135:
                    self.assertGreater(amount, 200)
                    self.assertNotIn(barrier.entity, fight.barriers)
            elif hero == reinhardt_barrier.HERO:
                self.assertAlmostEqual(barrier.vitals.total, 1600 - amount, places=4)
            elif hero == shot_guards.GENJI:
                self.assertEqual(hitscan.call_count, 2)
                self.assertIs(hitscan.call_args_list[1].args[0], defender)
                self.assertAlmostEqual(f.health(f.b), 225 - amount, places=4)
                self.assertEqual(len(defender.client.messages(combat.HIT)), 1)
            else:
                self.assertAlmostEqual(guard.instance.vars[21312].value(), amount, places=4)
            self.assertFalse(f.b.client.messages(combat.HIT))
        else:
            armour = min(amount * 0.5, 5) if hero == reinhardt_barrier.HERO else 0
            self.assertAlmostEqual(f.health(target), before - amount + armour, places=4)
            self.assertEqual(f.health(f.b), 225)
            self.assertEqual(len(f.b.client.messages(combat.HIT)), 1)
            if barrier is not None:
                self.assertEqual(barrier.vitals.total, 1600)
            elif hero == shot_guards.SIGMA:
                self.assertEqual(guard.instance.vars[21312].value(), 0)
        self.assertEqual(f.health(f.a), 200)

    def start_visor(self, f, distance=8):
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 10)
        script = f.b.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        self.step(f, enemy=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(83):
            self.step(f)
        self.assertTrue(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertTrue(root.state(7).active)
        return root

    def test_actual_visor_hit_during_paid_emp_windup_gates_pulse_on_survival(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = sombra.SombraCombatTests()
                f.setUp()
                root = self.start_visor(f)
                fight = f.run.match.combat
                body = f.a.body
                health = fight.vitals[body]
                script = f.a.body_script
                script.set_ult_charge(1400)
                emp = self.emp(f)
                pulse = emp.node_state(emp.graph.node(40))
                yaw = 32768 + 3600
                f.run.step({f.a: (0, 0, 32), f.b: (yaw, 0, 0)})
                self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(pulse.counter, 0)
                self.assertEqual(root.state(7).targets, (body,))
                if lethal:
                    health.now[HEALTH_POOL] = 20
                f.run.step({f.b: (yaw, 0, FIRE)})
                hit, = f.b.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], body)
                self.assertEqual(health.total, 0 if lethal else 180)
                self.assertEqual(pulse.counter, 0)
                if lethal:
                    self.assertEqual(health.killer, f.b.body)
                    self.assertEqual(health.death_tick, f.run.frame)
                f.press(0, 85)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(f.b.body in fight.hacks, not lethal)
                if not lethal:
                    effect, = fight.hacks[f.b.body]
                    self.assertEqual(effect.application.source_entity(), body)
                    self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
                self.assertFalse(root.state(7).targets)

    def test_actual_visor_hit_interrupts_sombra_hack_on_a_separate_target(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        hero = f.b.hero
        target = match.add_player(3, "Hack target", hero.guid, 1, False)
        in_world(match, target)
        f.run.players.append(target)
        self.assertTrue(match.switch_hero(target, hero))
        target.team = 1
        target.mover.position[:] = (5, 0, 0)
        target.mover.body = None
        self.start_visor(f)
        angles = sombra.aim(fight._eye(f.a), (5, 1, 0))
        yaw = 32768 + 3600

        def step(enemy=0):
            f.run.step({f.a: (*angles, 2), f.b: (yaw, 0, enemy)})

        for _ in range(12):
            step()
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & hack.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value().id, target.body)
        step(enemy=FIRE)
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
        self.assertEqual(f.health(f.a), 180)
        impact_frame = f.run.frame
        for _ in range(35):
            if not hack.flags & hack.RUNNING:
                break
            step()
        self.assertFalse(hack.flags & hack.RUNNING)
        elapsed = (f.run.frame - impact_frame) * combat.TICK
        self.assertAlmostEqual(hack.remaining() + elapsed, 2, delta=0.04)
        self.assertFalse(fight.hacks)
        self.assertEqual(f.health(target), 200)

    def test_actual_visor_hit_cancels_camo_when_still_visible_at_close_range(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        root = self.start_visor(f, 1.8)
        fight = f.run.match.combat
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        tracker = f.camo().node_state(f.camo().graph.node(128))
        self.assertTrue(camo.flags & camo.RUNNING)
        self.assertEqual(tracker.targets, (f.b.body,))
        target = next(t for t in fight._targets() if t.entity == f.a.body)
        a, b = target.segment()
        center = tuple((lower + upper) / 2 for lower, upper in zip(a, b, strict=True))
        yaw, pitch = sombra.aim(fight._eye(f.b), center)
        yaw += 3600
        f.run.step({f.b: (yaw, pitch, 0)})
        self.assertEqual(root.state(7).targets, (f.a.body,))
        f.run.step({f.b: (yaw, pitch, FIRE)})
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
        self.assertEqual(f.health(f.a), 180)
        f.press(0, 65)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertFalse(tracker.active)
        self.assertFalse(tracker.targets)
        self.assertGreater(camo.remaining(), 0)

    def test_actual_lethal_visor_hit_retires_settled_translocator_on_the_same_frame(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = sombra.SombraCombatTests()
        f.setUp()
        fight = f.run.match.combat
        f.press(16, 20)
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        root = self.start_visor(f)
        self.assertGreater(beacon.position[2], f.b.mover.position[2])
        self.assertEqual(root.state(7).targets, (f.a.body,))
        self.assertEqual(fight.vitals[beacon.entity].total, 5)
        health = fight.vitals[f.a.body]
        health.now[HEALTH_POOL] = 20
        f.run.step({f.b: (32768 + 3600, 0, FIRE)})
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
        self.assertEqual(health.killer, f.b.body)
        self.assertEqual(health.death_tick, f.run.frame)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertNotIn(beacon.entity, fight.vitals)
        self.assertFalse(beacon.body_script.component.instances)
        self.assertEqual(sum(u.entity == beacon.entity and u.op == OP_DESTROY
                             for u in f.a.client.entities), 1)
        f.press(0, 70)
        self.assertFalse(root.state(7).targets)
        self.assertEqual(sum(u.entity == beacon.entity and u.op == OP_DESTROY
                             for u in f.a.client.entities), 1)

    def test_actual_sombra_pistol_kill_ends_visor_before_enemy_fire(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        root = self.start_visor(f)
        fight = f.run.match.combat
        self.assertEqual(f.health(f.b), 200)
        health = fight.vitals[f.b.body]
        health.now[HEALTH_POOL] = 8
        yaw, pitch = sombra.aim(fight._eye(f.a), (0, 1.2, 8))
        script = f.b.body_script
        rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x0254)
        self.assertEqual(root.state(7).targets, (f.a.body,))
        self.assertEqual(rifle.vars[53].value(), 30)
        f.run.step({f.a: (yaw, pitch, FIRE), f.b: (32768 + 3600, 0, FIRE)})
        self.assertEqual(health.total, 0)
        self.assertEqual(health.killer, f.a.body)
        self.assertEqual(health.death_tick, f.run.frame)
        self.assertEqual(f.weapon().vars[53].value(), 59)
        self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(root.state(7).targets)
        self.assertFalse(script.component.entity_var(2337).value())
        self.assertEqual(rifle.vars[53].value(), 30)
        self.assertFalse(f.b.client.messages(combat.HIT))
        self.assertEqual(f.health(f.a), 200)

    def test_actual_translocator_recall_drops_visor_lock_despite_a_delayed_shot(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        for recall in (False, True):
            with self.subTest(recall=recall):
                f = sombra.SombraCombatTests()
                f.setUp()
                match = f.run.match
                fight = match.combat
                f.press(16, 20)
                f.press(0, 180)
                beacon, = fight.translocators.entries.values()
                self.assertTrue(beacon.settled)
                root = self.start_visor(f)
                z = (beacon.position[2] + f.b.mover.position[2]) / 2
                self.assertGreater(z, f.b.mover.position[2])
                f.geometry((-8, 0, z, 8, 0, z, 8, 5, z), (-8, 0, z, 8, 5, z, -8, 5, z))
                yaw = 32768 + 3600
                f.run.step({f.b: (yaw, 0, 0)})
                self.assertEqual(root.state(7).targets, (f.a.body,))
                f.run.frame += 1
                match.update(f.run.frame * combat.TICK, f.run.frame)
                f.a.take_commands([Command(f.run.frame, buttons=16 if recall else 0)])
                self.assertEqual(f.a.mover.position[2] > z, recall)
                target = next(t for t in fight._targets() if t.entity == f.a.body)
                viewed = fight.seen(target, f.run.frame - 10)
                self.assertLess(viewed.feet[2], f.b.mover.position[2])
                f.b.take_commands([Command(f.run.frame, yaw=yaw, buttons=FIRE, extra=160)])
                script = f.b.body_script
                rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x0254)
                self.assertEqual(rifle.vars[53].value(), 29)
                self.assertEqual(root.state(7).targets, () if recall else (f.a.body,))
                hits = f.b.client.messages(combat.HIT)
                if recall:
                    self.assertFalse(hits)
                    self.assertEqual(f.health(f.a), 200)
                else:
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
                    self.assertEqual(f.health(f.a), 180)
                f.press(0, 45)
                self.assertEqual(beacon.entity in fight.translocators.entries, not recall)

    def test_actual_visor_rifle_resolves_projected_barrier_and_directional_guards(self):
        for hero, button in ((reinhardt_barrier.ZARYA, 16), (reinhardt_barrier.HERO, 2),
                             (shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            facings = (0,) if hero == reinhardt_barrier.ZARYA else (0, -32768)
            for facing in facings:
                with self.subTest(hero=hero, facing=facing):
                    self.check_visor_defense(hero, button, facing)

    def check_visor_defense(self, hero, button, facing):
        f = sombra.SombraCombatTests()
        f.setUp()
        fight = f.run.match.combat
        defender, target = self.add_rifle_defender(f, hero, 8)
        root = self.start_visor(f)
        projected = target is f.a
        angles = sombra.aim(fight._eye(defender), (0, 1.3, 0)) if projected else (facing, 0)
        yaw = 32768 + 3600

        def step(enemy=0, guard=0):
            f.run.step({f.b: (yaw, 0, enemy), defender: (*angles, guard)})

        step()
        self.assertEqual(root.state(7).targets, (target.body,))
        before = f.health(target)
        step(guard=button)
        barrier = guard = None
        if hero == reinhardt_barrier.HERO:
            for _ in range(30):
                if fight.barriers:
                    break
                step(guard=button)
        if hero in (reinhardt_barrier.HERO, reinhardt_barrier.ZARYA):
            barrier, = fight.barriers.values()
            if projected:
                self.assertIs(barrier.protected, f.a)
            elif not barrier.mesh:
                self.skipTest("requires the extracted Reinhardt shield mesh")
        else:
            guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
            self.assertIsNotNone(guard)
        held = 2 if hero == reinhardt_barrier.HERO else 0
        step(enemy=FIRE, guard=held)
        step(guard=held)
        self.assertEqual(root.state(7).targets, (target.body,))
        rifle = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x0254)
        self.assertEqual(rifle.vars[53].value(), 29)
        hits = f.b.client.messages(combat.HIT)
        if facing == 0:
            self.assertEqual(f.health(target), before)
            self.assertFalse(hits)
            if projected:
                self.assertEqual(barrier.vitals.total, 180)
                self.assertEqual(defender.body_script.component.entity_var(87).value(), 4)
            elif hero == reinhardt_barrier.HERO:
                self.assertEqual(barrier.vitals.total, 1580)
            elif hero == shot_guards.GENJI:
                hit, = defender.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.b.body)
                self.assertEqual(f.health(f.b), 180)
            else:
                self.assertEqual(guard.instance.vars[21312].value(), 20)
        else:
            hit, = hits
            self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], target.body)
            self.assertEqual(f.health(target), before - (15 if hero == reinhardt_barrier.HERO else 20))
            self.assertEqual(f.health(f.b), 200)
            if barrier is not None:
                self.assertEqual(barrier.vitals.total, 1600)
            elif hero == shot_guards.SIGMA:
                self.assertEqual(guard.instance.vars[21312].value(), 0)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_visor_rifle_charges_only_the_first_of_grasp_and_projected_barrier(self):
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                f = sombra.SombraCombatTests()
                f.setUp()
                match = f.run.match
                fight = match.combat
                zarya, _ = self.add_rifle_defender(f, reinhardt_barrier.ZARYA, 8)
                hero = heroes()[shot_guards.SIGMA]
                sigma = match.add_player(4, "Sigma", hero.guid, 1, False)
                in_world(match, sigma)
                f.run.players.append(sigma)
                self.assertTrue(match.switch_hero(sigma, hero))
                sigma.team = 1
                sigma.mover.position[:] = (0.8, 0, 3 if grasp_first else -3)
                sigma.mover.body = None
                root = self.start_visor(f)
                angles = sombra.aim(fight._eye(zarya), (0, 1.3, 0))
                yaw = 32768 + 1400
                f.run.step({f.b: (yaw, 0, 0), zarya: (*angles, 16), sigma: (0, 0, 8)})
                self.assertEqual(root.state(7).targets[0], f.a.body)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                grasp = shot_guards.active_guard(sigma, fight.tick * combat.TICK * 1000)
                self.assertIsNotNone(grasp)
                energy = zarya.body_script.component.entity_var(87)
                energy_before = energy.value()
                f.run.step({f.b: (yaw, 0, FIRE)})
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(f.health(sigma), 400)
                self.assertFalse(f.b.client.messages(combat.HIT))
                self.assertEqual(bubble.vitals.total, 200 if grasp_first else 180)
                self.assertEqual(grasp.instance.vars[21312].value(), 20 if grasp_first else 0)
                self.assertEqual(energy.value(), energy_before if grasp_first else 4)

    def test_actual_visor_uses_same_delayed_target_pose_for_aim_and_hit(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        root = self.start_visor(f)
        match = f.run.match
        fight = match.combat
        yaw = 32768 + 1200
        for _ in range(24):
            f.run.frame += 1
            match.update(f.run.frame * combat.TICK, f.run.frame)
            f.a.take_commands([Command(f.run.frame, right=-127)])
            f.b.take_commands([Command(f.run.frame, yaw=yaw)])
        self.assertEqual(root.state(7).targets, (f.a.body,))
        f.run.frame += 1
        match.update(f.run.frame * combat.TICK, f.run.frame)
        target = next(t for t in fight._targets() if t.entity == f.a.body)
        viewed = fight.seen(target, f.run.frame - 20)
        self.assertGreater(target.feet[0] - viewed.feet[0], 2 * target.radius)
        f.b.take_commands([Command(f.run.frame, yaw=yaw, buttons=FIRE, extra=320)])
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
        self.assertEqual(f.health(f.a), 180)

    def test_actual_visor_uses_static_range_cover_team_and_camo_for_rifle_aim(self):
        for case in ("clear", "wall", "team", "camo", "near_camo", "range_in", "range_out", "angle"):
            with self.subTest(case=case):
                f = sombra.SombraCombatTests()
                f.setUp()
                root = self.start_visor(f, {"near_camo": 1.8, "range_in": 44, "range_out": 46}.get(case, 8))
                match = f.run.match
                if case == "wall":
                    f.wall()
                if case == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = f.b.team = 0
                if case in ("camo", "near_camo"):
                    f.press(8)
                    f.press(0, 60)
                    self.assertTrue(f.camo().state(13).flags & nodes.Ability.RUNNING)
                fight = match.combat
                target = next(t for t in fight._targets() if t.entity == f.a.body)
                a, b = target.segment()
                center = tuple((lower + upper) / 2 for lower, upper in zip(a, b, strict=True))
                yaw, pitch = sombra.aim(fight._eye(f.b), center)
                yaw += 5200 if case == "angle" else 3600
                for _ in range(3):
                    f.run.step({f.b: (yaw, pitch, 0)})
                locked = case in ("clear", "near_camo", "range_in")
                self.assertEqual(root.state(7).targets, (f.a.body,) if locked else ())
                component = f.b.body_script.component
                slot = component.entity_var(2337)
                self.assertEqual(tuple(e.id for e in slot.value()), root.state(7).targets)
                rifle = next(i for i in component.instances.values() if i.graph.index == 0x0254)
                f.run.step({f.b: (yaw, pitch, FIRE)})
                f.run.step({f.b: (yaw, pitch, 0)})
                self.assertEqual(rifle.vars[53].value(), 29)
                hits = f.b.client.messages(combat.HIT)
                if locked:
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.a.body)
                    self.assertLess(f.health(f.a), 200)
                    if case != "range_in":
                        self.assertEqual(f.health(f.a), 180)
                else:
                    self.assertFalse(hits)
                    self.assertEqual(f.health(f.a), 200)

    def test_actual_visor_tracks_sombra_and_all_five_npcs_then_aims_at_first(self):
        runtime = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime) / "data" / "pve_npcs_174.json")
        f = sombra.SombraCombatTests()
        f.setUp()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 1, 0
        f.a.mover.position[:] = (2, 0, 0)
        f.a.mover.body = None
        for player in f.run.players:
            player.client.bot_stream = lambda body: None
        actors = []
        for key, x in zip(("1", "2", "3", "4", "5"), (-3.5, -2.5, 0, 2.5, 3.5), strict=True):
            definition = catalog[key]
            self.assertIn(definition.body, mover.loaded_mover_table())
            actors.append(npc.spawn(match, definition, (x, 0, -4), team=1))
        root = self.start_visor(f)
        fight = match.combat
        sniper = actors[2].entity
        target = next(t for t in fight._targets() if t.entity == sniper)
        a, b = target.segment()
        center = tuple((lower + upper) / 2 for lower, upper in zip(a, b, strict=True))
        yaw, pitch = sombra.aim(fight._eye(f.b), center)
        yaw += 650
        f.run.step({f.b: (yaw, pitch, 0)})
        tracker = root.state(7)
        entities = {actor.entity for actor in actors}
        self.assertEqual(set(tracker.targets), entities | {f.a.body})
        self.assertEqual(tracker.targets[0], sniper)
        f.press(8)
        f.press(0, 60)
        f.run.step({f.b: (yaw, pitch, 0)})
        self.assertTrue(f.camo().state(13).flags & nodes.Ability.RUNNING)
        self.assertEqual(set(tracker.targets), entities)
        self.assertEqual(tracker.targets[0], sniper)
        before = {entity: fight.vitals[entity].total for entity in entities}
        f.run.step({f.b: (yaw, pitch, FIRE)})
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], sniper)
        for entity in entities:
            self.assertEqual(fight.vitals[entity].total, before[entity] - (20 if entity == sniper else 0))
        self.assertEqual(f.health(f.a), 200)
        for _ in range(390):
            f.run.step({f.b: (yaw, pitch, 0)})
        self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(tracker.targets)
        self.assertFalse(f.b.body_script.component.entity_var(2337).value())
        f.run.step({f.b: (yaw, pitch, FIRE)})
        self.assertEqual(len(f.b.client.messages(combat.HIT)), 1)

    def test_actual_hack_and_emp_stop_visor_aim_and_deny_recast_until_expiry(self):
        for attack in (2, 32):
            with self.subTest(attack=attack):
                f = sombra.SombraCombatTests()
                f.setUp()
                root = self.start_visor(f)
                self.assertEqual(root.state(7).targets, (f.a.body,))
                script = f.b.body_script
                charge = script.component.entity_var(342)
                if attack == 32:
                    f.a.body_script.set_ult_charge(1400)
                for frame in range(82 if attack == 2 else 42):
                    self.step(f, buttons=attack if attack == 2 or frame == 0 else 0)
                fight = f.run.match.combat
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
                self.assertFalse(root.state(7).active)
                self.assertFalse(root.state(7).targets)
                self.assertFalse(script.component.entity_var(2337).value())
                self.assertEqual(charge.value(), 0)
                cost = script.component.entity_var(338).value()
                script.set_ult_charge(cost)
                self.step(f, enemy=32)
                self.assertEqual(charge.value(), cost)
                self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
                yaw = 32768 + 3600
                f.run.step({f.b: (yaw, 0, FIRE)})
                f.run.step({f.b: (yaw, 0, 0)})
                rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x0254)
                self.assertEqual(rifle.vars[53].value(), 29)
                self.assertFalse(f.b.client.messages(combat.HIT))
                self.assertEqual(f.health(f.a), 200)
                f.press(0, effect.until_tick - f.run.frame + 2)
                self.assertNotIn(f.b.body, fight.hacks)
                self.step(f, enemy=32)
                self.assertTrue(root.state(0).flags & nodes.Ability.RUNNING)
                self.assertEqual(charge.value(), 0)
                for _ in range(83):
                    f.run.step({f.b: (yaw, 0, 0)})
                self.assertEqual(root.state(7).targets, (f.a.body,))
                f.run.step({f.b: (yaw, 0, FIRE)})
                self.assertEqual(f.health(f.a), 180)

    def test_hack_and_emp_cancel_active_deadeye_and_preserve_paid_charge(self):
        for button in (2, 32):
            for wall in (False, True):
                with self.subTest(button=button, wall=wall):
                    f = self.make()
                    fight = f.run.match.combat
                    enemy = f.b.body_script
                    enemy.ult_seconds = 0
                    cost = enemy.component.entity_var(338).value()
                    charge = enemy.component.entity_var(342)
                    deadeye = next(i for i in enemy.component.instances.values() if i.graph.index == 0x022F)
                    cast = deadeye.node_state(deadeye.graph.node(1))
                    weapon = next(i for i in enemy.component.instances.values() if i.graph.index == 0x01D2)
                    enemy.set_ult_charge(cost)
                    self.step(f, enemy=32)
                    f.press(0, 12)
                    self.assertTrue(cast.flags & cast.RUNNING)
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(weapon.vars[53].value(), 6)
                    if wall:
                        f.wall()
                    if button == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for frame in range(82 if button == 2 else 42):
                        attack = button if button == 2 or frame == 0 else 0
                        self.step(f, buttons=attack)
                    self.assertEqual(bool(cast.flags & cast.RUNNING), wall)
                    self.assertEqual(f.b.body in fight.hacks, not wall)
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(weapon.vars[53].value(), 6)
                    self.assertEqual(f.health(f.b), 225)
                    self.assertEqual(f.health(f.a), 200)

                    if wall:
                        self.step(f, enemy=2)
                        f.press(0, 25)
                        self.assertFalse(cast.flags & cast.RUNNING)
                    else:
                        effect, = fight.hacks[f.b.body]
                        self.assertEqual(effect.application.source_entity(), f.a.body)
                        f.press(0, 45)
                        self.assertIs(fight.hacks[f.b.body][0], effect)
                        weapon = next(i for i in enemy.component.instances.values()
                                      if i.graph.index == 0x01D2)
                        self.assertEqual(weapon.vars[53].value(), 6)
                        enemy.set_ult_charge(cost)
                        self.step(f, enemy=32)
                        self.assertFalse(cast.flags & cast.RUNNING)
                        self.assertEqual(charge.value(), cost)
                        f.press(0, 3)
                        self.step(f, enemy=FIRE)
                        self.assertEqual(weapon.vars[53].value(), 5)
                        self.assertEqual(f.health(f.a), 130)
                        f.press(0, effect.until_tick - f.run.frame + 2)
                        self.assertNotIn(f.b.body, fight.hacks)
                        self.assertFalse(cast.flags & cast.RUNNING)
                        self.step(f, enemy=32)
                        self.assertTrue(cast.flags & cast.RUNNING)
                        self.assertEqual(charge.value(), 0)
                        f.press(0, 30)
                        self.step(f, enemy=2)
                        f.press(0, 30)
                        self.assertFalse(cast.flags & cast.RUNNING)
                        weapon = next(i for i in enemy.component.instances.values()
                                      if i.graph.index == 0x01D2)
                        # The fresh Deadeye cycle restores the live revolver graph at six rounds.
                        self.assertEqual(weapon.vars[53].value(), 6)

    def test_hack_and_emp_prevent_actual_enemy_flashbang_until_silence_expires(self):
        for cast in (2, 0x20):
            with self.subTest(sombra_input=cast):
                f = self.make()
                f.a.body_script.set_ult_charge(1400)
                for _ in range(100 if cast == 2 else 30):
                    self.step(f, buttons=cast)
                fight = f.run.match.combat
                self.assertIn(f.b.body, fight.hacks)
                deadline = max(effect.until_tick for effect in fight.hacks[f.b.body])
                self.step(f, enemy=16)
                for _ in range(30):
                    self.step(f)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(fight.is_stunned(f.a))
                while f.run.frame < deadline:
                    self.step(f)
                self.stun(f)


class SombraNpcControlOverlapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def make(self, definition):
        f = SombraControlTests().make(distance=20)
        for player in f.run.players:
            player.client.bot_stream = lambda body: None  # no observer projectile-wire claim
        bot = npc.spawn(f.run.match, definition, (0, 0, 3), team=1)
        bot.body_script.component.set(9948, 0)  # explicit authored profile, not retail difficulty
        f.press(0, 4)
        f.a.body_script.set_ult_charge(1400)
        f.press(0x20, 30)
        self.assertIn(bot.entity, f.run.match.combat.hacks)
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)
        f.b.mover.position[:] = (0, 0, 6)
        f.b.mover.body = None
        return f, bot

    def stun(self, f, bot):
        flashbang.FlashbangTests.step(self, f.run, f.b, bot, cast=16)
        for _ in range(50):
            if f.run.match.combat.is_stunned(bot):
                return f.run.match.combat.stuns[bot.entity]
            flashbang.FlashbangTests.step(self, f.run, f.b, bot)
        self.fail("Actual Flashbang did not stun the NPC")

    def test_emp_hack_and_actual_flashbang_keep_independent_deadlines_for_all_five(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                f, bot = self.make(definition)
                fight = f.run.match.combat
                self.assertIn(bot.entity, fight.hacks)
                hack_end = max(effect.until_tick for effect in fight.hacks[bot.entity])
                before = fight.vitals[bot.entity].total
                # The caster started outside EMP range and is repositioned by the fixture.
                stun_end = self.stun(f, bot)
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(fight.vitals[bot.entity].total, before - 25)
                self.assertLess(stun_end, hack_end)
                while f.run.frame < stun_end:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertFalse(fight.is_stunned(bot))
                self.assertIn(bot.entity, fight.hacks)
                self.assertTrue(bot.body_script.component.entity_var(888).value())
                self.assertEqual(bot.body_script.component.entity_var(6396).value().id, f.a.body)
                while f.run.frame < hack_end:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertFalse(bot.body_script.component.entity_var(6396).value())

    def test_four_actual_firearm_npcs_resume_primary_after_stun_while_emp_hack_remains(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.make(self.catalog[key])
                fight = f.run.match.combat
                npc_control.start(f.run.match, f.a)
                end = self.stun(f, bot)
                # Already emitted projectiles may still land; observe after that flight window.
                for _ in range(8):
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                before = f.health(f.a)
                while f.run.frame < end - 1:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                    self.assertEqual(f.health(f.a), before)
                    self.assertNotIn(20, bot.body_script.component.held)
                hack_end = max(effect.until_tick for effect in fight.hacks[bot.entity])
                while f.run.frame < hack_end - 1 and f.health(f.a) == before:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertLess(f.health(f.a), before)
                self.assertIn(bot.entity, fight.hacks)
                self.assertTrue(bot.body_script.component.entity_var(888).value())


if __name__ == "__main__":
    unittest.main()
