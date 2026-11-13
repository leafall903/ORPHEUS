"""Actual Junkrat releases feeding the shared candidate; no native acceptance claim."""
import math
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim
from ow174.game import combat, defenses, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command, FIRE
from ow174.game.content import find_hero


class JunkratForceProducerR2Tests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).resolve().parents[2] / "fixtures/behavior/junkrat-force-producer-r2-v1.json"
        self.expected = json.loads(fixture.read_text(encoding="utf-8"))["expected"]
        self.run = Run(names=("Junkrat", "Reinhardt"))
        self.source, self.victim = self.run.players
        self.npcs = tuple(bot.entity for bot in self.run.match.bots)
        self.assertEqual(len(self.npcs), 15)
        for player, hero, birth, position in (
            (self.source, "Junkrat", 9000, (1000, 0, 1000)),
            (self.victim, "Reinhardt", 12000, (1000, 0, 1004.5)),
        ):
            player.commands.last_frame = birth
            self.run.match.switch_hero(player, find_hero(hero))
            player.mover.position = list(position)
            player.mover.body = None
            player.body_script.create_arrived = True
        self.run.match.collision_world = flat_world(0)
        self.fight = self.run.match.combat
        for _ in range(30):
            self.step()
        self.assertAlmostEqual(defenses.knockback_scale(self.victim), .7, places=6)

    def step(self, buttons=0, *, yaw=0, pitch=0, forward=0):
        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        offset = self.run.frame - 1000
        self.source.take_commands([Command(9000 + offset, buttons=buttons, yaw=yaw,
                                           pitch=pitch, forward=forward)])
        self.victim.take_commands([Command(12000 + offset)])

    def target(self):
        return next(t for t in self.fight._targets() if t.entity == self.victim.body)

    def health(self, player):
        target = next(t for t in self.fight._targets() if t.entity == player.body)
        return self.fight.vitals_of(target).total

    def assert_force(self, calls, horizontal, vertical):
        self.assertEqual(len(calls), 1)
        call, = calls
        x, y, z = call.args[1]
        self.assertAlmostEqual(math.hypot(x, z), horizontal, places=4)
        self.assertAlmostEqual(y, vertical, places=4)
        self.assertNotIn("impact_frame", call.kwargs)
        self.assertEqual(call.kwargs["impact_tick"], self.impact)
        self.assertEqual(call.kwargs["source_entity"], self.source.body)

    def test_actual_remote_mine_samples_once_resists_and_self_launches_then_returns_input(self):
        self.step(8, pitch=4096)
        for _ in range(49):
            self.step(pitch=4096)
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        x, y, z = mine.flight.position
        self.victim.mover.position[:] = (x, 0, z + 1.2)
        self.source.mover.position[:] = (x, 0, z - 1.2)
        before = self.health(self.victim)
        self.impact = self.run.frame + 1
        with patch.object(self.fight.knockbacks, "apply", wraps=self.fight.knockbacks.apply) as apply:
            self.step(2)
            for _ in range(3):
                if mine.entity not in self.fight.junkrat.mines:
                    break
                self.step()
        enemy = [c for c in apply.call_args_list if c.args[0].entity == self.victim.body]
        own = [c for c in apply.call_args_list if c.args[0].entity == self.source.body]
        self.assert_force(enemy, self.expected["mine"]["planar"], self.expected["mine"]["upward"])
        self.assert_force(own, self.expected["mine_self"]["planar"], self.expected["mine_self"]["upward"])
        self.assertEqual(before - self.health(self.victim), self.expected["mine"]["health_loss"])
        self.assertEqual(self.health(self.source), 200)
        self.assertNotIn(mine.entity, self.fight.junkrat.mines)
        for _ in range(200):
            self.step()
        self.assertAlmostEqual(self.source.mover.position[1], 0, delta=.05)
        position = tuple(self.source.mover.position)
        for _ in range(10):
            self.step(forward=127)
        self.assertGreater(math.dist(position, self.source.mover.position), .1)

    def test_actual_frag_hit_submits_one_world_timed_resisted_blast(self):
        yaw, pitch = aim(self.fight._eye(self.source),
                         (self.victim.mover.position[0], .9, self.victim.mover.position[2]))
        before = self.health(self.victim)
        with patch.object(self.fight.knockbacks, "apply", wraps=self.fight.knockbacks.apply) as apply:
            self.step(FIRE, yaw=yaw, pitch=pitch)
            for _ in range(20):
                self.step(yaw=yaw, pitch=pitch)
                if any(c.args[0].entity == self.victim.body for c in apply.call_args_list):
                    self.impact = self.run.frame
                    break
        calls = [c for c in apply.call_args_list if c.args[0].entity == self.victim.body]
        self.assert_force(calls, self.expected["frag"]["planar"], self.expected["frag"]["upward"])
        self.assertAlmostEqual(before - self.health(self.victim), self.expected["frag"]["health_loss"], places=4)

    def test_actual_tire_detonation_submits_one_resisted_force_with_authored_rings(self):
        script = self.source.body_script
        script.set_ult_charge(script.component.vars[338].value())
        self.step(0x20)
        tire, = self.fight.junkrat.tire.actors.values()
        while self.run.frame < tire.arm_tick:
            self.step()
        x, y, z = tire.mover.position
        self.victim.mover.position[:] = (x, 0, z + 8)
        scales = []
        explode = self.fight._explode

        def observe(rocket, point, **kwargs):
            target = self.target()
            distance = max(0, combat.segment_distance(point, *target.segment()) - target.radius)
            horizontal, _ = pharah.RocketImpulse(6, 0, 0, tire.blast.impulse_rings).scales(distance)
            scales.append(horizontal)
            return explode(rocket, point, **kwargs)

        before = self.health(self.victim)
        self.impact = self.run.frame + 1
        with patch.object(self.fight.knockbacks, "apply", wraps=self.fight.knockbacks.apply) as apply, \
                patch.object(self.fight, "_explode", side_effect=observe), \
                patch.object(self.fight, "_damage", wraps=self.fight._damage) as damage:
            self.step(FIRE)
        self.assertEqual(len(scales), 1)
        calls = [c for c in apply.call_args_list if c.args[0].entity == self.victim.body]
        self.assert_force(calls, 6 * scales[0] * .7, self.expected["tire"]["upward"])
        contacts = [call for call in damage.call_args_list if call.args[1].entity == self.victim.body]
        self.assertEqual(len(contacts), self.expected["tire"]["damaging_contacts"])
        self.assertGreater(self.health(self.victim), 0)
        self.assertLess(self.health(self.victim), before)
        self.assertNotIn(tire.entity, self.fight.junkrat.tire.actors)

    def tearDown(self):
        self.assertEqual(tuple(bot.entity for bot in self.run.match.bots), self.npcs)
