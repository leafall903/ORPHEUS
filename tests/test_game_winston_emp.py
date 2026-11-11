"""Sombra's authored EMP against Winston and his deployed sphere."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_winston_hack as fixtures
from test_game_mover import box, wall

from ow174.game import combat, hacks, npc, zarya
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, graph


class WinstonEmpTests(unittest.TestCase):
    setUp = fixtures.WinstonHackTests.setUp
    move = staticmethod(fixtures.WinstonHackTests.move)
    step = fixtures.WinstonHackTests.step
    health = fixtures.WinstonHackTests.health

    def deploy(self):
        self.step(16)
        for _ in range(4):
            self.step()
        return self.fight.barriers.actors[self.owner.body]

    def emp(self):
        caster = self.caster.body_script
        charge = caster.component.entity_var(338).value()
        caster.set_ult_charge(charge)
        self.step(cast=32)
        self.assertLess(caster.component.entity_var(342).value(), charge)
        for _ in range(12):
            self.step()
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        for _ in range(35):
            self.step()

    def project(self, *, cast=0):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[zarya.HERO]))
        self.move(self.ally, (-3, 1, 0))
        self.step(ally=16, cast=cast)
        for _ in range(4):
            self.step(cast=cast)
        self.assertTrue(self.fight.zarya_barriers.protects(self.owner))
        return self.fight.zarya_barriers.projected[self.ally.body]

    def test_emp_routes_both_victim_graphs_and_preserves_health(self):
        self.move(self.caster, (0, 1, -6))
        bubble = self.deploy()
        self.emp()
        component = self.owner.body_script.component
        self.assertTrue(any(i.graph.index == hacks.EMP_HIT for i in component.instances.values()))
        application, end = self.fight.hacks.effects[self.owner.body][0]
        self.assertEqual(application.source_entity(), self.caster.body)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(bubble.health, 0)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.step(16)
        for _ in range(4):
            self.step()
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertNotIn(application.id, component.instances)

    def test_emp_collapses_bubble_even_with_no_victim_in_range(self):
        bubble = self.deploy()
        token = self.fight.barriers.seen[self.owner.body]
        self.move(self.owner, (0, 1, -30))
        self.emp()
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.fight.barriers.seen[self.owner.body], token)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)

    def test_emp_collapses_release_frame_barrier_but_not_a_later_accepted_deployment(self):
        for cast_offset in (19, 20):
            with self.subTest(cast_offset=cast_offset):
                self.setUp()
                component = self.owner.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0xFA)
                deployment = instance.state(3)
                caster = self.caster.body_script
                caster.set_ult_charge(caster.component.entity_var(338).value())
                self.step(cast=32)
                for offset in range(2, 22):
                    self.step(16 if offset == cast_offset else 0)
                self.assertTrue(deployment.active)
                self.assertFalse(self.fight.hacks.active(self.owner.body))
                self.assertNotIn(self.owner.body, self.fight.barriers.actors)

                self.step()
                self.assertTrue(self.fight.hacks.active(self.owner.body))
                self.assertNotIn(self.owner.body, self.fight.barriers.actors)
                self.assertEqual(deployment.active, cast_offset == 20)
                self.step()
                self.assertEqual(deployment.counter, 1)
                self.assertFalse(deployment.active)
                self.assertEqual(instance.state(2).cur, 13)
                self.assertIn(self.owner.body, self.fight.barriers.seen)
                if cast_offset == 20:
                    bubble = self.fight.barriers.actors[self.owner.body]
                    self.assertEqual(bubble.health, 700)
                    self.assertAlmostEqual(bubble.expires - self.fight.now, 9)
                else:
                    self.assertNotIn(self.owner.body, self.fight.barriers.actors)
                self.assertEqual(self.health(self.owner), 500)

    def test_ally_and_uncharged_emp_cannot_collapse_the_bubble(self):
        bubble = self.deploy()
        self.step(cast=32)
        for _ in range(40):
            self.step()
        self.assertEqual(bubble.health, 700)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.caster.team = self.owner.team
        self.emp()
        self.assertEqual(bubble.health, 700)
        self.assertFalse(self.fight.hacks.active(self.owner.body))

    def test_wall_blocks_both_winston_and_deployed_sphere(self):
        self.move(self.caster, (0, 1, 9))
        bubble = self.deploy()
        self.match.collision_world = World(box(-40, 40, -1, 1, -40, 40) + wall(-20, 7, 20, 7, -2, 8))
        self.emp()
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(self.owner), 500)
        self.assertFalse(self.fight.hacks.active(self.owner.body))

    def test_emp_drains_character_shields_without_spilling(self):
        self.assertTrue(self.match.switch_hero(self.owner, heroes()[zarya.HERO]))
        self.move(self.owner, (0, 1, 0))
        self.step()
        vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.owner))
        self.assertGreater(vitals.now[combat.SHIELDS_POOL], 0)
        health = vitals.now[combat.HEALTH_POOL]
        armour = vitals.now[combat.ARMOUR_POOL]
        self.emp()
        self.assertTrue(self.fight.hacks.active(self.owner.body))
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], health)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], armour)

    def test_emp_breaks_projected_barrier_and_spares_winston(self):
        bubble = self.deploy()
        shield = self.project()
        self.emp()
        self.assertEqual(shield.remaining, 0)
        self.assertFalse(self.fight.zarya_barriers.protects(self.owner))
        self.assertEqual(self.ally.body_script.component.entity_var(87).value(), 40)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(bubble.health, 0)

    def test_emp_breaks_personal_barrier_without_draining_underlying_shields(self):
        self.assertTrue(self.match.switch_hero(self.owner, heroes()[zarya.HERO]))
        self.move(self.owner, (0, 1, 0))
        self.step(8)
        self.assertTrue(self.fight.zarya_barriers.protects(self.owner))
        shield = self.fight.zarya_barriers.active[self.owner.body]
        before = self.health(self.owner)
        self.emp()
        self.assertEqual(shield.remaining, 0)
        self.assertEqual(self.owner.body_script.component.entity_var(87).value(), 40)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertEqual(self.health(self.owner), before)

    def test_projected_barrier_interrupts_hack_without_damage_and_expires_while_idle(self):
        for _ in range(12):
            self.step(cast=2)
        ability = next(i for i in self.caster.body_script.component.instances.values()
                       if i.graph.index == hacks.HACK)
        self.assertEqual(ability.vars[375].value(), (expr.Entity(self.owner.body),))
        shield = self.project(cast=2)
        energy = self.ally.body_script.component.entity_var(87)
        before = energy.value()
        for _ in range(65):
            self.step(cast=2)
        self.assertFalse(self.fight.hacks.active(self.owner.body))
        self.assertEqual(shield.remaining, 200)
        self.assertEqual(energy.value(), before)
        end = self.run.frame + 130
        self.match.update(end * combat.TICK, end, frames=False)
        self.run.frame = end
        self.assertFalse(self.fight.zarya_barriers.protects(self.owner))
        self.step()
        for _ in range(75):
            self.step(cast=2)
        self.assertTrue(self.fight.hacks.active(self.owner.body))

    def test_emp_runs_victim_graphs_and_drains_only_shields_on_all_five_talon_bodies(self):
        catalog = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        data = os.environ.get("ORPHEUS_PVE_NPC_DATA")
        if not catalog or not data:
            self.skipTest("actual Talon catalog or graph bundle unavailable")
        caches = (graph.graph, graph._records, graph._data)
        for cache in caches:
            self.addCleanup(cache.cache_clear)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", Path(data) / "game_graphs_174.json.gz"))
        for cache in caches:
            cache.cache_clear()
        definitions = npc.load_catalog(Path(catalog))
        self.assertEqual({d.name for d in definitions.values()},
                         {'Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'})
        targets = [
            npc.spawn(self.match, definition, (2 + index, 1, 2), team=self.owner.team, yaw=32768)
            for index, definition in enumerate(definitions.values())
        ]
        enforcer = next(d for d in definitions.values() if d.name == 'Enforcer')
        friendly = npc.spawn(self.match, enforcer, (-2, 1, 2), team=self.caster.team, yaw=32768)
        self.assertTrue(all(bot.body_script is not None for bot in (*targets, friendly)))
        pools = {bot.entity: dict(self.fight.vitals[bot.entity].now) for bot in (*targets, friendly)}
        self.emp()
        effects = {}
        for bot in targets:
            with self.subTest(npc=bot.name):
                component = bot.body_script.component
                self.assertTrue(any(i.graph.index == hacks.EMP_HIT for i in component.instances.values()))
                self.assertTrue(self.fight.hacks.active(bot.entity))
                [(application, end)] = self.fight.hacks.effects[bot.entity]
                self.assertEqual(application.graph.index, hacks.APPLICATION)
                self.assertEqual(application.source_entity(), self.caster.body)
                self.assertEqual(self.fight.vitals[bot.entity].now,
                                 {**pools[bot.entity], combat.SHIELDS_POOL: 0})
                effects[bot.entity] = (application, end)
        self.assertEqual(self.fight.vitals[friendly.entity].now, pools[friendly.entity])
        self.assertFalse(self.fight.hacks.active(friendly.entity))
        [deadline] = {end for _, end in effects.values()}
        self.run.frame = deadline - 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertTrue(all(self.fight.hacks.active(entity) for entity in effects))
        self.run.frame = deadline
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        for entity, (application, _) in effects.items():
            self.assertFalse(self.fight.hacks.active(entity))
            self.assertNotIn(application.id, application.component.instances)
