"""Winston barrier creation and shell collision through actual Match input."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr


class JunkratWorldBarrierTests(unittest.TestCase):
    def test_actual_deployment_blocks_outside_grenade_and_allows_inside_hit(self):
        for distance, shield_damage, body_damage in ((8, 120, 0), (2, 0, 120)):
            with self.subTest(distance=distance):
                run = Run(KANEZAKA_DM, ("Junkrat", "Winston"))
                shooter, winston = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                x, y, z = shooter.mover.position
                winston.spawn = ((x, y, z + distance), winston.spawn[1])
                run.match.switch_hero(shooter, find_hero("Junkrat"))
                run.match.switch_hero(winston, find_hero("Winston"))
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                run.step({winston: (0, 0, 16)})
                for _ in range(12):
                    run.step({})
                actor, = fight.barriers.world.actors.values()
                self.assertEqual(actor.health, 700)
                cast = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFA)
                self.assertEqual(cast.vars[38].value(), expr.Entity(actor.entity))
                self.assertNotIn(38, dict(winston.body_script.replicated_vars(cast)))
                hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
                self.assertTrue(hosted.states[11].active)
                self.assertEqual(expr.to_float(hosted.vars[567].value()), 9)
                target = next(t for t in fight._targets() if t.owner is winston)
                before = fight.vitals_of(target).total
                run.step({shooter: (0, 0, FIRE)})
                for _ in range(35):
                    run.step({})
                self.assertAlmostEqual(actor.health, 700 - shield_damage)
                expected = body_damage - 10 if body_damage else 0  # two armoured damage events
                self.assertAlmostEqual(fight.vitals_of(target).total, before - expected)

                if distance == 8:
                    for remaining in (460, 340, 220, 100, 0):
                        for _ in range(115 if remaining == 0 else 16):
                            run.step({})
                        run.step({shooter: (0, 0, FIRE)})
                        for _ in range(35):
                            run.step({})
                        self.assertAlmostEqual(actor.health, remaining)
                        self.assertEqual(fight.vitals_of(target).total, before)
                    self.assertFalse(fight.barriers.world.actors)
                else:
                    while run.frame - actor.born_tick < 550:
                        run.step({})
                    self.assertIn(actor.entity, fight.barriers.world.actors)
                    while run.frame - actor.born_tick < 580:
                        run.step({})
                    self.assertFalse(fight.barriers.world.actors)
                    run.step({winston: (0, 0, 16)})
                    for _ in range(12):
                        run.step({})
                    self.assertFalse(fight.barriers.world.actors)
                    while run.frame - actor.born_tick < 900:
                        run.step({})
                    run.step({winston: (0, 0, 16)})
                    for _ in range(12):
                        run.step({})
                    next_actor, = fight.barriers.world.actors.values()
                    self.assertNotEqual(next_actor.entity, actor.entity)
                    self.assertEqual(next_actor.health, 700)
                    run.match.switch_hero(winston, find_hero("Zarya"))
                    self.assertFalse(fight.barriers.world.actors)

    def test_soldier_hits_the_empty_winston_shell_only_with_clear_los(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run = Run(KANEZAKA_DM, ("Soldier", "Winston"))
                soldier, winston = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                fight = run.match.combat
                x, y, z = soldier.mover.position
                winston.spawn = ((x, y, z + 8), winston.spawn[1])
                run.match.switch_hero(winston, find_hero("Winston"))
                for _ in range(25):
                    run.step({})
                run.step({winston: (0, 0, 16)})
                for _ in range(12):
                    run.step({})
                shield, = fight.barriers.world.actors.values()
                self.assertEqual(shield.health, 700)
                if blocked:
                    plane = z + 1
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
                    )
                winston.mover = Mover((x + 6, y, z + 8), world=run.match.collision_world,
                                       data=mover_data(winston.hero.body))
                run.step({})
                body = next(t for t in fight._targets() if t.owner is winston)
                origin = fight._eye(soldier)
                direction = combat.aim(0, 0)
                self.assertIsNone(combat.ray_capsule(origin, direction, *body.segment(), body.radius))
                self.assertIs(fight.barriers.intercept(soldier, origin, direction, 20)[1], shield)
                before = fight.vitals_of(body).total
                run.step({soldier: (0, 0, FIRE)})
                for _ in range(12):
                    run.step({})
                self.assertEqual(shield.health, 700 if blocked else 680)
                self.assertEqual(fight.vitals_of(body).total, before)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_winston_shell_absorbs_total_mayhem_after_actual_enemy_kill(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run = Run(KANEZAKA_DM, ("Junkrat", "Soldier", "Winston"))
                junkrat, soldier, winston = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(junkrat, find_hero("Junkrat"))
                run.match.switch_hero(winston, find_hero("Winston"))
                x, y, z = junkrat.mover.position
                soldier.spawn = ((x, y, z - 6), soldier.spawn[1])
                run.match.switch_hero(soldier, soldier.hero)
                winston.mover = Mover((x, y, z + 5.5), world=run.match.collision_world,
                                       data=mover_data(winston.hero.body))
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                if protected:
                    run.step({winston: (0, 0, 16)})
                    for _ in range(12):
                        run.step({})
                    bubble, = fight.barriers.world.actors.values()
                    self.assertEqual(bubble.health, 700)
                winston.mover = Mover((x, y, z + 1.6), world=run.match.collision_world,
                                       data=mover_data(winston.hero.body))
                run.step({})
                if protected:
                    self.assertGreater(math.dist((x, y, z), bubble.point), bubble.radius)
                    self.assertLess(math.dist(winston.mover.position, bubble.point), bubble.radius)
                target = next(t for t in fight._targets() if t.owner is winston)
                before = fight.vitals_of(target).total
                fire_at = aim(fight._eye(soldier), fight._eye(junkrat))
                for _ in range(110):
                    run.step({soldier: (*fire_at, FIRE)})
                    if fight.is_dead(junkrat):
                        break
                self.assertTrue(fight.is_dead(junkrat))
                dead = next(t for t in fight._targets() if t.owner is junkrat)
                self.assertEqual(fight.vitals_of(dead).killer, soldier.body)
                self.assertEqual(sum(rocket.shooter is junkrat for rocket in fight.rockets), 6)
                world_barriers = fight.barriers.world
                with (
                    patch.object(world_barriers, "damage", wraps=world_barriers.damage) as damage,
                    patch.object(fight, "_explode", wraps=fight._explode) as explode,
                ):
                    for _ in range(100):
                        run.step({})
                        if not any(rocket.shooter is junkrat for rocket in fight.rockets):
                            break
                    self.assertEqual(sum(call.args[0].shooter is junkrat for call in explode.call_args_list), 6)
                self.assertFalse(any(rocket.shooter is junkrat for rocket in fight.rockets))
                if protected:
                    amounts = sorted(call.args[1] for call in damage.call_args_list)
                    self.assertEqual(amounts, [1, 1] + [50] * 6)
                    self.assertTrue(all(call.args[0] is bubble for call in damage.call_args_list))
                    self.assertEqual(bubble.health, 398)
                    self.assertEqual(fight.vitals_of(target).total, before)
                    self.assertFalse(junkrat.client.messages(combat.HIT))
                else:
                    self.assertEqual(fight.vitals_of(target).total, before - 280.5)
                    hits = junkrat.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [1, 45, 45, 45, 45, 50, 50])
