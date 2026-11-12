"""Delivered boost-credit checks using the combined server's prepared effect pack."""
import unittest
from unittest.mock import patch
import test_game_mercy_chains as chains
import test_game_mercy_staff as staff_tests
from test_game_combat import aim
from ow174.game.commands import Command, FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr, graph, runtime, support
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class BoostFixture:
    def setUp(self):
        self.fixture = staff_tests.MercyStaffTests()
        self.fixture.setUp()
        self.run, self.match = self.fixture.run, self.fixture.match
        self.mercy, self.ally, self.enemy = self.fixture.mercy, self.fixture.ally, self.fixture.enemy
        self.fight = self.fixture.fight

    def doCleanups(self):
        self.fixture.doCleanups()

    def step(self, count, *, beam=SECONDARY, attack=0):
        for _ in range(count):
            x, y, z = self.ally.mover.position
            beam_yaw, beam_pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
            x, y, z = self.enemy.mover.position
            shot_yaw, shot_pitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            self.run.step({self.mercy: (beam_yaw, beam_pitch, beam),
                           self.ally: (shot_yaw, shot_pitch, attack)})

    def enemy_health(self):
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        return self.fight.vitals_of(target).total

@unittest.skipUnless(graph.effect_script(support.BOOST), "prepared Mercy effect graphs required")
class MercyBoostChargeTests(unittest.TestCase):
    def fixture(self):
        f = BoostFixture()
        f.setUp()
        self.addCleanup(f.doCleanups)
        c = f.mercy.body_script.component
        c.set(12918, 0.0)  # isolate real damage awards from passive time
        slot = c.entity_var(ULT_CHARGE)
        return f, slot


    def test_rifle_credits_the_actual_six_point_bonus_to_staff_caster(self):
        f, charge = self.fixture()
        f.step(10)
        before = charge.value()
        f.step(1, attack=FIRE)
        f.step(5, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 174, places=4)
        self.assertAlmostEqual(charge.value() - before, 6, places=4)


    def test_original_assist_receiver_checks_classifier_and_killer_controller(self):
        f, _ = self.fixture()
        mercy = f.mercy
        component = mercy.body_script.component
        original = runtime.Instance._run_node
        staff = next(i for i in component.instances.values() if i.graph.index == 0x006B)
        criteria = []

        def observe(instance, node, caller):
            if instance.graph.index == 0x006B and node.pos == 184:
                criteria.append(instance.evaluate(node.field("m_297E0B63")))
            return original(instance, node, caller)

        for classifier, killer, expected in (
            (1, f.ally, True), (0, f.ally, False), (2, f.ally, False),
            (3, f.ally, False), (1, mercy, False),
        ):
            with self.subTest(classifier=classifier, killer=killer.name):
                criteria.clear()
                params = {
                    0xCB: (classifier,), 0x12: expr.Entity(killer.entity), 0x15: expr.Entity(f.enemy.body),
                }
                with patch.object(runtime.Instance, "_run_node", observe):
                    self.assertTrue(runtime.send_message(component, 0x0240000000000143, params, f.enemy.body))
                    component.run_now()
                    f.step(60, beam=0)
                    self.assertFalse(criteria)
                    f.step(15, beam=0)
                want = [(expr.Entity(mercy.body), expr.Entity(killer.entity))] if expected else []
                self.assertEqual(criteria, want)
                self.assertEqual(staff.vars[1839].value(), ())


    def test_helix_award_keeps_launch_owner_when_the_beam_is_released(self):
        f, charge = self.fixture()
        f.step(10)
        before = charge.value()
        f.step(1, attack=SECONDARY)
        self.assertTrue(f.fight.rockets)
        f.step(25, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 44, places=4)
        self.assertAlmostEqual(charge.value() - before, 36, places=4)


    def boost_charge_at_impact(self, missing=36):
        f, charge = self.fixture()
        script = f.mercy.body_script
        component = script.component
        cost = component.entity_var(ULT_COST).value()
        f.enemy.mover.position[:] = (0, 1, 22)
        f.enemy.mover.body = None
        f.step(10)
        script.set_ult_charge(cost - missing)
        f.step(1, attack=SECONDARY)
        launch = f.run.frame
        self.assertTrue(f.fight.rockets)
        for _ in range(65):
            f.run.frame += 1
            f.match.update(f.run.frame * 0.016, f.run.frame)
            if charge.value() == cost:
                break
        self.assertAlmostEqual(f.enemy_health(), 44, places=4)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(script.frame, launch)
        self.assertGreater(f.run.frame, launch + 1)
        return f, script, charge, cost


    def test_late_ultimate_input_cannot_spend_boost_charge_before_delivery(self):
        f, script, charge, cost = self.boost_charge_at_impact()
        component = script.component
        f.mercy.take_commands([Command(f.run.frame - 1, buttons=0x20)])
        self.assertEqual(charge.value(), cost)
        self.assertFalse(component.entity_var(842).value())
        f.mercy.take_commands([Command(f.run.frame, buttons=0x20)])
        self.assertEqual(charge.value(), 0)
        self.assertTrue(component.entity_var(842).value())


    def test_same_hero_respawn_preserves_future_charge_boundary(self):
        f, old, _, cost = self.boost_charge_at_impact()
        impact = f.run.frame
        caster = next(target for target in f.fight._targets() if target.owner is f.mercy)
        f.fight._damage(f.enemy, caster, 500, False, None, impact)
        f.run.frame += 750
        f.match.update(f.run.frame * 0.016, f.run.frame)
        script = f.mercy.body_script
        self.assertIsNot(script, old)
        self.assertTrue(old.retired)
        credits = script.world.pending_ult_credits
        self.assertEqual({time for time, _ in credits}, {impact * script.frame_ms})
        self.assertEqual(sum(amount for _, amount in credits), 36)
        component = script.component
        f.mercy.take_commands([Command(impact - 1, buttons=0x20)])
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(component.entity_var(842).value())


    def test_late_staff_healing_fills_charge_before_the_later_boost(self):
        f, script, charge, cost = self.boost_charge_at_impact(missing=0.5)
        launch = script.frame
        impact = f.run.frame
        self.assertEqual(script.world.pending_ult_credits, ((impact * script.frame_ms, 0.5),))
        x, y, z = f.ally.mover.position
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 1, z))

        f.mercy.take_commands([Command(launch + 1, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertAlmostEqual(f.fixture.vitals.total, 100.88)
        self.assertEqual(charge.value(), cost)
        self.assertFalse(hasattr(script.world, "pending_ult_credits"))
        f.mercy.take_commands([Command(launch + 2, yaw=yaw, pitch=pitch, buttons=FIRE | 0x20)])
        self.assertEqual(charge.value(), 0)
        self.assertTrue(script.component.entity_var(842).value())


    def test_overkill_counts_only_the_extra_health_actually_removed(self):
        for remaining, expected in ((10, 0), (23, 3)):
            with self.subTest(remaining=remaining):
                f, charge = self.fixture()
                target = next(t for t in f.fight._targets() if t.owner is f.enemy)
                vitals = f.fight.vitals_of(target)
                vitals.damage(200 - remaining)  # injury setup, outside the award path
                f.step(10)
                before = charge.value()
                f.step(1, attack=FIRE)
                f.step(5, beam=0)
                self.assertTrue(vitals.dead)
                self.assertAlmostEqual(charge.value() - before, expected, places=4)


    def test_authored_victim_marker_rejects_boost_award_without_removing_damage(self):
        f, charge = self.fixture()
        f.enemy.body_script.component.set(6442, True)
        f.step(10)
        before = charge.value()
        f.step(1, attack=FIRE)
        f.step(5, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 174, places=4)
        self.assertEqual(charge.value(), before)


    def test_replacement_caster_cannot_collect_an_old_launch_award(self):
        f, _ = self.fixture()
        f.step(10)
        old = f.mercy.body_script
        f.step(1, attack=SECONDARY)
        rocket, = f.fight.rockets
        self.assertIs(rocket.boost_credit[0], old)
        f.match.switch_hero(f.mercy, f.mercy.hero)
        self.assertTrue(old.retired)
        self.assertIn(rocket, f.fight.rockets)
        current = f.mercy.body_script.component
        current.set(12918, 0.0)
        f.step(25, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 44, places=4)
        self.assertEqual(current.entity_var(ULT_CHARGE).value(), 0)

    def test_award_for_boosted_helix_that_lands_after_caster_death_survives_respawn(self):
        f = chains.MercyChainTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        mercy, ally, killer, enemy = f.mercy, f.anchor, f.chain, f.enemy
        self.assertTrue(f.match.switch_hero(killer, heroes()[0x02E0000000000042]))
        killer.team = enemy.team
        killer.mover.position[:] = (4, 1, 0)
        enemy.mover.position[:] = (0, 1, 22)
        for player in f.run.players:
            player.mover.body = None
        old_script, old_body = mercy.body_script, mercy.body
        component = old_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        killer_yaw, killer_pitch = aim(f.fight._eye(killer), (0, 1.8, 0))
        beam = (*aim(f.fight._eye(mercy), (0, 2, 5)), SECONDARY)
        helix = (*aim(f.fight._eye(ally), (0, 2, 22)), SECONDARY)
        for _ in range(35):
            f.run.step({})
        for _ in range(90):
            f.run.step({mercy: beam, killer: (killer_yaw, killer_pitch, FIRE)})
            if f.health(mercy) <= 60:
                break
        self.assertEqual(f.health(mercy), 60)
        for _ in range(40):
            f.run.step({mercy: beam})
        f.run.step({mercy: beam, ally: helix})
        rocket, = f.fight.rockets
        self.assertIs(rocket.boost_credit[0], old_script)
        f.run.step({mercy: beam, killer: (killer_yaw, killer_pitch, FIRE)})
        self.assertTrue(f.fight.is_dead(mercy))
        self.assertIn(rocket, f.fight.rockets)
        self.assertFalse(f.fight.support_effects.attachments)
        for _ in range(50):
            f.run.step({})
        self.assertAlmostEqual(f.health(enemy), 44, places=4)
        self.assertAlmostEqual(charge.value(), 36)
        for _ in range(155):
            f.run.step({})
            if mercy.body != old_body:
                break
        self.assertNotEqual(mercy.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertAlmostEqual(mercy.body_script.component.entity_var(ULT_CHARGE).value(), 36)
