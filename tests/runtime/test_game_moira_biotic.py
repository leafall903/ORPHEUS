"""Actual Ana grenade status across Moira healing consumers; native aura host open."""

import unittest
from dataclasses import replace

import test_game_moira as moira_tests
import test_game_moira_orbs as orb_tests
from test_game_combat import Run, aim, in_world

from ow174.game import moira, stats
from ow174.game.collision import World
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_COST


class MoiraBioticTests(unittest.TestCase):
    start_ult = moira_tests.MoiraOpenAcceptanceTests.start_ult
    launch_orb = orb_tests.MoiraOrbTests.launch

    def fight(self, status='none', *, self_target=False):
        run = Run(names=('Ana', 'Moira', 'Recipient'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        ana, source, recipient = run.players
        self.assertTrue(match.switch_hero(ana, find_hero('ana')))
        self.assertTrue(match.switch_hero(source, find_hero('moira')))
        source.body_script.component.set(12918, 0)
        source.body_script.set_ult_charge(0)
        source.team = 0
        recipient.team = 1 if self_target else 0
        ana.team = 1 if status == 'block' else 0
        x, y, z = source.mover.position
        recipient.mover.position = [x, y, z + 6]
        ana.mover.position = [x + 3, y, z if self_target else z + 6]
        for player in run.players:
            player.mover.body = None
        for _ in range(20):
            run.step({})
        actor = source if self_target else recipient
        target = next(t for t in match.combat._targets() if t.owner is actor)
        health = match.combat.vitals_of(target)
        if status != 'none':
            self.cast(run, ana, target)
            field = health.healing_block_until if status == 'block' else health.healing_boost_until
            self.assertGreater(field, match.combat.now)
        health.damage(health.total - 40)  # equal wounded starting condition after the real impact
        match.combat._sync_body_health(actor, health)
        return run, ana, source, target, health

    def cast(self, run, ana, target):
        x, y, z = target.feet
        direction = aim(run.match.combat._eye(ana), (x, y + target.height / 2, z))
        run.step({ana: (*direction, 16)})
        for _ in range(20):
            run.step({})

    def measure(self, attack, status):
        self_target = attack in ('grasp_self', 'ultimate_self')
        run, _ana, source, target, health = self.fight(status, self_target=self_target)
        return self.sample(run, source, target, health, attack)

    def sample(self, run, source, target, health, attack, direction=(0, 0)):
        fight = run.match.combat
        orb = None
        if attack == 'orb':
            orb = self.launch_orb(run, source, healing=True)
        elif attack in ('ultimate', 'ultimate_self'):
            self.start_ult(run, source)
        elif attack == 'tail':
            for _ in range(15):
                run.step({source: (*direction, 1)})
            for _ in range(30):
                run.step({})
                if not fight.rockets:
                    break
            self.assertIn(target.entity, fight.moira_hots)
        before = health.total
        initial_energy = orb.energy if orb else None
        for _ in range(20):
            run.step({source: (*direction, 1 if attack == 'spray' else 2 if attack == 'grasp_self' else 0)})
        given = health.total - before
        if orb:
            self.assertAlmostEqual(initial_energy - orb.energy, given, places=5)
        return given

    def test_all_six_healing_consumers_obey_actual_grenade_block_and_boost(self):
        for attack in ('spray', 'tail', 'orb', 'ultimate', 'grasp_self', 'ultimate_self'):
            with self.subTest(attack=attack):
                base = self.measure(attack, 'none')
                self.assertGreater(base, 0)
                self.assertAlmostEqual(self.measure(attack, 'block'), 0, places=5)
                self.assertAlmostEqual(self.measure(attack, 'boost'), base * 1.5, places=4)

    def test_enemy_grenade_does_not_apply_antiheal_during_live_protection(self):
        for hero, press in ((0x02E0000000000002, 8), (0x02E00000000000DD, 8),
                            (0x02E0000000000020, 32)):
            for protected in (False, True):
                with self.subTest(hero=hero, protected=protected):
                    run, ana, source, original, _ = self.fight()
                    match = run.match
                    fight = match.combat
                    recipient = original.owner
                    position = list(recipient.mover.position)
                    self.assertTrue(match.switch_hero(recipient, heroes()[hero]))
                    recipient.team = 0
                    recipient.mover.position = position
                    recipient.mover.body = None
                    ana.team = 1
                    for _ in range(30):
                        run.step({})
                    if protected:
                        if press == 32:
                            script = recipient.body_script
                            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                        run.step({recipient: (0, 0, press)})
                    self.assertEqual(fight.damage_immune(recipient), protected)
                    target = next(t for t in fight._targets() if t.owner is recipient)
                    health = fight.vitals_of(target)
                    before = health.total
                    damage = ana.stats.value(stats.HERO_DAMAGE)
                    self.cast(run, ana, target)
                    if protected:
                        self.assertEqual(health.total, before)
                        self.assertEqual(health.healing_block_until, 0)
                        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), damage)
                    else:
                        self.assertEqual(before - health.total, 50)
                        self.assertGreater(health.healing_block_until, fight.now)
                    if protected:
                        health.damage(75)
                    healing = source.stats.value(stats.HEALING)
                    for _ in range(20):
                        run.step({source: (0, 0, 1)})
                    self.assertEqual(source.stats.value(stats.HEALING) > healing, protected)

    def test_blocked_ally_healing_gives_no_stats_or_charge_and_retains_orb_energy(self):
        run, _, source, _, health = self.fight('block')
        orb = self.launch_orb(run, source, healing=True)
        for _ in range(25):
            run.step({})
        self.assertEqual(health.total, 40)
        self.assertEqual(orb.energy, 300)
        self.assertEqual(source.stats.value(stats.HEALING), 0)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)

    def test_fade_entry_cleanses_block_before_deadline_and_healing_resumes(self):
        run, _, source, _, health = self.fight('block', self_target=True)
        deadline = health.healing_block_until
        run.step({source: (0, 0, 8)})
        self.assertTrue(moira.fading(source))
        self.assertEqual(health.healing_block_until, 0)
        for _ in range(70):
            run.step({})
            if not moira.fading(source):
                break
        self.assertFalse(moira.fading(source))
        before = health.total
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertLess(run.match.combat.now, deadline)
        self.assertGreater(health.total, before)

    def test_status_expiry_restores_base_coalescence_healing(self):
        for status in ('block', 'boost'):
            with self.subTest(status=status):
                run, _, source, _, health = self.fight(status)
                deadline = health.healing_block_until if status == 'block' else health.healing_boost_until
                ultimate = self.start_ult(run, source)
                while run.match.combat.now < deadline:
                    run.step({})
                self.assertTrue(ultimate.states[20].active)
                health.damage(health.total - 40)
                before = health.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(health.total - before, 22.4, places=4)

    def test_actual_friendly_boost_cannot_override_active_enemy_block(self):
        run, old_ana, source, target, health = self.fight('block')
        x, y, z = target.feet
        old_ana.mover.position = [x + 30, y, z]
        old_ana.mover.body = None
        hero = find_hero('ana')
        ana = run.match.add_player(4, 'Friendly Ana', hero.guid, 3, False)
        in_world(run.match, ana)
        run.players.append(ana)
        self.assertTrue(run.match.switch_hero(ana, hero))
        ana.team = source.team
        ana.mover.position = [x - 3, y, z]
        ana.mover.body = None
        for _ in range(20):
            run.step({})
        self.cast(run, ana, target)
        self.assertGreater(health.healing_boost_until, run.match.combat.now)
        self.assertGreater(health.healing_block_until, run.match.combat.now)
        self.assertEqual(health.total, 40)
        for _ in range(30):
            run.step({source: (0, 0, 1)})
        self.assertEqual(health.total, 40)
        self.assertEqual(source.stats.value(stats.HEALING), 0)

    def test_grenade_static_wall_rejects_health_and_status(self):
        run, ana, _, target, health = self.fight()
        x, y, z = target.feet
        wall_x = x + 1.5
        run.match.collision_world = World([
            (wall_x, y - 5, z - 10, wall_x, y + 5, z - 10, wall_x, y + 5, z + 10),
            (wall_x, y - 5, z - 10, wall_x, y + 5, z + 10, wall_x, y - 5, z + 10),
        ])
        self.cast(run, ana, target)
        self.assertEqual(health.total, 40)
        self.assertEqual(health.healing_boost_until, 0)
        self.assertEqual(health.healing_block_until, 0)

    def test_ffa_same_team_is_hostile_for_grenade(self):
        run, ana, _, target, health = self.fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        health.heal(160)  # full initial health, allowing the actual50 damage
        self.cast(run, ana, target)
        self.assertEqual(health.total, 150)
        self.assertGreater(health.healing_block_until, run.match.combat.now)
        self.assertEqual(health.healing_boost_until, 0)

    def test_already_active_fade_rejects_incoming_grenade_health_and_block(self):
        run, ana, source, target, health = self.fight(self_target=True)
        ana.team = 1 - source.team
        run.step({source: (0, 0, 8)})
        self.assertTrue(moira.fading(source))
        self.cast(run, ana, target)
        self.assertTrue(moira.fading(source))
        self.assertEqual(health.total, 40)
        self.assertEqual(health.healing_block_until, 0)

    def test_second_actual_boost_refreshes_deadline_without_multiplying_scalar(self):
        base = self.measure('ultimate', 'none')
        run, first, source, target, health = self.fight('boost')
        old_deadline = health.healing_boost_until
        x, y, z = target.feet
        first.mover.position = [x + 30, y, z]
        first.mover.body = None
        hero = find_hero('ana')
        second = run.match.add_player(4, 'Second boost', hero.guid, 3, False)
        in_world(run.match, second)
        run.players.append(second)
        self.assertTrue(run.match.switch_hero(second, hero))
        second.team = source.team
        second.mover.position = [x - 3, y, z]
        second.mover.body = None
        for _ in range(20):
            run.step({})
        self.cast(run, second, target)
        self.assertGreater(health.healing_boost_until, old_deadline)
        health.damage(health.total - 40)
        given = self.sample(run, source, target, health, 'ultimate')
        self.assertAlmostEqual(given, base * 1.5, places=4)

    def test_old_ana_grenade_retains_source_body_and_cannot_charge_replacement_moira(self):
        run, ana, _, target, health = self.fight()
        ana.team = 1 - target.team
        x, y, z = target.feet
        direction = aim(run.match.combat._eye(ana), (x, y + target.height / 2, z))
        run.step({ana: (*direction, 16)})
        self.assertTrue(run.match.combat.rockets)
        launched = ana.body
        self.assertTrue(run.match.switch_hero(ana, find_hero('moira')))
        self.assertNotEqual(ana.body, launched)
        ana.body_script.component.set(12918, 0)
        ana.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, launched)
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)
