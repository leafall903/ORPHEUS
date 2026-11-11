"""Actual Q/E paths through the explicitly reconstructed physical Pulse bridge."""

import unittest
from dataclasses import replace

import test_game_tracer_deflect as deflect_tests
from test_game_combat import aim, in_world

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class PulseDeflectTests(unittest.TestCase):
    health = deflect_tests.TracerDeflectTests.health

    def step(self, run, *, moving=None, buttons=None):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for actor in run.players:
            actor.client.queue_entities(run.match.remote_movements(actor))
        for actor in run.players:
            actor.take_commands([Command(run.frame, right=127 if actor is moving else 0,
                                         buttons=(buttons or {}).get(actor, 0))])

    def launch(self, *, frontal=True, mode=None, deflect_button=16):
        run, player, genji, incoming, reflected, instance = deflect_tests.TracerDeflectTests().arena(
            distance=3, frontal=frontal, activate_deflect=bool(deflect_button))
        if mode is not None:
            run.match.game_map = replace(run.match.game_map, mode_guid=mode)
            self.assertTrue(run.match.switch_hero(player, player.hero))
            player.body_script.ult_seconds = 0
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (*incoming, 32), genji: (*reflected, deflect_button)})
        for _ in range(20):
            if run.match.combat.pulse_bombs.bombs:
                break
            run.step({genji: (*reflected, deflect_button)})
        self.assertEqual(len(run.match.combat.pulse_bombs.bombs), 1)
        bomb = run.match.combat.pulse_bombs.bombs[0]
        self.assertEqual(script.component.entity_var(342).value(), 0)
        return run, player, genji, bomb, incoming, reflected, instance

    def reflect(self, run, player, genji, bomb, reflected):
        for _ in range(40):
            if bomb.shooter is genji or bomb.stuck:
                break
            run.step({genji: (*reflected, 16)})
        self.assertIs(bomb.shooter, genji)
        self.assertEqual(bomb.source, genji.body)
        self.assertFalse(bomb.stuck)
        self.assertIsNone(bomb.attached)
        self.assertEqual(self.health(run, genji).total, 200)

    def attach(self, run, player, genji, bomb, reflected):
        self.reflect(run, player, genji, bomb, reflected)
        for _ in range(40):
            if bomb.stuck:
                break
            run.step({genji: (*reflected, 0)})
        self.assertEqual(bomb.attached, player.body)
        self.assertEqual(self.health(run, player).total, 145)

    def support(self, run, genji, hero=SOLDIER):
        actor = run.match.add_player(3, "Support", hero, 0, False)
        in_world(run.match, actor)
        run.players.append(actor)
        self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
        x, y, z = genji.mover.position
        actor.mover.position[:] = (x, y, z+3)
        actor.body_script.ult_seconds = 0
        facing = aim(run.match.combat._eye(actor), (x, y+1, z))
        return actor, facing

    def test_batched_pulse_uses_genjis_deflect_state_and_aim_at_contact(self):
        for gate in ("front then rear", "rear then front", "late deflect"):
            with self.subTest(gate=gate):
                continuous = self.pulse_deflect_during_batch(gate, batched=False)
                batched = self.pulse_deflect_during_batch(gate, batched=True)
                reflected = gate == "front then rear"
                self.assertEqual(continuous[:3], (reflected, not reflected, 200 if reflected else 195))
                self.assertEqual(batched[:3], continuous[:3])
                for actual, expected in zip(batched[3], continuous[3], strict=True):
                    self.assertAlmostEqual(actual, expected, places=5)

    def pulse_deflect_during_batch(self, gate, *, batched):
        late = gate == "late deflect"
        run, player, genji, bomb, _, _, instance = self.launch(
            frontal=gate != "rear then front", deflect_button=0 if late else 16,
        )
        x, y, z = player.mover.position
        front = aim(run.match.combat._eye(genji), (x, y + 1.55, z))
        rear = (0, 0)
        initial = rear if gate == "rear then front" else front
        final = rear if gate == "front then rear" else front
        for _ in range(3):
            run.step({genji: (*initial, 0 if late else 16)})
        self.assertFalse(bomb.stuck)
        self.assertIs(bomb.shooter, player)
        commands = []
        for i in range(1, 31):
            direction, buttons = (initial, 0 if late else 16) if i < 15 else (final, 16)
            commands.append(Command(run.frame + i, yaw=direction[0], pitch=direction[1], buttons=buttons))
        if batched:
            genji.take_commands(commands)
            run.frame += len(commands)
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for command in commands:
                run.step({genji: (command.yaw, command.pitch, command.buttons)})
        self.assertTrue(instance.states[10].active)
        health = self.health(run, genji).total
        return bomb.shooter is genji, bomb.attached == genji.body, health, bomb.velocity

    def test_frontal_pulse_transfers_owner_and_ends_original_prediction_without_resetting_life(self):
        run, player, genji, bomb, _, reflected, instance = self.launch()
        shot = bomb.shot
        self.assertIsNotNone(shot)
        self.assertIn(shot, run.match.combat.projectiles.shots)
        initial_life = bomb.life_left
        self.reflect(run, player, genji, bomb, reflected)
        self.assertGreater(bomb.age, 0)
        self.assertLess(bomb.life_left, initial_life)
        self.assertAlmostEqual(bomb.age + bomb.life_left, 5)
        self.assertEqual(bomb.fuse_left, 0)
        self.assertTrue(instance.states[10].active)
        self.assertLess(bomb.velocity[2], 0)
        self.assertIsNone(bomb.shot)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertTrue(shot.observers)
        self.assertTrue(all(view.finished and not view.explode for view in shot.observers))
        before = (bomb.position, bomb.velocity, bomb.age, bomb.life_left)
        run.match.combat.pulse_bombs.update(run.frame)
        self.assertEqual((bomb.position, bomb.velocity, bomb.age, bomb.life_left), before)

    def test_reflected_pulse_hits_original_tracer_and_credits_genji_without_original_charge(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.attach(run, player, genji, bomb, reflected)
        self.assertAlmostEqual(bomb.fuse_left, 1, delta=combat.TICK)
        # Actual strafe input moves the defender out of the reconstructed blast.
        for _ in range(80):
            self.step(run, moving=genji)
        self.assertTrue(self.health(run, player).dead)
        self.assertEqual(self.health(run, player).killer, genji.body)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 150)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_rear_pulse_sticks_and_is_not_retroactively_reflected_when_defender_turns(self):
        run, player, genji, bomb, _, reflected, instance = self.launch(frontal=False)
        for _ in range(40):
            if bomb.stuck:
                break
            run.step({genji: (*reflected, 16)})
        self.assertEqual(bomb.attached, genji.body)
        self.assertIs(bomb.shooter, player)
        self.assertEqual(self.health(run, genji).total, 195)
        self.assertTrue(instance.states[10].active)
        run.step({genji: (-32768, 0, 16)})
        self.assertIs(bomb.shooter, player)
        self.assertEqual(bomb.attached, genji.body)

    def test_reflected_blast_self_damage_belongs_to_current_owner_without_extra_kill_credit(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.attach(run, player, genji, bomb, reflected)
        for _ in range(80):
            run.step({})
        self.assertTrue(self.health(run, player).dead)
        self.assertTrue(self.health(run, genji).dead)
        self.assertEqual(self.health(run, genji).killer, genji.body)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 150)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)

    def test_original_body_replacement_preserves_reflected_bomb_but_new_owner_replacement_retires_it(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.reflect(run, player, genji, bomb, reflected)
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000002]))
        self.assertIn(bomb, run.match.combat.pulse_bombs.bombs)
        self.assertEqual(bomb.source, genji.body)
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000002]))
        self.assertNotIn(bomb, run.match.combat.pulse_bombs.bombs)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.health(run, player).total, 250)
        self.assertEqual(self.health(run, genji).total, 250)

    def test_actual_rear_primary_kills_deflector_without_cancelling_transferred_bomb(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.reflect(run, player, genji, bomb, reflected)
        source_body = genji.body
        actor, facing = self.support(run, genji)
        self.health(run, genji).now[combat.HEALTH_POOL] = 11
        run.step({actor: (*facing, 1), genji: (*reflected, 0)})
        self.assertTrue(self.health(run, genji).dead)
        self.assertIn(bomb, run.match.combat.pulse_bombs.bombs)
        for _ in range(100):
            run.step({})
        self.assertTrue(self.health(run, player).dead)
        self.assertEqual(self.health(run, player).killer, source_body)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_recall_detaches_reflected_bomb_and_protects_original_tracer(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.attach(run, player, genji, bomb, reflected)
        run.step({player: (0, 0, 16)})
        run.step({})
        self.assertIsNone(bomb.attached)
        for _ in range(100):
            self.step(run, moving=genji)
        self.assertFalse(self.health(run, player).dead)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_actual_rear_flash_interrupts_deflect_without_cancelling_transferred_bomb(self):
        run, player, genji, bomb, _, reflected, instance = self.launch()
        self.reflect(run, player, genji, bomb, reflected)
        actor, facing = self.support(run, genji, 0x02E0000000000042)
        run.step({actor: (*facing, 16), genji: (*reflected, 0)})
        for _ in range(50):
            if run.match.combat.is_stunned(genji):
                break
            run.step({genji: (*reflected, 0)})
        self.assertTrue(run.match.combat.is_stunned(genji))
        self.assertFalse(instance.states[10].active)
        self.assertEqual(self.health(run, genji).total, 175)
        self.assertIn(bomb, run.match.combat.pulse_bombs.bombs)
        self.assertIs(bomb.shooter, genji)
        for _ in range(100):
            run.step({})
        self.assertTrue(self.health(run, player).dead)
        self.assertEqual(self.health(run, player).killer, genji.body)

    def test_blink_carries_reflected_attachment_and_blast_away_from_deflector(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.attach(run, player, genji, bomb, reflected)
        before = bomb.position
        run.step({player: (0, 0, 8)})
        run.step({})
        self.assertGreater(bomb.position[2] - before[2], 7.4)
        self.assertEqual(bomb.attached, player.body)
        for _ in range(80):
            run.step({})
        self.assertTrue(self.health(run, player).dead)
        self.assertEqual(self.health(run, genji).total, 200)

    def redirected(self, *, allied=False, wall=False, mode=None, buffered=False):
        run, player, genji, bomb, _, _, _ = self.launch(mode=mode)
        third, _ = self.support(run, genji)
        x, y, z = player.mover.position
        third.mover.position[:] = (x+2, y, z+1)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, genji.team, third.team = 0, 1, 1 if allied else 0
        if buffered:
            # Explicit enlarged pool exposes full blast amount without overkill.
            run.match.combat.vitals[third.body] = combat.Vitals(third.body, 500)
        facing = aim(run.match.combat._eye(genji), (x+2, y+1, z+1))
        if wall:
            face = x+1
            triangles = [
                (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
                (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
                (face, y, z-1, face, y, z+4, face, y+4, z+4),
                (face, y, z-1, face, y+4, z+4, face, y+4, z-1),
                (face, y, z-1, face, y+4, z+4, face, y, z+4),
                (face, y, z-1, face, y+4, z-1, face, y+4, z+4),
            ]
            run.match.collision_world = World(triangles, name="Reflected Pulse wall")
            for actor in run.players:
                actor.mover.use(world=run.match.collision_world)
        self.reflect(run, player, genji, bomb, facing)
        for _ in range(60):
            if bomb.stuck:
                break
            run.step({genji: (*facing, 0)})
        self.assertTrue(bomb.stuck)
        return run, player, genji, third, bomb

    def test_reflected_pulse_uses_defender_aim_and_enemy_team_for_contact_and_blast(self):
        run, player, genji, third, bomb = self.redirected()
        self.assertEqual(bomb.attached, third.body)
        self.assertEqual(self.health(run, third).total, 195)
        for _ in range(80):
            self.step(run, moving=genji)
        self.assertTrue(self.health(run, third).dead)
        self.assertEqual(self.health(run, third).killer, genji.body)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_reflected_pulse_cannot_contact_or_damage_defenders_ally(self):
        run, _, genji, third, bomb = self.redirected(allied=True)
        self.assertNotEqual(bomb.attached, third.body)
        for _ in range(80):
            self.step(run, moving=genji)
        self.assertEqual(self.health(run, third).total, 200)

    def test_static_wall_stops_reflected_flight_and_occludes_its_blast(self):
        run, _, genji, third, bomb = self.redirected(wall=True)
        self.assertIsNone(bomb.attached)
        self.assertEqual(self.health(run, third).total, 200)
        for _ in range(80):
            self.step(run, moving=genji)
        self.assertEqual(self.health(run, third).total, 200)

    def test_blink_evades_return_flight_and_reflection_does_not_extend_five_second_life(self):
        run, player, genji, bomb, _, reflected, _ = self.launch()
        self.reflect(run, player, genji, bomb, reflected)
        run.step({player: (0, 0, 8)})
        run.match.collision_world = World([], name="Reflected Pulse lifetime")
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        for _ in range(320):
            run.step({})
        self.assertNotIn(bomb, run.match.combat.pulse_bombs.bombs)
        self.assertFalse(bomb.stuck)
        self.assertAlmostEqual(bomb.age, 5, places=6)
        self.assertEqual(self.health(run, player).total, 150)

    def test_reflection_preserves_authored_pve_450_blast_instead_of_requerying_new_hero(self):
        run, _, genji, third, bomb = self.redirected(mode=0x0230000000000025, buffered=True)
        self.assertEqual(bomb.attached, third.body)
        self.assertEqual(self.health(run, third).total, 495)
        self.assertEqual(bomb.blast.amount, 450)
        for _ in range(80):
            self.step(run, moving=genji)
        self.assertEqual(self.health(run, third).total, 45)


if __name__ == "__main__":
    unittest.main()
