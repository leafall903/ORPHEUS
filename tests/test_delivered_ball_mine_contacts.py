"""Original Minefield contact/filter checks on the composed actor host."""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch
from test_delivered_ball_mine_factory import MineTests
from test_delivered_wrecking_ball_fireball import relocate, prepare_swing
from test_delivered_wrecking_ball_grapple import hold
from test_game_combat import Run, KANEZAKA_DM, aim
from ow174.game import ball, ball_mines, barriers, combat, stats, world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import entity_queries, expr, graph, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

REAPER = 0x02E0000000000002

def phase_instance(player):
    index, _ = combat.PHASE_STATES[player.hero.guid]
    return next(i for i in player.body_script.component.instances.values() if i.graph.index == index)


class MineContactTests(unittest.TestCase):
    setUpClass = classmethod(MineTests.setUpClass.__func__)
    scenario = MineTests.scenario
    deploy = MineTests.deploy
    isolated = MineTests.isolated
    target = MineTests.target
    flying = MineTests.flying

    def test_enemy_ball_roll_triggers_armed_mine_and_takes_damage(self):
        run, player, enemy = self.scenario()
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, player)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz + 4))
        fight = run.match.combat
        health = fight.vitals_of(self.target(run, enemy))
        initial = health.total
        run.step({enemy: (-32768, 0, 8)})
        self.assertTrue(ball.rolling(enemy))
        self.assertFalse(mine.triggered)
        for _ in range(75):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            enemy.take_commands([Command(run.frame, yaw=-32768, forward=127, extra=0)])
            if mine.triggered:
                break
        self.assertTrue(ball.rolling(enemy))
        self.assertTrue(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(health.total, initial - 125)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_enemy_fireball_cannot_destroy_an_armed_mine_before_it_triggers(self):
        run, owner, enemy = self.scenario()
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = owner.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, owner)
        self.assertTrue(mine.armed)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz - 6))
        prepare_swing(run, enemy)
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, enemy))
        self.assertEqual(vitals.total, 600)
        script = enemy.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        instance = ball.instance(enemy, 0x1203)
        for _ in range(100):
            search = instance.states.get(35)
            fireball = search is not None and search.active
            hold(run, enemy, 1)
            if mine.triggered:
                break
            self.assertEqual(mine.health, 50)
        self.assertTrue(fireball)
        self.assertTrue(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 475)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_mine_damage_uses_the_second_search_output(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        radius = primary.graph.node(50).field('m_radius')
        evaluate = primary.evaluate

        def narrow_damage_search(config, *args, **kwargs):
            return 0.0 if config is radius else evaluate(config, *args, **kwargs)

        x, y, z = mine.position
        relocate(run, enemy, (x + 1, y, z))
        with patch.object(primary, 'evaluate', side_effect=narrow_damage_search):
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(primary.vars[1193].value(), 1)
        self.assertEqual(primary.vars[375].value(), ())
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 200)

    def test_one_mine_explosion_hits_each_eligible_player_once(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Left', 'Right', 'Ally', 'Outside'))
        player, left, right, ally, outside = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        ally.team = player.team
        x, y, z = player.mover.position
        for actor in (left, right, ally, outside):
            if actor is not ally:
                actor.team = 1 - player.team
            relocate(run, actor, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        for actor, dx, dz in ((left, -1, 0), (right, 1, 0), (ally, 0, 1), (outside, 0, 4)):
            relocate(run, actor, (x + dx, y, z + dz))
        fight = run.match.combat
        vitals = [fight.vitals_of(self.target(run, actor)) for actor in (left, right, ally, outside)]
        self.assertEqual([v.total for v in vitals], [200, 200, 200, 200])
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual([v.total for v in vitals], [70, 70, 200, 200])
        self.assertLess(left.mover.velocity[0], -4)
        self.assertGreater(right.mover.velocity[0], 4)
        self.assertGreater(left.mover.velocity[1], 4)
        self.assertGreater(right.mover.velocity[1], 4)
        for _ in range(30):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual([v.total for v in vitals], [70, 70, 200, 200])
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_overlapping_enemy_mines_trigger_independently(self):
        run, player, enemy = self.scenario()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[ball.HERO]))
        point = tuple(player.mover.position)
        relocate(run, enemy, point)
        for _ in range(40):
            run.step({})
        for owner in (player, enemy):
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32), enemy: (0, 0, 32)})
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        mines = list(fight.mines.entities.values())
        own_mine, enemy_mine = (
            min((mine for mine in mines if mine.owner is owner), key=lambda mine: mine.entity)
            for owner in (player, enemy)
        )
        for mine in mines:
            if mine is not own_mine and mine is not enemy_mine:
                fight.mines.remove(mine)
        x, y, z = point
        relocate(run, player, (x - 20, y, z))
        relocate(run, enemy, (x + 20, y, z))
        for _ in range(200):
            run.step({})
        self.assertTrue(own_mine.armed and enemy_mine.armed)
        self.assertLess(math.dist(own_mine.position, enemy_mine.position), 0.1)
        self.assertEqual([own_mine.health, enemy_mine.health], [50, 50])
        self.assertFalse(own_mine.triggered or enemy_mine.triggered)

        x, y, z = own_mine.position
        relocate(run, enemy, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(own_mine.triggered)
        self.assertFalse(enemy_mine.triggered)
        self.assertEqual(enemy_mine.health, 50)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 475)
        for _ in range(30):
            run.step({})
        self.assertNotIn(own_mine.entity, fight.mines.entities)
        self.assertIs(fight.mines.entities.get(enemy_mine.entity), enemy_mine)
        self.assertTrue(enemy_mine.armed)
        self.assertEqual(enemy_mine.health, 50)

        x, y, z = enemy_mine.position
        relocate(run, player, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(enemy_mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, player)).total, 475)
        for owner in (player, enemy):
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_authored_mine_filter_short_circuits_component_key_and_routes_candidate_source(self):
        run, owner, enemy = self.scenario()
        mine = self.isolated(run, owner)
        primary = mine.body_script.primary()
        condition = primary.graph.node(52).field('m_filter')['m_condition']
        key_query, fallback_query = condition['m_configVars']
        self.assertEqual(key_query['m_88B0050A'], '0x0D600000000002CB')
        self.assertEqual(fallback_query['$'], 'STU_A58C1E63')
        owner_component = owner.body_script.component
        supplied_source = (expr.Entity(enemy.body),)
        producer = owner_component.play_loaded_graph(
            graph.graph(0x0636), source=owner.body, params=[(0x0A40000000000392, supplied_source)],
        )
        # Isolate the authored start binding from the unresolved 0636 initializer and effects.
        assignment, = (event for event in producer.queue if event.kind == runtime.ASSIGN)
        self.assertEqual(assignment.assignment, (expr.ENTITY, 8117, supplied_source))
        producer.queue.clear()
        producer.post(assignment)
        owner_component.run_now()
        self.assertEqual(owner_component.entity_var(8117).value(), supplied_source)
        key = 0x2CB
        present = entity_queries.Component76Keys(primary=frozenset({key}))
        fallback_key = entity_queries.Component76Keys(fallback=frozenset({key}))
        suppressed = entity_queries.Component76Keys(
            primary=frozenset({key}), fallback=frozenset({key}), suppressed=frozenset({key}),
        )
        cases = (
            (None, False, True, False),
            (present, False, False, True),
            (present, True, True, True),
            (fallback_key, False, False, True),
            (suppressed, False, True, False),
        )
        component = primary.component
        mine_world = component.world
        native = mine_world.native

        for keys, fallback, expected, called in cases:
            with self.subTest(keys=keys, fallback=fallback):
                def evaluate_native(cfg, instance, *, result=fallback):
                    if cfg is fallback_query:
                        self.assertIs(instance, primary)
                        self.assertEqual(instance.evaluate(cfg['m_target']), expr.Entity(mine.entity))
                        source = instance.evaluate(cfg['m_source'])
                        self.assertEqual(source, supplied_source)
                        self.assertEqual(expr.to_entity(source), enemy.body)
                        return result
                    return native(cfg, instance)

                with (patch.object(mine_world, 'component76_keys', return_value=keys) as lookup,
                      patch.object(mine_world, 'native', side_effect=evaluate_native) as query,
                      component.parameter_scope({8: expr.Entity(owner.body)})):
                    self.assertIs(primary.evaluate(condition), expected)
                lookup.assert_called_once_with(owner.body)
                self.assertEqual(query.call_count, int(called))

    def test_overlap_before_native_preparation_timer_does_not_damage(self):
        run, player, enemy = self.scenario()
        mines = self.deploy(run, player, ticks=20)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        x, ground, z = player.mover.position
        relocate(run, player, (x + 20, ground, z))
        relocate(run, enemy, (x, ground, z + 0.5))
        while not mine.landed:
            run.step({})
        landed = run.frame
        for _ in range(90):
            run.step({})
            self.assertFalse(mine.triggered)
            self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        while not mine.triggered and run.frame < landed + 100:
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertGreaterEqual(run.frame - landed, math.ceil(1.5 / 0.016))
    def test_wraith_cannot_trigger_armed_mine_until_phase_expires(self):
        run, player, reaper = self.scenario()
        run.match.switch_hero(reaper, heroes()[REAPER])
        x, y, z = player.mover.position
        relocate(run, reaper, (x + 100, y, z))
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        run.step({reaper: (0, 0, 8)})
        phase = phase_instance(reaper).states[5]
        self.assertTrue(phase.active)
        x, y, z = mine.position
        relocate(run, reaper, (x, y, z + 0.5))
        fight = run.match.combat
        for _ in range(40):
            run.step({})
        self.assertTrue(phase.active)
        self.assertFalse(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, reaper)).total, 250)
        self.assertFalse(reaper.mover.airborne)
        for _ in range(160):
            run.step({})
            if mine.triggered:
                break
        self.assertFalse(phase.active)
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, reaper)).total, 120)
        self.assertGreater(reaper.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_live_cryo_stack_excludes_the_candidate_from_damage_search(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, player, mei = self.scenario()
                run.match.switch_hero(mei, heroes()[barriers.MEI])
                x, y, z = player.mover.position
                relocate(run, mei, (x + 100, y, z))
                mine = self.isolated(run, player)
                run.step({mei: (-32768, 0, 8)})
                stack = mei.body_script.component.entity_var(9588, create=False)
                self.assertTrue(expr.truthy(stack.value()))
                if cancel:
                    for _ in range(20):
                        run.step({})
                    run.step({mei: (-32768, 0, 2)})
                    self.assertFalse(expr.truthy(stack.value()))

                primary = mine.body_script.primary()
                mine_world = primary.component.world
                native = mine_world.native
                relation_calls = []
                relations = {
                    id(primary.graph.node(pos).field('m_filter')['m_condition']['m_configVars'][-1]): pos
                    for pos in (52, 50)
                }

                def evaluate_native(
                    config, instance, *, relations=relations, native=native, calls=relation_calls,
                ):
                    pos = relations.get(id(config))
                    if pos is None:
                        return native(config, instance)
                    calls.append(pos)
                    return True

                x, y, z = mine.position
                relocate(run, mei, (x, y, z + 0.5))
                keys = entity_queries.Component76Keys(primary=frozenset({0x2CB}))
                with (patch.object(mine_world, 'component76_keys', return_value=keys),
                      patch.object(mine_world, 'native', side_effect=evaluate_native)):
                    run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(relation_calls, [52, 50] if cancel else [52])
                self.assertEqual(primary.vars[375].value(), (expr.Entity(mei.body),) if cancel else ())
