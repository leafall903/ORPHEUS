"""Exercise the real Genji Deflect phase against Brigitte and incoming rifle fire."""

import unittest

import test_game_brigitte as brigitte_tests

from ow174.game import stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes

GENJI = 0x02E0000000000029
DEFLECT = 16


class DeflectTests(unittest.TestCase):
    def scene(self, names=('Brigitte', 'Genji')):
        run, brig = brigitte_tests.BrigitteTests().fixture(names)
        genji = run.players[1]
        self.assertTrue(run.match.switch_hero(genji, heroes()[GENJI]))
        x, y, z = brig.mover.position
        genji.mover.position, genji.mover.body = [x, y, z + 3], None
        return run, brig, genji

    def vitals(self, run, owner):
        combat = run.match.combat
        return combat.vitals_of(next(t for t in combat._targets() if t.owner is owner))

    def phase(self, genji):
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        return instance.states[10]

    def test_front_flail_melee_and_whip_are_blocked_and_rear_contacts_land(self):
        for attack, amount in ((FIRE, 35), (0x800, 35), (8, 70)):
            for front in (True, False):
                with self.subTest(attack=attack, front=front):
                    run, brig, genji = self.scene()
                    hp = self.vitals(run, genji)
                    start = tuple(genji.mover.position)
                    yaw = -32768 if front else 0
                    run.step({genji: (yaw, 0, DEFLECT)})
                    self.assertTrue(self.phase(genji).active)
                    for frame in range(20 if attack == FIRE else 40):
                        run.step({brig: (0, 0, attack if frame == 0 or attack == FIRE else 0),
                                  genji: (yaw, 0, 0)})
                    self.assertTrue(self.phase(genji).active)
                    self.assertEqual(hp.total, 200 if front else 200 - amount)
                    if front:
                        self.assertEqual(tuple(genji.mover.position), start)
                        self.assertFalse(run.match.combat.brigitte.inspire)

    def test_bash_lands_through_active_deflect(self):
        run, brig, genji = self.scene()
        hp = self.vitals(run, genji)
        for frame in range(22):
            run.step({brig: (0, 0, SECONDARY), genji: (-32768, 0, DEFLECT if frame == 0 else 0)})
        self.assertTrue(self.phase(genji).active)
        run.step({brig: (0, 0, SECONDARY | FIRE), genji: (-32768, 0, 0)})
        run.step({brig: (0, 0, SECONDARY), genji: (-32768, 0, 0)})
        self.assertEqual(hp.total, 195)
        self.assertTrue(run.match.combat.is_stunned(genji))

    def test_deflect_expiry_reopens_front_flail_contact(self):
        run, brig, genji = self.scene()
        hp = self.vitals(run, genji)
        run.step({genji: (-32768, 0, DEFLECT)})
        self.assertTrue(self.phase(genji).active)
        for _ in range(160):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(self.phase(genji).active)
        brigitte_tests.BrigitteTests().swing(run, brig, 20)
        self.assertEqual(hp.total, 165)

    def test_rifle_reflection_reaches_brigitte_or_her_raised_barrier(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, brig, genji = self.scene(('Brigitte', 'Genji', 'Soldier'))
                soldier = run.players[2]
                x, y, z = genji.mover.position
                brig.mover.position, brig.mover.body = [x + 3, y, z - 3], None
                soldier.mover.position, soldier.mover.body = [x, y, z - 8], None
                for frame in range(23):
                    run.step({brig: (-8192, 0, SECONDARY if shield else 0),
                              genji: (24576, 0, DEFLECT if frame == 0 else 0)})
                self.assertTrue(self.phase(genji).active)
                run.step({brig: (-8192, 0, SECONDARY if shield else 0),
                          genji: (24576, 0, 0), soldier: (0, 0, FIRE)})
                self.assertEqual(self.vitals(run, genji).total, 200)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                if shield:
                    self.assertEqual(self.vitals(run, brig).total, 200)
                    self.assertEqual(run.match.combat.brigitte.barriers[brig.body].health, 230)
                else:
                    self.assertLess(self.vitals(run, brig).total, 200)
                    self.assertGreater(genji.stats.value(stats.HERO_DAMAGE), 0)

    def projectile_scene(self, front, shield, ana=False, retire=None, offset=0.0, wall_ahead=False):
        run, brig, genji = self.scene(('Brigitte', 'Genji', 'Launcher'))
        match = run.match
        match.game_map = match.game_map.__class__(**{**match.game_map.__dict__, 'free_for_all': False})
        launcher = run.players[2]
        ana_hero = next(h for h in heroes().values() if h.name == 'Ana')
        if ana:
            self.assertTrue(match.switch_hero(launcher, ana_hero))
        launcher.team, genji.team = brig.team, 1 - brig.team
        x, y, z = genji.mover.position
        destination = 4.06 if offset == 0.0 else 4.414 - 2 * offset
        brig.mover.position, brig.mover.body = [x + 3, y, z - destination], None
        launcher.mover.position, launcher.mover.body = [x + offset, y, z - 8], None
        if wall_ahead:
            from test_game_mover import box

            from ow174.game.collision import StaticCollisionProvider, World

            collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                              + box(x - 1, x + 1, y, y + 4, z - 4, z - 3.9))
            match.collision_provider = StaticCollisionProvider(collision)
            match.collision_world = collision
        brig_cmd = (-8192, 0, SECONDARY if shield else 0)
        yaw = 24576 if front else 0
        for frame in range(23):
            run.step({brig: brig_cmd, genji: (yaw, 0, DEFLECT if frame == 0 else 0)})
        self.assertTrue(self.phase(genji).active)
        if offset:
            target = next(t for t in match.combat._targets() if t.owner is genji)
            self.assertGreater(offset, target.radius)
        run.step({brig: brig_cmd, genji: (yaw, 0, 0), launcher: (0, 0, 16 if ana else SECONDARY)})
        fight = match.combat
        self.assertEqual(len(fight.rockets), 1)
        rocket = fight.rockets[0]
        shot, lifetime = rocket.shot, rocket.left
        saw_redirect = False
        for _ in range(55):
            run.step({brig: brig_cmd, genji: (yaw, 0, 0)})
            if rocket.reflected:
                self.assertIs(rocket.shooter, genji)
                self.assertEqual(rocket.team, genji.team)
                self.assertLess(rocket.left, lifetime)
                self.assertIsNone(rocket.shot)
                if not saw_redirect and retire is not None:
                    retiring = genji if retire == 'deflector' else launcher
                    self.assertTrue(match.switch_hero(retiring, ana_hero))
                    self.assertEqual(rocket in fight.rockets, retire == 'launcher')
                saw_redirect = True
            if rocket not in fight.rockets:
                break
            self.assertAlmostEqual(rocket.left + rocket.flown, lifetime, places=7)
        self.assertFalse(fight.rockets)
        self.assertEqual(saw_redirect, front and not wall_ahead)
        if not ana:
            self.assertIsNotNone(shot.release_at)
        return run, brig, genji, launcher

    def test_helix_reflection_hits_brigitte_or_shield_and_rear_hit_stays_hostile(self):
        for front, shield in ((True, False), (True, True), (False, False)):
            with self.subTest(front=front, shield=shield):
                run, brig, genji, launcher = self.projectile_scene(front, shield)
                self.assertEqual(self.vitals(run, genji).total, 200 if front else 80)
                self.assertEqual(self.vitals(run, brig).total, 90 if front and not shield else 200)
                self.assertEqual(launcher.stats.value(stats.HERO_DAMAGE), 0 if front else 120)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 110 if front and not shield else 0)
                if shield:
                    self.assertEqual(run.match.combat.brigitte.barriers[brig.body].health, 130)

    def test_reflected_ana_grenade_uses_deflector_team_for_damage_and_antiheal(self):
        run, brig, genji, launcher = self.projectile_scene(True, False, ana=True)
        hp = self.vitals(run, brig)
        self.assertEqual(self.vitals(run, genji).total, 200)
        self.assertEqual(hp.total, 145)
        self.assertEqual(hp.healing_factor(run.match.combat.now), 0.0)
        self.assertEqual(launcher.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 55)

    def test_reflected_flight_follows_deflector_body_retirement(self):
        for retire in ('launcher', 'deflector'):
            with self.subTest(retire=retire):
                run, brig, _genji, _launcher = self.projectile_scene(True, False, retire=retire)
                self.assertEqual(self.vitals(run, brig).total, 90 if retire == 'launcher' else 200)

    def test_helix_outside_body_capsule_still_hits_authored_box(self):
        run, brig, genji, _launcher = self.projectile_scene(True, False, offset=0.65)
        self.assertEqual(self.vitals(run, genji).total, 200)
        self.assertEqual(self.vitals(run, brig).total, 90)

    def test_world_contact_precedes_an_active_deflection_box(self):
        run, brig, genji, _launcher = self.projectile_scene(True, False, wall_ahead=True)
        self.assertEqual(self.vitals(run, genji).total, 200)
        self.assertEqual(self.vitals(run, brig).total, 200)
