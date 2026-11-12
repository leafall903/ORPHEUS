"""Archived Minefield lifetime and EMP checks on the sole Controls host."""
import math
import unittest
from dataclasses import replace
import test_delivered_ball_mine_factory as mine_fixtures
from test_delivered_wrecking_ball_fireball import relocate
from test_game_combat import Run, KANEZAKA_DM, aim
from ow174.game import ball, combat, sombra_control, world, stats
from ow174.game.collision import World
from ow174.game.content import heroes, SOLDIER
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class EmpCast(unittest.TestCase):
    def cast(self, run, sombra, held=None):
        held = held or {}
        run.step({**held, sombra: (-32768, 0, 32)})
        instances = sombra.body_script.component.instances.values()
        graph = next(i for i in instances if i.graph.index == sombra_control.EMP)
        for _ in range(65):
            run.step(held)
            if graph.state(17).counter:
                break
        self.assertEqual(graph.state(17).counter, 1)
        self.assertEqual(graph.vars[1648].value(), 15)
        self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)


class MineStatusTests(unittest.TestCase):
    setUpClass = classmethod(mine_fixtures.MineTests.setUpClass.__func__)
    scenario = mine_fixtures.MineTests.scenario
    deploy = mine_fixtures.MineTests.deploy
    isolated = mine_fixtures.MineTests.isolated
    target = mine_fixtures.MineTests.target
    flying = mine_fixtures.MineTests.flying

    def test_actual_emp_disables_armed_mine_and_natural_expiry_restores_trigger(self):
        run, player, sombra = self.scenario()
        distant = tuple(sombra.mover.position)
        run.match.switch_hero(sombra, heroes()[sombra_control.HERO])
        relocate(run, sombra, distant)
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 6))
        script = sombra.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        EmpCast().cast(run, sombra)
        component = mine.body_script.component
        child, = (i for i in component.instances.values() if i.graph.index == sombra_control.PULSE)
        self.assertEqual(child.source_entity(), sombra.body)
        for _ in range(5):
            run.step({})
        effect, = (i for i in component.instances.values() if i.graph.index == sombra_control.EFFECT)
        self.assertEqual(effect.source_entity(), sombra.body)
        self.assertEqual(effect.vars[215].value(), 10)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)

        relocate(run, sombra, (x, y, z + 0.5))
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, sombra))
        deadline = math.ceil((effect.start + 10000) / component.frame_ms)
        run.frame = deadline - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(mine.triggered)
        self.assertFalse(mine.armed)
        self.assertTrue(child.teardown)
        self.assertFalse(effect.teardown)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 200)

        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(effect.teardown)
        self.assertFalse(component.entity_var(8912).value())
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)
        self.assertGreater(sombra.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_overlapping_actual_emps_keep_mine_disabled_until_last_expiry(self):
        run = Run(KANEZAKA_DM, ('Ball', 'First Sombra', 'Second Sombra'))
        player, first, second = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, first.team, second.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        x, y, z = player.mover.position
        for caster in (first, second):
            run.match.switch_hero(caster, heroes()[sombra_control.HERO])
            relocate(run, caster, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, first, (x, y, z + 6))
        relocate(run, second, (x + 6, y, z))
        component = mine.body_script.component
        for caster in (first, second):
            caster.body_script.ult_seconds = 0
            caster.body_script.set_ult_charge(caster.body_script.component.entity_var(ULT_COST).value())
            EmpCast().cast(run, caster)
            for _ in range(5):
                run.step({})
            self.assertFalse(mine.armed)

        effects = {i.source_entity(): i for i in component.instances.values()
                   if i.graph.index == sombra_control.EFFECT}
        self.assertEqual(set(effects), {first.body, second.body})
        first_effect, second_effect = effects[first.body], effects[second.body]
        self.assertEqual(first_effect.vars[215].value(), 10)
        self.assertEqual(second_effect.vars[215].value(), 10)
        first_deadline, second_deadline = (
            math.ceil((effect.start + 10000) / component.frame_ms)
            for effect in (first_effect, second_effect)
        )
        self.assertLess(first_deadline, second_deadline)
        relocate(run, second, (x, y, z + 0.5))
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, second))
        run.frame = first_deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(first_effect.teardown)
        self.assertFalse(second_effect.teardown)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 200)

        run.frame = second_deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(second_effect.teardown)
        self.assertFalse(component.entity_var(8912).value())
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_enemy_rifle_destroys_emp_disabled_mine_before_expiry(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Sombra', 'Soldier'))
        player, sombra, soldier = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, sombra.team, soldier.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(sombra, heroes()[sombra_control.HERO])
        run.match.switch_hero(soldier, heroes()[SOLDIER])
        x, y, z = player.mover.position
        for enemy in (sombra, soldier):
            relocate(run, enemy, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)

        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 6))
        script = sombra.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        EmpCast().cast(run, sombra)
        for _ in range(5):
            run.step({})
        component = mine.body_script.component
        effect, = (i for i in component.instances.values() if i.graph.index == sombra_control.EFFECT)
        deadline = math.ceil((effect.start + 10000) / component.frame_ms)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)

        fight = run.match.combat
        relocate(run, soldier, (x - 3, y, z))
        angles = aim(fight._eye(soldier), (x, y + 0.23, z))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        for _ in range(20):
            run.step({soldier: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        for _ in range(90):
            run.step({})
        self.assertLess(run.frame, deadline)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertNotIn(mine.entity, fight.vitals)
        self.assertTrue(effect.teardown)
        self.assertFalse(component.instances)
        self.assertEqual(soldier.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

        relocate(run, soldier, (x, y, z + 0.5))
        vitals = fight.vitals_of(self.target(run, soldier))
        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(mine.triggered)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertFalse(component.instances)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for viewer in (player, sombra, soldier):
            self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_DESTROY
                                 for item in viewer.client.entities), 1)

    def test_actual_emp_during_flight_still_disables_mine_after_landing(self):
        run, player, sombra = self.scenario()
        run.match.switch_hero(sombra, heroes()[sombra_control.HERO])
        x, y, z = player.mover.position
        relocate(run, sombra, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        for owner in (player, sombra):
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        EmpCast().cast(run, sombra, {player: (0, 0, 32)})
        fight = run.match.combat
        self.assertEqual(len(fight.mines.entities), 15)
        mine = min(fight.mines.entities.values(), key=lambda item: item.entity)
        for other in tuple(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        component = mine.body_script.component
        self.assertFalse(mine.landed)
        self.assertTrue(any(i.graph.index == sombra_control.PULSE for i in component.instances.values()))
        previous = mine.position
        run.step({})
        self.assertNotEqual(mine.position, previous)
        self.assertTrue(component.entity_var(8912).value())
        for _ in range(220):
            run.step({})
            if mine.landed:
                break
        self.assertTrue(mine.landed)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 0.5))
        for _ in range(120):
            run.step({})
        self.assertFalse(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(fight.vitals_of(self.target(run, sombra)).total, 200)

    def test_actual_emp_leaves_allied_covered_and_out_of_range_mines_armed(self):
        for case in ('ally', 'wall', 'range'):
            with self.subTest(case=case):
                run, player, sombra = self.scenario()
                distant = tuple(sombra.mover.position)
                run.match.switch_hero(sombra, heroes()[sombra_control.HERO])
                relocate(run, sombra, distant)
                mine = self.isolated(run, player)
                x, y, z = mine.position
                relocate(run, sombra, (x, y, z + (17 if case == 'range' else 6)))
                if case == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sombra.team = player.team
                if case == 'wall':
                    a, b = (x - 3, y, z + 3), (x + 3, y, z + 3)
                    c, d = (x + 3, y + 4, z + 3), (x - 3, y + 4, z + 3)
                    run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
                script = sombra.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                EmpCast().cast(run, sombra)
                for _ in range(5):
                    run.step({})
                self.assertTrue(mine.armed)
                self.assertEqual(mine.health, 50)
                self.assertFalse(any(i.graph.index in (sombra_control.PULSE, sombra_control.EFFECT)
                                     for i in mine.body_script.component.instances.values()))

    def test_authored_lifetime_ends_with_native_fade_and_destruction_on_tick_gap(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        run.frame += 1600
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertFalse(mine.triggered)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        self.assertTrue(any(item.entity == mine.entity and item.op == world.OP_DESTROY
                            for item in player.client.entities))

    def test_creator_lifetime_zero_fraction_and_double_are_values_not_keys(self):
        for scalar in (0, 0.5, 2):
            with self.subTest(scalar=scalar):
                run, player, _ = self.scenario()
                player.body_script.component.set(13032, scalar)
                mines = self.deploy(run, player, ticks=20)
                self.assertEqual(len(mines), 15)
                for mine in mines:
                    duration = mine.body_script.primary().vars[6471].value()
                    self.assertGreaterEqual(duration, 18 * scalar)
                    self.assertLessEqual(duration, 20 * scalar)
                if scalar == 0:
                    run.frame += 300
                    run.step({})
                    self.assertFalse(run.match.combat.mines.entities)

    def test_creator_infinite_flag_overrides_lifetime_and_survives_normal_deadline(self):
        run, player, _ = self.scenario()
        player.body_script.component.set(13032, 0)
        player.body_script.component.set(13033, True)
        mines = self.deploy(run, player)
        self.assertEqual(len(mines), 15)
        self.assertTrue(all(m.body_script.primary().vars[6471].value() == 9999 for m in mines))
        run.frame += 1600
        run.step({})
        self.assertEqual(set(run.match.combat.mines.entities), {m.entity for m in mines})

    def test_armed_mine_reads_live_creator_knockback_zero_half_and_default_damage(self):
        for scalar in (0, 0.5, 1):
            with self.subTest(scalar=scalar):
                run, player, enemy = self.scenario()
                mine = self.isolated(run, player)
                # Change after deployment and arming: this is a creator member,
                # not a copied initializer or a numeric ruleset identifier.
                player.body_script.component.set(16349, scalar)
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 70)
                if scalar == 0:
                    self.assertEqual(enemy.mover.velocity, (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 5 * scalar - 0.4)
                    self.assertLessEqual(enemy.mover.velocity[1], 5 * scalar)
                    self.assertGreater(enemy.mover.velocity[2], 5 * scalar - 0.4)
                    self.assertLessEqual(enemy.mover.velocity[2], 5 * scalar)
    def test_native_source_query_rejects_missing_or_replaced_creator_script(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        cfg = primary.graph.node(33).field('m_condition')['m_value']['m_configVars'][0]
        self.assertEqual(cfg['$'], 'STU_2B2E2F6D')
        source_world = primary.component.world
        player.body_script.component.set(13032, 0.5)
        enemy.body_script.component.set(13032, 2)
        self.assertEqual(primary.evaluate(cfg), expr.Entity(mine.source))
        self.assertEqual(source_world.entity_var(mine.source, 13032), 0.5)

        original = player.body_script
        try:
            # Explicit namespace absence/replacement fixtures. Normal hero
            # retirement already destroys owned mines in the bounded bridge;
            # these isolate the native query's missing-selected-entity branch.
            for replacement in (None, enemy.body_script):
                with self.subTest(replacement=replacement is not None):
                    player.body_script = replacement
                    self.assertEqual(primary.evaluate(cfg), expr.Entity(0))
                    self.assertIsNone(source_world.entity_var(mine.source, 13032))
                    self.assertIsNone(source_world.entity_ruleset(mine.source, 16349))
                    self.assertFalse(source_world.script_component_present(mine.source))
        finally:
            player.body_script = original
        self.assertEqual(primary.evaluate(cfg), expr.Entity(mine.source))
        self.assertEqual(source_world.entity_var(mine.source, 13032), 0.5)
