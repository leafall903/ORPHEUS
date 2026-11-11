"""104319 FirstPerson fields, actual crouch commands and narrow-cover shot checks."""

import os
import unittest
from pathlib import Path
from xml.etree import ElementTree

import test_game_roadhog_defenses as phases
from test_game_mover import box

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class RoadhogEyeTests(unittest.TestCase):
    def test_selected_registry_matches_original_body_xml_fields(self):
        directory = os.environ.get("ORPHEUS_TEST_EYE_XML")
        if directory is None:
            self.skipTest("explicit original104319 body XML directory required")
        for body, expected in ((0x28F, (1.6, 1.12)), (0x2A3, (1.425, .9975))):
            root = ElementTree.parse(Path(directory) / f"{body:012X}.003.xml").getroot()
            component = next(node for node in root.iter() if node.tag == "STUFirstPersonComponent"
                             and "m_E29993A5" in node.attrib)
            observed = (float(component.attrib["m_E29993A5"]), float(component.attrib["m_816B830F"]))
            self.assertEqual(observed, expected)
            for crouched, value in zip((False, True), observed, strict=True):
                self.assertEqual(combat.eye_height(0x0400000000000000 | body, crouched), value)

    def test_actual_crouch_pose_eye_target_and_history_share_authored_height(self):
        for hero, standing, crouched_height in ((0x40, 1.6, 1.12), (0x42, 1.425, .9975)):
            with self.subTest(hero=hero):
                run, subject, _, _ = phases.RoadhogDefenseTests().session("Reaper", distance=6)
                if hero != 0x40:
                    run.match.switch_hero(subject, heroes()[0x02E0000000000000 | hero])
                for crouched, expected in ((False, standing), (True, crouched_height), (False, standing)):
                    for _ in range(30):
                        run.step({subject: (0, 0, CROUCH if crouched else 0)})
                    self.assertEqual(subject.mover.crouched, crouched)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is subject)
                    self.assertAlmostEqual(fight._eye(subject)[1] - subject.mover.position[1], expected)
                    self.assertEqual(target.eye, expected)
                    self.assertEqual(fight.history[subject.body][-1][3], expected)

    @staticmethod
    def cover(y, top):
        return World([*box(-30, 30, y - 1, y, -30, 30),
            (-20, y - 1, 2, 20, y - 1, 2, 20, y + top, 2),
            (-20, y - 1, 2, 20, y + top, 2, -20, y + top, 2),
        ])

    @staticmethod
    def session():
        run, hog, target, rear = phases.RoadhogDefenseTests().session("Reaper", distance=6)
        run.match.switch_hero(target, heroes()[0x02E0000000000042])
        target.mover.position[:] = (0, hog.mover.position[1], 6)
        target.mover.body = None
        run.match.combat.vitals[target.body] = combat.Vitals(target.body, 20000)
        for _ in range(30):
            run.step({})
        return run, hog, target, rear

    def test_roadhog_standing_slug_clears_cover_that_old155_eye_would_hit(self):
        case = phases.RoadhogDefenseTests()
        run, hog, target, _ = self.session()
        y = hog.mover.position[1]
        run.match.collision_world = self.cover(y, 1.575)
        run.step({hog: (0, 0, 2)})
        for _ in range(35):
            run.step({})
        self.assertEqual(case.health(run, target), 19950)

    def test_roadhog_crouched_slug_hits_cover_that_old115_eye_would_clear(self):
        case = phases.RoadhogDefenseTests()
        run, hog, target, _ = self.session()
        for _ in range(30):
            run.step({hog: (0, 0, CROUCH)})
        self.assertTrue(hog.mover.crouched)
        y = hog.mover.position[1]
        run.match.collision_world = self.cover(y, 1.135)
        run.step({hog: (0, 0, CROUCH | 2)})
        for _ in range(35):
            run.step({hog: (0, 0, CROUCH)})
        self.assertEqual(case.health(run, target), 20000)

    def test_enemy_mccree_authored_eye_does_not_shoot_through_intermediate_cover(self):
        case = phases.RoadhogDefenseTests()
        run, hog, source, _ = self.session()
        y = hog.mover.position[1]
        source.mover.position[:] = (0, y, 0)
        hog.mover.position[:] = (0, y, 6)
        source.mover.body = hog.mover.body = None
        run.match.collision_world = self.cover(y, 1.48)
        run.step({source: (0, 0, 1)})
        for _ in range(30):
            run.step({})
        self.assertEqual(case.health(run, hog), 600)


if __name__ == "__main__":
    unittest.main()
