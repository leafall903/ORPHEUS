"""Actual Genji Deflect State versus Ana; reconstructed body collision only."""
import unittest

import test_game_ana as ana_fixture
from test_game_combat import aim, in_world

from ow174.game import projectiles, stats
from ow174.game.content import SOLDIER, find_hero
from ow174.game.world import OP_DESTROY


class AnaDeflectTests(unittest.TestCase):
    def fight(self, *, facing=True, ally=False, distance=6):
        run, ana, genji, old_entry = ana_fixture.AnaRifleTests().setup_fight(
            enemy=not ally, distance=distance)
        self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
        genji.mover.position = list(old_entry.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is genji)
        own = next(t for t in fight._targets() if t.owner is ana)
        forward = aim(fight._eye(genji), (own.feet[0], own.feet[1] + 1, own.feet[2]))
        if not facing:
            forward = (0, 0)
        run.step({genji: (*forward, 0x10)})
        state = next(i.states.get(10) for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        self.assertTrue(state.active)
        return run, ana, genji, target, own, forward

    def attack(self, run, ana, genji, target, forward, button, *, scoped=False):
        direction = aim(run.match.combat._eye(ana),
                        (target.feet[0], target.feet[1] + 1, target.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2), genji: (*forward, 0)})
        run.step({ana: (*direction, button | (2 if scoped else 0)), genji: (*forward, 0)})
        for _ in range(70):
            run.step({genji: (*forward, 0)})

    def test_both_rifle_modes_return_damage_and_credit_to_genji(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, ana, genji, target, own, forward = self.fight()
                self.attack(run, ana, genji, target, forward, 1, scoped=scoped)
                fight = run.match.combat
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertEqual(fight.vitals_of(own).total, 130)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_sleep_returns_five_damage_and_sleep_to_ana(self):
        run, ana, genji, target, own, forward = self.fight()
        self.attack(run, ana, genji, target, forward, 8)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(own).total, 195)
        self.assertTrue(fight.is_asleep(ana))
        self.assertFalse(fight.is_asleep(genji))
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_reflected_grenade_damages_and_antiheals_ana(self):
        run, ana, genji, target, own, forward = self.fight()
        self.attack(run, ana, genji, target, forward, 0x10)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(own).total, 150)
        self.assertGreater(fight.vitals_of(own).healing_block_until, fight.now)
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_back_shots_and_expired_deflect_damage_genji(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                run, ana, genji, target, own, forward = self.fight(facing=expired)
                if expired:
                    for _ in range(160):
                        run.step({genji: (*forward, 0)})
                self.attack(run, ana, genji, target, forward, 1)
                self.assertEqual(run.match.combat.vitals_of(target).total, 130)
                self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_allied_rifle_healing_is_not_reflected(self):
        run, ana, genji, target, own, forward = self.fight(ally=True)
        fight = run.match.combat
        fight.vitals_of(target).damage(100)
        self.attack(run, ana, genji, target, forward, 1)
        self.assertEqual(fight.vitals_of(target).total, 170)
        self.assertEqual(fight.vitals_of(own).total, 200)
        self.assertEqual(ana.stats.value(stats.HEALING), 70)

    def test_deflect_blocks_front_melee_without_return_damage(self):
        run, ana, genji, target, own, forward = self.fight(distance=1.5)
        self.attack(run, ana, genji, target, forward, 0x800)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_static_wall_blocks_a_scoped_shot_before_deflection(self):
        run, ana, genji, target, own, forward = self.fight()
        ana_fixture.set_static_wall(run, ana, 3)
        self.attack(run, ana, genji, target, forward, 1, scoped=True)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(run.match.combat.vitals_of(own).total, 200)

    def test_existing_rifle_dose_is_not_removed_by_later_deflect(self):
        run, ana, genji, old_entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True)
        self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
        genji.mover.position = list(old_entry.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is genji)
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        forward = aim(fight._eye(genji), (ana.mover.position[0], ana.mover.position[1] + 1,
                                         ana.mover.position[2]))
        run.step({ana: (*direction, 1)})
        for _ in range(10):
            run.step({})
        self.assertTrue(fight.timed_hits)
        self.assertLess(fight.vitals_of(target).total, 200)
        run.step({genji: (*forward, 0x10)})
        for _ in range(60):
            run.step({genji: (*forward, 0)})
        self.assertEqual(fight.vitals_of(target).total, 130)

    def test_allied_grenade_heals_and_boosts_deflecting_genji(self):
        run, ana, genji, target, _own, forward = self.fight(ally=True)
        fight = run.match.combat
        fight.vitals_of(target).damage(100)
        self.attack(run, ana, genji, target, forward, 0x10)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertGreater(fight.vitals_of(target).healing_boost_until, fight.now)
        self.assertEqual(ana.stats.value(stats.HEALING), 100)

    def test_grenade_ground_splash_is_not_reflected(self):
        run, ana, genji, target, _own, forward = self.fight()
        fight = run.match.combat
        direction = aim(fight._eye(ana),
                        (target.feet[0], target.feet[1], target.feet[2] - 0.5))
        run.step({ana: (*direction, 0x10), genji: (*forward, 0)})
        for _ in range(70):
            run.step({genji: (*forward, 0)})
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertGreater(fight.vitals_of(target).healing_block_until, fight.now)

    def test_reflected_hip_flight_survives_either_body_replacement(self):
        for replace_shooter in (False, True):
            with self.subTest(replace_shooter=replace_shooter):
                run, ana, genji, target, _own, forward = self.fight(distance=18)
                fight = run.match.combat
                direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                run.step({ana: (*direction, 1), genji: (*forward, 0)})
                for _ in range(30):
                    if fight.rockets:
                        break
                    run.step({genji: (*forward, 0)})
                rocket, = fight.rockets
                shot = rocket.shot
                self.assertIsNotNone(shot)
                original_body, reflected_body = ana.body, genji.body
                self.assertEqual((rocket.source_entity, shot.body), (original_body, original_body))
                copy = fight.projectiles.lease_id(ana, rocket.definition, shot.frame)
                self.assertIsNotNone(copy)
                shot.created[copy] = projectiles._ProjectileArrival(received=True)
                ana.client.entities.clear()

                for _ in range(30):
                    run.step({genji: (*forward, 0)})
                    if rocket.source_entity == reflected_body:
                        break
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(rocket.source_entity, reflected_body)
                self.assertIsNone(rocket.shot)
                self.assertIsNotNone(shot.release_at)

                replaced = ana if replace_shooter else genji
                old_body = replaced.body
                position = list(replaced.mover.position)
                self.assertTrue(run.match.switch_hero(replaced, replaced.hero))
                self.assertNotEqual(replaced.body, old_body)
                replaced.mover.position = position
                own = next(t for t in fight._targets() if t.owner is ana)
                vitals = fight.vitals_of(own)
                vitals.damage(140)
                for _ in range(90):
                    run.step({})

                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(shot, fight.projectiles.shots)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, reflected_body)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                destroy, = [row for row in ana.client.entities if row.entity == copy and row.op == OP_DESTROY]
                self.assertEqual(destroy.ch2 is None, replace_shooter)

    def test_reflected_dart_killer_keeps_retired_genji_body_identity(self):
        run, ana, genji, target, own, forward = self.fight()
        fight = run.match.combat
        fight.vitals_of(own).damage(195)
        direction = aim(fight._eye(ana),
                        (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, 8), genji: (*forward, 0)})
        old_body = genji.body
        for _ in range(50):
            run.step({genji: (*forward, 0)})
            if any(r.source_entity == old_body for r in fight.rockets):
                break
        self.assertTrue(any(r.source_entity == old_body for r in fight.rockets))
        self.assertTrue(run.match.switch_hero(genji, find_hero("Soldier")))
        for _ in range(30):
            run.step({})
        self.assertTrue(fight.vitals_of(own).dead)
        self.assertEqual(fight.vitals_of(own).killer, old_body)

    def test_detached_grenade_reflects_after_ana_replacement_and_survives_genji_retirement(self):
        for genji_dies in (False, True):
            with self.subTest(genji_dies=genji_dies):
                run, ana, genji, target, _, forward = self.fight(distance=10)
                fight = run.match.combat
                ally = run.match.add_player(3, "Genji ally", SOLDIER, genji.team, False)
                in_world(run.match, ally)
                run.players.append(ally)
                self.assertTrue(run.match.switch_hero(ally, ally.hero))
                x, y, z = ana.mover.position
                ally.spawn = (x + 1.5, y, z), ally.spawn[1]
                ally.mover = ally.new_mover()
                ally_target = next(item for item in fight._targets() if item.owner is ally)
                ally_health = fight.vitals_of(ally_target)
                ally_health.damage(125)
                x, y, z = target.feet
                direction = ana_fixture.grenade_aim(run, ana, (x, y + 1, z))
                run.step({ana: (*direction, 16), genji: (*forward, 0)})
                for _ in range(35):
                    if fight.rockets:
                        break
                    run.step({genji: (*forward, 0)})
                rocket, = fight.rockets
                original_ana, original_genji = ana.body, genji.body
                self.assertEqual(rocket.source_entity, original_ana)
                self.assertIsNone(rocket.shot)

                position = tuple(ana.mover.position)
                self.assertTrue(run.match.switch_hero(ana, ana.hero))
                self.assertNotEqual(ana.body, original_ana)
                ana.spawn = position, ana.spawn[1]
                ana.mover = ana.new_mover()
                self.assertIn(rocket, fight.rockets)
                for _ in range(50):
                    if rocket.source_entity == original_genji:
                        break
                    run.step({genji: (*forward, 0)})
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(rocket.source_entity, original_genji)
                self.assertEqual(rocket.team, genji.team)

                own = next(item for item in fight._targets() if item.owner is ana)
                vitals = fight.vitals_of(own)
                if genji_dies:
                    vitals.damage(160)
                    fight._damage(ana, target, 1000, False, None, run.frame)
                    self.assertTrue(fight.is_dead(genji))
                else:
                    self.assertTrue(run.match.switch_hero(genji, genji.hero))
                    self.assertNotEqual(genji.body, original_genji)
                self.assertIn(rocket, fight.rockets)
                for _ in range(70):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(rocket.source_entity, original_genji)
                self.assertEqual(ally_health.total, 175)
                self.assertGreater(ally_health.healing_boost_until, fight.now)
                self.assertEqual(ally_health.healing_block_until, 0)
                self.assertEqual(genji.stats.value(stats.HEALING), 100)
                if genji_dies:
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, original_genji)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 40)
                else:
                    self.assertEqual(vitals.total, 150)
                    self.assertGreater(vitals.healing_block_until, fight.now)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 50)
