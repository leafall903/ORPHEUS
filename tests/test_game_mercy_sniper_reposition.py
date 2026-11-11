"""Actual Sniper MoveTo with Mercy as the opposing body."""

import math
import unittest
from unittest.mock import Mock

import test_game_mercy_npc_geometry as geometry
from test_game_combat import aim, in_world
from test_game_mercy_blaster_critical import equip, shot
from test_game_mover import FLOOR, box, wall

from ow174.game import combat, npc, npc_control, sniper_reposition
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.mover import quantise

ANA = 0x02E000000000013B


class MercySniperRepositionTests(unittest.TestCase):
    def encounter(self):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.name == "Sniper")
        run, mercy, bot, target = fixture.spawn(definition, 1)
        bot.position = (0, 1, 2)
        fixture.tick(run, mercy, bot, 10, buttons=0)
        return run, mercy, bot, target, fixture

    def test_authored_move_pauses_primary_then_resumes_and_recovers(self):
        run, mercy, bot, _, fixture = self.encounter()
        match = run.match
        fight = match.combat
        mercy.client.bot_stream = Mock(return_value=None)
        npc_control.start(match, mercy)
        self.assertEqual(npc_control.reposition(match, mercy), bot.entity)
        state = sniper_reposition.move_state(bot)
        destination, speed = state.move_request()
        self.assertEqual((state.instance.graph.index, state.node.pos, speed), (0x142C, 16, 20))
        self.assertAlmostEqual(destination.z, 6)
        self.assertFalse(sniper_reposition.request(match, bot, mercy))

        health = fight.vitals[mercy.body]
        run.step({})
        self.assertEqual(bot.position[2], quantise(2.32))
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 200)
        end = run.frame + 32
        match.update(end * combat.TICK, end, frames=False)
        run.frame = end
        self.assertFalse(state.active)
        self.assertAlmostEqual(bot.position[2], 6)
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(200):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            if health.total < 200:
                break
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(health.total, 100)

        npc_control.stop(match)
        mercy.mover.position[:] = (0, 1, 4)
        mercy.mover.body = None
        self.assertFalse(sniper_reposition.request(match, bot, mercy))
        end = run.frame + math.ceil(6 / combat.TICK)
        match.update(end * combat.TICK, end, frames=False)
        run.frame = end
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertEqual(npc_control.reposition(match, mercy), bot.entity)
        self.assertEqual(state.counter, 2)

    def test_rejects_allies_range_and_occlusion(self):
        for reason in ("ally", "range", "wall"):
            with self.subTest(reason=reason):
                run, mercy, bot, _, _ = self.encounter()
                match = run.match
                if reason == "ally":
                    bot.team = mercy.team
                elif reason == "range":
                    bot.position = (0, 1, 6)
                else:
                    match.collision_world = World(FLOOR + wall(-8, 1, 8, 1))
                self.assertFalse(sniper_reposition.request(match, bot, mercy))
                self.assertFalse(sniper_reposition.move_state(bot).active)
                self.assertFalse(match.combat.sniper_repositions)

    def test_sleep_cancels_reposition_and_mercy_blaster_wakes_sniper_before_cooldown(self):
        run, mercy, bot, target, fixture = self.encounter()
        match, fight = run.match, run.match.combat
        ana = match.add_player(2, "Ana", ANA, 0, False)
        in_world(match, ana)
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        ana.team = mercy.team
        ana.mover.position[:] = (0, 1, 8)
        ana.mover.body = None
        run.players.append(ana)
        for player in (mercy, ana):
            player.client.bot_stream = Mock(return_value=None)
        fixture.tick(run, mercy, bot, 35, buttons=0)
        before = fight.vitals_of(target).total
        mercy_target = next(item for item in fight._targets() if item.owner is mercy)
        mercy_health = fight.vitals_of(mercy_target)
        yaw, pitch = aim(fight._eye(ana), (0, 2, 2))
        run.step({ana: (yaw, pitch, 8)})
        for _ in range(60):
            if any(rocket.shooter is ana for rocket in fight.rockets):
                break
            run.step({ana: (yaw, pitch, 0)})
        self.assertTrue(any(rocket.shooter is ana for rocket in fight.rockets))
        self.assertFalse(fight.is_asleep(bot))
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(match, mercy), bot.entity)
        self.assertEqual(npc_control.reposition(match, mercy), bot.entity)
        state = sniper_reposition.move_state(bot)
        self.assertTrue(state.active)
        for _ in range(12):
            run.step({})
            if fight.is_asleep(bot):
                break
        self.assertTrue(fight.is_asleep(bot))
        self.assertEqual(fight.vitals_of(target).total, before - 5)
        run.step({})
        self.assertFalse(state.active)
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        position = bot.position
        self.assertGreater(position[2], 2)
        self.assertLess(position[2], 6)
        self.assertEqual(mercy_health.total, 200)

        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        mercy.take_commands([Command(run.frame, action=2)])
        fixture.tick(run, mercy, bot, 35, buttons=0)
        self.assertTrue(fight.is_asleep(bot))
        self.assertEqual(bot.position, position)
        self.assertFalse(bot.attack_input)
        self.assertEqual(mercy_health.total, 200)

        yaw, pitch = aim(fight._eye(mercy), (position[0], position[1] + 0.8, position[2]))
        run.step({mercy: (yaw, pitch, FIRE)})
        for _ in range(12):
            run.step({})
            if not fight.is_asleep(bot):
                break
        self.assertFalse(fight.is_asleep(bot))
        self.assertEqual(fight.vitals_of(target).total, before - 25)
        self.assertFalse(state.active)
        self.assertEqual(bot.position, position)
        for _ in range(200):
            run.step({})
            if mercy_health.total < 200:
                break
        self.assertEqual(mercy_health.total, 100)

        npc_control.stop(match)
        mercy.mover.position[:] = (position[0], position[1], position[2] - 2)
        mercy.mover.body = None
        self.assertFalse(sniper_reposition.request(match, bot, mercy))
        deadline = math.ceil((state.ended_at + sniper_reposition.COOLDOWN_MS) / state.instance.component.frame_ms)
        self.assertGreater(deadline, run.frame)
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertFalse(sniper_reposition.request(match, bot, mercy))
        match.update(deadline * combat.TICK, deadline, frames=False)
        run.frame = deadline
        self.assertTrue(sniper_reposition.request(match, bot, mercy))
        self.assertEqual(state.counter, 2)

    def test_capsule_blocks_travel_after_eye_ray_admits_destination(self):
        run, mercy, bot, _, _ = self.encounter()
        match = run.match
        match.collision_world = World(FLOOR + box(-8, 8, 1, 2.4, 4, 4.2))
        self.assertTrue(sniper_reposition.request(match, bot, mercy))
        state = sniper_reposition.move_state(bot)
        end = run.frame + 45
        match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(state.active)
        self.assertGreater(bot.position[2], 2)
        self.assertLess(bot.position[2], 4)
        self.assertFalse(match.combat.sniper_repositions)

    def test_stop_and_removal_end_move_without_late_travel(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                run, mercy, bot, _, _ = self.encounter()
                match = run.match
                npc_control.reposition(match, mercy)
                run.step({})
                state = sniper_reposition.move_state(bot)
                position = bot.position
                if remove:
                    self.assertIn(bot.entity, npc.remove_spawned(match))
                else:
                    self.assertEqual(npc_control.stop(match), 1)
                for _ in range(12):
                    run.step({})
                self.assertFalse(state.active)
                self.assertEqual(bot.position, position)
                self.assertFalse(match.combat.sniper_repositions)

    def test_blaster_hit_preserves_reposition_unless_lethal(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, mercy, bot, target, _ = self.encounter()
                match, fight = run.match, run.match.combat
                equip(run, mercy)
                mercy.client.bot_stream = Mock(return_value=None)
                health = fight.vitals_of(target)
                mercy_target = next(t for t in fight._targets() if t.owner is mercy)
                mercy_health = fight.vitals_of(mercy_target)
                remaining = 15 if lethal else 35
                fight._damage(mercy, target, health.total - remaining, False, None, run.frame)
                self.assertEqual(health.total, remaining)
                self.assertEqual(npc_control.start(match, mercy), bot.entity)
                self.assertEqual(npc_control.reposition(match, mercy), bot.entity)
                state = sniper_reposition.move_state(bot)
                activation = state.counter
                destination, speed = state.move_request()
                self.assertEqual(speed, 20)

                shot(run, mercy, bot.position, 0.8, frames=8)
                self.assertEqual(health.total, 0 if lethal else 15)
                self.assertEqual(health.dead, lethal)
                self.assertGreater(bot.position[2], 2)
                self.assertLess(bot.position[2], destination.z)
                self.assertEqual(state.active, not lethal)
                self.assertEqual(state.counter, activation)
                self.assertEqual(bot.entity in fight.sniper_repositions, not lethal)
                self.assertFalse(bot.attack_input)
                self.assertEqual(mercy_health.total, 200)
                if lethal:
                    self.assertEqual(health.killer, mercy.body)
                    mercy.client.queue_bot_death.assert_called_once()
                    stopped = bot.position
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(bot.position, stopped)
                    self.assertEqual(mercy_health.total, 200)
                else:
                    self.assertEqual(state.move_request(), (destination, speed))
                    for _ in range(32):
                        run.step({})
                        self.assertEqual(mercy_health.total, 200)
                        if not state.active:
                            break
                    self.assertFalse(state.active)
                    self.assertAlmostEqual(bot.position[2], destination.z)
                    for _ in range(200):
                        run.step({})
                        if mercy_health.total < 200:
                            break
                    self.assertEqual(mercy_health.total, 100)

    def test_actual_mercy_melee_kill_retires_active_reposition(self):
        run, mercy, bot, target, fixture = self.encounter()
        match = run.match
        fight = match.combat
        health = fight.vitals_of(target)
        health.damage(health.total - 30)
        fight._sync_body_health(bot, health)
        npc_control.reposition(match, mercy)
        state = sniper_reposition.move_state(bot)
        fixture.tick(run, mercy, bot, 1, buttons=0x800)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, mercy.body)
        self.assertFalse(state.active)
        self.assertFalse(fight.sniper_repositions)
