"""Real Mei Blizzard input against normal and Transcendent Zen."""
import unittest

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import mei_frost
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO as ZEN


class ZenyattaBlizzardTests(unittest.TestCase):
    def arena(self, *, projection=False):
        run = Run(names=("Mei", "Zen", "Zarya") if projection else ("Mei", "Zen"))
        mei, zen = run.players[:2]
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(mei, heroes()[mei_frost.HERO]))
        self.assertTrue(run.match.switch_hero(zen, heroes()[ZEN]))
        mei.team, zen.team = 0, 1
        x, y, z = mei.mover.position
        zen.mover.position = [x+2, y, z+5]
        zen.mover.body = None
        for _ in range(35):
            run.step({})
        return run, mei, zen, aim(run.match.combat._eye(mei), (x, y, z+5))

    def cast(self, run, mei, angles, *, charge):
        if charge:
            script = mei.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        for index in range(32):
            run.step({mei: (*angles, 0x20 if index == 0 else 0)})

    def land(self, run, mei, angles):
        fight = run.match.combat
        self.cast(run, mei, angles, charge=True)
        self.assertEqual(mei.body_script.component.entity_var(342).value(), 0)
        flight, = fight.blizzard.flights
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        for _ in range(150):
            run.step({})
            if fight.blizzard.areas:
                break
        area, = fight.blizzard.areas.values()
        self.assertEqual(area.ends-area.starts, 4250)
        self.assertEqual(area.last_ms, area.starts)
        return area

    def test_actual_blizzard_needs_charge_and_deals_timed_damage_and_freeze(self):
        run, mei, zen, angles = self.arena()
        fight = run.match.combat
        self.cast(run, mei, angles, charge=False)
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        area = self.land(run, mei, angles)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        while run.frame*16 < area.starts+2632:
            run.step({})
        self.assertTrue(fight.is_stunned(zen))
        frozen = fight.frost[zen.body]
        self.assertEqual(frozen.frozen_until - frozen.frozen_from, 1300)
        while run.frame*16 < area.ends+16:
            run.step({})
        self.assertFalse(fight.blizzard.areas)
        self.assertAlmostEqual(fight.vitals[zen.body].total, 115)
        for _ in range(80):
            run.step({})
        self.assertFalse(fight.is_stunned(zen))
        self.assertNotIn(zen.body, fight.frost)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        ammo = weapon.vars[53].value()
        run.step({zen: (0, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), ammo-1)

    def test_actual_blizzard_does_not_damage_or_freeze_transcendent_zen(self):
        run, mei, zen, angles = self.arena()
        fight = run.match.combat
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        area = self.land(run, mei, angles)
        while run.frame*16 < area.ends+16:
            run.step({})
            self.assertTrue(fight.zenyatta.ultimate(zen))
            self.assertNotIn(zen.body, fight.frost)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertFalse(fight.is_stunned(zen))

    def test_actual_blizzard_rechecks_cover_range_and_team(self):
        for mode in ("wall", "range", "ally"):
            with self.subTest(mode=mode):
                self.reject_blizzard(mode)

    def reject_blizzard(self, mode):
        run, mei, zen, angles = self.arena()
        fight = run.match.combat
        area = self.land(run, mei, angles)
        x, y, z = area.position
        if mode == "wall":
            run.match.collision_world = World(
                box(x-20, x+20, y-2, y, z-20, z+20) + wall(x+1, z-6, x+1, z+6, y, y+5)
            )
        elif mode == "range":
            zen.mover.position = [x+12, y, z]
            zen.mover.body = None
        else:
            zen.team = mei.team
        while run.frame*16 < area.ends+16:
            run.step({})
            self.assertNotIn(zen.body, fight.frost)
        self.assertEqual(fight.vitals[zen.body].total, 200)

    def test_actual_projected_barrier_delays_blizzard_damage_and_frost(self):
        run, mei, zen, angles = self.arena(projection=True)
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        area = self.land(run, mei, angles)
        aim_zen = aim(fight._eye(zarya), (x, y+1.55, z))
        run.step({zarya: (*aim_zen, 0x10)})
        self.assertIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        while run.frame*16 < area.starts+800:
            run.step({})
            self.assertNotIn(zen.body, fight.frost)
        self.assertLess(fight.zarya_barriers.projected[zarya.body].remaining, 200)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        for _ in range(150):
            run.step({})
            if zen.body in fight.frost:
                break
        self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        self.assertIn(zen.body, fight.frost)
        self.assertLess(fight.vitals[zen.body].total, 200)

    def test_actual_deflect_transfers_throw_and_grasp_consumes_it(self):
        for graph, button in ((0x03A3, 0x10), (0x1D02, 0x08)):
            with self.subTest(graph=graph):
                self.intercept(graph, button)

    def intercept(self, graph_index, button):

        run, mei, zen, _ = self.arena(projection=True)
        guard = run.players[2]
        hero = next(h for h in heroes().values() if h.name == ("Genji" if graph_index == 0x03A3 else "Sigma"))
        self.assertTrue(run.match.switch_hero(guard, hero))
        guard.team = 1
        zen.team = mei.team
        x, y, z = mei.mover.position
        zen.mover.position = [x+2, y, z-5]
        zen.mover.body = None
        guard.mover.position = [x, y, z+6]
        guard.mover.body = None
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for i in range(120):
            run.step({mei: (0, 0, 0x20 if i == 0 else 0),
                      guard: (-32768, 0, button if 20 <= i < 30 else 0)})
            if i == 29:
                instance = next(it for it in guard.body_script.component.instances.values()
                                if it.graph.index == graph_index)
                self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                    for s in instance.states.values()))
        self.assertFalse(fight.blizzard.flights)
        if graph_index == 0x03A3:
            area, = fight.blizzard.areas.values()
            self.assertIs(area.owner, guard)
        else:
            self.assertFalse(fight.blizzard.areas)
