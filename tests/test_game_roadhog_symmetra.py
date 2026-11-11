"""Actual Photon Barrier preview and confirmation with Roadhog combat."""

import math
import os
import unittest
from dataclasses import replace

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import npc, symmetra_barrier
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


HOOK_LAYOUTS = ((4.25, True, False), (0, False, False), (4.25, True, True))


@unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"), "Photon Barrier model input required")
class RoadhogSymmetraTests(unittest.TestCase):
    def session(self):
        run, hog, sym, rear = phases.RoadhogDefenseTests().session("Symmetra", distance=6)
        rear.mover.position[0] = 30
        rear.mover.body = None
        for _ in range(40):
            self.step(run, hog, sym)
        return run, hog, sym

    def step(self, run, hog, sym, *, fire=0, cast=0):
        x, y, z = sym.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 0.8, z))
        run.step({hog: (*angles, fire), sym: (-32768, 0, cast)})

    def charge(self, sym):
        script = sym.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        return cost

    def preview(self, run, hog, sym):
        self.step(run, hog, sym, cast=32)
        for _ in range(15):
            self.step(run, hog, sym)

    def cast(self, run, hog, sym, *, rotate=False):
        fight = run.match.combat
        cost = self.charge(sym)
        self.preview(run, hog, sym)
        self.assertEqual(sym.body_script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        if rotate:
            self.preview(run, hog, sym)
        self.step(run, hog, sym, cast=1)
        for _ in range(35):
            self.step(run, hog, sym)
            actor = fight.symmetra_barriers.actors.get(sym.body)
            if actor is not None:
                self.assertEqual(sym.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(actor.health, 4000)
                self.assertEqual(actor.expires - run.frame, 750)
                self.assertTrue(any(i.graph.index == 0x156C for i in actor.component.instances.values()))
                return actor
        self.fail("Photon Barrier did not deploy")

    def test_confirmed_photon_wall_cuts_only_crossing_live_hook_without_spending_health(self):
        for caster_depth, crosses, allied in HOOK_LAYOUTS:
            with self.subTest(crosses=crosses, allied=allied):
                self.check_confirmed_photon_wall_live_hook(caster_depth, crosses, allied)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_confirmed_photon_wall_cuts_crossing_live_hook_on_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for caster_depth, crosses, allied in HOOK_LAYOUTS:
                with self.subTest(npc=definition.name, crosses=crosses, allied=allied):
                    self.check_confirmed_photon_wall_live_hook(
                        caster_depth, crosses, allied, definition=definition, client=actual.NpcClient,
                    )

    def check_confirmed_photon_wall_live_hook(self, caster_depth, crosses, allied, *, definition=None, client=None):
        run, hog, caster = self.session()
        fight = run.match.combat
        service = fight.symmetra_barriers
        cuts = crosses and not allied
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear = run.players[2]
        hog.team, caster.team, rear.team = 0, int(not allied), 1
        y = hog.mover.position[1]
        caster.mover.position[:] = (2.4, y, caster_depth)
        caster.mover.body = None
        if definition is None:
            victim = rear
            victim.mover.position[:] = (0, y, 17)
            victim.mover.body = None
        else:
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(run.match, definition, (0, y, 17), team=1)
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        entity = target.entity
        pool = fight.vitals_of(target)

        def depth():
            return (victim.mover.position if definition is None else victim.position)[2]

        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        look = aim(fight._eye(hog), centre)
        run.step({hog: (*look, 8)})
        for _ in range(55):
            run.step({hog: (*look, 0)})
            if fight.hooks.controlled(entity):
                break
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertEqual(pool.total, before - 30)
        cost = self.charge(caster)
        run.step({caster: (-32768, 0, 32)})
        preview = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x156B)
        placement = preview.state(preview.graph.node(57).state)
        before_preview = depth()
        for _ in range(8):
            run.step({caster: (-32768, 0, 0)})
            self.assertTrue(fight.hooks.controlled(entity))
            self.assertFalse(service.actors)
        self.assertTrue(placement.active)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertLess(depth(), before_preview)
        position = depth()

        run.step({caster: (-32768, 0, 1)})
        for _ in range(35):
            if caster.body in service.actors:
                break
            self.assertTrue(fight.hooks.controlled(entity))
            run.step({caster: (-32768, 0, 0)})
        actor = service.actors[caster.body]
        run.step({})
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(fight.hooks.controlled(entity), not cuts)
        current = next(t for t in fight._targets() if t.owner is victim)
        origin = fight._eye(hog)
        x, y, z = current.feet
        delta = (x - origin[0], y + current.height / 2 - origin[1], z - origin[2])
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta)
        reach = length - current.radius
        self.assertEqual(service.ray(actor, origin, direction, reach) is not None, crosses)
        contacts = fight._barrier_contacts(hog, origin, direction, reach)
        self.assertEqual(any(hit[1] is actor for hit in contacts), cuts)
        if cuts:
            position = depth()
        for _ in range(6):
            run.step({})
        if cuts:
            self.assertEqual(depth(), position)
        else:
            self.assertLess(depth(), position)
        if definition is not None and cuts:
            self.assertEqual(victim.velocity, (0, 0, 0))
        self.assertEqual(actor.health, 4000)
        self.assertEqual(pool.total, before - 30)

    def test_preview_cancel_and_uncharged_refusal(self):
        run, hog, sym = self.session()
        fight = run.match.combat
        self.preview(run, hog, sym)
        self.step(run, hog, sym, cast=1)
        for _ in range(25):
            self.step(run, hog, sym)
        self.assertFalse(fight.symmetra_barriers.actors)
        cost = self.charge(sym)
        self.preview(run, hog, sym)
        self.step(run, hog, sym, cast=2)
        for _ in range(30):
            self.step(run, hog, sym)
        self.assertFalse(fight.symmetra_barriers.actors)
        self.assertEqual(sym.body_script.component.entity_var(ULT_CHARGE).value(), cost)

    def test_actual_primary_and_hook_spend_wall_pool_then_expiry_exposes_body(self):
        run, hog, sym = self.session()
        fight = run.match.combat
        wall = self.cast(run, hog, sym)
        self.step(run, hog, sym, fire=1)
        for _ in range(30):
            self.step(run, hog, sym)
        self.assertLess(wall.health, 4000)
        health = phases.RoadhogDefenseTests().health
        self.assertEqual(health(run, sym), 20000)
        before = wall.health
        self.step(run, hog, sym, fire=8)
        for _ in range(30):
            self.step(run, hog, sym)
        self.assertAlmostEqual(wall.health, before - 30)
        self.assertEqual(health(run, sym), 20000)
        self.assertFalse(fight.hooks.controlled(sym.body))
        while run.frame < wall.expires - 1:
            self.step(run, hog, sym)
        self.assertIs(fight.symmetra_barriers.actors.get(sym.body), wall)
        self.step(run, hog, sym)
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        self.step(run, hog, sym, fire=2)
        for _ in range(25):
            self.step(run, hog, sym)
        self.assertLess(health(run, sym), 20000)

    def test_second_q_rotates_deployed_plane(self):
        run, hog, sym = self.session()
        wall = self.cast(run, hog, sym, rotate=True)
        self.assertEqual(wall.yaw, -16384)
        self.assertEqual(sym.hero.guid, symmetra_barrier.HERO)
        self.assertEqual(len(run.match.combat.symmetra_barriers.geometry.triangles), 2)


    def test_whole_hog_breaks_wall_and_later_pellets_reach_body(self):
        run, hog, sym = self.session()
        fight = run.match.combat
        wall = self.cast(run, hog, sym)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, sym, fire=32)
        for _ in range(425):
            self.step(run, hog, sym)
        self.assertEqual(wall.health, 0)
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        self.assertLess(run.frame, wall.expires)
        self.assertLess(phases.RoadhogDefenseTests().health(run, sym), 20000)

    def test_allied_wall_allows_friendly_shots(self):
        run, hog, sym = self.session()
        rear = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = sym.team = 0
        rear.team = 1
        rear.mover.position[:] = (0, hog.mover.position[1], 8)
        rear.mover.body = None
        wall = self.cast(run, hog, sym)
        self.step(run, hog, sym, fire=2)
        for _ in range(30):
            self.step(run, hog, sym)
        self.assertEqual(wall.health, 4000)
        self.assertLess(phases.RoadhogDefenseTests().health(run, rear), 20000)

    def test_child_vm_can_be_exported_without_evaluation(self):
        from unittest.mock import patch

        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
        from ow174.game.script import runtime

        run, hog, sym = self.session()
        wall = self.cast(run, hog, sym)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("evaluation during export")):
            before = snapshot_digest(export_component_vm(wall.component))
            self.assertEqual(snapshot_digest(export_component_vm(wall.component)), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_photon_wall_intercepts_both_directions_with_actual_npc_bodies(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                for reverse in (False, True):
                    if reverse and definition.name == "Assassin":
                        continue
                    with self.subTest(npc=definition.name, reverse=reverse):
                        run, hog, sym = self.session()
                        fight = run.match.combat
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        hog.team, sym.team = 0, 0 if reverse else 1
                        sym.mover.position[0] = 2.4
                        sym.mover.body = None
                        for player in run.players:
                            player.client = actual.NpcClient(player)
                        wall = self.cast(run, hog, sym)
                        y = hog.mover.position[1]
                        bot = npc.spawn(run.match, definition, (0, y, 12), team=1)
                        for _ in range(15):
                            run.step({})
                        target = next(t for t in fight._targets() if t.owner is bot)
                        before = fight.vitals_of(target).total
                        if reverse:
                            a, b = target.segment()
                            centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
                            hog.mover.yaw, hog.mover.pitch = aim(fight._eye(hog), centre)
                            self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                        else:
                            angles = aim(fight._eye(hog), (0, y+1, 12))
                            run.step({hog: (*angles, 1)})
                        for _ in range(280 if reverse else 40):
                            run.step({})
                            if wall.health < 4000:
                                break
                        if reverse:
                            npc_control.stop(run.match)
                        self.assertLess(wall.health, 4000)
                        self.assertEqual(fight.vitals_of(target).total, before)
                        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()
