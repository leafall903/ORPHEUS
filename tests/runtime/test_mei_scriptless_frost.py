"""Mei frost must reach original training targets without body scripts."""
import json
import unittest
from pathlib import Path

from test_game_combat import Run, aim

from ow174.game import combat, mei
from ow174.game.content import heroes


class ScriptlessFrostTests(unittest.TestCase):
    def scene(self):
        run = Run()
        owner = run.players[0]
        self.assertTrue(run.match.switch_hero(owner, heroes()[mei.HERO]))
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        target = next(target for target in fight._targets()
                      if getattr(target.owner, "body_script", True) is None
                      and target.team != owner.team
                      and target.feet[1] >= owner.mover.position[1])
        return run, owner, target

    def test_actual_spray_freezes_enemy_training_bot_and_reports_hits(self):
        run, owner, target = self.scene()
        fight = run.match.combat
        x, y, z = target.feet
        fight.walls.place_player(owner, (x, y, z - 3))
        health = fight.vitals_of(target).total
        for _ in range(110):
            target = next(t for t in fight._targets() if t.entity == target.entity)
            facing = aim(fight._eye(owner), (target.feet[0], target.feet[1] + .9, target.feet[2]))
            run.step({owner: (*facing, 1)})
            if fight.frozen(target.owner):
                break
        self.assertTrue(fight.frozen(target.owner))
        self.assertLess(fight.vitals_of(target).total, health)
        self.assertTrue(owner.client.messages(combat.HIT))

    def hit(self, run, owner, target):
        fight = run.match.combat
        self.assertTrue(fight._damage(
            owner, target, 1, False, combat.MEI_SPRAY_LOADOUT, fight.tick,
            award_ult=False, source_scaled=True,
        ))

    def test_damage_register_accumulates_freeze_without_target_script(self):
        contract = json.loads((Path(__file__).resolve().parents[2]
                               / 'fixtures/behavior/mei-scriptless-frost-v1.json').read_text())
        run, owner, target = self.scene()
        fight = run.match.combat
        health = fight.vitals_of(target).total
        self.hit(run, owner, target)
        self.assertAlmostEqual(fight.frost(target.owner)[0], contract['expected']['first_hit_progress'])
        self.assertFalse(fight.frozen(target.owner))
        for _ in range(contract['input']['hit_count'] - 1):
            self.hit(run, owner, target)
        self.assertEqual(health - fight.vitals_of(target).total, contract['expected']['registered_damage'])
        self.assertTrue(fight.frozen(target.owner))
        deadline = fight.frost_hits[target.entity].frozen_until
        self.assertGreater(deadline, fight.tick)
        self.assertEqual(fight.movement_pause_until(target.owner), deadline)
        self.hit(run, owner, target)
        self.assertEqual(fight.frost_hits[target.entity].frozen_until, deadline)
        for _ in range(deadline - run.frame - 1):
            run.step({})
        self.assertTrue(fight.frozen(target.owner))
        run.step({})
        self.assertFalse(fight.frozen(target.owner))
        self.assertNotIn(target.entity, fight.frost_sources)
        self.assertLessEqual(fight.movement_pause_until(target.owner), fight.tick)

    def test_partial_frost_expires_on_world_clock_without_target_script(self):
        run, owner, target = self.scene()
        fight = run.match.combat
        self.hit(run, owner, target)
        for _ in range(64):
            run.step({})
        self.assertIsNone(fight.frost(target.owner))
        self.assertNotIn(target.entity, fight.frost_hits)
        self.assertNotIn(target.entity, fight.frost_sources)
