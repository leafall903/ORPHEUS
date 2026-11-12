"""D.Va pilot combat from actual Match command admission and authored pistol graphs.

This accepts only the existing pilot body. Initial mech creation/possession is
not implemented here, and no original-client presentation is claimed.
"""

import math
import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input
from test_game_mover import box

from ow174.game import bots, dva, projectiles, weapon_aim
from ow174.game.collision import World
from ow174.game.combat import Vitals, ray_capsule
from ow174.game.content import find_hero
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE


class PilotTests(unittest.TestCase):
    def scenario(self, npc=False, distance=3.0):
        run = Run(KANEZAKA_DM, ("Pilot", "Victim"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        pilot, victim = run.players
        match.switch_hero(pilot, find_hero("dva"))
        dva_pilot_input(pilot)
        pilot.mover.position = [0.0, 1.0, 0.0]
        victim.mover.position = [0.0, 1.0, distance]
        target_actor = victim
        if npc:
            victim.spawned = False
            target_actor = bots.Bot(bots.BOT_ENTITY, {
                "hero": f"0x{victim.hero.guid:X}", "team": 1,
                "position": (0.0, 1.0, distance), "rotation": (0, 0, 0, 1), "graphs": [],
            }, {"name": "Pilot target", "body": "0x0400000000001403", "health": 200}, {})
            match.bots.append(target_actor)
        for _ in range(100):
            run.step({})
        fight = match.combat
        target = next(body for body in fight._targets() if body.owner is target_actor)
        return run, pilot, target, fight.vitals_of(target)

    def step(self, run, pilot, frames, buttons=0, yaw=0):
        for _ in range(frames):
            run.step({pilot: (yaw, 0, buttons)})

    def test_primary_press_launches_authored_projectile_and_deals_fourteen_damage(self):
        for npc in (False, True):
            run, pilot, _, vitals = self.scenario(npc)
            self.step(run, pilot, 1, 1)
            self.assertEqual(vitals.total, 200)
            self.assertEqual(len(run.match.combat.rockets), 1)
            self.step(run, pilot, 8)
            self.assertEqual(vitals.total, 186)
            weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
            self.assertEqual(weapon.vars[53].value(), 19)

    def test_light_gun_scores_torso_or_critical_damage_at_projectile_impact(self):
        from ow174.game.combat import CRITICAL, HIT

        for critical in (False, True):
            with self.subTest(critical=critical):
                run, pilot, target, health = self.scenario()
                fight = run.match.combat
                pilot.body_script.ult_seconds = 0
                pilot.body_script.set_ult_charge(0)
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                x, y, z = target.feet
                yaw, pitch = aim(fight._eye(pilot), (x, y + (target.eye if critical else 1.0), z))
                before = health.total
                run.step({pilot: (yaw, pitch, 1)})
                self.assertEqual(health.total, before)
                self.step(run, pilot, 8)
                expected = 28 if critical else 14
                self.assertEqual(before - health.total, expected)
                self.assertEqual(charge.value(), expected)
                hit, = pilot.client.messages(HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], expected)
                self.assertEqual(hit["+0x78"]["+0x8"], CRITICAL if critical else 0)

    def test_light_gun_bakes_normalized_spread_with_zero_launch_cone(self):
        run, pilot, _, health = self.scenario()
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x02B8)
        self.assertEqual(weapon.vars[231].value(), 0)
        self.step(run, pilot, 1, 1)
        volley = weapon.state(5)
        launch = volley.node.field("m_5EC1EB38")
        self.assertEqual(launch["$"], "STU_239B571E")
        self.assertIsNone(launch["m_AC20B1C1"])
        self.assertIsNone(launch["m_23C72C36"])
        muzzle, = launch["m_muzzles"]
        self.assertEqual(muzzle["m_entityHandle"]["m_identifier"], 49)
        self.assertEqual(muzzle["m_entityHandle"]["m_60DB8F99"], 1)
        self.assertEqual(volley.evaluate(muzzle["m_hardPoint"], watch=False).guid, 0x0DC0000000000006)
        handle = volley.evaluate(muzzle["m_entityHandle"], watch=False)
        self.assertIsInstance(handle, expr.Handle)
        cosmetic = weapon.component.instances[handle.instance].states[handle.state]
        self.assertTrue(cosmetic.active)
        self.assertEqual((cosmetic.node.pos, cosmetic.node.cls), (71, "STUStatescriptStateCosmeticEntity"))
        self.assertEqual(cosmetic.evaluate(cosmetic.node.field("m_B5E79B82"), watch=False).guid,
                         0x0D8000000000376A)
        self.assertEqual(cosmetic.evaluate(cosmetic.node.field("m_E591AF01"), watch=False).guid,
                         0x0DC000000000000A)
        self.assertEqual(volley.output(2), 1)
        self.assertEqual(volley.spread_ramp, 0)
        self.assertGreater(weapon.vars[231].value(), 0)
        for endpoint in ("m_7F0FED02", "m_429A6C85"):
            self.assertEqual(volley.evaluate(launch[endpoint], watch=False), 0)
        rocket, = run.match.combat.rockets
        self.assertEqual(rocket.direction, (0, 0, 1))
        self.assertEqual(rocket.position, run.match.combat._eye(pilot))
        self.step(run, pilot, 8)
        self.assertEqual(health.total, 186)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(weapon.vars[231].value(), 1)
        delay = weapon.state(9)
        self.assertTrue(delay.active)
        self.assertEqual(delay.ms, 1000)
        self.step(run, pilot, 80)
        self.assertEqual(weapon.state(6).start, delay.start + delay.ms)
        self.assertEqual(weapon.vars[231].value(), 0)

    def test_light_gun_launch_quantizes_the_subframe_click_aim(self):
        from ow174.game.combat import TICK
        from ow174.game.combat import aim as shot_aim
        from ow174.game.commands import FIRE, SUBFRAME, Command

        run, pilot, _, _ = self.scenario()
        run.frame += 1
        run.match.update(run.frame * TICK, run.frame, frames=False)
        command = Command(run.frame, buttons=FIRE | SUBFRAME, subframe=128,
                          click_yaw=16384, click_pitch=16250)
        pilot.take_commands([command])
        rocket, = run.match.combat.rockets
        raw = shot_aim(*command.aim())
        expected = weapon_aim.normalize_direction(weapon_aim.quantized_direction(raw))
        self.assertGreater(math.dist(rocket.direction, raw), .01)
        self.assertEqual(rocket.direction, expected)
        self.assertAlmostEqual(rocket.direction[1], -1, delta=1e-7)

    def test_light_gun_graze_uses_authored_radius_and_preserves_near_cover(self):
        for case, extra, damage in (("graze", 0.12, 14), ("outside", 0.28, 0), ("cover", 0.12, 0)):
            with self.subTest(case=case):
                run, pilot, target, health = self.scenario()
                pilot.body_script.ult_seconds = 0
                pilot.body_script.set_ult_charge(0)
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                pilot.mover.position[0] = target.radius + extra
                pilot.mover.body = None
                fight = run.match.combat
                x, _, z = fight._eye(pilot)
                yaw, pitch = aim(fight._eye(pilot), (x, target.feet[1] + 1, z + 3))
                run.step({pilot: (yaw, pitch, 1)})
                shot, = fight.rockets
                self.assertEqual(shot.collision_radius, 0.25)
                a, b = target.segment()
                self.assertIsNone(ray_capsule(shot.position, shot.direction, a, b, target.radius))
                expanded = ray_capsule(shot.position, shot.direction, a, b, target.radius + 0.25)
                if case == "cover":
                    self.assertIsNotNone(expanded)
                    wall = shot.position[2] + shot.direction[2] * (expanded + 0.10)
                    run.match.collision_world = World([
                        (-2, 0, wall, 2, 0, wall, 2, 5, wall),
                        (-2, 0, wall, 2, 5, wall, -2, 5, wall),
                    ])
                self.step(run, pilot, 10)
                self.assertEqual(health.total, 200 - damage)
                self.assertEqual(charge.value(), damage)

    def test_light_gun_ordinary_contact_preempts_an_earlier_expanded_candidate(self):
        run, pilot, target, health = self.scenario()
        fight = run.match.combat
        self.step(run, pilot, 1, buttons=1)
        shot, = fight.rockets
        self.assertEqual(shot.direction, (0.0, 0.0, 1.0))
        self.assertEqual(shot.collision_radius, .25)

        x, y, z = shot.position
        feet_y = y - target.height * .5
        target.owner.mover.position[:] = (x, feet_y, z + .4 + target.radius)
        target.owner.mover.body = None
        lateral = target.radius + .08
        expanded_radius = target.radius + shot.collision_radius
        offset = math.sqrt(expanded_radius ** 2 - lateral ** 2)
        grazer = bots.Bot(bots.BOT_ENTITY, {
            "hero": f"0x{target.owner.hero.guid:X}", "team": target.team,
            "position": (x + lateral, feet_y, z + .04 + offset),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Grazing target", "body": f"0x{target.owner.hero.body:X}", "health": 200}, {})
        run.match.bots.append(grazer)

        narrow, grazing = fight.player_target(target.owner), next(
            body for body in fight._targets() if body.owner is grazer)
        hit = ray_capsule(shot.position, shot.direction, *narrow.segment(), narrow.radius)
        self.assertIsNone(ray_capsule(shot.position, shot.direction, *grazing.segment(), grazing.radius))
        expanded = ray_capsule(shot.position, shot.direction, *grazing.segment(), expanded_radius)
        self.assertAlmostEqual(hit, .4)
        self.assertAlmostEqual(expanded, .04)
        self.assertLess(expanded + shot.collision_radius, hit)

        self.step(run, pilot, 1)
        self.assertEqual(health.total, 186)
        self.assertEqual(fight.vitals_of(grazing).total, 200)

    def test_miss_cover_and_allied_target_take_no_damage(self):
        for case in ("miss", "cover", "ally"):
            run, pilot, target, vitals = self.scenario()
            if case == "cover":
                run.match.collision_world = World([
                    (-10, -10, 1, 10, -10, 1, 10, 10, 1),
                    (-10, -10, 1, 10, 10, 1, -10, 10, 1),
                ])
            elif case == "ally":
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                target.owner.team = pilot.team
            self.step(run, pilot, 1, 1, 16384 if case == "miss" else 0)
            self.step(run, pilot, 10)
            self.assertEqual(vitals.total, 200)
            self.assertEqual(pilot.client.messages(25001), [])

    def test_twenty_round_magazine_rejects_fire_during_authored_reload(self):
        run, pilot, target, _ = self.scenario()
        fight = run.match.combat
        vitals = fight.vitals[target.entity] = Vitals(target.entity, 5000)
        self.step(run, pilot, 200, 1)
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(vitals.total, 5000 - 20 * 14)
        self.step(run, pilot, 30, 1)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(vitals.total, 5000 - 20 * 14)
        self.step(run, pilot, 100)
        self.assertEqual(weapon.vars[53].value(), 20)
        self.step(run, pilot, 1, 1)
        self.step(run, pilot, 8)
        self.assertEqual(vitals.total, 5000 - 21 * 14)

    def test_light_gun_clips_flight_at_volley_range_before_lifetime(self):
        for distance, damage in ((99.0, 14), (101.0, 0)):
            with self.subTest(distance=distance):
                run, pilot, target, vitals = self.scenario()
                fight = run.match.combat
                run.match.collision_world = World(box(-20, 20, -1, 1, -20, 110))
                for player, z in ((pilot, 0.0), (target.owner, distance)):
                    player.mover.use(world=run.match.collision_world)
                    player.mover.position[:] = (0.0, 1.0, z)
                    player.mover.body = None
                run.step({})
                self.step(run, pilot, 1, 1)
                rocket, = fight.rockets
                self.assertEqual((rocket.speed, rocket.left), (50.0, 2.0))
                self.assertEqual(rocket.definition, dva.LIGHT_GUN_PROJECTILE)
                self.assertIsNone(rocket.shot)
                self.assertNotIn(rocket.definition, projectiles.definitions(pilot.body_script))
                self.assertFalse(fight.projectiles.shots)
                self.assertFalse(fight.projectiles.observed)
                self.step(run, pilot, 120)
                self.assertEqual(vitals.total, 200)
                self.assertIn(rocket, fight.rockets)
                self.step(run, pilot, 10)
                self.assertEqual(vitals.total, 200 - damage)
                self.assertFalse(fight.rockets)
                if not damage:
                    self.assertAlmostEqual(rocket.flown, 2.0)
                    self.assertAlmostEqual(rocket.position[2], 100.0, places=3)

    def test_repeated_input_empty_reload_cycles_resume_primary(self):
        run, pilot, target, _ = self.scenario()
        vitals = run.match.combat.vitals[target.entity] = Vitals(target.entity, 5000)
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
        for cycle in (1, 2):
            self.step(run, pilot, 200, 1)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertEqual(vitals.total, 5000-cycle*20*14)
            self.step(run, pilot, 30, 1)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertEqual(vitals.total, 5000-cycle*20*14)
            self.step(run, pilot, 100)
            self.assertEqual(weapon.vars[53].value(), 20)
        self.step(run, pilot, 1, 1)
        self.step(run, pilot, 8)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(vitals.total, 5000-41*14)

    def test_enemy_player_command_fire_damages_the_pilot(self):
        run, pilot, _, _ = self.scenario()
        enemy = run.players[1]
        fight = run.match.combat
        target = next(body for body in fight._targets() if body.owner is pilot)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 150)
        yaw, pitch = aim(fight._eye(enemy), (0.0, 1.8, 0.0))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertEqual(vitals.total, 130)

    def test_launched_light_gun_round_hits_after_pilot_death_without_charge(self):
        run, pilot, target, health = self.scenario(distance=25)
        fight = run.match.combat
        enemy = target.owner
        script = pilot.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        starting_charge = charge.value()
        pilot_health = fight.vitals[pilot.body] = Vitals(pilot.body, 5)
        before = health.total
        self.step(run, pilot, 1, 1)
        projectile, = fight.rockets
        self.assertIs(projectile.shooter, pilot)
        yaw, pitch = aim(fight._eye(enemy), (0, 2.1, 0))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(pilot_health.dead)
        self.assertEqual(pilot_health.killer, enemy.body)
        self.assertIn(projectile, fight.rockets)
        self.assertEqual(health.total, before)
        self.step(run, pilot, 60, 1)
        self.assertEqual(health.total, before - 14)
        self.assertFalse(fight.rockets)
        self.assertEqual(charge.value(), starting_charge)
        self.assertEqual(len(pilot.client.messages(25001)), 1)

    def test_death_prevents_new_shots_and_owner_replacement_removes_query_target(self):
        run, pilot, _, _ = self.scenario()
        script = pilot.body_script
        script.set_health(0, 150)
        self.step(run, pilot, 30, 1)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(pilot.client.messages(25001), [])
        original = pilot.body
        run.match.switch_hero(pilot, find_hero("soldier"))
        self.assertIsNone(script.world._lookup_body_owner(original))


if __name__ == "__main__":
    unittest.main()
