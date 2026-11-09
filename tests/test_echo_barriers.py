"""Real Echo input versus finite Zarya pet barriers; native hosting/presentation remain open."""
import unittest
from dataclasses import replace

import test_echo_combat as echoes
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data

ZARYA = 0x02E0000000000068


class EchoBarrierTests(unittest.TestCase):
    def session(self, distance=5):
        run, echo, zarya, _, angles = echoes.EchoPrimaryTests().session(distance=distance)
        run.match.switch_hero(zarya, heroes()[ZARYA], preserve_pose=True)
        for _ in range(25):
            run.step({})
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(0)
        target = next(t for t in run.match.combat._targets() if t.owner is zarya)
        return run, echo, zarya, target, angles

    def start(self, run, zarya):
        run.step({zarya: (0, 0, 8)})
        bubble = run.match.combat.barriers.sync(zarya)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertNotEqual(bubble.entity, zarya.body)
        return bubble

    @staticmethod
    def fire(run, echo, angles):
        run.step({echo: (*angles, 1)})
        for _ in range(22):
            run.step({echo: (*angles, 0)})

    def test_primary_hits_finite_barrier_instead_of_body_and_gives_no_health_charge(self):
        run, echo, zarya, target, angles = self.session()
        bubble = self.start(run, zarya)
        self.fire(run, echo, angles)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertEqual(bubble.health, 149)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(echo.client.messages(combat.HIT), [])
        weapon = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x19E5)
        self.assertEqual(weapon.vars[53].value(), 11)
        energy = zarya.body_script.component.entity_var(87).value()
        self.assertGreater(energy, 9)
        self.assertLessEqual(energy, 10.2+0.0001)

    def test_outer_sphere_miss_static_wall_team_and_rear(self):
        for mode in ('outer', 'miss', 'wall', 'ally', 'rear'):
            with self.subTest(mode=mode):
                run, echo, zarya, target, angles = self.session()
                fight = run.match.combat
                x, y, z = zarya.mover.position
                if mode in ('outer', 'miss'):
                    offset = 1.1 if mode == 'outer' else 2.0
                    angles = aim(fight._eye(echo), (x+offset, y+target.height*.5, z))
                elif mode == 'wall':
                    run.match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                        + wall(x-5, z-2, x+5, z-2, y0=y, y1=y+6))
                elif mode == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    zarya.team = echo.team
                bubble = self.start(run, zarya)
                if mode == 'rear':
                    self.assertLess(echo.mover.position[2], zarya.mover.position[2])
                    self.assertGreater(combat.aim(zarya.mover.yaw, zarya.mover.pitch)[2], 0)
                self.fire(run, echo, angles)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertEqual(bubble.health, 149 if mode in ('outer', 'rear') else 200)

    def test_break_consumes_last_whole_hit_then_next_round_hits_body(self):
        run, echo, zarya, target, angles = self.session()
        fight = run.match.combat
        bubble = self.start(run, zarya)
        for _ in range(4):
            self.fire(run, echo, angles)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.barriers.sync(zarya))
        self.assertFalse(bubble.phase.active)
        self.assertEqual(fight.vitals_of(target).total, 400)
        self.fire(run, echo, angles)
        self.assertEqual(fight.vitals_of(target).total, 349)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 51)

    def test_stickies_attach_to_barrier_and_delayed_blast_debits_its_pool(self):
        run, echo, zarya, target, angles = self.session()
        fight = run.match.combat
        bubble = self.start(run, zarya)
        run.step({echo: (*angles, 2)})
        for _ in range(35):
            run.step({echo: (*angles, 0)})
        self.assertEqual(bubble.health, 170)
        self.assertEqual(len(fight.rockets), 6)
        self.assertTrue(all(r.attached_entity == bubble.entity for r in fight.rockets))
        for _ in range(70):
            run.step({echo: (*angles, 0)})
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 20)
        self.assertEqual(fight.vitals_of(target).total, 400)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)

    def test_actual_melee_can_hit_outer_sphere_without_reaching_body(self):
        run, echo, zarya, target, angles = self.session(distance=3)
        bubble = self.start(run, zarya)
        for _ in range(24):
            run.step({echo: (*angles, 0x800)})
        self.assertEqual(bubble.health, 170)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)

    def test_beam_uses_low_barrier_health_instead_of_healthy_owner(self):
        run, echo, zarya, target, angles = self.session()
        bubble = self.start(run, zarya)
        for _ in range(2):
            self.fire(run, echo, angles)
        self.assertEqual(bubble.health, 98)
        run.step({echo: (*angles, 16)})
        beam = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1FBD)
        for _ in range(25):
            if beam.states.get(17) is not None and beam.states[17].active:
                break
            run.step({echo: (*angles, 0)})
        self.assertIsNotNone(beam.states.get(17))
        self.assertTrue(beam.states[17].active)
        before = bubble.health
        run.step({echo: (*angles, 0)})
        self.assertAlmostEqual(before-bubble.health, 200*echo.body_script.frame_ms/1000, places=4)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)

    def test_actual_flashbang_pool_damage_does_not_stun_protected_body(self):
        run, caster, zarya, target, _ = self.session(distance=3)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042], preserve_pose=True)
        for _ in range(25):
            run.step({})
        bubble = self.start(run, zarya)
        angles = aim(run.match.combat._eye(caster), run.match.combat._eye(zarya))
        run.step({caster: (*angles, 16)})
        for _ in range(45):
            run.step({caster: (*angles, 0)})
        self.assertEqual(bubble.health, 175)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertFalse(run.match.combat.is_stunned(zarya))

    def test_expiry_cooldown_rejection_and_fresh_pool_after_reuse(self):
        run, echo, zarya, target, angles = self.session()
        fight = run.match.combat
        first = self.start(run, zarya)
        self.fire(run, echo, angles)
        run.step({zarya: (0, 0, 8)})
        self.assertIs(fight.barriers.sync(zarya), first)
        for _ in range(145):
            run.step({})
        self.assertIsNone(fight.barriers.sync(zarya))
        run.step({zarya: (0, 0, 8)})
        self.assertIsNone(fight.barriers.sync(zarya))
        self.fire(run, echo, angles)
        self.assertEqual(fight.vitals_of(target).total, 349)
        for _ in range(700):
            run.step({})
        second = self.start(run, zarya)
        self.assertNotEqual(first.entity, second.entity)
        self.assertGreater(second.counter, first.counter)

    def test_flashbang_that_breaks_low_barrier_does_not_damage_or_stun_body(self):
        run, echo, zarya, target, angles = self.session(distance=3)
        fight = run.match.combat
        hero = heroes()[0x02E0000000000042]
        caster = run.match.add_player(3, 'Flashbang source', hero.guid, echo.team, False)
        in_world(run.match, caster)
        run.match.switch_hero(caster, hero)
        run.players.append(caster)
        x, y, z = zarya.mover.position
        caster.mover = Mover((x+2, y, z), world=run.match.collision_world, data=mover_data(hero.body))
        for _ in range(25):
            run.step({})
        bubble = self.start(run, zarya)
        run.step({echo: (*angles, 2)})
        for _ in range(100):
            if not fight.rockets and bubble.health < 200:
                break
            run.step({echo: (*angles, 0)})
        self.assertEqual(bubble.health, 20)
        self.assertTrue(bubble.phase.active)
        toward = aim(fight._eye(caster), bubble.centre())
        run.step({caster: (*toward, 16)})
        for _ in range(28):
            run.step({caster: (*toward, 0)})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(fight.vitals_of(target).total, 400)
        self.assertFalse(fight.is_stunned(zarya))

    def test_real_allied_field_heals_body_during_barrier_without_refilling_pet(self):
        run, echo, zarya, target, angles = self.session()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        healer = run.match.add_player(3, 'Allied Field', SOLDIER, zarya.team, False)
        in_world(run.match, healer)
        run.match.switch_hero(healer, heroes()[SOLDIER])
        run.players.append(healer)
        x, y, z = zarya.mover.position
        healer.mover = Mover((x+1, y, z), world=run.match.collision_world,
                             data=mover_data(healer.hero.body))
        for _ in range(25):
            run.step({})
        self.fire(run, echo, angles)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 349)
        run.step({healer: (0, 0, 16)})
        bubble = self.start(run, zarya)
        self.fire(run, echo, angles)
        for _ in range(85):
            run.step({})
        self.assertTrue(bubble.phase.active)
        self.assertGreater(vitals.total, 349)
        self.assertLessEqual(vitals.total, 400)
        self.assertEqual(bubble.health, 149)
        self.assertTrue(any(s.field_tick is not None for s in fight.projectiles.shots))

    def test_native_zarya_camera_and_real_crouch_keep_barrier_attached(self):
        run, echo, zarya, target, _ = self.session()
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(zarya)[1]-zarya.mover.position[1], 1.6)
        bubble = self.start(run, zarya)
        original = bubble.centre()
        for _ in range(20):
            run.step({zarya: (0, 0, CROUCH)})
        self.assertTrue(zarya.mover.crouched)
        self.assertAlmostEqual(fight._eye(zarya)[1]-zarya.mover.position[1], 1.12)
        self.assertLess(bubble.centre()[1], original[1])
        angles = aim(fight._eye(echo), bubble.centre())
        run.step({echo: (*angles, 1), zarya: (0, 0, CROUCH)})
        for _ in range(22):
            run.step({echo: (*angles, 0), zarya: (0, 0, CROUCH)})
        self.assertEqual(bubble.health, 149)
        self.assertEqual(fight.vitals_of(target).total, 400)

    def test_copied_zarya_barrier_retires_with_copy_and_echo_is_vulnerable(self):
        run, echo, zarya, _, angles = self.session()
        fight = run.match.combat
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        self.assertEqual(echo.hero.guid, ZARYA)
        for _ in range(25):
            run.step({})
        run.match.switch_hero(zarya, heroes()[echoes.ECHO], preserve_pose=True)
        for _ in range(820):
            run.step({})
        bubble = self.start(run, echo)
        old = echo.body
        self.assertTrue(any(b.caster_body == old for b in fight.barriers.live.values()))
        for _ in range(200):
            if not fight.echo_duplicates.active(echo):
                break
            run.step({})
        self.assertFalse(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, echoes.ECHO)
        self.assertLess(echo.body_script.component.now, bubble.phase.start+2000)
        self.assertFalse(any(b.caster_body == old for b in fight.barriers.live.values()))
        self.assertFalse(any(k[0] == old for k in fight.barriers.seen))
        self.assertIsNone(fight.barriers.sync(echo))
        self.assertIsNone(fight.barriers.attached(bubble.entity))
        own = next(t for t in fight._targets() if t.owner is echo)
        self.assertEqual(fight.vitals_of(own).total, 200)
        x, y, z = own.feet
        reverse = aim(fight._eye(zarya), (x, y + .8, z))
        self.fire(run, zarya, reverse)
        self.assertEqual(fight.vitals_of(own).total, 149)

    def test_real_active_barrier_is_registered_in_passive_snapshot(self):
        observer = snapshots.ServerSnapshotTests()
        session, peers, players = observer.scene()
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': ZARYA})
        peers[0].commands(player.body_script.frame+1, 1, 8)
        bubble = player.match.combat.barriers.sync(player)
        self.assertIsNotNone(bubble)
        before = observer.capture(session)
        self.assertIn('ow174.game.barriers.Bubble', str(before))
        self.assertEqual(snapshots.snapshot_digest(before),
                         snapshots.snapshot_digest(observer.capture(session)))


if __name__ == '__main__':
    unittest.main()
