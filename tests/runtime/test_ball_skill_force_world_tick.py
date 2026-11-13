"""Actual Ball skills dispatch one force in world time on shifted owner epochs.

The pre-existing force direction/strength policies are not native acceptance.
Shared receiver/transport correctness is owned by0042 and is not retested here.
"""
import json
import unittest
from unittest.mock import patch

import test_delivered_ball_mine_factory as mines
import test_delivered_wrecking_ball_combat as scenarios
import test_delivered_wrecking_ball_fireball as fireball
import test_delivered_wrecking_ball_grapple as grapple
import test_delivered_wrecking_ball_piledriver as piledriver
from ow174.game import world
from ow174.game.commands import Command


def shifted_commands(run, source, enemy):
    for player, frame in ((source, 9000), (enemy, 12000)):
        position = tuple(player.mover.position)
        player.commands.last_frame = frame
        run.match.switch_hero(player, player.hero)
        player.mover.position[:] = position
        created = player.body_script.created(world.EntityUpdate(player.body, world.OP_CREATE))
        created.stream.arrived(0)

    def step(commands):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        for player in run.players:
            yaw, pitch, buttons = commands.get(player, (0, 0, 0))
            player.take_commands([Command(player.body_script.frame + 1, yaw=yaw, pitch=pitch,
                                          buttons=buttons, extra=0)])

    run.step = step
    for _ in range(40):
        run.step({})


def observe(fight, events):
    original = fight.knockbacks.apply

    def apply(target, impulse, **kwargs):
        accepted = original(target, impulse, **kwargs)
        owner = target.owner
        velocity = owner.velocity if hasattr(owner, 'velocity') else owner.mover.velocity
        events.append({'world': fight.match.tick, 'target': target.entity, 'impulse': tuple(impulse),
                       'accepted': accepted, 'postVelocity': tuple(velocity), **kwargs})
        return accepted

    return patch.object(fight.knockbacks, 'apply', apply)


class SkillForceWorldTickTests(unittest.TestCase):
    def dispatch(self, events, player):
        self.assertEqual(len(events), 1, events)
        event = events[0]
        self.assertTrue(event['accepted'])
        self.assertNotIn('impact_frame', event)
        self.assertEqual(event['source_entity'], player.body)
        self.assertEqual(event['impact_tick'], event['world'])
        self.assertLess(event['impact_tick'], player.body_script.ball_birth[1])

    def test_actual_ctrl_landing_hits_npc_once_without_source_epoch_delay(self):
        run, player, enemy, target, _ = scenarios.BallCombatTests().scenario(npc=True, distance=1.5)
        shifted_commands(run, player, enemy)
        piledriver.lift(run, player)
        events = []
        with observe(run.match.combat, events):
            for at in range(100):
                run.step({player: (0, 0, 128 if at < 10 else 0)})
                if events:
                    break
        self.dispatch(events, player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 100)
        run.step({})
        self.assertGreater(target.owner.velocity[1], 0)
        self.assertFalse(getattr(run.match.combat.knockbacks, 'pending_npc', {}))
        print('BALL_FORCE_CTRL ' + json.dumps(events))

    def test_actual_grapple_swing_fireball_hits_player_once(self):
        run, player, enemy, target, _ = scenarios.BallCombatTests().scenario(distance=6)
        shifted_commands(run, player, enemy)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        fireball.prepare_swing(run, player)
        events = []
        with observe(run.match.combat, events):
            for _ in range(100):
                grapple.hold(run, player, 1)
                if events:
                    break
        self.dispatch(events, player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 150)
        self.assertGreater(events[0]['impulse'][2], 20)
        for _ in range(3):
            if enemy.mover.velocity[1] > 0:
                break
            run.step({player: (0, 0, 2)})
        self.assertGreater(enemy.mover.velocity[1], 0)
        self.assertEqual(len(events), 1)
        print('BALL_FORCE_FIREBALL ' + json.dumps(events))

    def test_actual_q_mine_arming_and_contact_dispatch_once_with_caster_source(self):
        fixture = mines.MineTests()
        run, player, enemy = fixture.scenario()
        shifted_commands(run, player, enemy)
        mine = fixture.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        fireball.relocate(run, enemy, (x, y, z + 1))
        enemy.mover.velocity = (0, 0, 0)
        target = fixture.target(run, enemy)
        events = []
        with observe(run.match.combat, events):
            for _ in range(4):
                run.step({})
                if events:
                    break
        self.dispatch(events, player)
        self.assertTrue(mine.triggered)
        self.assertEqual(run.match.combat.vitals_of(target).total, 70)
        self.assertGreater(enemy.mover.velocity[1], 0)
        print('BALL_FORCE_MINE ' + json.dumps(events))

    def test_actual_ctrl_rejected_force_does_not_install_air_lock(self):
        run, player, enemy, _, _ = scenarios.BallCombatTests().scenario(distance=1.5)
        shifted_commands(run, player, enemy)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        piledriver.lift(run, player)
        fight = run.match.combat
        with patch.object(fight.knockbacks, 'apply', return_value=False) as apply:
            for at in range(100):
                run.step({player: (0, 0, 128 if at < 10 else 0)})
                if apply.called:
                    break
        apply.assert_called_once()
        self.assertEqual(fight.vitals_of(target).total, 100)
        self.assertNotIn(target.entity, fight.pile_air_locks)
