"""Actual 104319 Fortify against Mei's direct damage and frost."""

import unittest

import test_mei_combat as fixtures

from ow174.game import combat
from ow174.game.content import heroes

ORISA = 0x02E000000000013E


class MeiFortifyTests(unittest.TestCase):
    def scene(self):
        fixture = fixtures.MeiCombatTests()
        run, mei, orisa, _target = fixture.duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(orisa, heroes()[ORISA]))
        x, y, z = mei.mover.position
        fight.walls.place_player(orisa, (x, y, z + 5.))
        for _ in range(25):
            run.step({})
        run.step({orisa: (-32768, 0, 8)})
        for _ in range(8):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is orisa)
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CF4)
        state = instance.states[4]
        self.assertTrue(state.active)
        self.assertAlmostEqual(state.evaluate(state.node.field("m_50530EAB"), watch=False), -.4)
        return fixture, run, mei, orisa, target, instance, state

    def test_fortify_reduces_icicle_and_expires_without_input(self):
        fixture, run, mei, orisa, target, instance, state = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(target)
        for height in (.9, target.eye):
            before = pool.total
            fixture.fire(run, mei, target, 2, height)
            self.assertAlmostEqual(before - pool.total, 40., places=5)
            self.assertEqual(mei.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"], 0)
        self.assertTrue(state.active)

        for _ in range(280):
            if not state.active:
                break
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
        self.assertFalse(state.active)
        self.assertGreater(instance.node_state(instance.graph.node(3)).remaining(), 0.)
        before = pool.total
        fixture.fire(run, mei, target, 2, target.eye)
        self.assertAlmostEqual(before - pool.total, 145.)
        self.assertEqual(mei.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"], combat.CRITICAL)
        self.assertFalse(fight.frozen(orisa))

    def test_fortify_reduces_quick_melee_until_expiry(self):
        fixture, run, mei, orisa, _target, _instance, state = self.scene()
        fight = run.match.combat
        x, y, z = mei.mover.position
        fight.walls.place_player(orisa, (x, y, z + 2.))
        for _ in range(10):
            run.step({})
        target = next(target for target in fight._targets() if target.owner is orisa)
        pool = fight.vitals_of(target)
        before = pool.total
        fixture.fire(run, mei, target, 0x800, 0.9, frames=6)
        self.assertTrue(state.active)
        self.assertAlmostEqual(before - pool.total, 13., places=5)
        for _ in range(280):
            if not state.active:
                break
            run.step({})
        self.assertFalse(state.active)
        before = pool.total
        fixture.fire(run, mei, target, 0x800, 0.9, frames=6)
        self.assertEqual(before - pool.total, 25.)

    def test_fortify_reduces_spray_and_prevents_frost_until_expiry(self):
        fixture, run, mei, orisa, target, _instance, state = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(target)
        before = pool.total
        fixture.fire(run, mei, target, 1, .9, frames=1)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(before - pool.total, .825, places=5)
        self.assertIsNone(fight.frost(orisa))

        for _ in range(280):
            if not state.active:
                break
            run.step({})
        self.assertFalse(state.active)
        before = pool.total
        fixture.fire(run, mei, target, 1, .9, frames=1)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(before - pool.total, 1.375)
        self.assertIsNotNone(fight.frost(orisa))

    def test_active_blizzard_resumes_frost_when_fortify_ends(self):
        _fixture, run, mei, orisa, target, _instance, state = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(target)
        angle = fixtures.aim(fight._eye(mei), target.feet)
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        for frame in range(120):
            run.step({mei: (*angle, 32 if frame == 0 else 0)})
            if pool.total < 450.:
                break
        self.assertLess(pool.total, 450.)
        self.assertTrue(state.active)
        self.assertIsNone(fight.frost(orisa))
        area, = fight.blizzard.areas.values()
        self.assertNotIn(target.entity, area.frost_due)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, .096, places=5)
        for _ in range(280):
            if not state.active:
                break
            run.step({})
            if state.active:
                self.assertIsNone(fight.frost(orisa))
        self.assertFalse(state.active)
        self.assertTrue(fight.blizzard.areas)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, .16, places=5)
        self.assertIsNotNone(fight.frost(orisa))
