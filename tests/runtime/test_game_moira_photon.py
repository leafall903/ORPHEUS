"""Actual Photon Barrier placement and Moira interactions on its mesh."""

import math
import os
import unittest
from dataclasses import replace

from test_game_combat import Run, aim, in_world
from test_game_moira_control import MCCREE

from ow174.game import combat, photon_barrier, stats
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"),
                     "verified build 104319 Photon Barrier mesh required")
class MoiraPhotonTests(unittest.TestCase):
    def arena(self, *, friendly=False):
        run = Run(names=("Moira", "Symmetra"))
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, caster = run.players
        self.assertTrue(match.switch_hero(source, find_hero("moira")))
        self.assertTrue(match.switch_hero(caster, find_hero("symmetra")))
        source.team, caster.team = 0, 0 if friendly else 1
        x, y, z = source.mover.position
        caster.mover.position[:] = (x, y, z + 6)
        for actor in run.players:
            actor.mover.body = None
        for _ in range(25):
            run.step({})
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == photon_barrier.GRAPH)
        return run, source, caster, instance

    def preview(self, run, caster, pitch):
        script = caster.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1680)
        script.set_ult_charge(cost)
        for _ in range(10):
            run.step({caster: (-32768, pitch, 32)})
        run.step({caster: (-32768, pitch, 0)})

    def confirm(self, run, caster, *, rotate=False, invalid_first=False):
        manager = run.match.combat.photon_barriers
        charge = caster.body_script.component.entity_var(ULT_CHARGE)
        pitch = -8192 if invalid_first else 8192
        self.preview(run, caster, pitch)
        if invalid_first:
            run.step({caster: (-32768, pitch, 1)})
            self.assertFalse(manager.actors)
            self.assertEqual(charge.value(), 1680)
            run.step({caster: (-32768, 8192, 0)})
        if rotate:
            run.step({caster: (-32768, 8192, 32)})
            run.step({caster: (-32768, 8192, 0)})
        run.step({caster: (-32768, 8192, 1)})
        self.assertEqual(charge.value(), 0)
        self.assertFalse(manager.actors)

    def deploy(self, run, caster, instance, *, rotate=False, invalid_first=False):
        self.confirm(run, caster, rotate=rotate, invalid_first=invalid_first)
        manager = run.match.combat.photon_barriers
        for _ in range(26):
            if manager.actors:
                break
            run.step({caster: (-32768, 8192, 0)})
        wall, = manager.actors.values()
        self.assertEqual(wall.health, 4000)
        self.assertEqual(wall.expires - run.frame, 750)
        self.assertEqual(instance.vars[38].value(), expr.Entity(wall.body))
        self.assertIn(0x156C, [child.graph.index for child in wall.component.instances.values()])
        return wall

    @staticmethod
    def health(fight, actor):
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is actor))

    def ally(self, run, source, caster):
        match = run.match
        hero = find_hero("soldier: 76")
        ally = match.add_player(3, "Ally", hero.guid, source.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, hero))
        ally.team = source.team
        x, y, z = caster.mover.position
        ally.mover.position[:] = (x, y, z + 0.6)
        ally.mover.body = None
        return ally

    def test_invalid_confirmation_preserves_charge_and_rotation_changes_mesh_contact(self):
        run, source, caster, instance = self.arena()
        fight = run.match.combat
        wall = self.deploy(run, caster, instance, rotate=True, invalid_first=True)
        self.assertAlmostEqual(abs(math.sin(wall.yaw)), 1)
        direction = aim(fight._eye(source), fight._eye(caster))
        source.mover.position[0] += 1.5
        source.mover.body = None
        self.assertIsNone(fight.photon_barriers.trace(source, fight._eye(source), combat.aim(*direction), 8))

    def test_grasp_breaks_hostile_mesh_without_body_overflow(self):
        run, source, caster, instance = self.arena()
        fight = run.match.combat
        wall = self.deploy(run, caster, instance)
        health = self.health(fight, caster)
        before = health.total
        direction = aim(fight._eye(source), fight._eye(caster))
        for _ in range(12):
            run.step({source: (*direction, 2)})
            if wall.health < 4000:
                break
        self.assertAlmostEqual(4000 - wall.health, 0.8, places=3)
        self.assertEqual(health.total, before)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
        wall.health = 0.4
        run.step({source: (*direction, 2)})
        self.assertEqual(wall.health, 0)
        self.assertEqual(health.total, before)
        self.assertFalse(fight.photon_barriers.actors)
        run.step({source: (*direction, 2)})
        self.assertLess(health.total, before)

    def test_lethal_grasp_during_confirmed_windup_aborts_wall(self):
        run, source, caster, instance = self.arena()
        fight = run.match.combat
        self.confirm(run, caster)
        wait = instance.states[instance.graph.node(0).state]
        self.assertTrue(wait.active)
        health = self.health(fight, caster)
        health.damage(health.total - 0.4)
        direction = aim(fight._eye(source), fight._eye(caster))
        for _ in range(8):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(run.frame, yaw=direction[0], pitch=direction[1], buttons=2)])
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertFalse(wait.active)
        for _ in range(28):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertFalse(fight.photon_barriers.actors)
            self.assertFalse(fight.photon_barriers.casts)

    def test_confirmed_wall_uses_same_lifetime_without_caster_commands(self):
        run, _, caster, instance = self.arena()
        start = run.frame
        expected = self.deploy(run, caster, instance)
        deployment = run.frame - start
        for jump in (0, 1, combat.COMBAT_CATCH_UP + 5):
            with self.subTest(jump=jump):
                run, _, caster, instance = self.arena()
                start = run.frame
                self.confirm(run, caster)
                manager = run.match.combat.photon_barriers
                self.assertIn(caster, manager.casts)
                end = start + deployment + max(1, jump)
                while run.frame < end:
                    run.frame = end if jump else run.frame + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if run.frame <= start + deployment:
                        self.assertFalse(manager.actors)
                wall, = manager.actors.values()
                self.assertEqual(wall.position, expected.position)
                self.assertEqual(wall.yaw, expected.yaw)
                self.assertEqual(wall.health, expected.health)
                self.assertEqual(wall.expires - start, expected.expires - start)
                self.assertEqual(instance.vars[38].value(), expr.Entity(wall.body))
                self.assertIn(photon_barrier.ACTOR_GRAPH,
                              [child.graph.index for child in wall.component.instances.values()])
                self.assertFalse(manager.casts)
                run.step({caster: (-32768, 8192, 0)})
                self.assertEqual(list(manager.actors.values()), [wall])

    def test_actual_flashbang_cancels_confirmed_cast_without_symmetra_commands(self):
        run, source, caster, instance = self.arena()
        match, fight = run.match, run.match.combat
        hero = heroes()[MCCREE]
        attacker = match.add_player(3, "McCree", hero.guid, source.team, False)
        in_world(match, attacker)
        run.players.append(attacker)
        self.assertTrue(match.switch_hero(attacker, hero))
        attacker.team = source.team
        x, y, z = caster.mover.position
        attacker.mover.position[:] = (x, y, z - 5)
        attacker.mover.body = None
        for _ in range(25):
            run.step({})
        self.preview(run, caster, 8192)
        for frame in range(12):
            run.step({attacker: (0, 0, 16 if frame == 0 else 0), caster: (-32768, 8192, 0)})
            if fight.rockets:
                break
        flash, = (rocket for rocket in fight.rockets if rocket.shooter is attacker)
        self.assertTrue(flash.detonate_on_expiry)
        run.step({caster: (-32768, 8192, 1)})
        wait = instance.states[instance.graph.node(0).state]
        self.assertTrue(wait.active)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        health = self.health(fight, caster)
        before = health.total
        self.assertFalse(fight.is_stunned(caster))
        for _ in range(30):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            self.assertFalse(fight.photon_barriers.actors)
        self.assertNotIn(flash, fight.rockets)
        self.assertTrue(fight.is_stunned(caster))
        self.assertFalse(wait.active)
        self.assertFalse(fight.photon_barriers.casts)
        self.assertEqual(before - health.total, 25)

    def test_hero_swap_discards_confirmed_cast(self):
        run, _, caster, instance = self.arena()
        fight = run.match.combat
        self.confirm(run, caster)
        self.assertTrue(instance.states[instance.graph.node(0).state].active)
        self.assertTrue(run.match.switch_hero(caster, find_hero("soldier: 76")))
        run.frame += 30
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.photon_barriers.casts)
        self.assertFalse(fight.photon_barriers.actors)

    def test_spray_respects_allegiance_and_resumes_after_expiry(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, source, caster, instance = self.arena(friendly=friendly)
                match, fight = run.match, run.match.combat
                wall = self.deploy(run, caster, instance)
                ally = self.ally(run, source, caster)
                health = self.health(fight, ally)
                health.damage(100)
                direction = aim(fight._eye(source), fight._eye(ally))
                for _ in range(30):
                    run.step({source: (*direction, 1)})
                self.assertEqual(health.total > 100, friendly)
                self.assertEqual(wall.health, 4000)
                if not friendly:
                    run.frame = wall.expires - 1
                    match.update(run.frame * combat.TICK, run.frame)
                    self.assertIn(wall, fight.photon_barriers.actors.values())
                    run.step({})
                    self.assertFalse(fight.photon_barriers.actors)
                    for _ in range(25):
                        run.step({source: (*direction, 1)})
                    self.assertGreater(health.total, 100)

    def test_both_orbs_cross_mesh_before_tethering(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, caster, instance = self.arena()
                fight = run.match.combat
                wall = self.deploy(run, caster, instance)
                recipient = self.ally(run, source, caster) if healing else caster
                health = self.health(fight, recipient)
                if healing:
                    health.damage(80)
                before = health.total
                direction = aim(fight._eye(source), fight._eye(recipient))
                run.step({source: (*direction, 16)})
                for _ in range(40):
                    run.step({source: (*direction, 1 if healing else 2)})
                    if fight.moira_orbs:
                        break
                orb, = fight.moira_orbs
                budget, radius = (300, 5) if healing else (200, 4)
                target = next(target for target in fight._targets() if target.owner is recipient)
                a, b = target.segment()
                centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
                blocked_in_range = False
                for _ in range(45):
                    distance = math.dist(orb.position, centre)
                    ray = tuple((right - left) / distance
                                for left, right in zip(orb.position, centre, strict=True))
                    blocked = fight.photon_barriers.trace(source, orb.position, ray, distance) is not None
                    if health.total != before:
                        self.assertFalse(blocked)
                        break
                    reach = combat.segment_distance(orb.position, a, b) - target.radius
                    blocked_in_range |= blocked and reach <= radius
                    self.assertEqual(orb.energy, budget)
                    run.step({})
                self.assertTrue(blocked_in_range)
                self.assertGreater(health.total - before if healing else before - health.total, 0)
                self.assertLess(orb.energy, budget)
                self.assertEqual(orb.bounces, 0)
                self.assertEqual(wall.health, 4000)

    def test_melee_hits_mesh_and_coalescence_kills_owner_without_retiring_wall(self):
        run, source, caster, instance = self.arena()
        match, fight = run.match, run.match.combat
        wall = self.deploy(run, caster, instance)
        health = self.health(fight, caster)
        before = health.total
        direction = aim(fight._eye(source), fight._eye(caster))
        contact = fight.photon_barriers.trace(source, fight._eye(source), combat.aim(*direction), 8)
        self.assertIsNotNone(contact)
        source.mover.position[2] += max(0, contact[0] - 2.1)
        source.mover.body = None
        direction = aim(fight._eye(source), fight._eye(caster))
        run.step({source: (*direction, 0x800)})
        for _ in range(18):
            run.step({source: (*direction, 0)})
        self.assertEqual(wall.health, 3970)
        self.assertEqual(health.total, before)
        health.damage(health.total - 0.4)
        script = source.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({source: (*direction, 32)})
        for _ in range(65):
            if health.dead:
                break
            run.step({source: (*direction, 0)})
        self.assertTrue(health.dead)
        self.assertEqual(wall.health, 3970)
        self.assertIs(fight.photon_barriers.actors[wall.source_body], wall)
        run.frame += 250
        match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(caster.body, wall.source_body)
        self.assertIs(fight.photon_barriers.actors[wall.source_body], wall)
