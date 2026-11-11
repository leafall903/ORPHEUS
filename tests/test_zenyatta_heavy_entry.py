"""Execute Heavy Assault charge-entry damage against a second actual Zen."""
import math
import unittest

import test_zenyatta_npcs as scenes
from test_game_mover import box, wall

from ow174.game import heavy_assault, npc_control
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO


class ChargeEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def test_charge_entry_hits_nearby_enemy_without_early_damage_to_distant_target(self):
        for mode in ("enemy", "ally", "far", "wall", "q"):
            with self.subTest(mode=mode):
                run, primary, heavy, _ = scenes.ZenyattaNpcTests().arena(
                    scenes.ZenyattaNpcTests.catalog["5"], names=("Primary Zen", "Side Zen"))
                zen = run.players[1]
                self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
                zen.team = heavy.team if mode == "ally" else primary.team
                x, y, z = primary.mover.position
                heavy.position = (x, y, z+10)
                zen.mover.position = [x+(4 if mode == "far" else 1.5), y, z+9.4]
                zen.mover.body = None
                for _ in range(35):
                    run.step({})
                if mode == "wall":
                    edge = x+.8
                    run.match.collision_world = World(
                        box(x-25, x+25, y-2, y, z-25, z+25)
                        + wall(edge, z+8, edge, z+12, y, y+5)
                    )
                if mode == "q":
                    script = zen.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
                self.assertEqual(npc_control.start(run.match, primary, special_attack=True), heavy.entity)
                instance = heavy_assault.ability(heavy.body_script.component, 0x1408, 82).instance
                for _ in range(75):
                    run.frame += 1
                    run.match.update(run.frame*.016, run.frame)
                    impact = instance.states.get(instance.graph.node(35).state)
                    if impact and impact.counter:
                        break
                self.assertTrue(impact and impact.counter)
                fight = run.match.combat
                self.assertEqual(fight.vitals[primary.body].total, 200)
                self.assertEqual(fight.vitals[zen.body].total, 150 if mode == "enemy" else 200)
                vx, vy, vz = zen.mover.velocity
                if mode in ("enemy", "q"):
                    self.assertAlmostEqual(math.hypot(vx, vz), 30, places=4)
                    self.assertEqual(vy, 2)
                else:
                    self.assertEqual((vx, vy, vz), (0, 0, 0))

    def test_front_entry_group_is_excluded_from_fifty_damage_branch(self):
        run, zen, heavy, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        heavy.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), heavy.entity)
        for _ in range(20):
            run.step({})
        zen.mover.position = [x, y, z+8]
        zen.mover.body = None
        instance = heavy_assault.ability(heavy.body_script.component, 0x1408, 82).instance
        for _ in range(55):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            impact = instance.states.get(instance.graph.node(120).state)
            if impact and impact.counter:
                break
        self.assertTrue(impact and impact.counter)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)
        self.assertEqual(zen.mover.velocity, (0.0, 2.0, -1.0))
