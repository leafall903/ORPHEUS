"""Real Genji Deflect input versus Echo health/projectiles; geometry is reconstructed."""
import unittest

import test_echo_combat as echo_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


class EchoDeflectTests(unittest.TestCase):
    def session(self, distance=5):
        run, echo, defender, _, angles = echo_tests.EchoPrimaryTests().session(distance=distance)
        run.match.switch_hero(defender, heroes()[GENJI], preserve_pose=True)
        fight = run.match.combat
        facing = aim(fight._eye(defender), fight._eye(echo))
        for _ in range(25):
            run.step({defender: (*facing, 0)})
        target = next(t for t in fight._targets() if t.owner is defender)
        own = next(t for t in fight._targets() if t.owner is echo)
        return run, echo, defender, target, own, angles, facing

    @staticmethod
    def deflect_state(defender):
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == 0x3A3)
        return instance.states.get(10)

    def start(self, run, defender, facing):
        run.step({defender: (*facing, 16)})
        for _ in range(5):
            run.step({defender: (*facing, 0)})
        self.assertIsNotNone(self.deflect_state(defender))
        self.assertTrue(self.deflect_state(defender).active)

    def test_actual_genji_deflect_returns_all_three_echo_primary_projectiles(self):
        run, echo, defender, target, own, angles, facing = self.session()
        x, y, z = own.feet
        facing = aim(run.match.combat._eye(defender), (x, y + .8, z))
        self.start(run, defender, facing)
        run.step({echo: (*angles, 1), defender: (*facing, 0)})
        for _ in range(25):
            run.step({defender: (*facing, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(run.match.combat.vitals_of(own).total, 149)
        self.assertEqual(len(defender.client.messages(combat.HIT)), 3)
        self.assertEqual(echo.client.messages(combat.HIT), [])

    def test_genji_view_height_uses_authored_standing_and_crouched_values(self):
        run, _, defender, _, _, _, facing = self.session()
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(defender)[1] - defender.mover.position[1], 1.2)
        for _ in range(3):
            run.step({defender: (*facing, CROUCH)})
        self.assertTrue(defender.mover.crouched)
        self.assertAlmostEqual(fight._eye(defender)[1] - defender.mover.position[1], .84)
        target = next(t for t in fight._targets() if t.owner is defender)
        self.assertAlmostEqual(target.eye, .84)

    def test_actual_deflect_returns_sticky_contacts_and_delayed_explosions(self):
        run, echo, defender, target, own, angles, facing = self.session()
        self.start(run, defender, facing)
        run.step({echo: (*angles, 2), defender: (*facing, 0)})
        for _ in range(140):
            run.step({echo: (*angles, 0), defender: (*facing, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(run.match.combat.vitals_of(own).total, 20)
        self.assertFalse(run.match.combat.rockets)
        self.assertTrue(defender.client.messages(combat.HIT))
        self.assertEqual(echo.client.messages(combat.HIT), [])

    def test_deflect_blocks_front_melee_without_reflecting_but_not_rear_melee(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                run, echo, defender, target, own, angles, facing = self.session(distance=2)
                if not frontal:
                    facing = ((facing[0] + 65536) % 65536 - 32768, -facing[1])
                self.start(run, defender, facing)
                run.step({echo: (*angles, 0x800), defender: (*facing, 0)})
                for _ in range(30):
                    run.step({defender: (*facing, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 200 if frontal else 170)
                self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_focusing_beam_passes_deflect_and_does_not_damage_its_owner(self):
        run, echo, defender, target, own, angles, facing = self.session()
        self.start(run, defender, facing)
        for _ in range(20):
            run.step({echo: (*angles, 16), defender: (*facing, 0)})
        self.assertLess(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(run.match.combat.vitals_of(own).total, 200)
        self.assertTrue(self.deflect_state(defender).active)

    def test_rear_projectiles_and_projectiles_after_deflect_expiry_hit_genji(self):
        for rear in (True, False):
            with self.subTest(rear=rear):
                run, echo, defender, target, own, _, facing = self.session()
                x, y, z = target.feet
                angles = aim(run.match.combat._eye(echo), (x, y + .7, z))
                if rear:
                    facing = ((facing[0] + 65536) % 65536 - 32768, -facing[1])
                self.start(run, defender, facing)
                if not rear:
                    for _ in range(140):
                        run.step({defender: (*facing, 0)})
                    self.assertFalse(self.deflect_state(defender).active)
                run.step({echo: (*angles, 1), defender: (*facing, 0)})
                for _ in range(25):
                    run.step({defender: (*facing, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 149)
                self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_real_wall_intercepts_before_deflect_and_misses_do_not_turn_into_hits(self):
        for blocked in (True, False):
            with self.subTest(wall=blocked):
                run, echo, defender, target, own, angles, facing = self.session()
                self.start(run, defender, facing)
                if blocked:
                    x, y, z = echo.mover.position
                    run.match.collision_world = World(
                        box(x-20, x+20, y-1, y, z-20, z+20)
                        + wall(x-5, z+2, x+5, z+2, y0=y, y1=y+5))
                else:
                    angles = (angles[0] + 16384, 0)
                run.step({echo: (*angles, 1), defender: (*facing, 0)})
                for _ in range(25):
                    run.step({defender: (*facing, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_echo_copied_genji_deflects_and_gains_charge_as_the_damage_owner(self):
        run, echo, enemy, _, _, angles, _ = self.session()
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        self.assertTrue(run.match.combat.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, GENJI)
        echo.body_script.ult_seconds = 0
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        x, y, z = enemy.mover.position
        facing = aim(fight._eye(echo), (x, y + .8, z))
        incoming = aim(fight._eye(enemy), (echo.mover.position[0], echo.mover.position[1] + 1,
                                          echo.mover.position[2]))
        self.start(run, echo, facing)
        run.step({enemy: (*incoming, 1), echo: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*facing, 0)})
        targets = {t.owner: t for t in fight._targets()}
        self.assertEqual(fight.vitals_of(targets[echo]).total, 200)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 149)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 331.5)
        for _ in range(140):
            run.step({echo: (*facing, 0)})
        self.assertFalse(self.deflect_state(echo).active)
        instance = self.deflect_state(echo).instance
        self.assertGreater(instance.vars[61].value(), 0)
        run.step({echo: (*facing, 16)})
        for _ in range(5):
            run.step({echo: (*facing, 0)})
        self.assertFalse(self.deflect_state(echo).active)
        for _ in range(700):
            if instance.vars[61].value() <= 0:
                break
            run.step({echo: (*facing, 0)})
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.start(run, echo, facing)
        run.step({enemy: (*incoming, 1), echo: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*facing, 0)})
        self.assertEqual(fight.vitals_of(targets[echo]).total, 200)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 98)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 663)

        run.step({enemy: (*incoming, 16), echo: (*facing, 0)})
        for _ in range(20):
            run.step({enemy: (*incoming, 0), echo: (*facing, 0)})
        self.assertTrue(self.deflect_state(echo).active)
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertLess(fight.vitals_of(targets[echo]).total, 200)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 98)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 663)
