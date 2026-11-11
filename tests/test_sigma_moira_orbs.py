"""Actual Biotic Orb launch, aura, and Sigma defense ordering."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_sigma_barrier as barrier_fixtures
import test_sigma_combat as combat_fixtures
import test_sigma_deflect as deflect_fixtures
import test_sigma_moira as moira_fixtures
from test_game_combat import aim, in_world

from ow174.game import combat, moira_orbs, sigma, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.script import expr, graph, runtime


class SigmaMoiraOrbTests(unittest.TestCase):
    def setUp(self):
        if any(graph.graph(index) is None for index in (0x10FF, 0x1100, 0x1101, 0x1102)):
            self.skipTest("requires the external Biotic Orb control and aura graphs")
        self.fixture = moira_fixtures.SigmaMoiraTests()

    def launch(self, run, player, owner, *, healing=False, defense=0, facing=0, aim_at=None):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(owner), fight._eye(aim_at or player))
        run.step({owner: (yaw, pitch, 0x10), player: (facing, 0, defense)})
        for _ in range(40):
            run.step({owner: (yaw, pitch, FIRE if healing else SECONDARY), player: (facing, 0, 0)})
            if fight.moira_orbs:
                break
        orb, = fight.moira_orbs
        self.assertEqual(orb.definition, moira_orbs.HEAL_ORB if healing else moira_orbs.DAMAGE_ORB)
        self.assertEqual((orb.speed, orb.left, orb.loadout), (20, 7, 0x0B9000000000016D))
        self.assertFalse(fight.rockets)
        return orb

    def test_grasp_removes_both_orbs_and_uses_the_volley_amount_not_aura_energy(self):
        for healing, distance in ((False, 2), (False, 6), (True, 2)):
            with self.subTest(healing=healing, distance=distance):
                run, player, owner, health, _ = self.fixture.scene(distance=distance)
                fight = run.match.combat
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(73)
                orb = self.launch(run, player, owner, healing=healing, defense=8)
                grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(orb.energy, 300 if healing else 200)
                self.assertEqual(orb.absorption, 0 if healing else 100)
                for _ in range(60):
                    before = health.total
                    run.step({})
                    if orb not in fight.moira_orbs:
                        self.assertEqual(health.total, before)  # interception precedes this tick's aura
                        break
                self.assertNotIn(orb, fight.moira_orbs)
                self.assertEqual(grasp.vars[21312].value(), 0 if healing else 100)
                if not healing and distance == 6:
                    self.assertLess(health.total, 400)
                    self.assertAlmostEqual(orb.energy, 200 - (400 - health.total))
                else:
                    self.assertEqual(health.total, 400)
                before = health.total
                for _ in range(130):
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                if healing:
                    self.assertFalse(health.temporary_shields)
                    self.assertEqual(health.total, before)
                else:
                    self.assertAlmostEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 60, places=5)
                    self.assertAlmostEqual(health.total, before + 60, places=5)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 73)

    @unittest.skipUnless(barrier_fixtures.MODEL, "requires the external Experimental Barrier mesh")
    def test_damage_orb_crosses_experimental_barrier_and_spends_its_budget_on_body_damage(self):
        run, player, (owner,), barrier, _ = barrier_fixtures.SigmaBarrierTests().arena()
        health, _ = self.fixture.prepare(run, player, owner)
        orb = self.launch(run, player, owner)
        self.assertGreater(orb.position[2], barrier.position[2])
        for _ in range(80):
            run.step({})
            if health.total < 400:
                break
        self.assertLess(orb.position[2], barrier.position[2])
        self.assertLess(health.total, 400)
        before, position = health.total, orb.position
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before - health.total, 8)
        self.assertAlmostEqual(position[2] - orb.position[2],
                               abs(orb.direction[2]) * 20 * .275 * 10 * combat.TICK)
        self.assertAlmostEqual(orb.energy, 200 - (400 - health.total))
        self.assertEqual(barrier.vitals.total, 700)

    def test_static_cover_blocks_aura_and_reflects_orb_motion(self):
        run, player, owner, health, _ = self.fixture.scene()
        combat_fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
        orb = self.launch(run, player, owner, defense=8)
        for _ in range(35):
            run.step({})
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertEqual(orb.bounces, 1)
        self.assertGreater(orb.direction[2], 0)
        self.assertEqual(orb.energy, 200)
        self.assertEqual(health.total, 400)
        self.assertEqual(grasp.vars[21312].value(), 0)

    def test_allied_healing_orb_preserves_temporary_shields_and_is_not_consumed(self):
        run, player, owner, health, _ = self.fixture.scene(distance=3)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = player.team
        health.now[combat.HEALTH_POOL] -= 60
        health.grant_temporary_shields(sigma.SHIELD_TAG, 30, 400)
        orb = self.launch(run, player, owner, healing=True, defense=8)
        for _ in range(20):
            run.step({})
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertIn(orb, run.match.combat.moira_orbs)
        self.assertAlmostEqual(health.now[combat.HEALTH_POOL], 260.8)
        self.assertEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 30)
        self.assertAlmostEqual(orb.energy, 279.2)
        self.assertEqual(grasp.vars[21312].value(), 0)

    def test_damage_orb_exhausts_the_launch_budget_without_direct_impact_damage(self):
        run, player, owner, health, _ = self.fixture.scene(distance=3)
        owner.body_script.component.set(12619, .025)
        orb = self.launch(run, player, owner)
        self.assertEqual(orb.energy, 5)
        self.assertEqual(orb.absorption, 100)
        for _ in range(20):
            run.step({})
        self.assertEqual(orb.energy, 0)
        self.assertNotIn(orb, run.match.combat.moira_orbs)
        self.assertAlmostEqual(health.total, 395)

    def test_orb_expires_on_world_time_even_if_most_flight_is_outside_the_retained_window(self):
        run, player, owner, health, _ = self.fixture.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = player.team
        orb = self.launch(run, player, owner)
        run.step({})
        start = orb.position
        tick = run.frame + 500
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertNotIn(orb, run.match.combat.moira_orbs)
        self.assertEqual(orb.left, 0)
        self.assertAlmostEqual(orb.flown, 7)
        self.assertAlmostEqual(start[2] - orb.position[2], abs(orb.direction[2]) * 20 * (7 - combat.TICK))
        self.assertEqual(health.total, 400)

    def launch_before_lethal_accretion(self, game_map=combat_fixtures.KANEZAKA_DM):
        run, player, owner, _ = combat_fixtures.SigmaCombatTests().arena(offset=0, distance=6, game_map=game_map)
        health, owner_health = self.fixture.prepare(run, player, owner)
        fight = run.match.combat
        health.now[combat.HEALTH_POOL] = 5
        health.now[combat.SHIELDS_POOL] = 0
        owner_health.now[combat.HEALTH_POOL] = 60
        owner_health.now[combat.SHIELDS_POOL] = 0
        x, y, z = owner.mover.position
        rock_aim = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*rock_aim, 0x10)})
        for _ in range(50):
            if fight.rockets:
                break
            run.step({player: (*rock_aim, 0)})
        self.assertEqual(len(fight.rockets), 1)
        self.fixture.fire(run, player, owner, 0x10)
        for _ in range(10):
            self.fixture.fire(run, player, owner, SECONDARY)
            if fight.moira_orbs:
                break
        orb, = fight.moira_orbs
        old_body = owner.body
        for _ in range(25):
            run.step({})
            if owner_health.dead:
                break
        self.assertTrue(owner_health.dead)
        self.assertEqual(owner_health.killer, player.body)
        self.assertFalse(health.dead)
        self.assertIn(orb, fight.moira_orbs)
        return run, player, owner, health, orb, old_body

    def test_launched_orb_survives_lethal_accretion_and_can_kill_sigma_posthumously(self):
        run, _, _, health, _, old_body = self.launch_before_lethal_accretion()
        for _ in range(35):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, old_body)

    def test_orb_keeps_its_source_through_the_practice_respawn_timer(self):
        run, player, owner, health, orb, launch_body = self.launch_before_lethal_accretion(PRACTICE_RANGE)
        fight = run.match.combat
        death_time = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner)).died_at
        self.assertEqual(combat.respawn_seconds(run.match), 3)
        x, y, z = player.mover.position
        player.mover.position[:] = (x + 12, y, z)
        player.mover.body = None
        for _ in range(210):
            run.step({})
            if owner.body != launch_body:
                break
        self.assertNotEqual(owner.body, launch_body)
        self.assertGreaterEqual(fight.now + combat.EPSILON, death_time + 3)
        self.assertLess(fight.now, death_time + 3 + combat.TICK + combat.EPSILON)
        self.assertFalse(fight.is_dead(owner))
        self.assertIn(orb, fight.moira_orbs)
        self.assertEqual(health.total, 5)
        x, _, z = orb.position
        player.mover.position[:] = (x, y, z)
        player.mover.body = None
        for _ in range(20):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, launch_body)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)

    def test_orb_kill_keeps_the_launch_body_after_hero_replacement(self):
        run, player, owner, health, _ = self.fixture.scene()
        health.now[combat.HEALTH_POOL] = 5
        health.now[combat.SHIELDS_POOL] = 0
        orb = self.launch(run, player, owner)
        launch_body = owner.body
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotEqual(owner.body, launch_body)
        self.assertIn(orb, run.match.combat.moira_orbs)
        with patch.object(runtime, "send_message", wraps=runtime.send_message) as send:
            for _ in range(60):
                run.step({})
                if health.dead:
                    break
        (params, sender), = (call.args[2:] for call in send.call_args_list
                             if call.args[:2] == (player.body_script.component, combat.DIED))
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, launch_body)
        self.assertEqual(sender, launch_body)
        self.assertEqual(params[combat.KILLER], expr.Entity(launch_body))
        self.assertEqual(params[combat.LOADOUT], expr.Asset(orb.loadout))
        self.assertEqual(player.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], launch_body)
        self.assertEqual(owner.client.messages(combat.KILL_NOTICE)[-1]["+0x80"]["+0x0"], launch_body)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)

    def test_grasp_still_intercepts_the_enemy_orb_after_hero_replacement(self):
        run, player, owner, health, _ = self.fixture.scene(distance=2)
        fight = run.match.combat
        orb = self.launch(run, player, owner, defense=8)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotEqual(owner.body, orb.source_body)
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        for _ in range(30):
            run.step({})
            if orb not in fight.moira_orbs:
                break
        self.assertNotIn(orb, fight.moira_orbs)
        self.assertEqual(health.total, 400)
        self.assertEqual(grasp.vars[21312].value(), 100)

    def deflect_scene(self, defense=0):
        genji_case = deflect_fixtures.SigmaDeflectTests()
        self.addCleanup(genji_case.doCleanups)
        genji_case.setUp()
        run, player, owner, health, _ = self.fixture.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = player.team
        genji = run.match.add_player(3, "Genji", deflect_fixtures.GENJI, 1 - player.team, False)
        in_world(run.match, genji)
        run.players.append(genji)
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflect_fixtures.GENJI]))
        x, y, z = player.mover.position
        owner.mover.position[:] = (x, y, z - 3)
        genji.mover.position[:] = (x, y, z + 4)
        owner.mover.body = genji.mover.body = None
        run.step({})
        fight = run.match.combat
        facing = aim(fight._eye(genji), fight._eye(owner))
        run.step({genji: (*facing, 0x10)})
        orb = self.launch(run, player, owner, defense=defense, aim_at=genji)
        return run, player, owner, genji, health, orb, facing

    @unittest.skipUnless(os.environ.get("ORPHEUS_GENJI_GRAPHS"), "requires the external Genji graph bundle")
    def test_deflect_turns_an_allied_orb_into_hostile_kinetic_grasp_input(self):
        run, player, owner, genji, health, orb, facing = self.deflect_scene(defense=8)
        fight = run.match.combat
        for _ in range(70):
            before = health.total
            run.step({genji: (*facing, 0)})
            if orb.shooter is owner:
                self.assertEqual(health.total, 400)
            if orb not in fight.moira_orbs:
                self.assertEqual(health.total, before)
                break
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertIs(orb.shooter, genji)
        self.assertNotIn(orb, fight.moira_orbs)
        self.assertEqual(grasp.vars[21312].value(), 100)
        self.assertAlmostEqual(health.total, 396.8)

    @unittest.skipUnless(os.environ.get("ORPHEUS_GENJI_GRAPHS"), "requires the external Genji graph bundle")
    def test_reflected_orb_uses_the_deflect_body_even_after_replacement(self):
        run, _, owner, genji, health, orb, facing = self.deflect_scene()
        for _ in range(60):
            run.step({genji: (*facing, 0)})
            if orb.shooter is genji:
                break
        self.assertIs(orb.shooter, genji)
        self.assertEqual(orb.source_body, genji.body)
        self.assertNotEqual(orb.source_body, owner.body)
        deflect_body = genji.body
        self.assertTrue(run.match.switch_hero(genji, heroes()[SOLDIER]))
        self.assertNotEqual(genji.body, deflect_body)
        health.now[combat.HEALTH_POOL] = 5
        health.now[combat.SHIELDS_POOL] = 0
        for _ in range(30):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, deflect_body)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 0)
