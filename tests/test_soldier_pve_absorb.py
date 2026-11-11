"""Actual five NPC bodies behind Sigma and four real NPC incoming weapons."""

import unittest

import test_soldier_absorb as grasp_fixture
import test_soldier_pve_static as fixture
from test_game_combat import in_world

from ow174.game import sigma_grasp, stats
from ow174.game.content import heroes


class SoldierPveAbsorbTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    pool = grasp_fixture.SoldierAbsorbTests.pool
    instance = grasp_fixture.SoldierAbsorbTests.instance
    state = grasp_fixture.SoldierAbsorbTests.state

    def arena(self, key, *, incoming=False, activate=True):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key,
                                                               (0, 0, 6 if incoming else 3), 1)
        sigma = run.match.add_player(2, "Sigma", sigma_grasp.SIGMA, 1, False)
        in_world(run.match, sigma)
        sigma.client = fixture.NpcRecordingClient(sigma)
        run.players.append(sigma)
        run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA])
        sigma.team = soldier.team if incoming else 1 - soldier.team
        sigma.mover.position[:] = [0, 0, 1]
        for player in run.players:
            player.mover.position = list(player.mover.position)
            player.mover.body = None
            player.body_script.ult_seconds = 0
        self.facing = 0 if incoming else -32768
        for _ in range(20):
            self.step(run, soldier, sigma)
        if activate:
            self.activate(run, soldier, sigma)
        return run, soldier, sigma, actor

    def activate(self, run, soldier, sigma):
        self.step(run, soldier, sigma, sigma_buttons=8)
        for _ in range(4):
            self.step(run, soldier, sigma)
        self.assertTrue(self.state(sigma).active)

    def step(self, run, soldier, sigma, buttons=0, *, sigma_buttons=0):
        run.step({soldier: (0, 0, buttons), sigma: (self.facing, 0, sigma_buttons)})

    def test_sigma_absorbs_soldier_rifle_helix_and_visor_before_each_actual_npc(self):
        for key, definition in self.catalog.items():
            for weapon, amount in (("rifle", 20), ("helix", 120), ("visor", 20)):
                with self.subTest(npc=definition.name, weapon=weapon):
                    run, soldier, sigma, actor = self.arena(key, activate=weapon != "visor")
                    if weapon == "visor":
                        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                        self.step(run, soldier, sigma, 0x20)
                        for _ in range(80):
                            self.step(run, soldier, sigma)
                        self.activate(run, soldier, sigma)
                    before = self.pool(run, actor).total
                    self.step(run, soldier, sigma, 2 if weapon == "helix" else 1)
                    for _ in range(20):
                        self.step(run, soldier, sigma)
                    self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), amount)
                    self.assertEqual(self.pool(run, actor).total, before)
                    self.assertEqual(self.pool(run, sigma).total, 400)
                    self.assertEqual(self.pool(run, soldier).total, 200)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_four_actual_npc_controller_shots_are_absorbed_before_soldier(self):
        for key, amount in (("1", 20), ("2", 50), ("3", 100), ("3", 5), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name, amount=amount):
                charge_shot = key == "3" and amount == 100
                run, soldier, sigma, actor = self.arena(key, incoming=True, activate=not charge_shot)
                if key == "3" and not charge_shot:
                    actor.body_script.component.edges([(16, True), (16, False)])
                actor.attack_target, actor.firing = soldier.entity, True
                if charge_shot:
                    for _ in range(20):
                        self.step(run, soldier, sigma)
                    self.activate(run, soldier, sigma)
                for _ in range(120):
                    self.step(run, soldier, sigma)
                    if (self.instance(sigma).find_var(0, 21312).value()
                            and not any(r.shooter is actor for r in run.match.combat.rockets)):
                        break
                actor.firing = False
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), amount)
                self.assertEqual(self.pool(run, soldier).total, 200)
                self.assertEqual(self.pool(run, sigma).total, 400)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
