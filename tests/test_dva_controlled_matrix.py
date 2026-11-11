"""Actual Zen projectiles against an owned mech after Call Mech entry."""
import os
import unittest
from itertools import product
from unittest.mock import patch

import test_dva_factory as factory
from test_game_combat import aim

from ow174.game import combat, dva
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_RUNTIME") and os.environ.get("ORPHEUS_PROJECTILE_TAGS"),
                     "requires actual mech metadata and verified projectile tags")
class DvaControlledMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        factory.DvaFactoryTests.setUpClass()

    def called_mech(self):
        run, pilot, zen, actor, _, _ = factory.DvaFactoryTests().arena()
        for _ in range(120):
            run.step({})
            if dva.controlled_mech(pilot) is actor:
                break
        self.assertIs(dva.controlled_mech(pilot), actor)
        return run, pilot, zen, actor

    def test_actual_call_mech_routes_secondary_to_matrix_and_blocks_orb(self):
        run, pilot, zen, actor = self.called_mech()
        fight = run.match.combat
        self.assertNotIn(pilot.body, {t.entity for t in fight._targets()})
        mx, my, mz = actor.position
        zx, zy, zz = zen.mover.position
        forward = aim((mx, my+1.74, mz), (zx, zy+1.6, zz))
        orb = aim(fight._eye(zen), (mx, my+1.4, mz))
        instance = next(i for i in actor.body_script.component.instances.values()
                        if i.graph.index == 0x2CC)
        for _ in range(100):
            run.step({pilot: (*forward, 0)})
        for _ in range(10):
            run.step({pilot: (*forward, SECONDARY)})
        self.assertEqual(instance.vars[971].value(), 100)
        self.assertGreater(instance.vars[87].value(), 90)
        self.assertLess(instance.vars[87].value(), 100)
        self.assertTrue(instance.states[3].active)
        run.step({pilot: (*forward, SECONDARY), zen: (*orb, FIRE)})
        for _ in range(35):
            run.step({pilot: (*forward, SECONDARY)})
        self.assertEqual(fight.vitals[actor.entity].total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)
        for _ in range(30):
            run.step({pilot: (*forward, 0)})
        self.assertFalse(instance.states[3].active)
        run.step({pilot: (*forward, 0), zen: (*orb, FIRE)})
        for _ in range(40):
            run.step({})
        self.assertLess(fight.vitals[actor.entity].total, 600)
        self.assertTrue(zen.client.messages(combat.HIT))

    def test_owned_matrix_depletion_matches_continuous_and_skipped_orb_impacts(self):
        def start():
            run, pilot, zen, actor = self.called_mech()
            fight = run.match.combat
            mx, my, mz = actor.position
            zen.mover.position = [mx, my, mz+24]
            zen.mover.body = None
            forward = aim((mx, my+1.74, mz), (mx, my+1.6, mz+24))
            shot = aim(fight._eye(zen), (mx, my+1.0, mz))
            instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x02CC)
            for _ in range(100):
                run.step({pilot: (*forward, 0)})
            run.step({pilot: (*forward, SECONDARY)})
            self.assertTrue(instance.states[3].active)
            self.assertNotEqual(actor.entity, pilot.body)
            self.assertIs(dva.controlled_mech(pilot), actor)
            return run, pilot, zen, actor, instance, forward, shot

        run, pilot, zen, actor, instance, forward, shot = start()
        cast = run.frame
        fight = run.match.combat
        run.step({pilot: (*forward, SECONDARY), zen: (*shot, FIRE)})
        launch = run.frame
        rocket, = fight.rockets
        for _ in range(35):
            run.step({pilot: (*forward, SECONDARY)})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals[actor.entity].total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))
        travel = run.frame-launch
        self.assertGreater(travel, 5)
        for _ in range(150):
            if not instance.states[3].active:
                break
            run.step({pilot: (*forward, SECONDARY)})
        expiry = run.frame-cast
        self.assertEqual(expiry, 125)
        self.assertEqual(instance.vars[87].value(), 0)

        for late, skipped in product((False, True), repeat=2):
            with self.subTest(late=late, skipped=skipped):
                run, pilot, zen, actor, instance, forward, shot = start()
                fight = run.match.combat
                cast = run.frame
                release = cast+expiry+int(late)-travel
                while run.frame < cast+expiry-3:
                    run.step({pilot: (*forward, SECONDARY),
                              zen: (*shot, FIRE if run.frame+1 == release else 0)})
                self.assertTrue(instance.states[3].active)
                self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                if skipped:
                    run.frame += 4
                    run.step({pilot: (*forward, SECONDARY)})
                else:
                    for _ in range(5):
                        run.step({pilot: (*forward, SECONDARY)})
                self.assertEqual(run.frame, cast+expiry+2)
                for _ in range(25):
                    run.step({pilot: (*forward, SECONDARY)})
                self.assertFalse(fight.rockets)
                self.assertIs(dva.controlled_mech(pilot), actor)
                self.assertEqual(fight.vitals[actor.entity].total, 557 if late else 600)
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(late))
                if late:
                    self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], actor.entity)
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], 43)

    def test_actual_controlled_fusion_cannons_damage_normal_zen_and_respect_transcendence(self):
        for transcendent in (False, True):
            with self.subTest(transcendent=transcendent):
                self.check_fusion_cannons(transcendent)

    def check_fusion_cannons(self, transcendent):
        run, pilot, zen, actor = self.called_mech()
        fight = run.match.combat
        mx, my, mz = actor.position
        zen.mover.position = [mx, my, mz+2]
        zen.mover.body = None
        forward = aim((mx, my+combat.EYE, mz), (mx, my+1.05, mz+2))
        for _ in range(100):
            run.step({pilot: (*forward, 0)})
        if transcendent:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            run.step({zen: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(zen))
        for _ in range(10):
            run.step({pilot: (*forward, FIRE)})
        health = fight.vitals[zen.body].total
        if transcendent:
            self.assertEqual(health, 200)
            self.assertTrue(fight.zenyatta.ultimate(zen))
        else:
            self.assertLess(health, 200)

    def test_controlled_body_moves_while_pilot_stays_at_the_entry_position(self):
        run, pilot, _, actor = self.called_mech()
        parked = tuple(pilot.mover.position)
        start = tuple(actor.position)
        for _ in range(30):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            pilot.take_commands([Command(run.frame, forward=127)])
        self.assertEqual(tuple(pilot.mover.position), parked)
        self.assertGreater(actor.position[2], start[2]+.3)
        self.assertEqual(actor.body_script.frame, pilot.body_script.frame)

    def test_owned_body_registry_is_causal_and_retires_on_hero_change(self):
        run, pilot, _, actor = self.called_mech()
        component = actor.body_script.component
        registry = component.world.dva_queries
        self.assertEqual(registry.lookup_owner(actor.entity).current, pilot.entity)
        before = snapshot_digest(export_component_vm(component))
        registry.owner_context.environment_present = False
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        registry.owner_context.environment_present = True
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        self.assertTrue(run.match.switch_hero(pilot, heroes()[SOLDIER]))
        self.assertNotIn(actor, run.match.bots)
        self.assertIsNone(registry.lookup_owner(actor.entity))
        self.assertIsNone(dva.controlled_mech(pilot))

    def test_actual_zen_orb_demechs_then_authored_return_restores_pilot_fire(self):
        run, pilot, zen, actor = self.called_mech()
        fight = run.match.combat
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        script = pilot.body_script
        parked = tuple(pilot.mover.position)
        for _ in range(30):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            pilot.take_commands([Command(run.frame, forward=127)])
        fallen = tuple(actor.position)
        self.assertGreater(fallen[2], parked[2]+.3)
        vitals = fight.vitals[actor.entity]
        vitals.now.update({0: 20, 1: 0})
        target = (fallen[0], fallen[1]+1, fallen[2])
        angles = aim(fight._eye(zen), target)
        observed = []
        send = nodes.send

        def watch(instance, node, caller):
            if instance.graph.index == 0x0276 and node.pos == 5:
                observed.append(instance.component.entity)
            return send(instance, node, caller)

        self.assertFalse(run.match.queue_body_message(pilot.body, dva.RETURN_TO_PILOT, {}, zen.body))
        with patch.object(nodes, "send", side_effect=watch):
            run.step({zen: (*angles, FIRE)})
            for _ in range(25):
                run.step({})
                if actor.dead:
                    break
        self.assertTrue(actor.dead)
        self.assertEqual(observed, [actor.entity])
        self.assertIsNone(dva.controlled_mech(pilot))
        self.assertIs(pilot.body_script, script)
        self.assertIn(pilot.body, {t.entity for t in fight._targets()})
        self.assertAlmostEqual(pilot.mover.position[2], actor.position[2], delta=.01)
        pilot_target = next(t for t in fight._targets() if t.owner is pilot)
        self.assertEqual(fight.vitals_of(pilot_target).total, 150)
        x, y, z = pilot.mover.position
        zen.mover.position = [x, y, z+3]
        zen.mover.body = None
        for _ in range(100):
            run.step({})
        angles = aim(fight._eye(pilot), (x, y+1.1, z+3))
        zen_target = next(t for t in fight._targets() if t.owner is zen)
        before = fight.vitals_of(zen_target).total
        run.step({pilot: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x2B8)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(fight.vitals[zen.body].total, before-14)
