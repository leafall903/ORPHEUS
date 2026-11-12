"""Real Zarya projected-barrier input against Brigitte's authored attacks."""

import unittest
from dataclasses import replace

import test_game_brigitte as brigitte_tests
import test_game_brigitte_defenses as defense_tests
import test_game_brigitte_healing_modifiers as healing_tests
import test_game_brigitte_shield as shield_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero


class ProjectedBarrierTests(unittest.TestCase):
    def scene(self, distance=3):
        run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Zarya", "Recipient"))
        zarya, ally = run.players[1:]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        brig.team, zarya.team, ally.team = 0, 1, 1
        x, y, z = brig.mover.position
        zarya.mover.position, zarya.mover.body = [x + 8, y, z + distance], None
        ally.mover.position, ally.mover.body = [x, y, z + distance], None
        for _ in range(25):
            run.step({})
        return run, brig, zarya, ally

    def cast(self, run, zarya, ally):
        fight = run.match.combat
        target = brigitte_tests.BrigitteTests().target(run, ally)
        x, y, z = target.feet
        direction = aim(fight._eye(zarya), (x, y + target.height / 2, z))
        run.step({zarya: (*direction, 16)})
        return fight.projected_barriers.get(ally.body)

    def test_actual_projection_absorbs_brigitte_damage_and_attached_effects(self):
        for kind, remaining in (("flail", 165), ("quick_melee", 165), ("whip", 130), ("bash", 195)):
            with self.subTest(kind=kind):
                run, brig, zarya, ally = self.scene()
                fight = run.match.combat
                vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, ally))
                before, position = vitals.total, tuple(ally.mover.position)
                barrier = self.cast(run, zarya, ally)
                self.assertIsNotNone(barrier)
                self.assertEqual((barrier.source, barrier.health), (zarya.body, 200))
                if kind == "bash":
                    shield_tests.BarrierTests().raise_shield(run, brig, 22)
                defense_tests.DefensiveControlTests().attack(run, brig, kind)
                self.assertEqual((vitals.total, barrier.health), (before, remaining))
                energy = zarya.body_script.component.entity_var(87, create=False)
                self.assertAlmostEqual(energy.value(), (200 - remaining) * 0.2)
                self.assertEqual(tuple(ally.mover.position), position)
                self.assertFalse(fight.is_stunned(ally))
                self.assertFalse(fight.brigitte.inspire)
                for _ in range(135):
                    run.step({})
                self.assertNotIn(ally.body, fight.projected_barriers)
                recovery_attack = "quick_melee" if kind == "quick_melee" else "flail"
                defense_tests.DefensiveControlTests().attack(run, brig, recovery_attack)
                self.assertEqual(vitals.total, before - 35)

    def test_actual_whip_observes_projection_start_and_expiry_during_flight(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                run, brig, zarya, ally = self.scene(distance=18)
                fight = run.match.combat
                vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, ally))
                before, start = vitals.total, tuple(ally.mover.position)
                if expires:
                    barrier = self.cast(run, zarya, ally)
                    self.assertIsNotNone(barrier)
                    for _ in range(150):
                        if fight.now >= barrier.until - 0.30:
                            break
                        run.step({})
                brigitte_tests.BrigitteTests().whip(run, brig, 14)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                self.assertEqual(fight._active_projected_barrier(ally.body) is not None, expires)
                if expires:
                    for _ in range(10):
                        if fight._active_projected_barrier(ally.body) is None:
                            break
                        run.step({})
                    self.assertIsNone(fight._active_projected_barrier(ally.body))
                else:
                    barrier = self.cast(run, zarya, ally)
                    self.assertIsNotNone(barrier)
                self.assertEqual(barrier.health, 200)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.brigitte.whips)
                if expires:
                    self.assertEqual(vitals.total, before - 70)
                    self.assertGreater(ally.mover.position[2], start[2] + 2)
                    self.assertEqual(barrier.health, 200)
                    self.assertIn((brig.body, brig.body), fight.brigitte.inspire)
                else:
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(tuple(ally.mover.position), start)
                    self.assertEqual(barrier.health, 130)
                    self.assertFalse(fight.brigitte.inspire)

    def test_actual_projection_rejects_enemy_ffa_wall_and_range(self):
        for reason in ("enemy", "ffa", "wall", "range"):
            with self.subTest(reason=reason):
                run, brig, zarya, ally = self.scene()
                if reason == "enemy":
                    ally.team = brig.team
                elif reason == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                elif reason == "range":
                    x, y, z = ally.mover.position
                    ally.mover.position, ally.mover.body = [x - 31, y, z], None
                    run.step({})
                else:
                    x, y, z = ally.mover.position
                    collision = World(box(x - 10, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x + 2, x + 2.1, y, y + 4, z - 1, z + 1))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                self.assertIsNone(self.cast(run, zarya, ally))

    def test_issued_barrier_remains_on_recipient_when_caster_replaces_body(self):
        run, brig, zarya, ally = self.scene()
        fight = run.match.combat
        barrier = self.cast(run, zarya, ally)
        self.assertIsNotNone(barrier)
        source = zarya.body
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        self.assertNotEqual(zarya.body, source)
        defense_tests.DefensiveControlTests().attack(run, brig, "flail")
        self.assertEqual((barrier.source, barrier.health), (source, 165))
        energy = zarya.body_script.component.entity_var(87, create=False)
        self.assertFalse(energy is not None and energy.value())
        old = ally.body
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        self.assertNotIn(old, fight.projected_barriers)
        self.assertNotIn(ally.body, fight.projected_barriers)

    def test_actual_helix_breaks_either_particle_barrier_without_same_rocket_spill(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, shooter, caster, ally = self.scene(6)
                fight = run.match.combat
                bystander = None if projected else caster
                position = list(shooter.mover.position)
                self.assertTrue(run.match.switch_hero(shooter, find_hero("soldier")))
                shooter.mover.position, shooter.mover.body = position, None
                if projected:
                    barrier = self.cast(run, caster, ally)
                    self.assertIsNotNone(barrier)
                else:
                    position = list(ally.mover.position)
                    self.assertTrue(run.match.switch_hero(ally, find_hero("zarya")))
                    ally.mover.position, ally.mover.body = position, None
                    caster = ally
                    run.step({ally: (-32768, 0, 8)})
                target = brigitte_tests.BrigitteTests().target(run, ally)
                vitals = fight.vitals_of(target)
                initial = vitals.total
                x, y, z = target.feet
                direction = aim(fight._eye(shooter), (x, y + target.height / 2, z))
                for _ in range(105):
                    run.step({shooter: (*direction, FIRE)})
                    pool = fight.personal_barriers.get(ally.body, (0, 0, 200))
                    left = barrier.health if projected else pool[2]
                    if 0 < left <= 40:
                        break
                else:
                    self.fail("actual rifle did not chip the barrier into Helix direct-damage range")
                self.assertEqual(vitals.total, initial)
                if bystander is not None:
                    bystander.mover.position, bystander.mover.body = [x + 1.5, y, z], None
                    neighbour = brigitte_tests.BrigitteTests().target(run, bystander)
                    neighbour_hp = fight.vitals_of(neighbour)
                    neighbour_before = neighbour_hp.total
                run.step({shooter: (*direction, SECONDARY)})
                for _ in range(25):
                    run.step({shooter: (*direction, 0)})
                self.assertEqual(vitals.total, initial)
                if bystander is not None:
                    self.assertLess(neighbour_hp.total, neighbour_before)
                if projected:
                    self.assertEqual(barrier.health, 0)
                else:
                    self.assertNotIn(ally.body, fight.personal_barriers)
                energy = caster.body_script.component.entity_var(87, create=False)
                self.assertAlmostEqual(energy.value(), 40, delta=0.1)
                for _ in range(15):
                    run.step({shooter: (*direction, FIRE)})
                self.assertLess(vitals.total, initial)

    def test_projection_cleanses_ana_grenade_and_actual_repair_pack_heals(self):
        fixture = healing_tests.HealingModifierTests()
        run, brig, ana, ally, hp = fixture.scene(False, initial_health=300)
        zarya = run.players[3]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        zarya.team = ally.team
        x, y, z = ally.mover.position
        zarya.mover.position, zarya.mover.body = [x - 6, y, z], None
        expiry = fixture.grenade(run, ana, hp, False)
        fight = run.match.combat
        self.assertEqual(hp.healing_factor(fight.now), 0)
        barrier = self.cast(run, zarya, ally)
        self.assertIsNotNone(barrier)
        self.assertEqual(hp.healing_factor(fight.now), 1)
        before = hp.total
        fixture.pack(run, brig)
        self.assertLess(fight.now, expiry)
        self.assertAlmostEqual(hp.total - before, 110)
        self.assertEqual(barrier.health, 200)

    def test_personal_and_projected_pools_do_not_stack_in_either_cast_order(self):
        for projected_first in (False, True):
            with self.subTest(projected_first=projected_first):
                run, shooter, caster, ally = self.scene(6)
                for actor, hero in ((shooter, "soldier"), (ally, "zarya")):
                    position = list(actor.mover.position)
                    self.assertTrue(run.match.switch_hero(actor, find_hero(hero)))
                    actor.mover.position, actor.mover.body = position, None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                if projected_first:
                    barrier = self.cast(run, caster, ally)
                    self.assertIsNotNone(barrier)
                    run.step({ally: (-32768, 0, 8)})
                    self.assertIsNone(fight._personal_barrier_pet(ally))
                    self.assertNotIn(ally.body, fight.personal_barriers)
                else:
                    run.step({ally: (-32768, 0, 8)})
                    self.assertIsNotNone(fight._personal_barrier_pet(ally))
                    self.assertIsNone(self.cast(run, caster, ally))
                target = brigitte_tests.BrigitteTests().target(run, ally)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                direction = aim(fight._eye(shooter), (x, y + target.height / 2, z))
                for _ in range(120):
                    run.step({shooter: (*direction, FIRE)})
                    if vitals.total < before:
                        break
                self.assertEqual(vitals.total, before - 20)
                if projected_first:
                    self.assertIsNone(fight._personal_barrier_pet(ally))
                    self.assertEqual(barrier.health, 0)
                for actor, expected in ((caster, 40 if projected_first else 0),
                                        (ally, 0 if projected_first else 40)):
                    energy = actor.body_script.component.entity_var(87, create=False)
                    self.assertAlmostEqual((energy.value() if energy else None) or 0, expected, delta=0.1)
                if projected_first:
                    run.step({ally: (-32768, 0, 8)})
                    self.assertIsNotNone(fight._personal_barrier_pet(ally))
