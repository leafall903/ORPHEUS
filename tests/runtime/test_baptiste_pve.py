"""Opt-in actual five-NPC static fixtures; no AI or original-client acceptance.

ORPHEUS_PVE_NPC_DATA names the external catalog/graph directory. The selected
runtime must also contain explicit mover rows for every NPC; a generic fallback
cannot prove this suite's collision/range scope. No assets enter the checkout.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_combat as combat_fixture
from test_baptiste_combat import BAPTISTE
from test_baptiste_matrix import deploy_matrix
from test_game_combat import Run, aim, in_world

from ow174.game import mover, npc
from ow174.game.collision import World, flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph

DATA = os.environ.get("ORPHEUS_PVE_NPC_DATA")


def clear_graph_caches():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


@unittest.skipUnless(DATA, "requires explicit external five-NPC catalog/graph data")
class BaptistePveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = npc.load_catalog(Path(DATA) / "pve_npcs_174.json")
        mover.mover_data(next(iter(cls.catalog.values())).body)
        table = mover.loaded_mover_table()
        missing = [row.name for row in cls.catalog.values() if row.body not in table]
        if missing:
            raise RuntimeError(f"NPC acceptance requires explicit mover rows: {missing}; "
                               "select a prepared ORPHEUS_RUNTIME_DIR")
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", Path(DATA) / "game_graphs_174.json.gz")
        cls.graph_patch.start()
        clear_graph_caches()

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        clear_graph_caches()

    def make(self, definition, team=1, distance=8):
        run = Run()
        match, player = run.match, run.players[0]
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[BAPTISTE])
        player.mover.position = [0.0, 1.0, 0.0]
        player.team = 0
        # Explicit adapter transport for death delivery; no wire ACK claim.
        player.client.queue_bot_death = lambda entity, event: None
        player.client.queue_bot_death_update = lambda entity, event: None
        bot = npc.spawn(match, definition, (0.0, 1.0, float(distance)), team=team)
        self.assertIsNotNone(bot.body_script)
        return run, player, bot, match.combat, match.combat.vitals[bot.entity]

    def test_npc_close_attacks_consume_authored_damage_and_reach_on_match_clock(self):
        catalog = {row.name: row for row in self.catalog.values()}
        cases = (
            ("Heavy Assault", 31, 3.99, "front", 120, False),
            ("Heavy Assault", 31, 4.01, "front", 200, False),
            ("Trooper", 31, 1.49, "front", 170, False),
            ("Trooper", 31, 1.50, "front", 200, False),
            ("Heavy Assault", 35, 2.49, "side", 150, False),
            ("Heavy Assault", 35, 2.51, "side", 200, False),
            ("Heavy Assault", 35, 3.14, "front", 200, True),
            ("Heavy Assault", 35, 3.16, "front", 200, False),
        )
        for name, button, distance, direction, expected, capture in cases:
            with self.subTest(npc=name, button=button, distance=distance, direction=direction):
                run, player, bot, fight, _ = self.make(catalog[name], distance=distance)
                player.client.bot_stream = lambda _: None
                bot.yaw = -32768
                if direction == "side":
                    # Sample laterally from the side-hit sphere centre, outside the grab cone.
                    bot.position = (distance, 1.0, -0.65)
                    bot.yaw = 0
                bot.firing = True
                script = bot.body_script
                first = run.frame + 1
                advance = script.advance_live

                def tick(frame, position, *, edges=(), advance=advance, first=first, button=button):
                    if frame in (first, first + 1):
                        edges = (*edges, (button, frame == first))
                    return advance(frame, position, edges=edges)

                with patch.object(script, "advance_live", tick):
                    for _ in range(60):
                        run.step({})
                self.assertIs(bot.body_script, script)
                self.assertEqual(fight.vitals[player.body].total, expected)
                if direction == "side":
                    self.assertEqual(player.mover.position[0] < 0, expected < 200)
                self.assertEqual(any(instance.graph.index == 0x1407
                                     for instance in player.body_script.component.instances.values()), capture)

    def test_heavy_marked_contact_uses_distinct_damage_stage(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for flag, distance, expected in ((961, .99, 25), (961, 1.01, 200), (3692, .99, 200)):
            with self.subTest(flag=flag, distance=distance):
                run, player, bot, fight, _ = self.make(definition, distance=distance)
                player.client.bot_stream = lambda _: None
                player.body_script.component.set(flag, True)
                bot.yaw = -32768
                bot.firing = True
                script = bot.body_script
                first = run.frame + 1
                advance = script.advance_live

                def tick(frame, position, *, edges=(), first=first, advance=advance):
                    if frame in (first, first + 1):
                        edges = (*edges, (35, frame == first))
                    return advance(frame, position, edges=edges)

                with patch.object(script, "advance_live", tick):
                    for _ in range(65):
                        run.step({})
                self.assertIs(bot.body_script, script)
                self.assertEqual(fight.vitals[player.body].total, expected)

    def test_heavy_primary_timeout_pushes_after_releasing_the_victim(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, fight, _ = self.make(definition, distance=2)
        run.match.collision_world = flat_world(1.0)
        player.client.bot_stream = lambda _: None
        bot.yaw = -32768
        bot.firing = True
        script = bot.body_script
        first = run.frame + 1
        advance = script.advance_live

        def tick(frame, position, *, edges=()):
            if frame in (first, first + 1):
                edges = (*edges, (35, frame == first))
            return advance(frame, position, edges=edges)

        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1408)
        with patch.object(script, "advance_live", tick):
            for _ in range(244):
                run.step({})
            before = tuple(player.mover.position)
            run.step({})
            released = tuple(player.mover.position)
            run.step({})

        release = instance.states[instance.graph.node(88).state]
        self.assertEqual(release.counter, 2)
        self.assertFalse(release.active)
        self.assertEqual(fight.vitals[player.body].total, 200)
        self.assertGreater(released[1], before[1])
        self.assertGreater(released[2], before[2])
        self.assertLess(player.mover.position[2], released[2])

    def test_heavy_primary_wall_contact_applies_one_impact_and_recovers(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, player, bot, fight, _ = self.make(definition, distance=2)
                run.match.collision_world = World([
                    (-100, 1, -100, 100, 1, -100, 100, 1, 100),
                    (-100, 1, -100, 100, 1, 100, -100, 1, 100),
                    (-8, -1, -3, 8, -1, -3, 8, 12, -3),
                    (-8, -1, -3, 8, 12, -3, -8, 12, -3),
                ])
                player.client.bot_stream = lambda _: None
                if protected:
                    for frame in range(55):
                        run.step({player: (0, 8192, 16 if frame == 0 else 0)})
                    field, = fight.baptiste.fields.values()
                    self.assertIn(player.body, field.protections)
                bot.yaw = -32768
                bot.firing = True
                script = bot.body_script
                first = run.frame + 1
                advance = script.advance_live

                def tick(frame, position, *, edges=()):
                    if frame in (first, first + 1):
                        edges = (*edges, (35, frame == first))
                    return advance(frame, position, edges=edges)

                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1408)
                with patch.object(script, "advance_live", tick):
                    for _ in range(65):
                        run.step({})
                    impact = instance.states[instance.graph.node(29).state]
                    self.assertEqual(impact.counter, 1)
                    self.assertEqual(instance.find_var(0, 59).value(), 2)
                    self.assertEqual(fight.vitals[player.body].total, 20 if protected else 0)
                    self.assertLess(bot.position[2], 0)
                    self.assertGreater(player.mover.position[2], -3)
                    for _ in range(130):
                        run.step({})
                    reaction = next(i for i in script.component.instances.values() if i.graph.index == 0x1254)
                    lock = reaction.states[reaction.graph.node(61).state]
                    self.assertTrue(lock.active)
                    for _ in range(105):
                        run.step({})
                self.assertIs(bot.body_script, script)
                self.assertFalse(lock.active)
                self.assertEqual(impact.counter, 1)
                self.assertEqual(instance.find_var(0, 59).value(), 0)
                self.assertGreater(instance.find_var(0, 61).value(), 0)
                self.assertFalse(any(i.graph.index == 0x1407 for i in player.body_script.component.instances.values()))

    def test_all_five_use_explicit_body_movers_for_geometry_and_speed(self):
        expected = {"Trooper": (1.7, 0.5, 5.75), "Enforcer": (1.9, 0.5, 5.0),
                    "Sniper": (1.75, 0.5, 4.5), "Assassin": (1.5, 0.5, 14.0),
                    "Heavy Assault": (2.3, 0.7, 2.2)}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                _, _, bot, fight, _ = self.make(definition)
                target = next(item for item in fight._targets() if item.owner is bot)
                self.assertIn(bot.body, mover.loaded_mover_table())
                self.assertEqual((target.height, target.radius, bot.speed), expected[definition.name])

    def test_explicit_mover_capsules_cover_primary_side_range_and_wall_boundaries(self):
        for definition in self.catalog.values():
            for case in ("side_hit", "side_miss", "range25", "range45", "wall"):
                with self.subTest(npc=definition.name, case=case):
                    run, player, bot, fight, vitals = self.make(definition)
                    values = mover.mover_data(bot.body)
                    x = (values.stand_radius + (0.01 if case == "side_miss" else -0.01)
                         if case.startswith("side_") else 0.0)
                    distance = 25 if case == "range25" else 45 if case == "range45" else 8
                    z = distance + values.stand_radius if case.startswith("range") else distance
                    bot.position = (x, fight._eye(player)[1] - values.stand_height / 2, z)
                    if case == "wall":
                        run.match.collision_world = World([(-10, -10, 3, 10, -10, 3, 0, 10, 3)])
                    weapon = next(item for item in player.body_script.component.instances.values()
                                  if item.graph.index == 0x1932)
                    before = vitals.total
                    # Explicit static-ray fixture: actual hit definition, no NPC
                    # navigation or player-pose integration is implied here.
                    combat_fixture.BaptisteCombatTests().fire(run, player, fight, weapon, 6)
                    expected = 0 if case in ("side_miss", "wall") else 7.2 if case == "range45" else 24
                    self.assertAlmostEqual(before - vitals.total, expected, places=5)

    def test_primary_actual_input_hits_all_five_with_authored_base_pools(self):
        self.assertEqual(len(self.catalog), 5)
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _, fight, vitals = self.make(definition)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                before = vitals.total
                yaw, pitch = aim(fight._eye(player), (0.0, 2.0, 8.0))
                for _ in range(30):
                    run.step({player: (yaw, pitch, 1)})
                    if vitals.total < before:
                        break
                self.assertEqual(before - vitals.total, 24)
                self.assertEqual(player.body_script.component.vars[342].value(), 24)
                self.assertEqual(vitals.most[1], definition.armour)
                self.assertEqual(vitals.most[2], definition.shields)

    def test_matrix_actual_primary_crossing_doubles_damage_to_all_five(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _, fight, vitals = self.make(definition)
                deploy_matrix(run, player)
                before = vitals.total
                yaw, pitch = aim(fight._eye(player), (0.0, 2.0, 8.0))
                for _ in range(30):
                    run.step({player: (yaw, pitch, 1)})
                    if vitals.total < before:
                        break
                self.assertEqual(before - vitals.total, 48)

    def test_actual_damage_fills_authored_cost_then_matrix_spends_it(self):
        definition = max(self.catalog.values(), key=lambda row: row.health)
        run, player, _, fight, vitals = self.make(definition)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        cost = script.component.vars[338].value()
        self.assertGreater(vitals.total, cost)
        yaw, pitch = aim(fight._eye(player), (0.0, 2.0, 8.0))
        before = vitals.total
        for frame in range(2200):
            buttons = 0x400 if frame in (650, 1300) else 1
            run.step({player: (yaw, pitch, buttons)})
            if script.component.vars[342].value() >= cost:
                break
        self.assertEqual(script.component.vars[342].value(), cost)
        self.assertGreaterEqual(before - vitals.total, cost)
        self.assertFalse(vitals.dead)
        self.spend_earned_matrix(run, player, fight)

    def spend_earned_matrix(self, run, player, fight):
        script = player.body_script
        # Actual placement inputs, with no GM refill between damage and cast.
        for _ in range(40):
            run.step({player: (0, 8192, 0)})
        run.step({player: (0, 8192, 0x20)})
        for _ in range(7):
            run.step({player: (0, 8192, 0)})
        run.step({player: (0, 8192, 0x20)})
        run.step({player: (0, 8192, 0)})
        run.step({player: (0, 8192, 1)})
        for _ in range(30):
            run.step({})
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertEqual(len(fight.baptiste.matrices), 1)

    def test_actual_healing_fills_authored_cost_then_matrix_spends_it(self):
        definition = max(self.catalog.values(), key=lambda row: row.health)
        run, player, bot, fight, vitals = self.make(definition, team=0, distance=4)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        cost = script.component.vars[338].value()
        self.assertGreater(vitals.total, cost + 100)
        attacker = npc.spawn(run.match, definition, (20.0, 1.0, 20.0), team=1)
        target = next(item for item in fight._targets() if item.owner is bot)
        fight._damage(attacker, target, cost + 100, False, None, run.frame)
        before = vitals.total
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(3000):
                buttons = 0x400 if frame in (650, 1300, 1950) else 2
                run.step({player: (0, 0, buttons)})
                if script.component.vars[342].value() >= cost:
                    break
        self.assertEqual(script.component.vars[342].value(), cost)
        self.assertGreaterEqual(vitals.total - before, cost)
        self.spend_earned_matrix(run, player, fight)

    def test_matrix_amplifies_allied_soldier_actual_helix_direct_and_splash(self):
        definition = max(self.catalog.values(), key=lambda row: row.health)
        run, owner, _, fight, vitals = self.make(definition)
        deploy_matrix(run, owner)
        owner.mover.position[0] = 20
        source = run.match.add_player(2, "Ally Soldier", SOLDIER, owner.team, False)
        in_world(run.match, source)
        run.players.append(source)
        run.match.switch_hero(source, heroes()[SOLDIER])
        source.mover.position[:] = (0.0, 1.0, 0.0)
        before = vitals.total
        yaw, pitch = aim(fight._eye(source), (0.0, 2.0, 8.0))
        with patch.object(fight.projectiles, "fired", return_value=None):
            for _ in range(40):
                run.step({source: (yaw, pitch, 2)})
                if vitals.total < before:
                    break
        self.assertEqual(before - vitals.total, 240)

    def test_enforcer_live_health_gate_is_resolved_without_forcing_controller_gate(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        run, player, bot, fight, vitals = self.make(definition)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        health_gate = weapon.graph.node(18).field("m_condition")
        controller_gate = weapon.graph.node(1).field("m_condition")
        self.assertTrue(weapon.evaluate(health_gate))
        self.assertTrue(weapon.states[1].active)
        if bot.body_script.controller is None:
            self.assertFalse(weapon.evaluate(controller_gate))
        else:
            self.assertTrue(weapon.evaluate(controller_gate))
        # Legacy inputs cannot bypass the absent controller. Prepared controller
        # data executes its authored flag instead; full pellet/AI acceptance is separate.
        component = bot.body_script.component
        component.edges([(20, True)])
        for tick in range(run.frame + 1, run.frame + 21):
            component.run_frame(tick)
        if bot.body_script.controller is None:
            self.assertFalse(component.shots)
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._damage(player, target, vitals.total + 1000, False, None, run.frame)
        self.assertFalse(weapon.evaluate(health_gate))
        self.assertTrue(vitals.dead)

    def test_secondary_preserves_five_npc_team_semantics(self):
        for definition in self.catalog.values():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    run, player, bot, fight, vitals = self.make(definition, team=team, distance=4)
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(0)
                    attacker = npc.spawn(run.match, definition, (20.0, 1.0, 20.0), team=1 - team)
                    target = next(item for item in fight._targets() if item.owner is bot)
                    fight._damage(attacker, target, 100, False, None, run.frame)
                    before = vitals.total
                    yaw, pitch = aim(fight._eye(player),
                                     (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
                    with patch.object(fight.projectiles, "fired", return_value=None):
                        for _ in range(40):
                            run.step({player: (yaw, pitch, 2)})
                    self.assertEqual(vitals.total, before + (70 if team == 0 else 0))
                    self.assertEqual(player.body_script.component.vars[342].value(), 70 if team == 0 else 0)

    def test_regenerative_burst_restores_seventy_five_to_all_five_friendly_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, fight, vitals = self.make(definition, team=0, distance=4)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                attacker = npc.spawn(run.match, definition, (20.0, 1.0, 20.0), team=1)
                target = next(item for item in fight._targets() if item.owner is bot)
                fight._damage(attacker, target, 100, False, None, run.frame)
                before = vitals.total
                for frame in range(370):
                    run.step({player: (0, 0, 8 if frame < 20 else 0)})
                self.assertAlmostEqual(vitals.total, before + 75)
                self.assertAlmostEqual(player.body_script.component.vars[342].value(), 75, delta=0.001)

    def test_assassin_verified_eye_horizontal_and_center_hit_but_high_aim_misses_capsule(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        for mode in ('horizontal', 'center', 'high_miss'):
            with self.subTest(mode=mode):
                run, player, bot, fight, vitals = self.make(definition, team=0, distance=4)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                attacker = npc.spawn(run.match, definition, (20.0, 1.0, 20.0), team=1)
                target = next(item for item in fight._targets() if item.owner is bot)
                fight._damage(attacker, target, 100, False, None, run.frame)
                before = vitals.total
                angles = aim(fight._eye(player), (0.0, target.feet[1] + target.height / 2, 4.0))
                angles = angles if mode == 'center' else (0, -2000 if mode == 'high_miss' else 0)
                with patch.object(fight.projectiles, "fired", return_value=None):
                    for _ in range(40):
                        run.step({player: (*angles, 2)})
                expected = 0 if mode == 'high_miss' else 70
                self.assertEqual(vitals.total - before, expected)
                self.assertEqual(player.body_script.component.vars[342].value(), expected)

    def test_manual_pve_corpses_are_never_automatically_replaced(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, fight, vitals = self.make(definition)
                target = next(item for item in fight._targets() if item.owner is bot)
                fight._damage(player, target, vitals.total + 1000, False, None, run.frame)
                self.assertTrue(vitals.dead)
                self.assertTrue(bot.dead)
                fight.update(100.0, run.frame + 1000, frames=False)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0)
                self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_sniper_declared_weapon_components_are_initialized(self):
        definition = next(item for item in self.catalog.values() if item.name == "Sniper")
        _, _, bot, _, _ = self.make(definition)
        instances = list(bot.body_script.component.instances.values())
        weapon_graphs = {item.graph.index for item in instances}
        self.assertTrue({0x0015, 0x1122, 0x1134} <= weapon_graphs)
        slots = {item.graph.index: item.vars[28].value() for item in instances
                 if item.graph.index in (0x1122, 0x1134)}
        self.assertEqual(slots, {0x1122: 1, 0x1134: 2})

    def test_actual_npc_weapon_input_damages_baptiste_for_three_supported_bodies(self):
        # Explicit logical-button input represents an NPC weapon control request.
        # Enforcer's gate and Assassin's melee/specials remain open, not skipped
        # as successes. Navigation/acquisition and projectile transport are separate.
        for name, expected in (("Trooper", 20), ("Sniper", 100), ("Heavy Assault", 5)):
            with self.subTest(npc=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                run, player, bot, fight, _ = self.make(definition)
                bot.yaw = -32768
                target = next(item for item in fight._targets() if item.owner is player)
                vitals = fight.vitals_of(target)
                script = bot.body_script
                with patch.object(fight.projectiles, "fired", return_value=None):
                    for delta in range(1, 401):
                        frame = run.frame + delta
                        fight.tick = frame
                        script.component.run_frame(frame, [(20, True)] if delta == 1 else [])
                        script._report_shots()
                        for state, number, when, activation in list(script.component.shots):
                            fight._shoot(bot, None, state, number, when, activation=activation)
                        script.component.shots.clear()
                        for rocket in list(fight.rockets):
                            fight._fly(rocket)
                        if vitals.total < 200:
                            break
                self.assertEqual(200 - vitals.total, expected)

    def test_flashbang_rejects_held_enforcer_and_trooper_fire_until_a_new_press(self):
        for name in ("Enforcer", "Trooper"):
            with self.subTest(npc=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, fight, _ = self.make(definition)
                cassidy = run.match.add_player(2, "Cassidy", SOLDIER, 0, False)
                in_world(run.match, cassidy)
                run.match.switch_hero(cassidy, heroes()[0x02E0000000000042])
                run.players.append(cassidy)
                cassidy.mover.position[:] = (3.0, 1.0, 8.0)
                cassidy.mover.body = None
                bot.yaw = -32768
                bot.firing = True
                for viewer in run.players:
                    viewer.client.bot_stream = lambda entity: None
                script = bot.body_script
                advance_live = script.advance_live
                start = run.frame
                scheduled = {11: (20, True), 150: (20, False), 151: (20, True), 152: (20, False)}

                def advance(
                    frame, position, *, edges=(), scheduled=scheduled, start=start, advance_live=advance_live
                ):
                    edge = scheduled.get(frame - start)
                    if edge is not None:
                        edges = (*edges, edge)
                    return advance_live(frame, position, edges=edges)

                hp = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
                with patch.object(script, "advance_live", side_effect=advance):
                    yaw, pitch = aim(fight._eye(cassidy), (0.0, 2.0, 8.0))
                    run.step({cassidy: (yaw, pitch, 16)})
                    for _ in range(10):
                        run.step({})
                    self.assertTrue(fight.is_stunned(bot))
                    self.assertNotIn(20, script.component.held)
                    while run.frame < start + 149:
                        run.step({})
                    self.assertFalse(fight.is_stunned(bot))
                    self.assertEqual(hp.total, 200)
                    for _ in range(100):
                        run.step({})
                        if hp.total < 200:
                            break
                self.assertIs(bot.body_script, script)
                self.assertLess(hp.total, 200)

    def test_melee_actual_input_damages_all_five_enemy_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                # Keep the Assassin outside its opening capture while still inside melee reach.
                distance = 2.9 if definition.name == "Assassin" else 1.5
                run, player, _, _, vitals = self.make(definition, distance=distance)
                before = vitals.total
                for frame in range(35):
                    run.step({player: (0, 0, 0x800 if frame == 0 else 0)})
                self.assertEqual(before - vitals.total, 30)

    def test_assassin_opening_capture_preempts_same_frame_baptiste_melee(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, _, fight, vitals = self.make(definition, distance=1.5)
        before = vitals.total

        run.step({player: (0, 0, 0x800)})

        self.assertEqual(vitals.total, before)
        self.assertEqual(fight.vitals[player.body].total, 175)
        self.assertTrue(any(instance.graph.index == 0x1177
                            for instance in player.body_script.component.instances.values()))

    def test_actual_protection_state_uses_complete_npc_health_and_shield_pools(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, fight, vitals = self.make(definition)
                most = definition.health + definition.armour + definition.shields
                self.assertEqual(bot.body_script.component.maximum_health, most)
                protection = bot.body_script.component.play_loaded_graph(
                    graph.graph(0x1763), source=player.body, instigator=player.body, policy=1,
                )
                bot.body_script.component.run_now()
                self.assertAlmostEqual(bot.body_script.component.vars[19452].value(), most * 0.1,
                                       places=4)
                target = next(item for item in fight._targets() if item.owner is bot)
                fight._damage(player, target, 10000, False, None, run.frame)
                self.assertAlmostEqual(vitals.total, most * 0.1, places=4)
                self.assertFalse(bot.dead)
                bot.body_script.component.destroy(protection)
                fight._damage(player, target, 10000, False, None, run.frame)
                self.assertTrue(bot.dead)

    def cast_field(self, run, player, fight):
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(60):
                run.step({player: (0, 8192, 16 if frame == 0 else 0)})
        self.assertEqual(len(fight.deployables), 1)
        return next(iter(fight.deployables.values()))

    def test_actual_field_input_protects_all_five_friendly_npcs_until_destroyed(self):
        enemy_definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, fight, vitals = self.make(definition, team=0, distance=4)
                enemy = npc.spawn(run.match, enemy_definition, (0.0, 1.0, 12.0), team=1)
                actor = self.cast_field(run, player, fight)
                target = next(item for item in fight._targets() if item.owner is bot)
                # Explicit lethal enemy-NPC combat input, not an AI/special claim.
                fight._damage(enemy, target, 10000, False, None, run.frame)
                self.assertAlmostEqual(vitals.total, sum(vitals.most.values()) * 0.1, places=4)
                self.assertFalse(bot.dead)
                ward_target = next(item for item in fight._targets() if item.owner is actor)
                fight._damage(enemy, ward_target, 10000, False, None, run.frame)
                self.assertNotIn(actor.entity, fight.deployables)
                fight._damage(enemy, target, 10000, False, None, run.frame)
                self.assertTrue(bot.dead)
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_field_arms_before_trooper_hit_inside_retained_tick_gap(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, _, fight, _ = self.make(definition)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(1)
        player.mover.body = None
        player.client.bot_stream = lambda entity: None
        run.step({})
        start = run.frame
        hp = fight.vitals[player.body]
        hp.damage(199)
        fight._sync_body_health(player, hp)

        bot = npc.spawn(run.match, definition, (0, 1, 8), team=1)
        bot.yaw = -32768
        bot.firing = True
        script = bot.body_script
        original = script.advance_live

        def advance(frame, position, *, edges=()):
            if frame - start in (1, 2):
                edges = (*edges, (20, frame - start == 1))
            return original(frame, position, edges=edges)

        with patch.object(script, "advance_live", side_effect=advance):
            for offset in range(1, 27):
                run.step({player: (-32768, 8192, 16 if offset == 4 else 0)})
            ward, = fight.baptiste.fields.values()
            self.assertEqual(ward.active_tick, start + 28)
            self.assertNotIn(player.body, ward.protections)
            run.frame += 5
            run.step({})
        self.assertIs(bot.body_script, script)
        self.assertIn(player.body, ward.protections)
        self.assertFalse(hp.dead)
        self.assertEqual(hp.total, 20)

    def test_actual_npc_weapons_can_aim_vertically_and_hit_the_field(self):
        for name, expected in (("Trooper", 20), ("Sniper", 100), ("Heavy Assault", 5)):
            with self.subTest(npc=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                run, player, bot, fight, _ = self.make(definition)
                actor = self.cast_field(run, player, fight)
                bot.yaw, bot.pitch = aim((bot.position[0], bot.position[1] + 1.55, bot.position[2]),
                                        (actor.position[0], actor.position[1] + 0.5, actor.position[2]))
                self.assertNotEqual(bot.movement(run.frame).pitch, 0)
                script = bot.body_script
                vitals = fight.vitals[actor.entity]
                with patch.object(fight.projectiles, "fired", return_value=None):
                    for delta in range(1, 240):
                        frame = run.frame + delta
                        fight.tick = frame
                        script.component.run_frame(frame, [(20, True)] if delta == 1 else [])
                        script._report_shots()
                        for state, number, when, activation in list(script.component.shots):
                            fight._shoot(bot, None, state, number, when, activation=activation)
                        script.component.shots.clear()
                        for rocket in list(fight.rockets):
                            fight._fly(rocket)
                        if vitals.total < 150:
                            break
                self.assertLess(frame, actor.expires_tick)
                self.assertEqual(150 - vitals.total, expected)


if __name__ == "__main__":
    unittest.main()
