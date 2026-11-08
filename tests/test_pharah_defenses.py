"""Actual defensive skill input versus Pharah attacks; phase policy is reconstructed."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_combat as model
import test_pharah_combat as primary

from ow174.game import defenses
from ow174.game.content import heroes


class PharahDefenseTests(unittest.TestCase):
    health = primary.PharahCombatTests.health

    def encounter(self, name):
        run = model.Run(model.KANEZAKA_DM, ("Pharah", name, "Behind"))
        shooter, defender, behind = run.players
        by_name = {hero.name: hero for hero in heroes().values()}
        run.match.switch_hero(shooter, by_name["Pharah"])
        run.match.switch_hero(defender, by_name[name])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        y = shooter.mover.position[1]
        for player, z in ((shooter, 0), (defender, 8), (behind, 12)):
            player.mover.position = [0, y, z]
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(30):
            run.step({})
        return run, shooter, defender, behind

    def cast(self, run, defender):
        run.step({defender: (-32768, 0, 8)})
        for _ in range(5):
            run.step({})
        self.assertTrue(defenses.phase(defender)[0])

    def fire(self, run, shooter):
        run.step({shooter: (0, 0, 1)})
        for _ in range(40):
            run.step({})

    def test_actual_wraith_and_fade_let_rocket_pass_to_enemy_behind(self):
        for name in ("Reaper", "Moira"):
            with self.subTest(hero=name):
                run, shooter, defender, behind = self.encounter(name)
                before = self.health(run, defender).total
                self.cast(run, defender)
                self.assertTrue(defenses.phase(defender)[1])
                self.fire(run, shooter)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, behind).total, 80)
                self.assertEqual(shooter.body_script.component.entity_var(342).value(), 120)

    def test_phase_expiry_restores_damage_and_collision_without_sticky_immunity(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(hero=name):
                run, shooter, defender, behind = self.encounter(name)
                self.cast(run, defender)
                for _ in range(320):
                    run.step({})
                self.assertFalse(defenses.phase(defender)[0])
                before = self.health(run, defender).total
                self.fire(run, shooter)
                self.assertEqual(self.health(run, defender).total, before - 120)
                self.assertEqual(self.health(run, behind).total, 200)

    def test_cryo_freeze_stops_rocket_but_grants_no_damage_or_charge(self):
        run, shooter, defender, behind = self.encounter("Mei")
        before = self.health(run, defender).total
        self.cast(run, defender)
        self.assertFalse(defenses.phase(defender)[1])
        self.fire(run, shooter)
        self.assertEqual(self.health(run, defender).total, before)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_cryo_freeze_blocks_concussion_impulse_and_melee_damage(self):
        run, shooter, defender, behind = self.encounter("Mei")
        behind.mover.position[0] = 20
        behind.mover.body = None
        self.cast(run, defender)
        before = list(defender.mover.position)
        run.step({shooter: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(defender.mover.position, before)
        shooter.mover.position[2] = 6.2
        shooter.mover.body = None
        health = self.health(run, defender).total
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, defender).total, health)

    def test_active_phase_entry_and_duration_export_in_passive_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        reaper = next(hero for hero in heroes().values() if hero.name == "Reaper")
        self.assertTrue(session.apply("switch_hero", {"account": player.account_lo, "hero": reaper.guid}))
        peers[0].commands(player.body_script.frame + 1, 1, 8)
        self.assertTrue(defenses.phase(player)[0])
        self.assertFalse(case.capture(session)["restorable"])
        peers[0].commands(player.body_script.frame + 1, 350, 0)
        self.assertFalse(defenses.phase(player)[0])
        self.assertFalse(case.capture(session)["restorable"])

    def defended_shot(self, name, facing=-32768):
        run, shooter, defender, behind = self.encounter(name)
        button = 16 if name == "Genji" else 8
        run.step({defender: (facing, 0, button)})
        for _ in range(5):
            run.step({defender: (facing, 0, 0)})
        before = self.health(run, defender).total
        run.step({shooter: (0, 0, 1), defender: (facing, 0, 0)})
        for _ in range(60):
            run.step({defender: (facing, 0, 0)})
        return run, shooter, defender, behind, before

    def test_genji_deflect_redirects_real_rocket_and_damage_credit_to_defender(self):
        run, shooter, defender, behind, before = self.defended_shot("Genji")
        self.assertEqual(self.health(run, defender).total, before)
        self.assertEqual(self.health(run, shooter).total, 80)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        self.assertEqual([h["+0x78"]["+0x4"] for h in defender.client.messages(25001)], [40, 80])

    def test_sigma_grasp_consumes_real_rocket_without_blast_damage_or_charge(self):
        run, shooter, defender, behind, before = self.defended_shot("Sigma")
        self.assertEqual(self.health(run, defender).total, before)
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_active_front_defenses_do_not_protect_the_back(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(hero=name):
                run, shooter, defender, _, before = self.defended_shot(name, facing=0)
                self.assertEqual(self.health(run, defender).total, before - 120)
                self.assertEqual(self.health(run, shooter).total, 200)

    def test_genji_deflect_ends_and_next_primary_can_damage_normally(self):
        run, shooter, defender, _, before = self.defended_shot("Genji")
        for _ in range(200):
            run.step({})
        self.fire(run, shooter)
        self.assertEqual(self.health(run, defender).total, before - 120)

    def test_allied_deflector_does_not_intercept_friendly_rocket(self):
        run, shooter, defender, behind = self.encounter("Genji")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        defender.team = shooter.team
        behind.team = 1 - shooter.team
        run.step({defender: (-32768, 0, 16)})
        for _ in range(5):
            run.step({defender: (-32768, 0, 0)})
        run.step({shooter: (0, 0, 1), defender: (-32768, 0, 0)})
        for _ in range(60):
            run.step({defender: (-32768, 0, 0)})
        self.assertEqual(self.health(run, behind).total, 80)
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(self.health(run, defender).total, 200)

    def test_static_wall_before_deflector_wins_without_redirect_or_blast_leak(self):
        run, shooter, defender, behind = self.encounter("Genji")
        run.step({defender: (-32768, 0, 16)})
        wall = primary.PharahCombatTests.wall(self, shooter.mover.position[1])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            run.step({shooter: (0, 0, 1), defender: (-32768, 0, 0)})
            for _ in range(60):
                run.step({defender: (-32768, 0, 0)})
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(self.health(run, defender).total, 200)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_reflected_concussion_keeps_zero_damage_and_physical_impulse(self):
        run, shooter, defender, behind = self.encounter("Genji")
        behind.mover.position[0] = 30
        behind.mover.body = None
        run.step({defender: (-32768, 0, 16)})
        for _ in range(5):
            run.step({defender: (-32768, 0, 0)})
        run.step({shooter: (0, 0, 16), defender: (-32768, 0, 0)})
        redirected = False
        for _ in range(60):
            run.step({defender: (-32768, 0, 0)})
            redirected |= any(r.shooter is defender for r in run.match.combat.rockets)
        self.assertTrue(redirected)
        self.assertLess(shooter.mover.position[2], -1)
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(self.health(run, defender).total, 200)
        self.assertFalse(defender.client.messages(25001))

    def test_reflected_barrage_kills_shooter_and_stops_new_ultimate_shots(self):
        run, shooter, defender, behind = self.encounter("Genji")
        behind.mover.position[0] = 30
        behind.mover.body = None
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({defender: (-32768, 0, 16)})
        for _ in range(5):
            run.step({defender: (-32768, 0, 0)})
        run.step({shooter: (0, 0, 32), defender: (-32768, 0, 0)})
        for _ in range(90):
            run.step({defender: (-32768, 0, 0)})
        self.assertTrue(self.health(run, shooter).dead)
        self.assertEqual(self.health(run, defender).total, 200)
        self.assertFalse(script.ultimate_running())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x2B)
        reported = instance.states[1].reported
        for _ in range(20):
            run.step({})
        self.assertEqual(instance.states[1].reported, reported)


if __name__ == "__main__":
    unittest.main()
