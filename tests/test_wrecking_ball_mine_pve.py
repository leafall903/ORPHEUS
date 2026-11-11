"""Minefield against actual external NPC bodies and controlled weapon input."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_wrecking_ball_control as control_tests
import test_wrecking_ball_emp as emp_tests
import test_wrecking_ball_mines as mine_tests
import test_wrecking_ball_pve as pve_tests
from test_game_combat import aim
from test_wrecking_ball_fireball import relocate

from ow174.game import assassin, combat, npc_charge, npc_control, npc_melee, npc_reposition, sombra_emp
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(all(os.environ.get(key) for key in (
    'ORPHEUS_BALL_MINE_BUNDLE', 'ORPHEUS_PVE_DATA_DIR', 'ORPHEUS_PVE_MOVERS',
)), 'external mine/NPC data and authored movers required')
class MinePveTests(unittest.TestCase):
    clear_graphs = staticmethod(pve_tests.BallPveTests.clear_graphs)
    scenario = pve_tests.BallPveTests.scenario

    @classmethod
    def setUpClass(cls):
        pve_tests.BallPveTests.setUpClass.__func__(cls)
        cls.bundle.stop()
        cls.bundle = patch.object(graph, 'GRAPHS_PATH', Path(os.environ['ORPHEUS_BALL_MINE_BUNDLE']))
        cls.bundle.start()
        cls.clear_graphs()

    @classmethod
    def tearDownClass(cls):
        pve_tests.BallPveTests.tearDownClass.__func__(cls)

    def test_actual_mine_explosion_interrupts_moving_heavy_charge(self):
        heavy = next(definition for definition in self.catalog.values() if definition.name == 'Heavy Assault')
        run, player, bot, target, own = self.scenario(heavy, distance=100)
        match = run.match
        fight = match.combat
        _, y, _ = player.mover.position
        match.collision_world = flat_world(y)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        player.mover.use(world=match.collision_world)
        player.body_script.ult_seconds = 0
        mine = mine_tests.MineTests().isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, player, (x, y, z - 5))
        bot.position = (x, y, z + 5)
        angles = aim(fight._eye(player), (x, y + target.height / 2, z + 5))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(match, player), bot.entity)
        charge = npc_charge._ability(bot)
        for _ in range(60):
            run.step({})
        self.assertLess(bot.position[2], z + 4)
        self.assertEqual(charge.instance.vars[59].value(), 1)
        self.assertFalse(mine.triggered)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(40):
            run.step({})
            if mine.triggered:
                break
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, before - 130)
        self.assertTrue(bot.airborne)
        self.assertGreater(bot.velocity[1], 4)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertGreater(charge.remaining(), 0)
        self.assertEqual(own.total, 600)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(12):
            run.step({})
        self.assertTrue(bot.airborne)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertEqual(own.total, 600)

    def test_actual_mine_interrupts_sniper_retreat_then_rifle_recovers(self):
        sniper = next(definition for definition in self.catalog.values() if definition.name == 'Sniper')
        run, player, bot, target, own = self.scenario(sniper, distance=100)
        match = run.match
        fight = match.combat
        _, floor, _ = player.mover.position
        match.collision_world = flat_world(floor)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        player.mover.use(world=match.collision_world)
        player.body_script.ult_seconds = 0
        mine = mine_tests.MineTests().isolated(run, player)
        self.assertTrue(mine.armed)
        x, _, z = mine.position
        relocate(run, player, (x, floor, z - 6.5))
        bot.position = (x, floor, z - 5)
        angles = aim(fight._eye(player), (x, floor + target.height / 2, z - 5))
        run.step({player: (*angles, 16)})
        self.assertEqual(own.temporary_shields, 175)
        self.assertFalse(mine.triggered)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        run.step({})
        retreat = npc_reposition.movement_state(bot)
        destination, speed = retreat.move_request()
        self.assertAlmostEqual(destination.z, z - 1)
        self.assertEqual(speed, 20)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(16):
            self.assertTrue(retreat.active)
            self.assertFalse(bot.attack_input)
            run.step({})
            if mine.triggered:
                break
        self.assertTrue(mine.triggered)
        self.assertGreater(bot.position[2], z - 5)
        self.assertLess(bot.position[2], destination.z)
        self.assertEqual(vitals.total, before - 130)
        self.assertTrue(bot.airborne)
        self.assertGreater(bot.velocity[1], 4)
        self.assertFalse(retreat.active)
        self.assertIsNone(retreat.move_request())
        self.assertEqual(own.temporary_shields, 175)
        for _ in range(180):
            run.step({})
            self.assertEqual(retreat.counter, 1)
            if own.temporary_shields < 175:
                break
        self.assertFalse(bot.airborne)
        self.assertEqual(own.temporary_shields, 75)
        self.assertEqual(own.total - own.temporary_shields, 600)
        self.assertEqual(vitals.total, before - 130)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_heavy_charge_crosses_emp_disabled_mine_before_it_rearms(self):
        self.check_heavy_charge_with_emp_disabled_mine()

    def test_actual_moving_heavy_charge_cancels_when_target_becomes_a_mine(self):
        self.check_heavy_charge_with_emp_disabled_mine(target_swap='cancel')

    def test_actual_heavy_charge_query_ignores_mine_then_hits_restored_ball_target(self):
        self.check_heavy_charge_with_emp_disabled_mine(target_swap='query')

    def check_heavy_charge_with_emp_disabled_mine(self, *, target_swap=None):
        heavy = next(definition for definition in self.catalog.values() if definition.name == 'Heavy Assault')
        run, player, bot, target, own = self.scenario(heavy, distance=100, names=('Ball', 'Sombra'))
        match = run.match
        fight = match.combat
        sombra = run.players[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        sombra.team = bot.team
        self.assertTrue(match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA]))
        x, y, z = player.mover.position
        relocate(run, sombra, (x + 100, y, z))
        match.collision_world = flat_world(y)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        player.mover.use(world=match.collision_world)
        player.body_script.ult_seconds = 0
        mine = mine_tests.MineTests().isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x + 6, y, z))
        script = sombra.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra)
        for _ in range(5):
            run.step({})
        self.assertFalse(mine.armed)
        self.assertEqual(mine.health, 50)

        relocate(run, player, (x, y, z - 5))
        bot.position = (x, y, z + 5)
        angles = aim(fight._eye(player), (x, y + target.height / 2, z + 5))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(match, player), bot.entity)
        charge = npc_charge._ability(bot)
        impact = charge.instance.node_state(charge.instance.graph.node(npc_melee.CHARGE_IMPACT))
        initial_impacts = impact.counter
        start = bot.position
        vitals = fight.vitals_of(target)
        initial_health = vitals.total
        for _ in range(130):
            run.step({})
            self.assertFalse(mine.armed)
            self.assertFalse(mine.triggered)
            self.assertEqual(mine.health, 50)
            self.assertEqual(vitals.total, initial_health)
            if target_swap is not None and math.dist(start, bot.position) > 1.5:
                break
            if own.total < 600:
                break
        if target_swap == 'query':
            self.assertGreater(math.dist(start, bot.position), 1.5)
            self.assertEqual(own.total, 600)
            self.check_charge_mine_contact_query(run, player, bot, mine, charge)
            self.assertEqual(impact.counter, initial_impacts)
            self.assertEqual(own.total, 600)
            self.assertEqual(mine.health, 50)
            bot.attack_target = player.entity
            for _ in range(60):
                run.step({})
                if own.total < 600:
                    break
            self.assertEqual(own.total, 430)
            self.assertEqual(impact.counter, initial_impacts + 1)
            self.assertEqual(charge.instance.vars[6836].value(), (expr.Entity(player.body),))
            self.assertEqual(charge.instance.vars[npc_charge.PHASE].value(), 2)
        elif target_swap == 'cancel':
            self.assertGreater(math.dist(start, bot.position), 1.5)
            self.assertEqual(own.total, 600)
            self.assertEqual(impact.counter, initial_impacts)
            bot.attack_target = mine.entity
            interrupted_at = bot.position
            for _ in range(45):
                run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(bot.position, interrupted_at)
                self.assertEqual(impact.counter, initial_impacts)
                self.assertEqual(own.total, 600)
                self.assertEqual(mine.health, 50)
            self.assertEqual(charge.instance.vars[npc_charge.PHASE].value(), 0)
            self.assertGreater(charge.remaining(), 0)
        else:
            self.assertLess(bot.position[2], z)
            self.assertEqual(own.total, 430)
            self.assertEqual(charge.instance.vars[npc_charge.PHASE].value(), 2)
        self.assertEqual(npc_control.stop(match), 1)

        for _ in range(625):
            run.step({})
            if mine.armed:
                break
        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertEqual(vitals.total, initial_health)
        bot.position = (x, y, z + 1.2)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, initial_health - 130)
        self.assertEqual(bot.velocity[1], 5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def check_charge_mine_contact_query(self, run, player, bot, mine, charge):
        fight = run.match.combat
        original_position = mine.position
        x, y, z = original_position
        # Place the mine inside the elevated query sphere as the charge passes it.
        mine.position = (x, y + 0.5, z)
        target = next(t for t in fight._enemies(bot) if t.owner is mine)
        query = charge.instance.graph.node(npc_melee.CHARGE_QUERY)
        offset = charge.evaluate(query.field('m_05BE62DD'), watch=False)
        radius = expr.to_float(charge.evaluate(query.field('m_radius'), watch=False))
        a, b = target.segment()
        in_range_queries = []
        distances = []
        track_targets = npc_melee.track_targets
        update_charge = npc_charge.update

        def keep_motion(fight, source, target, enabled):
            # Keep the real movement driver, bypassing only its invalid-target cancellation.
            if source is bot:
                return update_charge(fight, source, player, True)
            return update_charge(fight, source, target, enabled)

        def record_query(world, state):
            handled = track_targets(world, state)
            if handled and state.instance is charge.instance and state.node.pos == query.pos:
                x, y, z = bot.position
                dx, _, dz = combat.aim(bot.yaw, 0)
                centre = (x + dz * offset.x + dx * offset.z, y + offset.y, z - dx * offset.x + dz * offset.z)
                eye = (x, centre[1], z)
                distance = combat.segment_distance(centre, a, b)
                distances.append(distance)
                if (distance <= radius + target.radius
                        and combat.capsule_visible(fight._collision(), eye, a, b, target.radius)):
                    in_range_queries.append(charge.instance.vars[6836].value())
            return handled

        bot.attack_target = mine.entity
        with (patch.object(npc_charge, 'update', side_effect=keep_motion),
              patch.object(npc_melee, 'track_targets', side_effect=record_query)):
            for _ in range(65):
                run.step({})
                if bot.position[2] < mine.position[2] - 0.8:
                    break
        mine.position = original_position
        self.assertLess(bot.position[2], mine.position[2] - 0.8)
        self.assertEqual(charge.instance.vars[npc_charge.PHASE].value(), 1)
        self.assertTrue(in_range_queries, (min(distances, default=None), radius + target.radius))
        self.assertTrue(all(result == () for result in in_range_queries))
        self.assertFalse(mine.triggered)

    def test_actual_five_npcs_take_one_130_explosion_and_physically_land(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=100)
                mine = mine_tests.MineTests().isolated(run, player)
                before = run.match.combat.vitals_of(target).total
                x, y, z = mine.position
                bot.position = (x, y, z + 0.5)
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 130)
                self.assertEqual(bot.velocity[1], 5)
                self.assertGreater(bot.velocity[2], 4.9)
                for _ in range(100):
                    run.step({})
                self.assertAlmostEqual(bot.position[1], y, delta=0.02)
                self.assertGreater(bot.position[2], z + 1)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 130)

    def test_actual_five_allied_npcs_never_trigger_or_take_mine_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=100, ally=True)
                mine = mine_tests.MineTests().isolated(run, player)
                before = run.match.combat.vitals_of(target).total
                x, y, z = mine.position
                bot.position = (x, y, z + 0.5)
                for _ in range(20):
                    run.step({})
                self.assertFalse(mine.triggered)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertFalse(bot.airborne)

    def test_actual_five_enemy_npcs_require_mine_range_and_clear_line_of_sight(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=100)
                player.body_script.ult_seconds = 0
                mine = mine_tests.MineTests().isolated(run, player)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                x, y, z = mine.position
                bot.position = (x, y, z + 3)
                run.step({})
                self.assertTrue(mine.armed)
                self.assertFalse(mine.triggered)
                self.assertEqual(vitals.total, before)

                original_world = run.match.collision_world
                original_provider = run.match.collision_provider
                triangles = [tuple(original_world.data[i:i + 9])
                             for i in range(0, len(original_world.data), 13)]
                wall_z = z + 0.25
                triangles.extend([
                    (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                    (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                ])
                blocked_world = World(triangles)
                run.match.collision_world = blocked_world
                run.match.collision_provider = StaticCollisionProvider(blocked_world)
                bot.position = (x, y, z + 1.2)
                for _ in range(10):
                    run.step({})
                self.assertTrue(mine.armed)
                self.assertFalse(mine.triggered)
                self.assertEqual(vitals.total, before)
                self.assertEqual(bot.position, (x, y, z + 1.2))

                run.match.collision_world = original_world
                run.match.collision_provider = original_provider
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(vitals.total, before - 130)
                self.assertEqual(bot.velocity[1], 5)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def destroy_mine_with_npc_primary(self, run, bot, mine, name):
        x, y, z = mine.position
        distance = 3 if name == 'Enforcer' else 6
        bot.position = (x - distance, y, z)
        # Controlled weapon input; native AI acquisition remains open.
        bot.spawner = {**bot.spawner, 'manual_spawn': False}
        angles = aim((x - distance, y + combat.EYE, z), (x, y + 0.23, z))
        bot.yaw, bot.pitch = map(float, angles)
        bot.firing = True
        button = bot.body_script.component.world.ai_weapon_button(0)
        bot.body_script.component.edges([(button, True)])
        for _ in range(250):
            run.step({})
            if mine.health <= 0:
                break
        self.assertEqual(mine.health, 0)
        self.assertFalse(mine.triggered)
        bot.firing = False
        bot.body_script.component.edges([(button, False)])

    def test_controlled_trooper_enforcer_sniper_heavy_primary_destroy_fifty_hp_mine(self):
        for definition in self.catalog.values():
            if definition.name not in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _, _ = self.scenario(definition, distance=100)
                mine = mine_tests.MineTests().isolated(run, player)
                self.destroy_mine_with_npc_primary(run, bot, mine, definition.name)
                for _ in range(90):
                    run.step({})
                self.assertNotIn(mine.entity, run.match.combat.mines.entities)

    def test_controlled_npc_primaries_destroy_emp_disabled_mines_without_late_blast(self):
        for definition in self.catalog.values():
            if definition.name == 'Assassin':
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(
                    definition, distance=100, names=('Ball', 'Sombra'),
                )
                match = run.match
                fight = match.combat
                sombra = run.players[1]
                match.game_map = replace(match.game_map, free_for_all=False)
                sombra.team = bot.team
                self.assertNotEqual(player.team, bot.team)
                self.assertTrue(match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA]))
                x, y, z = player.mover.position
                relocate(run, sombra, (x, y, z + 100))
                player.body_script.ult_seconds = 0
                mine = mine_tests.MineTests().isolated(run, player)
                self.assertTrue(mine.armed)
                x, y, z = mine.position
                relocate(run, sombra, (x, y, z + 6))
                script = sombra.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                emp_tests.BallEmpTests().cast(run, sombra)
                for _ in range(5):
                    run.step({})
                component = mine.body_script.component
                effect, = (i for i in component.instances.values() if i.graph.index == sombra_emp.EFFECT)
                deadline = math.ceil((effect.start + 10000) / component.frame_ms)
                self.assertEqual(effect.source_entity(), sombra.body)
                self.assertEqual(mine.health, 50)
                self.assertFalse(mine.armed)

                self.destroy_mine_with_npc_primary(run, bot, mine, definition.name)
                self.assertLess(run.frame, deadline)
                vitals = fight.vitals_of(target)
                before = vitals.total
                bot.position = (x, y, z + 0.5)
                for _ in range(90):
                    run.step({})
                self.assertLess(run.frame, deadline)
                self.assertNotIn(mine.entity, fight.mines.entities)
                self.assertNotIn(mine.entity, fight.vitals)
                self.assertTrue(effect.teardown)
                self.assertFalse(component.instances)
                run.frame = deadline
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(mine.triggered)
                self.assertNotIn(mine.entity, fight.mines.entities)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(bot.entity, fight.npc_impulses)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_all_five_npcs_trigger_surviving_mine_after_killing_ball(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, own = self.scenario(definition, distance=1.5)
                fight = run.match.combat
                player.body_script.ult_seconds = 0
                angles = aim(fight._eye(player),
                             (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
                run.step({player: (*angles, 0)})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(4200):
                    run.step({})
                    if own.total <= 160:
                        break
                self.assertGreater(own.total, 0)
                self.assertLessEqual(own.total, 160)
                self.assertEqual(npc_control.stop(run.match), 1)

                x, y, z = player.mover.position
                bot.position = (x + 100, y, z)
                mine = mine_tests.MineTests().isolated(run, player)
                self.assertTrue(mine.body_script.primary().states[28].active)
                x, y, z = player.mover.position
                bot.position = (x, y, z + 1.5)
                angles = aim(fight._eye(player), (x, y + target.height / 2, z + 1.5))
                run.step({player: (*angles, 0)})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(1000):
                    run.step({})
                    if own.dead:
                        break
                self.assertTrue(own.dead)
                self.assertEqual(own.killer, bot.entity)
                self.assertIn(mine.entity, fight.mines.entities)
                self.assertTrue(mine.body_script.primary().states[28].active)
                self.assertEqual(mine.health, 50)

                x, y, z = mine.position
                bot.position = (x, y, z + 0.5)
                vitals = fight.vitals_of(target)
                before = vitals.total
                run.step({})
                self.assertTrue(own.dead)
                self.assertTrue(mine.triggered)
                self.assertEqual(vitals.total, before - 130)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_assassin_pin_rejects_full_minefield_then_fresh_cast_succeeds(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, target, own = self.scenario(definition, distance=1.5)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(cost)
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertLess(own.total, 600)
        for tick in range(20):
            run.step({player: (*angles, 32 if tick % 2 == 0 else 0)})
            self.assertFalse(script.component.held)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(charge.value(), cost)
        self.assertFalse(fight.mines.seen)
        self.assertFalse(fight.mines.entities)

        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.is_stunned(player))
        bot.position = (x + 100, y, z)
        for _ in range(20):
            run.step({})
        self.assertEqual(charge.value(), cost)
        self.assertFalse(fight.mines.seen)
        self.assertFalse(fight.mines.entities)

        run.step({player: (*angles, 32)})
        for _ in range(20):
            run.step({})
        self.assertEqual(charge.value(), 0)
        self.assertEqual(len(fight.mines.seen), 3)
        self.assertEqual(len(fight.mines.entities), 15)
        self.assertTrue(all(mine.source == player.body for mine in fight.mines.entities.values()))

    def test_delayed_minefield_respects_assassin_pin_start_and_release(self):
        for lead, live, reports in (
            (0, False, (18, 3, 8)),
            (1, False, ()),
            (8, False, (18,)),
            (12, False, (18, 3)),
            (1, True, ()),
            (8, True, (18,)),
            (12, True, (18, 3)),
        ):
            with self.subTest(lead=lead, live=live):
                self.check_delayed_minefield_across_assassin_pin(lead, live, reports)

    def check_delayed_minefield_across_assassin_pin(self, lead, live, reports):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, target, own = self.scenario(definition, distance=1.5)
        match = run.match
        fight = match.combat
        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        charge = script.component.entity_var(ULT_CHARGE)
        script.ult_seconds = 0
        script.set_ult_charge(cost)
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        run.step({player: (*angles, 0)})
        run.frame += 15
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        frozen = script.frame, script.component.now
        pin_start = None
        for _ in range(25):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if pin_start is None and fight.pins:
                pin_start = run.frame
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertGreater(pin_start - lead, frozen[0])
        self.assertLess(own.total, 600)
        deadline = run.frame
        if not live:
            self.assertEqual(npc_control.stop(match), 1)
        self.assertEqual(fight.is_stunned(player), live)
        self.assertEqual((script.frame, script.component.now), frozen)

        if lead:
            instances = script.component.instances
            player.take_commands([Command(pin_start - lead, buttons=32)])
            self.assertEqual(charge.value(), 0)
            self.assertFalse(any(i.graph.index == assassin.VICTIM_GRAPH for i in instances.values()))
            player.take_commands([Command(deadline)])
            self.assertTrue(all(mine.reported_frame < pin_start for mine in fight.mines.entities.values()))
            if live:
                victim, = (i for i in instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
                self.assertEqual(victim.source_entity(), bot.entity)
                self.assertEqual(fight.pins, {bot.entity: player.body})
                self.assertEqual(npc_control.stop(match), 1)
        else:
            before = tuple(player.mover.position)
            player.take_commands([Command(deadline - 1, buttons=32, forward=127)])
            self.assertEqual(charge.value(), cost)
            self.assertEqual(tuple(player.mover.position), before)
            self.assertFalse(fight.mines.seen)
            player.take_commands([Command(deadline, buttons=32)])
            self.assertEqual(charge.value(), 0)
        for _ in range(20):
            run.step({})
        self.assertEqual({key[2] for key in fight.mines.seen}, set(reports))
        self.assertEqual(len(fight.mines.entities), (0, 1, 7, 15)[len(reports)])
        self.assertEqual(charge.value(), 0)

    def test_overlapping_assassin_pin_and_sleep_preserve_both_command_windows(self):
        for sleep_starts_first in (False, True):
            for pin_ends_first in (False, True):
                with self.subTest(sleep_starts_first=sleep_starts_first, pin_ends_first=pin_ends_first):
                    self.check_assassin_pin_and_sleep_overlap(sleep_starts_first, pin_ends_first)

    def check_assassin_pin_and_sleep_overlap(self, sleep_starts_first, pin_ends_first):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, target, own = self.scenario(definition, distance=1.5, names=('Ball', 'Ana'))
        match = run.match
        fight = match.combat
        ana = run.players[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        ana.team = bot.team
        self.assertTrue(match.switch_hero(ana, heroes()[control_tests.ANA]))
        x, y, z = player.mover.position
        relocate(run, ana, (x - 4, y, z))
        for _ in range(45):
            run.step({})
        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        charge = script.component.entity_var(ULT_CHARGE)
        script.ult_seconds = 0
        script.set_ult_charge(cost)
        angles = aim(fight._eye(player), (target.feet[0], y + target.height / 2, target.feet[2]))
        run.step({player: (*angles, 0)})
        frozen = script.frame
        control = control_tests.BallControlTests()
        if sleep_starts_first:
            sleep_start, original_wake = control.sleep_dart_without_target_commands(run, ana, player)
            self.assertFalse(fight.pins)
            self.assertEqual(npc_control.start(match, player), bot.entity)
            for _ in range(25):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                if fight.pins:
                    break
        else:
            self.assertEqual(npc_control.start(match, player), bot.entity)
            sleep_start, original_wake = control.sleep_dart_without_target_commands(run, ana, player)
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertIn(player.body, fight.sleeps)
        self.assertEqual(original_wake - sleep_start, 313)
        self.assertLess(own.total, 600)
        pin_start = script.timed_control_commands[assassin.PIN_MESSAGE][0][0]
        self.assertEqual(sleep_start < pin_start, sleep_starts_first)
        self.assertNotEqual(sleep_start, pin_start)
        if pin_ends_first:
            self.assertEqual(npc_control.stop(match), 1)
            self.assertFalse(fight.pins)
            first_end = run.frame
            self.assertTrue(fight.is_stunned(player))
            match.update((original_wake - 1) * combat.TICK, original_wake - 1, frames=False)
            self.assertEqual(fight.sleeps[player.body], (sleep_start, original_wake))
            run.frame = original_wake
            match.update(run.frame * combat.TICK, run.frame, frames=False)
        else:
            wake_damage = None
            while player.body in fight.sleeps and run.frame <= original_wake:
                health_before_tick = own.total
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                if (wake_damage is None and own.total < health_before_tick
                        and (run.frame - sleep_start) * combat.TICK > combat.SLEEP_DAMAGE_GRACE):
                    wake_damage = run.frame
            first_end = run.frame
            self.assertIsNotNone(wake_damage)
            self.assertLess(first_end, original_wake)
            self.assertEqual(first_end, wake_damage + math.ceil(combat.SLEEP_WAKE / combat.TICK))
            self.assertNotIn(player.body, fight.sleeps)
            self.assertEqual(fight.pins, {bot.entity: player.body})
            self.assertTrue(fight.is_stunned(player))
            run.frame += 8
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(script.frame, frozen)

        before = tuple(player.mover.position)
        for frame in (first_end, run.frame - 1):
            player.take_commands([Command(frame, buttons=32, forward=127)])
            self.assertEqual(charge.value(), cost)
            self.assertEqual(tuple(player.mover.position), before)
            self.assertFalse(fight.mines.seen)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(charge.value(), 0)
        for _ in range(20):
            run.step({})
        self.assertEqual(len(fight.mines.seen), 3)
        self.assertEqual(len(fight.mines.entities), 15)

    def test_actual_assassin_pin_interrupts_paid_minefield_after_first_report(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, target, own = self.scenario(definition, distance=1.5)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        run.step({player: (*angles, 32)})
        for _ in range(10):
            if fight.mines.entities:
                break
            run.step({player: (*angles, 0)})
        mine, = fight.mines.entities.values()
        self.assertEqual(len(fight.mines.seen), 1)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step({player: (*angles, 32)})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertLess(own.total, 600)
        self.assertFalse(own.dead)
        for _ in range(20):
            run.step({player: (*angles, 32)})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(set(fight.mines.entities), {mine.entity})
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.is_stunned(player))
        bot.position = (x + 100, y, z)
        for _ in range(200):
            run.step({})
        self.assertEqual(set(fight.mines.entities), {mine.entity})
        self.assertEqual(len(fight.mines.seen), 1)
        self.assertTrue(mine.body_script.primary().states[28].active)
        self.assertEqual(mine.health, 50)
        vitals = fight.vitals_of(target)
        before = vitals.total
        mx, my, mz = mine.position
        bot.position = (mx, my, mz + 0.5)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, before - 130)
        self.assertEqual(bot.velocity[1], 5)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_manual_assassin_pins_ball_then_mine_lift_releases_the_pin(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, target, own = self.scenario(definition, distance=100)
        player.body_script.ult_seconds = 0
        mine = mine_tests.MineTests().isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        x, y, z = mine.position
        bot.position = (x, y, z + 2.4)
        relocate(run, player, (x + 0.8, y, z))
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(player), (x, y + 1, z + 2.4))
        run.step({player: (*angles, 16)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(bot.body_script.component.world.manual_target, player.body)
        self.assertEqual(own.temporary_shields, 150)
        self.assertEqual(mine.health, 50)
        self.assertFalse(mine.triggered)
        self.assertIn(mine.entity, fight.mines.entities)

        # Position the NPC at the trigger boundary while its actual pin is active.
        # The authored mine search, damage, lift and subsequent pin exit all run.
        before = fight.vitals_of(target).total
        bot.position = (x, y, z + 1.5)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(target).total, before - 130)
        self.assertEqual(bot.velocity[1], 5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(90):
            run.step({})
            if not fight.is_stunned(player):
                break
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        bx, by, bz = bot.position
        self.assertGreater(math.dist((bx, by + combat.EYE, bz), fight._eye(player)), assassin.PIN_RANGE)

    def test_melee_windup_cancels_when_manual_target_becomes_a_mine(self):
        for name, damage in (('Trooper', 25), ('Heavy Assault', 75)):
            with self.subTest(npc=name):
                self.check_melee_target_swapped_to_mine(name, damage)

    def test_npc_melee_graphs_reject_mine_when_windup_is_allowed_to_finish(self):
        for name, damage in (('Trooper', 25), ('Heavy Assault', 75)):
            with self.subTest(npc=name):
                self.check_melee_target_swapped_to_mine(name, damage, allow_impact=True)

    def check_melee_target_swapped_to_mine(self, name, damage, *, allow_impact=False):
        definition = next(d for d in self.catalog.values() if d.name == name)
        run, player, bot, _, own = self.scenario(definition, distance=100, names=('Ball', 'Sombra'))
        match = run.match
        fight = match.combat
        sombra = run.players[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        sombra.team = bot.team
        self.assertTrue(match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA]))
        x, y, z = player.mover.position
        relocate(run, sombra, (x + 100, y, z))
        mine = mine_tests.MineTests().isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x + 6, y, z))
        script = sombra.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra)
        for _ in range(5):
            run.step({})
        self.assertFalse(mine.armed)
        self.assertEqual(mine.health, 50)

        if allow_impact:
            # Put the disabled mine inside the Trooper's elevated search sphere.
            mine.position = (x, y + 0.4, z)
        relocate(run, player, (x + 0.4, y, z))
        bot.position = (x, y, z + 1.5)
        angles = aim(fight._eye(player), (x, y + 1, z + 1.5))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(match, player), bot.entity)
        component = bot.body_script.component
        ability = npc_melee.ability(component)
        _, query_node, impact_node, _ = npc_melee.MELEE[bot.hero]
        impact = ability.instance.node_state(ability.instance.graph.node(impact_node))
        initial_impacts = impact.counter
        for _ in range(30):
            run.step({})
            if ability.flags & nodes.Ability.RUNNING:
                break
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(impact.counter, initial_impacts)
        self.assertEqual(own.total, 600)
        bot.attack_target = mine.entity
        if allow_impact:
            target = next(t for t in fight._enemies(bot) if t.owner is mine)
            query = ability.instance.graph.node(query_node)
            offset = ability.evaluate(query.field('m_05BE62DD'), watch=False)
            radius = expr.to_float(ability.evaluate(query.field('m_radius'), watch=False))
            bx, by, bz = bot.position
            dx, _, dz = combat.aim(bot.yaw, 0)
            centre = (bx + dz * offset.x + dx * offset.z, by + offset.y, bz - dx * offset.x + dz * offset.z)
            a, b = target.segment()
            self.assertLessEqual(combat.segment_distance(centre, a, b), radius + target.radius,
                                 (centre, a, b, offset))
            eye = (bx, centre[1], bz)
            self.assertTrue(combat.capsule_visible(fight._collision(), eye, a, b, target.radius))
            query_results = []
            track_targets = npc_melee.track_targets

            def record_query(world, state):
                handled = track_targets(world, state)
                if handled and state.instance is ability.instance and state.node.pos == query_node:
                    query_results.append(ability.instance.vars[375].value())
                return handled

            # Bypass manual cancellation so the actual graph can resolve its target query.
            with (patch.object(npc_melee, 'update', return_value=True),
                  patch.object(npc_melee, 'track_targets', side_effect=record_query)):
                for _ in range(45):
                    run.step({})
                    if impact.counter != initial_impacts or not ability.flags & nodes.Ability.RUNNING:
                        break
            self.assertTrue(query_results)
            self.assertTrue(all(result == () for result in query_results))
            expected_impacts = initial_impacts + int(name == 'Heavy Assault')
            self.assertEqual(impact.counter, expected_impacts)
            self.assertEqual(impact.evaluate(impact.node.field('m_FA3ECC43'), watch=False), ())
            initial_impacts = expected_impacts
        else:
            run.step({})
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(45):
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertEqual(impact.counter, initial_impacts)
            self.assertEqual(own.total, 600)
            self.assertEqual(mine.health, 50)
            self.assertFalse(mine.triggered)
        self.assertEqual(npc_control.stop(match), 1)

        for _ in range(400):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(mine.armed)
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for _ in range(60):
            run.step({})
            if own.total < 600:
                break
        self.assertEqual(impact.counter, initial_impacts + 1)
        self.assertEqual(ability.instance.vars[375].value(), (expr.Entity(player.body),))
        self.assertEqual(own.total, 600 - damage)
        self.assertEqual(mine.health, 50)
        self.assertFalse(mine.triggered)

    def test_assassin_controller_and_pin_graph_reject_deployed_mine(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        run, player, bot, _, own = self.scenario(definition, distance=100)
        mine = mine_tests.MineTests().isolated(run, player)
        x, y, z = mine.position
        bot.position = (x, y, z + 2.4)
        relocate(run, player, (x + 0.8, y, z))
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(player), (x, y + 1, z + 2.4))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        component = bot.body_script.component

        # Inject a deployable ID where the manual command normally stores a player ID.
        bot.attack_target = mine.entity
        initial_health = own.total
        for _ in range(150):
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertIsNone(component.world.manual_target)
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(own.total, initial_health)
        self.assertTrue(mine.armed)
        self.assertEqual(mine.health, 50)
        self.assertFalse(mine.triggered)

        # Supply the same deployable to the actual pin graph, bypassing manual selection.
        manual_spawner = bot.spawner
        bot.spawner = {**manual_spawner, 'manual_spawn': False}
        assassin.bind(component.world, component, mine.entity)
        button = component.world.ai_weapon_button(0)
        component.edges([(button, True)])
        pin = next(i for i in component.instances.values() if i.graph.index == assassin.GRAPH)
        impact = pin.node_state(pin.graph.node(15))
        initial_impacts = impact.counter
        for _ in range(150):
            run.step({})
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(mine.health, 50)
            if impact.counter != initial_impacts:
                break
        self.assertGreater(impact.counter, initial_impacts)
        self.assertEqual(component.world.manual_target, mine.entity)
        requested = impact.evaluate(impact.node.field('m_FA3ECC43'), watch=False)
        self.assertEqual(expr.to_entity(requested), mine.entity)
        self.assertEqual(own.total, initial_health)
        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        bot.spawner = manual_spawner
        npc_control.stop(run.match)

        bx, by, bz = bot.position
        angles = aim(fight._eye(player), (bx, by + 1, bz))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(component.world.manual_target, player.body)
        self.assertEqual(mine.health, 50)


if __name__ == '__main__':
    unittest.main()
