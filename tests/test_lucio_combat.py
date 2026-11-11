"""Actual 104319 Lucio inputs against authoritative player/NPC health pools.

These are headless reconstruction checks, not original-client acceptance.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import bots, combat, wraith
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes

LUCIO = 0x02E0000000000079


class LucioCombatTests(unittest.TestCase):
    def scenario(self):
        run = Run(names=("Lucio", "Enemy", "Ally"))
        player, enemy, ally = run.players
        run.match.switch_hero(player, heroes()[LUCIO])
        player.team, enemy.team, ally.team = 0, 1, 0
        player.mover.position[:] = (0, 1, 0)
        enemy.mover.position[:] = (0, 1, 4)
        ally.mover.position[:] = (1, 1, 4)
        run.match.bots.clear()
        return run, player, enemy, ally

    def health(self, run, owner):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target).total

    def test_soundwave_hits_enemy_once_without_harming_ally(self):
        run, player, enemy, ally = self.scenario()
        for _ in range(40):
            run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), 175)
        self.assertEqual(self.health(run, ally), 200)
        self.assertEqual(self.health(run, player), 200)

    def test_wraith_blocks_attacks_then_expires_without_target_commands(self):
        def attacker_step(run, player, buttons=0):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.client.queue_entities(run.match.remote_movements(player))
            player.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=buttons, extra=0)])

        for button, damage in ((FIRE, 80), (SECONDARY, 25), (0x800, 30)):
            with self.subTest(button=button):
                run, player, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[0x02E0000000000002])
                enemy.mover.position[:] = (0, 1, 2 if button == 0x800 else 4)
                ally.mover.position[:] = (20, 1, 4)
                for _ in range(8):
                    run.step({})
                run.step({enemy: (0, 0, 8)})
                wait = wraith.instance_for(enemy).states[3]
                self.assertTrue(wait.active)
                self.assertEqual(wait.ms, 3000)
                target_frame = enemy.body_script.frame

                before = self.health(run, enemy)
                knockback_serial = run.match.combat.knockbacks.serial
                attacker_step(run, player, button)
                for _ in range(249):
                    self.assertEqual(self.health(run, enemy), before)
                    self.assertFalse(enemy.mover.velocity_changes)
                    attacker_step(run, player)
                self.assertEqual(self.health(run, enemy), before)
                self.assertEqual(run.match.combat.knockbacks.serial, knockback_serial)
                self.assertEqual(enemy.body_script.frame, target_frame)
                last_protected_tick = (wait.start + wait.ms - 1) // enemy.body_script.component.frame_ms
                self.assertTrue(wraith.protects(enemy, last_protected_tick))
                self.assertFalse(wraith.protects(enemy, last_protected_tick + 1))

                attacker_step(run, player, button)
                for _ in range(44):
                    attacker_step(run, player)
                self.assertEqual(before - self.health(run, enemy), damage)
                if button == 0x800:
                    self.assertEqual(run.match.combat.knockbacks.serial, knockback_serial + 1)

    def test_soundwave_rejects_behind_outside_cone_and_distant_targets(self):
        for position in ((0, 1, -4), (4, 1, 0), (0, 1, 10)):
            with self.subTest(position=position):
                run, player, enemy, _ = self.scenario()
                enemy.mover.position[:] = position
                run.step({player: (0, 0, SECONDARY)})
                self.assertEqual(self.health(run, enemy), 200)

    def test_soundwave_respects_static_occlusion(self):
        run, player, enemy, _ = self.scenario()
        # The collision engine is tested separately; inject an intervening hit
        # into its distance boundary, while retaining real input/graph/damage.
        with patch.object(combat, "ray_world", return_value=1.0):
            run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), 200)

    def test_soundwave_npc_damage_and_retained_death(self):
        run, player, _, _ = self.scenario()
        # A training NPC exercises the shared Bot path. This does not stand in
        # for the five Archives archetypes whose host is still under development.
        npc = bots.make(run.match.game_map.map_guid)[0]
        npc.position = (0, 1, 3)
        npc.team, npc.retain_corpse = 1, True
        run.match.bots[:] = [npc]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is npc)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 20)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(vitals.total, 0)
        self.assertTrue(npc.dead)
        self.assertFalse(any(who is npc for _, who in fight.respawns))
        self.assertIn(npc, run.match.bots)

    def test_primary_projectiles_deal_damage_and_lucio_receives_damage(self):
        run, player, enemy, ally = self.scenario()
        ally.mover.position[:] = (10, 1, 4)
        for _ in range(25):
            run.step({player: (0, 0, FIRE)})
        self.assertLess(self.health(run, enemy), 200)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 30, False, None, run.frame)
        self.assertEqual(self.health(run, player), 170)

    def test_dead_lucio_cannot_emit_soundwave_damage(self):
        run, player, enemy, _ = self.scenario()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 500, False, None, run.frame)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), 200)

    def test_soundwave_uses_shared_armour_and_shield_damage_order(self):
        run, player, enemy, _ = self.scenario()
        vitals = combat.Vitals(enemy.body, 200, armour=50, shields=10)
        run.match.combat.vitals[enemy.body] = vitals
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 40)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_soundwave_free_for_all_does_not_treat_same_team_as_ally(self):
        run, player, enemy, ally = self.scenario()
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), 175)
        self.assertEqual(self.health(run, ally), 175)
        self.assertEqual(self.health(run, player), 200)

    def test_npc_can_damage_and_kill_lucio(self):
        run, player, enemy, _ = self.scenario()
        npc = bots.make(run.match.game_map.map_guid)[0]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(npc, target, 25, False, None, run.frame)
        self.assertEqual(self.health(run, player), 175)
        fight._damage(npc, target, 200, False, None, run.frame)
        self.assertTrue(fight.is_dead(player))
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), 200)

    def test_soundwave_cooldown_rejects_represses_until_four_seconds(self):
        run, player, enemy, _ = self.scenario()
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x776)
        damage = instance.state(11)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(damage.counter, 1)
        self.assertEqual(self.health(run, enemy), 175)
        # Press and release repeatedly; none of the 124 intermediate presses
        # may bypass the authored cooldown or cause additional pool loss.
        for offset in range(1, 250):
            run.step({player: (0, 0, SECONDARY if offset % 2 == 0 else 0)})
            self.assertEqual(damage.counter, 1)
            self.assertEqual(self.health(run, enemy), 175)
        # The first accepted hit now physically displaces the victim. Return
        # it to range to test cooldown expiry, independent of that trajectory.
        enemy.mover.position[:] = (0, 1, 4)
        enemy.mover.velocity = (0., 0., 0.)
        enemy.mover.body = None
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(damage.counter, 2)
        self.assertEqual(self.health(run, enemy), 150)

    def test_replayed_soundwave_input_cannot_repeat_damage(self):
        run, player, enemy, _ = self.scenario()
        run.step({player: (0, 0, SECONDARY)})
        for _ in range(5):
            player.take_commands([Command(run.frame, buttons=SECONDARY, extra=0)])
        self.assertEqual(self.health(run, enemy), 175)


if __name__ == "__main__":
    unittest.main()
