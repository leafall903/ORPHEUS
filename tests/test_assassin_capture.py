"""Actual associated target-message and1157 manual capture against Pharah.

Manual classification/contact/health timing are reconstructed, not native AI.
"""
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_pve as pve
from test_game_mover import FLOOR, wall

from ow174.game import assassin, combat, defenses, npc, npc_control, zarya_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command, FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph


class AssassinCaptureTests(unittest.TestCase):
    encounter = pve.PharahPveTests.encounter
    health = pve.PharahPveTests.health
    aim_at_body = staticmethod(pve.PharahPveTests.aim_at_body)
    create = pve.PharahPveTests.create

    @classmethod
    def setUpClass(cls):
        bundle = Path(os.environ.get('ORPHEUS_ASSOCIATED_GRAPH_BUNDLE', ''))
        if not bundle.is_file():
            raise unittest.SkipTest('Supply complete actual five-NPC associated graph closure')
        pve.PharahPveTests.setUpClass()
        cls.addClassCleanup(pve.PharahPveTests.tearDownClass)
        cls.catalog = pve.PharahPveTests.catalog
        mount = patch.object(graph, 'GRAPHS_PATH', bundle)
        mount.start()
        cls.addClassCleanup(mount.stop)
        pve.PharahPveTests.clear_graphs()
        cls.addClassCleanup(pve.PharahPveTests.clear_graphs)

    def arena(self, distance=1.8):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        return self.create(definition, distance=distance)

    def test_autonomous_assassin_acquires_and_captures_through_actual_graph(self):
        run, player, _bot = self.arena()
        self.assertEqual(len(npc.remove_spawned(run.match)), 1)
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(run.match, definition, (0, player.mover.position[1], 1.8),
                        team=1, autonomous=True)
        associated = bot.body_script.associated_component
        fight = run.match.combat
        for _ in range(40):
            run.step({})
            if fight.is_pinned(player):
                break
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(player.body))
        override = associated.entity_var(14365, create=False)
        self.assertFalse(override is not None and expr.truthy(override.value()))
        self.assertTrue(fight.is_pinned(player))
        self.assertEqual(self.health(run, player).total, 175)
        npc_control.stop(run.match)
        for _ in range(4):
            run.step({})
        self.assertFalse(fight.is_pinned(player))
        self.assertFalse(expr.truthy(associated.entity_var(1650).value()))

    def test_autonomous_search_skips_occluded_allied_and_authored_excluded_targets(self):
        run, player, _bot = self.arena()
        npc.remove_spawned(run.match)
        rival = run.players[1]
        rival.mover.position = [3.5, player.mover.position[1], 0]
        rival.mover.body = None
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, rival.team = 0, 1
        player.body_script.component.entity_var(14695).base = True
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(run.match, definition, (0, player.mover.position[1], 1.8),
                        team=1, autonomous=True)
        associated = bot.body_script.associated_component
        fight = run.match.combat
        y = player.mover.position[1]
        wall = World([(-4, y, .9, 4, y + 5, .9, 4, y, .9),
                      (-4, y, .9, -4, y + 5, .9, 4, y + 5, .9)])
        for _ in range(5):
            run.step({})
        selected = associated.entity_var(1650, create=False)
        self.assertFalse(selected is not None and expr.truthy(selected.value()))
        player.body_script.component.entity_var(14695).base = False
        with patch.object(fight, '_collision', return_value=wall):
            for _ in range(8):
                run.step({})
                self.assertFalse(bot.attack_input)
        for _ in range(20):
            run.step({})
            if fight.is_pinned(player):
                break
        self.assertTrue(fight.is_pinned(player))
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(player.body))
        player.team = bot.team
        for _ in range(5):
            run.step({})
        self.assertFalse(fight.is_pinned(player))
        self.assertFalse(bot.attack_input)
        self.assertFalse(expr.truthy(associated.entity_var(1650).value()))
        self.assertEqual(self.health(run, player).total, 175)
        player.team = 0
        for _ in range(20):
            run.step({})
            if fight.is_pinned(player):
                break
        self.assertTrue(fight.is_pinned(player))
        self.assertEqual(self.health(run, player).total, 150)
        player.body_script.component.entity_var(14695).base = True
        for _ in range(5):
            run.step({})
        self.assertFalse(fight.is_pinned(player))
        self.assertFalse(expr.truthy(associated.entity_var(1650).value()))

    def test_autonomous_assassin_walks_into_capture_range_and_stops_for_occlusion(self):
        run, player, _bot = self.arena()
        npc.remove_spawned(run.match)
        y = player.mover.position[1]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, run.players[1].team = 0, 1
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(run.match, definition, (0, y, 8), team=1, autonomous=True)
        fight = run.match.combat
        for _ in range(18):
            run.step({})
        self.assertLess(bot.position[2], 7.8)
        self.assertGreater(bot.position[2], 4)
        self.assertEqual(self.health(run, player).total, 200)
        self.assertIsNotNone(bot.impulse_mover)
        wall = World([(-4, y, 3.5, 4, y + 5, 3.5, 4, y, 3.5),
                      (-4, y, 3.5, -4, y + 5, 3.5, 4, y + 5, 3.5)])
        with patch.object(fight, '_collision', return_value=wall):
            for _ in range(30):
                run.step({})
            stopped = bot.position
            for _ in range(20):
                run.step({})
                self.assertFalse(bot.attack_input)
            self.assertEqual(bot.position, stopped)
            self.assertGreater(stopped[2], 3.5)
            self.assertEqual(self.health(run, player).total, 200)
        for _ in range(120):
            run.step({})
            if fight.is_pinned(player):
                break
        self.assertTrue(fight.is_pinned(player))
        self.assertLessEqual(bot.position[2], 3)
        self.assertEqual(self.health(run, player).total, 175)

    def test_autonomous_assassin_detours_around_static_cover_and_captures_pharah(self):
        run, player, _bot = self.arena()
        match = run.match
        npc.remove_spawned(match)
        y = player.mover.position[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, run.players[1].team = 0, 1
        match.collision_world = World(FLOOR + wall(-2, 4, 2, 4))
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(match, definition, (0, y, 8), team=1, autonomous=True)
        associated = bot.body_script.associated_component
        fight = match.combat
        health = self.health(run, player)
        start = bot.position
        excluded = player.body_script.component.entity_var(14695)
        excluded.base = True
        for _ in range(12):
            run.step({})
        self.assertEqual(bot.position, start)
        self.assertIsNone(bot.combat_detour)
        excluded.base = False

        path = [start]
        for _ in range(300):
            run.step({})
            path.append(bot.position)
            target = next(candidate for candidate in fight._enemies(bot) if candidate.owner is player)
            if not assassin.visible(bot, fight, target):
                selected = associated.entity_var(1650, create=False)
                self.assertFalse(selected is not None and expr.truthy(selected.value()))
                self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, 200)
            if fight.is_pinned(player):
                break

        crossings = [point for before, point in zip(path, path[1:]) if before[2] >= 4 > point[2]]
        self.assertTrue(crossings, path[::20])
        self.assertTrue(all(abs(point[0]) > 2 for point in crossings), crossings)
        self.assertTrue(fight.is_pinned(player), path[::20])
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(player.body))
        self.assertEqual(health.total, 175)
        npc_control.stop(match)
        for _ in range(10):
            run.step({})
        self.assertIsNone(bot.combat_detour)
        self.assertFalse(fight.is_pinned(player))
        self.assertEqual(health.total, 175)

    def test_autonomous_assassin_detours_on_rialto_collision_and_captures_pharah(self):
        cache = Path(os.environ.get('ORPHEUS_RIALTO_COLLISION', ''))
        if not cache.is_file():
            self.skipTest('Supply the extracted build 104319 Rialto collision cache')
        world = World.load(cache, frozenset({0x0D80000000002F4A}))
        run, player, _bot = self.arena()
        match = run.match
        npc.remove_spawned(match)
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, run.players[1].team = 0, 1
        match.collision_world = world
        player.mover.use(world=world)
        player.mover.position = [-139, world.ground(-139, -112, 0, -4).y, -112]
        player.mover.body = None
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        start = (-143, world.ground(-143, -118, 0, -4).y, -118)
        bot = npc.spawn(match, definition, start, team=1, autonomous=True)
        fight = match.combat
        target = next(candidate for candidate in fight._enemies(bot) if candidate.owner is player)
        self.assertFalse(assassin.visible(bot, fight, target))
        health = self.health(run, player)
        path = [start]
        for _ in range(360):
            run.step({})
            path.append(bot.position)
            if fight.is_pinned(player):
                break

        self.assertTrue(fight.is_pinned(player), path[::25])
        self.assertTrue(any(point[0] < -142.5 and point[2] > -117 for point in path))
        self.assertGreater(bot.position[2], -114)
        self.assertEqual(bot.body_script.associated_component.entity_var(1650).value(), expr.Entity(player.body))
        self.assertEqual(health.total, 175)

        npc_control.stop(match)
        for _ in range(4):
            run.step({})
        self.assertFalse(fight.is_pinned(player))
        assassin_health = self.health(run, bot)
        before = assassin_health.total
        run.step({player: (*self.aim_at_body(run, player, bot), FIRE)})
        for _ in range(45):
            run.step({})
            if assassin_health.total < before:
                break
        self.assertEqual(assassin_health.total, before - 120)

    def test_concussion_displaces_an_approaching_assassin_before_it_resumes_autonomous_capture(self):
        run, player, _bot = self.arena()
        npc.remove_spawned(run.match)
        y = player.mover.position[1]
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(run.match, definition, (0, y, 12), team=1, autonomous=True)
        for _ in range(7):
            run.step({})
        mover = bot.impulse_mover
        self.assertIsNotNone(mover)
        self.assertLess(bot.velocity[2], 0)
        before = self.health(run, bot).total
        launched = False
        for frame in range(40):
            aim = self.aim_at_body(run, player, bot)
            run.step({player: (*aim, 16 if frame == 0 else 0)})
            if bot.velocity[2] > 0 and bot.velocity[1] > 0:
                launched = True
                break
        self.assertTrue(launched)
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(tuple(mover.position), bot.position)
        self.assertEqual(self.health(run, bot).total, before)
        self.assertEqual(self.health(run, player).total, 200)

        fight = run.match.combat
        for _ in range(8):
            run.step({})
            self.assertFalse(fight.is_pinned(player))
        for _ in range(240):
            if fight.is_pinned(player):
                break
            run.step({})
        self.assertTrue(fight.is_pinned(player))
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(self.health(run, player).total, 175)
        self.assertEqual(self.health(run, bot).total, before)

    def test_jump_jet_delays_autonomous_capture_until_pharah_descends(self):
        contact_frames = []
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, pharah, _ = self.arena()
                self.assertEqual(len(npc.remove_spawned(run.match)), 1)
                definition = next(row for row in self.catalog.values() if row.name == "Assassin")
                ground = pharah.mover.position[1]
                bot = npc.spawn(run.match, definition, (0, ground, 8), team=1, autonomous=True)
                fight = run.match.combat
                vitals = self.health(run, pharah)
                start = run.frame
                run.step({pharah: (0, 0, 8 if jump else 0)})
                if jump:
                    self.assertEqual(pharah.mover.velocity[1], 20)
                max_height = ground
                for _ in range(260):
                    if fight.is_pinned(pharah):
                        break
                    self.assertEqual(vitals.total, 200)
                    run.step({})
                    max_height = max(max_height, pharah.mover.position[1])
                self.assertTrue(fight.is_pinned(pharah))
                self.assertEqual(vitals.total, 175)
                self.assertEqual(bot.body_script.associated_component.entity_var(1650).value(),
                                 expr.Entity(pharah.body))
                if jump:
                    self.assertGreater(max_height, ground + 3)
                    self.assertLess(pharah.mover.position[1], max_height - 2)
                contact_frames.append(run.frame - start)
        self.assertGreater(contact_frames[1], contact_frames[0] + 15)

    def test_lethal_pharah_primary_stops_autonomous_assassin_before_capture(self):
        run, pharah, _ = self.arena()
        self.assertEqual(len(npc.remove_spawned(run.match)), 1)
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        bot = npc.spawn(run.match, definition, (0, pharah.mover.position[1], 12), team=1, autonomous=True)
        fight = run.match.combat
        target = self.health(run, bot)
        target.damage(target.total - 60)
        pharah.body_script.ult_seconds = 0
        pharah.body_script.set_ult_charge(0)
        for _ in range(7):
            run.step({})
        self.assertLess(bot.velocity[2], 0)
        associated = bot.body_script.associated_component
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(pharah.body))

        aim = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*aim, 1)})
        for _ in range(55):
            if target.dead:
                break
            run.step({})
            self.assertFalse(fight.is_pinned(pharah))
        self.assertTrue(target.dead)
        self.assertEqual(target.killer, pharah.body)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 60)
        position = tuple(bot.position)
        for _ in range(90):
            run.step({})
            self.assertFalse(fight.is_pinned(pharah))
        self.assertFalse(bot.attack_input)
        self.assertNotIn(36, bot.body_script.component.held)
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_manual_message_uses_actual_associated_writer_and_typed_member(self):
        run, player, bot = self.arena()
        script = bot.body_script
        associated = script.associated_component
        self.assertIsNone(associated.entity_var(1650, create=False))
        npc_control.start(run.match, player)
        run.step({})
        self.assertEqual(associated.entity_var(14365).value(), expr.Entity(player.body))
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(player.body))
        attack = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(attack.vars[536].value(), expr.Entity(player.body))
        self.assertEqual(attack.evaluate({'$': 'STU_DB742151', 'm_entity': {'$': 'STU_B5A0CAF0'}}),
                         expr.Entity(associated.entity))
        self.assertNotIsInstance(attack.vars[536].value(), expr.Map)

    def test_stop_releases_manual_input_and_authored_target(self):
        run, player, bot = self.arena()
        npc_control.start(run.match, player)
        run.step({})
        self.assertIn(36, bot.body_script.component.held)
        npc_control.stop(run.match)
        for _ in range(3):
            run.step({})
        self.assertNotIn(36, bot.body_script.component.held)
        self.assertFalse(expr.truthy(bot.body_script.associated_component.entity_var(1650).value()))

    def test_actual_contact_capture_deals_authored25_once(self):
        run, player, _bot = self.arena()
        npc_control.start(run.match, player)
        for _ in range(60):
            run.step({})
            self.assertEqual(self.health(run, player).total, 175)

    def test_actual_pin_dot_starts_after_authored_delay_and_stop_releases_control(self):
        run, player, bot = self.arena()
        npc_control.start(run.match, player)
        for _ in range(60):
            run.step({})
        self.assertEqual(self.health(run, player).total, 175)
        self.assertTrue(run.match.combat.is_pinned(player))
        for _ in range(3):
            run.step({})
        self.assertAlmostEqual(self.health(run, player).total, 174.3, places=5)
        for _ in range(12):
            run.step({})
        self.assertAlmostEqual(self.health(run, player).total, 155.1, places=5)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.is_pinned(player))
        before = self.health(run, player).total
        for _ in range(90):
            run.step({})
        self.assertEqual(self.health(run, player).total, before)
        self.assertNotIn(36, bot.body_script.component.held)

    def test_projected_barrier_intercepts_assassin_entry_or_existing_pin_damage(self):
        for already_pinned in (False, True):
            with self.subTest(already_pinned=already_pinned):
                run, pharah, bot = self.arena()
                match = run.match
                fight = match.combat
                zarya = run.players[1]
                self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team = zarya.team = 0
                zarya.mover.position = [4.5, pharah.mover.position[1], 0]
                zarya.mover.body = None
                for _ in range(30):
                    run.step({})
                health = self.health(run, pharah)
                if already_pinned:
                    self.assertEqual(npc_control.start(match, pharah), bot.entity)
                    run.step({})
                    self.assertTrue(fight.is_pinned(pharah))
                    self.assertEqual(health.total, 175)
                run.step({zarya: (-16384, 1000, 16)})
                for _ in range(3):
                    run.step({})
                barrier = fight.zarya_bubbles[zarya.body, zarya_barrier.PROJECTED]
                self.assertIs(barrier.target, pharah)
                self.assertEqual(barrier.health, 200)
                if not already_pinned:
                    self.assertEqual(npc_control.start(match, pharah), bot.entity)
                first_credit = None
                for _ in range(75):
                    run.step({})
                    self.assertEqual(fight.is_pinned(pharah), already_pinned)
                    self.assertEqual(health.total, 175 if already_pinned else 200)
                    if first_credit is None and barrier.health < 200:
                        energy = zarya.body_script.component.entity_var(87).value()
                        first_credit = (200 - barrier.health) * .2
                        self.assertAlmostEqual(energy, first_credit, places=4)
                self.assertIsNotNone(first_credit)
                if already_pinned:
                    self.assertLess(barrier.health, 175)
                else:
                    self.assertEqual(barrier.health, 175)
                for _ in range(85):
                    run.step({})
                    self.assertEqual(fight.is_pinned(pharah), already_pinned)
                    if barrier.phase() is None:
                        break
                self.assertIsNone(barrier.phase())
                if already_pinned:
                    run.step({})
                    self.assertLess(health.total, 175)
                else:
                    self.assertEqual(health.total, 200)
                    npc_control.stop(match)
                    run.step({})
                    self.assertEqual(npc_control.start(match, pharah), bot.entity)
                    for _ in range(40):
                        run.step({})
                        if fight.is_pinned(pharah):
                            break
                    self.assertTrue(fight.is_pinned(pharah))
                    self.assertEqual(health.total, 175)

    def test_orisa_fortify_blocks_assassin_capture_and_periodic_damage(self):
        run, orisa, bot = self.arena()
        match = run.match
        y = orisa.mover.position[1]
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position = [0, y, 0]
        orisa.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(defenses.fortified(orisa))
        orisa.mover.yaw, orisa.mover.pitch = self.aim_at_body(run, orisa, bot)
        self.assertEqual(npc_control.start(match, orisa), bot.entity)
        health = self.health(run, orisa)
        before = health.total
        for _ in range(90):
            run.step({})
            self.assertFalse(match.combat.is_pinned(orisa))
        self.assertAlmostEqual(before - health.total, 10, places=5)

    def test_capture_blocks_commands_without_spending_pharah_resources_then_recovers(self):
        run, player, _bot = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        npc_control.start(run.match, player)
        run.step({})
        self.assertTrue(run.match.combat.is_pinned(player))
        position = tuple(player.mover.position)
        instances = {i.graph.index: i for i in script.component.instances.values()}
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            player.take_commands([Command(run.frame, buttons=1 | 2 | 8 | 16 | 32 | 0x800,
                                          forward=127)])
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(instances[0x23].vars[53].value(), 6)
        self.assertEqual(instances[0x25].vars[87].value(), 100)
        self.assertEqual(instances[0x27].vars[61].value(), 0)
        self.assertEqual(instances[0x7B].vars[61].value(), 0)
        self.assertFalse(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), script.component.entity_var(338).value())
        npc_control.stop(run.match)
        run.step({})
        run.step({player: (0, 0, 8)})
        self.assertEqual(player.mover.velocity[1], 20)

    def test_autonomous_assassin_interrupts_barrage_without_resuming_after_release(self):
        run, player, _bot = self.arena()
        self.assertEqual(len(npc.remove_spawned(run.match)), 1)
        fight = run.match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        away = (-32768, 0, 0)
        run.step({player: (-32768, 0, 32)})
        for _ in range(8):
            run.step({player: away})
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        self.assertTrue(script.ultimate_running())
        self.assertGreater(volley.reported, 0)

        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot = npc.spawn(run.match, definition, (0, player.mover.position[1], 1.8),
                        team=1, autonomous=True)
        for _ in range(40):
            run.step({player: away})
            if fight.is_pinned(player):
                break
        self.assertTrue(fight.is_pinned(player))
        controller = bot.body_script.associated_component
        self.assertEqual(controller.entity_var(1650).value(), expr.Entity(player.body))
        self.assertEqual(self.health(run, player).total, 175)
        self.assertFalse(script.ultimate_running())
        count = volley.reported
        for _ in range(12):
            run.step({player: away})
        self.assertEqual(volley.reported, count)
        npc_control.stop(run.match)
        self.assertFalse(fight.is_pinned(player))
        for _ in range(12):
            run.step({player: away})
        self.assertFalse(script.ultimate_running())
        self.assertEqual(volley.reported, count)

    def test_capture_interrupts_running_barrage_and_preserves_fresh_listener(self):
        run, player, _bot = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (-32768, 0, 32)})  # fire away from the Assassin
        self.assertTrue(script.ultimate_running())
        player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, _bot)
        npc_control.start(run.match, player)
        run.step({})
        self.assertTrue(run.match.combat.is_pinned(player))
        self.assertFalse(script.ultimate_running())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        count = volley.reported
        for _ in range(12):
            run.step({player: (-32768, 0, 32)})
        self.assertEqual(volley.reported, count)
        npc_control.stop(run.match)
        run.step({})
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())

    def test_manual_target_beyond_contact_range_causes_no_damage_or_capture(self):
        run, player, bot = self.arena(distance=8)
        npc_control.start(run.match, player)
        for _ in range(90):
            run.step({})
        self.assertIn(36, bot.body_script.component.held)
        self.assertEqual(bot.body_script.associated_component.entity_var(1650).value(),
                         expr.Entity(player.body))
        self.assertEqual(self.health(run, player).total, 200)
        self.assertFalse(run.match.combat.is_pinned(player))

    def test_capture_releases_hover_thrust(self):
        run, player, bot = self.arena()
        run.step({player: (0, 0, 2)})
        hover = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x25)
        self.assertTrue(any(s.active and s.node.cls == 'STU_A9C4FF83' for s in hover.states.values()))
        player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, bot)
        npc_control.start(run.match, player)
        run.step({})
        self.assertTrue(run.match.combat.is_pinned(player))
        self.assertFalse(any(s.active and s.node.cls == 'STU_A9C4FF83' for s in hover.states.values()))
        fuel = hover.vars[87].value()
        for _ in range(12):
            run.step({player: (0, 0, 2)})
        self.assertGreaterEqual(hover.vars[87].value(), fuel)

    def test_target_becoming_teammate_releases_capture(self):
        run, player, bot = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertNotEqual(player.team, bot.team)
        npc_control.start(run.match, player)
        run.step({})
        self.assertTrue(run.match.combat.is_pinned(player))
        player.team = bot.team
        for _ in range(90):
            run.step({})
        self.assertFalse(run.match.combat.is_pinned(player))
        self.assertEqual(self.health(run, player).total, 175)
        self.assertNotIn(36, bot.body_script.component.held)

    def test_authored_damage_eventually_kills_and_releases_target(self):
        run, player, bot = self.arena()
        vitals = self.health(run, player)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(220):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertFalse(run.match.combat.pins)
        self.assertNotIn(36, bot.body_script.component.held)
        self.assertFalse(expr.truthy(bot.body_script.associated_component.entity_var(1650).value()))

    def test_autonomous_assassin_kills_then_reacquires_pharahs_replacement_body(self):
        run, pharah, _ = self.arena()
        self.assertEqual(len(npc.remove_spawned(run.match)), 1)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team, run.players[1].team = 0, 1
        position = tuple(pharah.mover.position)
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        bot = npc.spawn(run.match, definition, (0, position[1], 8), team=1, autonomous=True)
        fight = run.match.combat
        associated = bot.body_script.associated_component
        original = pharah.body
        vitals = self.health(run, pharah)
        self.assertEqual(vitals.total, 200)
        for _ in range(120):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        self.assertLess(bot.position[2], 3)
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(original))
        self.assertEqual(vitals.total, 175)
        for _ in range(220):
            if vitals.dead:
                break
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        for _ in range(6):
            run.step({})
        self.assertFalse(fight.is_pinned(pharah))
        self.assertFalse(bot.attack_input)
        self.assertFalse(expr.truthy(associated.entity_var(1650).value()))

        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, original)
        pharah.mover.position = list(position)
        pharah.mover.body = None
        replacement = self.health(run, pharah)
        self.assertEqual(replacement.total, 200)
        for _ in range(120):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        self.assertEqual(associated.entity_var(1650).value(), expr.Entity(pharah.body))
        self.assertEqual(replacement.total, 175)
        self.assertEqual(vitals.total, 0)
        override = associated.entity_var(14365, create=False)
        self.assertFalse(override is not None and expr.truthy(override.value()))

    def test_occlusion_releases_capture_and_target_without_late_damage(self):
        run, player, bot = self.arena()
        npc_control.start(run.match, player)
        run.step({})
        self.assertTrue(run.match.combat.is_pinned(player))
        y = player.mover.position[1]
        wall = World([(-20, y, -20, -20, y, 20, 20, y, 20),
                      (-20, y, -20, 20, y, 20, 20, y, -20),
                      (-10, y-5, .9, 10, y-5, .9, 10, y+5, .9),
                      (-10, y-5, .9, 10, y+5, .9, -10, y+5, .9)])
        with patch.object(run.match, 'collision_world', wall):
            for _ in range(90):
                run.step({})
        self.assertEqual(self.health(run, player).total, 175)
        self.assertFalse(run.match.combat.is_pinned(player))
        self.assertNotIn(36, bot.body_script.component.held)
        self.assertFalse(expr.truthy(bot.body_script.associated_component.entity_var(1650).value()))

    def test_teammate_quick_melee_rescues_pharah_only_on_enemy_body_hit(self):
        for case in ("hit", "cover", "range", "friendly"):
            with self.subTest(case=case):
                run, pharah, bot = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                rescuer = run.players[1]
                pharah.team = 0
                rescuer.team = bot.team if case == "friendly" else pharah.team
                y = pharah.mover.position[1]
                if case == "cover":
                    match.collision_world = World([
                        (-20, y, -20, -20, y, 20, 20, y, 20),
                        (-20, y, -20, 20, y, 20, 20, y, -20),
                        (-10, y - 5, 2.55, 10, y - 5, 2.55, 10, y + 5, 2.55),
                        (-10, y - 5, 2.55, 10, y + 5, 2.55, -10, y + 5, 2.55),
                    ])
                self.assertEqual(npc_control.start(match, pharah), bot.entity)
                run.step({})
                self.assertTrue(fight.is_pinned(pharah))
                rescuer.mover.position = [0, y, 7 if case == "range" else 3.3]
                rescuer.mover.body = None
                before = self.health(run, bot).total
                yaw, pitch = self.aim_at_body(run, rescuer, bot)
                run.step({rescuer: (yaw, pitch, 0x800)})
                for _ in range(16):
                    run.step({})
                self.assertEqual(self.health(run, bot).total, before - 30 if case == "hit" else before)
                self.assertEqual(fight.is_pinned(pharah), case != "hit")
                if case == "hit":
                    self.assertNotIn(36, bot.body_script.component.held)
                    for _ in range(90):
                        run.step({})
                    self.assertEqual(self.health(run, pharah).total, 175)
                    run.step({pharah: (0, 0, 8)})
                    self.assertEqual(pharah.mover.velocity[1], 20)

    def test_autonomous_assassin_reengages_after_quick_melee_rescue(self):
        run, pharah, _ = self.arena()
        npc.remove_spawned(run.match)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        rescuer = run.players[1]
        pharah.team = rescuer.team = 0
        y = pharah.mover.position[1]
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(match, definition, (0, y, 1.8), team=1, autonomous=True)
        fight = match.combat
        for _ in range(40):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        rescuer.mover.position = [0, y, 3.3]
        rescuer.mover.body = None
        yaw, pitch = self.aim_at_body(run, rescuer, bot)
        run.step({rescuer: (yaw, pitch, 0x800)})
        for _ in range(20):
            if not fight.is_pinned(pharah):
                break
            run.step({})
        self.assertFalse(fight.is_pinned(pharah))
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        recovery = attack.states[attack.graph.node(37).state]
        self.assertTrue(recovery.active)
        for _ in range(125):
            if not recovery.active:
                break
            self.assertFalse(fight.is_pinned(pharah))
            self.assertEqual(self.health(run, pharah).total, 175)
            run.step({})
        self.assertFalse(recovery.active)
        self.assertEqual(recovery.ended_at - recovery.start, 2000)
        for _ in range(40):
            if fight.is_pinned(pharah):
                break
            run.step({})
        self.assertTrue(fight.is_pinned(pharah))
        self.assertEqual(self.health(run, pharah).total, 150)

    def test_source_stun_releases_capture_and_requires_new_manual_engagement(self):
        run, player, bot = self.arena()
        npc_control.start(run.match, player)
        run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._hit_effect(target, combat.FLASHBANG_EFFECT)
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(fight.is_pinned(player))
        self.assertFalse(bot.firing)
        for _ in range(140):
            run.step({})
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(self.health(run, player).total, 175)
        self.assertNotIn(36, bot.body_script.component.held)

    def test_death_removal_and_player_replacement_release_control(self):
        for action in ('source_death', 'victim_death', 'remove', 'replace', 'failure'):
            with self.subTest(action=action):
                run, player, bot = self.arena()
                npc_control.start(run.match, player)
                run.step({})
                fight = run.match.combat
                self.assertTrue(fight.is_pinned(player))
                if action in ('source_death', 'victim_death'):
                    victim = bot if action == 'source_death' else player
                    source = player if victim is bot else bot
                    target = next(t for t in fight._targets() if t.owner is victim)
                    fight._damage(source, target, self.health(run, victim).total+1, False, None, run.frame)
                    self.assertTrue(self.health(run, victim).dead)
                elif action == 'remove':
                    npc.remove_spawned(run.match)
                elif action == 'failure':
                    bot.body_script.retire(failed=True)
                else:
                    self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
                self.assertFalse(fight.pins)
                for _ in range(3):
                    run.step({})
                self.assertFalse(fight.pins)

    def test_health_clock_is_passively_stored_and_strict_owner_is_refused(self):
        from ow174.game import assassin

        run, player, bot = self.arena()
        npc_control.start(run.match, player)
        for _ in range(75):
            run.step({})
        component = bot.body_script.component
        attack = next(i for i in component.instances.values() if i.graph.index == 0x1157)
        health = attack.states[attack.graph.node(15).state]
        self.assertEqual(health.last_applied, component.now)
        with patch.object(type(attack), 'evaluate', side_effect=AssertionError('passive export')):
            before = snapshot_digest(export_component_vm(component))
            health.last_applied -= 1
            self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
            health.last_applied += 1
            self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        component.strict = True
        self.assertIsNone(assassin.owner(component.world))
        self.assertIs(assassin.modify_health(component.world, health, 16), NotImplemented)


if __name__ == '__main__':
    unittest.main()
