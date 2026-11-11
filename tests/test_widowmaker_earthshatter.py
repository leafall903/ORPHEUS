"""Actual Reinhardt Earthshatter input against Widowmaker on staged source."""

import os
import unittest

from test_game_combat import aim, in_world
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import earthshatter, rein_barrier, server_snapshot
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.expr import INSTANCE, Entity


class WidowmakerEarthshatterTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[earthshatter.HERO]))
        self.a.mover.position[:] = (0, 0, 8)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat

    def launch(self):
        script = self.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.b: (0, 0, 32)})
        for _ in range(80):
            if self.fight.earthshatter.shatters:
                break
            self.run.step({})
        self.assertTrue(self.fight.earthshatter.shatters)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_wave_damages_stuns_and_expires(self):
        self.launch()
        for _ in range(60):
            self.run.step({})
        self.assertEqual(self.health(self.a), 125)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertFalse(self.fight.earthshatter.shatters)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.press(0, 200)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_active_wave_travel_changes_stored_state(self):
        self.launch()
        manager = self.fight.earthshatter
        wave, = manager.shatters.values()

        def capture():
            storage = server_snapshot._Graph(
                {'combat': self.fight, 'caster': self.b}, server_snapshot.WorldRegistry(),
            )
            root = storage.value(manager)
            return root, storage.finish()

        first = capture()
        wave.travelled += 1
        self.assertNotEqual(capture(), first)

    def test_caster_death_cancels_windup_but_preserves_released_wave(self):
        for released in (False, True):
            with self.subTest(released=released):
                self.setUp()
                caster = self.fight.vitals_of(self.target(self.b))
                caster.damage(caster.total - 1)
                caster.damage(caster.total - 1)
                self.assertEqual(caster.total, 1)
                if released:
                    self.launch()
                else:
                    script = self.b.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    self.run.step({self.b: (0, 0, 32)})
                    self.assertFalse(self.fight.earthshatter.shatters)
                self.press(FIRE)
                self.assertTrue(caster.dead)
                for _ in range(70):
                    self.run.step({})
                self.assertFalse(self.fight.earthshatter.shatters)
                self.assertEqual(self.health(self.a), 125 if released else 175)
                self.assertEqual(self.fight.is_stunned(self.a), released)

    @unittest.skipUnless(
        os.environ.get('ORPHEUS_REINHARDT_BARRIER_GEOMETRY'), 'requires authored shield mesh',
    )
    def test_actual_raised_physical_shield_blocks_wave(self):
        for health in (1600, 5):
            with self.subTest(health=health):
                self.setUp()
                self.check_physical_shield(health)

    def check_physical_shield(self, health):
        ally = self.run.match.add_player(3, 'ShieldAlly', earthshatter.HERO, self.a.team, False)
        in_world(self.run.match, ally)
        self.assertTrue(self.run.match.switch_hero(ally, heroes()[earthshatter.HERO]))
        ally.mover.position[:] = (0, 0, 5)
        ally.mover.body = None
        self.run.players.append(ally)
        script = self.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        launched = False
        for n in range(120):
            self.run.step({ally: (-32768, 0, 2), self.b: (0, 0, 32 if n == 35 else 0)})
            launched |= bool(self.fight.earthshatter.shatters)
            if n == 34:
                shield = rein_barrier.active(ally)
                self.assertIsNotNone(shield)
                shield.write((INSTANCE, 586), health)
                shield.component.run_now()
        self.assertTrue(launched)
        self.assertEqual(shield.vars[586].value(), max(0, health - 50))
        self.assertLess(script.component.entity_var(342).value(), 20)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.is_stunned(self.a))

    def test_static_wall_stops_actual_wave(self):
        self.run.match.collision_world = World([
            (-30, 0, -30, -30, 0, 30, 30, 0, 30),
            (-30, 0, -30, 30, 0, 30, 30, 0, -30),
            (-8, 0, 4, 8, 0, 4, 8, 4, 4),
            (-8, 0, 4, 8, 4, 4, -8, 4, 4),
        ])
        self.launch()
        for _ in range(80):
            self.run.step({})
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertFalse(self.fight.earthshatter.shatters)



class WidowmakerEarthshatterBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_actual_projected_barrier_absorbs_wave_and_blocks_stun(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[earthshatter.HERO]))
        self.a.mover.position[:] = (0, 0, 8)
        self.b.mover.position[:] = (0, 0, 0)
        self.c.mover.position[:] = (-2, 0, 8)
        for player in self.run.players:
            player.mover.body = None
        fight = self.run.match.combat
        protecting = aim(fight._eye(self.c), fight._eye(self.a))
        self.run.step({self.c: (*protecting, 0x10)})
        self.assertEqual(self.barrier().vars[536].value(), Entity(self.a.body))
        script = self.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.b: (0, 0, 32)})
        for _ in range(75):
            self.run.step({})
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 150)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.is_stunned(self.a))
