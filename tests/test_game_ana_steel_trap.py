"""Actual Junkrat and Ana commands against the reconstructed Steel Trap."""
import gzip
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_game_ana as fixture
import test_game_ana_baptiste_field as field_fixture
import test_game_ana_projected_barrier as barrier_fixture
from test_game_combat import aim

from ow174.game.commands import Command, FIRE, JUMP
from ow174.game.content import find_hero
from ow174.game.script import expr, graph


class AnaSteelTrapTests(unittest.TestCase):
    @staticmethod
    def place_near(ana, trap, offset):
        x, _, z = trap.position
        field_fixture.AnaBaptisteFieldTests.place(ana, (x, ana.mover.position[1], z - offset))

    def arena(self, *, enemy=True):
        run, ana, junkrat, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(junkrat, find_hero("Junkrat")))
        x, y, z = ana.mover.position
        junkrat.mover.position[:] = x + 4, y, z + 5
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        return run, ana, junkrat, vitals

    def throw(self, run, junkrat):
        traps = run.match.combat.steel_traps
        run.step({junkrat: (0, 0, 16)})
        trap = None
        for _ in range(90):
            run.step({})
            trap = traps.devices.get(junkrat.body)
            if trap is not None and trap.armed_at is not None:
                break
        self.assertIsNotNone(trap)
        self.assertIsNotNone(trap.armed_at)
        self.assertEqual(trap.health, 100)
        self.assertEqual(trap.instance.find_var(*trap.output).value(), expr.Entity(trap.body))
        return trap

    def capture(self, run, ana, trap):
        self.place_near(ana, trap, 0.7)
        while run.frame < trap.armed_at:
            run.step({})
        self.assertEqual(trap.victim, ana.body)

    def test_repeated_world_tick_does_not_advance_the_trap_twice(self):
        run, _, junkrat, _ = self.arena()
        fight = run.match.combat
        run.step({junkrat: (0, 0, 16)})
        trap = fight.steel_traps.devices[junkrat.body]
        run.step({})
        self.assertIsNone(trap.armed_at)
        self.assertLess(trap.remaining, 5)
        flight = trap.position, trap.velocity, trap.remaining
        run.match.update(fight.now, fight.tick, frames=False)
        self.assertEqual((trap.position, trap.velocity, trap.remaining), flight)
        run.step({})
        self.assertLess(trap.remaining, flight[2])

    def test_actual_trap_arms_damages_and_blocks_movement_until_expiry(self):
        run, ana, junkrat, vitals = self.arena()
        fight = run.match.combat
        trap = self.throw(run, junkrat)
        self.place_near(ana, trap, 0.7)
        run.step({})
        self.assertIsNone(trap.victim)
        self.assertEqual(vitals.total, 200)
        self.capture(run, ana, trap)
        self.assertEqual(vitals.total, 120)
        self.assertEqual(trap.release_tick - run.frame, 188)
        position = tuple(ana.mover.position)
        for _ in range(6):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            ana.take_commands([Command(run.frame, forward=127, buttons=JUMP)])
        self.assertEqual(tuple(ana.mover.position), position)
        while run.frame < trap.release_tick:
            run.step({})
        self.assertFalse(fight.steel_traps.rooted(ana.body))
        self.assertNotIn(junkrat.body, fight.steel_traps.devices)
        self.assertEqual(trap.instance.find_var(*trap.output).value(), expr.Entity(0))

    def test_ana_attacks_damage_hostile_trap_and_leave_friendly_trap_intact(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5),
                                       (16, False, 50), (0x800, False, 30)):
            for enemy in (True, False):
                with self.subTest(button=button, scoped=scoped, enemy=enemy):
                    run, ana, junkrat, _ = self.arena(enemy=enemy)
                    trap = self.throw(run, junkrat)
                    if button == 0x800:
                        self.place_near(ana, trap, 1.35)
                    field_fixture.AnaBaptisteFieldTests().fire(run, ana, trap.centre(), button, scoped=scoped)
                    self.assertEqual(trap.health, 100 - amount if enemy else 100)

    def test_captured_ana_can_break_trap_with_actual_rifle(self):
        run, ana, junkrat, vitals = self.arena()
        fight = run.match.combat
        trap = self.throw(run, junkrat)
        self.capture(run, ana, trap)
        direction = aim(fight._eye(ana), trap.centre())
        for expected in (30, 0):
            run.step({ana: (*direction, FIRE)})
            for _ in range(62):
                run.step({ana: (*direction, 0)})
            self.assertEqual(trap.health, expected)
        self.assertLess(run.frame, trap.release_tick)
        self.assertFalse(fight.steel_traps.rooted(ana.body))
        self.assertEqual(vitals.total, 120)

    def test_trap_rejects_friendly_and_occluded_targets(self):
        for enemy in (False, True):
            with self.subTest(enemy=enemy):
                run, ana, junkrat, vitals = self.arena(enemy=enemy)
                trap = self.throw(run, junkrat)
                self.place_near(ana, trap, 1)
                world = run.match.collision_world
                if enemy:
                    fixture.set_static_wall(run, ana, 0.5)
                while run.frame < trap.armed_at + 3:
                    run.step({})
                self.assertIsNone(trap.victim)
                self.assertEqual(vitals.total, 200)
                if enemy:
                    run.match.collision_world = world
                    run.step({})
                    self.assertEqual(trap.victim, ana.body)
                    self.assertEqual(vitals.total, 120)

    def test_actual_projected_barrier_consumes_trap_contact(self):
        helper = barrier_fixture.AnaProjectedBarrierTests()
        run, ana, zarya, junkrat = helper.fight(ally_ana=True)
        self.assertTrue(run.match.switch_hero(junkrat, find_hero("Junkrat")))
        junkrat.team = 0
        x, y, z = ana.mover.position
        field_fixture.AnaBaptisteFieldTests.place(junkrat, (x + 4, y, z + 5))
        for _ in range(25):
            run.step({})
        trap = self.throw(run, junkrat)
        self.place_near(ana, trap, 0.7)
        field_fixture.AnaBaptisteFieldTests.place(zarya, (trap.position[0] + 3, y, trap.position[2] - 0.7))
        shield = helper.cast(run, zarya, ana)
        while run.frame < trap.armed_at:
            run.step({})
        fight = run.match.combat
        self.assertEqual(shield.health, 120)
        self.assertEqual(fight.vitals_of(helper.entry(run, ana)).total, 200)
        self.assertFalse(fight.steel_traps.rooted(ana.body))
        self.assertNotIn(junkrat.body, fight.steel_traps.devices)

    def test_trap_health_and_arming_tick_enter_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        junkrat = players[1]
        peers[1].message(21616, stored.pick(junkrat.entity, find_hero("Junkrat").guid), now=100.8)
        peers[1].commands(junkrat.body_script.frame + 1, 40, 16)
        fight = junkrat.match.combat
        trap = fight.steel_traps.devices[junkrat.body]
        fight.update(fight.now + 50 * 0.016, fight.tick + 50, frames=False)
        self.assertIsNotNone(trap.armed_at)
        before = stored.snapshot_digest(case.capture(session))
        trap.health -= 5
        after = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, after)
        trap.armed_at += 1
        self.assertNotEqual(after, stored.snapshot_digest(case.capture(session)))

    def test_owner_death_leaves_trap_but_victim_replacement_clears_it(self):
        run, ana, junkrat, _ = self.arena()
        trap = self.throw(run, junkrat)
        self.capture(run, ana, trap)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is junkrat)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 1)
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, FIRE)})
        for _ in range(65):
            run.step({})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, ana.body)
        self.assertTrue(fight.steel_traps.rooted(ana.body))
        self.assertIs(fight.steel_traps.devices[junkrat.body], trap)
        self.assertTrue(run.match.switch_hero(ana, ana.hero))
        self.assertNotIn(junkrat.body, fight.steel_traps.devices)
        self.assertFalse(fight.steel_traps.rooted(ana.body))

    @unittest.skipUnless(os.getenv("ORPHEUS_STEEL_TRAP_SUPPLEMENT"), "requires extracted 09BC")
    def test_owner_replacement_retires_trap_and_authored_victim_status(self):
        path = Path(os.environ["ORPHEUS_STEEL_TRAP_SUPPLEMENT"])
        record, = json.loads(gzip.decompress(path.read_bytes()))["graphs"]
        status = graph.Graph(record)
        lookup = graph.graph
        with patch.object(graph, "graph", side_effect=lambda index: status if index == 0x09BC else lookup(index)):
            run, ana, junkrat, _ = self.arena()
            trap = self.throw(run, junkrat)
            self.capture(run, ana, trap)
            flag = ana.body_script.component.entity_var(23154)
            self.assertIs(flag.value(), True)
            old = junkrat.body
            self.assertTrue(run.match.switch_hero(junkrat, junkrat.hero))
            self.assertNotIn(old, run.match.combat.steel_traps.devices)
            self.assertFalse(run.match.combat.steel_traps.rooted(ana.body))
            self.assertIsNone(flag.value())
