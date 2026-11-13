"""A real Mercy melee preserves viewed target identity and shared force clocks."""
import unittest
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE
from ow174.game import bodytrace, combat, movelog
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY


class MercyMeleeViewClockTests(unittest.TestCase):
    observation = None

    def test_actual_melee_hits_viewed_body_once_with_world_force_tick(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))
        run = Run(SCENE, ('Mercy', 'ViewedNear', 'ViewedFar'))
        mercy, viewed, other = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(mercy, heroes()[MERCY], start_frame=9000)
        mercy.commands.reset(last_frame=9000)
        mercy.team, viewed.team, other.team = 0, 1, 1
        birth = mercy.body_script.mercy_birth
        near, far = (0., 1., 1.5), (8., 1., 1.5)
        for offset in range(1, 42):
            swapped = offset > 33
            viewed.mover.position[:] = far if swapped else near
            other.mover.position[:] = near if swapped else far
            viewed.mover.body = other.mover.body = None
            tick = 1000 + offset
            match.update(tick * combat.TICK, tick)
            if offset < 41:
                mercy.take_commands([Command(9000 + offset)])
        fight = match.combat
        targets = {t.owner:t for t in fight._targets()}
        before = [fight.vitals_of(targets[p]).total for p in (viewed,other)]
        original_submit, original_apply = fight._submit_attack, fight.knockbacks.apply
        contacts, impulses = [], []
        def submit(shooter, frame, contact):
            if shooter is mercy and isinstance(contact, combat.MeleeContact):
                contacts.append({'sourceFrame':frame,'viewTick':contact.view_tick,
                                 'impactTick':contact.impact_tick})
            return original_submit(shooter,frame,contact)
        def apply(target, vector, **kwargs):
            impulses.append({'body':target.entity,'vector':list(vector),'kwargs':kwargs})
            return original_apply(target,vector,**kwargs)
        yaw,pitch=aim(fight._eye(mercy),(near[0],near[1]+1.,near[2]))
        with patch.object(fight,'_submit_attack',submit), patch.object(fight.knockbacks,'apply',apply):
            mercy.take_commands([Command(9041,yaw=yaw,pitch=pitch,buttons=0x800,extra=128)])
            mercy.take_commands([Command(9041,yaw=yaw,pitch=pitch,buttons=0x800,extra=128)])
        after=[fight.vitals_of(targets[p]).total for p in (viewed,other)]
        hits=mercy.client.messages(combat.HIT)
        self.__class__.observation={'birth':list(birth),'worldTick':match.tick,'sourceFrame':9041,
          'viewDelayMs':128,'expectedViewTick':1033,'viewedBody':viewed.body,'otherBody':other.body,
          'sourceBody':mercy.body,'before':before,'after':after,'contacts':contacts,'impulses':impulses,
          'hits':hits,'forceSerial':fight.knockbacks.serial}
        self.assertEqual(after,[before[0]-30,before[1]])
        self.assertEqual(contacts,[{'sourceFrame':9041,'viewTick':1033.,'impactTick':1041}])
        self.assertEqual(len(impulses),1)
        self.assertEqual(impulses[0]['body'],viewed.body)
        self.assertEqual(impulses[0]['kwargs'],{'impact_tick':1041,'source_entity':mercy.body})
        self.assertEqual([h['+0x78']['+0x0']['+0x0'] for h in hits],[viewed.body])
        self.assertEqual([h['+0x84'] for h in hits],[9041])
