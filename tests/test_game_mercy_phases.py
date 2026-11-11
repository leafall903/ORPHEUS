"""Actual Mercy combat against and alongside protected heroes."""

import unittest

import test_game_mercy_boost as boost
import test_game_mercy_chains as chains
import test_game_mercy_deflection as defense
import test_game_mercy_guardian as guardian
from test_game_combat import aim

from ow174.game import combat, cryo, phase_defenses, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr, support_effects
from ow174.game.script.driver import ULT_CHARGE

ABILITY_1 = 0x08
MELEE = 0x800
FRIENDLY_PROTECTION = (*((hero, phase_defenses.active) for hero in phase_defenses.PHASES),
                       (cryo.HERO, cryo.protected))


class MercyPhaseTests(unittest.TestCase):
    def encounter(self, hero, close=False):
        helper = defense.MercyDeflectionTests()
        f, instance = helper.fixture(hero, phase_defenses.PHASES[hero][0])
        self.addCleanup(helper.doCleanups)
        if close:
            x, y, z = f.mercy.mover.position
            f.enemy.mover.position[:] = [x, y, z + 1.5]
            f.enemy.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        return f, instance, f.fight.vitals_of(target)

    @staticmethod
    def attack(f, button, frames=25, target=None):
        target = f.enemy if target is None else target
        x, y, z = target.mover.position
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.mercy: (yaw, pitch, button)})
        for _ in range(frames):
            f.run.step({})

    def test_phase_blocks_actual_attacks_until_authored_expiry(self):
        for hero, duration, cooldown in ((0x02E0000000000002, 3000, 8), (0x02E00000000001A2, 750, 6)):
            for button, damage in ((FIRE, 20), (MELEE, 30)):
                with self.subTest(hero=hero, button=button):
                    f, instance, health = self.encounter(hero, close=button == MELEE)
                    before = health.total
                    f.run.step({f.enemy: (0, 0, ABILITY_1)})
                    self.assertTrue(phase_defenses.active(f.enemy))
                    wait = instance.state(3)
                    self.assertEqual(wait.ms, duration)
                    self.assertEqual(instance.vars[472].value(), cooldown)
                    self.attack(f, button)
                    self.assertTrue(phase_defenses.active(f.enemy))
                    self.assertEqual(health.total, before)
                    self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
                    remaining = wait.start + wait.ms - instance.component.now
                    for _ in range(remaining // 16 + 35):
                        f.run.step({})
                    self.assertFalse(phase_defenses.active(f.enemy))
                    f.run.step({f.enemy: (0, 0, ABILITY_1)})
                    self.assertFalse(phase_defenses.active(f.enemy))
                    self.attack(f, button, frames=40)
                    self.assertEqual(health.total, before - damage)

    def test_bullet_passes_through_phased_body_to_enemy_behind(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                f, _, health = self.encounter(hero, close=True)
                x, y, z = f.enemy.mover.position
                f.ally.team = f.enemy.team
                f.ally.mover.position[:] = [x, y, z + 2]
                f.ally.mover.body = None
                before = health.total
                behind = f.vitals.total
                f.run.step({f.enemy: (0, 0, ABILITY_1)})
                self.assertTrue(phase_defenses.active(f.enemy))
                self.attack(f, FIRE, target=f.ally)
                self.assertEqual(health.total, before)
                self.assertEqual(f.vitals.total, behind - 20)

    def test_in_flight_critical_resolves_against_the_phase_at_contact(self):
        for hero in phase_defenses.PHASES:
            for expires in (False, True):
                with self.subTest(hero=hero, expires=expires):
                    f, phase, front_health = self.encounter(hero)
                    f.ally.team = f.enemy.team
                    f.enemy.mover.position[:] = (0, 1, 15)
                    f.ally.mover.position[:] = (0, 1, 20)
                    f.enemy.mover.body = f.ally.mover.body = None
                    component = f.mercy.body_script.component
                    component.set(12918, 0.0)
                    charge = component.entity_var(ULT_CHARGE)
                    award = next(i for i in component.instances.values() if i.graph.index == 0x0B8B)
                    f.vitals.damage(f.vitals.total - 17)
                    f.fight._sync_body_health(f.ally, f.vitals)

                    if expires:
                        f.run.step({f.enemy: (0, 0, ABILITY_1)})
                        wait = phase.state(3)
                        while wait.start + wait.ms - phase.component.now > 48:
                            f.run.step({})
                    self.assertEqual(phase_defenses.active(f.enemy), expires)
                    before_charge = charge.value()
                    before_damage = f.mercy.stats.value(stats.HERO_DAMAGE)
                    before_health = front_health.total
                    target = next(t for t in f.fight._targets() if t.owner is f.ally)
                    yaw, pitch = aim(f.fight._eye(f.mercy), (0, 1 + target.eye, 20))
                    f.run.step({f.mercy: (yaw, pitch, FIRE)})
                    projectile, = f.fight.rockets
                    self.assertIs(projectile.shooter, f.mercy)
                    self.assertEqual(projectile.direct.amount, 20)
                    self.assertEqual(charge.value(), before_charge)

                    if expires:
                        for _ in range(5):
                            f.run.step({})
                    else:
                        f.run.step({f.enemy: (0, 0, ABILITY_1)})
                    self.assertEqual(phase_defenses.active(f.enemy), not expires)
                    self.assertIn(projectile, f.fight.rockets)
                    self.assertEqual(front_health.total, before_health)
                    self.assertEqual(f.vitals.total, 17)
                    for _ in range(35):
                        f.run.step({})
                        if not f.fight.rockets:
                            break

                    hit = f.enemy if expires else f.ally
                    dealt = 40 if expires else 17
                    self.assertEqual(front_health.total, before_health - (40 if expires else 0))
                    self.assertEqual(f.vitals.total, 17 if expires else 0)
                    self.assertEqual(f.vitals.killer, None if expires else f.mercy.body)
                    self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE) - before_damage, dealt)
                    self.assertEqual(charge.value() - before_charge, dealt)
                    self.assertEqual(award.find_var(0, 393).value(), expr.Entity(hit.body))
                    marker, = f.mercy.client.messages(combat.HIT)
                    flags = combat.CRITICAL if expires else combat.CRITICAL | combat.KILLING
                    self.assertEqual((marker["+0x78"]["+0x4"], marker["+0x78"]["+0x8"]), (dealt, flags))
                    self.assertFalse(f.fight.rockets)
                    for _ in range(20):
                        if not f.fight.projectiles.shots:
                            break
                        f.run.step({})
                    self.assertFalse(f.fight.projectiles.shots)
                    self.assertEqual(charge.value() - before_charge, dealt)
                    self.assertEqual(f.mercy.client.messages(combat.HIT), [marker])

    def test_staff_and_guardian_select_protected_allies(self):
        for hero, protected in FRIENDLY_PROTECTION:
            with self.subTest(hero=hero):
                f = guardian.GuardianAngelTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                self.assertTrue(f.match.switch_hero(f.ally, heroes()[hero]))
                f.ally.mover.position[:] = (0, 1, 12)
                f.ally.mover.body = None
                target = next(t for t in f.fight._targets() if t.owner is f.ally)
                health = f.fight.vitals_of(target)
                f.fight._damage(f.enemy, target, 100, False, None, f.run.frame)
                before = health.total
                f.run.step({f.ally: (0, 0, ABILITY_1)})
                self.assertTrue(protected(f.ally))

                f.step(20, FIRE)
                self.assertTrue(protected(f.ally))
                self.assertAlmostEqual(health.total - before, 17.6)
                self.assertAlmostEqual(f.mercy.stats.value(stats.HEALING), 17.6)

                f.step(4, FIRE | guardian.SHIFT)
                self.assertTrue(protected(f.ally))
                self.assertTrue(f.instance.state(0).active)
                self.assertGreater(f.mercy.mover.position[2], 0.5)
                self.assertAlmostEqual(health.total - before, 21.12)
                f.step(1)
                self.assertFalse(f.instance.state(0).active)
                self.assertGreater(f.cooldown(), 0)

    def test_valkyrie_secondary_heals_protected_allies(self):
        for hero, protected in FRIENDLY_PROTECTION:
            with self.subTest(hero=hero):
                f = chains.MercyChainTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                f.start()
                self.assertTrue(f.match.switch_hero(f.chain, heroes()[hero]))
                target = next(t for t in f.fight._targets() if t.owner is f.chain)
                f.fight._damage(f.enemy, target, 100, False, None, f.run.frame)
                before = f.health(f.chain)
                f.step(1, beam=0, attack=ABILITY_1)
                self.assertTrue(protected(f.chain))
                f.step(20)
                self.assertTrue(protected(f.chain))
                self.assertAlmostEqual(f.health(f.chain) - before, 19.2, places=4)

    def protected_boost(self, hero, protected, *, chained):
        fixture = chains.MercyChainTests() if chained else boost.MercyBoostTests()
        try:
            fixture.setUp()
            f = fixture
            ally = f.chain if chained else f.ally
            self.assertTrue(f.match.switch_hero(ally, heroes()[hero]))
            x, y, z = ally.mover.position
            f.enemy.mover.position[:] = (x, y, z + 1.5)
            f.enemy.mover.body = None
            if chained:
                f.start()
            component = f.mercy.body_script.component
            component.set(12918, 0.0)
            charge = component.entity_var(ULT_CHARGE)
            initial_charge = charge.value()
            beam_target = f.anchor if chained else ally
            definition = support_effects.CHAIN_BOOST if chained else support_effects.BOOST

            def attachment():
                item, = [effect for effect in f.fight.support_effects.attachments.values()
                         if effect.target == ally.body and effect.definition == definition]
                return item

            def step(count, *, beam=SECONDARY, attack=0):
                for _ in range(count):
                    x, y, z = beam_target.mover.position
                    beam_aim = aim(f.fight._eye(f.mercy), (x, y + 1, z))
                    x, y, z = f.enemy.mover.position
                    attack_aim = aim(f.fight._eye(ally), (x, y + 0.8, z))
                    f.run.step({f.mercy: (*beam_aim, beam), ally: (*attack_aim, attack)})

            step(35)
            effect = attachment()
            step(1, attack=ABILITY_1)
            self.assertTrue(protected(ally))
            self.assertIs(attachment(), effect)
            self.assertAlmostEqual(f.fight.damage_scale(ally), 1.3)
            step(1, attack=MELEE)
            step(26)
            self.assertTrue(protected(ally))
            target = next(t for t in f.fight._targets() if t.owner is f.enemy)
            health = f.fight.vitals_of(target)
            self.assertEqual(health.total, 200)
            self.assertEqual(f.mercy.stats.value(boost.AMPLIFIED), 0)
            self.assertEqual(charge.value(), initial_charge)

            if hero == cryo.HERO:
                step(1, attack=SECONDARY)
            for _ in range(200):
                if not protected(ally):
                    break
                step(1)
            self.assertFalse(protected(ally))
            step(35)
            self.assertIs(attachment(), effect)
            step(1, attack=MELEE)
            step(26)
            self.assertAlmostEqual(health.total, 161, places=4)
            self.assertAlmostEqual(ally.stats.value(stats.HERO_DAMAGE), 39, places=4)
            self.assertAlmostEqual(f.mercy.stats.value(boost.AMPLIFIED), 9, places=4)
            self.assertAlmostEqual(charge.value() - initial_charge, 0 if chained else 9, places=4)
            self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
            step(40, beam=0)
            self.assertEqual(f.fight.damage_scale(ally), 1)
            step(1, beam=0, attack=MELEE)
            step(26, beam=0)
            self.assertAlmostEqual(health.total, 131, places=4)
            self.assertAlmostEqual(ally.stats.value(stats.HERO_DAMAGE), 69, places=4)
            self.assertAlmostEqual(f.mercy.stats.value(boost.AMPLIFIED), 9, places=4)
            self.assertAlmostEqual(charge.value() - initial_charge, 0 if chained else 9, places=4)
        finally:
            fixture.doCleanups()

    @unittest.skipUnless(boost.EFFECT_BUNDLE.is_file(), "explicit extracted effect pack required")
    def test_direct_and_valkyrie_boost_survive_ally_protection(self):
        for hero, protected in FRIENDLY_PROTECTION:
            for chained in (False, True):
                with self.subTest(hero=hero, chained=chained):
                    self.protected_boost(hero, protected, chained=chained)
