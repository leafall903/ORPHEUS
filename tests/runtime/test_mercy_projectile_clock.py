"""Mercy command batches share one projectile flight and damage clock."""

import unittest
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE

from ow174.game import bodytrace, combat, movelog
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command, FIRE
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY


class MercyProjectileClockTests(unittest.TestCase):
    def test_birth_clock_is_preserved_as_causal_body_state(self):
        run = Run(SCENE)
        mercy, = run.players
        run.match.switch_hero(mercy, heroes()[MERCY])
        script = mercy.body_script
        before = snapshot_digest(export_body_vm(script))
        world, frame = script.mercy_birth
        script.mercy_birth = world + 1, frame
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)

    def test_batches_preserve_flight_and_timeout(self):
        self.check_batches(hit=False)

    def test_batches_preserve_target_damage_and_hit_frame(self):
        self.check_batches(hit=True)

    def check_batches(self, *, hit):
        for batch in (1, 8, 32):
            with self.subTest(batch=batch):
                with patch.object(bodytrace, "ENABLED", False), patch.object(movelog, "ENABLED", False):
                    run = Run(SCENE, ("Mercy", "Enemy"))
                    mercy, enemy = run.players
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    match.switch_hero(mercy, heroes()[MERCY])
                    for _ in range(35):
                        run.step({})
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame)
                    mercy.take_commands([Command(run.frame, action=2)])
                    for _ in range(35):
                        run.step({})
                    x, y, z = mercy.mover.position
                    enemy.mover.position[:] = (x, y, z + 40)
                    enemy.mover.body = None
                    mercy.team, enemy.team = 0, 1
                    target = next(t for t in match.combat._targets() if t.owner is enemy)
                    vitals = match.combat.vitals_of(target)
                    yaw, pitch = aim(match.combat._eye(mercy), (x, y + 0.8, z + 40))
                    if not hit:
                        pitch = -4000
                    first = run.frame + 1
                    rocket = None
                    for start in range(first, first + 32, batch):
                        end = start + batch - 1
                        match.update(end * combat.TICK, end)
                        mercy.take_commands([
                            Command(frame, yaw=yaw, pitch=pitch, buttons=FIRE if frame == first else 0)
                            for frame in range(start, end + 1)
                        ])
                        if rocket is None:
                            rocket, = match.combat.rockets
                            self.assertEqual(rocket.last_tick, first)
                    query = first + 48
                    match.update(query * combat.TICK, query)
                    self.assertAlmostEqual(rocket.flown, 0.768)
                    self.assertAlmostEqual(rocket.left, 1.232)
                    for tick in range(query + 1, first + 127):
                        match.update(tick * combat.TICK, tick)
                        if rocket not in match.combat.rockets:
                            break
                    self.assertNotIn(rocket, match.combat.rockets)
                    self.assertEqual(tick, first + (50 if hit else 125))
                    self.assertEqual(vitals.total, 180 if hit else 200)
                    hits = mercy.client.messages(combat.HIT)
                    self.assertEqual(len(hits), int(hit))
                    if hit:
                        self.assertEqual(hits[0]["+0x84"], first + 50)
                        self.assertEqual(hits[0]["+0x78"]["+0x4"], 20)
