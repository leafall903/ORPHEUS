"""Actual 05FE Cryo-Freeze against Winston's authored attacks."""

import unittest

import test_game_winston_wraith as fixtures
from test_game_combat import Run, aim

from ow174.game import combat, protections, stats, winston
from ow174.game.commands import Command
from ow174.game.content import heroes


class WinstonCryoTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonWraithTests.move)
    health = fixtures.WinstonWraithTests.health

    def setUp(self):
        fixtures.WinstonWraithTests.setUp(self)
        self.mei = self.reaper
        self.assertTrue(self.match.switch_hero(self.mei, heroes()[protections.MEI]))
        self.move(self.mei, (0, 1, 1.5))

    def step(self, attack=0, defense=0, *, pitch=0):
        self.run.step({self.owner: (0, pitch, attack), self.mei: (-32768, 0, defense)})

    def cryo(self):
        self.step(defense=8)
        instance = protections.cryo_instance(self.mei)
        self.assertTrue(protections.cryo_active(self.mei))
        return instance

    def test_tesla_spends_ammo_but_gives_no_hit_or_charge_until_cryo_cancel(self):
        instance = self.cryo()
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        for _ in range(30):
            self.step(1)
        self.assertLess(weapon.vars[53].value(), 100)
        self.assertEqual(self.health(self.mei), 250)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.step(defense=2)
        self.assertFalse(protections.cryo_active(self.mei))
        self.assertGreater(instance.vars[61].value(), 11.9)
        self.step(defense=8)
        self.assertFalse(protections.cryo_active(self.mei))
        for _ in range(20):
            self.step(1)
            if self.health(self.mei) < 250:
                break
        self.assertAlmostEqual(self.health(self.mei), 249.04)

    def test_melee_jump_launch_and_primal_have_no_damage_or_knockback_in_cryo(self):
        for attack in (0x800, 8, 1):
            with self.subTest(attack=attack):
                if attack != 0x800:
                    self.setUp()
                if attack == 1:
                    fixtures.WinstonWraithTests.rage(self)
                self.cryo()
                self.step(attack)
                if attack == 8:
                    self.assertIn(self.owner.body, self.fight.winston_jump.flights)
                for _ in range(60):
                    self.step(attack if attack == 1 else 0)
                self.assertTrue(protections.cryo_active(self.mei))
                self.assertEqual(self.health(self.mei), 250)
                self.assertEqual(self.owner.client.messages(combat.HIT), [])
                self.assertEqual(tuple(self.mei.mover.position), (0, 1, 1.5))
                self.assertEqual(self.mei.mover.velocity, (0, 0, 0))
                if attack == 1:
                    self.assertTrue(winston.primal_active(self.owner.body_script.component))
                    self.assertTrue(self.fight.primal_punches.seen[self.owner.body])
                if attack == 0x800:
                    melee = next(i for i in self.owner.body_script.component.instances.values()
                                 if i.graph.index == 0x43)
                    self.assertEqual(melee.states[4].counter, 1)

    def test_landing_excludes_cryo_and_tesla_connects_after_cancel(self):
        self.move(self.mei, (0, 1, 30))
        self.step(8, pitch=-16384)
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        for _ in range(250):
            self.step()
            if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 3:
                break
        self.move(self.mei, (0, 1, 1.5))
        self.cryo()
        for _ in range(40):
            self.step()
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertTrue(protections.cryo_active(self.mei))
        self.assertEqual(self.health(self.mei), 250)
        for _ in range(30):
            self.step()
        self.step(defense=2)
        self.assertFalse(protections.cryo_active(self.mei))
        self.step(1)
        self.assertLess(self.health(self.mei), 250)

    def test_full_idle_duration_heals_200_and_expires_without_duplicate_credit(self):
        vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.mei))
        other = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.owner))
        vitals.damage(220)
        other.damage(100)
        other_health = other.total
        instance = self.cryo()
        script = self.mei.body_script
        frame, health = script.frame, vitals.total
        self.mei.take_commands([Command(frame + offset, buttons=2) for offset in range(1, 301)])
        self.assertEqual(script.frame, frame)
        self.assertEqual(vitals.total, health)
        self.assertTrue(protections.cryo_active(self.mei))
        for frames, active, expected in ((125, True, 130), (126, False, 230), (30, False, 230)):
            self.run.frame += frames
            self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
            self.assertEqual(protections.cryo_active(self.mei), active)
            self.assertAlmostEqual(vitals.total, expected, delta=0.9)
        self.assertGreater(instance.vars[61].value(), 0)
        self.assertEqual(vitals.total, 230)
        self.assertEqual(other.total, other_health)
        self.step(1)
        self.assertAlmostEqual(vitals.total, 229.04)

    def test_cancel_stops_healing_and_idle_cooldown_requires_new_press(self):
        vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.mei))
        vitals.damage(220)
        instance = self.cryo()
        for _ in range(30):
            self.step()
        self.step(defense=2)
        cancelled = vitals.total
        self.assertGreater(cancelled, 30)
        self.assertLess(cancelled, 60)
        self.step(defense=8)
        self.step()
        self.run.frame += 760
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(vitals.total, cancelled)
        self.assertFalse(protections.cryo_active(self.mei))
        self.assertEqual(instance.states[6].counter, 1)
        self.assertEqual(instance.vars[61].value(), 0)
        self.step(defense=8)
        self.assertTrue(protections.cryo_active(self.mei))
        self.assertEqual(instance.states[6].counter, 2)

    def test_sparse_cryo_healing_respects_actual_grenade_expiry(self):
        cases = (((True,), 162), ((False,), 234), ((True, False), 174.8), ((False, True), 136.4))
        for blocks, expected in cases:
            for sparse in (False, True):
                with self.subTest(blocks=blocks, sparse=sparse):
                    casters_names = ('First Ana', 'Second Ana')[:len(blocks)]
                    run = Run(names=('Winston', 'Mei', *casters_names))
                    match = run.match
                    match.bots.clear()
                    winston_player, mei, *casters = run.players
                    self.assertTrue(match.switch_hero(winston_player, heroes()[winston.HERO]))
                    self.assertTrue(match.switch_hero(mei, heroes()[protections.MEI]))
                    winston_player.team, mei.team = 0, 1
                    self.move(winston_player, (10, 1, 0))
                    self.move(mei, (0, 1, 0))
                    fight = match.combat
                    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is mei))
                    deadlines = []
                    first_cast = run.frame
                    for index, (ana, block) in enumerate(zip(casters, blocks, strict=True)):
                        self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                        ana.team = 0 if block else 1
                        self.move(ana, (0, 1, 4))
                        while run.frame < first_cast + index * 32:
                            run.step({})
                        windows = vitals.healing_blocks if block else vitals.healing_boosts
                        yaw, pitch = aim(fight._eye(ana), (0, 2, 0))
                        run.step({ana: (yaw, pitch, 16)})
                        for _ in range(60):
                            if windows:
                                break
                            run.step({})
                        self.assertEqual(len(windows), 1)
                        deadlines.append(windows[0][1])
                        self.move(ana, (-10, 1, 0))
                    if len(deadlines) == 2:
                        self.assertEqual(deadlines[1] - deadlines[0], 32)
                    while run.frame < deadlines[0] - 63:
                        run.step({})
                    vitals.damage(vitals.total - 10)
                    run.step({mei: (0, 0, 8)})
                    self.assertTrue(protections.cryo_active(mei))
                    updates = (251,) if sparse else (1,) * 251
                    for frames in updates:
                        run.frame += frames
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(protections.cryo_active(mei))
                    self.assertAlmostEqual(vitals.total, expected, places=5)
                    self.assertAlmostEqual(mei.stats.value(stats.HEALING), expected - 10, places=5)
                    self.move(winston_player, (0, 1, -1.5))
                    run.step({winston_player: (0, 0, 1)})
                    self.assertAlmostEqual(vitals.total, expected - .96, places=5)

    def test_replacing_mei_retires_cryo_protection_and_healing_sample(self):
        self.cryo()
        old_body = self.mei.body
        self.assertIn(old_body, self.fight.cryo_samples)
        self.assertTrue(self.match.switch_hero(self.mei, heroes()[winston.HERO]))
        self.assertNotIn(old_body, self.fight.cryo_samples)
        self.assertFalse(protections.cryo_active(self.mei))
