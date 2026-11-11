"""Actual 104319 Minefield release and Roadhog's ordinary commands."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_roadhog_piledriver as pile_tests
from test_game_combat import aim, in_world
from test_game_mover import box

from ow174.game import ball_mines, stats, world
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class RoadhogMinefieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not graph.GRAPHS_PATH.is_file() or graph.entity_body(ball_mines.BODY) is None:
            raise unittest.SkipTest("prepared 2578/151A Minefield graph bundle required")

    def place(self, actor, point):
        actor.mover.position[:] = point
        actor.mover.body = None

    def scene(self):
        run, hog, ball, other = pile_tests.RoadhogPiledriverTests().scene()
        x, y, z = hog.mover.position
        self.place(hog, (x + 35, y, z))
        return run, hog, ball, other

    def cast(self, run, ball):
        script = ball.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({ball: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def deploy(self, run, ball, *, armed=True):
        self.assertIsNotNone(graph.entity_body(ball_mines.BODY))
        self.cast(run, ball)
        field = run.match.combat.mines
        for _ in range(180 if armed else 24):
            run.step({})
        mines = list(field.entities.values())
        self.assertEqual(len(mines), 15)
        self.assertTrue(all(m.landed and m.armed for m in mines) if armed else
                        all(not m.landed and not m.armed for m in mines))
        return mines

    def isolate(self, run, ball):
        mine, *others = self.deploy(run, ball)
        for other in others:
            run.match.combat.mines.remove(other)
        x, y, z = mine.position
        self.place(ball, (x + 23, y, z))
        return mine

    def pool(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def test_actual_q_expands_fifteen_live_entities_then_arms_and_expires(self):
        run, hog, ball, _ = self.scene()
        field = run.match.combat.mines
        mines = self.deploy(run, ball, armed=False)
        self.assertEqual(len(field.seen), 3)
        self.assertEqual({m.health for m in mines}, {50})
        self.assertEqual({m.source for m in mines}, {ball.body})
        self.assertEqual({m.body_script.primary().graph.index for m in mines}, {0x151A})
        self.assertEqual({u.entity for u in hog.client.entities if u.op == world.OP_CREATE
                          and u.entity in field.entities}, set(field.entities))
        for _ in range(90):
            run.step({})
        self.assertTrue(all(m.landed and not m.armed for m in mines))
        for _ in range(66):
            run.step({})
        self.assertTrue(all(m.armed for m in mines))
        for _ in range(1400):
            run.step({})
            if not field.entities:
                break
        self.assertFalse(field.entities)
        self.assertTrue(all(m.removing and not m.triggered for m in mines))
        self.assertEqual(self.pool(run, hog).total, 600)

    def test_single_explosion_hits_once_and_does_not_stun(self):
        run, hog, ball, _ = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        x, y, z = mine.position
        self.place(hog, (x + 0.55, y, z))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 130)
        self.assertEqual(fight.impulses.velocity(hog.body), (5, 5, 0))
        self.assertFalse(fight.is_stunned(hog))
        for _ in range(85):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(self.pool(run, hog).total, 470)

    def test_mine_blast_preserves_buffered_quick_melee(self):
        run, hog, ball, victim = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, ball.team, victim.team = 0, 1, 1
        x, y, z = mine.position
        self.place(hog, (x + 3.3, y, z))
        self.place(victim, (x + 3.3, y, z + 1.5))
        run.step({})

        held = {hog: (0, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.pool(run, victim).total
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        while run.frame < first + 54:
            run.step(held)
        self.assertFalse(mine.triggered)
        self.assertEqual(self.pool(run, victim).total, before - 30)
        self.place(hog, (x + .55, y, z))
        self.place(victim, (x + 1.0, y, z + 1.5))
        run.step(held)
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)
        self.assertEqual(fight.impulses.velocity(hog.body), (5, 5, 0))
        while run.frame < first + 61:
            run.step(held)
        self.assertEqual(self.pool(run, victim).total, before - 30)

        run.step(held)
        self.assertEqual(run.frame, first + 62)
        self.assertGreater(hog.mover.position[1], y)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.pool(run, victim).total, before - 60)
        for _ in range(65):
            run.step(held)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(self.pool(run, hog).total, 470)
        self.assertEqual(self.pool(run, victim).total, before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_breather_halves_the_explosion_and_keeps_healing(self):
        run, hog, ball, _ = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        pool = self.pool(run, hog)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        run.step({hog: (0, 0, 16)})
        for _ in range(60):
            if fight.incoming_damage_scale(hog) == .5 and pool.total > 200:
                break
            run.step({})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 65)
        after = pool.total
        for _ in range(20):
            run.step({})
        self.assertGreater(pool.total, after)

    def test_team_and_occlusion_prevent_trigger(self):
        for mode in ("team", "wall"):
            with self.subTest(mode=mode):
                run, hog, ball, _ = self.scene()
                mine = self.isolate(run, ball)
                x, y, z = mine.position
                if mode == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = mine.team
                else:
                    run.match.collision_world = World(
                        box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                        + box(x + .1, x + .3, y, y + 5, z - 5, z + 5),
                    )
                self.place(hog, (x + 1.05, y, z))
                for _ in range(12):
                    run.step({})
                self.assertFalse(mine.triggered)
                self.assertEqual(self.pool(run, hog).total, 600)

    def test_primary_secondary_melee_and_whole_hog_can_damage_the_mine(self):
        for button, distance in ((1, 2.3), (2, 4), (2, 10), (0x800, 2.2), (32, 2.3)):
            with self.subTest(button=button, distance=distance):
                run, hog, ball, _ = self.scene()
                mine = self.isolate(run, ball)
                fight = run.match.combat
                x, y, z = mine.position
                self.place(hog, (x, y, z - distance))
                for _ in range(8):
                    run.step({})
                self.assertFalse(mine.triggered)
                script = hog.body_script
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(hog), mine.centre())
                run.step({hog: (*angles, button)})
                for _ in range(90):
                    run.step({hog: (*angles, 0)})
                if button == 0x800:
                    self.assertEqual(mine.health, 20)
                else:
                    self.assertLess(mine.health, 50)
                if not mine.health:
                    for _ in range(100):
                        if mine.entity not in fight.mines.entities:
                            break
                        run.step({})
                    self.assertNotIn(mine.entity, fight.mines.entities)
                self.assertFalse(mine.triggered)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertEqual(hog.stats.value(stats.HERO_DAMAGE), 0)

    def test_owner_replacement_retires_flying_mines(self):
        run, _, ball, _ = self.scene()
        mines = self.deploy(run, ball, armed=False)
        field = run.match.combat.mines
        self.assertTrue(run.match.switch_hero(ball, heroes()[SOLDIER]))
        self.assertFalse(field.entities)
        self.assertFalse(field.seen)
        self.assertTrue(all(m.removing and not m.body_script.component.instances for m in mines))

    def test_explosion_keeps_whole_hog_running(self):
        run, hog, ball, other = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = mine.position
        self.place(hog, (x, y, z - 4))
        self.place(other, (x + 9, y, z))
        look = aim(fight._eye(hog), (x + 9, y + 1.2, z))
        run.step({hog: (*look, 32)})
        self.assertTrue(ultimate.flags & 1)
        self.place(hog, (x + .55, y, z))
        run.step({hog: (*look, 0)})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)
        self.assertTrue(ultimate.flags & 1)
        before = self.pool(run, other).total
        for _ in range(50):
            bx, by, bz = other.mover.position
            look = aim(fight._eye(hog), (bx, by + 1.2, bz))
            run.step({hog: (*look, 0)})
            if self.pool(run, other).total < before:
                break
        self.assertLess(self.pool(run, other).total, before)
        self.assertTrue(ultimate.flags & 1)

    def test_live_mine_health_and_vm_are_causal_server_state(self):
        from unittest.mock import patch

        import test_server_snapshot as storage

        from ow174.game.commands import Command
        from ow174.game.server_snapshot import snapshot_digest

        fixture = storage.ServerSnapshotTests()
        session, _, players = fixture.scene()
        ball = players[0]
        match = ball.match
        session.apply("switch_hero", {"account": ball.account_lo, "hero": ball_mines.HERO})
        self.assertEqual(ball.hero.guid, ball_mines.HERO)
        script = ball.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for offset in range(75):
            frame = script.frame + 1
            match.update(frame * .016, frame)
            ball.take_commands([Command(frame, buttons=32 if offset == 45 else 0)])
        mines = match.combat.mines.entities
        self.assertEqual(len(mines), 15)
        mine = next(iter(mines.values()))
        self.assertIsNotNone(ball.client.bot_stream(mine.entity))
        with patch.object(ball_mines.MineScript, "advance", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(fixture.capture(session))
            self.assertEqual(before, snapshot_digest(fixture.capture(session)))
            mine.health -= 1
            self.assertNotEqual(before, snapshot_digest(fixture.capture(session)))
            mine.health += 1
            self.assertEqual(before, snapshot_digest(fixture.capture(session)))

    def test_uncharged_q_refuses_then_a_real_mine_credits_lethal_damage(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        run.step({ball: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.mines.entities)
        mine = self.isolate(run, ball)
        pool = self.pool(run, hog)
        pool.damage(500)
        fight._sync_body_health(hog, pool)
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, ball.body)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_projected_zarya_bubble_absorbs_blast_and_blocks_its_impulse(self):
        self.check_projected_zarya_bubble(body_occluded=False)

    def test_projected_zarya_bubble_triggers_mine_with_body_occluded(self):
        self.check_projected_zarya_bubble(body_occluded=True)

    def check_projected_zarya_bubble(self, *, body_occluded):
        from ow174.game.script.expr import Entity

        run, hog, ball, support = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = mine.position
        self.place(hog, (x, y, z + 6))
        self.place(support, (x, y, z + 12))
        for _ in range(45):
            run.step({})
        look = aim(fight._eye(support), (x, y + 1.2, z + 6))
        run.step({support: (*look, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        if body_occluded:
            run.match.collision_world = World(
                box(-50, 50, y - 1, y, -50, 50) + box(x - 2, x + 2, y, y + 3, z + .52, z + .54),
            )
        self.place(hog, (x, y, z + (1.35 if body_occluded else .55)))
        run.step({})
        self.assertTrue(mine.triggered)
        selected = mine.body_script.primary().vars[375].value()
        self.assertEqual(selected, () if body_occluded else (Entity(hog.body),))
        self.assertEqual(bubble.health, 70)
        self.assertAlmostEqual(support.body_script.component.entity_var(87).value(), 26)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertNotIn(hog.body, fight.impulses.live)


    def test_deflected_roadhog_primary_and_slug_hit_his_allied_mine(self):
        for button in (1, 2):
            with self.subTest(button=button):
                self.check_deflected_scrap_hits_mine(button)

    def check_deflected_scrap_hits_mine(self, button):
        from ow174.game import defenses

        run, hog, ball, genji = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = ball.team = 0
        genji.team = 1
        mine = self.isolate(run, ball)
        hero = next(hero for hero in heroes().values() if hero.name == "Genji")
        self.assertTrue(match.switch_hero(genji, hero))
        x, y, z = mine.position
        self.place(hog, (x, y, z - 4))
        self.place(genji, (x, y, z + 2.4))
        for _ in range(45):
            run.step({})
        self.assertNotIn(mine, tuple(fight.mines.enemies(hog)))
        self.assertIn(mine, tuple(fight.mines.enemies(genji)))
        hog_look = aim(fight._eye(hog), (x, y + 1, z + 2.4))
        genji_look = aim(fight._eye(genji), mine.centre())
        run.step({genji: (*genji_look, 16)})
        for _ in range(5):
            run.step({genji: (*genji_look, 0)})
        target = next(target for target in fight._targets() if target.owner is genji)
        origin = fight._eye(hog)
        gate = defenses.projectile_gate(origin, (0, 0, 1), 8, (target,))
        self.assertIsNotNone(gate)
        genji_pool = self.pool(run, genji)
        before = genji_pool.total
        run.step({hog: (*hog_look, button), genji: (*genji_look, 0)})
        for _ in range(80):
            run.step({genji: (*genji_look, 0)})
            if mine.entity not in fight.mines.entities:
                break
        if button == 2:
            self.assertEqual(mine.health, 0)
            for _ in range(100):
                if mine.entity not in fight.mines.entities:
                    break
                run.step({})
            self.assertNotIn(mine.entity, fight.mines.entities)
        else:
            self.assertGreater(mine.health, 0)
            self.assertLess(mine.health, 50)
            self.assertIn(mine.entity, fight.mines.entities)
        self.assertFalse(mine.triggered)
        self.assertEqual(genji_pool.total, before)
        self.assertEqual(hog.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_winston_dome_intercepts_one_blast_for_two_targets_even_on_break(self):
        for dome_health in (700, 50):
            with self.subTest(dome_health=dome_health):
                self.check_shared_dome_blast(dome_health)

    def test_mine_resolves_covered_and_exposed_targets_in_either_order(self):
        for exposed_first in (False, True):
            for dome_health in (700, 50):
                with self.subTest(exposed_first=exposed_first, dome_health=dome_health):
                    self.check_shared_dome_blast(dome_health, exposed_first=exposed_first)

    def check_shared_dome_blast(self, dome_health, *, exposed_first=None):
        from ow174.game.script.expr import Entity
        from ow174.game.winston_barrier import RADIUS

        run, hog, ball, winston = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = winston.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Winston")
        self.assertTrue(run.match.switch_hero(winston, hero))
        x, y, z = mine.position
        self.place(winston, (x + RADIUS + .35, y, z))
        for _ in range(45):
            run.step({})
        run.step({winston: (0, 0, 16)})
        for _ in range(7):
            run.step({})
        dome, = fight.winston_bubbles.actors.values()
        self.assertEqual(dome.health, 700)
        if dome_health < dome.health:
            fight.winston_bubbles.damage(dome, dome.health - dome_health)
        self.assertFalse(mine.triggered)
        hog_pool, winston_pool = self.pool(run, hog), self.pool(run, winston)
        before = hog_pool.total, winston_pool.total

        self.place(hog, (x + .95, y, z + .2))
        self.place(winston, (x + .95, y, z - .2))
        expected_targets = {Entity(hog.body), Entity(winston.body)}
        if exposed_first is not None:
            match = run.match
            exposed = match.add_player(9, "Exposed target", SOLDIER, 0, False)
            in_world(match, exposed)
            self.assertTrue(match.switch_hero(exposed, heroes()[SOLDIER]))
            self.place(exposed, (x - .75, y, z))
            exposed_pool = self.pool(run, exposed)
            self.assertEqual(exposed_pool.total, 200)
            expected_targets.add(Entity(exposed.body))
            if exposed_first:
                match.players.reverse()
        run.step({})
        self.assertTrue(mine.triggered)
        selected = mine.body_script.primary().vars[375].value()
        self.assertEqual(set(selected), expected_targets)
        self.assertEqual(dome.health, max(0, dome_health - 130))
        self.assertEqual((hog_pool.total, winston_pool.total), before)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0 if exposed_first is None else 130)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertNotIn(winston.body, fight.impulses.live)
        if exposed_first is not None:
            self.assertEqual(selected[0 if exposed_first else -1], Entity(exposed.body))
            self.assertEqual(exposed_pool.total, 70)
            self.assertLess(fight.impulses.velocity(exposed.body)[0], 0)
        for _ in range(3):
            run.step({})
        self.assertEqual(dome.retired, dome_health < 130)
        self.assertEqual((hog_pool.total, winston_pool.total), before)
        if exposed_first is not None:
            self.assertEqual(exposed_pool.total, 70)

    def test_airborne_mines_cross_deployed_energy_barriers_and_still_arm(self):
        from test_game_roadhog_orisa_barrier import RoadhogOrisaBarrierTests
        from test_game_roadhog_sigma_barrier import RoadhogSigmaBarrierTests
        from test_game_roadhog_symmetra import RoadhogSymmetraTests
        from test_game_roadhog_winston import RoadhogWinstonTests

        for name, model in (("Winston", None), ("Sigma", "SIGMA"),
                            ("Orisa", "ORISA"), ("Symmetra", "SYMMETRA")):
            with self.subTest(barrier=name):
                if model is not None and not os.environ.get(f"ORPHEUS_{model}_COLLISION"):
                    self.skipTest(f"104319 {name} collision input required")
                run, hog, ball, caster = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = caster.team = 0
                ball.team = 1
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(run.match.switch_hero(caster, hero))
                x, y, z = ball.mover.position
                feet = (x + 5.35, y, z) if name == "Winston" else (x, y, z + 8)
                self.place(caster, feet)
                for _ in range(45):
                    run.step({})
                if name == "Winston":
                    actor = RoadhogWinstonTests().cast(run, hog, caster)
                elif name == "Sigma":
                    actor = fight.sigma_barriers.actors[caster.body]
                    RoadhogSigmaBarrierTests().deploy(run, hog, caster, actor)
                elif name == "Orisa":
                    actor = RoadhogOrisaBarrierTests().deploy(run, hog, caster)
                    # The low mesh crosses the descending part of the volley.
                    self.place(ball, (x, y, actor.position[2] - 4))
                else:
                    actor = RoadhogSymmetraTests().cast(run, hog, caster)
                health = actor.health
                self.place(caster, (x + 30, y, z))
                self.cast(run, ball)
                crossed = set()
                for _ in range(180):
                    crossed.update(self.step_across_barrier(run, ball, actor, {}))
                mines = fight.mines.entities
                self.assertTrue(crossed, "no airborne chord crossed the live hostile barrier surface")
                self.assertEqual(len(mines), 15)
                self.assertTrue(crossed <= mines.keys())
                self.assertTrue(all(m.armed and (m.health, m.source, m.team) == (50, ball.body, ball.team)
                                    for m in mines.values()))
                self.assertEqual(actor.health, health)
                mine = mines[min(crossed)]
                for other in tuple(mines.values()):
                    if other is not mine:
                        fight.mines.remove(other)
                self.place(hog, mine.position)
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(self.pool(run, hog).total, 470)

    def step_across_barrier(self, run, ball, barrier, commands):
        fight = run.match.combat
        previous = {mine.entity: mine.position for mine in fight.mines.entities.values() if not mine.landed}
        run.step(commands)
        crossed = set()
        for mine in fight.mines.entities.values():
            if mine.landed or mine.entity not in previous:
                continue
            start = previous[mine.entity]
            length = math.dist(start, mine.position)
            if length == 0:
                continue
            direction = tuple((b - a) / length for a, b in zip(start, mine.position, strict=True))
            contacts = fight._barrier_contacts(ball, start, direction, length)
            if any(cover is barrier and 0 < distance < length for distance, cover, _ in contacts):
                crossed.add(mine.entity)
        return crossed

    def test_airborne_mines_cross_held_shields_and_still_arm(self):
        for name, offset in (("Reinhardt", 5.4), ("Brigitte", 5.85)):
            with self.subTest(shield=name):
                if not os.environ.get(f"ORPHEUS_{name.upper()}_COLLISION"):
                    self.skipTest(f"104319 {name} collision input required")
                run, hog, ball, defender = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = defender.team = 0
                ball.team = 1
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(run.match.switch_hero(defender, hero))
                x, y, z = ball.mover.position
                self.place(defender, (x, y, z + offset))
                raised = {defender: (-32768, 0, 2)}
                for _ in range(45):
                    run.step(raised)
                shield = fight.barriers[defender.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, shield.maximum)
                self.cast(run, ball)
                for _ in range(100):
                    crossed = self.step_across_barrier(run, ball, defender, raised)
                    if crossed:
                        break
                self.assertTrue(crossed, "no airborne chord crossed the raised hostile shield")
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, shield.maximum)
                mine = fight.mines.entities[min(crossed)]
                self.assertFalse(mine.landed or mine.armed)
                self.place(defender, (x + 30, y, z))
                for _ in range(150):
                    run.step(raised)
                mines = fight.mines.entities
                self.assertEqual(len(mines), 15)
                self.assertIs(mines[mine.entity], mine)
                self.assertTrue(all(m.armed and (m.health, m.source, m.team) == (50, ball.body, ball.team)
                                    for m in mines.values()))
                self.assertEqual(shield.current, shield.maximum)
                for other in tuple(mines.values()):
                    if other is not mine:
                        fight.mines.remove(other)
                self.place(hog, mine.position)
                run.step(raised)
                self.assertTrue(mine.triggered)
                self.assertEqual(self.pool(run, hog).total, 470)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_COLLISION"), "104319 Sigma collision input required")
    def test_moving_sigma_barrier_alone_triggers_only_a_touching_enemy_mine(self):
        from ow174.game import barriers, combat

        for allied, offset in ((False, 0), (True, 0), (False, 6)):
            with self.subTest(allied=allied, offset=offset):
                run, hog, ball, sigma = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, ball.team = 0, 1
                sigma.team = ball.team if allied else hog.team
                hero = next(hero for hero in heroes().values() if hero.name == "Sigma")
                self.assertTrue(run.match.switch_hero(sigma, hero))
                mine = self.isolate(run, ball)
                self.assertTrue(mine.armed)
                x, y, z = mine.position
                centre = mine.centre()
                self.place(sigma, (x + offset, y, z + 8))
                heading = aim(fight._eye(sigma), (x + offset, y + 1, z))
                service = fight.sigma_barriers
                shield = service.actors[sigma.body]
                primary = mine.body_script.primary()
                radius = primary.evaluate(primary.graph.node(52).field("m_radius"))
                for target in fight._enemies(ball):
                    distance = combat.segment_distance(centre, *target.segment()) - target.radius
                    self.assertGreater(distance, radius)
                touched = False
                for _ in range(60):
                    run.step({sigma: (*heading, 2)})
                    if shield.active() and barriers.overlaps(
                        service.geometry, shield.position, shield.yaw, shield.pitch,
                        centre, centre, ball_mines.HIT_RADIUS, 0, 0,
                    ):
                        touched = True
                self.assertEqual(touched, offset == 0)
                triggered = not allied and touched
                self.assertEqual(mine.triggered, triggered)
                self.assertEqual(shield.health, 570 if triggered else 700)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0)
                if triggered:
                    self.assertEqual(primary.vars[375].value(), ())
                    for _ in range(120):
                        run.step({})
                    self.assertNotIn(mine.entity, fight.mines.entities)
                else:
                    self.assertTrue(mine.armed)

    def test_armed_mine_hits_orisa_and_photon_barriers_separately_and_together(self):
        from test_game_combat import in_world

        from ow174.game import barriers

        for names in (("Orisa",), ("Symmetra",), ("Orisa", "Symmetra")):
            with self.subTest(barriers=names):
                if any(not os.environ.get(f"ORPHEUS_{name.upper()}_COLLISION") for name in names):
                    self.skipTest("104319 Orisa and Photon Barrier model inputs required")
                run, hog, ball, defender = self.scene()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, ball.team = 0, 1
                defenders = [defender]
                if len(names) == 2:
                    sym = match.add_player(4, "Photon Barrier", SOLDIER, 3, False)
                    in_world(match, sym)
                    run.players.append(sym)
                    defenders.append(sym)
                x, y, z = ball.mover.position
                casters = dict(zip(names, defenders, strict=True))
                for name, actor in casters.items():
                    actor.team = 0
                    hero = next(hero for hero in heroes().values() if hero.name == name)
                    self.assertTrue(match.switch_hero(actor, hero))
                    self.place(actor, (x, y, z + (14 if name == "Orisa" else 3)))
                for _ in range(45):
                    run.step({})
                sym = casters.get("Symmetra")
                if sym is not None:
                    script = sym.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                for tick in range(100):
                    commands = dict.fromkeys(casters.values(), (-32768, 0, 0))
                    if "Orisa" in casters and tick == 0:
                        commands[casters["Orisa"]] = (-32768, 0, 16)
                    if sym is not None and tick in (0, 16):
                        commands[sym] = (-32768, 0, 32 if tick == 0 else 1)
                    run.step(commands)
                deployed = {"Orisa": fight.orisa_barriers, "Symmetra": fight.symmetra_barriers}
                expected = {"Orisa": 600, "Symmetra": 4000}
                shields = {name: deployed[name].actors[actor.body] for name, actor in casters.items()}
                for name, shield in shields.items():
                    self.assertEqual(shield.health, expected[name])
                mine, *others = self.deploy(run, ball, armed=False)
                for other in others:
                    fight.mines.remove(other)
                primary = mine.body_script.primary()
                for _ in range(150):
                    run.step({})
                    if mine.triggered:
                        break
                self.assertTrue(mine.triggered)
                self.assertEqual(primary.vars[375].value(), ())
                centre = mine.centre()
                for name, shield in shields.items():
                    self.assertTrue(barriers.overlaps(deployed[name].geometry, shield.position, shield.yaw, 0,
                                                     centre, centre, ball_mines.HIT_RADIUS, 0, 0))
                    self.assertEqual(shield.health, expected[name] - 130)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(15):
                    run.step({})
                for name, shield in shields.items():
                    self.assertEqual(shield.health, expected[name] - 130)

    def test_raised_shields_trigger_mine_without_selecting_the_wielder(self):
        for name, dx, dz in (("Reinhardt", -2.3, .85), ("Brigitte", 0, .7)):
            with self.subTest(shield=name):
                if not os.environ.get(f"ORPHEUS_{name.upper()}_COLLISION"):
                    self.skipTest(f"104319 {name} collision input required")
                run, hog, ball, defender = self.scene()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team = defender.team = 0
                ball.team = 1
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(defender, hero))
                mine = self.isolate(run, ball)
                x, y, z = mine.position
                self.place(defender, (x, y, z + 10))
                raised = {defender: (-32768, 0, 2)}
                for _ in range(45):
                    run.step(raised)
                shield = fight.barriers[defender.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, shield.maximum)
                if name == "Brigitte":
                    # A thin slab hides the body but leaves the forward shield surface visible.
                    match.collision_world = World(
                        box(-50, 50, y - 1, y, -50, 50) + box(x - 4, x + 4, y, y + 3, z + .1555, z + .1655),
                    )
                self.place(defender, (x + dx, y, z + dz))
                run.step(raised)
                self.assertTrue(mine.triggered)
                self.assertEqual(mine.body_script.primary().vars[375].value(), ())
                self.assertEqual(shield.current, shield.maximum - 130)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0)

    def test_deflect_and_grasp_do_not_convert_a_landed_mine_blast(self):
        from ow174.game import defenses

        for name, button in (("Genji", 16), ("Sigma", 8)):
            with self.subTest(defense=name):
                run, hog, ball, support = self.scene()
                mine = self.isolate(run, ball)
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = support.team = 0
                ball.team = 1
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(run.match.switch_hero(support, hero))
                x, y, z = mine.position
                self.place(support, (x, y, z + 6))
                for _ in range(45):
                    run.step({})
                run.step({support: (-32768, 0, button)})
                for _ in range(5):
                    run.step({support: (-32768, 0, 0)})
                target = next(target for target in fight._targets() if target.owner is support)
                gate = defenses.projectile_gate((x, y + target.eye, z), (0, 0, 1), 6, (target,))
                self.assertIsNotNone(gate)
                support_pool = self.pool(run, support)
                before = support_pool.total
                self.place(hog, (x + .7, y, z))
                self.place(support, (x, y, z + .7))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(self.pool(run, hog).total, 470)
                self.assertEqual(support_pool.total, before - 130)
                self.assertEqual(self.pool(run, ball).total, 20000)
                if name == "Sigma":
                    grasp = next(i for i in support.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertFalse(grasp.vars[21312].value())
                    self.assertFalse(support_pool.temporary_shields)

    def test_invulnerable_reaper_does_not_trigger_the_mine_until_wraith_ends(self):
        from ow174.game import defenses

        run, hog, ball, reaper = self.scene()
        mine = self.isolate(run, ball)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = reaper.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Reaper")
        self.assertTrue(run.match.switch_hero(reaper, hero))
        x, y, z = mine.position
        self.place(reaper, (x + 6, y, z))
        for _ in range(45):
            run.step({})
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(defenses.protection(reaper)[0])
        self.place(reaper, (x + .55, y, z))
        reaper_pool = self.pool(run, reaper)
        before = reaper_pool.total
        for _ in range(235):
            if not defenses.protection(reaper)[0]:
                break
            self.assertFalse(mine.triggered)
            self.assertEqual(reaper_pool.total, before)
            run.step({reaper: (0, 0, 0)})
        self.assertFalse(defenses.protection(reaper)[0])
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(reaper_pool.total, before - 130)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(fight.impulses.velocity(reaper.body), (5, 5, 0))

    def test_enemy_emp_suspends_armed_mine_until_authored_hack_expires(self):
        from ow174.game import sombra

        run, hog, ball, caster = self.scene()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        ball.team = 1
        mine = self.isolate(run, ball)
        self.assertTrue(match.switch_hero(caster, heroes()[sombra.SOMBRA]))
        x, y, z = mine.position
        self.place(caster, (x + 6, y, z))
        for _ in range(45):
            run.step({})
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (0, 0, 32)})
        component = mine.body_script.component
        for _ in range(50):
            if component.entity_var(8912).value():
                break
            run.step({})
        self.assertLess(script.component.entity_var(ULT_CHARGE).value(), 5)
        self.assertTrue(component.entity_var(8912).value())
        scope = mine.body_script.emp_scope()
        self.assertTrue(scope.active)
        self.assertEqual(scope.instance.vars[215].value(), 10)
        self.assertEqual(mine.health, 50)
        self.assertFalse(mine.armed)
        self.place(hog, (x + .55, y, z))
        pool = self.pool(run, hog)
        disabled_at = run.frame
        for _ in range(650):
            if not component.entity_var(8912).value():
                break
            self.assertFalse(mine.triggered)
            self.assertEqual(pool.total, 600)
            run.step({})
        self.assertFalse(component.entity_var(8912).value())
        self.assertFalse(any(i.graph.index in (0xB1F, 0xB32) for i in component.instances.values()))
        self.assertAlmostEqual(run.frame - disabled_at, 10 / .016, delta=1)
        for _ in range(12):
            if mine.triggered:
                break
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(pool.total, 470)

    def test_emp_suspends_airborne_mines_through_landing_without_restart(self):
        from ow174.game import sombra

        run, hog, ball, caster = self.scene()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        ball.team = 1
        self.assertTrue(match.switch_hero(caster, heroes()[sombra.SOMBRA]))
        x, y, z = ball.mover.position
        self.place(caster, (x + 7, y, z))
        for _ in range(45):
            run.step({})
        self.place(ball, (x, y + 8, z))
        volley = self.deploy(run, ball, armed=False)
        field = match.combat.mines
        self.assertTrue(all(not mine.landed for mine in volley))

        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (0, 0, 32)})
        for _ in range(55):
            if all(mine.body_script.emp_scope() is not None for mine in volley):
                break
            run.step({})
        self.assertLess(script.component.entity_var(ULT_CHARGE).value(), 10)
        self.assertTrue(all(mine.body_script.emp_scope() is not None for mine in volley))
        self.assertTrue(all(not mine.landed for mine in volley))
        disabled_at = run.frame
        mine, *others = volley
        for other in others:
            field.remove(other)
        scope = mine.body_script.emp_scope()
        for _ in range(180):
            if mine.landed:
                break
            run.step({})
        self.assertTrue(mine.landed)
        self.assertIs(mine.body_script.emp_scope(), scope)
        self.assertFalse(mine.armed)
        self.assertEqual((mine.source, mine.team, mine.health), (ball.body, ball.team, 50))

        x, y, z = mine.position
        self.place(hog, (x + .55, y, z))
        pool = self.pool(run, hog)
        for _ in range(650):
            if mine.body_script.emp_scope() is None:
                break
            self.assertFalse(mine.triggered)
            self.assertEqual(pool.total, 600)
            run.step({})
        self.assertIsNone(mine.body_script.emp_scope())
        self.assertAlmostEqual(run.frame - disabled_at, 10 / .016, delta=1)
        for _ in range(12):
            if mine.triggered:
                break
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(pool.total, 470)

    def test_emp_ignores_friendly_out_of_range_and_ice_wall_occluded_mines(self):
        for case in ("friendly", "range", "ice wall"):
            with self.subTest(case=case):
                self.check_mine_emp_filter(case)

    def check_mine_emp_filter(self, case):
        from ow174.game import sombra

        run, hog, ball, caster = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = 0
        caster.team = int(case == "friendly")
        ball.team = 1
        mine = self.isolate(run, ball)
        self.assertTrue(match.switch_hero(caster, heroes()[sombra.SOMBRA]))
        x, y, z = mine.position
        self.place(caster, (x - 25 if case == "range" else x + 6, y, z))
        if case == "ice wall":
            mei = next(hero for hero in heroes().values() if hero.name == "Mei")
            self.assertTrue(match.switch_hero(hog, mei))
            self.place(hog, (x + 8, y, z + 1))
        for _ in range(45):
            run.step({})
        if case == "ice wall":
            look = aim(fight._eye(hog), (x + 3, y, z))
            for buttons in (16, 0, 1, 0):
                run.step({hog: (*look, buttons)})
            self.assertEqual(len(fight.walls.pieces), 5)
            self.assertTrue(fight.walls.blocked(fight._eye(caster), mine.centre()))

        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (0, 0, 32)})
        for _ in range(50):
            run.step({})
            self.assertTrue(mine.armed)
            self.assertIsNone(mine.body_script.emp_scope())
        self.assertLess(script.component.entity_var(ULT_CHARGE).value(), 5)
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)

    def test_deployed_ice_wall_blocks_mine_acquisition_until_mei_cancels_it(self):
        run, hog, ball, mei = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        ball.team = 1
        mine = self.isolate(run, ball)
        hero = next(hero for hero in heroes().values() if hero.name == "Mei")
        self.assertTrue(match.switch_hero(mei, hero))
        x, y, z = mine.position
        self.place(mei, (x + 8, y, z))
        for _ in range(45):
            run.step({})
        look = aim(fight._eye(mei), (x + .56, y, z))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*look, buttons)})
        self.assertEqual(len(fight.walls.pieces), 5)
        self.place(hog, (x + 1.7, y, z))
        self.assertTrue(fight.walls.blocked(mine.centre(), fight._eye(hog)))
        pool = self.pool(run, hog)
        for _ in range(8):
            run.step({})
            self.assertFalse(mine.triggered)
            self.assertEqual(pool.total, 600)
        run.step({mei: (*look, 16)})
        self.assertFalse(fight.walls.pieces)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(pool.total, 470)

    def side_wall_scene(self):
        run, hog, ball, _ = self.scene()
        x, y, z = ball.mover.position
        wall_z = z + 2
        run.match.collision_world = World(
            box(x - 50, x + 50, y - 1, y, z - 50, z + 50)
            + box(x - 8, x + 8, y - 1, y + 10, wall_z, wall_z + .25),
        )
        return run, hog, ball, wall_z

    def ice_wall_overlap_scene(self):
        run, hog, ball, mei = self.scene()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        ball.team = 1
        self.assertTrue(match.switch_hero(mei, heroes()[0x02E00000000000DD]))
        x, y, z = ball.mover.position
        self.place(mei, (x + 25, y, z))
        for _ in range(45):
            run.step({})
        return run, hog, ball, mei

    def place_wall_over_mine(self, run, mei, floor, point):
        fight = run.match.combat
        x, _, z = point
        self.place(mei, (x + 8, floor, z))
        look = aim(fight._eye(mei), (x, floor, z))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*look, buttons)})
        self.assertEqual(len(fight.walls.pieces), 5)
        return look

    def test_ice_wall_placed_over_armed_mine_hides_target_without_triggering_it(self):
        run, hog, ball, mei = self.ice_wall_overlap_scene()
        fight = run.match.combat
        mine = self.isolate(run, ball)
        x, y, z = mine.position
        search = mine.body_script.primary().states[28]
        armed_at = search.start
        look = self.place_wall_over_mine(run, mei, y, mine.position)
        inside = fight.walls.cast(mine.centre(), (1, 0, 0), 1)
        self.assertIsNotNone(inside)
        self.assertEqual((inside[0], inside[2]), (0, (0, 0, 0)))
        self.assertFalse(mine.triggered)
        self.assertEqual(inside[1].health, 400)

        self.place(hog, (x + 1.7, y, z))
        pool = self.pool(run, hog)
        for _ in range(8):
            run.step({})
            self.assertTrue(mine.armed)
            self.assertFalse(mine.triggered)
            self.assertEqual(pool.total, 600)
        self.assertEqual(mine.position, (x, y, z))
        self.assertEqual(search.start, armed_at)
        self.assertEqual(mine.health, 50)

        run.step({mei: (*look, 16)})
        self.assertFalse(fight.walls.pieces)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(pool.total, 470)
        for _ in range(85):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(pool.total, 470)

    def test_ice_wall_placed_around_airborne_mine_does_not_manufacture_a_contact(self):
        run, hog, ball, mei = self.ice_wall_overlap_scene()
        fight = run.match.combat
        floor = ball.mover.position[1]
        mines = self.deploy(run, ball, armed=False)
        mine = None
        for _ in range(130):
            mine = next((m for m in mines if not m.landed and m.velocity[1] < 0
                         and floor + 2.1 < m.position[1] < floor + 3.5), None)
            if mine is not None:
                break
            run.step({})
        self.assertIsNotNone(mine)
        for other in mines:
            if other is not mine:
                fight.mines.remove(other)
        point, velocity = mine.position, mine.velocity
        for _ in range(4):
            velocity, _, point = ball_mines.flight_step(point, velocity, mine.gravity, ball_mines.TICK)
        look = self.place_wall_over_mine(run, mei, floor, point)

        self.assertFalse(mine.landed or mine.armed or mine.triggered)
        self.assertIsNone(mine.wall_support)
        speed = math.hypot(*mine.velocity)
        direction = tuple(value / speed for value in mine.velocity)
        inside = fight.walls.cast(mine.position, direction, 0.5)
        self.assertIsNotNone(inside)
        self.assertEqual((inside[0], inside[2]), (0, (0, 0, 0)))
        velocity, _, endpoint = ball_mines.flight_step(
            mine.position, mine.velocity, mine.gravity, ball_mines.TICK,
        )
        run.step({})
        self.assertEqual(mine.position, endpoint)
        self.assertEqual(mine.velocity, velocity)
        self.assertFalse(mine.landed or mine.armed or mine.triggered)
        self.assertIsNone(mine.wall_support)
        self.assertEqual(inside[1].health, 400)

        run.step({mei: (*look, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(150):
            if mine.armed:
                break
            run.step({})
        self.assertIs(fight.mines.entities[mine.entity], mine)
        self.assertTrue(mine.landed and mine.armed)
        self.assertIsNone(mine.wall_support)
        self.assertEqual(mine.normal, (0, 1, 0))
        self.assertEqual(mine.health, 50)
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)

    def test_side_wall_contact_keeps_real_mine_airborne_until_floor(self):
        from unittest.mock import patch

        run, hog, ball, wall_z = self.side_wall_scene()
        fight = run.match.combat
        y = ball.mover.position[1]
        self.cast(run, ball)
        contact = []
        slide = ball_mines.slide_wall

        def observe(velocity, hit):
            result = slide(velocity, hit)
            contact.append((hit, result))
            return result

        with patch.object(ball_mines, "slide_wall", side_effect=observe):
            for _ in range(120):
                run.step({})
                if contact:
                    break
        self.assertTrue(contact)
        hit, (position, velocity) = contact[0]
        self.assertAlmostEqual(hit.point[2], wall_z, places=5)
        self.assertAlmostEqual(velocity[2], 0)
        self.assertLess(position[2], wall_z)
        mine = next(m for m in fight.mines.entities.values() if m.position == position)
        self.assertFalse(mine.landed or mine.armed)
        for _ in range(180):
            if mine.armed:
                break
            run.step({})
        self.assertTrue(mine.armed)
        self.assertEqual(mine.normal, (0, 1, 0))
        self.assertAlmostEqual(mine.position[1], y)
        self.assertEqual(mine.velocity, (0, 0, 0))
        self.assertEqual(mine.health, 50)
        for other in tuple(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)

    def deployed_wall_scene(self, distance):
        run, hog, ball, mei = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Mei")
        self.assertTrue(match.switch_hero(mei, hero))
        x, y, z = ball.mover.position
        self.place(mei, (x, y, z + 10))
        for _ in range(45):
            run.step({})
        look = aim(fight._eye(mei), (x, y, z + distance))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*look, buttons)})
        self.assertEqual(len(fight.walls.pieces), 5)
        self.place(mei, (x + 30, y, z))
        return run, hog, ball, mei, look

    def test_deployed_ice_wall_side_slides_mine_until_ground_contact(self):
        from unittest.mock import patch

        run, hog, ball, _, _ = self.deployed_wall_scene(2)
        fight = run.match.combat
        self.cast(run, ball)
        with patch.object(ball_mines, "slide_wall", wraps=ball_mines.slide_wall) as slide:
            for _ in range(100):
                run.step({})
                if slide.called:
                    break
        self.assertTrue(slide.called)
        velocity, hit = slide.call_args.args
        position, _ = ball_mines.slide_wall(velocity, hit)
        mine = next(m for m in fight.mines.entities.values() if m.position == position)
        self.assertFalse(mine.landed or mine.armed)
        self.assertIsNone(mine.wall_support)
        for _ in range(180):
            if mine.armed:
                break
            run.step({})
        self.assertTrue(mine.armed)
        self.assertAlmostEqual(mine.position[1], ball.mover.position[1])
        self.assertEqual(mine.normal, (0, 1, 0))
        for other in tuple(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)

    def test_mine_falls_from_cancelled_ice_wall_without_resetting_arming(self):
        run, hog, ball, mei, look = self.deployed_wall_scene(4)
        fight = run.match.combat
        x, y, z = ball.mover.position
        self.place(ball, (x, y + 8, z))
        mines = self.deploy(run, ball)
        perched = [mine for mine in mines if mine.wall_support is not None and mine.normal[1] > .95]
        self.assertTrue(perched)
        mine = perched[0]
        pillar = fight.walls.pieces[mine.wall_support]
        height = pillar.feet[1] + pillar.height
        self.assertAlmostEqual(mine.position[1], height)
        search = mine.body_script.primary().states[28]
        armed_at = search.start
        for other in mines:
            if other is not mine:
                fight.mines.remove(other)

        run.step({mei: (*look, 16)})
        self.assertFalse(fight.walls.pieces)
        first_update = len(hog.client.entities)
        run.step({})
        self.assertFalse(mine.landed or mine.armed)
        self.assertLess(mine.position[1], height)
        self.assertLess(mine.velocity[1], 0)
        drop, = [update.movement for update in hog.client.entities[first_update:]
                 if update.entity == mine.entity and update.op == world.OP_UPDATE]
        self.assertEqual((drop.position, drop.velocity), (mine.position, mine.velocity))
        self.assertIn(mine.entity, [update.entity for update in fight.mines.movements(hog)])
        first_update = len(hog.client.entities)
        for _ in range(90):
            if mine.landed:
                break
            run.step({})
        self.assertTrue(mine.armed)
        self.assertIsNone(mine.wall_support)
        self.assertAlmostEqual(mine.position[1], y)
        self.assertEqual(mine.velocity, (0, 0, 0))
        landing, = [update.movement for update in hog.client.entities[first_update:]
                    if update.entity == mine.entity and update.op == world.OP_UPDATE]
        self.assertEqual((landing.position, landing.velocity), (mine.position, mine.velocity))
        self.assertNotIn(mine.entity, [update.entity for update in fight.mines.movements(hog)])
        self.assertEqual(mine.body_script.primary().states[28].start, armed_at)
        self.assertEqual((mine.source, mine.team, mine.health), (ball.body, ball.team, 50))
        self.place(hog, mine.position)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(self.pool(run, hog).total, 470)

    def test_world_tick_gap_preserves_wall_slide_and_arming(self):
        from unittest.mock import patch

        samples = []
        for gap in (False, True):
            run, _, ball, _ = self.side_wall_scene()
            field = run.match.combat.mines
            self.cast(run, ball)
            for _ in range(30):
                if len(field.entities) == 15:
                    break
                run.step({})
            self.assertEqual(len(field.entities), 15)
            self.assertTrue(all(not mine.landed for mine in field.entities.values()))
            with patch.object(ball_mines, "slide_wall", wraps=ball_mines.slide_wall) as slide:
                if gap:
                    run.frame += 59
                    run.step({})
                else:
                    for _ in range(60):
                        run.step({})
                for _ in range(120):
                    run.step({})
            self.assertTrue(slide.called)
            self.assertTrue(all(mine.armed for mine in field.entities.values()))
            samples.append((slide.call_count, [
                (mine.entity, mine.position, mine.normal, mine.travelled,
                 mine.body_script.primary().states[28].start, mine.last_tick)
                for mine in field.entities.values()
            ]))
        self.assertEqual(samples[0], samples[1])

    def test_real_practice_range_and_kings_row_geometry_settles_mines_and_hits_roadhog(self):
        root = os.environ.get("ORPHEUS_TEST_MINE_COLLISION")
        if not root:
            self.skipTest("explicit build 104319 map collision inputs required")
        from unittest.mock import patch

        samples = (
            ("0688", (54.56507, 1, 42.14788), False),
            ("00D4", (-85.048645, .99401355, -6.1768403), True),
        )
        for code, anchor, ceiling in samples:
            with self.subTest(map=code):
                run, hog, ball, other = self.scene()
                collision = World.load(Path(root) / f"{code}.col")
                run.match.collision_world = collision
                self.place(ball, anchor)
                for actor, shift in ((hog, 80), (other, 100)):
                    x, y, z = actor.mover.position
                    self.place(actor, (x + shift, y, z))
                with patch.object(ball_mines, "slide_wall", wraps=ball_mines.slide_wall) as slide:
                    mines = self.deploy(run, ball)
                self.assertTrue(slide.called)
                self.assertTrue(all(abs(mine.normal[1]) >= ball_mines.WALL_NORMAL_Y for mine in mines))
                self.assertEqual(any(mine.normal[1] < -.95 for mine in mines), ceiling)
                for mine in mines:
                    self.assertEqual(mine.velocity, (0, 0, 0))
                    # Sample close to the contact to avoid a neighboring
                    # surface at tight floor, wall or ceiling junctions.
                    start = tuple(p + .002 * n for p, n in zip(mine.position, mine.normal, strict=True))
                    hit = collision.cast_ray(start, tuple(-n for n in mine.normal), .004)
                    self.assertIsNotNone(hit)
                    self.assertAlmostEqual(hit.distance, .002, delta=.0002)
                mine = next(mine for mine in mines if mine.normal[1] > .95)
                for other in mines:
                    if other is not mine:
                        run.match.combat.mines.remove(other)
                self.place(hog, mine.position)
                run.step({})
                self.assertEqual([m for m in mines if m.triggered], [mine])
                self.assertEqual(self.pool(run, hog).total, 470)
