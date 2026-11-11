"""Actual00FA creation/00FC timers; reconstructed finite dome crossings."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import combat, winston_barrier
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, nodes, runtime


class RoadhogWinstonTests(unittest.TestCase):
    health = phases.RoadhogDefenseTests.health
    step = phases.RoadhogDefenseTests.step

    def session(self, distance=8):
        run, hog, caster, rear = phases.RoadhogDefenseTests().session('Winston', distance=distance)
        rear.mover.position[0] = 20
        for _ in range(30):
            self.step(run, hog, caster)
        return run, hog, caster, rear

    def cast(self, run, hog, caster):
        self.step(run, hog, caster, cast=16)
        self.assertFalse(run.match.combat.winston_bubbles.actors)
        for _ in range(6):
            self.step(run, hog, caster)
        actors = run.match.combat.winston_bubbles.actors
        self.assertEqual(len(actors), 1)
        return next(iter(actors.values()))

    def test_actual_creation_output_request_parameters_and_original_lifetime(self):
        run, hog, caster, _ = self.session()
        actor = self.cast(run, hog, caster)
        source = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0xFA)
        self.assertEqual(expr.to_entity(source.vars[38].value()), actor.body)
        self.assertEqual(actor.health, 700)
        self.assertEqual(actor.request.resource.guid, 0x05800000000000FC)
        self.assertEqual(actor.request.targets, (actor.body,))
        callee = next(iter(actor.component.instances.values()))
        self.assertEqual(expr.to_float(callee.vars[567].value()), 9)
        self.assertEqual(expr.to_float(callee.vars[587].value()), 700)
        self.assertTrue(callee.states[11].active)
        centre = actor.centre
        caster.mover.position[0] += 20
        for _ in range(550):
            self.step(run, hog, caster)
        self.assertEqual(actor.centre, centre)
        self.assertIn(actor.body, run.match.combat.winston_bubbles.actors)
        for _ in range(20):
            self.step(run, hog, caster)
        self.assertTrue(actor.retired)
        self.assertFalse(actor.component.instances)
        self.assertFalse(run.match.combat.winston_bubbles.actors)

    def test_primary_hook_slug_and_fragments_block_without_body_charge(self):
        for button, distance, expected in ((1, 8, 535), (8, 8, 670), (2, 8, 650), (2, 16, 535)):
            with self.subTest(button=button, distance=distance):
                run, hog, caster, _ = self.session(distance)
                actor = self.cast(run, hog, caster)
                self.step(run, hog, caster, fire=button)
                for _ in range(45):
                    self.step(run, hog, caster)
                self.assertAlmostEqual(actor.health, expected, places=2)
                self.assertEqual(self.health(run, caster), 20000)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(run.match.combat.hooks.controlled(caster.body))

    def test_new_dome_cuts_live_hook_only_when_shell_crosses_the_link(self):
        for lateral, cuts in ((2.5, True), (8, False)):
            with self.subTest(cuts=cuts):
                self.check_new_dome_hook_link(lateral, cuts)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_new_dome_releases_all_five_live_npc_hook_pulls(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_new_dome_hook_link(2.5, True, definition=definition, client=actual.NpcClient)

    def check_new_dome_hook_link(self, lateral, cuts, *, definition=None, client=None):
        from ow174.game import npc

        run, hog, caster, victim = self.session()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team, victim.team = 0, 1, 1
        y = hog.mover.position[1]
        caster.mover.position[:] = (lateral, y, 12 if definition is None else 8)
        caster.mover.body = None
        if definition is None:
            victim.mover.position[:] = (0, y, 17)
            victim.mover.body = None
        else:
            victim.mover.position[0] = 20
            victim.mover.body = None
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(match, definition, (0, y, 12), team=1)
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = aim(fight._eye(hog), centre)
        run.step({hog: (*angles, 8)})
        for _ in range(50):
            run.step({hog: (*angles, 0)})
            if fight.hooks.controlled(target.entity):
                break
        self.assertTrue(fight.hooks.controlled(target.entity))
        self.assertLess(pool.total, before)

        hooked_health = pool.total
        if definition is None:
            self.assertEqual(hooked_health, before - 30)
        run.step({caster: (0, 0, 16)})
        for _ in range(15):
            if fight.winston_bubbles.actors:
                break
            self.assertTrue(fight.hooks.controlled(target.entity))
            run.step({})
        dome, = fight.winston_bubbles.actors.values()
        run.step({})
        self.assertEqual(fight.hooks.controlled(target.entity), not cuts)
        self.assertEqual(dome.health, 700)
        def depth():
            return (victim.mover.position if definition is None else victim.position)[2]

        position = depth()
        for _ in range(12):
            run.step({})
        if cuts:
            self.assertEqual(depth(), position)
        else:
            self.assertLess(depth(), position)
        self.assertEqual(pool.total, hooked_health)
        if definition is not None:
            self.assertEqual(victim.velocity, (0, 0, 0))
        self.assertEqual(dome.health, 700)

    def test_breaking_hook_consumes_whole_hit_and_original_two_frame_death_tail(self):
        run, hog, caster, _ = self.session()
        actor = self.cast(run, hog, caster)
        fight = run.match.combat
        fight.winston_bubbles.damage(actor, 695)
        self.step(run, hog, caster, fire=8)
        for _ in range(40):
            self.step(run, hog, caster)
            if actor.health == 0:
                break
        self.assertEqual(actor.health, 0)
        self.assertFalse(actor.retired)
        callee = next(iter(actor.component.instances.values()))
        self.assertTrue(callee.states[12].active)  #00FC node29 is a frame wait
        self.assertEqual(callee.component.entity_var(478).value(), True)
        self.assertEqual(self.health(run, caster), 20000)
        for _ in range(3):
            self.step(run, hog, caster)
        self.assertTrue(actor.retired)
        self.assertFalse(actor.component.instances)
        self.assertEqual(self.health(run, caster), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_inside_body_is_hittable_but_exit_surface_blocks_external_target(self):
        for inside in (True, False):
            with self.subTest(inside=inside):
                run, hog, caster, rear = self.session()
                actor = self.cast(run, hog, caster)
                hog.mover.position[2] = 7
                hog.mover.body = None
                if not inside:
                    caster.mover.position[0] = 20
                    rear.mover.position[:] = (0, hog.mover.position[1], 15)
                    caster.mover.body = rear.mover.body = None
                for _ in range(3):
                    self.step(run, hog, caster)
                if inside:
                    x, y, z = caster.mover.position
                    angles = aim(run.match.combat._eye(hog), (x, y + 0.5, z))
                    run.step({hog: (*angles, 1)})
                else:
                    # Aim through the exit, not toward the relocated owner.
                    run.step({hog: (0, 0, 1)})
                for _ in range(35):
                    self.step(run, hog, caster)
                self.assertAlmostEqual(actor.health, 700 if inside else 535, places=2)
                self.assertAlmostEqual(self.health(run, caster), 19835 if inside else 20000, places=3)
                self.assertEqual(self.health(run, rear), 20000)

    def test_whole_hog_pushes_victim_out_of_dome_then_hits_exit_shell(self):
        self.check_whole_hog_dome_crossing()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_whole_hog_pushes_all_five_npcs_out_of_dome_then_hits_exit_shell(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_whole_hog_dome_crossing(definition=definition, client=actual.NpcClient)

    def check_whole_hog_dome_crossing(self, *, definition=None, client=None):
        from ow174.game import npc

        run, hog, caster, victim = self.session()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team, victim.team = 0, 1, 1
        dome = self.cast(run, hog, caster)
        y = hog.mover.position[1]
        hog.mover.position[:] = (0, y, 7)
        caster.mover.position[0] = 20
        hog.mover.body = caster.mover.body = None
        if definition is None:
            victim.mover.position[:] = (0, y, 11)
            victim.mover.body = None
        else:
            victim.mover.position[0] = 20
            victim.mover.body = None
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(match, definition, (0, y, 11.6), team=1)
            fight.vitals[victim.entity] = combat.Vitals(victim.entity, 20000)
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = (0, 0) if definition is None else aim(fight._eye(hog), centre)

        def step(button=0):
            run.step({hog: (*angles, button)})

        def depth():
            return (victim.mover.position if definition is None else victim.position)[2]

        hog.body_script.set_ult_charge(2240)
        step(32)
        for _ in range(80):
            step()
            if target.entity in fight.impulses.live:
                break
        self.assertIn(target.entity, fight.impulses.live)
        self.assertLess(pool.total, before)
        self.assertLess(dome.health, 700)

        exit_depth = dome.centre[2] + winston_barrier.RADIUS + target.radius
        for _ in range(90):
            step()
            if depth() > exit_depth:
                break
        self.assertGreater(depth(), exit_depth)
        # Let hit detection's view history clear the crossing before sampling damage.
        for _ in range(8):
            step()
        body_health, shell_health = pool.total, dome.health
        for _ in range(12):
            step()
        self.assertEqual(pool.total, body_health)
        self.assertLess(dome.health, shell_health)
        self.assertGreater(dome.health, 0)

    def test_melee_hits_shell_independently_and_inside_melee_hits_body(self):
        for inside in (False, True):
            with self.subTest(inside=inside):
                run, hog, caster, _ = self.session()
                actor = self.cast(run, hog, caster)
                hog.mover.position[2] = 6.5 if inside else 1
                hog.mover.body = None
                for _ in range(3):
                    self.step(run, hog, caster)
                self.step(run, hog, caster, fire=0x800)
                for _ in range(25):
                    self.step(run, hog, caster)
                self.assertEqual(actor.health, 700 if inside else 670)
                self.assertEqual(self.health(run, caster), 19970 if inside else 20000)

    def test_world_occlusion_team_pass_and_miss(self):
        for mode in ('wall', 'ally', 'miss'):
            with self.subTest(mode=mode):
                run, hog, caster, rear = self.session()
                actor = self.cast(run, hog, caster)
                if mode == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = caster.team = actor.team = 0
                    rear.team = 1
                    rear.mover.position[:] = (0, hog.mover.position[1], 9)
                    rear.mover.body = None
                elif mode == 'wall':
                    y = hog.mover.position[1]
                    run.match.collision_world = World([
                        (-10, y-10, 1, 10, y-10, 1, 10, y+10, 1),
                        (-10, y-10, 1, 10, y+10, 1, -10, y+10, 1),
                    ])
                if mode == 'miss':
                    run.step({hog: (16384, 0, 1)})
                else:
                    self.step(run, hog, caster, fire=1)
                for _ in range(35):
                    self.step(run, hog, caster)
                self.assertEqual(actor.health, 700)
                self.assertEqual(self.health(run, caster), 20000)
                if mode == 'ally':
                    self.assertLess(self.health(run, rear), 20000)
                else:
                    self.assertEqual(self.health(run, rear), 20000)

    def test_pending_cast_cancel_or_death_does_not_create_and_deployed_owner_death_persists(self):
        for mode in ('cancel', 'pending_death', 'deployed_death', 'replacement'):
            with self.subTest(mode=mode):
                run, hog, caster, _ = self.session()
                if mode in ('deployed_death', 'replacement'):
                    actor = self.cast(run, hog, caster)
                else:
                    self.step(run, hog, caster, cast=16)
                if mode == 'cancel':
                    source = next(i for i in caster.body_script.component.instances.values()
                                  if i.graph.index == 0xFA)
                    source.component.destroy(source)  # actual instance teardown cancels pending waits
                    caster.body_script.component.run_now()
                elif mode == 'replacement':
                    run.match.switch_hero(caster, hog.hero)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is caster)
                    run.match.combat._damage(hog, target, 50000, False, None, run.frame)
                for _ in range(8):
                    self.step(run, hog, caster)
                actors = run.match.combat.winston_bubbles.actors
                if mode in ('deployed_death', 'replacement'):
                    self.assertIs(actors[actor.body], actor)
                    self.assertEqual(actor.health, 700)
                else:
                    self.assertFalse(actors)

    def test_whole_hog_breaks_dome_then_later_pellets_reach_body(self):
        run, hog, caster, _ = self.session()
        actor = self.cast(run, hog, caster)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, caster, fire=32)
        for _ in range(100):
            self.step(run, hog, caster)
        self.assertEqual(actor.health, 0)
        self.assertTrue(actor.retired)
        self.assertLess(self.health(run, caster), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_active_actor_storage_is_read_only_and_health_clock_request_are_causal(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        caster = players[0]
        session.apply('switch_hero', {'account': caster.account_lo, 'hero': 0x02E0000000000009})
        fight = caster.match.combat
        for offset in range(50):
            frame = caster.body_script.frame+1
            caster.match.tick = fight.tick = frame
            fight.now = frame*combat.TICK
            caster.body_script.command(frame, 16 if offset == 35 else 0)
            fight.winston_bubbles.update(frame)
        actor = next(iter(fight.winston_bubbles.actors.values()))
        with (patch.object(runtime.Var, 'value', side_effect=AssertionError('no evaluation')),
              patch.object(winston_barrier.Barriers, 'update', side_effect=AssertionError('no advance'))):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            actor.health -= 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            actor.health += 1
            actor.component.now += 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph bundle required')
    def test_dome_protects_all_five_actual_npcs_from_primary_slug_fragments_hook_and_ultimate(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        case = actual.RoadhogFiveNpcTests()
        try:
            for definition in case.definitions.values():
                for button, distance in ((1, 8), (2, 8), (2, 16), (8, 8), (32, 8)):
                    with self.subTest(npc=definition.name, button=button, distance=distance):
                        run, hog, bot, pool, look = case.session(definition, distance=distance, caster=True)
                        caster = run.players[1]
                        run.match.switch_hero(caster, heroes()[0x02E0000000000009])
                        x, y, z = bot.position
                        caster.mover.position[:] = (x+2, y, z)
                        caster.mover.body = None
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        caster.team = bot.team = 1
                        hog.team = 0
                        for _ in range(30):
                            run.step({})
                        run.step({caster: (0, 0, 16)})
                        for _ in range(6):
                            run.step({})
                        actor = next(iter(run.match.combat.winston_bubbles.actors.values()))
                        before = pool.total
                        if button == 32:
                            hog.body_script.set_ult_charge(2240)
                        run.step({hog: (*look, button)})
                        for _ in range(100):
                            run.step({})
                            self.assertFalse(run.match.combat.hooks.controlled(bot.entity))
                            if actor.health < 700:
                                break
                        self.assertLess(actor.health, 700)
                        self.assertGreater(actor.health, 0)
                        self.assertEqual(pool.total, before)
                        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph bundle required')
    def test_actual_four_manual_npc_guns_hit_surface_protecting_roadhog(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        case = actual.RoadhogFiveNpcTests()
        try:
            for definition in case.definitions.values():
                if definition.name == 'Assassin':
                    continue
                with self.subTest(npc=definition.name):
                    run, hog, _bot, _, look = case.session(definition, distance=10, caster=True)
                    caster = run.players[1]
                    run.match.switch_hero(caster, heroes()[0x02E0000000000009])
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster.team = hog.team = 0
                    for player in run.players:
                        player.client = actual.NpcClient(player)
                    for _ in range(30):
                        run.step({})
                    run.step({caster: (0, 0, 16)})
                    for _ in range(6):
                        run.step({})
                    actor = next(iter(run.match.combat.winston_bubbles.actors.values()))
                    hog.mover.yaw, hog.mover.pitch = look
                    npc_control.start(run.match, hog)
                    for _ in range(300):
                        run.step({})
                        if actor.health < 700:
                            break
                    self.assertLess(actor.health, 700)
                    self.assertEqual(self.health(run, hog), 600)
                    npc_control.stop(run.match)
                    for _ in range(100):
                        run.step({})
                    self.assertEqual(self.health(run, hog), 600)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph bundle required')
    def test_dome_does_not_grant_blanket_immunity_to_assassin_inside(self):
        import test_game_roadhog_assassin as actual

        actual.RoadhogAssassinTests.setUpClass()
        case = actual.RoadhogAssassinTests()
        try:
            run, hog, bot, _, look, own = case.encounter(support=True)
            caster = run.players[1]
            run.match.switch_hero(caster, heroes()[0x02E0000000000009])
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            caster.team = hog.team = 0
            for _ in range(30):
                run.step({})
            run.step({caster: (0, 0, 16)})
            for _ in range(6):
                run.step({})
            actor = next(iter(run.match.combat.winston_bubbles.actors.values()))
            case.engage(run, hog, bot, look)
            self.assertEqual(own.total, 575)
            self.assertEqual(actor.health, 700)
            self.assertEqual(run.match.combat.pins.get(bot.entity), hog.body)
        finally:
            actual.RoadhogAssassinTests.tearDownClass()

    def test_cooldown_starts_on_cast_refuses_repress_and_reopens_after_thirteen_seconds(self):
        run, hog, caster, _ = self.session()
        actor = self.cast(run, hog, caster)
        source = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0xFA)
        ability = next(s for s in source.states.values() if isinstance(s, nodes.Ability))
        self.assertGreater(ability.remaining(), 12)
        self.step(run, hog, caster, cast=16)
        for _ in range(565):
            self.step(run, hog, caster)
        self.assertTrue(actor.retired)
        self.assertGreater(ability.remaining(), 3)
        self.step(run, hog, caster, cast=16)
        for _ in range(6):
            self.step(run, hog, caster)
        self.assertFalse(run.match.combat.winston_bubbles.actors)
        for _ in range(260):
            self.step(run, hog, caster)
        self.assertEqual(ability.remaining(), 0)
        second = self.cast(run, hog, caster)
        self.assertNotEqual(second.body, actor.body)
        self.assertEqual(second.health, 700)


if __name__ == '__main__':
    unittest.main()
