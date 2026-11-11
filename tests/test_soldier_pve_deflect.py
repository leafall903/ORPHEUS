"""Real NPC payloads and bodies through the scoped resource-box reconstruction."""

import math
import unittest

import test_soldier_pve_static as fixture
from test_game_combat import in_world

from ow174.game import combat, deflection, stats
from ow174.game.content import heroes


class SoldierPveDeflectTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    vitals = fixture.SoldierPveStaticTests.vitals

    def arena(self, key, position, team, *, facing=24576, pitch=2731, genji_team=1, activate=True):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, position, team)
        genji = run.match.add_player(2, "Genji", deflection.GENJI, 1, False)
        in_world(run.match, genji)
        genji.client = fixture.NpcRecordingClient(genji)
        run.players.append(genji)
        run.match.switch_hero(genji, heroes()[deflection.GENJI])
        genji.team = genji_team
        genji.mover.position[:] = [0, 0, 1]
        for player in run.players:
            player.mover.position = list(player.mover.position)
            player.mover.body = None
            player.body_script.ult_seconds = 0
        self.facing, self.pitch = facing, pitch
        for _ in range(20):
            self.step(run, soldier, genji)
        if activate:
            self.activate(run, soldier, genji)
        return run, soldier, genji, actor

    def activate(self, run, soldier, genji):
        self.step(run, soldier, genji, genji_buttons=16)
        for _ in range(4):
            self.step(run, soldier, genji)
        self.assertTrue(deflection.state_of(next(t for t in run.match.combat._targets()
                                                if t.owner is genji)).active)

    def step(self, run, soldier, genji, buttons=0, *, genji_buttons=0):
        run.step({soldier: (0, 0, buttons), genji: (self.facing, self.pitch, genji_buttons)})

    def test_visor_hits_all_five_npcs_after_deflect_expires(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, genji, actor = self.arena(
                    key, (0, 0, 6), 1, facing=-32768, pitch=0, activate=False,
                )
                genji.mover.position = [0.65, 0, 1]
                genji.mover.body = None
                fight = run.match.combat
                script = soldier.body_script
                script.set_ult_charge(script.component.vars[338].value())
                self.step(run, soldier, genji, 0x20)
                for _ in range(80):
                    self.step(run, soldier, genji)
                self.activate(run, soldier, genji)
                target = next(t for t in fight._targets() if t.owner is genji)
                state = deflection.state_of(target)
                a, b = target.segment()
                body_contact = combat.ray_capsule(fight._eye(soldier), combat.aim(0, 0), a, b, target.radius)
                self.assertIsNone(body_contact)
                pool = self.vitals(run, actor)
                initial = pool.total
                self.step(run, soldier, genji, 1)
                self.assertEqual(pool.total, initial)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                returned = 200 - self.vitals(run, soldier).total
                self.assertGreater(returned, 0)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)
                for _ in range(150):
                    self.step(run, soldier, genji)
                    if not state.active:
                        break
                self.assertFalse(state.active)
                self.step(run, soldier, genji, 1)
                self.assertEqual(pool.total, initial - 20)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 20)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)

    def test_returned_soldier_round_uses_new_team_and_hits_each_actual_npc_body(self):
        contact = 1 - .75 * math.sqrt(2)
        for key, definition in self.catalog.items():
            for team in (0, 1):
                with self.subTest(npc=definition.name, target_team=team):
                    run, soldier, genji, actor = self.arena(key, (2, 0, contact - 2), team)
                    pool = self.vitals(run, actor)
                    before = pool.total
                    self.step(run, soldier, genji, 1)
                    self.assertEqual(before - pool.total, 20 if team == 0 else 0)
                    self.assertEqual(self.vitals(run, soldier).total, 200)
                    self.assertEqual(self.vitals(run, genji).total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 20 if team == 0 else 0)
                    self.assertEqual(genji.body_script.component.entity_var(342).value(),
                                     20 if team == 0 else 0)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_npc_controller_round_returns_to_four_shooters(self):
        for key, amount in (("1", 20), ("2", 50), ("3", 100), ("3", 5), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name, amount=amount):
                charge_shot = key == "3" and amount == 100
                run, soldier, genji, actor = self.arena(key, (0, 0, 5), 1,
                                                       facing=0, pitch=0, genji_team=0,
                                                       activate=not charge_shot)
                pool = self.vitals(run, actor)
                before = pool.total
                if key == "3" and not charge_shot:
                    actor.body_script.component.edges([(16, True), (16, False)])
                actor.attack_target, actor.firing = soldier.entity, True
                if charge_shot:
                    # Let the actual Sniper begin its authored two-second charge;
                    # starting Deflect before that charge would expire too early.
                    for _ in range(20):
                        self.step(run, soldier, genji)
                    self.activate(run, soldier, genji)
                for _ in range(120):
                    self.step(run, soldier, genji)
                    if (pool.total < before
                            and not any(r.shooter in (actor, genji) for r in run.match.combat.rockets)):
                        break
                actor.firing = False
                self.assertEqual(before - pool.total, amount)
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertEqual(self.vitals(run, genji).total, 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), amount)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), amount)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_returned_helix_keeps_payload_and_new_team_for_each_actual_npc_body(self):
        contact = 1 - .75 * math.sqrt(2)
        for key, definition in self.catalog.items():
            for team in (0, 1):
                with self.subTest(npc=definition.name, target_team=team):
                    run, soldier, genji, actor = self.arena(key, (6, 0, contact - 6), team, pitch=1000)
                    pool = self.vitals(run, actor)
                    before = pool.total
                    self.step(run, soldier, genji, 2)
                    rocket, = run.match.combat.rockets
                    for _ in range(160):
                        if rocket not in run.match.combat.rockets:
                            break
                        self.step(run, soldier, genji)
                    self.assertNotIn(rocket, run.match.combat.rockets)
                    self.assertIs(rocket.shooter, genji)
                    self.assertEqual(rocket.source_entity, genji.body)
                    self.assertEqual(before - pool.total, 120 if team == 0 else 0)
                    self.assertEqual(self.vitals(run, soldier).total, 200)
                    self.assertEqual(self.vitals(run, genji).total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120 if team == 0 else 0)
                    self.assertEqual(genji.body_script.component.entity_var(342).value(),
                                     120 if team == 0 else 0)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_returned_visor_round_keeps_noncritical_payload_for_each_actual_npc_body(self):
        contact = 1 - .75 * math.sqrt(2)
        for key, definition in self.catalog.items():
            for team in (0, 1):
                with self.subTest(npc=definition.name, target_team=team):
                    run, soldier, genji, actor = self.arena(key, (6, 0, contact - 6), team,
                                                           pitch=1000, activate=False)
                    soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                    self.step(run, soldier, genji, 0x20)
                    for _ in range(80):
                        self.step(run, soldier, genji)
                    self.activate(run, soldier, genji)
                    pool = self.vitals(run, actor)
                    before = pool.total
                    self.step(run, soldier, genji, 1)
                    self.assertEqual(before - pool.total, 20 if team == 0 else 0)
                    self.assertEqual(self.vitals(run, soldier).total, 200)
                    self.assertEqual(self.vitals(run, genji).total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 20 if team == 0 else 0)
                    self.assertEqual(genji.body_script.component.entity_var(342).value(),
                                     20 if team == 0 else 0)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
