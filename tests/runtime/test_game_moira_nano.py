"""Actual Ana Nano release and Moira consumers; native modifier timing remains open."""

import unittest
from unittest.mock import patch

import test_game_moira as moira_tests
import test_game_moira_biotic as biotic_tests
import test_game_moira_orbs as orb_tests
from test_game_combat import Run, aim, in_world

from ow174.game import stats
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.content import find_hero
from ow174.game.script import nodes


class MoiraNanoTests(unittest.TestCase):
    start_ult = moira_tests.MoiraOpenAcceptanceTests.start_ult
    launch_orb = orb_tests.MoiraOrbTests.launch

    @staticmethod
    def target(fight, actor):
        return next(t for t in fight._targets() if t.owner is actor)

    def add_ana(self, run, recipient):
        match = run.match
        ana = match.add_player(len(run.players) + 1, 'Nano caster', find_hero('ana').guid, 3, False)
        in_world(match, ana)
        run.players.append(ana)
        self.assertTrue(match.switch_hero(ana, find_hero('ana')))
        ana.team = recipient.team
        x, y, z = self.target(match.combat, recipient).feet
        ana.mover.position = [x + 4, y, z]
        ana.mover.body = None
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        for _ in range(20):
            run.step({})
        return ana

    def cast(self, run, ana, recipient, *, charged=True):
        fight = run.match.combat
        target = self.target(fight, recipient)
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        if charged:
            ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        run.step({ana: (*direction, 32)})
        for _ in range(12):
            run.step({ana: (*direction, 0)})

    def fight(self):
        run = Run(names=('Moira', 'Target'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, victim = run.players
        match.switch_hero(source, find_hero('moira'))
        match.switch_hero(victim, find_hero('roadhog'))
        source.team, victim.team = 0, 1
        x, y, z = source.mover.position
        victim.mover.position = [x, y, z + 1.5]
        for player in run.players:
            player.mover.body = None
        for _ in range(20):
            run.step({})
        source.body_script.component.set(12918, 0)
        source.body_script.set_ult_charge(0)
        return run, source, victim

    def test_actual_release_heals250_spends_charge_and_expires_after8_seconds(self):
        run, _, victim = self.fight()
        fight = run.match.combat
        ana = self.add_ana(run, victim)
        health = fight.vitals_of(self.target(fight, victim))
        health.damage(400)
        fight._sync_body_health(victim, health)
        self.cast(run, ana, victim)
        self.assertEqual(health.total, 450)
        self.assertEqual(ana.stats.value(stats.HEALING), 250)
        self.assertEqual(ana.body_script.component.vars[342].value(), 0)
        self.assertGreater(health.nano_until, fight.now + 7.8)
        for _ in range(520):
            run.step({})
        self.assertLessEqual(health.nano_until, fight.now)
        self.assertFalse(ana.body_script.ultimates[0].flags & nodes.Ability.RUNNING)

    def measure(self, attack, *, outgoing=False, incoming=False):
        run, source, victim = self.fight()
        fight = run.match.combat
        if outgoing:
            self.cast(run, self.add_ana(run, source), source)
        if incoming:
            self.cast(run, self.add_ana(run, victim), victim)
        for caster in run.players[2:]:
            caster.mover.position[0] += 30
            caster.mover.body = None
        health = fight.vitals_of(self.target(fight, victim))
        x, y, z = victim.mover.position
        direction = aim(fight._eye(source), (x, y + 1, z))
        orb = None
        if attack == 'orb':
            orb = self.launch_orb(run, source)
        elif attack == 'ultimate':
            self.start_ult(run, source)
        before = health.total
        before_stats = source.stats.value(stats.HERO_DAMAGE)
        energy = orb.energy if orb else None
        for i in range(20):
            button = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and i == 0 else 0
            run.step({source: (*direction, button)})
        dealt = before - health.total
        self.assertGreater(dealt, 0)
        self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE) - before_stats, dealt, places=5)
        if orb:
            self.assertAlmostEqual(energy - orb.energy, dealt, places=5)
        return dealt

    def test_four_actual_moira_damage_paths_obey_outgoing_and_incoming_scalars(self):
        for attack in ('grasp', 'orb', 'melee', 'ultimate'):
            with self.subTest(attack=attack):
                base = self.measure(attack)
                self.assertAlmostEqual(self.measure(attack, outgoing=True), base * 1.5, places=4)
                self.assertAlmostEqual(self.measure(attack, incoming=True), base * .5, places=4)
                combined = self.measure(attack, outgoing=True, incoming=True)
                self.assertAlmostEqual(combined, base * .75, places=4)

    def test_enemy_and_uncharged_search_cannot_apply(self):
        for enemy, charged in ((True, True), (False, False)):
            with self.subTest(enemy=enemy, charged=charged):
                run, source, victim = self.fight()
                ana = self.add_ana(run, source)
                recipient = victim if enemy else source
                self.cast(run, ana, recipient, charged=charged)
                health = run.match.combat.vitals_of(self.target(run.match.combat, recipient))
                self.assertEqual(health.nano_until, 0)

    def test_nano_does_not_amplify_any_of_the_six_moira_healing_paths(self):
        case = biotic_tests.MoiraBioticTests()
        for attack in ('spray', 'tail', 'orb', 'ultimate', 'grasp_self', 'ultimate_self'):
            results = []
            for active in (False, True):
                with self.subTest(attack=attack, active=active):
                    run, _, source, target, health = case.fight(self_target=attack.endswith('_self'))
                    if active:
                        caster = self.add_ana(run, source)
                        self.cast(run, caster, source)
                        deadline = run.match.combat.vitals[source.body].nano_until
                        self.assertGreater(deadline, run.match.combat.now)
                    for caster in run.players:
                        if caster.hero.name.lower() == 'ana':
                            caster.mover.position[0] += 30
                    health.damage(health.total - 40)
                    run.match.combat._sync_body_health(target.owner, health)
                    results.append(case.sample(run, source, target, health, attack))
            self.assertGreater(results[0], 0)
            self.assertAlmostEqual(results[0], results[1], places=4)

    def test_actual_grenade_blocks_or_amplifies_nano_impact_heal(self):
        for status, expected in (('block', 100), ('boost', 475)):
            with self.subTest(status=status):
                run, source, victim = self.fight()
                grenade = self.add_ana(run, victim)
                if status == 'block':
                    grenade.team = source.team
                target = self.target(run.match.combat, victim)
                biotic_tests.MoiraBioticTests.cast(self, run, grenade, target)
                health = run.match.combat.vitals_of(target)
                health.damage(health.total - 100)
                run.match.combat._sync_body_health(victim, health)
                caster = self.add_ana(run, victim)
                self.cast(run, caster, victim)
                self.assertEqual(health.total, expected)
                self.assertGreater(health.nano_until, run.match.combat.now)

    def test_caster_retirement_preserves_buff_but_target_replacement_does_not_inherit(self):
        run, source, _victim = self.fight()
        fight = run.match.combat
        ana = self.add_ana(run, source)
        self.cast(run, ana, source)
        old = fight.vitals[source.body]
        deadline = old.nano_until
        self.assertTrue(run.match.switch_hero(ana, find_hero('soldier: 76')))
        for _ in range(10):
            run.step({})
        self.assertEqual(old.nano_until, deadline)
        self.assertGreater(deadline, fight.now)
        self.assertTrue(run.match.switch_hero(source, find_hero('moira')))
        run.step({})
        replacement = fight.vitals_of(self.target(fight, source))
        self.assertEqual(replacement.nano_until, 0)

    def test_target_search_state_snapshot_does_not_query_live_world(self):
        run, source, _ = self.fight()
        ana = self.add_ana(run, source)
        self.cast(run, ana, source)
        with patch('ow174.game.nano.targets', side_effect=AssertionError('snapshot must not search')):
            for actor in (ana, source):
                with self.subTest(actor=actor.name):
                    first = snapshot_digest(export_body_vm(actor.body_script))
                    second = snapshot_digest(export_body_vm(actor.body_script))
                    self.assertEqual(first, second)

    def test_boosted_emitted_orb_keeps_declared200_actual_health_budget(self):
        run, source, victim = self.fight()
        fight = run.match.combat
        caster = self.add_ana(run, source)
        self.cast(run, caster, source)
        caster.mover.position[0] += 30
        health = fight.vitals_of(self.target(fight, victim))
        before = health.total
        orb = self.launch_orb(run, source)
        target = self.target(fight, victim)
        orb.position = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        orb.speed = 0  # emitted-orb fixed-pose accounting, not native flight proof
        for _ in range(300):
            run.step({})
            if orb not in fight.moira_orbs:
                break
        self.assertNotIn(orb, fight.moira_orbs)
        self.assertEqual(orb.energy, 0)
        self.assertGreater(orb.left, 0)
        self.assertAlmostEqual(before - health.total, 200, places=4)
        self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), 200, places=4)


if __name__ == '__main__':
    unittest.main()
