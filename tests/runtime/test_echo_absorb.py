"""Actual Sigma Grasp input consumes Echo fire; native geometry/pool presentation stay reconstructed."""
import unittest
from dataclasses import replace

import test_echo_combat as echo_tests
import test_echo_duplicate as copies
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, projectiles
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

SIGMA = 0x02E000000000023B


class EchoAbsorbTests(unittest.TestCase):
    def session(self, distance=5):
        run, echo, sigma, _, angles = echo_tests.EchoPrimaryTests().session(distance=distance)
        run.match.switch_hero(sigma, heroes()[SIGMA], preserve_pose=True)
        for _ in range(25):
            run.step({})
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma)
        own = next(t for t in fight._targets() if t.owner is echo)
        facing = aim(fight._eye(sigma), fight._eye(echo))
        return run, echo, sigma, target, own, angles, facing

    @staticmethod
    def grasp(sigma):
        return next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)

    def start(self, run, sigma, facing):
        run.step({sigma: (*facing, 8)})
        for _ in range(5):
            run.step({sigma: (*facing, 0)})
        self.assertTrue(self.grasp(sigma).states[1].active)

    def finish(self, run, sigma, facing):
        for _ in range(160):
            if not self.grasp(sigma).states[1].active:
                break
            run.step({sigma: (*facing, 0)})
        self.assertFalse(self.grasp(sigma).states[1].active)

    def test_real_grasp_absorbs_echo_projectiles_instead_of_reflecting_them(self):
        run, echo, sigma, target, own, angles, facing = self.session()
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertEqual(run.match.combat.vitals_of(own).total, 200)
        self.assertEqual(echo.client.messages(combat.HIT), [])
        self.assertEqual(sigma.client.messages(combat.HIT), [])
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(self.grasp(sigma).vars[21312].value(), 51)

    def capture(self):
        run, echo, sigma, target, own, angles, facing = self.session()
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.finish(run, sigma, facing)
        return run, echo, sigma, target, own, angles, facing

    def test_captured_damage_grants_temporary_shields_consumed_before_base_pools(self):
        run, echo, sigma, target, _, angles, facing = self.capture()
        vitals = run.match.combat.vitals_of(target)
        self.assertAlmostEqual(vitals.temporary_shields, 30.6, places=4)
        self.assertAlmostEqual(vitals.total, 430.6, places=4)
        self.assertAlmostEqual(vitals.maximum, 430.6, places=4)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertAlmostEqual(vitals.now[SHIELDS_POOL], 79.6, places=4)
        self.assertAlmostEqual(vitals.total, 379.6, places=4)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 51)

    def test_real_sticky_is_consumed_without_delayed_blast_and_grants_108(self):
        run, echo, sigma, target, own, angles, facing = self.session()
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 2), sigma: (*facing, 0)})
        for _ in range(150):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        fight = run.match.combat
        self.assertEqual(self.grasp(sigma).vars[21312].value(), 180)
        self.assertAlmostEqual(fight.vitals_of(target).temporary_shields, 108, places=4)
        self.assertAlmostEqual(fight.vitals_of(target).total, 508, places=4)
        self.assertEqual(fight.vitals_of(own).total, 200)
        self.assertFalse(fight.rockets)
        self.assertEqual(echo.client.messages(combat.HIT), [])

    def test_beam_and_melee_bypass_grasp_and_give_no_absorbed_damage(self):
        for button, frames, distance in ((16, 20, 5), (0x800, 30, 2)):
            with self.subTest(button=button):
                run, echo, sigma, target, _, angles, facing = self.session(distance)
                self.start(run, sigma, facing)
                run.step({echo: (*angles, button), sigma: (*facing, 0)})
                for _ in range(frames):
                    run.step({echo: (*angles, 0), sigma: (*facing, 0)})
                self.assertLess(run.match.combat.vitals_of(target).total, 400)
                self.assertEqual(self.grasp(sigma).vars[21312].value(), 0)
                self.finish(run, sigma, facing)
                self.assertEqual(run.match.combat.vitals_of(target).temporary_shields, 0)

    def test_real_wall_miss_allies_and_rear_fire_do_not_feed_grasp(self):
        for kind in ('wall', 'miss', 'ally', 'rear'):
            with self.subTest(kind=kind):
                run, echo, sigma, target, _, angles, facing = self.session()
                if kind == 'rear':
                    facing = ((facing[0]+65536) % 65536 - 32768, -facing[1])
                self.start(run, sigma, facing)
                if kind == 'wall':
                    x, y, z = echo.mover.position
                    run.match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                        + wall(x-5, z+1, x+5, z+1, y0=y, y1=y+6))
                elif kind == 'miss':
                    angles = (angles[0]+16384, 0)
                elif kind == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sigma.team = echo.team
                run.step({echo: (*angles, 1), sigma: (*facing, 0)})
                for _ in range(25):
                    run.step({echo: (*angles, 0), sigma: (*facing, 0)})
                self.assertEqual(self.grasp(sigma).vars[21312].value(), 0)
                self.assertEqual(run.match.combat.vitals_of(target).total, 349 if kind == 'rear' else 400)

    def test_real_multiple_echo_volleys_hit_the_authored_400_shield_cap(self):
        run, echo, sigma, target, _, _, facing = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        attackers = [echo]
        x, y, z = echo.mover.position
        for index in range(3):
            other = run.match.add_player(index+3, 'Echo source', echo_tests.ECHO, echo.team, False)
            in_world(run.match, other)
            run.match.switch_hero(other, heroes()[echo_tests.ECHO])
            run.players.append(other)
            other.mover = Mover((x + (index-1)*.15, y, z), world=run.match.collision_world,
                                data=mover_data(other.hero.body))
            attackers.append(other)
        for _ in range(25):
            run.step({})
        self.start(run, sigma, facing)
        for _ in range(90):
            inputs = {a: (*aim(run.match.combat._eye(a), (x, y+1, z+5)), 1) for a in attackers}
            inputs[sigma] = (*facing, 0)
            run.step(inputs)
        self.assertGreater(self.grasp(sigma).vars[21312].value(), 400/.6)
        self.finish(run, sigma, facing)
        self.assertEqual(run.match.combat.vitals_of(target).temporary_shields, 400)
        self.assertEqual(run.match.combat.vitals_of(target).total, 800)

    def test_decay_cooldown_rejection_and_reuse_follow_resource_values(self):
        run, echo, sigma, target, _, angles, facing = self.capture()
        fight, instance = run.match.combat, self.grasp(sigma)
        vitals = fight.vitals_of(target)
        run.step({sigma: (*facing, 8)})
        for _ in range(5):
            run.step({sigma: (*facing, 0)})
        self.assertFalse(instance.states[1].active)
        self.assertTrue(any(pool.identifier == 0x5847 and not pool.removed for pool in vitals.extra.values()))
        grace = instance.states[29]
        grace_end = grace.start / 1000 + expr.to_float(grace.evaluate(grace.node.field("m_timeout"), watch=False))
        while fight.now + combat.TICK <= grace_end:
            run.step({sigma: (*facing, 0)})
        self.assertAlmostEqual(vitals.temporary_shields, 30.6, places=4)
        before = vitals.temporary_shields
        for _ in range(100):
            run.step({sigma: (*facing, 0)})
        self.assertAlmostEqual(before-vitals.temporary_shields, 8, delta=.08)
        for _ in range(800):
            if instance.vars[61].value() <= 0:
                break
            run.step({sigma: (*facing, 0)})
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertFalse(instance.states[1].active)
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.finish(run, sigma, facing)
        self.assertAlmostEqual(vitals.temporary_shields, 30.6, places=4)

    def test_allied_real_field_does_not_refill_spent_temporary_shields(self):
        run, echo, sigma, target, _, angles, facing = self.session(distance=2)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        healer = run.match.add_player(3, 'Field ally', SOLDIER, sigma.team, False)
        in_world(run.match, healer)
        run.match.switch_hero(healer, heroes()[SOLDIER])
        run.players.append(healer)
        x, y, z = sigma.mover.position
        healer.mover = Mover((x+2, y, z), world=run.match.collision_world,
                             data=mover_data(healer.hero.body))
        for _ in range(25):
            run.step({})
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.finish(run, sigma, facing)
        run.step({echo: (*angles, 0x800), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({sigma: (*facing, 0)})
        vitals = run.match.combat.vitals_of(target)
        self.assertAlmostEqual(vitals.temporary_shields, .6, places=4)
        run.step({healer: (0, 16000, 16)})
        for _ in range(140):
            run.step({sigma: (*facing, 0)})
        self.assertGreater(len(run.match.combat.projectiles.shots), 0)
        field = next(shot for shot in run.match.combat.projectiles.shots
                     if shot.spawn == projectiles.FIELD_ENTITY)
        self.assertIsNotNone(field.field_tick)
        a, b = target.segment()
        self.assertLess(combat.segment_distance(projectiles.field_centre(field), a, b) - target.radius,
                        projectiles.HEAL_RADIUS)
        self.assertAlmostEqual(vitals.temporary_shields, .6, places=4)

    def test_copy_shields_expire_with_the_body_before_their_decay_deadline(self):
        run, echo, enemy, _, _, angles, _ = self.session()
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        fight = run.match.combat
        self.assertTrue(fight.echo_duplicates.active(echo))
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(775):
            run.step({})
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(0)
        facing = aim(fight._eye(echo), fight._eye(enemy))
        incoming = aim(fight._eye(enemy), (echo.mover.position[0], echo.mover.position[1]+1,
                                          echo.mover.position[2]))
        self.start(run, echo, facing)
        copied = next(t for t in fight._targets() if t.owner is echo)
        run.step({enemy: (*incoming, 1), echo: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*facing, 0)})
        self.finish(run, echo, facing)
        self.assertAlmostEqual(fight.vitals_of(copied).temporary_shields, 30.6, places=4)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        for _ in range(200):
            if not fight.echo_duplicates.active(echo):
                break
            run.step({})
        restored = next(t for t in fight._targets() if t.owner is echo)
        self.assertEqual(echo.hero.guid, echo_tests.ECHO)
        self.assertEqual(fight.vitals_of(restored).temporary_shields, 0)
        self.assertEqual(fight.vitals_of(restored).total, 200)

    def test_copied_grasp_counts_projectiles_but_not_incoming_beam(self):
        run, echo, enemy, _, _, angles, _ = self.session()
        copies.EchoDuplicateTests().charged(echo)
        run.step({echo: (*angles, 32)})
        fight = run.match.combat
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, SIGMA)
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(25):
            run.step({})

        facing = aim(fight._eye(echo), fight._eye(enemy))
        x, y, z = echo.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1, z))
        copied = next(t for t in fight._targets() if t.owner is echo)
        vitals = fight.vitals_of(copied)
        self.start(run, echo, facing)
        grasp = self.grasp(echo)
        run.step({echo: (*facing, 0), enemy: (*incoming, 1)})
        for _ in range(25):
            run.step({echo: (*facing, 0)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(grasp.vars[21312].value(), 51)

        run.step({echo: (*facing, 0), enemy: (*incoming, 16)})
        for _ in range(20):
            run.step({echo: (*facing, 0), enemy: (*incoming, 0)})
        self.assertTrue(grasp.states[1].active)
        self.assertLess(vitals.total, 400)
        self.assertEqual(grasp.vars[21312].value(), 51)
        self.finish(run, echo, facing)
        self.assertAlmostEqual(vitals.temporary_shields, 30.6, places=4)
        self.assertTrue(fight.echo_duplicates.active(echo))

    def test_sigma_camera_and_temporary_health_are_registered_for_passive_snapshots(self):
        run, _, sigma, _, _, _, facing = self.session()
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(sigma)[1]-sigma.mover.position[1], 2.1)
        for _ in range(3):
            run.step({sigma: (*facing, CROUCH)})
        self.assertAlmostEqual(fight._eye(sigma)[1]-sigma.mover.position[1], 1.75)
        observer = snapshots.ServerSnapshotTests()
        session, _, players = observer.scene(two=True)
        player = players[0]
        target = next(t for t in player.match.combat._targets() if t.owner is player)
        vitals = player.match.combat.vitals_of(target)
        # This checks passive storage only; no synthetic grant is counted as combat evidence.
        vitals.temporary_shield = combat.TemporaryShield(17.0, 20.0, 5.0, 5.0, (1, 7))
        document = observer.capture(session)
        self.assertTrue('temporary_shield' in str(document))
        self.assertTrue('decay_from' in str(document))

    def test_copied_soldier_hitscan_is_absorbed_with_real_magazine_consumption(self):
        fixture = copies.EchoDuplicateTests()
        run, echo, sigma, _, angles = fixture.session()
        fixture.start(run, echo, angles)
        echo.body_script.ult_seconds = 0
        run.match.switch_hero(sigma, heroes()[SIGMA], preserve_pose=True)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        facing = aim(fight._eye(sigma), fight._eye(echo))
        self.start(run, sigma, facing)
        for _ in range(26):
            run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        self.assertEqual(self.grasp(sigma).vars[21312].value(), 80)
        weapon = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x254)
        self.assertEqual(weapon.vars[53].value(), 26)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        target = next(t for t in fight._targets() if t.owner is sigma)
        self.assertEqual(fight.vitals_of(target).total, 400)
        self.finish(run, sigma, facing)
        self.assertAlmostEqual(fight.vitals_of(target).temporary_shields, 48, places=4)

    def test_rear_actual_flashbang_interrupts_grasp_without_granting_pending_shields(self):
        run, echo, sigma, target, _, angles, facing = self.session()
        caster_hero = heroes()[0x02E0000000000042]
        caster = run.match.add_player(3, 'Rear Flashbang', caster_hero.guid, echo.team, False)
        in_world(run.match, caster)
        run.match.switch_hero(caster, caster_hero)
        run.players.append(caster)
        x, y, z = sigma.mover.position
        caster.mover = Mover((x, y, z+2), world=run.match.collision_world,
                             data=mover_data(caster.hero.body))
        for _ in range(25):
            run.step({sigma: (*facing, 0)})
        self.start(run, sigma, facing)
        run.step({echo: (*angles, 1), sigma: (*facing, 0)})
        for _ in range(25):
            run.step({echo: (*angles, 0), sigma: (*facing, 0)})
        self.assertEqual(self.grasp(sigma).vars[21312].value(), 51)
        rear = aim(run.match.combat._eye(caster), (x, y+1, z))
        run.step({caster: (*rear, 16), sigma: (*facing, 0)})
        for _ in range(50):
            if run.match.combat.is_stunned(sigma):
                break
            run.step({caster: (*rear, 0), sigma: (*facing, 0)})
        self.assertTrue(run.match.combat.is_stunned(sigma))
        self.assertFalse(self.grasp(sigma).states[1].active)
        self.assertEqual(run.match.combat.vitals_of(target).temporary_shields, 0)
        self.assertEqual(run.match.combat.vitals_of(target).total, 375)
