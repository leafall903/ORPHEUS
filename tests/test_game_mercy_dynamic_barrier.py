"""Actual Mercy blaster contacts against Reinhardt's original shield state."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_deflection as defense
from test_game_combat import aim

from ow174.game import stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE

REINHARDT = 0x02E0000000000007


class MercyDynamicBarrierTests(unittest.TestCase):
    def setUp(self):
        default = Path(__file__).resolve().parents[3] / "0007/runtime-inputs/reinhardt_collision_174.json"
        path = Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", default))
        if not path.is_file():
            self.skipTest("prepared 104319 Reinhardt model descriptor required")
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(path)}))

    def shot(self, *, behind=False, release=False, break_shield=False):
        base = defense.MercyDeflectionTests()
        f, graph = base.fixture(REINHARDT, 0x0095)
        self.addCleanup(base.doCleanups)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        x, y, z = f.mercy.mover.position
        yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        if behind:
            yaw = yaw % 65536 - 32768
        for _ in range(6):
            f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
        pet = graph.node_state(graph.graph.node(51))
        self.assertTrue(pet.active)
        buttons = 0 if release else SECONDARY
        for _ in range(6):
            f.run.step({f.enemy: (yaw, pitch, buttons)})
        self.assertEqual(pet.active, not release)
        barrier = f.fight.barriers[f.enemy.body]
        if break_shield:
            barrier.current = 20.0  # Arrange the last 20 durability before an actual projectile impact.
        durability = barrier.current
        x, y, z = f.enemy.mover.position
        attack_yaw, attack_pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.mercy: (attack_yaw, attack_pitch, FIRE), f.enemy: (yaw, pitch, buttons)})
        for _ in range(25):
            f.run.step({f.enemy: (yaw, pitch, buttons)})
        if behind or release:
            self.assertEqual(barrier.current, durability)
        else:
            self.assertEqual(barrier.current, durability - 20)
        if break_shield:
            self.assertFalse(pet.active)
            self.assertGreater(barrier.broken_until, f.fight.now)
            for _ in range(10):
                f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
            self.assertFalse(pet.active)
            for _ in range(120):
                f.run.step({f.enemy: (yaw, pitch, 0)})
            self.assertGreater(barrier.current, 0)
            for _ in range(200):
                f.run.step({f.enemy: (yaw, pitch, 0)})
            for _ in range(6):
                f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
            self.assertTrue(pet.active)
        return before - vitals.total

    def test_raised_frontal_barrier_prevents_damage_to_the_player(self):
        self.assertEqual(self.shot(), 0)

    def test_backside_or_released_barrier_does_not_protect_the_player(self):
        for options in ({"behind": True}, {"release": True}):
            with self.subTest(**options):
                self.assertEqual(self.shot(**options), 15)

    def test_last_shot_breaks_barrier_and_blocks_early_raise_until_recovery(self):
        self.assertEqual(self.shot(break_shield=True), 0)

    def test_reflected_blaster_breaks_friendly_barrier_then_next_shot_hits_body(self):
        base = defense.MercyDeflectionTests()
        f, shield = base.fixture(REINHARDT, 0x0095)
        self.addCleanup(base.doCleanups)
        reinhardt, genji = f.enemy, f.ally
        reinhardt.team, genji.team = genji.team, reinhardt.team
        self.assertTrue(f.match.switch_hero(genji, heroes()[defense.GENJI]))
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for player, distance in ((f.mercy, 0), (reinhardt, 5), (genji, 10)):
            player.mover.position[:] = (0, 1, distance)
            player.mover.body = None
        component = f.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        for _ in range(6):
            f.run.step({})
        f.run.step({reinhardt: (0, 0, SECONDARY), genji: (-32768, 0, 0x10)})
        poses = {reinhardt: (0, 0, SECONDARY), genji: (-32768, 0, 0)}
        for _ in range(5):
            f.run.step(poses)
        state = shield.node_state(shield.graph.node(51))
        self.assertTrue(state.active)
        self.assertTrue(deflect.state(10).active)
        barrier = f.fight.barriers[reinhardt.body]
        barrier.current = 20.0
        health = {player: base.health(f, player) for player in (f.mercy, reinhardt, genji)}
        initial_charge = charge.value()
        initial_credit = genji.stats.value(stats.HERO_DAMAGE)
        yaw, pitch = aim(f.fight._eye(f.mercy), (0, 2.2, 10))

        def fire():
            f.run.step({**poses, f.mercy: (yaw, pitch, FIRE)})
            for _ in range(32):
                f.run.step(poses)
            self.assertFalse(f.fight.rockets)
            self.assertFalse(f.fight.projectiles.shots)
            self.assertTrue(deflect.state(10).active)

        fire()
        self.assertEqual(barrier.current, 0)
        self.assertFalse(state.active)
        self.assertGreater(barrier.broken_until, f.fight.now)
        for player, before in health.items():
            self.assertEqual(base.health(f, player), before)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), initial_credit)

        fire()
        self.assertEqual(base.health(f, reinhardt), health[reinhardt] - 15)
        self.assertEqual(base.health(f, f.mercy), health[f.mercy])
        self.assertEqual(base.health(f, genji), health[genji])
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE) - initial_credit, 15)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge.value(), initial_charge)
