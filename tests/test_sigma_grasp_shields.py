"""Reconstructed Grasp shield service driven by actual absorption and authored expiry."""

import math
import struct
import unittest

import test_sigma_grasp as grasp_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, sigma, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes


class SigmaShieldTests(unittest.TestCase):
    def granted(self):
        run, player, enemy, vitals, instance = grasp_tests.SigmaGraspTests().arena()
        for frame in range(160):
            run.step({enemy: (-32768, 0, FIRE if frame < 20 else 0)})
        self.assertEqual(instance.vars[21312].value(), 60)
        self.assertEqual(vitals.total, 436)
        return run, player, enemy, vitals

    def test_actual_grasp_exit_grants_sixty_percent_into_a_distinct_pool(self):
        run, player, _enemy, vitals = self.granted()
        pool = vitals.temporary_shields[sigma.SHIELD_TAG]
        self.assertEqual(pool.now, 36)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(vitals.maximum, 436)
        self.assertEqual(player.body_script.component.entity_var(1732).value(), 436)
        self.assertEqual(player.body_script.component.entity_var(1737).value(), 436)
        parts = vitals.component(vitals.ids)[51]
        base = struct.unpack("<IffI4B", parts[combat.SHIELDS_POOL * combat.POOLS])
        extra = struct.unpack("<IffI4B", parts[combat.SHIELDS_POOL * combat.POOLS + 1])
        self.assertNotEqual(base[0], extra[0])
        self.assertEqual(base[1:4], (100, 100, combat.SHIELDS_POOL))
        self.assertEqual(extra[1:4], (36, 36, combat.SHIELDS_POOL))
        self.assertIn(player.body, run.match.combat.grasp_shields)

    def test_real_rifle_hit_spends_temporary_before_base_and_healing_cannot_refill_it(self):
        run, player, enemy, vitals = self.granted()
        x, y, z = player.mover.position
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.8, z))
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 416)
        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 16)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        run.match.combat.heal_area(player, run.match.combat._eye(player), 10, 1000)
        self.assertEqual(vitals.total, 416)
        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 16)

    def test_three_second_grace_then_five_per_second_decay_and_zero_record(self):
        run, player, enemy, vitals = self.granted()
        fight = run.match.combat
        decay = fight.grasp_shields[player.body]
        start = math.ceil(decay.starts / combat.TICK)
        run.frame = start - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 436)
        before_damage = enemy.stats.value(stats.HERO_DAMAGE)
        run.frame = start + 125
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 26, delta=0.081)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), before_damage)
        run.frame += 1000
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.maximum, 400)
        self.assertNotIn(player.body, fight.grasp_shields)
        cleared = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * combat.POOLS + 1]
        self.assertEqual(struct.unpack("<IffI4B", cleared)[1:3], (0, 0))
        self.assertEqual(player.body_script.component.entity_var(1732).value(), 400)

    def capped(self):
        run = Run(KANEZAKA_DM, tuple(["Sigma"] + [f"Enemy{i}" for i in range(6)]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player, *enemies = run.players
        run.match.switch_hero(player, heroes()[sigma.HERO])
        x, y, z = player.mover.position
        commands = {player: (0, 0, 0x08)}
        facing = {}
        for index, enemy in enumerate(enemies):
            enemy.mover.position = [x + (index - 2.5) * 1.2, y, z + 6]
            angles = aim(run.match.combat._eye(enemy), (x, y + combat.EYE, z))
            commands[enemy] = (*angles, SECONDARY)
            facing[enemy] = (*angles, 0)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        vitals = run.match.combat.vitals_of(target)
        run.step(commands)
        for _ in range(160):
            run.step(facing)
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == sigma.GRASP)
        self.assertEqual(instance.vars[21312].value(), 720)
        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 400)
        self.assertEqual(vitals.total, 800)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        return run, player, enemies, vitals, instance, facing

    def test_six_actual_helix_attacks_hit_the_extracted_four_hundred_cap(self):
        self.capped()

    def test_a_legitimate_second_cast_after_cooldown_can_add_to_remaining_bonus(self):
        run, player, enemies, vitals, instance, facing = self.capped()
        ability = instance.states[0]
        for _ in range(1000):
            if ability.remaining() <= 0:
                break
            run.step(facing)
        self.assertEqual(ability.remaining(), 0)
        old_activation = run.match.combat.grasp_grants[player.body]
        before = vitals.temporary_shields[sigma.SHIELD_TAG].now
        attacker = enemies[0]
        for frame in range(160):
            commands = dict(facing)
            commands[player] = (0, 0, 0x08 if frame == 0 else 0)
            yaw, pitch, _ = facing[attacker]
            commands[attacker] = (yaw, pitch, FIRE if frame < 20 else 0)
            run.step(commands)
        self.assertEqual(instance.vars[21312].value(), 60)
        self.assertNotEqual(run.match.combat.grasp_grants[player.body], old_activation)
        self.assertGreater(vitals.temporary_shields[sigma.SHIELD_TAG].now, before)
        self.assertLessEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 400)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)

    def test_death_during_grasp_prevents_a_delayed_shield_grant(self):
        run, player, enemy, vitals, _instance = grasp_tests.SigmaGraspTests().arena()
        for _ in range(20):
            run.step({enemy: (-32768, 0, FIRE)})
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        for _ in range(220):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertFalse(vitals.temporary_shields)
        self.assertNotIn(player.body, run.match.combat.grasp_shields)

    def test_death_consumes_existing_bonus_and_clears_decay_owner(self):
        run, player, enemy, vitals = self.granted()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 0)
        self.assertNotIn(player.body, run.match.combat.grasp_shields)

    def test_body_replacement_retires_old_pool_and_decay_state(self):
        run, player, _enemy, _vitals = self.granted()
        previous = player.body
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertNotIn(previous, run.match.combat.grasp_shields)
        self.assertNotIn(previous, run.match.combat.grasp_grants)
        self.assertNotIn(previous, run.match.combat.vitals)
        self.assertNotEqual(previous, player.body)

    def test_replayed_authored_request_cannot_duplicate_or_restore_an_expired_grant(self):
        run, player, _enemy, vitals = self.granted()
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == sigma.GRASP)
        node = instance.graph.node(34)
        deadline = run.match.combat.grasp_shields[player.body].starts
        self.assertTrue(script.world.play_compatible_script(instance, node))
        self.assertEqual(vitals.total, 436)
        self.assertEqual(run.match.combat.grasp_shields[player.body].starts, deadline)
        run.frame += 1500
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertTrue(script.world.play_compatible_script(instance, node))
        self.assertEqual(vitals.total, 400)


if __name__ == "__main__":
    unittest.main()
