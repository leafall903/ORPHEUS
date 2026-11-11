import math
import unittest
from unittest.mock import patch

import test_mei_blizzard as blizzard_fixtures
import test_mei_roadhog_offense as offense
import test_mei_wall as walls
from test_game_combat import aim

from ow174.game import mei
from ow174.game.script import nodes


class MeiWholeHogForceTests(unittest.TestCase):
    def scene(self, distance=8):
        case = offense.MeiRoadhogOffenseTests()
        helper, run, owner, hog, fight, angles = case.scene(distance)
        body = hog.body_script
        body.set_ult_charge(body.component.entity_var(338).value())
        return helper, run, owner, hog, fight, angles

    def test_actual_whole_hog_pushes_mei_once_per_damaging_volley(self):
        helper, run, owner, hog, fight, angles = self.scene()
        start = tuple(owner.mover.position)
        add_impulse = owner.mover.add_external_impulse
        with patch.object(owner.mover, 'add_external_impulse', wraps=add_impulse) as impulse:
            for frame in range(120):
                run.step({owner: (0, 0, 0), hog: (*angles, 32 if frame == 0 else 0)})
                if impulse.called:
                    break
            self.assertEqual(impulse.call_count, 1)
            dx, dy, dz = impulse.call_args.args[0]
            self.assertAlmostEqual(math.hypot(dx, dz), 10)
            self.assertAlmostEqual(dy, 1.8)
            self.assertLess(dz, 0)
        self.assertLess(helper.health(fight, owner), 250)
        for _ in range(12):
            run.step({owner: (0, 0, 0)})
        self.assertLess(owner.mover.position[2], start[2] - 0.5)

    def test_actual_cryo_blocks_whole_hog_damage_and_force(self):
        helper, run, owner, hog, fight, angles = self.scene()
        run.step({owner: (0, 0, 8)})
        self.assertTrue(mei.protected(owner))
        start = tuple(owner.mover.position)
        for frame in range(120):
            run.step({owner: (0, 0, 0), hog: (*angles, 32 if frame == 0 else 0)})
        self.assertTrue(mei.protected(owner))
        self.assertEqual(helper.health(fight, owner), 250)
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertFalse(owner.mover.external_impulse)

    def test_actual_ice_wall_takes_volley_before_mei_and_prevents_force(self):
        helper, run, owner, hog, fight, angles = self.scene()
        pieces, _ = walls.WallTests().place(run, owner, distance=3.0)
        self.assertEqual(len(pieces), 5)
        start = tuple(owner.mover.position)
        for frame in range(120):
            run.step({owner: (0, 0, 0), hog: (*angles, 32 if frame == 0 else 0)})
            if pieces[2].health < 400:
                break
        self.assertLess(pieces[2].health, 400)
        self.assertEqual(helper.health(fight, owner), 250)
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertFalse(owner.mover.external_impulse)

    def test_actual_spray_freeze_stops_firing_through_thaw_and_held_q(self):
        _, run, owner, hog, fight, _ = self.scene(4)
        ability = next(state for state in hog.body_script.ultimates if state.instance.graph.index == 0x01EF)
        with patch.object(fight, '_hitscan_pellet', wraps=fight._hitscan_pellet) as ray:
            for frame in range(110):
                run.step({hog: (0, 0, 32 if frame == 0 else 0)})
                if ray.called:
                    break
            self.assertTrue(ray.called)
            self.assertTrue(ability.flags & nodes.Ability.RUNNING)
            angles = aim(fight._eye(owner), fight._eye(hog))
            for _ in range(130):
                run.step({owner: (*angles, 1), hog: (0, 0, 0)})
                if fight.frozen(hog):
                    break
            self.assertTrue(fight.frozen(hog))
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            fired = ray.call_count
            for _ in range(140):
                run.step({hog: (0, 0, 32)})
            self.assertFalse(fight.frozen(hog))
            self.assertEqual(ray.call_count, fired)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_actual_whole_hog_pushes_mei_without_delivered_commands(self):
        helper, run, owner, hog, fight, angles = self.scene()
        start = owner.mover.position[2]
        # Run.step normally fills omitted input with neutral commands.
        with patch.object(owner, 'take_commands', return_value=None) as blocked:
            for frame in range(120):
                run.step({hog: (*angles, 32 if frame == 0 else 0)})
        self.assertEqual(blocked.call_count, 120)
        self.assertLess(helper.health(fight, owner), 250)
        self.assertLess(owner.mover.position[2], start - 5)

    def test_actual_landed_blizzard_interrupts_already_firing_ultimate(self):
        _, run, owner, hog, fight, _ = self.scene(4)
        blizzard = blizzard_fixtures.BlizzardTests()
        blizzard.launch(run, owner)
        area = blizzard.landed(run)
        while run.frame * 16 < area.starts + 1880:
            run.step({})
        ability = next(state for state in hog.body_script.ultimates if state.instance.graph.index == 0x01EF)
        with patch.object(fight, '_hitscan_pellet', wraps=fight._hitscan_pellet) as ray:
            for frame in range(80):
                run.step({hog: (0, 0, 32 if frame == 0 else 0)})
                if fight.frozen(hog):
                    break
            self.assertTrue(ray.called)
            self.assertTrue(fight.frozen(hog))
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            fired = ray.call_count
            for _ in range(140):
                run.step({hog: (0, 0, 32)})
            self.assertFalse(fight.frozen(hog))
            self.assertEqual(ray.call_count, fired)
