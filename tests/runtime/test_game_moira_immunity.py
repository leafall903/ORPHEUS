"""Two real Moira command streams: Fade against each reconstructed damage path."""

import unittest

import test_game_moira as fixtures
import test_game_moira_charge as charge_fixtures
import test_game_moira_orbs as orb_fixtures

from ow174.game.content import heroes
from ow174.game.script import expr


class MoiraFadeInteractionTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight
    start_ult = fixtures.MoiraOpenAcceptanceTests.start_ult
    launch_orb = orb_fixtures.MoiraOrbTests.launch

    def opponents(self, *, distance=3):
        run, source, target, _health = self.setup_fight(ally=False)
        victim = target.owner
        run.match.switch_hero(victim, heroes()[fixtures.MOIRA])
        x, y, z = source.mover.position
        victim.mover.position = [x, y, z + distance]
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        health = fight.vitals_of(targets[victim])
        own = fight.vitals_of(targets[source])
        health.damage(100)
        own.damage(100)
        charge_fixtures.MoiraCombatChargeTests.reset(source)
        return run, source, victim, health, own

    def test_faded_grasp_target_yields_no_damage_self_heal_charge_or_hit_refill(self):
        run, source, victim, health, own = self.opponents()
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0xF76)
        weapon.write((expr.INSTANCE, 87), 20.0)
        run.step({victim: (0, 0, 8)})
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertEqual((health.total, own.total), (100, 100))
        self.assertEqual(charge_fixtures.MoiraCombatChargeTests.charge(source), 0)
        self.assertLess(weapon.find_var(expr.INSTANCE, 87).value(), 21)
        for _ in range(40):
            run.step({})
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertLess(health.total, 100)
        self.assertGreater(own.total, 100)
        self.assertGreater(weapon.find_var(expr.INSTANCE, 87).value(), 21)

    def test_damage_orb_neither_slows_nor_spends_energy_on_faded_target(self):
        run, source, victim, health, _own = self.opponents()
        orb = self.launch_orb(run, source)
        run.step({victim: (0, 0, 8)})
        before = health.total, orb.energy, orb.position
        for _ in range(20):
            run.step({})
        self.assertEqual((health.total, orb.energy), before[:2])
        self.assertAlmostEqual(orb.position[2] - before[2][2], 6.4, places=4)
        for _ in range(40):
            run.step({})
        x, y, z = victim.mover.position
        orb.position, orb.speed = (x, y + 1, z), 0
        run.step({})
        self.assertLess(health.total, before[0])
        self.assertLess(orb.energy, before[1])

    def test_fade_blocks_coalescence_damage_while_caster_self_healing_continues(self):
        run, source, victim, health, own = self.opponents()
        self.start_ult(run, source)
        run.step({victim: (0, 0, 8)})
        before = health.total, own.total
        for _ in range(20):
            run.step({})
        self.assertEqual(health.total, before[0])
        self.assertAlmostEqual(own.total - before[1], 16, places=4)
        self.assertEqual(charge_fixtures.MoiraCombatChargeTests.charge(source), 0)
        for _ in range(40):
            run.step({})
        self.assertLess(health.total, before[0])

    def test_fade_blocks_quick_melee_then_a_new_swing_can_damage(self):
        run, source, victim, health, _own = self.opponents(distance=1.5)
        # Establish Fade before the attack command. The fixture serializes
        # players' input; this does not prove same-frame input arbitration.
        run.step({victim: (0, 0, 8)})
        run.step({source: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, 100)
        for _ in range(40):
            run.step({})
        run.step({source: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, 70)
