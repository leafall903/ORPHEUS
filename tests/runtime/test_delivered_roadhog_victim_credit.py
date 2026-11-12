"""Original0040 victim-side credit reaches its supported current weapon consumers."""
import unittest
import test_doomfist_combat as doom_fixture
import test_delivered_wrecking_ball_combat as ball_fixture
import test_delivered_wrecking_ball_grapple as swing_fixture
import test_delivered_wrecking_ball_fireball as fireball_fixture
import test_sigma_grasp_shields as grasp_fixture
from test_game_combat import Run, aim
from ow174.game.content import heroes

HOG = 0x02E0000000000040


def change_target(run, target):
    point = tuple(target.mover.position)
    run.match.switch_hero(target, heroes()[HOG])
    target.mover.position[:] = point
    target.mover.body = None
    fight = run.match.combat
    selected = next(t for t in fight._targets() if t.owner is target)
    return fight.vitals_of(selected)


def isolate(player):
    script = player.body_script
    script.ult_seconds = 0
    script.set_ult_charge(0)
    return script


class RoadhogVictimCreditTests(unittest.TestCase):
    def test_actual_hand_cannon_credits_original_victim_adjusted_loss(self):
        case = doom_fixture.HandCannonTests()
        case.prepare(3)
        vitals = change_target(case.run, case.b)
        script = isolate(case.a)
        case.fire()
        case.settle(14)
        self.assertEqual(vitals.base_total, 534)
        self.assertEqual(script.component.entity_var(342).value(), 49.5)

    def test_actual_cannons_and_melee_keep_original_hog_credit(self):
        for button, health, charge in ((1,595,3.75),(0x800,570,22.5)):
            with self.subTest(button=button):
                run, player, target, _, _ = ball_fixture.BallCombatTests().scenario(distance=1.5)
                vitals = change_target(run,target)
                script = isolate(player)
                x,y,z = target.mover.position
                look = aim(run.match.combat._eye(player),(x,y+1,z))
                run.step({player:(*look,button)})
                for _ in range(40):run.step({player:(*look,0)})
                self.assertEqual(vitals.base_total,health)
                self.assertEqual(script.component.entity_var(342).value(),charge)

    def test_actual_grapple_fireball_credits_original_hog_loss(self):
        run, player, target, _, _ = swing_fixture.scene(airborne=8)
        vitals = change_target(run,target)
        script = isolate(player)
        fireball_fixture.first_hit(run,player,target)
        self.assertEqual(vitals.base_total,550)
        self.assertEqual(script.component.entity_var(342).value(),37.5)

    def test_final_genji_quad_impact_uses_original_adjustment(self):
        run = Run(names=('Genji','Hog'))
        player,target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player,heroes()[0x02E0000000000029])
        vitals = change_target(run,target)
        script = isolate(player)
        fight = run.match.combat
        selected = next(t for t in fight._targets() if t.owner is target)
        fight._damage(player,selected,50,False,0x0B90000000000189,run.frame)
        self.assertEqual(vitals.base_total,550)
        self.assertEqual(script.component.entity_var(342).value(),37.5)

    def test_hog_slug_credits_only_actual_permanent_loss_beneath_grasp(self):
        run,target,player,vitals = grasp_fixture.SigmaShieldTests().granted()
        point = tuple(player.mover.position)
        run.match.switch_hero(player,heroes()[HOG])
        player.mover.position[:] = point
        player.mover.body = None
        script = isolate(player)
        x,y,z = target.mover.position
        look = aim(run.match.combat._eye(player),(x,y+.7,z))
        run.step({player:(*look,2)})
        for _ in range(20):run.step({player:(*look,0)})
        self.assertEqual(vitals.base_total,386)
        self.assertEqual(vitals.temporary_shields,0)
        self.assertEqual(script.component.entity_var(342).value(),14)

    def test_actual_soldier_authored_target_adjustment_is_not_doubled(self):
        run = Run(names=('Soldier','Hog'))
        player,target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        vitals = change_target(run,target)
        script = isolate(player)
        x,y,z = player.mover.position
        target.mover.position[:]=[x,y,z+3]
        target.mover.body=None
        look=aim(run.match.combat._eye(player),(x,y+.7,z+3))
        run.step({player:(*look,1)})
        self.assertEqual(vitals.base_total,580)
        self.assertEqual(script.component.entity_var(342).value(),15)
