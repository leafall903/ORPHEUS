"""Actual landed Molten Core across fixed and deployed barrier surfaces."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_defenses as defenses

from ow174.game import stats


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared Molten and projectile inputs")
class TorbjornMoltenCoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, hero):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, defender, victim = fixture.arena(hero, 15)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        defender.team = victim.team = 1 - torb.team
        torb.body_script.set_ult_charge(10000)
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, defender, 45)
        run.step({torb: (0, 4000, 1)})
        fight = run.match.combat
        for _ in range(60):
            run.step({})
            if fight.molten_pools:
                break
        pool, = fight.molten_pools.values()
        return fixture, run, torb, defender, victim, pool

    def assert_burn(self, fixture, run, torb, victim, pool):
        hp = fixture.health(run, victim)
        before = hp.total
        run.step({})
        self.assertIn(victim.body, pool.targets)
        self.assertIn((torb.entity, victim.body), run.match.combat.molten_burns)
        self.assertAlmostEqual(hp.total, before - pool.config["base_dps"] * .016, places=4)

    def test_winston_shell_stops_ground_path_in_both_directions_and_allied_shell_passes(self):
        for side in (-1, 1):
            with self.subTest(side=side):
                fixture, run, torb, winston, victim, pool = self.arena("Winston")
                x, y, z = pool.position
                fixture.place(winston, (x, y, z + 5 + side * .9))
                run.step({winston: (0, 0, 16)})
                for _ in range(45):
                    run.step({})
                shield, = run.match.combat.world_bubbles.actors.values()
                fixture.place(victim, (x, y, z + side * 1.8))
                hp = fixture.health(run, victim)
                before = hp.total
                for _ in range(8):
                    run.step({})
                    self.assertEqual(hp.total, before)
                    self.assertNotIn(victim.body, pool.targets)
                    self.assertNotIn((torb.entity, victim.body), run.match.combat.molten_burns)
                self.assertEqual(shield.health, 700)
                fixture.place(victim, (x, y, z - side * 1.1))
                self.assert_burn(fixture, run, torb, victim, pool)
                fixture.place(victim, (x, y, z + side * 1.8))
                run.step({})
                self.assertNotIn((torb.entity, victim.body), run.match.combat.molten_burns)
                # Change the firing alliance while leaving the same ground path in place.
                torb.team = winston.team
                victim.team = 1 - torb.team
                self.assert_burn(fixture, run, torb, victim, pool)
                self.assertEqual(shield.health, 700)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_COLLISION"), "needs Sigma geometry")
    def test_parked_sigma_mesh_retires_molten_burn_until_recalled_or_broken(self):
        for end in ("recall", "break"):
            with self.subTest(end=end):
                self.check_parked_sigma_mesh(end)

    def check_parked_sigma_mesh(self, end):
        fixture, run, torb, sigma, victim, pool = self.arena("Sigma")
        for _ in range(40):
            if pool.phase == 1:
                break
            run.step({})
        self.assertEqual(pool.phase, 1)
        x, y, z = pool.position
        fixture.place(sigma, (x, y, z + 4.0))
        fixture.place(victim, (x, y, z + 2.2))
        self.assert_burn(fixture, run, torb, victim, pool)
        fight = run.match.combat
        key = torb.entity, victim.body
        burn = fight.molten_burns[key]

        pose = {"defender_aim": (-32768, 0)}
        fixture.advance(run, torb, sigma, 15, defense=2, **pose)
        fixture.step(run, torb, sigma, **pose)
        shield = fight.sigma_barriers.actors[sigma.body]
        self.assertEqual(shield.mode, 3)
        self.assertTrue(shield.active())
        self.assertNotIn(key, fight.molten_burns, (pool.position, shield.position))
        self.assertNotIn(burn.instance, burn.script.component.instances)
        hp = fixture.health(run, victim)
        before = hp.total
        for _ in range(25):
            run.step({})
            self.assertEqual(hp.total, before)
            self.assertNotIn(victim.body, pool.targets)
        self.assertEqual(shield.health, 700)

        if end == "break":
            sigma_hp = fixture.health(run, sigma)
            sigma_before = sigma_hp.total
            credit = torb.stats.value(stats.HERO_DAMAGE)
            charge = torb.body_script.component.entity_var(342)
            charge_before = charge.value()
            yaw, pitch = fixture.body_aim(run, torb, sigma)
            run.step({torb: (yaw, pitch, 1)})
            for _ in range(40):
                run.step({})
                if shield.health < 700:
                    break
            self.assertEqual(shield.health, 675)
            self.assertEqual(sigma_hp.total, sigma_before)
            self.assertEqual(hp.total, before)
            self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), credit)
            self.assertEqual(charge.value(), charge_before)
            self.assertEqual(list(fight.molten_pools.values()), [pool])

            # Keep an actual Molten shot as the breaking hit.
            fight.sigma_barriers.damage(shield, 655)
            self.assertEqual(shield.health, 20)
            fixture.advance(run, torb, sigma, 30)
            run.step({torb: (yaw, pitch, 1)})
            for _ in range(40):
                run.step({})
                if not shield.active():
                    break
            self.assertEqual(shield.health, 0)
            self.assertEqual(shield.mode, 1)
            self.assertGreater(shield.source.vars[61].value(), 4.9)
            self.assertEqual(sigma_hp.total, sigma_before)
            self.assertIn(key, fight.molten_burns)
            self.assertAlmostEqual(before - hp.total, pool.config["base_dps"] * .016, places=4)
            self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, before - hp.total)
            self.assertEqual(charge.value(), charge_before)
        else:
            fixture.step(run, torb, sigma, defense=2, **pose)
        self.assertFalse(shield.active())
        self.assert_burn(fixture, run, torb, victim, pool)
        self.assertIsNot(fight.molten_burns[key], burn)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        if end == "break":
            fixture.place(victim, (x + 8, y, z + 2.2))
            fixture.advance(run, torb, sigma, 30)
            self.assertNotIn(key, fight.molten_burns)
            credit = torb.stats.value(stats.HERO_DAMAGE)
            run.step({torb: (yaw, pitch, 1)})
            for _ in range(40):
                run.step({})
                if sigma_hp.total < sigma_before:
                    break
            self.assertEqual(sigma_hp.total, sigma_before - 25)
            self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, 25)
            self.assertEqual(charge.value(), charge_before)
            self.assertEqual(list(fight.molten_pools.values()), [pool])

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "needs Reinhardt geometry")
    def test_reinhardt_shield_blocks_landed_molten_for_owner_and_ally(self):
        fixture, run, torb, rein, ally, pool = self.arena("Reinhardt")
        fight = run.match.combat
        for _ in range(40):
            if pool.phase == 1:
                break
            run.step({})
        self.assertEqual(pool.phase, 1)
        x, y, z = pool.position
        fixture.place(rein, (x, y, z + 1.55))
        fixture.place(ally, (x + .6, y, z + 2.25))
        recipients = (rein, ally)
        keys = [(torb.entity, target.body) for target in recipients]
        health = [fixture.health(run, target) for target in recipients]
        initial = [hp.total for hp in health]
        run.step({})
        self.assertTrue(all(hp.total < before for hp, before in zip(health, initial, strict=True)))
        burns = [fight.molten_burns[key] for key in keys]

        pose = {"defender_aim": (-32768, 4000)}
        fixture.advance(run, torb, rein, 15, defense=2, **pose)
        shield = fight.barriers.states[rein.body]
        self.assertTrue(shield.active)
        for key, burn in zip(keys, burns, strict=True):
            self.assertNotIn(key, fight.molten_burns)
            self.assertNotIn(burn.instance, burn.script.component.instances)
        self.assertEqual(shield.current, 1600)
        before_shot = [hp.total for hp in health]
        fixture.step(run, torb, rein, fire=1, defense=2, **pose)
        fixture.advance(run, torb, rein, 16, defense=2, **pose)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1575)
        self.assertEqual([hp.total for hp in health], before_shot)
        self.assertFalse(any(target.body in pool.targets for target in recipients))
        fixture.advance(run, torb, rein, 16, **pose)
        self.assertFalse(shield.active)
        self.assertTrue(all(hp.total < before for hp, before in zip(health, before_shot, strict=True)))
        for key, burn in zip(keys, burns, strict=True):
            self.assertIsNot(fight.molten_burns[key], burn)
        self.assertEqual(list(fight.molten_pools.values()), [pool])

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "needs Orisa geometry")
    def test_grounded_mesh_blocks_landed_pool_until_an_actual_globule_breaks_it(self):
        fixture, run, torb, orisa, victim, pool = self.arena("Orisa")
        x, y, z = pool.position
        fixture.place(orisa, (x, y, z + 1.3))
        run.step({orisa: (0, 16384, 16)})
        for _ in range(45):
            run.step({orisa: (0, 16384, 0)})
        fight = run.match.combat
        shield = fight.orisa_barriers.actors[orisa.body]
        fixture.place(orisa, (x + 7, y, z + 1.3))
        fixture.place(victim, (x, y, z + 2.2))
        hp = fixture.health(run, victim)
        before = hp.total
        for _ in range(8):
            run.step({})
            self.assertEqual(hp.total, before, (pool.position, shield.position, victim.mover.position))
            self.assertNotIn(victim.body, pool.targets)
        self.assertEqual(shield.health, 600)
        # Seed the breaking contact. The next actual globule takes the remaining pool.
        fight.orisa_barriers.damage(shield, 580)
        self.assertTrue(shield.active())
        yaw, pitch = fixture.body_aim(run, torb, victim)
        run.step({torb: (yaw, pitch, 1)})
        for _ in range(40):
            run.step({})
            if not shield.active():
                break
        self.assertFalse(shield.active())
        self.assertIn(victim.body, pool.targets)
        self.assertLess(hp.total, before)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
