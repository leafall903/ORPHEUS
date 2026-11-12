"""Offset defender controls distinguish ability interception from body occlusion."""

import unittest

import test_soldier_pve_absorb as guard
import test_soldier_pve_static as fixture
from test_game_combat import aim

from ow174.game import combat, npc_control, stats


class SoldierPveAbsorbOffsetTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    pool = guard.SoldierPveAbsorbTests.pool
    instance = guard.SoldierPveAbsorbTests.instance
    state = guard.SoldierPveAbsorbTests.state
    activate = guard.SoldierPveAbsorbTests.activate

    def arena(self, key, *, incoming=False):
        self.incoming, self.actor = incoming, None
        run, soldier, sigma, actor = guard.SoldierPveAbsorbTests.arena(self, key,
                                                                    incoming=incoming, activate=False)
        self.actor = actor
        sigma.mover.position = [1, 0, 3.5] if incoming else [.8, 0, 1]
        sigma.mover.body = None
        return run, soldier, sigma, actor

    def angles(self, run, soldier):
        if self.incoming or self.actor is None:
            return 0, 0
        x, y, z = self.actor.position
        return aim(run.match.combat._eye(soldier), (x, y + .8, z))

    def step(self, run, soldier, sigma, buttons=0, *, sigma_buttons=0):
        run.step({soldier: (*self.angles(run, soldier), buttons),
                  sigma: (self.facing, 0, sigma_buttons)})

    def test_visor_hits_all_five_npcs_after_grasp_expires_in_same_activation(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, sigma, actor = self.arena(key)
                script = soldier.body_script
                script.set_ult_charge(script.component.vars[338].value())
                self.step(run, soldier, sigma, 0x20)
                for _ in range(80):
                    self.step(run, soldier, sigma)
                self.activate(run, soldier, sigma)
                before = self.pool(run, actor).total
                self.step(run, soldier, sigma, 1)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 20)
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(160):
                    self.step(run, soldier, sigma)
                    if not self.state(sigma).active:
                        break
                self.assertFalse(self.state(sigma).active)
                self.step(run, soldier, sigma, 1)
                self.assertEqual(self.pool(run, actor).total, before - 20)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 20)
                self.assertEqual(script.component.vars[342].value(), 0)

    def test_all_five_npc_protection_requires_active_enemy_grasp_while_body_ray_misses(self):
        for key, definition in self.catalog.items():
            for weapon, amount in (("rifle", 20), ("helix", 120), ("visor", 20)):
                for mode in ("active_enemy", "inactive_enemy", "active_ally"):
                    with self.subTest(npc=definition.name, weapon=weapon, mode=mode):
                        run, soldier, sigma, actor = self.arena(key)
                        if mode == "active_ally":
                            sigma.team = soldier.team
                        if weapon == "visor":
                            soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                            self.step(run, soldier, sigma, 0x20)
                            for _ in range(80):
                                self.step(run, soldier, sigma)
                        if mode != "inactive_enemy":
                            self.activate(run, soldier, sigma)
                        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
                        a, b = target.segment()
                        self.assertIsNone(combat.ray_capsule(run.match.combat._eye(soldier),
                                                           combat.aim(*self.angles(run, soldier)),
                                                           a, b, target.radius))
                        before = self.pool(run, actor).total
                        self.step(run, soldier, sigma, 2 if weapon == "helix" else 1)
                        for _ in range(25):
                            self.step(run, soldier, sigma)
                        protected = mode == "active_enemy"
                        self.assertEqual(before - self.pool(run, actor).total, 0 if protected else amount)
                        summed = self.instance(sigma).find_var(0, 21312).value()
                        self.assertEqual(summed,
                                         amount if protected else None if mode == "inactive_enemy" else 0)
                        if protected:
                            self.assertEqual(self.pool(run, sigma).total, 400)
                            self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                            self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                        else:
                            self.assertGreaterEqual(soldier.stats.value(stats.HERO_DAMAGE), amount)

    def test_four_actual_npc_weapons_hit_soldier_when_offset_grasp_is_off(self):
        for key, amount in (("1", 20), ("2", 50), ("3", 100), ("5", 5)):
            for active in (True, False):
                with self.subTest(npc=self.catalog[key].name, active=active):
                    run, soldier, sigma, actor = self.arena(key, incoming=True)
                    if active and key != "3":
                        self.activate(run, soldier, sigma)
                    actor.attack_target, actor.firing = soldier.entity, True
                    if key == "3":
                        for _ in range(20):
                            self.step(run, soldier, sigma)
                        if active:
                            self.activate(run, soldier, sigma)
                    for _ in range(140):
                        self.step(run, soldier, sigma)
                        if ((self.pool(run, soldier).total < 200
                                or self.instance(sigma).find_var(0, 21312).value())
                                and not any(r.shooter is actor for r in run.match.combat.rockets)):
                            break
                    actor.firing = False
                    exposed = 28 if key == "2" else amount
                    self.assertEqual(self.pool(run, soldier).total, 200 if active else 200 - exposed)
                    self.assertEqual(self.pool(run, sigma).total, 400)
                    self.assertEqual(self.instance(sigma).find_var(0, 21312).value(),
                                     amount if active else None)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_same_npc_attack_order_hits_soldier_after_offset_grasp_expires(self):
        for key, absorbed, damage in (("1", 20, 20), ("2", 50, 28), ("3", 100, 100), ("3", 5, 5), ("5", 5, 5)):
            with self.subTest(npc=self.catalog[key].name, absorbed=absorbed):
                run, soldier, sigma, actor = self.arena(key, incoming=True)
                charged_sniper = key == "3" and absorbed == 100
                if not charged_sniper:
                    self.activate(run, soldier, sigma)
                    if key == "3":
                        actor.body_script.component.edges([(16, True), (16, False)])
                actor.attack_target, actor.firing = soldier.entity, True
                if charged_sniper:
                    for _ in range(20):
                        self.step(run, soldier, sigma)
                    self.activate(run, soldier, sigma)
                grasp = self.state(sigma)
                absorb_sum = self.instance(sigma).find_var(0, 21312)
                soldier_pool = self.pool(run, soldier)
                self.assertEqual(soldier_pool.total, 200)
                first_absorbed = absorb_sum.value() or None
                for _ in range(350):
                    # Tick the world and NPC controller without any player commands.
                    run.frame += 1
                    run.match.update(run.frame * .016, run.frame)
                    if grasp.active:
                        self.assertEqual(soldier_pool.total, 200)
                        if first_absorbed is None and absorb_sum.value():
                            first_absorbed = absorb_sum.value()
                    elif soldier_pool.total < 200:
                        break
                self.assertFalse(grasp.active)
                self.assertEqual(first_absorbed, absorbed)
                self.assertEqual(200 - soldier_pool.total, damage)
                self.assertTrue(actor.firing)
                self.assertEqual(actor.attack_target, soldier.entity)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_npc_capture_ignores_live_kinetic_grasp_and_releases_soldier(self):
        for key, contact_damage in (("4", 25), ("5", 0)):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, sigma, actor = self.arena(key, incoming=True)
                fight = run.match.combat
                sigma.mover.position = [1, 0, -2.4]
                sigma.mover.body = None
                actor.position = (0, 0, -1 if key == "4" else 0)
                self.activate(run, soldier, sigma)
                if key == "4":
                    x, y, z = actor.position
                    angles = aim(fight._eye(soldier), (x, y + .8, z))
                    run.step({soldier: (*angles, 0), sigma: (self.facing, 0, 0)})
                    self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                else:
                    actor.yaw = -32768
                    actor.body_script.component.edges([(35, True)])
                for _ in range(65):
                    self.step(run, soldier, sigma)
                    if fight.is_controlled(soldier):
                        break
                self.assertTrue(self.state(sigma).active)
                self.assertEqual(fight.pins.get(actor.entity), soldier.body)
                self.assertEqual(self.pool(run, soldier).total, 200 - contact_damage)
                self.assertEqual(self.pool(run, sigma).total, 400 - (50 if key == "5" else 0))
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
                self.assertGreater(npc_control.stop(run.match), 0)
                self.step(run, soldier, sigma)
                self.assertFalse(fight.is_controlled(soldier))
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                npc_pool = self.pool(run, actor)
                health = npc_pool.total
                x, y, z = actor.position
                angles = aim(fight._eye(soldier), (x, y + .8, z))
                run.step({soldier: (*angles, 1), sigma: (self.facing, 0, 0)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(npc_pool.total, health - 20)


if __name__ == "__main__":
    unittest.main()
