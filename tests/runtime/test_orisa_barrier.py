"""Real extracted mesh + command-driven combat; no network/native fidelity claim."""

import math
import os
import unittest
from pathlib import Path

import test_orisa_combat as fixture

from ow174.game import bots, combat
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes
from ow174.game.orisa_barrier import DEPLOY_DELAY, HEALTH, LIFETIME, Geometry


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "external barrier mesh required")
class BarrierTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        # Rebuild the fixture mover so its collision body shares the new pose.
        player.spawn = (position, player.spawn[1])
        player.mover = player.new_mover()

    def deploy(self):
        run, player, enemy = fixture.OrisaCombatTests().arena()
        fight = run.match.combat
        rocket = None
        for i in range(120):
            run.step({player: (0, 0, 16 if i == 0 else 0)})
            rocket = next((r for r in fight.rockets if r.source_graph == 0xBD7), rocket)
            if fight.orisa.barriers.active:
                break
        self.assertIsNotNone(rocket)
        self.assertEqual(len(fight.orisa.barriers.active), 1)
        shield = next(iter(fight.orisa.barriers.active.values()))
        x, y, z = shield.position
        self.place(player, (x, y, z - 6))
        self.place(enemy, (x, y, z + 5))
        run.step({})
        return run, player, enemy, rocket, shield

    def test_real_mesh_provenance_and_curved_surface(self):
        geometry = Geometry(Path(os.environ["ORPHEUS_ORISA_GEOMETRY"]))
        self.assertEqual(geometry.source,
                         "92c08849cb4348870aad667c74231e8c475cd05bf438c443860d2e8b9f55b28e")
        centre = geometry.cast((0, 0, 0), 0, (0, 1.5, 5), (0, 0, -1), 20)
        side = geometry.cast((0, 0, 0), 0, (3, 1.5, 5), (0, 0, -1), 20)
        self.assertIsNotNone(centre)
        self.assertIsNotNone(side)
        self.assertNotAlmostEqual(centre, side, places=2)
        self.assertIsNone(geometry.cast((0, 0, 0), 0, (4, 1.5, 5), (0, 0, -1), 20))
        self.assertIsNone(geometry.cast((0, 0, 0), 0, (0, 5, 5), (0, 0, -1), 20))
        rotated = geometry.cast((10, 0, 20), math.pi / 2, (15, 1.5, 20), (-1, 0, 0), 20)
        self.assertAlmostEqual(rotated, centre)

    def test_impact_delayed_deployment_has_authored_health_and_deadline(self):
        run, _, _, rocket, shield = self.deploy()
        self.assertEqual(shield.position, rocket.position)
        self.assertEqual(shield.health, HEALTH)
        self.assertEqual(rocket.ended, "world")
        self.assertAlmostEqual(shield.yaw, 0)
        self.assertEqual(shield.until - math.ceil(LIFETIME / combat.TICK), run.frame - 1)
        self.assertEqual(math.ceil(DEPLOY_DELAY / combat.TICK), 7)

    def test_enemy_hitscan_damages_mesh_without_headshot_or_owner_damage(self):
        run, player, enemy, _, shield = self.deploy()
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 580)
        self.assertEqual(vitals.total, before)

    def test_friendly_projectile_passes_mesh_and_damages_enemy(self):
        run, player, enemy, _, shield = self.deploy()
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, enemy))
        run.step({player: (0, 0, FIRE)})
        for _ in range(10):
            run.step({})
        self.assertEqual(shield.health, 600)
        self.assertEqual(vitals.total, 178)

    def test_enemy_projectile_stops_at_mesh_and_loses_no_critical_bonus(self):
        run, player, enemy, _, shield = self.deploy()
        run.match.switch_hero(enemy, find_hero("Orisa"))
        x, y, z = shield.position
        self.place(enemy, (x, y, z + 5))
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        run.step({enemy: (32768, 0, FIRE)})
        rockets = list(run.match.combat.rockets)
        for _ in range(10):
            run.step({})
        self.assertEqual(shield.health, 589)
        self.assertEqual(vitals.total, before)
        self.assertTrue(any(r.ended == "barrier" for r in rockets))

    def test_hitscan_can_damage_mesh_without_a_body_behind_it(self):
        run, player, enemy, _, shield = self.deploy()
        player.spawned = False
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 580)

    def test_nearer_body_is_hit_before_barrier(self):
        run, player, enemy, _, shield = self.deploy()
        x, y, z = shield.position
        self.place(player, (x, y, z + 2))
        run.step({})
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 600)
        self.assertLess(vitals.total, before)

    def test_static_wall_before_mesh_prevents_shield_damage(self):
        run, _, enemy, _, shield = self.deploy()
        fixture.OrisaCombatTests().wall(run, enemy, -2)
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 600)

    def test_depletion_removes_mesh_and_next_volley_hits_owner(self):
        run, player, enemy, _, shield = self.deploy()
        shield.health = 20
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        run.step({enemy: (32768, 0, FIRE)})
        self.assertNotIn(shield.entity, run.match.combat.orisa.barriers.active)
        self.assertEqual(vitals.total, before)
        for _ in range(10):
            run.step({enemy: (32768, 0, FIRE)})
        self.assertLess(vitals.total, before)

    def test_deadline_expires_without_refresh_after_world_gap(self):
        run, _, _, _, shield = self.deploy()
        manager = run.match.combat.orisa.barriers
        manager.update(shield.until - 1)
        self.assertIn(shield.entity, manager.active)
        manager.update(shield.until)
        self.assertFalse(manager.active)

    def test_full_health_barrier_is_destroyed_by_real_held_fire(self):
        run, player, enemy, _, shield = self.deploy()
        fight = run.match.combat
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        rounds = 0
        for _ in range(500):
            old = shield.health
            run.step({enemy: (32768, 0, FIRE)})
            if shield.health != old:
                rounds += 1
                self.assertEqual(old - shield.health, 20)
            if shield.entity not in fight.orisa.barriers.active:
                break
        self.assertEqual(rounds, 30)
        self.assertEqual(shield.health, 0)
        self.assertEqual(vitals.total, before)

    def test_team_ally_transparency_differs_from_same_team_ffa_enemy(self):
        run, player, enemy, _, shield = self.deploy()
        enemy.team = player.team
        manager = run.match.combat.orisa.barriers
        eye = run.match.combat._eye(enemy)
        # FFA bodies sharing a team id remain opponents.
        self.assertIsNotNone(manager.trace(enemy, eye, (0, 0, -1), 20))
        from dataclasses import replace
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertIsNone(manager.trace(enemy, eye, (0, 0, -1), 20))
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 600)

    def test_pending_expired_deployment_does_not_reappear_after_gap(self):
        run, _, _, rocket, _ = self.deploy()
        manager = run.match.combat.orisa.barriers
        manager.active.clear()
        manager.landed(rocket)
        due = manager.pending[0][0]
        manager.update(due + math.ceil(LIFETIME / combat.TICK))
        self.assertFalse(manager.pending)
        self.assertFalse(manager.active)

    def test_source_body_retirement_cancels_pending_deployment(self):
        run, player, _, rocket, _ = self.deploy()
        manager = run.match.combat.orisa.barriers
        manager.active.clear()
        manager.landed(rocket)
        run.match.switch_hero(player, find_hero("Soldier: 76"))
        manager.update(run.frame + 10)
        self.assertFalse(manager.pending)
        self.assertFalse(manager.active)

    def helix(self, run, enemy):
        fight = run.match.combat
        rocket = None
        for index in range(60):
            run.step({enemy: (32768, 0, 2 if index == 0 else 0)})
            rocket = next((r for r in fight.rockets if r.definition == 0x040000000000072C), rocket)
            if rocket is not None and rocket not in fight.rockets:
                return rocket
        self.fail("actual Helix command did not impact")

    def test_helix_direct_and_blast_damage_barrier_but_not_near_owner_behind_it(self):
        run, player, enemy, _, shield = self.deploy()
        x, y, z = shield.position
        self.place(player, (x, y, z - 1.5))
        run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        rocket = self.helix(run, enemy)
        self.assertEqual(rocket.ended, "barrier")
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
        self.assertAlmostEqual(shield.health, 480)
        self.assertEqual(vitals.total, before)

    def test_curved_flashbang_hits_the_mesh_without_stunning_nearby_owner(self):
        run, player, enemy, _, shield = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        x, y, z = shield.position
        self.place(player, (x, y, z - 1.5))
        self.place(enemy, (x, y, z + 5))
        run.step({})
        fight = run.match.combat
        target = fixture.OrisaCombatTests().target(run, player)
        before = fight.vitals_of(target).total
        run.step({enemy: (-32768, 0, 16)})
        rocket = None
        for _ in range(50):
            rocket = next((r for r in fight.rockets if r.source_graph == 0x01E9), rocket)
            if rocket is not None and rocket.ended:
                break
            run.step({})
        self.assertIsNotNone(rocket)
        self.assertEqual(rocket.ended, "barrier")
        self.assertEqual(rocket.gravity, 20)
        self.assertAlmostEqual(shield.health, 575)
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertFalse(fight.is_stunned(player))

    def test_helix_can_splash_exposed_npc_on_incoming_side_of_barrier(self):
        run, player, enemy, _, shield = self.deploy()
        x, y, z = shield.position
        bot = bots.Bot(run.match.combat.next_bot, {
            "hero": "0x02E000000000016B", "team": player.team,
            "position": (x + 1.5, y, z + 0.7), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Exposed target", "body": "0x0400000000001403", "health": 200}, {})
        run.match.combat.next_bot += 1
        run.match.bots.append(bot)
        fight = run.match.combat
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, bot))
        self.helix(run, enemy)
        self.assertLess(vitals.total, 200)
        self.assertGreater(vitals.total, 120)  # authored radial falloff, no headshot multiplier
        self.assertAlmostEqual(shield.health, 480)

    def test_same_explosion_does_not_leak_through_a_barrier_it_destroys(self):
        for health in (40, 100):  # destroyed by direct damage, or by direct+splash
            with self.subTest(health=health):
                run, player, enemy, _, shield = self.deploy()
                shield.health = health
                x, y, z = shield.position
                self.place(player, (x, y, z - 1.5))
                run.step({})
                fight = run.match.combat
                vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, player))
                before = vitals.total
                self.helix(run, enemy)
                self.assertEqual(shield.health, 0)
                self.assertNotIn(shield.entity, fight.orisa.barriers.active)
                self.assertEqual(vitals.total, before)

    def test_helix_body_impact_near_barrier_also_deals_radial_barrier_damage(self):
        run, player, enemy, _, shield = self.deploy()
        x, y, z = shield.position
        self.place(player, (x, y, z + 0.7))
        run.step({})
        rocket = self.helix(run, enemy)
        self.assertEqual(rocket.ended, "body")
        self.assertGreater(shield.health, 520)  # radial falloff; no direct shield hit
        self.assertLess(shield.health, 560)

    def test_actual_cross_hero_explosives_include_zero_direct_zarya_grenade(self):
        for name, button, expected in (("Pharah", FIRE, 120), ("Zarya", 2, 47),
                                        ("Junkrat", FIRE, 120)):
            with self.subTest(hero=name):
                run, _, enemy, _, shield = self.deploy()
                run.match.switch_hero(enemy, find_hero(name))
                x, y, z = shield.position
                self.place(enemy, (x, y, z + 5))
                rocket = None
                for index in range(80):
                    run.step({enemy: (32768, 0, button if index == 0 else 0)})
                    if run.match.combat.rockets:
                        rocket = run.match.combat.rockets[0]
                    if rocket is not None and rocket.ended is not None:
                        break
                self.assertIsNotNone(rocket)
                self.assertEqual(rocket.ended, "barrier")
                self.assertAlmostEqual(shield.health, 600 - expected)
                if name == "Zarya":
                    self.assertEqual(rocket.direct.amount, 0)

    def test_helix_world_impact_does_not_splash_through_wall(self):
        run, player, enemy, _, shield = self.deploy()
        x, y, z = shield.position
        # Put the barrier elsewhere; this check isolates static-world blast LOS.
        shield.position = (x + 30, y, z)
        self.place(player, (x, y, z + 1.5))
        fixture.OrisaCombatTests().wall(run, enemy, -2)
        run.step({})
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        rocket = self.helix(run, enemy)
        self.assertEqual(rocket.ended, "world")
        self.assertEqual(vitals.total, before)
        self.assertEqual(shield.health, 600)


if __name__ == "__main__":
    unittest.main()
