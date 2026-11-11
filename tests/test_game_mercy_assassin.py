"""Static gameplay driven by the extracted Assassin graph and Mercy body."""
import unittest
from unittest.mock import Mock

import test_game_mercy_npc_geometry as geometry
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip
from test_game_mercy_npc import FIRE, FLOOR, World, wall
import test_game_mercy_resurrect as resurrect_tests

from ow174.game import assassin, npc, npc_control
from ow174.game.combat import TICK
from ow174.game.commands import JUMP, Command
from ow174.game.script import nodes


class MercyAssassinTests(unittest.TestCase):
    def arena(self, team=1):
        setup = geometry.MercyNpcGeometryTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        fixture = setup.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.hero == assassin.HERO)
        run, player, bot, _ = fixture.spawn(definition, team)
        player.client.bot_stream = Mock(return_value=None)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        return fixture, run, player, bot, fight.vitals_of(target)

    def contact(self, fixture, run, player, bot, health):
        for _ in range(200):
            fixture.tick(run, player, bot, 1, buttons=0)
            if health.total != 200:
                break
        self.assertEqual(health.total, 175)
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertEqual(run.match.combat.pins, {bot.entity: player.body})

    def test_manual_pounce_entry_rate_input_lock_and_stop(self):
        fixture, run, player, bot, health = self.arena()
        fixture.tick(run, player, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        fixture.tick(run, player, bot, 40, buttons=0)
        self.assertEqual(health.total, 200)
        bot.position = (0, 1, 2.5)
        self.contact(fixture, run, player, bot, health)
        position = tuple(player.mover.position)
        fixture.tick(run, player, bot, 45, buttons=FIRE | 0x08 | 0x02 | 0x40)
        self.assertEqual(health.total, 175)
        fixture.tick(run, player, bot, 17, buttons=FIRE | 0x08 | 0x02 | 0x40)
        self.assertAlmostEqual(health.total, 174.3, places=5)
        fixture.tick(run, player, bot, 3, buttons=FIRE | 0x08 | 0x02 | 0x40)
        self.assertAlmostEqual(health.total, 169.5, places=5)
        self.assertFalse(player.body_script.component.held)
        self.assertEqual(tuple(player.mover.position), position)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.is_stunned(player))
        before = health.total
        fixture.tick(run, player, bot, 61, buttons=0)
        self.assertEqual(health.total, before)
        fixture.tick(run, player, bot, 65, buttons=0)
        self.assertGreater(health.total, before)

    def test_pinned_mercy_rejects_staff_switch_until_fresh_request(self):
        fixture, run, mercy, bot, health = self.arena()
        match, fight = run.match, run.match.combat
        equip(run, mercy)
        slot = mercy.body_script.component.entity_var(31)
        self.assertEqual(slot.value(), 2)
        bot.position = (0, 1, 2.5)
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(match, mercy), bot.entity)
        self.contact(fixture, run, mercy, bot, health)

        def switch(buttons=0):
            run.frame += 1
            match.update(run.frame * TICK, run.frame)
            mercy.take_commands([Command(run.frame, buttons=buttons, action=1)])

        for _ in range(5):
            switch(FIRE)
            self.assertTrue(fight.is_stunned(mercy))
            self.assertEqual(slot.value(), 2)
            self.assertEqual(mercy.body_script.buttons, 0)
        self.assertEqual(npc_control.stop(match), 1)
        fixture.tick(run, mercy, bot, 40, buttons=0)
        self.assertFalse(fight.is_stunned(mercy))
        self.assertEqual(slot.value(), 2)
        self.assertFalse(fight.rockets)

        definition = next(d for d in fixture.catalog.values() if d.hero == assassin.HERO)
        ally = npc.spawn(match, definition, (4, 1, 0), team=mercy.team)
        target = next(t for t in fight._targets() if t.owner is ally)
        ally_health = fight.vitals_of(target)
        ally_health.damage(100)
        fight._sync_body_health(ally, ally_health)
        before = ally_health.total
        switch()
        self.assertEqual(slot.value(), 1)
        fixture.tick(run, mercy, ally, 35, buttons=0)
        fixture.tick(run, mercy, ally, 20)
        self.assertAlmostEqual(ally_health.total - before, 17.6, places=4)

    def test_actual_pounce_interrupts_guardian_angel_and_allows_recast_after_release(self):
        fixture, run, player, bot, health = self.arena()
        bot.position = (2.75, 1, 2)
        definition = next(d for d in fixture.catalog.values() if d.hero == assassin.HERO)
        ally = npc.spawn(run.match, definition, (0, 1, 15), team=player.team)
        guardian = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x006E)
        cooldown = guardian.find_var(0, 61)
        fixture.tick(run, player, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        fixture.tick(run, player, bot, 40, buttons=0)
        self.assertEqual(health.total, 200)

        fixture.tick(run, player, ally, 1, buttons=0x08)
        self.assertTrue(guardian.state(0).active)
        for _ in range(25):
            fixture.tick(run, player, ally, 1, buttons=0x08)
            if health.total < 200:
                break
        self.assertEqual(health.total, 175)
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertFalse(guardian.state(0).active)
        self.assertGreater(cooldown.value(), 0)

        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.is_stunned(player))
        fixture.tick(run, player, ally, 1, buttons=0)
        fixture.tick(run, player, ally, 1, buttons=0x08)
        self.assertFalse(guardian.state(0).active)
        fixture.tick(run, player, ally, 100, buttons=0)
        self.assertEqual(cooldown.value(), 0)
        fixture.tick(run, player, ally, 1, buttons=0x08)
        self.assertTrue(guardian.state(0).active)

    def test_actual_pounce_cancels_resurrection_and_starts_authored_cooldown(self):
        geometry_case = geometry.MercyNpcGeometryTests()
        geometry_case.setUp()
        self.addCleanup(geometry_case.doCleanups)
        fixture = geometry_case.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.hero == assassin.HERO)
        resurrection = resurrect_tests.ResurrectTests()
        resurrection.setUp()
        self.addCleanup(resurrection.doCleanups)
        run, mercy, fight = resurrection.run, resurrection.mercy, resurrection.fight
        bot = npc.spawn(run.match, definition, (4, 1, 0), team=resurrection.enemy.team)
        yaw, pitch = aim(fight._eye(mercy), (4, 2, 0))
        run.step({mercy: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, mercy), bot.entity)
        resurrection.step(1, resurrect_tests.RESURRECT)

        def casting():
            return any(isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING
                       for state in resurrection.instance.states.values())

        self.assertTrue(casting())
        bot.position = (2.5, 1, 0)
        for _ in range(30):
            resurrection.step(1)
            if fight.is_stunned(mercy):
                break
        self.assertTrue(fight.is_stunned(mercy))
        self.assertFalse(casting())
        self.assertTrue(fight.is_dead(resurrection.ally))
        self.assertGreater(resurrection.cooldown(), 29)
        self.assertLessEqual(resurrection.cooldown(), 30)
        self.assertEqual(mercy.stats.value(0x086000000000026A), 0)
        resurrection.step(1, resurrect_tests.RESURRECT)
        self.assertFalse(casting())

        npc_control.stop(run.match)
        resurrection.step(1)
        resurrection.step(1, resurrect_tests.RESURRECT)
        self.assertFalse(casting())
        self.assertTrue(fight.is_dead(resurrection.ally))
        resurrection.step(1880)
        self.assertEqual(resurrection.cooldown(), 0)
        ally = next(t for t in fight._targets() if t.owner is resurrection.ally)
        fight._damage(resurrection.enemy, ally, 500, False, None, run.frame)
        resurrection.step(3)
        resurrection.cast()
        self.assertEqual(mercy.stats.value(0x086000000000026A), 1)

    def test_actual_pounce_interrupts_angelic_descent_and_release_allows_restart(self):
        fixture, run, player, bot, health = self.arena()
        player.spawn = ((0.0, 10.0, 0.0), 0.0)
        player.mover = player.new_mover()
        descent = next(i for i in player.body_script.component.instances.values()
                       if i.graph.index == 0x0071).state(1)
        fixture.tick(run, player, bot, 1, buttons=0)
        fixture.tick(run, player, bot, 30, buttons=JUMP)
        self.assertTrue(descent.active)
        self.assertAlmostEqual(player.mover.velocity[1], -2, delta=0.01)

        _, height, _ = player.mover.position
        bot.position = (2.5, height, 0)
        fixture.tick(run, player, bot, 1, buttons=JUMP)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(20):
            fixture.tick(run, player, bot, 1, buttons=JUMP)
            if health.total < 200:
                break
        self.assertEqual(health.total, 175)
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertFalse(descent.active)
        fixture.tick(run, player, bot, 5, buttons=JUMP)
        self.assertFalse(descent.active)
        self.assertLess(player.mover.velocity[1], -2.5)
        self.assertFalse(any(mod.index == 13 for mod in player.mover.mods.values()))

        npc_control.stop(run.match)
        fixture.tick(run, player, bot, 1, buttons=0)
        fixture.tick(run, player, bot, 30, buttons=JUMP)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertTrue(descent.active)
        self.assertAlmostEqual(player.mover.velocity[1], -2, delta=0.01)

    def test_actual_pounce_blocks_valkyrie_flight_input_without_ending_transformation(self):
        fixture, run, player, bot, health = self.arena()
        bot.position = (0, 1, 2.5)
        fixture.tick(run, player, bot, 35, buttons=0)
        script = player.body_script
        script.set_ult_charge(1820)
        fixture.tick(run, player, bot, 1, buttons=0x20)
        fixture.tick(run, player, bot, 35, buttons=0)
        valkyrie = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        duration = valkyrie.find_var(0, 215)
        self.assertTrue(script.component.entity_var(842).value())
        self.assertEqual(player.mover.flight_scale, 1)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(40):
            fixture.tick(run, player, bot, 1, buttons=0)
            if run.match.combat.is_stunned(player):
                break
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertLess(health.total, 200)
        before = duration.value()
        height = player.mover.position[1]
        fixture.tick(run, player, bot, 20, buttons=JUMP)
        self.assertEqual(player.mover.position[1], height)
        self.assertTrue(script.component.entity_var(842).value())
        self.assertGreater(duration.value(), 0)
        self.assertLess(duration.value(), before)

        npc_control.stop(run.match)
        fixture.tick(run, player, bot, 1, buttons=0)
        fixture.tick(run, player, bot, 20, buttons=JUMP)
        self.assertGreater(player.mover.position[1], height + 1)
        self.assertTrue(script.component.entity_var(842).value())

    def test_wall_breaks_pounce_and_friendly_selection_is_rejected(self):
        fixture, run, player, bot, health = self.arena()
        bot.position = (0, 1, 2.5)
        fixture.tick(run, player, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        open_world = run.match.collision_world
        run.match.collision_world = World(FLOOR + wall(-5, 1.25, 5, 1.25))
        fixture.tick(run, player, bot, 40, buttons=0)
        self.assertEqual(health.total, 200)
        run.match.collision_world = open_world
        self.contact(fixture, run, player, bot, health)
        run.match.collision_world = World(FLOOR + wall(-5, 1.25, 5, 1.25))
        fixture.tick(run, player, bot, 45, buttons=0)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertEqual(health.total, 175)

        fixture, run, player, bot, health = self.arena(team=0)
        bot.position = (0, 1, 2.5)
        fixture.tick(run, player, bot, 1, buttons=0)
        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
            npc_control.start(run.match, player)
        fixture.tick(run, player, bot, 40, buttons=0)
        self.assertEqual(health.total, 200)

    def test_lethal_pounce_and_explicit_source_removal_release_control(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                fixture, run, player, bot, health = self.arena()
                bot.position = (0, 1, 2.5)
                fixture.tick(run, player, bot, 1, buttons=0)
                npc_control.start(run.match, player)
                self.contact(fixture, run, player, bot, health)
                if lethal:
                    fixture.tick(run, player, bot, 171, buttons=0)
                    self.assertEqual(health.total, 0)
                    self.assertTrue(run.match.combat.is_dead(player))
                else:
                    world = bot.body_script.component.world
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                    self.assertIsNone(world.match)
                    self.assertIsNone(world.manual_target)
                    fixture.tick(run, player, bot, 80, buttons=0)
                    self.assertAlmostEqual(health.total, 181.08, places=5)
                self.assertFalse(run.match.combat.pins)
                self.assertFalse(run.match.combat.is_stunned(player))
