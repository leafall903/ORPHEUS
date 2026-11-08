"""Crossfade executes the actual external 0267 graph and changes match health.

Requires an extracted build104319 ORPHEUS_STATUS_BUNDLE. Missing resource data
is a skipped external fixture, never evidence of completed combat acceptance.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import bots, combat
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(graph.graph(0x0267) is not None, "requires extracted Lucio status supplement")
class LucioAuraTests(unittest.TestCase):
    def scenario(self, healing=True):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        if healing:
            run.step({source: (0, 0, 8)})  # authored Crossfade button35
            for _ in range(30):
                run.step({})
        else:
            run.step({})
        return helper, run, source, enemy, ally

    def wound(self, run, owner, amount=100):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight.vitals_of(target).damage(amount)

    def test_authored_ally_and_self_rates_and_enemy_exclusion(self):
        helper, run, source, enemy, ally = self.scenario()
        for owner in (source, ally, enemy):
            self.wound(run, owner)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 16 * 64 * combat.TICK, places=5)
        self.assertAlmostEqual(helper.health(run, source), 100 + (16 / 1.3) * 64 * combat.TICK, places=5)
        self.assertEqual(helper.health(run, enemy), 100)

    def test_speed_song_does_not_heal_and_switching_stops_healing(self):
        helper, run, source, _, ally = self.scenario(healing=False)
        self.wound(run, ally)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        run.step({source: (0, 0, 8)})
        for _ in range(64):
            run.step({})
        self.assertGreater(helper.health(run, ally), 100)
        run.step({source: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        after = helper.health(run, ally)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), after)

    def test_range_and_occlusion_remove_and_recreate_live_status(self):
        helper, run, _source, _, ally = self.scenario()
        self.wound(run, ally)
        ally.mover.position[:] = (100, 1, 0)
        ally.mover.body.teleport((100, 1 + ally.mover._lift(), 0))
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        ally.mover.position[:] = (0, 1, 4)
        ally.mover.body.teleport((0, 1 + ally.mover._lift(), 4))
        with patch.object(combat, "ray_world", return_value=1.0):
            for _ in range(64):
                run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        for _ in range(64):
            run.step({})
        self.assertGreater(helper.health(run, ally), 100)

    def test_ffa_only_self_and_no_resurrection_of_dead_ally(self):
        helper, run, source, enemy, ally = self.scenario()
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        for owner in (source, ally):
            self.wound(run, owner)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        self.assertGreater(helper.health(run, source), 100)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 500, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertTrue(fight.is_dead(ally))
        self.assertEqual(helper.health(run, ally), 0)

    def test_source_death_and_body_retirement_remove_aura(self):
        helper, run, source, enemy, ally = self.scenario()
        self.wound(run, ally)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is source)
        fight._damage(enemy, target, 500, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        self.assertFalse(fight.lucio_auras.leases)
        fight.retire_player_body(source)
        self.assertFalse(fight.lucio_auras.leases)

    def test_amp_uses_authored_fifty_two_ally_and_forty_self_rates(self):
        helper, run, source, _, ally = self.scenario()
        run.step({source: (0, 0, 16)})
        for _ in range(20):
            run.step({})
        self.wound(run, source)
        self.wound(run, ally)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 52 * 64 * combat.TICK, places=5)
        self.assertAlmostEqual(helper.health(run, source), 100 + 40 * 64 * combat.TICK, places=5)

    def test_amp_ends_and_represses_cannot_bypass_cooldown(self):
        _, run, source, _, _ = self.scenario()
        transitions, previous = [], False
        for offset in range(963):
            run.step({source: (0, 0, 16 if offset % 2 == 0 else 0)})
            var = source.body_script.component.entity_var(2381, create=False)
            active = bool(var.value()) if var is not None else False
            if active != previous:
                transitions.append((offset, active))
            previous = active
        # Includes authored cast time, three-second Amp lifetime, end-started
        # twelve-second cooldown and the next cast. Every other tick presses.
        self.assertEqual(transitions, [(12, True), (200, False), (962, True)])

    def test_two_heal_sources_use_authored_nonstacking_rate(self):
        helper = fixtures.LucioCombatTests()
        run, source, second, ally = helper.scenario()
        run.match.switch_hero(second, heroes()[fixtures.LUCIO])
        second.team = 0
        second.mover.position[:] = (2, 1, 0)
        run.step({source: (0, 0, 8), second: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        self.wound(run, ally)
        leases = [lease for lease in run.match.combat.lucio_auras.leases.values()
                  if lease.target.owner is ally]
        self.assertEqual(len(leases), 2)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 16 * 64 * combat.TICK, places=5)

    def test_actual_npc_script_reads_source_song_and_receives_ally_healing(self):
        helper, run, _source, _, _ = self.scenario()
        bot = bots.make(run.match.game_map.map_guid)[0]
        if graph.entity_body(bot.body) is None:
            self.skipTest("requires an external training NPC body graph")
        bot.team, bot.position, bot.route = 0, (0, 1, 4), []
        run.match._prepare_bot_script(bot)
        self.assertIsNotNone(bot.body_script)
        run.match.bots.append(bot)
        self.wound(run, bot)
        before = helper.health(run, bot)
        run.step({})  # enter aura; no retroactive heal before creation
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, bot), before + 16 * 64 * combat.TICK, places=5)
