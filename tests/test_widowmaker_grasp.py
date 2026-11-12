"""Widow commands against selected authored Sigma absorption/shield reconstruction."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat, sigma
from ow174.game.collision import World
from ow174.game.content import heroes


class _GraspMatch(_WidowmakerMatch):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[sigma.HERO]))
        self.place(7)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.step(60)

    def place(self, distance):
        self.b.mover.position[:] = (0, 0, distance)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)

    def step(self, count=1, *, widow=0, grasp=0, rear=False, height=1):
        for _ in range(count):
            direction = aim(self.run.match.combat._eye(self.a),
                            (0, self.b.mover.position[1] + height, self.b.mover.position[2]))
            self.run.step({self.a: (*direction, widow), self.b: (0 if rear else -32768, 0, grasp)})

    def instance(self):
        return next(
            i for i in self.b.body_script.component.instances.values() if i.graph.index == sigma.GRASP
        )

    def activate(self, **kwargs):
        self.step(grasp=8, **kwargs)
        self.step(2, **kwargs)
        self.assertTrue(self.instance().state(1).active)



class WidowmakerGraspTests(_GraspMatch, unittest.TestCase):
    def test_scoped_absorption_spends_ammo_without_damage_charge_then_grants_separate_shields(self):
        self.step(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.step(widow=FIRE | SCOPE)
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.instance().vars[21312].value(), 120)
        self.assertEqual(ammo - weapon.vars[53].value(), 5)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.step(160)
        vitals = self.run.match.combat.vitals[self.b.body]
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 72)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(vitals.maximum, 472)

    def test_widow_damage_spends_temporary_before_base_and_healing_cannot_refill_it(self):
        self.step(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        self.step(widow=FIRE | SCOPE)
        self.step(160)
        fight = self.run.match.combat
        vitals = fight.vitals[self.b.body]
        self.step(widow=FIRE)
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 59)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        fight.heal_area(self.b, fight._eye(self.b), 10, 1000)
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 59)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 13)

    def test_body_retirement_removes_grant_and_decay_bookkeeping(self):
        self.activate()
        self.step(widow=FIRE)
        self.step(160)
        fight = self.run.match.combat
        old = self.b.body
        self.assertIn(old, fight.grasp_shields)
        self.assertIn(old, fight.grasp_grants)
        self.run.match.switch_hero(self.b, heroes()[0x02E000000000006E])
        self.assertNotIn(old, fight.grasp_shields)
        self.assertNotIn(old, fight.grasp_grants)

    def test_primary_shot_grants_nominal_sixty_percent(self):
        self.activate()
        self.step(widow=FIRE)
        self.assertEqual(self.instance().vars[21312].value(), 13)
        self.step(160)
        self.assertAlmostEqual(self.health(self.b), 407.8, places=4)

    def test_rear_and_melee_damage_are_not_absorbed(self):
        self.place(1.5)
        self.activate(rear=True)
        self.step(widow=FIRE, rear=True)
        self.step(15, rear=True)
        self.step(widow=MELEE, rear=True)
        self.step(30, rear=True)
        self.assertEqual(self.health(self.b), 357)
        self.assertEqual(self.instance().vars[21312].value(), 0)

    def test_frontal_melee_bypasses_active_grasp(self):
        self.place(1.5)
        self.activate()
        self.step(widow=MELEE)
        self.step(30)
        self.assertEqual(self.health(self.b), 370)
        self.assertEqual(self.instance().vars[21312].value(), 0)

    def test_wall_before_grasp_and_allies_do_not_award_shields(self):
        self.activate()
        self.run.match.collision_world = World([
            (-10, -10, 2, 10, -10, 2, 10, 10, 2),
            (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        self.step(widow=FIRE)
        self.step(15)
        self.b.team = self.a.team
        self.run.match.collision_world = World([])
        self.step(widow=FIRE)
        self.assertEqual(self.instance().vars[21312].value(), 0)
        self.assertEqual(self.health(self.b), 400)

    def test_absorbed_mine_has_no_deployment_poison_or_future_damage_shield_credit(self):
        self.place(4)
        self.activate()
        self.step(widow=16)
        self.step(70)
        mines = self.run.match.combat.venom
        self.assertEqual(mines.flights, [])
        self.assertEqual(mines.mines, [])
        self.assertEqual(mines.poison, [])
        self.assertEqual(self.instance().vars[21312].value(), 0)
        self.step(160)
        self.assertEqual(self.health(self.b), 400)

    def test_expiry_and_cooldown_repress_cannot_extend_absorption(self):
        self.activate()
        self.step(160)
        state = self.instance().state(1)
        self.assertFalse(state.active)
        counter = state.counter
        self.step(grasp=8)
        self.assertFalse(state.active)
        self.assertEqual(state.counter, counter)
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 387)

    def test_established_poison_bypasses_grasp_and_does_not_award_absorption(self):
        self.place(1.5)
        self.step(widow=16)
        self.step(110)
        self.assertTrue(self.run.match.combat.venom.poison)
        self.activate()
        self.step(400)
        self.assertAlmostEqual(self.health(self.b), 325)
        self.assertEqual(self.instance().vars[21312].value(), 0)

    def test_actual_scoped_kill_ends_grasp_and_dead_repress_cannot_restore_it(self):
        self.step(100, widow=SCOPE)
        self.activate(widow=SCOPE, rear=True)
        state = self.instance().state(1)
        self.step(widow=SCOPE | FIRE, rear=True, height=combat.EYE)
        self.assertEqual(self.health(self.b), 100)
        self.step(100, widow=SCOPE, rear=True, height=combat.EYE)
        self.step(widow=SCOPE | FIRE, rear=True, height=combat.EYE)
        self.assertTrue(self.run.match.combat.is_dead(self.b))
        self.assertFalse(state.active)
        counter = state.counter
        self.step(30, grasp=8, rear=True)
        self.assertFalse(state.active)
        self.assertEqual(state.counter, counter)
        self.assertNotIn(self.b.body, self.run.match.combat.grasp_shields)
