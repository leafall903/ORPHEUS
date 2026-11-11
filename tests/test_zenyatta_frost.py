"""Actual Mei spray against Zenyatta movement, weapon and Transcendence."""
import math
import unittest
from unittest.mock import patch
from dataclasses import replace

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import combat, mei_frost
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.zenyatta import HERO as ZEN


class ZenyattaFrostTests(unittest.TestCase):
    def arena(self, *, ally=False):
        run = Run(names=("Mei", "Zen", "Zarya") if ally else ("Mei", "Zen"))
        mei, zen = run.players[:2]
        run.match.bots.clear()
        x, y, z = mei.mover.position
        for player, hero, team, distance in ((mei, mei_frost.HERO, 0, 0), (zen, ZEN, 1, 3)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position = [x, y, z+distance]
            player.mover.body = None
        for _ in range(30):
            run.step({})
        return run, mei, zen

    def aim_at(self, run, player, target, button):
        fight = run.match.combat
        x, y, z = target.mover.position
        return (*aim(fight._eye(player), (x, y+1, z)), button)

    def test_actual_spray_slows_freezes_and_stops_charged_weapon_until_thaw(self):
        run, mei, zen = self.arena()
        fight = run.match.combat
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        for _ in range(55):
            run.step({zen: self.aim_at(run, zen, mei, SECONDARY)})
        self.assertGreater(weapon.vars[1392].value(), 0)
        base_speed = zen.mover.data.run_forward
        slowed = False
        for _ in range(150):
            run.step({mei: self.aim_at(run, mei, zen, FIRE),
                      zen: self.aim_at(run, zen, mei, SECONDARY)})
            slowed |= zen.mover.values.run_forward < base_speed
            if fight.is_stunned(zen):
                break
        sample = fight.frost.samples[zen.body]
        self.assertTrue(slowed)
        self.assertEqual(sample[0], 1)
        self.assertEqual(sample[2]-sample[1], 1300)
        self.assertEqual(weapon.vars[1392].value(), 0)
        self.assertLess(fight.vitals[zen.body].total, 200)
        ammo = weapon.vars[53].value()
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        while (run.frame+1)*16 < sample[2]:
            run.step({zen: self.aim_at(run, zen, mei, FIRE | 0x20)})
            self.assertFalse(fight.zenyatta.ultimate(zen))
            self.assertTrue(fight.is_stunned(zen))
            self.assertEqual(weapon.vars[53].value(), ammo)
        run.step({})
        self.assertFalse(fight.is_stunned(zen))
        self.assertNotIn(zen.body, fight.frost.samples)
        run.step({zen: self.aim_at(run, zen, mei, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), ammo-1)

    def test_actual_thaw_releases_view_and_held_volley_on_the_same_tick_across_a_gap(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, mei, zen = self.arena()
                fight = run.match.combat
                for _ in range(150):
                    run.step({mei: self.aim_at(run, mei, zen, FIRE)})
                    if fight.is_stunned(zen):
                        break
                frozen = fight.frost.samples[zen.body]
                self.assertEqual(frozen[0], 1)
                deadline = math.ceil(frozen[2] / 16)
                held = self.aim_at(run, zen, mei, SECONDARY)
                self.assertNotEqual(held[:2], frozen[3:])
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
                charge_node = weapon.graph.node(122)
                ammo = weapon.vars[53].value()
                while run.frame < deadline - 3:
                    run.step({zen: held})
                    self.assertTrue(fight.is_stunned(zen))
                    self.assertEqual(weapon.vars[53].value(), ammo)
                with patch.object(zen.mover, "step_pose", wraps=zen.mover.step_pose) as poses:
                    if skipped:
                        run.frame += 2
                    while run.frame < deadline:
                        run.step({zen: held})
                commands = [call.args[0] for call in poses.call_args_list]
                self.assertEqual([command.frame for command in commands], list(range(deadline - 2, deadline + 1)))
                self.assertEqual([(cmd.yaw, cmd.pitch, cmd.buttons) for cmd in commands],
                                 [(*frozen[3:], 0)] * 2 + [held])
                self.assertNotIn(zen.body, fight.frost.samples)
                self.assertFalse(fight.is_stunned(zen))
                charge = weapon.states[charge_node.state]
                self.assertTrue(charge.active)
                self.assertEqual(charge.start, deadline * 16)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                for _ in range(130):
                    run.step({zen: held})
                for _ in range(60):
                    run.step({zen: self.aim_at(run, zen, mei, 0)})
                hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(mei.body, 48)] * 5)
                self.assertEqual(weapon.vars[53].value(), ammo - 5)

    def test_actual_spray_does_not_build_frost_during_transcendence(self):
        run, mei, zen = self.arena()
        fight = run.match.combat
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        for _ in range(150):
            run.step({mei: self.aim_at(run, mei, zen, FIRE)})
            self.assertNotIn(zen.body, fight.frost.samples)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertFalse(fight.is_stunned(zen))

    def test_actual_transcendence_cleans_partial_frost_before_freeze(self):
        run, mei, zen = self.arena()
        fight = run.match.combat
        for _ in range(30):
            run.step({mei: self.aim_at(run, mei, zen, FIRE)})
        self.assertGreater(fight.frost.samples[zen.body][0], 0)
        self.assertFalse(fight.is_stunned(zen))
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertNotIn(zen.body, fight.frost.samples)
        self.assertEqual(zen.body_script.component.entity_var(2427).value(), 0)

    def test_actual_projection_clears_freeze_without_stopping_the_ally(self):
        run, mei, zen = self.arena(ally=True)
        fight = run.match.combat
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        zarya.mover.position = [x+2, y, z+1]
        zarya.mover.body = None
        for _ in range(30):
            run.step({})
        for _ in range(160):
            run.step({mei: self.aim_at(run, mei, zen, FIRE)})
            if fight.is_stunned(zen):
                break
        self.assertTrue(fight.is_stunned(zen))
        angles = aim(fight._eye(zarya), (x, y+1.55, z))
        run.step({zarya: (*angles, 0x10)})
        self.assertIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        self.assertFalse(fight.is_stunned(zen))
        self.assertNotIn(zen.body, fight.frost.samples)
        before = zen.mover.position[2]
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            zen.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(zen.mover.position[2], before+.15)

    def test_one_actual_spray_passes_front_body_once_and_respects_wall(self):
        for mode in ("clear", "transcendence", "wall", "deflect"):
            with self.subTest(mode=mode):
                self.spray_line(mode)

    def spray_line(self, mode):
        game_map = replace(PRACTICE_RANGE, free_for_all=True, team_sizes=(8,))
        run = Run(game_map, ("Mei", "Near", "Far"))
        mei, near, far = run.players
        run.match.bots.clear()
        x, y, z = mei.mover.position
        for player, hero, distance in (
            (mei, mei_frost.HERO, 0), (near, 0x02E0000000000029 if mode == "deflect" else ZEN, 2),
            (far, ZEN, 4),
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position = [x, y, z+distance]
            player.mover.body = None
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        held = {}
        if mode == "transcendence":
            near.body_script.set_ult_charge(near.body_script.component.entity_var(338).value())
            run.step({near: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(near))
        elif mode == "wall":
            run.match.collision_world = World(
                box(x-20, x+20, 0, y, z-20, z+20) + wall(x-4, z+3, x+4, z+3)
            )
        elif mode == "deflect":
            held = {near: (-32768, 0, 0x10)}
            for _ in range(8):
                run.step(held)
            self.assertTrue(any(state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                                for instance in near.body_script.component.instances.values()
                                for state in instance.states.values()))
        run.step({**held, mei: self.aim_at(run, mei, far, FIRE)})
        for _ in range(30):
            run.step(held)
        hits = mei.client.messages(combat.HIT)
        ids = [hit["+0x78"]["+0x0"]["+0x0"] for hit in hits]
        expected = ([] if mode == "transcendence" else [near.body]) + ([] if mode == "wall" else [far.body])
        self.assertEqual(ids, expected)
        self.assertEqual(near.body in fight.frost.samples, mode != "transcendence")
        self.assertEqual(far.body in fight.frost.samples, mode != "wall")
