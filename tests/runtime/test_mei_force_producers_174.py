"""Actual Mei commands against the shared r2 host; no client acceptance."""
import unittest
from unittest.mock import patch

import test_delivered_mei_combat as scenes
import test_mei_blizzard as blizzard_scenes
import test_mei_frost as frost_scenes
import test_mei_wall as wall_scenes

from ow174.game import combat, mei
from ow174.game.content import heroes
from ow174.game.props import PlacedDynamicSphere


class MeiForceProducerTests(unittest.TestCase):
    def test_wall_entry_dispatches_each_original_pillar_pulse_once(self):
        run, owner, _enemy, target = scenes.MeiCombatTests().duel()
        fight = run.match.combat
        x, y, z = owner.mover.position
        sphere = run.match.dynamic_spheres.register(
            PlacedDynamicSphere(0xDD, 0, 0, (x + .25, y, z + 3), .18), inverse_mass=1.)
        with (
            patch.object(run.match.dynamic_spheres, 'dispatch',
                         wraps=run.match.dynamic_spheres.dispatch) as pulses,
            patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as forces,
        ):
            pieces, _ = wall_scenes.WallTests().place(run, owner)
            for _ in range(5):
                run.step({})
        self.assertEqual(pulses.call_count, 5)
        self.assertEqual({row.args[0].entity for row in pulses.call_args_list},
                         {piece.entity for piece in pieces})
        for row in pulses.call_args_list:
            request = row.args[0]
            self.assertEqual((request.strength, request.radius, request.parameter, request.interval),
                             (10., 1., 10000., 10.))
            self.assertEqual(tuple(request.position), tuple(fight.deployables[request.entity].position))
            actor = fight.deployables[request.entity]
            instance = next(i for i in actor.body_script.component.instances.values()
                            if i.graph.index == 0x636)
            self.assertEqual(request.start_ms, instance.states[instance.graph.node(64).state].start)
            self.assertGreaterEqual(request.start_ms, actor.created_tick * 16)
            self.assertLess(request.start_ms, (actor.created_tick + 1) * 16)
        self.assertGreater(sphere.velocity[0], 0.)
        self.assertEqual(forces.call_count, 0)
        self.assertEqual(fight.vitals_of(target).total, 200.)

    def test_shared_melee_damage_and_force_once(self):
        run, owner, enemy, _ = scenes.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[mei.HERO]))
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(enemy, (x, y, z + 2))
        before = tuple(enemy.mover.position)
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as calls:
            for _ in range(15):
                run.step({owner: (0, 0, 0x800)})
        self.assertEqual(calls.call_count, 1)
        call = calls.call_args
        self.assertEqual(call.kwargs['source_entity'], owner.body)
        self.assertIn('impact_tick', call.kwargs)
        self.assertNotIn('impact_frame', call.kwargs)
        self.assertLessEqual(call.kwargs['impact_tick'], run.match.tick)
        self.assertEqual(tuple(call.args[1]), (0., 0., 1.))
        self.assertEqual(fight.vitals_of(fight.player_target(enemy)).total, 220.)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertGreater(enemy.mover.position[2], before[2])
        self.assertAlmostEqual(enemy.mover.position[0], before[0])
        self.assertAlmostEqual(enemy.mover.position[1], before[1])

    def test_weapon_health_contacts_do_not_add_force(self):
        for button, damage in ((1, 2.75), (2, 75.)):
            with self.subTest(button=button):
                run, owner, _enemy, target = scenes.MeiCombatTests().duel()
                fight = run.match.combat
                with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as calls:
                    if button == 1:
                        frost_scenes.FrostTests().one_spray(run, owner, target)
                    else:
                        scenes.MeiCombatTests().fire(run, owner, target, button, .9)
                self.assertEqual(fight.vitals_of(target).total, 200. - damage)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
                self.assertEqual(calls.call_count, 0)

    def test_blizzard_actual_throw_and_area_damage_do_not_add_force(self):
        run, owner, enemy, target = scenes.MeiCombatTests().duel()
        fight = run.match.combat
        case = blizzard_scenes.BlizzardTests()
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as calls:
            case.launch(run, owner)
            area = case.landed(run)
            while run.frame * 16 <= area.starts + 32:
                run.step({})
        self.assertLess(fight.vitals_of(target).total, 200.)
        self.assertIsNotNone(fight.frost(enemy))
        self.assertEqual(calls.call_count, 0)

    def test_cryo_actual_heal_does_not_add_self_force(self):
        run, owner, _enemy, _ = scenes.MeiCombatTests().duel()
        fight = run.match.combat
        vitals = fight.vitals_of(fight.player_target(owner))
        vitals.health = 100.
        before = tuple(owner.mover.position)
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as calls:
            for frame in range(80):
                run.step({owner: (0, 0, 8 if frame == 0 else 0)})
        self.assertGreater(vitals.total, 100.)
        self.assertEqual(calls.call_count, 0)
        self.assertEqual(tuple(owner.mover.position), before)
