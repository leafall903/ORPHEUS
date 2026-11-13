"""Actual DVa ability commands use one common world-time force receiver."""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_delivery_created_body as created
from test_game_combat import KANEZAKA_DM, Run, dva_pilot_input

from ow174.game import defenses, dva
from ow174.game.combat import DvaImpulseContext, Vitals
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class DvaKnockbackProducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()
        cls.contract = json.loads((Path(__file__).resolve().parents[2]
                                   / 'fixtures/behavior/dva-knockback-producers-174-v1.json').read_text())

    def scene(self, distance):
        run, pilot, victim, _, mech = created.CreatedBodyTests().scenario()
        x, y, z = mech.position
        victim.mover.position[:] = (x, y, z + distance)
        victim.mover.body = None
        for _ in range(100):
            run.step({})
        fight = run.match.combat
        hp = fight.vitals[victim.body] = Vitals(victim.body, 5000)
        return run, pilot, victim, mech, hp

    def collect(self, fight, victim):
        events = []
        original = fight.knockbacks.apply

        def apply(target, impulse, **kwargs):
            before = target.owner.mover.velocity if target.owner is victim else None
            result = original(target, impulse, **kwargs)
            if target.owner is victim:
                events.append({'impulse': tuple(impulse), 'before': before, 'position': target.feet,
                               'world': fight.tick, 'accepted': result, **kwargs})
            return result
        return events, patch.object(fight.knockbacks, 'apply', apply)

    def check_force(self, events, source, action):
        flat, minimum, additive = self.contract['expected'][action]['force']
        self.assertEqual(len(events), 1, events)
        event = events[0]
        self.assertTrue(event['accepted'], event)
        self.assertEqual(event['source_entity'], source, event)
        self.assertEqual(event['impact_tick'], event['world'], event)
        self.assertNotIn('impact_frame', event)
        dx, dy, dz = event['impulse']
        self.assertAlmostEqual(math.hypot(dx, dz), flat, places=4)
        self.assertAlmostEqual(dy, max(additive, minimum - event['before'][1]), places=4)

    def test_actual_boosters_preserve_faster_airborne_y_and_contact_damage_once(self):
        run, pilot, victim, mech, hp = self.scene(4.5)
        start = tuple(victim.mover.position)
        victim.mover.velocity = (0., 5., 0.)
        events, collect = self.collect(run.match.combat, victim)
        with collect:
            run.step({pilot: (0, 0, 8)})
            for _ in range(35):
                run.step({})
        self.assertEqual(hp.total, 4990)
        self.check_force(events, mech.entity, 'boosters')
        self.assertGreater(victim.mover.position[2], start[2] + .1)

    def test_actual_self_destruct_applies_world_force_once_after_damage(self):
        run, pilot, victim, mech, hp = self.scene(3.)
        start = tuple(victim.mover.position)
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        events, collect = self.collect(run.match.combat, victim)
        with collect:
            run.step({pilot: (0, 0, 32)})
            for _ in range(210):
                run.step({})
        self.assertEqual(hp.total, 4000)
        self.check_force(events, mech.entity, 'self_destruct')
        self.assertGreater(victim.mover.position[2], start[2] + .1)

    def test_actual_call_mech_landing_uses_pilot_source_and_damage_once(self):
        run, pilot, victim, _mech, hp = self.scene(1.5)
        dva_pilot_input(pilot)
        for _ in range(225):
            run.step({})
        x, y, z = pilot.mover.position
        victim.mover.position[:] = (x, y, z + 1.5)
        victim.mover.body = None
        component = pilot.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        events, collect = self.collect(run.match.combat, victim)
        with collect:
            run.step({pilot: (0, 0, 32)})
            for _ in range(150):
                run.step({})
        self.assertEqual(hp.total, 4750)
        self.check_force(events, pilot.body, 'call_mech')
        event = events[0]
        displacement = sum((victim.mover.position[i] - event['position'][i]) * event['impulse'][i]
                           for i in (0, 2))
        self.assertGreater(displacement, .1)

    def test_original_self_destruct_ring_interpolation_and_exclusive_edge(self):
        _run, _pilot, _victim, mech, _hp = self.scene(3.)
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0xB2F)
        state = instance.node_state(instance.graph.node(22))
        context = DvaImpulseContext(instance)
        force = dva.health_impulse(state, context, (0., 0., 0.), (12., 0., 0.), 12., 15.)
        self.assertEqual(tuple(force), (17.5, 0., 0.))
        self.assertIsNone(dva.health_impulse(state, context, (0., 0., 0.), (20., 0., 0.), 20., 0.))
        self.assertIsNone(dva.health_impulse(state, context, (0., 0., 0.), (20., 0., 0.), 20. - 1e-8, 0.))

    def test_actual_fortify_blocks_boosters_force_but_preserves_reduced_damage(self):
        run, pilot, victim, mech, _hp = self.scene(4.5)
        self.assertTrue(run.match.switch_hero(victim, find_hero('orisa')))
        x, y, z = mech.position
        victim.mover.position[:], victim.mover.body = (x, y, z + 4.5), None
        for _ in range(20):
            run.step({})
        run.step({victim: (0, 0, 8)})
        self.assertAlmostEqual(defenses.fortify_scale(victim), .6)
        hp = run.match.combat.vitals[victim.body] = Vitals(victim.body, 5000)
        events, collect = self.collect(run.match.combat, victim)
        with collect:
            run.step({pilot: (0, 0, 8)})
            for _ in range(35):
                run.step({})
        self.assertAlmostEqual(hp.total, 4994, places=5)
        self.assertEqual(events, [])

    def test_actual_boosters_keep_offset_owner_frames_out_of_impact_tick(self):
        run = Run(KANEZAKA_DM, ('DVa', 'Victim'))
        pilot, victim = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot.commands.last_frame = 9000
        self.assertTrue(run.match.switch_hero(pilot, find_hero('dva')))
        mech = pilot.controlled_mech
        x, y, z = mech.position
        victim.mover.position[:], victim.mover.body = (x, y, z + 4.5), None

        def step(offset, buttons=0):
            world = 1000 + offset
            run.match.update(world * .016, world)
            pilot.take_commands([Command(9000 + offset, buttons=buttons)])
            victim.take_commands([Command(world)])

        for offset in range(1, 101):
            step(offset)
        hp = run.match.combat.vitals[victim.body] = Vitals(victim.body, 5000)
        events, collect = self.collect(run.match.combat, victim)
        with collect:
            step(101, 8)
            for offset in range(102, 137):
                step(offset)
        self.assertEqual(hp.total, 4990)
        self.check_force(events, mech.entity, 'boosters')
        self.assertLess(events[0]['impact_tick'], 2000)
        hits = pilot.client.messages(25001)
        self.assertTrue(hits)
        self.assertTrue(all(hit['+0x84'] >= 9000 for hit in hits))
