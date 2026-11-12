"""Build 104319 Barrier Field against actual Ashe combat input."""

import os
import unittest
from pathlib import Path

import test_ashe_bob as bob_fixtures
import test_ashe_combat as fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, sombra_hack
from ow174.game.content import HEROES_PATH, find_hero
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(
    HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file() and os.environ.get("ORPHEUS_REINHARDT_COLLISION"),
    "requires build 104319 Barrier Field mesh",
)
class AsheReinhardtBarrierTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    raised = (-32768, 0, 2)

    def raise_field(self, run, rein, z):
        self.assertTrue(run.match.switch_hero(rein, find_hero("reinhardt")))
        rein.mover.position[:] = (0, 1, z)
        rein.mover.body = None
        for _ in range(35):
            run.step({rein: self.raised})
        fight = run.match.combat
        shield = fight.reinhardt_barriers.states[rein.body]
        self.assertTrue(shield.active)
        self.assertEqual(shield.maximum, 1600)
        target = next(item for item in fight._targets() if item.owner is rein)
        return shield, fight.vitals_of(target)

    def test_viper_and_coach_hit_raised_field_and_viper_hits_body_after_lowering(self):
        run, ashe, rein = self.fight(distance=5)
        shield, vitals = self.raise_field(run, rein, 5)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ashe), fight._eye(rein))
        run.step({ashe: (yaw, pitch, 1), rein: self.raised})
        self.assertEqual(shield.current, 1560)
        for _ in range(50):
            run.step({ashe: (yaw, pitch, 2), rein: self.raised})
        run.step({ashe: (yaw, pitch, 3), rein: self.raised})
        self.assertEqual(shield.current, 1485)
        for _ in range(50):
            run.step({rein: self.raised})
        ashe.mover.position[:] = (0, 1, 2)
        ashe.mover.body = None
        yaw, pitch = aim(fight._eye(ashe), fight._eye(rein))
        run.step({ashe: (yaw, pitch, 8), rein: self.raised})
        for _ in range(20):
            run.step({rein: self.raised})
        self.assertEqual(shield.current, 1395)
        self.assertEqual(vitals.total, 500)
        self.assertFalse(ashe.client.messages(combat.HIT))

        for _ in range(30):
            run.step({})
        self.assertFalse(shield.active)
        run.step({ashe: (yaw, pitch, 1)})
        self.assertLess(vitals.total, 500)
        self.assertEqual(shield.current, 1395)

    def test_real_ads_breaks_full_pool_and_cannot_bypass_rearm_delay(self):
        run, ashe, rein = self.fight(distance=5)
        shield, vitals = self.raise_field(run, rein, 5)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ashe), fight._eye(rein))
        weapon = next(i for i in ashe.body_script.component.instances.values() if i.graph.index == 0x14FC)

        def fire_until(count):
            for ammo in range(int(weapon.vars[53].value()), count, -1):
                run.step({ashe: (yaw, pitch, 3), rein: self.raised})
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                if ammo - 1 > count:
                    for _ in range(48):
                        run.step({ashe: (yaw, pitch, 2), rein: self.raised})

        for _ in range(50):
            run.step({ashe: (yaw, pitch, 2), rein: self.raised})
        fire_until(0)
        self.assertEqual(shield.current, 700)
        run.step({rein: self.raised})
        for _ in range(300):
            run.step({ashe: (yaw, pitch, 0x400), rein: self.raised})
            if weapon.vars[53].value() == 12:
                break
        self.assertEqual(weapon.vars[53].value(), 12)
        for _ in range(50):
            run.step({ashe: (yaw, pitch, 2), rein: self.raised})
        fire_until(2)
        self.assertEqual(shield.current, 0)
        self.assertEqual(vitals.total, 500)
        self.assertFalse(shield.active)
        self.assertAlmostEqual(shield.broken_until - fight.now, 5)
        self.assertAlmostEqual(shield.regen_after - fight.now, 2)
        while fight.now < shield.regen_after:
            run.step({rein: self.raised if run.frame % 2 else (-32768, 0, 0)})
            self.assertFalse(shield.active)
        for _ in range(25):
            run.step({rein: self.raised})
        self.assertGreater(shield.current, 0)
        self.assertAlmostEqual(shield.current, 200 * (fight.now - shield.regen_after), places=3)
        self.assertFalse(shield.active)
        deadline = shield.broken_until
        while fight.now < deadline + .032:
            run.step({})
        for _ in range(12):
            run.step({rein: self.raised})
        self.assertTrue(shield.active)

    def test_natural_dynamite_hits_raised_mesh_without_body_damage_or_burn(self):
        run, ashe, rein = self.fight(distance=5)
        shield, vitals = self.raise_field(run, rein, 5)
        fight = run.match.combat
        for i in range(20):
            run.step({ashe: (0, 13000, 16 if i == 0 else 0), rein: self.raised})
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.fuse - 1:
            run.step({rein: self.raised})
        self.assertEqual(shield.current, 1600)
        run.step({rein: self.raised})
        self.assertEqual(shield.current, 1550)
        self.assertEqual(vitals.total, 500)
        self.assertNotIn((ashe.body, rein.body), fight.dynamite.burns)

    def test_melee_hits_visible_lateral_mesh_and_rejects_terrain(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                run, ashe, rein = self.fight(distance=5)
                shield, vitals = self.raise_field(run, rein, 5)
                ashe.mover.position[:] = (-1.8, 1, 2.3)
                ashe.mover.body = None
                if covered:
                    run.match.collision_world = wall_scene(2.8)
                for _ in range(10):
                    run.step({rein: self.raised})
                run.step({ashe: (0, 0, 0x800), rein: self.raised})
                for _ in range(30):
                    run.step({rein: self.raised})
                self.assertEqual(shield.current, 1600 if covered else 1570)
                self.assertEqual(vitals.total, 500)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_bob_gun_hits_raised_field_from_outside(self):
        run, _ashe, rein, actor = bob_fixtures.BobTests().cast(distance=9)
        shield, vitals = self.raise_field(run, rein, 8)
        health = vitals.total
        initial_pool = shield.current
        shots = actor.shots
        for _ in range(24):
            run.step({rein: self.raised})
        self.assertGreater(actor.shots, shots)
        self.assertEqual(shield.current, initial_pool - (actor.shots - shots) * 14)
        self.assertEqual(vitals.total, health)

    def test_rear_viper_fire_reaches_body_while_barrier_stays_raised(self):
        run, ashe, rein = self.fight(distance=5)
        shield, vitals = self.raise_field(run, rein, 5)
        ashe.mover.position[:] = (0, 1, 8)
        ashe.mover.body = None
        for _ in range(10):
            run.step({rein: self.raised})
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ashe), fight._eye(rein))
        run.step({ashe: (yaw, pitch, 1), rein: self.raised})
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertLess(vitals.total, 500)

    def sombra_scene(self, distance, *, allied=False):
        run, sombra, rein = self.fight(distance=distance, team_game=allied)
        self.assertTrue(run.match.switch_hero(sombra, find_hero("sombra")))
        sombra.mover.position[:] = (0, 1, 0)
        sombra.mover.body = None
        if allied:
            rein.team = sombra.team
        shield, vitals = self.raise_field(run, rein, distance)
        return run, sombra, rein, shield, vitals

    def test_emp_routes_field_and_body_independently(self):
        for case, distance, broken, locked in (
            ("both", 5, True, True),
            ("field_only", 16, True, False),
            ("body_only", 14.7, False, True),
            ("range", 20, False, False),
            ("terrain", 5, False, False),
            ("ally", 5, False, False),
        ):
            with self.subTest(case=case):
                run, sombra, rein, shield, vitals = self.sombra_scene(distance, allied=case == "ally")
                raised = (0, 0, 2) if case == "body_only" else self.raised
                for _ in range(6):
                    run.step({rein: raised})
                original = shield.entity
                self.assertNotEqual(original, rein.body)
                if case == "terrain":
                    run.match.collision_world = wall_scene(1.5)
                script = sombra.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                for frame in range(22):
                    run.step({sombra: (0, 0, 32 if frame == 0 else 0), rein: raised})
                run.step({rein: raised})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(sombra_hack.locked(rein), locked)
                self.assertEqual(vitals.total, 500)
                self.assertEqual(shield.current, 0 if broken else 1600)
                self.assertEqual(shield.active, not (broken or locked))
                self.assertEqual(shield.entity, 0 if broken or locked else original)
                self.assertEqual(shield.broken_until > run.match.combat.now, broken)

    def test_ordinary_hack_rejects_frontal_field_then_lowers_it_from_rear_without_break(self):
        run, sombra, rein, shield, vitals = self.sombra_scene(5)
        fight = run.match.combat
        original = shield.entity
        front = aim(fight._eye(sombra), fight._eye(rein))
        for _ in range(65):
            run.step({sombra: (*front, 2), rein: self.raised})
        self.assertFalse(sombra_hack.locked(rein))
        self.assertTrue(shield.active)
        self.assertEqual(shield.entity, original)

        sombra.mover.position[:] = (0, 1, 8)
        sombra.mover.body = None
        for _ in range(10):
            run.step({rein: self.raised})
        rear = aim(fight._eye(sombra), fight._eye(rein))
        for _ in range(65):
            run.step({sombra: (*rear, 2), rein: self.raised})
            if sombra_hack.locked(rein):
                break
        effect = sombra_hack.instance_for(rein, sombra_hack.HACK)
        self.assertIsNotNone(effect)
        wait = effect.states[58]
        run.step({rein: self.raised})
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertEqual(shield.broken_until, 0)
        self.assertEqual(vitals.total, 500)
        deadline = (wait.start + wait.ms) // rein.body_script.frame_ms
        while run.frame < deadline + 1:
            run.step({rein: self.raised if run.frame % 2 else (-32768, 0, 0)})
            if run.frame < deadline:
                self.assertFalse(shield.active)
        run.step({})
        for _ in range(10):
            run.step({rein: self.raised})
        self.assertFalse(sombra_hack.locked(rein))
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertNotEqual(shield.entity, original)
