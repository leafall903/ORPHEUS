"""Actual command -> authored primary state -> enemy pools; no client acceptance implied."""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_graph_analysis import make_graph, node

from ow174.game import bots, combat, symmetra
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


def arena():
    run = Run(KANEZAKA_DM, ("Symmetra", "Enemy"))
    run.match.bots.clear()
    run.match._pending_bot_scripts.clear()
    shooter, enemy = run.players
    run.match.switch_hero(shooter, heroes()[symmetra.HERO])
    shooter.mover.position = [0.0, 1.0, 0.0]
    enemy.mover.position = [0.0, 1.0, 6.0]
    return run, shooter, enemy


def health(run, owner):
    target = next(t for t in run.match.combat._targets() if t.owner is owner)
    return run.match.combat.vitals_of(target)


def fire(run, shooter, count=1, buttons=1, yaw=0):
    # Aim through the torso, away from the floor plane and head critical region.
    _, pitch = aim((0, 2.55, 0), (0, 2.0, 6))
    for _ in range(count):
        run.step({shooter: (yaw, pitch, buttons)})


class PhotonProjectorTests(unittest.TestCase):
    def test_primary_input_deals_authored_base_dps_and_updates_native_target(self):
        run, shooter, enemy = arena()
        fire(run, shooter, 10)
        self.assertAlmostEqual(health(run, enemy).total, 200 - 60 * .016 * 10, places=4)
        weapon = next(i for i in shooter.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        self.assertEqual(weapon.find_var(0, 16627).value(), expr.Entity(enemy.body))
        self.assertTrue(weapon.state(86).active)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 10)

    def test_release_miss_range_and_world_wall_stop_damage(self):
        for reason in ("release", "miss", "range", "wall"):
            with self.subTest(reason=reason):
                run, shooter, enemy = arena()
                if reason == "range":
                    enemy.mover.position[2] = 30
                else:
                    fire(run, shooter, 3)
                before = health(run, enemy).total
                if reason == "wall":
                    wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                                  (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                    with patch.object(run.match.combat, "_collision", return_value=wall):
                        fire(run, shooter, 3)
                else:
                    fire(run, shooter, 3, buttons=0 if reason == "release" else 1,
                         yaw=-32768 if reason == "miss" else 0)
                self.assertEqual(health(run, enemy).total, before)

    def test_ally_has_no_damage_and_enemy_can_damage_symmetra_shields(self):
        run, shooter, enemy = arena()
        # Team semantics apply outside FFA even when the same body was previously an enemy.
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False})
        enemy.team = shooter.team
        fire(run, shooter, 5)
        self.assertEqual(health(run, enemy).total, 200)
        enemy.team = 1 - shooter.team
        target = next(t for t in run.match.combat._targets() if t.owner is shooter)
        run.match.combat._damage(enemy, target, 30, False, None, run.frame)
        self.assertEqual(health(run, shooter).now[combat.SHIELDS_POOL], 95)
        self.assertEqual(health(run, shooter).now[combat.HEALTH_POOL], 100)

    def test_dead_shooter_and_duplicate_commands_cannot_deal_extra_damage(self):
        run, shooter, enemy = arena()
        fire(run, shooter, 1)
        before = health(run, enemy).total
        shooter.take_commands([Command(run.frame, buttons=1)])
        self.assertEqual(health(run, enemy).total, before)
        target = next(t for t in run.match.combat._targets() if t.owner is shooter)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        fire(run, shooter, 5)
        self.assertEqual(health(run, enemy).total, before)

    def test_training_npc_death_retains_corpse_and_beam_then_reaches_next_enemy(self):
        run, shooter, enemy = arena()
        definition = bots.table()["heroes"]["0x02E000000000016B"]
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": 1 - shooter.team,
            "position": (0.0, 1.0, 3.0), "rotation": (0, 0, 0, 1), "graphs": [],
        }, definition, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        health(run, bot).now[combat.HEALTH_POOL] = .5
        # Native NPC script creation is exercised by its own suite; this scenario
        # verifies terminal pools/corpse selection independently of graph mounting.
        with patch.object(combat.graph, "entity_body", return_value=None):
            fire(run, shooter, 2)
        self.assertTrue(bot.dead)
        self.assertEqual(health(run, bot).total, 0)
        self.assertIn(bot, run.match.bots)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
        self.assertLess(health(run, enemy).total, 200)

    def test_no_pose_frame_does_not_accumulate_endpoint_damage(self):
        run, shooter, enemy = arena()
        fire(run, shooter, 2)
        before = health(run, enemy).total
        run.match.combat.command(shooter, None)
        self.assertEqual(health(run, enemy).total, before)
        shooter.take_commands([Command(run.frame + 200, buttons=1, pitch=955)])
        # No pose is available during the old gap; endpoint integrates one frame.
        self.assertAlmostEqual(before - health(run, enemy).total, 60 * .016, places=5)

    def test_primary_stops_after_ammo_is_exhausted(self):
        run, shooter, enemy = arena()
        # Aim away while exhausting the magazine, then aim at a living target.
        fire(run, shooter, 660, yaw=-32768)
        self.assertEqual(health(run, enemy).total, 200)
        fire(run, shooter, 10)
        self.assertEqual(health(run, enemy).total, 200)

    def test_orb_release_flies_and_deals_direct_plus_splash_damage(self):
        run, shooter, enemy = arena()
        fire(run, shooter, 90, buttons=2)
        self.assertEqual(health(run, enemy).total, 200)
        fire(run, shooter, buttons=0)
        self.assertEqual(len(run.match.combat.rockets), 1)
        fire(run, shooter, 30, buttons=0)
        self.assertAlmostEqual(health(run, enemy).total, 80, places=4)
        self.assertEqual(len(run.match.combat.rockets), 0)

    def test_partial_orb_charge_reduces_damage_and_dead_body_cannot_release(self):
        run, shooter, enemy = arena()
        fire(run, shooter, 20, buttons=2)
        fire(run, shooter, 30, buttons=0)
        lost = 200 - health(run, enemy).total
        self.assertGreater(lost, 0)
        self.assertLess(lost, 120)
        fire(run, shooter, 20, buttons=2)
        target = next(t for t in run.match.combat._targets() if t.owner is shooter)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        fire(run, shooter, buttons=0)
        self.assertEqual(len(run.match.combat.rockets), 0)

    def test_reconstructed_health_query_uses_owned_current_pools_only(self):
        _run, shooter, _enemy = arena()
        script = shooter.body_script
        instance = next(iter(script.component.instances.values()))
        query = {"$": "STU_32E6E4D8", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(script.world.native(query, instance), 225)
        script.set_health(95, 225)
        self.assertEqual(script.world.native(query, instance), 95)
        script.set_health(0, 225)
        self.assertEqual(script.world.native(query, instance), 0)


class SymmetraPassiveTests(unittest.TestCase):
    def damaged(self):
        run, player, enemy = arena()
        fire(run, player, buttons=0)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 150, False, None, run.frame)
        return run, player, enemy, target

    def test_shield_regeneration_waits_three_seconds_then_restores_only_shields(self):
        run, player, _enemy, _target = self.damaged()
        vitals = health(run, player)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 75)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        fire(run, player, 187, buttons=0)  # 2.992 seconds
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        fire(run, player, buttons=0)  # 3.008 seconds: 8 ms of recovery
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], .24, places=4)
        fire(run, player, 400, buttons=0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 75)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 125)

    def test_damage_resets_regeneration_delay_and_death_never_regenerates(self):
        run, player, enemy, target = self.damaged()
        fire(run, player, 200, buttons=0)
        vitals = health(run, player)
        self.assertGreater(vitals.now[combat.SHIELDS_POOL], 0)
        run.match.combat._damage(enemy, target, 1, False, None, run.frame)
        before = vitals.now[combat.SHIELDS_POOL]
        fire(run, player, 187, buttons=0)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], before)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        fire(run, player, 200, buttons=0)
        self.assertEqual(vitals.total, 0)

    def test_skipped_and_repeated_world_ticks_match_contiguous_recovery(self):
        results = []
        for gap in (False, True):
            run, player, _enemy, _target = self.damaged()
            fight = run.match.combat
            if gap:
                fight.update(fight.now + 4, fight.tick + 250)
            else:
                fire(run, player, 250, buttons=0)
            before = health(run, player).now[combat.SHIELDS_POOL]
            fight.update(fight.now, fight.tick)
            self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], before)
            results.append(before)
        self.assertAlmostEqual(results[0], 30, places=4)
        self.assertAlmostEqual(results[0], results[1], places=4)

    def test_authored_recovery_delay_and_rate_modifiers_change_actual_pools(self):
        run, player, _enemy, _target = self.damaged()
        # A later runtime Stack overrides the existing base-recovery Stack.
        # Plain entity-var writes cannot override a live authored Stack output.
        modifier = make_graph(0x7FFE, states=(0, 1), nodes=[
            node(index, "STUStatescriptStateStack", state=index, server=True, fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": var, "m_60DB8F99": 1},
                "m_value": {"$": "STUConfigVarFloat", "m_value": value},
            }) for index, (var, value) in enumerate(((21868, 2.0), (21869, .5)))])
        modifier.header.update(m_nodesBitCount=1, m_statesBitCount=1,
                               m_remoteSyncNodesBitCount=0, m_syncVarsBitCount=0)
        instance = player.body_script.component.create(modifier, init=False)
        instance.state(0).begin()
        instance.state(1).begin()
        fire(run, player, 375, buttons=0)  # data delay 3 s * modifier 2
        self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], 0)
        fire(run, player, buttons=0)
        self.assertAlmostEqual(health(run, player).now[combat.SHIELDS_POOL], .24, places=4)


class SymmetraMeleeTests(unittest.TestCase):
    def test_melee_miss_range_and_ally_filter(self):
        for reason in ("miss", "range", "ally"):
            with self.subTest(reason=reason):
                run, shooter, enemy = arena()
                enemy.mover.position = [0, 1, 4 if reason == "range" else 2]
                if reason == "ally":
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, "free_for_all": False})
                    enemy.team = shooter.team
                fire(run, shooter, 30, buttons=0x800, yaw=-32768 if reason == "miss" else 0)
                self.assertEqual(health(run, enemy).total, 200)

    def test_melee_cannot_hit_through_static_wall(self):
        run, shooter, enemy = arena()
        enemy.mover.position = [0, 1, 2]
        wall = World([(-5, 0, 1, 5, 0, 1, 5, 8, 1),
                      (-5, 0, 1, 5, 8, 1, -5, 8, 1)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            fire(run, shooter, 30, buttons=0x800)
        self.assertEqual(health(run, enemy).total, 200)

    def test_melee_hits_once_and_requires_recovery_before_next_hit(self):
        run, shooter, enemy = arena()
        enemy.mover.position = [0, 1, 2]
        fire(run, shooter, buttons=0x800)
        self.assertEqual(health(run, enemy).total, 170)
        fire(run, shooter, buttons=0)
        fire(run, shooter, buttons=0x800)
        self.assertEqual(health(run, enemy).total, 170)
        fire(run, shooter, 70, buttons=0)
        fire(run, shooter, buttons=0x800)
        self.assertEqual(health(run, enemy).total, 140)


if __name__ == "__main__":
    unittest.main()
