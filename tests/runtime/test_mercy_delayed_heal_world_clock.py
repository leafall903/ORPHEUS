"""Actual delayed Mercy beam input against a real hostile Ana grenade interval."""
import unittest
from test_game_combat import Run,aim
from test_game_mercy_staff import SCENE
from test_game_ana import ANA,grenade_aim
from ow174.game.commands import Command,FIRE
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY

class MercyDelayedHealWorldClockTests(unittest.TestCase):
    def sample(self,anti,commands=1):
        run=Run(SCENE,('Mercy','AnaRecipient','HostileAna'))
        mercy,recipient,ana=run.players
        match=run.match
        match.bots.clear();match._pending_bot_scripts.clear()
        match.switch_hero(mercy,heroes()[MERCY],start_frame=9000)
        mercy.commands.reset(last_frame=9000)
        match.switch_hero(recipient,heroes()[ANA]);match.switch_hero(ana,heroes()[ANA])
        mercy.team=recipient.team=0;ana.team=1
        fight=match.combat
        target=next(t for t in fight._targets() if t.owner is recipient)
        for tick in range(1001,1401):
            match.update(tick*.016,tick)
            pos=recipient.mover.position
            yaw,pitch=aim(fight._eye(mercy),(pos[0],pos[1]+1.,pos[2]))
            mercy.take_commands([Command(9000+tick-1000,yaw=yaw,pitch=pitch,buttons=FIRE)])
            direction=grenade_aim(run,ana,(pos[0],pos[1]+.8,pos[2])) if anti and tick==1200 else (0,0)
            ana.take_commands([Command(tick,yaw=direction[0],pitch=direction[1],buttons=0x10 if anti and tick==1200 else 0)])
        match.update(1500*.016,1500)
        health=fight.vitals_of(target)
        if not anti:
            health.damage(60);fight._sync_body_health(recipient,health)
        self.assertEqual(health.total,140)
        self.assertEqual(fight.knockbacks.world_frame(mercy,9401,.016),1401)
        self.assertEqual(fight.healing.factor(target,end_tick=1401),0 if anti else 1)
        prior_stat=mercy.stats.values.copy()
        for frame in range(9401,9401+commands):
            fight.healing.prune_filters(1500)
            mercy.take_commands([Command(frame,yaw=yaw,pitch=pitch,buttons=FIRE)])
        self.assertEqual(mercy.commands.last_frame,9400+commands)
        self.assertEqual(mercy.body_script.frame,9400+commands)
        self.assertFalse(mercy.body_script_failed)
        if anti:self.assertEqual(mercy.stats.values,prior_stat)
        return health.total

    def test_real_delayed_beam_respects_historical_antiheal(self):
        self.assertEqual(self.sample(True),140)

    def test_real_delayed_beam_without_antiheal_keeps_normal_healing(self):
        self.assertAlmostEqual(self.sample(False),140.88)

    def test_late_pose_receipt_does_not_shift_next_staff_event_or_prune_boundary(self):
        self.assertEqual(self.sample(True,commands=2),140)
