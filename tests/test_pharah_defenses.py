"""Actual defensive skill input versus Pharah attacks; phase policy is reconstructed."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_combat as model
import test_pharah_combat as primary
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import defenses, sigma_grasp
from ow174.game.collision import World
from ow174.game.combat import HIT, SHIELDS_POOL, TICK
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

    def launch_concussion(self, run, shooter):
        fight = run.match.combat
        run.step({shooter: (0, 0, 16)})
        for _ in range(15):
            if fight.rockets:
                break
            run.step({})
        self.assertEqual(len(fight.rockets), 1)
        return fight.rockets[0]

    @staticmethod
    def aim_at_body(fight, shooter, victim):
        target = next(target for target in fight._targets() if target.owner is victim)
        x, y, z = target.feet
        return aim(fight._eye(shooter), (x, y + target.height / 2, z))

    def test_barrage_passes_through_ally_but_hits_same_body_as_enemy(self):
        for allied in (True, False):
            with self.subTest(allied=allied):
                run, pharah, front, rear = self.encounter("Soldier: 76")
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                pharah.team = 0
                front.team = 0 if allied else 1
                rear.team = 1
                front.mover.position[2] = 4
                rear.mover.position[2] = 8
                front.mover.body = rear.mover.body = None
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                fight = run.match.combat
                yaw, pitch = self.aim_at_body(fight, pharah, front)
                run.step({pharah: (yaw, pitch, 32)})
                first, = fight.rockets
                for _ in range(45):
                    if all(rocket is not first for rocket in fight.rockets):
                        break
                    run.step({})
                self.assertTrue(all(rocket is not first for rocket in fight.rockets))
                hit, untouched = (rear, front) if allied else (front, rear)
                self.assertEqual(self.health(run, hit).total, 160)
                self.assertEqual(self.health(run, untouched).total, 200)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_barrage_passes_through_wraith_and_fade_until_they_expire(self):
        for name in ("Reaper", "Moira"):
            with self.subTest(hero=name):
                run, pharah, front, rear = self.encounter(name)
                self.cast(run, front)
                if name == "Reaper":
                    for _ in range(100):
                        run.step({})
                self.assertTrue(defenses.phase(front)[1])
                before = self.health(run, front).total
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                fight = run.match.combat
                run.step({pharah: (0, 0, 32)})
                first, = fight.rockets
                for _ in range(45):
                    if all(rocket is not first for rocket in fight.rockets):
                        break
                    run.step({})
                self.assertTrue(defenses.phase(front)[1])
                self.assertTrue(all(rocket is not first for rocket in fight.rockets))
                self.assertEqual(self.health(run, front).total, before)
                self.assertEqual(self.health(run, rear).total, 160)
                for _ in range(150):
                    run.step({})
                    if self.health(run, front).total < before:
                        break
                self.assertEqual(defenses.phase(front), (False, False))
                self.assertEqual(before - self.health(run, front).total, 40)
                self.assertTrue(script.ultimate_running())
                self.assertEqual(script.component.entity_var(342).value(), 0)

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

    def test_concussion_passes_through_phase_started_after_launch(self):
        for name in ("Reaper", "Moira"):
            with self.subTest(hero=name):
                run, shooter, defender, rear = self.encounter(name)
                fight = run.match.combat
                front_pose, rear_pose = tuple(defender.mover.position), tuple(rear.mover.position)
                front_health = self.health(run, defender).total
                self.assertEqual(defenses.phase(defender), (False, False))
                blast = self.launch_concussion(run, shooter)
                self.assertLess(blast.position[2], front_pose[2])
                run.step({defender: (-32768, 0, 8)})
                self.assertEqual(defenses.phase(defender), (True, True))
                self.assertIn(blast, fight.rockets)
                crossed = False
                for _ in range(35):
                    if blast not in fight.rockets:
                        break
                    crossed |= blast.position[2] > front_pose[2]
                    run.step({})
                self.assertTrue(crossed)
                self.assertNotIn(blast, fight.rockets)
                self.assertEqual(defenses.phase(defender), (True, True))
                for _ in range(12):
                    run.step({})
                self.assertEqual(tuple(defender.mover.position), front_pose)
                self.assertGreater(rear.mover.position[2], rear_pose[2] + 1)
                self.assertEqual(self.health(run, defender).total, front_health)
                self.assertEqual(self.health(run, rear).total, 200)
                self.assertFalse(shooter.client.messages(HIT))
                self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_concussion_in_flight_hits_front_body_after_phase_expiry(self):
        for name in ("Reaper", "Moira"):
            with self.subTest(hero=name):
                control, _, phase_owner, _ = self.encounter(name)
                self.cast(control, phase_owner)
                remaining = 0
                while defenses.phase(phase_owner)[0] and remaining < 240:
                    control.step({})
                    remaining += 1
                self.assertFalse(defenses.phase(phase_owner)[0])
                self.assertGreater(remaining, 16)

                run, shooter, defender, rear = self.encounter(name)
                defender.mover.position[2] = 20
                defender.mover.body = None
                rear.mover.position[2] = 31
                rear.mover.body = None
                self.cast(run, defender)
                front_z = defender.mover.position[2]
                front_health = self.health(run, defender).total
                for _ in range(remaining - 16):
                    run.step({})
                self.assertEqual(defenses.phase(defender), (True, True))
                fight = run.match.combat
                blast = self.launch_concussion(run, shooter)
                self.assertEqual(defenses.phase(defender), (True, True))
                for _ in range(16):
                    if not defenses.phase(defender)[0]:
                        break
                    self.assertIn(blast, fight.rockets)
                    run.step({})
                self.assertEqual(defenses.phase(defender), (False, False))
                self.assertIn(blast, fight.rockets)
                self.assertLess(blast.position[2], front_z)
                for _ in range(40):
                    run.step({})
                self.assertNotIn(blast, fight.rockets)
                self.assertGreater(defender.mover.position[2], front_z + 1)
                self.assertEqual(rear.mover.position[2], 31)
                self.assertEqual(self.health(run, defender).total, front_health)
                self.assertEqual(self.health(run, rear).total, 200)
                self.assertFalse(shooter.client.messages(HIT))
                self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

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

    def test_barrage_hits_mei_only_after_cryo_freeze_expires(self):
        run, pharah, mei, rear = self.encounter("Mei")
        self.cast(run, mei)
        for _ in range(140):
            run.step({})
        self.assertEqual(defenses.phase(mei), (True, False))
        health = self.health(run, mei)
        before = health.total
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        mei.mover.position[2] = 4
        mei.mover.body = None
        yaw, pitch = self.aim_at_body(fight, pharah, mei)
        run.step({pharah: (yaw, pitch, 32)})
        first, = fight.rockets
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(35):
                if all(rocket is not first for rocket in fight.rockets):
                    break
                run.step({})
        self.assertTrue(any(call.args[1].owner is mei and call.args[2] == 10
                            for call in damage.call_args_list))
        self.assertTrue(all(rocket is not first for rocket in fight.rockets))
        self.assertEqual(defenses.phase(mei), (True, False))
        self.assertEqual(health.total, before)
        self.assertEqual(self.health(run, rear).total, 200)
        self.assertFalse(pharah.client.messages(25001))
        for _ in range(135):
            run.step({})
            if health.total < before:
                break
        self.assertEqual(defenses.phase(mei), (False, False))
        self.assertEqual(health.total, before - 40)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(self.health(run, rear).total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

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

    def test_genji_redirects_soldier_hitscan_toward_pharah_unless_cover_wins(self):
        by_name = {hero.name: hero for hero in heroes().values()}
        for covered in (False, True):
            with self.subTest(covered=covered):
                run, soldier, genji, pharah = self.encounter("Genji")
                match = run.match
                match.switch_hero(soldier, by_name["Soldier: 76"])
                match.switch_hero(pharah, by_name["Pharah"])
                match.game_map = replace(match.game_map, free_for_all=False)
                soldier.team = pharah.team = 0
                genji.team = 1
                y = genji.mover.position[1]
                soldier.mover.position = [0, y, 0]
                pharah.mover.position = [1.25, y, 1]
                soldier.mover.body = pharah.mover.body = None
                for _ in range(30):
                    run.step({})
                fight = match.combat
                yaw, pitch = self.aim_at_body(fight, genji, pharah)
                run.step({genji: (yaw, pitch, 16)})
                for _ in range(5):
                    run.step({genji: (yaw, pitch, 0)})
                collision = primary.PharahCombatTests.wall(self, y) if covered else fight._collision()
                with patch.object(fight, "_collision", return_value=collision):
                    run.step({soldier: (0, 0, 1), genji: (yaw, pitch, 0)})
                    for _ in range(5):
                        run.step({genji: (yaw, pitch, 0)})
                self.assertEqual(self.health(run, soldier).total, 200)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertEqual(self.health(run, pharah).total, 200 if covered else 180)

    def test_reflected_primary_applies_source_impulse_only_to_the_reflecting_body(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                run, shooter, defender, _ = self.encounter("Genji")
                fight = run.match.combat
                facing = (-32768, 0, 0)
                run.step({defender: (-32768, 0, 16)})
                for _ in range(5):
                    run.step({defender: facing})
                run.step({shooter: (0, 0, 1), defender: facing})
                for _ in range(24):
                    if fight.rockets and fight.rockets[0].shooter is defender:
                        break
                    run.step({defender: facing})
                reflected, = fight.rockets
                self.assertIs(reflected.shooter, defender)
                wall_z = reflected.position[2] - 0.125
                if replace_body:
                    soldier = next(hero for hero in heroes().values() if hero.name == "Soldier: 76")
                    run.match.switch_hero(defender, soldier)
                defender.mover.position = [0.0, shooter.mover.position[1], wall_z + 0.5]
                defender.mover.body = None
                y = defender.mover.position[1]
                wall = World([
                    (-10, y - 5, wall_z, 10, y - 5, wall_z, 10, y + 5, wall_z),
                    (-10, y - 5, wall_z, 10, y + 5, wall_z, -10, y + 5, wall_z),
                ])
                mover = defender.mover
                with (patch.object(fight, "_collision", return_value=wall),
                      patch.object(mover, "add_impulse", wraps=mover.add_impulse) as impulse):
                    for _ in range(6):
                        run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, shooter).total, 200)
                if replace_body:
                    impulse.assert_not_called()
                else:
                    self.assertAlmostEqual(self.health(run, defender).total, 160)
                    impulse.assert_called_once()
                    vx, vy, vz = impulse.call_args.args[0]
                    self.assertAlmostEqual(vx, 0.0, places=5)
                    self.assertAlmostEqual(vy, 12.0 * 0.2 / math.hypot(0.5, 0.2), places=5)
                    self.assertAlmostEqual(vz, 7.5, places=5)

    def test_aimed_defense_boxes_intercept_elevated_primary_only_when_pitched_up(self):
        for name, lift, button in (("Genji", 1.0, 16), ("Sigma", 2.35, 8)):
            for pitch in (0, -8192):
                with self.subTest(hero=name, pitch=pitch):
                    run, pharah, defender, _ = self.encounter(name)
                    facing = (-32768, pitch, 0)
                    run.step({defender: (-32768, pitch, button)})
                    for _ in range(5):
                        run.step({defender: facing})
                    pharah.mover.position[1] += lift
                    pharah.mover.body = None
                    run.step({pharah: (0, 0, 1), defender: facing})
                    fight = run.match.combat
                    outgoing, = fight.rockets
                    for _ in range(22):
                        run.step({defender: facing})
                        if all(rocket is not outgoing for rocket in fight.rockets):
                            break
                    intercepted = all(rocket is not outgoing for rocket in fight.rockets)
                    self.assertEqual(intercepted, bool(pitch))
                    if name == "Genji" and pitch:
                        reflected, = fight.rockets
                        self.assertIs(reflected.shooter, defender)
                    if name == "Sigma":
                        instance = next(i for i in defender.body_script.component.instances.values()
                                        if i.graph.index == sigma_grasp.GRASP)
                        self.assertEqual(instance.vars[21312].value(), 120 if pitch else 0)

    def test_primary_deflect_includes_authored_projectile_extent(self):
        for offset, intercepted in ((.949, True), (.951, False)):
            with self.subTest(offset=offset):
                run, pharah, genji, _ = self.encounter("Genji")
                pharah.mover.position[0] = offset
                pharah.mover.body = None
                facing = {genji: (-32768, 0, 0)}
                run.step({genji: (-32768, 0, 16)})
                for _ in range(5):
                    run.step(facing)
                fight = run.match.combat
                run.step({**facing, pharah: (0, 0, 1)})
                for _ in range(22):
                    if any(rocket.shooter is genji for rocket in fight.rockets):
                        break
                    run.step(facing)
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, genji if intercepted else pharah)

    def test_genji_reflects_an_in_flight_rocket_after_pharah_dies(self):
        for switch in (False, True):
            with self.subTest(switch=switch):
                self.postmortem_reflect(switch)

    def postmortem_reflect(self, switch):
        run, pharah, genji, ana = self.encounter("Genji")
        by_name = {hero.name: hero for hero in heroes().values()}
        self.assertTrue(run.match.switch_hero(ana, by_name["Ana"]))
        y = pharah.mover.position[1]
        genji.mover.position = [0, y, 24]
        ana.mover.position = [4, y, 0]
        genji.mover.body = ana.mover.body = None
        fight = run.match.combat
        self.health(run, pharah).damage(199)
        aim_dart = aim(fight._eye(ana), fight._eye(pharah))
        run.step({pharah: (0, 0, 1), ana: (*aim_dart, 8)})
        original, = [rocket for rocket in fight.rockets if rocket.shooter is pharah]
        for _ in range(38):
            if fight.is_dead(pharah):
                break
            run.step({ana: (*aim_dart, 0)})
        self.assertTrue(fight.is_dead(pharah))
        self.assertTrue(any(rocket is original for rocket in fight.rockets))
        self.assertEqual(self.health(run, genji).total, 200)
        ana.mover.position = [0, y, -4]
        ana.mover.body = None
        before = self.health(run, ana).total
        run.step({genji: (-32768, 0, 16)})
        for _ in range(45):
            if any(rocket.shooter is genji for rocket in fight.rockets):
                break
            run.step({genji: (-32768, 0, 0)})
        self.assertTrue(any(rocket.shooter is genji for rocket in fight.rockets))
        if switch:
            old_body = genji.body
            self.assertTrue(run.match.switch_hero(genji, by_name["Pharah"]))
            self.assertNotEqual(genji.body, old_body)
            genji.body_script.ult_seconds = 0
            genji.body_script.set_ult_charge(0)
            genji.mover.position[0] = 30
            genji.mover.body = None
        for _ in range(70):
            run.step({})
        self.assertEqual(self.health(run, ana).total, before - 120)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0 if switch else 120)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_two_actual_deflects_credit_only_the_final_reflecting_body(self):
        run = Run(KANEZAKA_DM, ("Pharah", "Genji One", "Genji Two", "Enemy"))
        pharah, first, second, victim = run.players
        by_name = {hero.name: hero for hero in heroes().values()}
        for player, name in ((pharah, "Pharah"), (first, "Genji"), (second, "Genji")):
            self.assertTrue(run.match.switch_hero(player, by_name[name]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        floor = pharah.mover.position[1]
        for player, x, z in ((pharah, 0, 0), (first, 0, 8), (second, 0, -8), (victim, 30, -2)):
            player.mover.position = [x, floor, z]
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(15):
            run.step({})
        run.step({first: (-32768, 0, 16), second: (0, 0, 16)})
        facing = {first: (-32768, 0, 0), second: (0, 0, 0)}
        for _ in range(5):
            run.step(facing)
        fight = run.match.combat
        run.step({**facing, pharah: (0, 0, 1)})
        original, = [rocket for rocket in fight.rockets if rocket.shooter is pharah]
        pharah.mover.position[0] = 30
        pharah.mover.body = None
        owners = []
        for _ in range(70):
            run.step(facing)
            if not fight.rockets:
                break
            rocket, = fight.rockets
            if rocket is original:
                continue
            if not owners or rocket.shooter is not owners[-1]:
                owners.append(rocket.shooter)
                self.assertEqual(rocket.reflected_by, rocket.shooter.body)
            if rocket.shooter is second:
                victim.mover.position[0] = 0
                victim.mover.body = None
                break
        self.assertEqual(owners, [first, second])
        for _ in range(35):
            run.step(facing)
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(run, victim).total, 80)
        for actor in (pharah, first, second):
            self.assertEqual(self.health(run, actor).total, 200)
            self.assertEqual(actor.body_script.component.entity_var(342).value(),
                             120 if actor is second else 0)

    def test_grasp_consumes_rockets_after_genji_changes_their_team(self):
        for button, credited in ((1, 120), (16, 0)):
            with self.subTest(button=button):
                self.assert_grasp_consumes_reflected_rocket(button, credited)

    def assert_grasp_consumes_reflected_rocket(self, button, credited):
        run, pharah, genji, sigma = self.encounter("Genji")
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sigma.team = 0
        genji.team = 1
        sigma.mover.position = [0, pharah.mover.position[1], -1]
        sigma.mover.body = None
        for player in (pharah, genji, sigma):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        fight = run.match.combat
        run.step({genji: (-32768, 0, 16), sigma: (0, 0, 8)})
        for _ in range(7):
            run.step({genji: (-32768, 0, 0)})
        actors = pharah, genji, sigma
        positions = [tuple(player.mover.position) for player in actors]
        base = self.health(run, sigma).total
        facing = {genji: (-32768, 0, 0)}
        run.step({**facing, pharah: (0, 0, button)})
        launched = False
        reflected = False
        for _ in range(45):
            launched |= any(rocket.shooter is pharah for rocket in fight.rockets)
            reflected |= any(rocket.shooter is genji for rocket in fight.rockets)
            run.step(facing)
        self.assertTrue(launched)
        self.assertTrue(reflected)
        self.assertFalse(fight.rockets)
        self.assertEqual([tuple(player.mover.position) for player in actors], positions)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, sigma).total, base)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(instance.vars[21312].value(), credited)
        for player in actors:
            self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
            self.assertFalse(player.client.messages(25001))
        if not credited:
            for _ in range(155):
                run.step(facing)
            self.assertNotIn(sigma.body, fight.grasp_shields)

    def test_sigma_grasp_consumes_real_rocket_without_blast_damage_or_charge(self):
        run, shooter, defender, behind, before = self.defended_shot("Sigma")
        self.assertEqual(self.health(run, defender).total, before)
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(instance.vars[21312].value(), 120)

    def sigma_after_grasp(self):
        run, shooter, defender, _, before = self.defended_shot("Sigma")
        fight = run.match.combat
        for _ in range(160):
            if defender.body in fight.grasp_shields:
                break
            run.step({})
        vitals = self.health(run, defender)
        self.assertEqual(vitals.total, before + 72)
        self.assertEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        return run, shooter, defender, vitals

    def test_sigma_grasp_expiry_grants_shield_spent_before_base_shields(self):
        run, shooter, _, vitals = self.sigma_after_grasp()
        self.fire(run, shooter)
        self.assertEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 0)
        self.assertEqual(vitals.now[SHIELDS_POOL], 52)
        self.assertEqual(vitals.total, 352)
        self.assertEqual(vitals.maximum, 400)

    def test_sigma_grasp_shield_has_grace_and_decay(self):
        run, _, defender, vitals = self.sigma_after_grasp()
        fight = run.match.combat
        decay = fight.grasp_shields[defender.body]
        run.frame = int(decay.starts / TICK)
        run.match.update(run.frame * TICK, run.frame)
        self.assertEqual(vitals.total, 472)
        run.frame += 125
        run.match.update(run.frame * TICK, run.frame)
        self.assertAlmostEqual(vitals.total, 462, delta=.081)
        run.frame += 1000
        run.match.update(run.frame * TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertNotIn(defender.body, fight.grasp_shields)

    def test_sigma_grasp_consumes_concussion_without_shield_or_impulse(self):
        run, shooter, defender, behind = self.encounter("Sigma")
        positions = [tuple(player.mover.position) for player in (defender, behind)]
        facing = {defender: (-32768, 0, 0)}
        run.step({defender: (-32768, 0, 8)})
        for _ in range(5):
            run.step(facing)
        run.step({**facing, shooter: (0, 0, 16)})
        for _ in range(185):
            run.step(facing)
        self.assertEqual([tuple(player.mover.position) for player in (defender, behind)], positions)
        self.assertEqual(self.health(run, defender).total, 400)
        self.assertNotIn(defender.body, run.match.combat.grasp_shields)
        self.assertFalse(run.match.combat.rockets)

    def test_sigma_grasp_absorbs_barrage_then_later_rockets_spend_granted_shield(self):
        run, shooter, defender, behind = self.encounter("Sigma")
        facing = {defender: (-32768, 2048, 0)}
        run.step({defender: (-32768, 2048, 8)})
        for _ in range(5):
            run.step(facing)
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({**facing, shooter: (0, 0, 32)})
        for _ in range(18):
            run.step(facing)
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertGreater(instance.vars[21312].value(), 0)
        self.assertEqual(self.health(run, defender).total, 400)
        self.assertEqual(self.health(run, behind).total, 200)

        fight = run.match.combat
        vitals = self.health(run, defender)
        for _ in range(160):
            if defender.body in fight.grasp_shields:
                break
            run.step(facing)
        self.assertIn(defender.body, fight.grasp_shields)
        temporary = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
        before = temporary.now
        self.assertGreater(before, 40)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        self.assertTrue(script.ultimate_running())
        for _ in range(20):
            run.step(facing)
            if temporary.now < before:
                break
        self.assertEqual(temporary.now, before - 40)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        self.assertEqual(self.health(run, behind).total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

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

    def test_barrage_keeps_firing_and_hits_after_genji_deflect_expires(self):
        run, pharah, genji, rear = self.encounter("Genji")
        rear.mover.position[0] = 30
        rear.mover.body = None
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        facing = {genji: (-32768, 0, 0)}
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step(facing)
        deflect = next(state for instance in genji.body_script.component.instances.values()
                       if instance.graph.index == 0x03A3 for state in instance.states.values()
                       if state.node.cls == "STUStatescriptStateDeflectProjectiles" and state.active)
        fight = run.match.combat
        run.step({**facing, pharah: (0, 0, 32)})
        for _ in range(25):
            if any(rocket.shooter is genji for rocket in fight.rockets):
                break
            run.step(facing)
        self.assertTrue(any(rocket.shooter is genji for rocket in fight.rockets))
        self.assertEqual(self.health(run, genji).total, 200)
        pharah.mover.position[0] = 4
        pharah.mover.body = None
        # Keep later curved rounds out of the defense box until its actual expiry.
        for _ in range(145):
            if not deflect.active:
                break
            run.step({**facing, pharah: (-16384, 0, 0)})
        self.assertFalse(deflect.active)
        before = self.health(run, genji).total
        yaw, pitch = self.aim_at_body(fight, pharah, genji)
        for _ in range(65):
            run.step({**facing, pharah: (yaw, pitch, 0)})
            if self.health(run, genji).total < before:
                break
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertFalse(deflect.active)
        self.assertLess(self.health(run, genji).total, before)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_barrage_deflect_restarts_native_curve_and_flight_bound(self):
        run, pharah, genji, rear = self.encounter("Genji")
        rear.mover.position[0] = 30
        rear.mover.body = None
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        facing = {genji: (-32768, 0, 0), pharah: (0, 0, 0x80)}
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step(facing)

        fight = run.match.combat
        run.step({**facing, pharah: (0, 0, 32 | 0x80)})
        original, = fight.rockets
        launch_bound = original.left
        delayed = replace(original, last_tick=fight.tick)
        for _ in range(25):
            run.step(facing)
            reflected = next((rocket for rocket in fight.rockets if rocket.shooter is genji), None)
            if reflected is not None:
                break
        self.assertIsNotNone(reflected)
        self.assertGreater(original.flown, 0.05)
        self.assertEqual(reflected.motion, original.motion)
        self.assertAlmostEqual(reflected.flown, 0.003500483, delta=1e-7)
        self.assertAlmostEqual(reflected.left, launch_bound - reflected.flown)
        self.assertEqual(launch_bound, 5)
        self.assertEqual(reflected.range_limit, 100)
        self.assertGreater(original.distance_flown, 6)
        self.assertLess(reflected.distance_flown, .2)
        x, y, z = genji.mover.position
        origin = (x, y + 1.2 + genji.mover.spring, z)
        # Original reflection dispatch, partial tick and next full tick from the eye.
        for actual, expected in zip(
            reflected.direction, (0.038900770, 0.057840325, -0.997567654), strict=True,
        ):
            self.assertAlmostEqual(actual, expected, delta=2e-7)
        for index, offsets in enumerate((
            (0.006456444, 0.033064133, -0.155736940),
            (0.033984999, 0.178804098, -0.867968813),
        )):
            if index:
                run.step(facing)
            self.assertIn(reflected, fight.rockets)
            for before, after, expected in zip(origin, reflected.position, offsets, strict=True):
                self.assertAlmostEqual(after - before, expected, delta=2e-6)

        before_catchup = len(fight.rockets)
        fight._fly(delayed)
        caught, = fight.rockets[before_catchup:]
        self.assertIs(caught.shooter, genji)
        self.assertEqual(caught.last_tick, reflected.last_tick)
        self.assertAlmostEqual(caught.flown, reflected.flown, delta=2e-7)
        for actual, expected in zip(caught.position, reflected.position, strict=True):
            self.assertAlmostEqual(actual, expected, delta=2e-6)

    def test_barrage_deflect_uses_raw_aim_after_launch_body_is_replaced(self):
        run, pharah, genji, rear = self.encounter("Genji")
        rear.mover.position[0] = 30
        rear.mover.body = None
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        facing = {genji: (-32768, 0, 0), pharah: (0, 0, 0x80)}
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step(facing)
        fight = run.match.combat
        run.step({**facing, pharah: (0, 0, 32 | 0x80)})
        original, = fight.rockets
        original_body = pharah.body
        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, original_body)
        self.assertIn(original, fight.rockets)
        for _ in range(25):
            run.step(facing)
            reflected = next((rocket for rocket in fight.rockets if rocket.shooter is genji), None)
            if reflected is not None:
                break
        self.assertIsNotNone(reflected)
        self.assertAlmostEqual(reflected.direction[0], 0)
        self.assertEqual(reflected.direction[1:], (0, -1))
        self.assertEqual(reflected.motion, original.motion)

    def test_barrage_reflected_from_crouched_eye_hits_cover_inside_deflect_box(self):
        run, pharah, genji, rear = self.encounter("Genji")
        rear.mover.position[0] = 30
        rear.mover.body = None
        crouch = {genji: (-32768, 0, 0x80)}
        for _ in range(20):
            run.step(crouch)
        run.step({genji: (-32768, 0, 16 | 0x80)})
        for _ in range(5):
            run.step(crouch)
        self.assertTrue(genji.mover.crouched)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        x, y, z = genji.mover.position
        wall_z = z - .15
        wall = World([
            (x-2, y-2, wall_z, x+2, y-2, wall_z, x+2, y+1.0, wall_z),
            (x-2, y-2, wall_z, x+2, y+1.0, wall_z, x-2, y+1.0, wall_z),
        ])
        fight = run.match.combat
        with (patch.object(fight, "_collision", return_value=wall),
              patch.object(fight, "_landed", wraps=fight._landed) as landed):
            run.step({**crouch, pharah: (0, 0, 32)})
            for _ in range(25):
                landed.reset_mock()
                run.step(crouch)
                ended = [call.args[0] for call in landed.call_args_list]
                reflected = next((rocket for rocket in ended if rocket.shooter is genji), None)
                if reflected is not None:
                    break
            self.assertIsNotNone(reflected)
            self.assertTrue(any(
                rocket.shooter is pharah and rocket.motion == reflected.motion for rocket in ended
            ))
            self.assertNotIn(reflected, fight.rockets)
            self.assertEqual((reflected.position[0], reflected.position[2]), (x, z))
            self.assertAlmostEqual(reflected.position[1] - y, .84 + genji.mover.spring, delta=2e-6)
            self.assertGreater(reflected.flown, 0)
            self.assertLess(reflected.flown, TICK)
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_reflected_barrage_kills_shooter_and_stops_new_ultimate_shots(self):
        run, shooter, defender, behind = self.encounter("Genji")
        defender.mover.position[2] = 4
        defender.mover.body = None
        behind.mover.position[0] = 30
        behind.mover.body = None
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({defender: (-32768, 0, 16)})
        for _ in range(5):
            run.step({defender: (-32768, 0, 0)})
        fight = run.match.combat
        yaw, pitch = self.aim_at_body(fight, shooter, defender)
        run.step({shooter: (yaw, pitch, 32), defender: (-32768, 0, 0)})
        for _ in range(90):
            run.step({shooter: (yaw, pitch, 0), defender: (-32768, 0, 0)})
            if self.health(run, shooter).dead:
                break
        self.assertTrue(self.health(run, shooter).dead)
        self.assertEqual(self.health(run, shooter).killer, defender.body)
        self.assertTrue(defender.client.messages(25001))
        self.assertEqual(self.health(run, defender).total, 200)
        self.assertFalse(script.ultimate_running())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x2B)
        reported = instance.states[1].reported
        for _ in range(20):
            run.step({})
        self.assertEqual(instance.states[1].reported, reported)


if __name__ == "__main__":
    unittest.main()
