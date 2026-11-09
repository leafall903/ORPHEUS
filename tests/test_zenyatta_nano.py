"""Authored Nano and bidirectional Zen combat through actual inputs."""

import unittest
from dataclasses import replace

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.content import PRACTICE_RANGE, find_hero, heroes
from ow174.game.zenyatta import HERO

ANA = 0x02E000000000013B
TEAM_MAP = replace(PRACTICE_RANGE, team_sizes=(6, 6))


class ZenyattaNanoTests(unittest.TestCase):
    def fight(self, *, enemy_hero="Soldier: 76"):
        run = Run(TEAM_MAP, ("Ana", "Zenyatta", "Enemy"))
        source, ally, enemy = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(source, heroes()[ANA]))
        self.assertTrue(match.switch_hero(ally, heroes()[HERO]))
        self.assertTrue(match.switch_hero(enemy, find_hero(enemy_hero)))
        source.team, ally.team, enemy.team = 0, 0, 1
        x, y, z = source.mover.position
        ally.mover.position = [x, y, z + 6]
        enemy.mover.position = [x + 12, y, z + 6]
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        return run, source, ally, enemy

    @staticmethod
    def target(fight, actor):
        return next(target for target in fight._targets() if target.owner is actor)

    def direction(self, fight, actor, target):
        entry = self.target(fight, target)
        return aim(fight._eye(actor), (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))

    def cast(self, run, source, target, *, charged=True):
        if charged:
            source.body_script.set_ult_charge(source.body_script.component.vars[338].value())
        direction = self.direction(run.match.combat, source, target)
        run.step({source: (*direction, 32)})
        for _ in range(12):
            run.step({source: (*direction, 0)})

    def single_shot(self, run, source, target, *, wait=8):
        if source.hero.guid == ANA:
            wait = max(wait, 55)  # projectile flight plus the actual0.6s rifle dose
        direction = self.direction(run.match.combat, source, target)
        run.step({source: (*direction, 1)})
        for _ in range(wait):
            run.step({source: (*direction, 0)})

    def test_cast_heals_zen_and_changes_both_damage_directions_until_expiry(self):
        run, source, zen, enemy = self.fight()
        self.assertEqual(zen.hero.guid, HERO)
        fight = run.match.combat
        x, y, z = zen.mover.position
        enemy.mover.position = [x+8, y, z]
        enemy.mover.body = None
        for _ in range(12):
            run.step({})
        zen_hp = fight.vitals_of(self.target(fight, zen))
        enemy_hp = fight.vitals_of(self.target(fight, enemy))
        zen_hp.damage(100)
        self.cast(run, source, zen)
        self.assertEqual(source.body_script.component.vars[342].value(), 0)
        self.assertEqual(zen_hp.total, 200)
        self.assertGreater(zen_hp.nano_until, fight.now+7.8)
        self.single_shot(run, zen, enemy, wait=23)
        self.assertEqual(enemy_hp.total, 128)
        self.single_shot(run, enemy, zen)
        self.assertEqual(zen_hp.total, 190)
        for _ in range(500):
            run.step({})
        self.assertLessEqual(zen_hp.nano_until, fight.now)
        self.single_shot(run, zen, enemy, wait=23)
        self.assertEqual(enemy_hp.total, 80)
        hit_count = len(enemy.client.messages(combat.HIT))
        before = zen_hp.total
        self.single_shot(run, enemy, zen)
        self.assertEqual(zen_hp.total, before-20)
        self.assertEqual(len(enemy.client.messages(combat.HIT)), hit_count+1)


    def test_orb_launched_before_nano_expiry_keeps_boost_at_impact(self):
        run, source, zen, enemy = self.fight()
        fight = run.match.combat
        x, y, z = zen.mover.position
        enemy.mover.position = [x+24, y, z]
        enemy.mover.body = None
        self.cast(run, source, zen)
        zen_hp = fight.vitals_of(self.target(fight, zen))
        enemy_hp = fight.vitals_of(self.target(fight, enemy))
        while fight.now + .05 < zen_hp.nano_until:
            run.step({})
        direction = self.direction(fight, zen, enemy)
        run.step({zen: (*direction, 1)})
        rocket, = fight.rockets
        self.assertEqual(rocket.direct.amount, 72)
        while fight.now < zen_hp.nano_until:
            run.step({})
        self.assertEqual(enemy_hp.total, 200)
        for _ in range(42):
            run.step({})
        self.assertEqual(enemy_hp.total, 128)

    def test_enemy_sleep_dart_interrupts_ana_before_releasing_nano_to_zen(self):
        run, source, zen, enemy = self.fight(enemy_hero="Ana")
        fight = run.match.combat
        zen_hp = fight.vitals_of(self.target(fight, zen))
        zen_hp.damage(100)
        direction = self.direction(fight, enemy, source)
        run.step({enemy: (*direction, 8)})
        for _ in range(28):
            run.step({enemy: (*direction, 0)})
        self.cast(run, source, zen)
        self.assertTrue(fight.is_stunned(source))
        self.assertIn(source.body, fight.sleeps)
        self.assertEqual(zen_hp.total, 100)
        self.assertEqual(zen_hp.nano_until, 0)

    def test_full_charged_volley_and_melee_stack_nano_with_discord(self):
        run, source, zen, enemy = self.fight()
        fight = run.match.combat
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z+12]
        enemy.mover.body = None
        self.cast(run, source, zen)
        hp = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
        direction = self.direction(fight, zen, enemy)
        run.step({zen: (*direction, 8)})
        self.assertEqual(fight.zenyatta.owners[zen.body].discord.target, enemy.body)
        for _ in range(130):
            run.step({zen: (*direction, 2)})
        for _ in range(60):
            run.step({zen: (*direction, 0)})
        hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x4"], hit["+0x8"]) for hit in hits], [(90, 0)]*5)
        self.assertEqual(hp.total, 550)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 15)
        enemy.mover.position = [x, y, z+1.5]
        enemy.mover.body = None
        for _ in range(15):
            run.step({})
        self.assertLess(fight.now, fight.vitals[zen.body].nano_until)
        direction = self.direction(fight, zen, enemy)
        run.step({zen: (*direction, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(hp.total, 493.75)
        self.assertEqual(weapon.vars[53].value(), 15)

    def test_nano_leaves_transcendence_healing_and_immunity_unchanged(self):
        totals = []
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                run, source, zen, enemy = self.fight()
                fight = run.match.combat
                self.cast(run, source, zen, charged=boosted)
                zen_hp = fight.vitals[zen.body]
                self.assertEqual(zen_hp.nano_until > fight.now, boosted)
                ally_hp = fight.vitals[source.body]
                ally_hp.damage(150)
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 32)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertEqual(zen.body_script.component.entity_var(342).value(), 0)
                self.single_shot(run, enemy, zen)
                self.assertEqual(zen_hp.total, 200)
                self.assertFalse(enemy.client.messages(combat.HIT))
                self.assertGreater(ally_hp.total, 50)
                totals.append(ally_hp.total)
        self.assertEqual(totals[0], totals[1])

    def test_enemy_grenade_blocks_nano_heal_on_zen_but_keeps_damage_reduction(self):
        run, source, zen, enemy = self.fight(enemy_hero="Ana")
        fight = run.match.combat
        target = self.target(fight, zen)
        hp = fight.vitals_of(target)
        hp.damage(50)
        direction = self.direction(fight, enemy, zen)
        run.step({enemy: (*direction, 16)})
        for _ in range(75):
            run.step({enemy: (*direction, 0)})
        self.assertEqual(hp.total, 90)
        self.assertEqual(fight.healing.factor(target), 0)
        self.cast(run, source, zen)
        self.assertEqual(hp.total, 90)
        self.assertGreater(hp.nano_until, fight.now)
        self.single_shot(run, enemy, zen, wait=60)
        self.assertEqual(hp.total, 55)

    def test_actual_caster_death_preserves_nano_but_zen_replacement_drops_it(self):
        run, source, zen, enemy = self.fight()
        fight = run.match.combat
        x, y, z = source.mover.position
        enemy.mover.position = [x+8, y, z]
        enemy.mover.body = None
        for _ in range(12):
            run.step({})
        self.cast(run, source, zen)
        old_body = zen.body
        hp = fight.vitals[old_body]
        deadline = hp.nano_until
        self.assertGreater(deadline, fight.now)
        fight.vitals[source.body].damage(180)
        self.single_shot(run, enemy, source)
        self.assertTrue(fight.is_dead(source))
        self.assertEqual(hp.nano_until, deadline)
        enemy_hp = fight.vitals[enemy.body]
        self.single_shot(run, zen, enemy, wait=24)
        self.assertEqual(enemy_hp.total, 128)
        position = list(zen.mover.position)
        self.assertTrue(run.match.switch_hero(zen, zen.hero))
        self.assertNotEqual(zen.body, old_body)
        self.assertNotIn(old_body, fight.vitals)
        zen.mover.position = position
        zen.mover.body = None
        self.assertEqual(fight.vitals_of(self.target(fight, zen)).nano_until, 0)
        for _ in range(12):
            run.step({})
        self.single_shot(run, zen, enemy, wait=24)
        self.assertEqual(enemy_hp.total, 80)

    def test_enemy_weapon_kill_during_cast_prevents_nano_release(self):
        for killed_name in ("Ana", "Zenyatta"):
            with self.subTest(killed=killed_name):
                run, source, zen, enemy = self.fight()
                fight = run.match.combat
                killed = source if killed_name == "Ana" else zen
                hp = fight.vitals_of(self.target(fight, killed))
                hp.damage(180)
                zen_hp = fight.vitals_of(self.target(fight, zen))
                if killed is source:
                    zen_hp.damage(100)
                source.body_script.set_ult_charge(source.body_script.component.entity_var(338).value())
                cast_direction = self.direction(fight, source, zen)
                lethal_direction = self.direction(fight, enemy, killed)
                run.step({source: (*cast_direction, 32), enemy: (*lethal_direction, 1)})
                self.assertTrue(fight.is_dead(killed))
                for _ in range(20):
                    run.step({})
                self.assertEqual(zen_hp.nano_until, 0)
                self.assertEqual(zen_hp.total, 100 if killed is source else 0)
