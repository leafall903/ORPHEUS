"""Hook retains the real owned mech mover and its existing body VM."""
import unittest
import test_dva_delivery_created_body as fixtures
from test_game_combat import aim
from ow174.game.content import heroes
from ow174.game.body_vm_snapshot import export_component_vm,snapshot_digest

class OwnedMechHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CreatedBodyTests.setUpClass()

    def test_actual_hook_uses_one_owned_mech_mover_and_preserves_vm(self):
        case=fixtures.CreatedBodyTests();r,p,h,_,b=case.scenario();r.match.switch_hero(h,heroes()[0x02E0000000000040]);f=r.match.combat;x,y,z=b.position;f.walls.place_player(h,(x,y,z-8));mover=b.control_mover;old_impulse=b.impulse_mover
        for _ in range(40):r.step({})
        t=next(t for t in f._targets() if t.owner is b);v=f.vitals_of(t);before=v.total;a,c=t.segment();look=aim(f._eye(h),tuple((l+q)/2 for l,q in zip(a,c)));initial=tuple(b.position);r.step({h:(*look,8)});caught=False;stored=False
        for _ in range(110):
         r.step({h:(*look,0),p:(0,0,1)})
         if f.hooks.controlled(b.entity):
          caught=True;flight=f.hooks.flights[h.body];assert flight.npc_mover is mover and b.control_mover is mover;assert b.impulse_mover is old_impulse
          if not stored:
           print('actual controlled Mech caught; sole mover identity retained',flush=True)
           snapshot_digest(export_component_vm(b.body_script.component));stored=True
           print('active controlled Mech strict Component export passed',flush=True)
         if caught and not f.hooks.flights:break
        assert caught
        assert before-v.total==25,(before,v.total)
        assert abs(b.position[2]-h.mover.position[2])<3.1,(b.position,h.mover.position)
        assert b.control_mover is mover and b.impulse_mover is old_impulse
        assert not f.hooks.flights
        print('actual created controlled DVa: hook25 armor loss, solecontrol_mover identity, no impulse clone, endpoint/release passed',initial,b.position,'clock',b.clock_offset,flush=True)
