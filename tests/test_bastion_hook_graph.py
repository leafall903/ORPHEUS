"""104319 Roadhog Hook graph inputs used by the Bastion control matrix."""

import math
import unittest
from dataclasses import replace

import test_bastion_combat as bastion
import test_mccree_flashbang as flashbang
from test_game_combat import aim

from ow174.game import combat, hook, stats, zarya_barriers
from ow174.game.content import heroes
from ow174.game.script import expr, graph


class BastionHookGraphTests(unittest.TestCase):
    def arena(self, distance=10):
        case = bastion.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, distance), enemy_hero=hook.HERO,
                     bystander_position=(12, 1, 12))
        return case

    @staticmethod
    def step(case, *, cast=False, buttons=0, miss=False):
        x, y, z = case.player.mover.position
        direction = (0, 0) if miss else aim(case.fight._eye(case.enemy), (x, y + 1, z))
        case.run.step({case.enemy: (*direction, 8 if cast else 0),
                       case.player: (*case.command_aim, buttons)})

    def cast(self, case, *, miss=False, buttons=0):
        self.step(case, cast=True, miss=miss, buttons=buttons)
        for _ in range(37):
            self.step(case, miss=miss, buttons=buttons)
            if case.fight.hooks.controlled(case.player.body):
                return True
        return False

    def test_actual_hook_damage_pull_input_lock_and_release(self):
        for form, expected in (("recon", 25), ("sentry", 19), ("tank", 19)):
            with self.subTest(form=form):
                case = self.arena()
                if form == "sentry":
                    case.configure()
                elif form == "tank":
                    case.tank()
                health = case.health(case.player)
                before = health.total
                self.assertTrue(self.cast(case))
                self.assertAlmostEqual(before - health.total, expected, places=5)
                hits = len(case.enemy.client.messages(combat.HIT))
                self.assertEqual(hits, 1)
                for _ in range(130):
                    if not case.fight.hooks.flights:
                        break
                    flight = case.fight.hooks.flights[case.enemy.body]
                    self.step(case, buttons=(1 | 2 | 8) if flight.arrived_at is None else 0)
                    if case.fight.hooks.controlled(case.player.body):
                        self.assertFalse(case.player.client.messages(combat.HIT))
                        self.assertAlmostEqual(before - health.total, expected, places=5)
                self.assertAlmostEqual(math.dist(case.player.mover.position, case.enemy.mover.position), 3,
                                       delta=0.15)
                self.assertFalse(case.fight.is_stunned(case.player))
                self.assertEqual(len(case.enemy.client.messages(combat.HIT)), hits)
                case.step(0, 2)
                roadhog = case.health(case.enemy)
                before = roadhog.total
                target = case.enemy.mover.position
                case.command_aim = aim(case.fight._eye(case.player), (target[0], target[1] + 1, target[2]))
                case.step(1, 30)
                self.assertLess(roadhog.total, before)

    def test_hook_cancels_active_repair_and_fresh_input_resumes_healing(self):
        case = self.arena()
        health = case.health(case.player)
        health.damage(160)
        case.fight._sync_body_health(case.player, health)
        case.step(2, 32)
        script = case.player.body_script
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x0153)
        self.assertTrue(repair.states[0].active)
        self.assertTrue(self.cast(case, buttons=2))
        self.assertFalse(repair.states[0].active)
        healed = case.player.stats.value(stats.HEALING)
        after_hit = health.total
        for _ in range(130):
            flight = case.fight.hooks.flights.get(case.enemy.body)
            if flight is None:
                break
            self.step(case, buttons=2 if flight.arrived_at is None else 0)
        self.assertFalse(case.fight.is_stunned(case.player))
        self.assertEqual(case.player.stats.value(stats.HEALING), healed)
        self.assertEqual(health.total, after_hit)
        case.step(2, 32)
        self.assertGreater(health.total, after_hit)

    def test_cooldown_rejects_new_cast_then_fresh_input_hits_again(self):
        case = self.arena()
        self.assertTrue(self.cast(case))
        case.step(0, 60)
        cast = next(i for i in case.enemy.body_script.component.instances.values()
                    if i.graph.index == 0x01C8)
        ability = next(state for state in cast.states.values() if state.node.pos == 11)
        launch = next(state for state in cast.states.values() if state.node.pos == 12)
        self.assertEqual(cast.find_var(0, 61).base, 8.0)
        self.assertGreater(ability.remaining(), 0)
        hits = len(case.enemy.client.messages(combat.HIT))
        health = case.health(case.player).total
        self.assertFalse(self.cast(case))
        self.assertEqual(launch.counter, 1)
        self.assertEqual(case.health(case.player).total, health)
        for _ in range(560):
            if ability.remaining() == 0:
                break
            self.step(case)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(launch.counter, 1)
        self.assertTrue(self.cast(case))
        self.assertEqual(launch.counter, 2)
        self.assertLess(case.health(case.player).total, health)
        self.assertEqual(len(case.enemy.client.messages(combat.HIT)), hits + 1)

    def test_caster_body_replacement_releases_a_caught_bastion(self):
        case = self.arena()
        self.assertTrue(self.cast(case))
        old_body = case.enemy.body
        self.assertTrue(case.run.match.switch_hero(case.enemy, case.bystander.hero))
        self.assertNotEqual(case.enemy.body, old_body)
        self.assertFalse(case.fight.is_stunned(case.player))
        self.assertFalse(case.fight.hooks.flights)

    def flashbang_arena(self, *, target_caster):
        case = self.arena()
        match = case.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        case.player.team, case.enemy.team = 0, 1
        mccree = case.bystander
        self.assertTrue(match.switch_hero(mccree, heroes()[0x02E0000000000042]))
        mccree.team = 0 if target_caster else 1
        mccree.mover.position[:] = (2.5, 1, 9 if target_caster else 7)
        mccree.mover.body = None
        case.step(0, 15)
        return case, mccree

    def test_flashbang_on_roadhog_releases_caught_bastion(self):
        case, mccree = self.flashbang_arena(target_caster=True)
        self.assertTrue(self.cast(case))
        health = case.health(case.player).total
        flashbang.FlashbangTests().cast(case.run, mccree, case.enemy)
        self.assertIn(case.enemy.body, case.fight.stuns)
        self.assertFalse(case.fight.hooks.flights)
        self.assertFalse(case.fight.is_stunned(case.player))
        self.assertEqual(case.health(case.player).total, health)
        case.step(0, 2)
        before = case.health(case.enemy).total
        x, y, z = case.enemy.mover.position
        case.command_aim = aim(case.fight._eye(case.player), (x, y + 1, z))
        case.step(1, 15)
        self.assertLess(case.health(case.enemy).total, before)

    def test_flashbang_on_caught_bastion_outlasts_hook_recovery(self):
        case, mccree = self.flashbang_arena(target_caster=False)
        self.assertTrue(self.cast(case))
        flight = case.fight.hooks.flights[case.enemy.body]
        for _ in range(35):
            if flight.arrived_at is not None:
                break
            self.step(case)
        self.assertIsNotNone(flight.arrived_at)
        for offset in range(40):
            flashbang.FlashbangTests().step(case.run, mccree, case.player, cast=16 if offset == 0 else 0)
            if case.player.body in case.fight.stuns:
                break
        self.assertIn(case.player.body, case.fight.stuns)
        deadline = case.fight.stuns[case.player.body]
        self.assertTrue(case.fight.hooks.controlled(case.player.body))
        self.assertEqual(case.health(case.player).total, 255)
        for _ in range(60):
            self.step(case, buttons=1)
            if not case.fight.hooks.controlled(case.player.body):
                break
        self.assertFalse(case.fight.hooks.controlled(case.player.body))
        self.assertLess(case.fight.tick, deadline)
        self.assertTrue(case.fight.is_stunned(case.player))
        self.assertFalse(case.player.client.messages(combat.HIT))
        while case.run.frame < deadline - 1:
            self.step(case, buttons=1)
        self.assertFalse(case.player.client.messages(combat.HIT))
        self.step(case)
        self.assertFalse(case.fight.is_stunned(case.player))

    def test_actual_projected_barrier_absorbs_hook_and_rejects_pull(self):
        case = self.arena()
        match = case.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        case.player.team = case.bystander.team = 0
        case.enemy.team = 1
        zarya = case.bystander
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        zarya.mover.position[:] = (4, 1, 0)
        zarya.mover.body = None
        case.step(0, 15)
        case.run.step({zarya: (-16384, 0, 0)})
        case.run.step({zarya: (-16384, 0, 16)})
        projection = next(i for i in zarya.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        self.assertTrue(projection.state(10).active)
        self.assertEqual(projection.vars[536].value(), expr.Entity(case.player.body))
        feet = tuple(case.player.mover.position)
        self.assertFalse(self.cast(case))
        self.assertEqual(case.health(case.player).total, 300)
        self.assertEqual(tuple(case.player.mover.position), feet)
        self.assertEqual(case.fight.zarya_barriers.projected[zarya.body].remaining, 170)
        self.assertFalse(case.enemy.client.messages(combat.HIT))
        self.assertFalse(case.fight.hooks.flights)

    def test_wall_team_range_and_aim_reject_damage_and_pull(self):
        for reason in ("wall", "team", "range", "aim"):
            with self.subTest(reason=reason):
                case = self.arena(23 if reason == "range" else 10)
                if reason == "wall":
                    case.wall(5)
                if reason == "team":
                    match = case.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team
                feet = tuple(case.player.mover.position)
                self.assertFalse(self.cast(case, miss=reason == "aim"))
                self.assertEqual(case.health(case.player).total, 300)
                self.assertEqual(tuple(case.player.mover.position), feet)
                self.assertFalse(case.enemy.client.messages(combat.HIT))
                self.assertFalse(case.fight.hooks.flights)

    def test_real_roadhog_input_exposes_launch_and_pull_parameters(self):
        case = bastion.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 10), enemy_hero=hook.HERO)
        roadhog = case.enemy
        direction = aim(case.fight._eye(roadhog), (0, 2, 0))

        for offset in range(11):
            case.run.step({roadhog: (*direction, 8 if offset == 0 else 0)})
            cast = next(instance for instance in roadhog.body_script.component.instances.values()
                        if instance.graph.index == 0x01C8)
            launch = next((state for state in cast.states.values() if state.node.pos == 12), None)
            self.assertEqual(launch is not None and launch.active, offset == 10)

        self.assertEqual(launch.counter, 1)
        hit = combat.hit_def(launch, {"m_modifyHealth": cast.graph._by_pos[19].field("m_modifyHealth")})
        self.assertEqual(hit.amount, 30)
        self.assertEqual(expr.to_float(cast.find_var(0, 1648).value()), 20)
        speed = launch.evaluate(cast.graph._by_pos[136].field("m_9B0385B4"), watch=False)
        radius = launch.evaluate(cast.graph._by_pos[17].field("m_radius"), watch=False)
        self.assertEqual((expr.to_float(speed), expr.to_float(radius)), (60, 0.5))

        pull = graph.graph(0x01C9)
        movement = pull._by_pos[0]
        speed = movement.field("m_speed")["m_expression"]["m_D99EF254"][0]
        offset = movement.field("m_865F3D01")["m_configVars"][2]
        separation = offset["m_expression"]["m_D99EF254"][0]
        gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
        self.assertEqual((speed, separation, gap), (40, 3, 1))
        self.assertEqual(pull._by_pos[5].field("m_timeout")["m_value"], 2)
        self.assertAlmostEqual(pull._by_pos[7].field("m_timeout")["m_value"], 0.3)


if __name__ == "__main__":
    unittest.main()
