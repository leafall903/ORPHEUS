"""Authored Ana E1A5B579 gravity through actual firing and world collision."""
import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat, projectiles
from ow174.game.collision import World
from ow174.game.world import OP_DESTROY


class AnaBallisticsTests(unittest.TestCase):
    def flight(self, button=16):
        run, ana, _enemy, entry = fixture.AnaRifleTests().setup_fight(enemy=True, distance=50)
        run.step({ana: (0, 0, button)})
        for _ in range(40):
            if run.match.combat.rockets:
                break
            run.step({})
        self.assertEqual(len(run.match.combat.rockets), 1)
        return run, ana, entry, run.match.combat.rockets[0]

    def test_authored_grenade_gravity_and_straight_rifle_dart_flight(self):
        for button, gravity, speed in ((16, 9.8, 30), (1, 0, 125), (8, 0, 60)):
            with self.subTest(button=button):
                run, _ana, _entry, rocket = self.flight(button)
                self.assertAlmostEqual(rocket.gravity, gravity, places=5)
                origin = rocket.position
                for _ in range(20):
                    run.step({})
                self.assertIn(rocket, run.match.combat.rockets)
                expected_drop = gravity * combat.TICK ** 2 * 20 * 21 / 2
                self.assertAlmostEqual(rocket.position[1], origin[1] - expected_drop, places=5)
                self.assertAlmostEqual(rocket.position[2], origin[2] + speed * combat.TICK * 20, places=5)

    def test_lofted_twenty_metre_grenade_hits_or_heals_where_straight_input_misses(self):
        for ally in (False, True):
            for lofted in (False, True):
                with self.subTest(ally=ally, lofted=lofted):
                    run, ana, _target, entry = fixture.AnaRifleTests().setup_fight(
                        enemy=not ally, distance=20)
                    fight = run.match.combat
                    vitals = fight.vitals_of(entry)
                    if ally:
                        vitals.damage(100)
                    point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                    direction = (fixture.grenade_aim(run, ana, point) if lofted
                                 else aim(fight._eye(ana), point))
                    run.step({ana: (*direction, 16)})
                    for _ in range(110):
                        run.step({})
                    expected = (200 if ally else 150) if lofted else (100 if ally else 200)
                    self.assertEqual(vitals.total, expected)

    def test_gravity_world_catch_up_matches_individual_frames(self):
        stepped, _ana, _entry, first = self.flight()
        skipped, _ana, _entry, second = self.flight()
        stepped.step({})
        skipped.step({})
        for _ in range(20):
            stepped.step({})
        skipped.frame += 20
        skipped.match.update(skipped.frame * combat.TICK, skipped.frame, frames=False)
        self.assertEqual(first.position, second.position)
        self.assertEqual(first.direction, second.direction)
        self.assertEqual(first.speed, second.speed)
        self.assertEqual(first.flown, second.flown)

    def test_detached_dart_and_grenade_keep_source_after_death_or_replacement(self):
        for button, damage in ((8, 5), (16, 50)):
            for replace_source in (False, True):
                with self.subTest(button=button, replacement=replace_source):
                    run, ana, enemy, entry = fixture.AnaRifleTests().setup_fight(enemy=True, distance=20)
                    fight = run.match.combat
                    victim = fight.vitals_of(entry)
                    point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                    direction = (fixture.grenade_aim(run, ana, point) if button == 16
                                 else aim(fight._eye(ana), point))
                    run.step({ana: (*direction, button)})
                    for _ in range(40):
                        if fight.rockets:
                            break
                        run.step({})
                    rocket, = fight.rockets
                    source = ana.body
                    self.assertEqual(rocket.source_entity, source)
                    self.assertIsNone(rocket.shot)
                    self.assertEqual(victim.total, 200)
                    if replace_source:
                        self.assertTrue(run.match.switch_hero(ana, ana.hero))
                        self.assertNotEqual(ana.body, source)
                    else:
                        caster = next(target for target in fight._targets() if target.owner is ana)
                        fight._damage(enemy, caster, 1000, False, None, run.frame)
                        self.assertTrue(fight.is_dead(ana))
                    self.assertIn(rocket, fight.rockets)
                    for _ in range(110):
                        run.step({})
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(rocket.source_entity, source)
                    self.assertEqual(victim.total, 200 - damage)
                    self.assertEqual(fight.is_asleep(enemy), button == 8)
                    self.assertEqual(victim.healing_block_until > fight.now, button == 16)

    def test_leased_hip_rifle_survives_death_but_retires_on_body_replacement(self):
        for replace_source in (False, True):
            with self.subTest(replacement=replace_source):
                run, ana, enemy, entry = fixture.AnaRifleTests().setup_fight(enemy=True, distance=50)
                fight = run.match.combat
                victim = fight.vitals_of(entry)
                point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                run.step({ana: (*aim(fight._eye(ana), point), 1)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.step({})
                rocket, = fight.rockets
                shot = rocket.shot
                self.assertIsNotNone(shot)
                source = ana.body
                self.assertEqual((rocket.source_entity, shot.body), (source, source))
                copy = fight.projectiles.lease_id(ana, rocket.definition, shot.frame)
                self.assertIsNotNone(copy)
                shot.created[copy] = projectiles._ProjectileArrival(received=True)
                ana.client.entities.clear()

                if replace_source:
                    self.assertTrue(run.match.switch_hero(ana, ana.hero))
                    self.assertNotEqual(ana.body, source)
                    self.assertNotIn(rocket, fight.rockets)
                else:
                    caster = next(target for target in fight._targets() if target.owner is ana)
                    fight._damage(enemy, caster, 1000, False, None, run.frame)
                    self.assertTrue(fight.is_dead(ana))
                    self.assertIn(rocket, fight.rockets)
                for _ in range(100):
                    run.step({})

                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(shot, fight.projectiles.shots)
                self.assertEqual(rocket.source_entity, source)
                self.assertEqual(victim.total, 200 if replace_source else 130)
                destroy, = [row for row in ana.client.entities if row.entity == copy and row.op == OP_DESTROY]
                self.assertEqual(destroy.ch2 is None, replace_source)

    def test_grenade_gravity_lands_on_floor_and_low_arc_clears_low_wall(self):
        run, ana, _entry, rocket = self.flight()
        for _ in range(45):
            run.step({})
        self.assertNotIn(rocket, run.match.combat.rockets)
        for lofted in (False, True):
            with self.subTest(lofted=lofted):
                run, ana, _target, entry = fixture.AnaRifleTests().setup_fight(enemy=True, distance=20)
                x, y, z = ana.mover.position
                wz = z + 10
                run.match.collision_world = World([
                    (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                    (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
                    (x - 3, y, wz, x + 3, y, wz, x + 3, y + 1, wz),
                    (x - 3, y, wz, x + 3, y + 1, wz, x - 3, y + 1, wz),
                ])
                point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                direction = fixture.grenade_aim(run, ana, point) if lofted else (0, 0)
                run.step({ana: (*direction, 16)})
                for _ in range(110):
                    run.step({})
                self.assertEqual(run.match.combat.vitals_of(entry).total, 150 if lofted else 200)
