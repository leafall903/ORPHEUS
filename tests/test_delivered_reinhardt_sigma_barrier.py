"""Actual Sigma Experimental Barrier against Reinhardt's combat inputs."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_delivered_reinhardt as fixtures

from ow174.game import sigma_barrier
from ow174.game.script import expr, graph
from ow174.game.content import SOLDIER, heroes

PROFILE = Path(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL", "missing-sigma-model"))


@unittest.skipUnless(PROFILE.is_file(), "prepared Sigma C015 mesh required")
class SigmaView:
    """Fixture reads the one actual native actor; no parallel state or pool."""
    def __init__(self, actor):
        self.actor = actor

    def __getattr__(self, name):
        return getattr(self.actor, name)

    health = property(lambda self: self.actor.vitals.total)
    mode = property(lambda self: self.actor.phase)
    body = property(lambda self: self.actor.entity)
    source = property(lambda self: self.actor.instance)
    speed = property(lambda self: expr.to_float(self.actor.instance.evaluate(
        graph.graph(0x1A2A).node(22).field("m_speed"))))

    def active(self):
        return self.actor.active

    def instance(self):
        return self.actor.child

    def set_health(self, amount):
        self.actor.vitals.damage(max(0., self.actor.vitals.total - amount), armour_scalar=1.)
        self.actor.sync_pool()


class ReinhardtSigmaBarrierTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def step(self, frames=1, *, rein=0, sigma=0):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, rein), self.enemy: (-32768, 0, sigma)})

    def deploy(self, *, allied=False):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_SIGMA_BARRIER_MODEL": str(PROFILE)}))
        fixtures.HammerTests.setUp(self)
        if allied:
            self.match.game_map = replace(self.match.game_map, free_for_all=False)
            self.rein.team, self.enemy.team, self.other.team = 0, 0, 1
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[sigma_barrier.HERO]))
        self.place(self.enemy, (0, 1, 8))
        self.place(self.other, (20, 1, 0))
        self.step(30)
        actor = SigmaView(next(a for a in self.match.combat.sigma_barriers.values() if a.owner is self.enemy))
        self.assertEqual(actor.health, 700)
        self.assertEqual(actor.speed, 16.5)
        self.assertFalse(actor.active())
        self.step(15, sigma=2)
        self.assertEqual(actor.mode, 2)
        self.step()
        self.assertEqual(actor.mode, 3)
        self.assertTrue(actor.active())
        return actor

    def test_actual_fire_strike_penetrates_deployed_barrier_once(self):
        actor = self.deploy()
        self.step(rein=16)
        self.step(70)
        self.assertEqual(actor.health, 600)
        self.assertEqual(self.health(self.enemy), 300)
        self.assertTrue(actor.active())

    def test_actual_hammer_damages_barrier_without_reaching_owner(self):
        actor = self.deploy()
        self.place(self.rein, (0, 1, actor.position[2] - 2.5))
        self.step(rein=1)
        self.step(48)
        self.assertEqual(actor.health, 615)
        self.assertEqual(self.health(self.enemy), 400)

    def test_actual_charge_crosses_barrier_and_pins_sigma(self):
        actor = self.deploy()
        self.step(rein=8)
        fight = self.match.combat
        for _ in range(90):
            self.step()
            if fixtures.pinned(fight, self.enemy.body):
                break
        self.assertTrue(fixtures.pinned(fight, self.enemy.body))
        self.assertEqual(actor.health, 700)
        self.assertEqual(self.health(self.enemy), 400)

    def test_hammer_and_melee_alias_use_sigma_team_at_contact(self):
        for button in (1, 0x800):
            for starts_hostile in (False, True):
                with self.subTest(button=button, starts_hostile=starts_hostile):
                    actor = self.deploy(allied=not starts_hostile)
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team, self.enemy.team = 0, int(starts_hostile)
                    self.place(self.rein, (0, 1, actor.position[2] - 2.5))
                    self.step(rein=button)
                    self.step(15)
                    self.assertEqual(actor.health, 700)
                    self.enemy.team = int(not starts_hostile)
                    self.step(45)
                    self.assertTrue(actor.active())
                    self.assertEqual(actor.health, 700 if starts_hostile else 615)
                    self.assertEqual(self.health(self.enemy), 400)

    def test_allied_sigma_barrier_passes_fire_strike_to_enemy(self):
        actor = self.deploy(allied=True)
        self.place(self.other, (0, 1, 12))
        self.step(rein=16)
        self.step(70)
        self.assertEqual(actor.health, 700)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(self.health(self.other), 100)

    def test_deployed_barrier_uses_sigma_team_at_attack_contact(self):
        for button in (16, 32):
            for starts_hostile in (False, True):
                with self.subTest(button=button, starts_hostile=starts_hostile):
                    actor = self.deploy(allied=not starts_hostile)
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team, self.enemy.team, self.other.team = 0, int(starts_hostile), 1
                    self.place(self.other, (0, 1, 12))
                    if button == 32:
                        self.rein.body_script.set_ult_charge(1540)
                    self.step(rein=button)
                    fight = self.match.combat
                    for _ in range(60):
                        released = fight.rockets if button == 16 else fight.shatters
                        if released:
                            break
                        self.step()
                    self.assertTrue(released)
                    self.assertEqual(actor.health, 700)
                    self.enemy.team = int(not starts_hostile)
                    self.step(65)
                    self.assertTrue(actor.active())
                    if button == 16:
                        self.assertEqual(actor.health, 700 if starts_hostile else 600)
                        self.assertEqual(self.health(self.enemy), 400 if starts_hostile else 300)
                        self.assertEqual(self.health(self.other), 100)
                    else:
                        self.assertEqual(actor.health, 700 if starts_hostile else 650)
                        self.assertEqual(self.health(self.enemy), 400)
                        self.assertEqual(self.health(self.other), 150 if starts_hostile else 200)
                        self.assertEqual(fixtures.controlled(fight, self.other.body), starts_hostile)

    def test_shield_bash_ignores_its_allied_deployment(self):
        actor = self.deploy()
        fixtures.shield_bash_at_deployment(self, actor.actor, actor.position[2] - 4, 1, allied=True)

    def test_shield_bash_stays_stopped_after_actual_sigma_recall(self):
        actor = self.deploy()
        hold, stopped = fixtures.shield_bash_at_deployment(self, actor.actor, actor.position[2] - 4, 1)
        self.run.step({**hold, self.enemy: (-32768, 0, 2)})
        self.assertFalse(actor.active())
        for _ in range(12):
            self.run.step(hold)
        self.assertEqual(tuple(self.other.mover.position), stopped)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(fixtures.controlled(self.match.combat, self.rein.body))
        self.assertEqual(self.other.client.messages(fixtures.combat.HIT), [])

    def test_actual_earthshatter_front_stays_blocked_after_sigma_recalls(self):
        actor = self.deploy()
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        fight = self.match.combat
        for _ in range(70):
            self.step()
            if actor.health < 700:
                break
        self.assertEqual(actor.health, 650)
        self.assertIn(self.rein.body, fight.shatters)
        self.step(sigma=2)
        self.assertFalse(actor.active())
        self.step(65)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertFalse(fixtures.controlled(fight, self.enemy.body))
        self.assertNotIn(self.rein.body, fight.shatters)

    def test_recall_leaves_only_the_previously_stopped_earthshatter_rays_blocked(self):
        actor = self.deploy()
        fight = self.match.combat
        origin = (-2, 1, -4.2)
        self.place(self.rein, origin)
        angle = math.radians(25)
        dx, dz = math.sin(angle), math.cos(angle)
        self.place(self.other, (origin[0] + 12 * dx, 1, origin[2] + 12 * dz))
        contact = actor.intersection((origin[0], 1.1, origin[2]), (dx, 0, dz), 12)
        self.assertGreater(contact, 9.6)

        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        for _ in range(70):
            self.step()
            if actor.health < 700:
                break
        wave = fight.shatters[self.rein.body]
        self.assertEqual(actor.health, 650)
        self.assertIn(61, wave.blocked)
        self.assertNotIn(100, wave.blocked)
        stopped = wave.fronts[61]

        self.step(sigma=2)
        self.assertFalse(actor.active())
        self.assertLess(wave.travelled, contact)
        self.step(35)
        self.assertEqual(wave.fronts[61], stopped)
        self.assertNotIn(100, wave.blocked)
        self.assertEqual(self.health(self.other), 150)
        self.assertTrue(fixtures.controlled(fight, self.other.body))

    def test_recall_waits_then_restores_damage_taken_from_fire_strike(self):
        actor = self.deploy()
        self.step(rein=16)
        self.step(70)
        self.assertEqual(actor.health, 600)
        self.step(sigma=2)
        self.assertEqual(actor.mode, 1)
        self.step(120)
        self.assertEqual(actor.health, 600)
        self.step(85)
        self.assertEqual(actor.health, 700)
        self.step(15, sigma=2)
        self.step()
        self.assertTrue(actor.active())
        self.assertEqual(actor.health, 700)

    def test_actual_fire_strike_break_locks_barrier_until_it_recovers(self):
        actor = self.deploy()
        actor.set_health(50)
        self.step(rein=16)
        for _ in range(70):
            self.step()
            if not actor.active():
                break
        self.assertEqual(actor.health, 0)
        self.assertEqual(actor.mode, 1)
        self.assertGreater(actor.source.vars[61].value(), 4)
        self.step(40, sigma=2)
        self.assertFalse(actor.active())
        self.assertEqual(self.health(self.enemy), 300)
        self.step(160)
        self.assertGreater(actor.health, 0)
        self.assertGreater(actor.source.vars[61].value(), 0)
        self.step(15, sigma=2)
        self.step()
        self.assertFalse(actor.active())
        self.step(130)
        self.step(15, sigma=2)
        self.step()
        self.assertTrue(actor.active())
        restored = actor.health
        self.place(self.rein, (0, 1, actor.position[2] - 2.5))
        self.step(rein=1)
        self.step(48)
        self.assertEqual(actor.health, restored - 85)
        self.assertEqual(self.health(self.enemy), 300)

    def test_actual_earthshatter_front_stays_blocked_after_break(self):
        actor = self.deploy()
        actor.set_health(30)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        fight = self.match.combat
        for _ in range(70):
            self.step()
            if not actor.active():
                break
        self.assertEqual(actor.health, 0)
        self.assertEqual(actor.mode, 1)
        self.assertGreater(actor.source.vars[61].value(), 4)
        self.assertIn(self.rein.body, fight.shatters)
        self.step(65)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertFalse(fixtures.controlled(fight, self.enemy.body))
        self.assertNotIn(self.rein.body, fight.shatters)

    def test_sigma_switch_retires_deployed_barrier_before_released_shatter_arrives(self):
        actor = self.deploy()
        old_body = self.enemy.body
        fight = self.match.combat
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        for _ in range(60):
            if self.rein.body in fight.shatters:
                break
            self.step()
        self.assertIn(self.rein.body, fight.shatters)
        self.assertEqual(actor.health, 700)

        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.assertNotEqual(self.enemy.body, old_body)
        self.assertNotIn(old_body, {a.owner_body: a for a in fight.sigma_barriers.values()})
        self.assertFalse(actor.active())
        self.assertIsNone(actor.instance())
        self.place(self.enemy, (0, 1, 8))
        for _ in range(65):
            self.step()
            if fixtures.controlled(fight, self.enemy.body):
                break
        self.assertTrue(fixtures.controlled(fight, self.enemy.body))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(actor.health, 700)
