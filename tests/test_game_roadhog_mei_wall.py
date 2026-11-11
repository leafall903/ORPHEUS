"""Real Mei Ice Wall against Roadhog commands and collision."""

import os
import unittest
from dataclasses import replace

import test_game_roadhog_defenses as fixtures

from ow174.game import combat
from ow174.game.commands import Command


class RoadhogMeiWallTests(unittest.TestCase):
    def scene(self, distance=8):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, mei, behind = fixture.session("Mei", distance=distance)
        behind.mover.position[0] += 20
        behind.mover.body = None
        return run, hog, mei

    def place(self, run, mei, distance=4):
        fight = run.match.combat
        x, y, z = mei.mover.position
        aim = fixtures.aim(fight._eye(mei), (x, y, z - distance))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*aim, buttons)})
        self.assertEqual(len(fight.walls.pieces), 5)
        return min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0])), aim

    def test_primary_and_slug_damage_wall_without_hitting_behind(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, hog, mei = self.scene()
                fight = run.match.combat
                pillar, _ = self.place(run, mei)
                self.assertEqual(pillar.health, 400)
                before = fixtures.RoadhogDefenseTests().health(run, mei)
                x, y, z = pillar.feet
                aim = fixtures.aim(fight._eye(hog), (x, y + 1.4, z))
                run.step({hog: (*aim, button)})
                for _ in range(35):
                    run.step({hog: (*aim, 0)})
                self.assertLess(pillar.health, 400)
                if button == 2:
                    self.assertEqual(pillar.health, 350)
                self.assertEqual(fixtures.RoadhogDefenseTests().health(run, mei), before)

    def test_hook_hits_pillar_without_tether_then_reopens_after_cancel(self):
        run, hog, mei = self.scene()
        fight = run.match.combat
        pillar, wall_aim = self.place(run, mei)
        before = fixtures.RoadhogDefenseTests().health(run, mei)
        x, y, z = mei.mover.position
        aim = fixtures.aim(fight._eye(hog), (x, y + 1.4, z))
        run.step({hog: (*aim, 8)})
        for _ in range(40):
            run.step({hog: (*aim, 0)})
            self.assertFalse(fight.hooks.controlled(mei.body))
        self.assertEqual(pillar.health, 370)
        self.assertEqual(fixtures.RoadhogDefenseTests().health(run, mei), before)
        run.step({mei: (*wall_aim, 16)})
        self.assertFalse(fight.walls.pieces)
        run.step({hog: (*aim, 1)})
        for _ in range(30):
            run.step({})
        self.assertLess(fixtures.RoadhogDefenseTests().health(run, mei), before)

    def test_ordinary_movement_stops_at_wall_and_recovers_after_cancel(self):
        run, hog, mei = self.scene()
        fight = run.match.combat
        pillar, wall_aim = self.place(run, mei)

        def move(ticks):
            for _ in range(ticks):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                for player in run.players:
                    player.client.queue_entities(run.match.remote_movements(player))
                    player.take_commands([Command(run.frame, forward=127 if player is hog else 0)])

        move(95)
        blocked = hog.mover.position[2]
        self.assertLess(blocked, pillar.feet[2] - pillar.depth)
        self.assertGreater(blocked, 2.8)
        run.step({mei: (*wall_aim, 16)})
        move(25)
        self.assertGreater(hog.mover.position[2], pillar.feet[2] + pillar.depth)

    def test_expired_slug_fragments_and_melee_each_damage_pillars(self):
        for button, caster_distance in ((2, 15), (0x800, 5.5)):
            with self.subTest(button=button):
                run, hog, mei = self.scene(caster_distance)
                fight = run.match.combat
                pillar, _ = self.place(run, mei)
                before = fixtures.RoadhogDefenseTests().health(run, mei)
                x, y, z = pillar.feet
                angles = fixtures.aim(fight._eye(hog), (x, y + 1.4, z))
                run.step({hog: (*angles, button)})
                for _ in range(45):
                    run.step({hog: (*angles, 0)})
                if button == 0x800:
                    self.assertEqual(pillar.health, 370)
                else:
                    self.assertLess(pillar.health, 350)  # multiple child pellets, no direct slug
                self.assertEqual(fixtures.RoadhogDefenseTests().health(run, mei), before)

    def test_whole_hog_breaks_wall_then_reaches_body_on_same_cast(self):
        run, hog, mei = self.scene()
        fight = run.match.combat
        pillar, _ = self.place(run, mei)
        before = fixtures.RoadhogDefenseTests().health(run, mei)
        x, y, z = mei.mover.position
        angles = fixtures.aim(fight._eye(hog), (x, y + 1.4, z))
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ultimate, = script.ultimates
        run.step({hog: (*angles, 32)})
        self.assertTrue(ultimate.flags & 1)
        first_hit = False
        for _ in range(280):
            run.step({hog: (*angles, 0)})
            if 0 < pillar.health < 400:
                first_hit = True
                self.assertEqual(fixtures.RoadhogDefenseTests().health(run, mei), before)
            if fixtures.RoadhogDefenseTests().health(run, mei) < before:
                break
        self.assertTrue(first_hit)
        self.assertNotIn(pillar.entity, fight.walls.pieces)
        self.assertLess(fixtures.RoadhogDefenseTests().health(run, mei), before)
        self.assertTrue(ultimate.flags & 1)

    def test_ice_wall_appearing_during_hook_breaks_the_live_tether(self):
        self.check_ice_wall_breaks_live_tether()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_ice_wall_appearing_during_hook_breaks_each_five_npc_tether(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_ice_wall_breaks_live_tether(definition=definition, client=actual.NpcClient)

    def check_ice_wall_breaks_live_tether(self, *, definition=None, client=None):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, mei, victim = fixture.session("Mei", distance=8)
        fight = run.match.combat
        x, y, z = hog.mover.position
        mei.mover.position[:] = (x + 4, y, z + 9)
        mei.mover.body = None
        if definition is None:
            victim.mover.position[:] = (x, y, z + 14)
            victim.mover.body = None
        else:
            from ow174.game import npc

            victim.mover.position[0] += 20
            victim.mover.body = None
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(run.match, definition, (x, y, z + 14), team=1)
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        entity = target.entity
        wall_aim = fixtures.aim(fight._eye(mei), (x, y, z + 7))
        attack = fixtures.aim(fight._eye(hog), (x, y + 1.4, z + 14))
        before = pool.total
        run.step({mei: (*wall_aim, 16), hog: (*attack, 8)})
        for _ in range(55):
            run.step({mei: (*wall_aim, 0), hog: (*attack, 0)})
            if fight.hooks.controlled(entity):
                break
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertIsNone(fight.hooks.flights[hog.body].arrived_at)
        run.step({mei: (*wall_aim, 1)})
        self.assertTrue(fight.walls.pieces)
        run.step({})
        self.assertFalse(fight.hooks.controlled(entity))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(pool.total, before - 30)
        self.assertEqual({pillar.health for pillar in fight.walls.pieces.values()}, {400})
        if definition is not None:
            position = victim.position
            for _ in range(6):
                run.step({})
            self.assertEqual(victim.position, position)
            self.assertEqual(victim.velocity, (0, 0, 0))

    def test_ally_fire_stops_without_damaging_wall_and_expiry_reopens_line(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, mei, victim = fixture.session("Mei", distance=8)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        victim.team = 1
        pillar, _ = self.place(run, mei)
        before = fixture.health(run, victim)
        x, y, z = victim.mover.position
        aim = fixtures.aim(fight._eye(hog), (x, y + 1.4, z))
        run.step({hog: (*aim, 1)})
        for _ in range(40):
            run.step({})
        self.assertEqual(pillar.health, 400)
        self.assertEqual(fixture.health(run, victim), before)
        while fight.tick < pillar.expires:
            run.step({})
        self.assertFalse(fight.walls.pieces)
        run.step({hog: (*aim, 1)})
        for _ in range(30):
            run.step({})
        self.assertLess(fixture.health(run, victim), before)

    def test_cancel_preserves_wall_cooldown_and_weapon_ammo(self):
        run, _, mei = self.scene()
        fight = run.match.combat
        _, aim = self.place(run, mei)
        instance = fight.walls.instance(mei)
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x05E6)
        self.assertEqual(weapon.vars[53].value(), 120)
        self.assertGreater(instance.state(33).remaining(), 11)
        run.step({mei: (*aim, 16)})
        self.assertFalse(fight.walls.pieces)
        for buttons in (0, 16, 0, 1, 0):
            run.step({mei: (*aim, buttons)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(780):
            run.step({})
        self.place(run, mei)
        self.assertEqual(len(fight.walls.pieces), 5)
