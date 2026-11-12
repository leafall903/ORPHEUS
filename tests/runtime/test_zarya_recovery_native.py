"""Actual authoritative shield regeneration, with resource-backed rate/delay."""

import unittest

from test_game_combat import Run
from ow174.game.content import heroes
ZARYA = 0x02E0000000000068
from test_game_graph_analysis import make_graph, node, output

from ow174.game import combat
from ow174.game.script.driver import HUD_HEALTH


class _ZaryaBeamFixture:
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy"))
        self.a, self.b = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.a.mover.position[:] = [0, 1, 0]
        self.b.mover.position[:] = [0, 1, 5]

    def health(self, owner=None):
        owner = self.b if owner is None else owner
        target = next(t for t in self.fight._targets() if t.owner is owner)
        return self.fight.vitals_of(target).total


class ZaryaRecoveryTests(unittest.TestCase):
    def setUp(self):
        _ZaryaBeamFixture.setUp(self)
        self.run.step({})  # Establish the authoritative world clock before taking damage.

    health = _ZaryaBeamFixture.health

    def modifier(self, identifier, value):
        # An explicit modifier fixture runs the real Stack State above the authored
        # default. Writing only its underlying base cannot override an active Stack.
        definition = make_graph(0xF16C, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateStack", state=0, fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": 1},
                "m_value": {"$": "STUConfigVarFloat", "m_value": value},
                "m_priority": {"$": "STUConfigVarFloat", "m_value": 100},
            }),
        ], states=[1], entries=[0])
        definition.header.update(m_nodesBitCount=1, m_statesBitCount=1,
                                 m_remoteSyncNodesBitCount=0, m_syncVarsBitCount=0)
        instance = self.a.body_script.component.create(definition)
        instance.init()
        self.a.body_script.component.run_now()
        return instance

    def advance(self, frames):
        for _ in range(frames):
            self.run.step({})

    def hit(self, amount, owner=None):
        owner = self.a if owner is None else owner
        target = next(t for t in self.fight._targets() if t.owner is owner)
        self.fight._damage(self.b, target, amount, False, None, self.run.frame)

    def shields(self):
        return self.fight.vitals[self.a.body].now[combat.SHIELDS_POOL]

    def test_no_recovery_before_three_seconds(self):
        self.hit(100)
        self.advance(187)
        self.assertEqual(self.shields(), 100)
        self.advance(1)
        self.assertAlmostEqual(self.shields(), 100.24)

    def test_authored_30_per_second_and_hud_health_are_updated(self):
        self.hit(100)
        self.advance(250)
        self.assertAlmostEqual(self.shields(), 130)
        self.assertAlmostEqual(self.health(self.a), 330)
        self.assertAlmostEqual(self.a.body_script.component.entity_var(HUD_HEALTH).value(), 330)

    def test_recovery_never_heals_white_health(self):
        self.hit(300)
        self.advance(250)
        vitals = self.fight.vitals[self.a.body]
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 100)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 30)

    def test_new_body_damage_restarts_delay(self):
        self.hit(100)
        self.advance(250)
        self.hit(20)
        before = self.shields()
        self.advance(187)
        self.assertEqual(self.shields(), before)
        self.advance(63)
        self.assertAlmostEqual(self.shields(), before + 30)

    def test_fully_absorbed_barrier_damage_does_not_restart_body_delay(self):
        self.hit(100)
        self.advance(185)
        self.run.step({self.a: (0, 0, 8)})
        self.hit(20)
        self.advance(64)
        self.assertAlmostEqual(self.shields(), 130)

    def test_continuous_body_damage_prevents_recovery(self):
        self.hit(1)
        for _ in range(200):
            self.run.step({})
            self.hit(0.1)
        self.assertAlmostEqual(self.shields(), 179)

    def test_recovery_caps_at_original_shield_maximum(self):
        self.hit(20)
        self.advance(500)
        self.assertEqual(self.shields(), 200)
        self.assertEqual(self.health(self.a), 400)

    def test_resource_rate_modifier_is_applied(self):
        self.modifier(21869, 2.0)
        self.hit(100)
        self.advance(250)
        self.assertAlmostEqual(self.shields(), 160)

    def test_resource_delay_modifier_is_applied(self):
        self.modifier(21868, 2.0)
        self.hit(100)
        self.advance(250)
        self.assertEqual(self.shields(), 100)
        self.advance(250)
        self.assertAlmostEqual(self.shields(), 160)

    def test_modifier_retirement_restores_authored_default_rate(self):
        modifier = self.modifier(21869, 2.0)
        self.hit(100)
        self.advance(250)
        self.assertAlmostEqual(self.shields(), 160)
        self.a.body_script.component.destroy(modifier)
        self.advance(50)
        self.assertAlmostEqual(self.shields(), 184)

    def test_zero_rate_disables_recovery_without_a_later_backlog(self):
        modifier = self.modifier(21869, 0.0)
        self.hit(100)
        self.advance(250)
        self.assertEqual(self.shields(), 100)
        self.a.body_script.component.destroy(modifier)
        self.advance(50)
        self.assertAlmostEqual(self.shields(), 124)

    def test_undeclared_rate_does_not_invent_recovery(self):
        component = self.a.body_script.component
        default = component.entity_var(21869).links[-1].state
        default.end(False)
        self.hit(100)
        self.advance(250)
        self.assertEqual(self.shields(), 100)
        default.begin()
        self.advance(50)
        self.assertAlmostEqual(self.shields(), 124)

    def test_duplicate_or_older_tick_cannot_add_recovery(self):
        self.hit(100)
        self.advance(250)
        before = self.shields()
        self.fight.update(self.fight.now + 1000, self.fight.tick, frames=False)
        self.fight.update(self.fight.now, self.fight.tick - 10, frames=False)
        self.assertEqual(self.shields(), before)

    def test_skipped_scheduler_only_recovers_post_delay_interval(self):
        self.hit(100)
        tick = self.run.frame + 250
        self.fight.update(tick * 0.016, tick, frames=False)
        self.assertAlmostEqual(self.shields(), 130)

    def test_projectile_impact_keeps_shield_recovery_earned_before_impact(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.b, self.a.hero))
                self.b.mover.position[:] = [0, 1, 5]
                for _ in range(51):
                    self.run.step({self.b: (-32768, 0, 1)})
                damaged = self.run.frame
                self.assertAlmostEqual(self.shields(), 140)
                self.advance(180)
                self.run.step({self.b: (-32768, 0, 2)})
                self.run.step({})
                rocket, = self.fight.rockets
                self.assertIs(rocket.shooter, self.b)
                self.assertEqual(rocket.last_tick, self.run.frame)
                self.assertAlmostEqual(self.shields(), 140)

                end = damaged + 210
                for tick in (end,) if sparse else range(self.run.frame + 1, end + 1):
                    self.run.match.update(tick * combat.TICK, tick, frames=False)
                self.run.frame = end
                self.assertFalse(self.fight.rockets)
                impact = round(self.fight.vitals[self.a.body].last_damage_at / combat.TICK)
                self.assertEqual(impact, damaged + 193)
                shields = 140 + 2.16 - 47
                self.assertAlmostEqual(self.shields(), shields, places=4)
                self.assertAlmostEqual(self.health(self.a), 200 + shields, places=4)
                self.advance(impact + 187 - end)
                self.assertAlmostEqual(self.shields(), shields, places=4)
                self.advance(1)
                self.assertAlmostEqual(self.shields(), shields + 0.24, places=4)

    def test_dead_body_does_not_regenerate_and_respawn_discards_its_clock(self):
        self.hit(500)
        old = self.a.body
        self.advance(125)
        self.assertTrue(self.fight.vitals[old].dead)
        self.assertEqual(self.fight.vitals[old].total, 0)
        self.assertNotIn(old, self.fight.zarya_recovery.bodies)
        self.advance(100)
        self.assertNotEqual(self.a.body, old)
        self.assertEqual(self.health(self.a), 400)

    def test_switching_body_discards_recovery_state(self):
        self.hit(100)
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotIn(old, self.fight.zarya_recovery.bodies)
        self.assertEqual(self.health(self.a), 400)

    def test_recovery_does_not_add_hits_or_combat_stats(self):
        self.hit(100)
        before = dict(self.a.stats.values)
        self.advance(250)
        self.assertEqual(self.a.stats.values, before)
        self.assertFalse(self.a.client.messages(combat.HIT))


if __name__ == "__main__":
    unittest.main()
