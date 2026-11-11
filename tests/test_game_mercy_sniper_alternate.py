"""Original Sniper alternate volley through the manual NPC controller."""

import unittest
from unittest.mock import Mock, patch

import test_game_mercy_npc as npc_tests
import test_game_mercy_npc_boost as boost_tests
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import npc, npc_control, zarya_barriers
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY


class MercySniperAlternateTests(unittest.TestCase):
    def npc_fixture(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        definition = next(d for d in fixture.catalog.values() if d.name == "Sniper")
        return fixture, definition

    def encounter(self, distance=8):
        fixture, definition = self.npc_fixture()
        run, mercy, bot, _ = fixture.spawn(definition, 1)
        bot.position = (0, 1, distance)
        mercy.client.bot_stream = Mock(return_value=None)
        fixture.tick(run, mercy, bot, 2, buttons=0)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is mercy))
        return run, mercy, bot, health, fixture

    def shot(self, run, mercy, bot, fixture, graph):
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(200):
                fixture.tick(run, mercy, bot, 1, buttons=0)
                if shoot.called:
                    break
            self.assertEqual([call.args[2].instance.graph.index for call in shoot.call_args_list], [graph])

    def test_original_switch_alternate_and_back_through_controller(self):
        run, mercy, bot, health, fixture = self.encounter()
        match = run.match
        component = bot.body_script.component
        self.assertEqual(npc_control.start(match, mercy, alternate=True), bot.entity)
        self.assertEqual(component.entity_var(31).value(), 2)
        self.shot(run, mercy, bot, fixture, 0x1134)
        rocket = match.combat.rockets[-1]
        self.assertEqual(rocket.speed, 200)
        self.assertEqual(rocket.left, 0.5)
        fixture.tick(run, mercy, bot, 6, buttons=0)
        self.assertEqual(health.total, 195)
        self.assertEqual(npc_control.start(match, mercy), bot.entity)
        self.assertEqual(component.entity_var(31).value(), 1)
        self.shot(run, mercy, bot, fixture, 0x1122)
        before = health.total
        fixture.tick(run, mercy, bot, 8, buttons=0)
        self.assertEqual(before - health.total, 100)

    def test_occlusion_suspends_then_stop_cancels_an_in_flight_volley(self):
        run, mercy, bot, health, fixture = self.encounter(distance=25)
        match = run.match
        open_world = match.collision_world
        npc_control.start(match, mercy, alternate=True)
        match.collision_world = World(FLOOR + wall(-8, 5, 8, 5))
        fixture.tick(run, mercy, bot, 60, buttons=0)
        self.assertFalse(bot.attack_input)
        self.assertFalse(match.combat.rockets)
        self.assertEqual(health.total, 200)
        match.collision_world = open_world
        self.shot(run, mercy, bot, fixture, 0x1134)
        self.assertTrue(match.combat.rockets)
        self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(match.combat.rockets)
        fixture.tick(run, mercy, bot, 70, buttons=0)
        self.assertEqual(health.total, 200)
        self.assertFalse(bot.attack_input)

    def test_lethal_alternate_credits_sniper_and_releases_input(self):
        run, mercy, bot, health, fixture = self.encounter()
        match = run.match
        health.damage(195)
        match.combat._sync_body_health(mercy, health)
        npc_control.start(match, mercy, alternate=True)
        self.shot(run, mercy, bot, fixture, 0x1134)
        fixture.tick(run, mercy, bot, 12, buttons=0)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(bot.attack_input)
        self.assertFalse(match.combat.rockets)

    def test_selection_rejects_friendly_or_out_of_range_sniper(self):
        run, mercy, bot, health, fixture = self.encounter()
        match = run.match
        bot.team = mercy.team
        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
            npc_control.start(match, mercy, alternate=True)
        bot.team = 1
        bot.position = (0, 1, 102)
        fixture.tick(run, mercy, bot, 2, buttons=0)
        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
            npc_control.start(match, mercy, alternate=True)
        self.assertEqual(bot.body_script.component.entity_var(31).value(), 1)
        self.assertEqual(health.total, 200)

    def test_projected_barrier_intercepts_original_alternate_volley(self):
        fixture, definition = self.npc_fixture()
        run = Run(names=("Mercy", "Zarya"))
        mercy, zarya = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(mercy, heroes()[MERCY])
        match.switch_hero(zarya, heroes()[zarya_barriers.HERO])
        mercy.team = zarya.team = 0
        mercy.mover.position[:] = (0, 1, 0)
        zarya.mover.position[:] = (3, 1, 0)
        for player in run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
        bot = npc.spawn(match, definition, (0, 1, 8), team=1)
        yaw, pitch = aim(match.combat._eye(mercy), (0, 2, 8))
        run.step({mercy: (yaw, pitch, 0), zarya: (-16384, 0, 16)})
        npc_control.start(match, mercy, alternate=True)
        self.shot(run, mercy, bot, fixture, 0x1134)
        fixture.tick(run, mercy, bot, 8, buttons=0)
        barrier = match.combat.zarya_barriers.projected[zarya.body]
        self.assertEqual(barrier.remaining, 195)
        self.assertEqual(match.combat.vitals[mercy.body].total, 200)

    def test_mercy_boost_increases_actual_alternate_damage_and_charge(self):
        fixture = boost_tests.MercyNpcBoostTests()
        self.addCleanup(fixture.doCleanups)
        f, bot, charge = fixture.encounter("Sniper")
        npc_control.start(f.match, f.enemy, alternate=True)
        before = charge.value()
        for _ in range(180):
            fixture.step(f, bot)
            if f.enemy_health() < 200:
                break
        self.assertAlmostEqual(f.enemy_health(), 193.5, places=5)
        self.assertAlmostEqual(charge.value() - before, 1.5, places=5)
