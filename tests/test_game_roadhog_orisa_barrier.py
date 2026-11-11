import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import combat, npc
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.script import expr, nodes, runtime


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "104319 Orisa collision input required")
class RoadhogOrisaBarrierTests(unittest.TestCase):
    step = phases.RoadhogDefenseTests.step
    health = phases.RoadhogDefenseTests.health

    def session(self, distance=8):
        run, hog, caster, rear = phases.RoadhogDefenseTests().session("Orisa", distance=distance)
        rear.mover.position[0] = 20
        rear.mover.body = None
        for _ in range(30):
            self.step(run, hog, caster)
        return run, hog, caster

    def deploy(self, run, hog, caster):
        service = run.match.combat.orisa_barriers
        run.step({caster: (-32768, 8192, 16)})
        flight = None
        for _ in range(35):
            run.step({caster: (-32768, 8192, 0)})
            if caster.body in service.flights:
                flight = service.flights[caster.body]
            if caster.body in service.actors:
                break
        self.assertIsNotNone(flight)
        actor = service.actors[caster.body]
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        self.assertLess(actor.position[2], caster.mover.position[2])
        self.assertEqual(actor.health, 600)
        return actor

    def test_landing_mesh_cuts_only_crossing_live_hook_without_spending_health(self):
        for lateral, cuts in ((1.8, True), (10, False)):
            with self.subTest(cuts=cuts):
                self.check_landing_mesh_live_hook(lateral, cuts)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_landing_mesh_cuts_crossing_live_hook_on_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for lateral, cuts in ((1.8, True), (10, False)):
                with self.subTest(npc=definition.name, cuts=cuts):
                    self.check_landing_mesh_live_hook(lateral, cuts, definition=definition, client=actual.NpcClient)

    def check_landing_mesh_live_hook(self, lateral, cuts, *, definition=None, client=None):
        run, hog, caster = self.session()
        fight = run.match.combat
        service = fight.orisa_barriers
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear = run.players[2]
        hog.team, caster.team, rear.team = 0, 1, 1
        y = hog.mover.position[1]
        caster.mover.position[:] = (lateral, y, 8)
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
        # Start the throw early enough for it to still be airborne when Hook latches.
        for frame in range(1, 56):
            run.step({hog: (*look, 0), caster: (-32768, 8192, 16 if frame == 15 else 0)})
            if fight.hooks.controlled(entity):
                break
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertEqual(pool.total, before - 30)
        self.assertIn(caster.body, service.flights)
        self.assertFalse(service.actors)
        position = depth()
        for _ in range(25):
            run.step({caster: (-32768, 8192, 0)})
            if caster.body in service.actors:
                break
            self.assertTrue(fight.hooks.controlled(entity))
        actor = service.actors[caster.body]
        run.step({})
        self.assertTrue(actor.active())
        self.assertEqual(fight.hooks.controlled(entity), not cuts)
        current = next(t for t in fight._targets() if t.owner is victim)
        origin = fight._eye(hog)
        x, y, z = current.feet
        delta = (x - origin[0], y + current.height / 2 - origin[1], z - origin[2])
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta)
        contacts = fight._barrier_contacts(hog, origin, direction, length - current.radius)
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
        self.assertEqual(actor.health, 600)
        self.assertEqual(pool.total, before - 30)

    def test_actual_primary_slug_fragments_and_hook_intercept_landed_mesh(self):
        for button, distance in ((1, 8), (2, 8), (2, 16), (8, 8)):
            with self.subTest(button=button, distance=distance):
                run, hog, caster = self.session(distance)
                actor = self.deploy(run, hog, caster)
                self.step(run, hog, caster, fire=button)
                for _ in range(45):
                    self.step(run, hog, caster)
                self.assertLess(actor.health, 600)
                self.assertEqual(self.health(run, caster), 20000)
                self.assertFalse(run.match.combat.hooks.controlled(caster.body))
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_actor_expires_via_original_chase_and_tail(self):
        run, hog, caster = self.session()
        actor = self.deploy(run, hog, caster)
        self.assertEqual(expr.to_float(actor.component.entity_var(567).value()), 20)
        start = run.frame
        fight = run.match.combat
        fight.update((start+1200)*combat.TICK, start+1200, frames=False)
        self.assertTrue(actor.active())
        fight.update((start+1300)*combat.TICK, start+1300, frames=False)
        self.assertNotIn(caster.body, fight.orisa_barriers.actors)

    def test_gap_crossing_landing_and_expiry_leaves_no_stale_mesh(self):
        run, _, caster = self.session()
        fight = run.match.combat
        service = fight.orisa_barriers
        run.step({caster: (-32768, 8192, 16)})
        for _ in range(20):
            if service.flights:
                break
            run.step({caster: (-32768, 8192, 0)})
        self.assertIn(caster.body, service.flights)
        self.assertFalse(service.actors)
        tick = run.frame+1400
        fight.update(tick*combat.TICK, tick, frames=False)
        self.assertFalse(service.flights)
        self.assertFalse(service.actors)

    def test_wall_bounces_actual_throw_before_ground_deployment(self):
        from test_game_mover import FLOOR, wall

        run, hog, caster = self.session()
        run.match.collision_world = World(FLOOR + wall(-10, 7.5, 10, 7.5))
        run.step({caster: (-32768, 8192, 16)})
        service = run.match.combat.orisa_barriers
        saw_bounce = False
        for _ in range(35):
            run.step({caster: (-32768, 8192, 0)})
            flight = service.flights.get(caster.body)
            if flight is not None and flight.velocity[2] > 0:
                saw_bounce = True
            if caster.body in service.actors:
                break
        self.assertTrue(saw_bounce)
        self.assertGreater(service.actors[caster.body].position[2], 7.5)
        self.assertEqual(self.health(run, hog), 600)

    def test_death_cancels_windup_but_landed_actor_keeps_its_timer(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                run, hog, caster = self.session()
                fight = run.match.combat
                if landed:
                    actor = self.deploy(run, hog, caster)
                else:
                    run.step({caster: (-32768, 8192, 16)})
                target = next(t for t in fight._targets() if t.owner is caster)
                fight._damage(hog, target, 50000, False, None, run.frame)
                for _ in range(30):
                    run.step({})
                self.assertFalse(fight.orisa_barriers.flights)
                if landed:
                    self.assertIs(fight.orisa_barriers.actors[caster.body], actor)
                    self.assertTrue(actor.active())
                else:
                    self.assertFalse(fight.orisa_barriers.actors)

    def test_cooldown_refuses_second_throw_then_new_landing_replaces_old_mesh(self):
        run, hog, caster = self.session()
        actor = self.deploy(run, hog, caster)
        service = run.match.combat.orisa_barriers
        source = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0xBD7)
        ability = source.states[3]
        self.assertIsInstance(ability, nodes.Ability)
        self.assertGreater(ability.remaining(), 0)
        run.step({caster: (-32768, 8192, 16)})
        for _ in range(25):
            run.step({})
        self.assertFalse(service.flights)
        self.assertIs(service.actors[caster.body], actor)
        for _ in range(1000):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({caster: (-32768, 8192, 16)})
        for _ in range(35):
            run.step({caster: (-32768, 8192, 0)})
            if service.actors[caster.body] is not actor:
                break
        self.assertIsNot(service.actors[caster.body], actor)
        self.assertFalse(actor.active())
        self.assertEqual(service.actors[caster.body].health, 600)

    def test_whole_hog_breaks_mesh_before_body_force(self):
        run, hog, caster = self.session()
        actor = self.deploy(run, hog, caster)
        hog.mover.position[2] = 4.5
        hog.mover.body = None
        for _ in range(4):
            self.step(run, hog, caster)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, caster, fire=32)
        saw_shield_hit = False
        for _ in range(140):
            self.step(run, hog, caster)
            if 0 < actor.health < 600:
                saw_shield_hit = True
                self.assertEqual(self.health(run, caster), 20000)
                self.assertNotIn(caster.body, run.match.combat.impulses.live)
            if self.health(run, caster) < 20000:
                break
        self.assertTrue(saw_shield_hit)
        self.assertEqual(actor.health, 0)
        self.assertLess(self.health(run, caster), 20000)

    def test_actual_melee_reaches_mesh_without_reaching_owner(self):
        run, hog, caster = self.session()
        actor = self.deploy(run, hog, caster)
        hog.mover.position[2] = actor.position[2]-1.7
        hog.mover.body = None
        for _ in range(3):
            self.step(run, hog, caster)
        self.step(run, hog, caster, fire=0x800)
        for _ in range(25):
            self.step(run, hog, caster)
        self.assertEqual(actor.health, 570)
        self.assertEqual(self.health(run, caster), 20000)

    def test_ally_passes_through_landed_mesh_and_hits_enemy(self):
        run, hog, caster = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        rear = run.players[2]
        rear.team = 1
        rear.mover.position[:] = (0, hog.mover.position[1], 9)
        rear.mover.body = None
        actor = self.deploy(run, hog, caster)
        self.step(run, hog, caster, fire=1)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertLess(self.health(run, rear), 20000)
        self.assertEqual(actor.health, 600)

    def test_actor_vm_export_preserves_pool_and_countdown_causality(self):
        run, hog, caster = self.session()
        actor = self.deploy(run, hog, caster)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("evaluation during export")):
            first = snapshot_digest(export_component_vm(actor.component))
            self.assertEqual(snapshot_digest(export_component_vm(actor.component)), first)
        run.match.combat.orisa_barriers.damage(actor, 30)
        self.assertNotEqual(snapshot_digest(export_component_vm(actor.component)), first)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_five_npc_targets_behind_orisa_mesh(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                with self.subTest(npc=definition.name):
                    run, hog, caster = self.session()
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, caster.team = 0, 1
                    actor = self.deploy(run, hog, caster)
                    bot = npc.spawn(run.match, definition, (1, hog.mover.position[1], 9), team=1)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    before = fight.vitals_of(target).total
                    for _ in range(3):
                        run.step({})
                    angles = aim(fight._eye(hog), (1, target.feet[1]+1, 9))
                    run.step({hog: (*angles, 1)})
                    for _ in range(40):
                        run.step({hog: (*angles, 0)})
                    self.assertLess(actor.health, 600)
                    self.assertEqual(fight.vitals_of(target).total, before)
                    self.assertEqual(self.health(run, caster), 20000)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_four_manual_npc_guns_hit_ally_mesh_before_roadhog(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                if definition.name == "Assassin":
                    continue
                with self.subTest(npc=definition.name):
                    run, hog, caster = self.session()
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = caster.team = 0
                    caster.mover.position[0] = 2.4
                    caster.mover.body = None
                    for player in run.players:
                        player.client = actual.NpcClient(player)
                    actor = self.deploy(run, hog, caster)
                    npc.spawn(run.match, definition, (0, hog.mover.position[1], 12), team=1)
                    for _ in range(15):
                        run.step({})
                    hog.mover.yaw = hog.mover.pitch = 0
                    npc_control.start(run.match, hog)
                    for _ in range(280):
                        run.step({})
                        if actor.health < 600:
                            break
                    self.assertLess(actor.health, 600)
                    self.assertEqual(self.health(run, hog), 600)
                    npc_control.stop(run.match)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()
