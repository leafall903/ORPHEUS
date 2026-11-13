"""Committed boost survives release/death with independent caster owner labels."""
import json
import unittest
from pathlib import Path

from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE

from ow174.game import combat
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/mercy-boost-history-world-clock-v1.json'


class MercyBoostHistoryWorldClockTests(unittest.TestCase):
    def sample(self, owner_birth, *, caster_dies=False):
        case = json.loads(FIXTURE.read_text(encoding='utf-8'))
        run = Run(SCENE, ('Mercy', 'Shooter', 'Enemy'))
        mercy, shooter, enemy = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(mercy, heroes()[MERCY], start_frame=owner_birth)
        mercy.commands.reset(last_frame=owner_birth)
        mercy.team = shooter.team = 0
        enemy.team = 1
        fight = match.combat
        mercy.body_script.set_ult_charge(1820)
        for tick in range(case['birthWorldTick'] + 1, case['receiveWorldTick'] + 1):
            match.update(tick * combat.TICK, tick)
            point = shooter.mover.position
            yaw, pitch = aim(fight._eye(mercy), (point[0], point[1] + 1, point[2]))
            buttons = (0x20 if tick == case['ultimateWorldTick'] else
                       SECONDARY if tick < case['boostUntilExclusive'] else 0)
            if not fight.is_dead(mercy):
                mercy.take_commands([Command(owner_birth + tick - case['birthWorldTick'],
                                             yaw=yaw, pitch=pitch, buttons=buttons)])
            if caster_dies and tick == case['casterDeathWorldTick']:
                target = next(t for t in fight._targets() if t.owner is mercy)
                fight._damage(enemy, target, 500, False, None, tick)
        attack_frame = case['lateAttackWorldTick']
        recorded = fight.support_effects.historical_boosts(mercy.body, attack_frame)
        self.assertIsNotNone(recorded)
        self.assertAlmostEqual(recorded[shooter.body], 0.3)
        target = next(t for t in fight._targets() if t.owner is enemy)
        health = fight.vitals_of(target)
        before = health.total
        hits = len(shooter.client.messages(combat.HIT))
        point = enemy.mover.position
        yaw, pitch = aim(fight._eye(shooter), (point[0], point[1] + 0.8, point[2]))
        command = Command(attack_frame, yaw=yaw, pitch=pitch, buttons=FIRE)
        shooter.take_commands([command])
        self.assertAlmostEqual(before - health.total, case['expectedDamage'], places=4)
        self.assertEqual(len(shooter.client.messages(combat.HIT)) - hits, case['expectedHitCount'])
        after = health.total
        shooter.take_commands([command])
        self.assertEqual(after - health.total, case['expectedDuplicateDamage'])
        self.assertFalse(fight.pending_attacks)
        self.assertFalse(mercy.body_script_failed)

    def test_actual_late_rifle_keeps_committed_boost_after_release_across_owner_epochs(self):
        for birth in (1000, 9000):
            with self.subTest(owner_birth=birth):
                self.sample(birth)

    def test_actual_late_rifle_keeps_committed_boost_after_caster_death_across_owner_epochs(self):
        for birth in (1000, 9000):
            with self.subTest(owner_birth=birth):
                self.sample(birth, caster_dies=True)
