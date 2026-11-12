"""Reconstructed Grasp shield service driven by actual absorption and authored expiry."""

import math
import struct
import unittest
from dataclasses import replace

import test_sigma_grasp as grasp_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import absorption, combat, interception, sigma_charge, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes


def grasp_slot(vitals):
    return next(pool.slot for pool in vitals.extra.values() if pool.identifier == absorption.SHIELD_TAG)


def grasp_amount(vitals):
    return sum(pool.now for pool in vitals.extra.values()
               if pool.identifier == absorption.SHIELD_TAG and pool.kind == combat.SHIELDS_POOL)


def grasp_decay_bodies(fight):
    return {entity for entity, vitals in fight.vitals.items() if not vitals.dead and grasp_amount(vitals) > 0}


def grasp_decay_start(player):
    instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == absorption.GRASP)
    wait = instance.states[instance.graph.node(71).state]
    return (wait.start + wait.ms) / 1000


def grasp_recovery_start(player):
    vitals = player.match.combat.vitals[player.body]
    instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0033)
    wait = instance.states[27]
    delay = wait.evaluate(wait.node.field("m_7130456B"), watch=False)
    return (vitals.last_damage_at + delay) / combat.TICK


class SigmaShieldTests(unittest.TestCase):
    def granted(self, *, close=False):
        run, player, enemy, vitals, instance = grasp_tests.SigmaGraspTests().arena(close=close)
        for frame in range(160):
            run.step({enemy: (-32768, 0, FIRE if frame < 20 else 0)})
        self.assertEqual(instance.vars[21312].value(), 60)
        self.assertEqual(vitals.total, 436)
        return run, player, enemy, vitals

    def test_actual_grasp_exit_grants_sixty_percent_into_a_distinct_pool(self):
        run, player, _enemy, vitals = self.granted()
        pool = next(pool for pool in vitals.extra.values() if pool.identifier == absorption.SHIELD_TAG and not pool.removed)
        self.assertEqual(pool.now, 36)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(vitals.maximum, 436)
        self.assertEqual(player.body_script.component.entity_var(1732).value(), 436)
        self.assertEqual(player.body_script.component.entity_var(1737).value(), 436)
        parts = vitals.component(vitals.ids)[51]
        base = struct.unpack("<IffI4B", parts[combat.SHIELDS_POOL * combat.POOLS])
        extra = struct.unpack("<IffI4B", parts[combat.SHIELDS_POOL * combat.POOLS + grasp_slot(vitals)])
        self.assertNotEqual(base[0], extra[0])
        self.assertEqual(base[1:4], (100, 100, combat.SHIELDS_POOL))
        self.assertEqual(extra[1:4], (36, 36, combat.SHIELDS_POOL))
        self.assertIn(player.body, grasp_decay_bodies(run.match.combat))

    def test_real_rifle_hit_spends_temporary_before_base_and_healing_cannot_refill_it(self):
        run, player, enemy, vitals = self.granted()
        x, y, z = player.mover.position
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.8, z))
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 416)
        self.assertEqual(grasp_amount(vitals), 16)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        run.match.combat.heal_area(player, run.match.combat._eye(player), 10, 1000)
        self.assertEqual(vitals.total, 416)
        self.assertEqual(grasp_amount(vitals), 16)

    def test_hypersphere_self_damage_spends_grasp_shields_and_restarts_ordinary_recovery(self):
        run, player, enemy, vitals = self.granted(close=True)
        fight = run.match.combat
        target = next(entry for entry in fight._targets() if entry.owner is enemy)
        enemy_health = fight.vitals_of(target)
        shield = combat.SHIELDS_POOL
        vitals.now[shield] = 80
        x, y, z = player.mover.position
        reply = aim(fight._eye(enemy), (x, y + 0.8, z))
        run.step({enemy: (*reply, FIRE)})
        self.assertEqual(grasp_amount(vitals), 16)
        rifle_deadline = grasp_recovery_start(player)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        yaw, pitch = aim(fight._eye(player), fight._eye(enemy))
        last_hit = None
        for frame in range(60):
            before = vitals.total
            run.step({player: (yaw, pitch, FIRE if frame == 0 else 0)})
            if vitals.total < before:
                self.assertEqual(before - vitals.total, 7.5)
                last_hit = run.frame
        self.assertIsNotNone(last_hit)
        self.assertEqual(grasp_amount(vitals), 1)
        self.assertGreater(grasp_recovery_start(player), rifle_deadline)
        self.assertEqual(vitals.now[shield], 80)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(enemy_health.total, 90)
        self.assertEqual(script.component.entity_var(342).value(), 110)
        while run.frame < last_hit + 187:
            run.step({})
            self.assertEqual(vitals.now[shield], 80)
        run.step({})
        self.assertAlmostEqual(vitals.now[shield], 80.24)
        self.assertEqual(script.component.entity_var(342).value(), 110)

    def test_allied_biotic_field_heals_base_pools_but_not_spent_grasp_shields(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Enemy", "Healer"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player, enemy, healer = run.players
        player.team = healer.team = 0
        enemy.team = 1
        run.match.switch_hero(player, heroes()[sigma_charge.HERO])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 6]
        healer.mover.position = [x + 2, y, z]
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(target)
        run.step({player: (0, 0, 0x08)})
        for frame in range(160):
            run.step({enemy: (-32768, 0, FIRE if frame < 20 else 0)})
        self.assertEqual(grasp_amount(vitals), 36)
        health, shields = combat.HEALTH_POOL, combat.SHIELDS_POOL
        vitals.now[health], vitals.now[shields] = 280, 90
        yaw, pitch = aim(fight._eye(enemy), (x, y + 0.8, z))
        run.step({enemy: (yaw, pitch, FIRE), healer: (0, 0, 0x10)})
        hit_tick = run.frame
        self.assertEqual(grasp_amount(vitals), 16)
        self.assertEqual((vitals.now[health], vitals.now[shields]), (280, 90))
        for _ in range(150):
            run.step({})
            self.assertEqual(grasp_amount(vitals), 16)
            if vitals.now[shields] > 90:
                self.assertEqual(vitals.now[health], 300)
            if vitals.now[shields] == 100:
                break
        self.assertEqual((vitals.now[health], vitals.now[shields]), (300, 100))
        self.assertLess(run.frame - hit_tick, 188)
        self.assertAlmostEqual(healer.stats.value(stats.HEALING), 30)
        for _ in range(200):
            run.step({})
            if grasp_amount(vitals) < 16:
                break
        self.assertLess(grasp_amount(vitals), 16)
        self.assertTrue(any(shot.field_tick is not None for shot in fight.projectiles.shots))
        self.assertAlmostEqual(healer.stats.value(stats.HEALING), 30)

    def test_three_second_grace_then_five_per_second_decay_and_zero_record(self):
        run, player, enemy, vitals = self.granted()
        fight = run.match.combat
        starts = grasp_decay_start(player)
        start = math.ceil(starts / combat.TICK)
        run.frame = start - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 436)
        before_damage = enemy.stats.value(stats.HERO_DAMAGE)
        run.frame = start + 125
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(grasp_amount(vitals), 26, delta=0.081)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), before_damage)
        run.frame += 1000
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.maximum, 400)
        self.assertNotIn(player.body, grasp_decay_bodies(fight))
        cleared = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * combat.POOLS + grasp_slot(vitals)]
        self.assertEqual(struct.unpack("<IffI4B", cleared)[1:3], (0, 0))
        self.assertEqual(player.body_script.component.entity_var(1732).value(), 400)

    def capped(self):
        run = Run(KANEZAKA_DM, tuple(["Sigma"] + [f"Enemy{i}" for i in range(6)]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player, *enemies = run.players
        run.match.switch_hero(player, heroes()[sigma_charge.HERO])
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
                        if i.graph.index == absorption.GRASP)
        self.assertEqual(instance.vars[21312].value(), 720)
        self.assertEqual(grasp_amount(vitals), 400)
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
        old_activation = run.match.combat.absorption.converted[player.body]
        before = grasp_amount(vitals)
        attacker = enemies[0]
        for frame in range(160):
            commands = dict(facing)
            commands[player] = (0, 0, 0x08 if frame == 0 else 0)
            yaw, pitch, _ = facing[attacker]
            commands[attacker] = (yaw, pitch, FIRE if frame < 20 else 0)
            run.step(commands)
        self.assertEqual(instance.vars[21312].value(), 60)
        self.assertNotEqual(run.match.combat.absorption.converted[player.body], old_activation)
        self.assertGreater(grasp_amount(vitals), before)
        self.assertLessEqual(grasp_amount(vitals), 400)
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
        self.assertNotIn(player.body, grasp_decay_bodies(run.match.combat))

    def test_death_consumes_existing_bonus_and_clears_decay_owner(self):
        run, player, enemy, vitals = self.granted()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(grasp_amount(vitals), 0)
        self.assertNotIn(player.body, grasp_decay_bodies(run.match.combat))

    def test_body_replacement_retires_old_pool_and_decay_state(self):
        run, player, _enemy, _vitals = self.granted()
        previous = player.body
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertNotIn(previous, grasp_decay_bodies(run.match.combat))
        self.assertNotIn(previous, run.match.combat.absorption.converted)
        self.assertNotIn(previous, run.match.combat.vitals)
        self.assertNotEqual(previous, player.body)

    def test_replayed_authored_request_cannot_duplicate_or_restore_an_expired_grant(self):
        run, player, _enemy, vitals = self.granted()
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == absorption.GRASP)
        node = instance.graph.node(34)
        deadline = grasp_decay_start(player)
        self.assertTrue(interception.play_shield_script(script.world, instance, node))
        self.assertEqual(vitals.total, 436)
        self.assertEqual(grasp_decay_start(player), deadline)
        run.frame += 1500
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, 400)
        self.assertTrue(interception.play_shield_script(script.world, instance, node))
        self.assertEqual(vitals.total, 400)


if __name__ == "__main__":
    unittest.main()
