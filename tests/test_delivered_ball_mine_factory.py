"""Original 01CA Minefield factory and flight checks against composed graphs."""
import test_server_snapshot as snapshot_tests
from test_game_combat import FakeClient, aim
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game import stats
from ow174.game import server_snapshot
import math
import unittest
import test_delivered_wrecking_ball_combat as ball_tests
from test_delivered_wrecking_ball_fireball import relocate
from ow174.game.collision import World, StaticCollisionProvider
from ow174.game import ball, ball_mines, world
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class MineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (graph.entity_body(ball_mines.BODY) is None or graph.graph(0x151A) is None
                or graph.graph(0x261E) is None):
            raise ValueError("select real composed 2578/151A/261E input")

    def scenario(self):
        run, player, enemy, _, _ = ball_tests.BallCombatTests().scenario()
        x, y, z = enemy.mover.position
        relocate(run, enemy, (x + 100, y, z))
        player.body_script.ult_seconds = 0
        return run, player, enemy

    def deploy(self, run, player, ticks=200):
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(ticks):
            run.step({})
        return list(run.match.combat.mines.entities.values())

    def flying(self):
        run, player, enemy = self.scenario()
        ball.instance(player, 0x1517).write((expr.INSTANCE, 7389), 0)
        mines = self.deploy(run, player, ticks=20)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        self.assertFalse(mine.landed)
        return run, player, enemy, mine

    def test_actual_q_expands_three_reports_into_fifteen_unique_owned_creates(self):
        run, player, _ = self.scenario()
        mines = self.deploy(run, player, ticks=20)
        self.assertEqual(len(mines), 15)
        self.assertEqual(len({m.entity for m in mines}), 15)
        self.assertTrue(all(m.owner is player and m.source == player.body and m.health == 50 for m in mines))
        self.assertTrue(all(m.flight_lifetime == 999 and m.reach == 500 for m in mines))
        self.assertTrue(all(m.born_tick <= m.last_tick
                            and m.born_tick == m.body_script.primary().initialized_frame for m in mines))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual({key[2] for key in run.match.combat.mines.seen}, {3, 8, 18})
        for viewer in run.players:
            creates = [item for item in viewer.client.entities
                       if item.op == world.OP_CREATE and item.entity in {m.entity for m in mines}]
            self.assertEqual(len(creates), 15)
            for item in creates:
                self.assertTrue(world.frame(run.frame, 0, entities=[item]))
        node = ball.instance(player, 0x1517).states[18]
        ids = set(run.match.combat.mines.entities)
        self.assertTrue(run.match.combat.mines.volley(player, node, 0, run.frame))
        self.assertEqual(set(run.match.combat.mines.entities), ids)

    def test_native_zero_negative_and_zero_displacement_gravity_launches(self):
        for gravity in (0, -5, 625):
            with self.subTest(gravity=gravity):
                run, player, _ = self.scenario()
                ball.instance(player, 0x1517).write((expr.INSTANCE, 7389), gravity)
                mines = self.deploy(run, player, ticks=20)
                self.assertEqual(len(mines), 15)
                first = min(mines, key=lambda m: m.entity)
                if gravity == 0:
                    self.assertEqual(first.velocity, (0, 10, 0))
                    self.assertFalse(first.landed)
                elif gravity < 0:
                    self.assertGreater(first.velocity[1], 10)
                    self.assertFalse(first.landed)
                else:
                    # 625*float32(.016) rounds to10. The central mine has a
                    # zero-displacement first step, then falls on later ticks.
                    # That step must not normalize a zero collision direction.
                    self.assertTrue(first.landed)
                self.assertTrue(all(math.isfinite(p) for m in mines for p in m.position))

    def test_actual_zero_gravity_cast_clips_at_authored_500_range_without_arming(self):
        run, player, enemy, mine = self.flying()
        run.frame += 4000
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(mine.travelled, 500)
        self.assertEqual(mine.velocity, (0, 0, 0))
        self.assertFalse(mine.landed or mine.triggered)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_DESTROY
                             for item in player.client.entities), 1)

    def test_budget_clips_sweep_before_wall_outside_remaining_range(self):
        run, _, _, mine = self.flying()
        # Explicit physical-state fixture after an actual cast. Unclipped .16m
        # movement would hit the wall at .12m, beyond the .1m remaining budget.
        mine.position, mine.velocity = (0, 10, 0), (0, 0, 10)
        mine.reach, mine.travelled = expr.f32(.1), 0
        collision = World([
            (-5, 0, .12, 5, 0, .12, 5, 20, .12),
            (-5, 0, .12, 5, 20, .12, -5, 20, .12),
        ])
        run.match.collision_world = collision
        run.match.collision_provider = StaticCollisionProvider(collision)
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertFalse(mine.landed or mine.triggered)
        self.assertAlmostEqual(mine.position[2], .1, places=6)
        # Captured original D72C80 for this .1/.16 boundary rounds one ULP
        # below the configured reach; it does not force the counter to reach.
        self.assertEqual(mine.travelled, .09999999403953552)

    def test_flight_time_uses_world_birth_not_future_command_report_and_strict_boundary(self):
        run, _, _, mine = self.flying()
        # Shortened lifetime is an explicit engine-state fixture; authored Q
        # supplies999, while this isolates the native terminal time branch.
        mine.born_tick = run.frame
        mine.reported_frame += 1000000
        mine.flight_lifetime = expr.f32(2 * ball_mines.TICK)
        mine.position, mine.velocity = (0, 10, 0), (0, 0, 0)
        mine.travelled = 0
        for _ in range(2):
            run.step({})
            self.assertIn(mine.entity, run.match.combat.mines.entities)
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(mine.travelled, 0)
        self.assertFalse(mine.landed or mine.triggered)

    def test_landed_bridge_uses_graph_lifetime_after_flight_stops(self):
        run, player, _ = self.scenario()
        mine = self.isolated(run, player)
        mine.flight_lifetime = ball_mines.FLIGHT_EPSILON
        run.step({})
        self.assertIn(mine.entity, run.match.combat.mines.entities)
        self.assertTrue(mine.landed)
        self.assertTrue(mine.body_script.primary().states[28].active)

    def test_second_actual_cast_replaces_old_pet_limit_and_body_retirement_cleans_up(self):
        run, player, _ = self.scenario()
        old = {m.entity for m in self.deploy(run, player)}
        current = {m.entity for m in self.deploy(run, player)}
        self.assertEqual(len(current), 15)
        self.assertFalse(old & current)
        run.match.switch_hero(player, player.hero)
        self.assertFalse(run.match.combat.mines.entities)
        self.assertFalse(run.match.combat.mines.seen)
    def isolated(self, run, player):
        mines = self.deploy(run, player)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        x, y, z = mine.position
        relocate(run, player, (x + 20, y, z))
        return mine

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)
    def test_real_client_tracks_mine_creates_and_full_server_snapshot_contains_them(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        self.assertTrue(session.apply('switch_hero', {'account': player.account_lo, 'hero': ball.HERO}))
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        script = player.body_script
        peers[0].commands(script.frame + 1, 40, 0, now=101.1)
        # This is a direct snapshot/transport test, not a sealed journal replay:
        # charge is supplied explicitly, outside the recording root interface.
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        peers[0].commands(script.frame + 1, 30, 32, now=101.1)
        mines = player.match.combat.mines.entities
        self.assertEqual(len(mines), 15)
        self.assertTrue(set(mines) <= set(player.client._bot_scripts))
        self.assertTrue(all(m.last_tick == player.match.tick for m in mines.values()))
        self.assertTrue(any(m.reported_frame > m.last_tick for m in mines.values()))
        session.tick(105.0, missed=249)
        peers[0].drain()
        peers[0].send(now=105.0)
        self.assertTrue(all(m.landed for m in mines.values()))
        self.assertTrue(any(m.body_script.primary().states.get(28)
                            and m.body_script.primary().states[28].active for m in mines.values()))
        before = server_snapshot.snapshot_digest(case.capture(session))
        mine = next(iter(mines.values()))
        mine.health = 49
        mine.body_script.set_health(49, 50)
        self.assertNotEqual(before, server_snapshot.snapshot_digest(case.capture(session)))

    def test_replacement_client_receives_existing_mines_once(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        enemy.client = FakeClient(enemy)
        run.step({})
        creates = [item for item in enemy.client.entities
                   if item.entity == mine.entity and item.op == world.OP_CREATE]
        self.assertEqual(len(creates), 1)
        run.step({})
        self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_CREATE
                             for item in enemy.client.entities), 1)
    def test_arming_after_contact_then_one_actual_130_hit_and_no_ultimate_charge(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        self.assertTrue(mine.landed)
        self.assertTrue(primary.states[28].active)
        self.assertEqual(primary.state(27).counter, 0)
        self.assertEqual(primary.state(5).counter, 0)
        self.assertTrue(18 <= primary.vars[6471].value() <= 20)
        point = mine.position
        relocate(run, enemy, (point[0], point[1], point[2] + 0.5))
        run.step({})
        fight = run.match.combat
        self.assertTrue(mine.triggered)
        self.assertFalse(primary.state(28).active)
        self.assertEqual(primary.state(27).counter, 1)
        self.assertEqual(primary.state(5).counter, 1)
        self.assertEqual(primary.vars[1193].value(), 1)
        self.assertEqual(primary.vars[375].value(), (expr.Entity(enemy.body),))
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertGreater(enemy.mover.velocity[2], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(30):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)

    def test_enemy_rifle_destroys_fifty_hp_without_hero_charge_or_elimination(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        target = (x, y + 0.23, z)
        angles = aim(run.match.combat._eye(enemy), target)
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        self.assertFalse(mine.triggered)
        for _ in range(20):
            run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        self.assertFalse(mine.triggered)
        for _ in range(90):
            run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(enemy.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 0)

    def test_owner_and_allied_player_cannot_trigger_or_damage_mines(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        point = mine.position
        relocate(run, player, (point[0], point[1], point[2] + 0.5))
        for _ in range(10):
            angles = aim(run.match.combat._eye(player), (point[0], point[1] + 0.23, point[2]))
            run.step({player: (*angles, 1)})
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, 'free_for_all': False},
        )
        enemy.team = player.team
        relocate(run, enemy, (point[0], point[1], point[2] - 0.5))
        for _ in range(10):
            angles = aim(run.match.combat._eye(enemy), (point[0], point[1] + 0.23, point[2]))
            run.step({enemy: (*angles, 1)})
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)

    def test_static_wall_and_outside_radius_block_trigger(self):
        for wall, distance in ((False, 3), (True, 1.2), (False, 1.2)):
            with self.subTest(wall=wall, distance=distance):
                run, player, enemy = self.scenario()
                mine = self.isolated(run, player)
                x, y, z = mine.position
                if wall:
                    old = run.match.collision_world
                    triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
                    front = z + 0.5
                    triangles += [
                        (x - 5, y, front, x + 5, y, front, x + 5, y + 5, front),
                        (x - 5, y, front, x + 5, y + 5, front, x - 5, y + 5, front),
                    ]
                    collision = World(triangles)
                    run.match.collision_world = collision
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    player.mover.use(world=collision)
                relocate(run, enemy, (x, y, z + distance))
                run.step({})
                blocked = wall or distance == 3
                self.assertEqual(mine.triggered, not blocked)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total,
                                 200 if blocked else 70)

    def test_ball_cannon_cannot_crit_mine_or_gain_charge_from_destroying_it(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(0)
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 45)
        for _ in range(40):
            run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(enemy.stats.value(stats.ELIMINATIONS), 0)

    def test_healing_does_not_repair_mine_or_count_it_for_adaptive_shield(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        run.match.combat.heal_area(player, mine.position, 5, 100)
        self.assertEqual(mine.health, 30)
        # The enemy Ball is in shield range of this deployable, but its creator
        # is far away; a mine must not count as an extra enemy hero/NPC.
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        relocate(run, enemy, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        run.step({enemy: (0, 0, 16)})
        vitals = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(vitals.temporary_shields, 100)

    def test_rewound_shot_before_mine_creation_cannot_hit_later_entity(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        enemy.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1, extra=5000)])
        self.assertEqual(mine.health, 50)
        for _ in range(8):
            run.step({})
        for _ in range(5):
            run.step({enemy: (*angles, 1)})
            if mine.health < 50:
                break
        self.assertEqual(mine.health, 30)
