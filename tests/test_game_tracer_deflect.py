"""Actual Tracer/Genji input paths; reconstructed admission/redirect policy."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class TracerDeflectTests(unittest.TestCase):
    def arena(self, *, distance=5, frontal=True, activate_deflect=True):
        run, player, genji, _ = tracer_tests.TracerPistolTests().fight()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = player.mover.position
        genji.mover.position[:] = [x, y, z + distance]
        for actor in run.players:
            actor.body_script.ult_seconds = 0
        incoming = aim(run.match.combat._eye(player), (x, y + 1, z + distance))
        reflected = aim(run.match.combat._eye(genji), (x, y + 1, z)) if frontal else (0, 0)
        run.step({genji: (*reflected, 16 if activate_deflect else 0)})
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        active = instance.states.get(10)
        self.assertEqual(bool(active and active.active), activate_deflect)
        return run, player, genji, incoming, reflected, instance

    def health(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target)

    def test_actual_frontal_pair_is_reflected_with_defender_hit_and_damage_credit(self):
        run, player, genji, incoming, reflected, _ = self.arena()
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, player).total, 138)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 12)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 2)
        self.assertEqual(player.client.messages(combat.HIT), [])
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x16)
        self.assertEqual(weapon.vars[53].value(), 38)

    def test_rear_pair_still_hits_deflector(self):
        run, player, genji, incoming, reflected, _ = self.arena(frontal=False)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(self.health(run, genji).total, 188)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 12)

    def test_reflected_lethal_pair_credits_one_kill_and_clamps_actual_loss(self):
        run, player, genji, incoming, reflected, _ = self.arena()
        self.health(run, player).now[combat.HEALTH_POOL] = 5
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertTrue(self.health(run, player).dead)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(self.health(run, player).killer, genji.body)

    def test_frontal_melee_is_blocked_without_reflected_damage_or_charge(self):
        run, player, genji, incoming, reflected, _ = self.arena(distance=1.5)
        run.step({player: (*incoming, 0x800), genji: (*reflected, 16)})
        for _ in range(40):
            run.step({genji: (*reflected, 0)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_expiry_exposes_defender_and_cooldown_rejects_new_press(self):
        run, player, genji, incoming, reflected, instance = self.arena()
        for _ in range(140):
            run.step({genji: (*reflected, 0)})
        self.assertFalse(instance.states[10].active)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertFalse(instance.states[10].active)
        self.assertEqual(self.health(run, genji).total, 188)
        self.assertEqual(self.health(run, player).total, 150)

    def test_authored_cooldown_ends_and_new_input_can_reflect_again(self):
        run, player, genji, incoming, reflected, instance = self.arena()
        for _ in range(650):
            run.step({genji: (*reflected, 0)})
        self.assertFalse(instance.states[10].active)
        run.step({genji: (*reflected, 16)})
        self.assertTrue(instance.states[10].active)
        self.assertEqual(instance.states[10].counter, 2)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, player).total, 138)

    def test_reflection_keeps_total_range_instead_of_starting_a_new_hundred_metres(self):
        run, player, genji, incoming, reflected, _ = self.arena(distance=60)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_reflection_falloff_uses_both_legs_of_the_ray(self):
        run, player, genji, incoming, reflected, _ = self.arena(distance=10)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        loss = 150 - self.health(run, player).total
        # Each individual leg is below the12m full-damage limit; the complete
        # outgoing+return path is inside the authored12..20m falloff interval.
        self.assertGreater(loss, 3.6)
        self.assertLess(loss, 12)
        self.assertEqual(self.health(run, genji).total, 200)

    def test_explicit_cancel_ends_reflection_and_cooldown_rejects_another_press(self):
        run, player, genji, incoming, reflected, instance = self.arena()
        for frame in range(60):
            run.step({genji: (*reflected, 16 if frame in (25, 45) else 0)})
        self.assertFalse(instance.states[10].active)
        self.assertEqual(instance.states[10].counter, 1)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 0)})
        self.assertEqual(self.health(run, genji).total, 188)
        self.assertEqual(self.health(run, player).total, 150)

    def test_reflected_bullets_resolve_shields_and_armour_individually(self):
        run, player, genji, incoming, reflected, _ = self.arena()
        vitals = combat.Vitals(player.body, 150, armour=50, shields=5)
        run.match.combat.vitals[player.body] = vitals
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(vitals.total, 196.5)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 8.5)
        self.assertEqual(self.health(run, genji).total, 200)

    def test_replacing_deflecting_body_does_not_protect_replacement_hero(self):
        run, player, genji, incoming, reflected, _ = self.arena()
        body = genji.body
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000002]))
        genji.mover.position[:] = position
        self.assertNotEqual(genji.body, body)
        run.step({player: (*incoming, FIRE), genji: (*reflected, 0)})
        self.assertEqual(self.health(run, genji).total, 238)
        self.assertEqual(self.health(run, player).total, 150)

    def test_actual_rear_pistol_kill_removes_deflection(self):
        run, player, genji, incoming, reflected, _ = self.arena(frontal=False)
        self.health(run, genji).now[combat.HEALTH_POOL] = 11
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertTrue(self.health(run, genji).dead)
        target = next(t for t in run.match.combat._targets() if t.owner is genji)
        self.assertFalse(run.match.combat._genji_deflects(target, (0, 0, -1)))
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)

    def redirected(self, *, allied=False, wall=False, recall=False, blink=False):
        run = Run(KANEZAKA_DM, ("Tracer", "Genji", "Third"))
        player, genji, third = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[tracer_tests.TRACER])
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        if recall or blink:
            run.match.switch_hero(third, heroes()[tracer_tests.TRACER])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, genji.team, third.team = 0, 1, 1 if allied else 0
        x, y, z = player.mover.position
        genji.mover.position[:] = [x, y, z+5]
        third.mover.position[:] = [x+3, y, z+3]
        for actor in run.players:
            actor.body_script.ult_seconds = 0
        incoming = aim(run.match.combat._eye(player), (x, y+1, z+5))
        reflected = aim(run.match.combat._eye(genji), (x+3, y+1, z+3))
        if wall:
            # Only the redirected ray crosses this finite wall; the original
            # Tracer->Genji ray remains on x and is unobstructed.
            face = x+1.5
            triangles = [
                (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
                (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
                (face, y, z+2, face, y, z+5, face, y+4, z+5),
                (face, y, z+2, face, y+4, z+5, face, y+4, z+2),
                (face, y, z+2, face, y+4, z+5, face, y, z+5),
                (face, y, z+2, face, y+4, z+2, face, y+4, z+5),
            ]
            run.match.collision_world = World(triangles, name="Deflect redirect wall")
            for actor in run.players:
                actor.mover.use(world=run.match.collision_world)
        run.step({genji: (*reflected, 16)})
        if recall or blink:
            run.step({third: (0, 0, 16 if recall else 8), genji: (*reflected, 16)})
        run.step({player: (*incoming, FIRE), genji: (*reflected, 16)})
        return run, player, genji, third

    def test_redirected_pair_uses_defender_aim_team_and_damage_credit(self):
        run, player, genji, third = self.redirected()
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, third).total, 188)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 12)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)

    def test_redirected_pair_cannot_damage_defender_ally(self):
        run, player, genji, third = self.redirected(allied=True)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, third).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_wall_blocks_redirected_pair_after_deflect(self):
        run, player, genji, third = self.redirected(wall=True)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, third).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_recall_blocks_redirected_pair_then_damage_resumes(self):
        run, player, genji, third = self.redirected(recall=True)
        self.assertEqual(self.health(run, third).total, 150)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(run, genji).total, 200)
        from ow174.game import tracer

        self.assertTrue(tracer.recalling(third))
        for _ in range(100):
            if not tracer.recalling(third):
                break
            run.step({genji: (genji.mover.yaw, genji.mover.pitch, 0)})
        self.assertFalse(tracer.recalling(third))
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(instance.states[10].active)
        incoming = aim(run.match.combat._eye(player),
                       (genji.mover.position[0], genji.mover.position[1]+1, genji.mover.position[2]))
        run.step({player: (*incoming, FIRE), genji: (genji.mover.yaw, genji.mover.pitch, 0)})
        self.assertEqual(self.health(run, third).total, 138)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 12)

    def test_blink_moves_redirected_target_out_of_defenders_aim(self):
        run, player, genji, third = self.redirected(blink=True)
        self.assertEqual(self.health(run, third).total, 150)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        blink = next(i for i in third.body_script.component.instances.values() if i.graph.index == 0x24)
        self.assertEqual(blink.vars[11].value(), 2)


if __name__ == "__main__":
    unittest.main()
