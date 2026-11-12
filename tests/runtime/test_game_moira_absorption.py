"""Real Moira orbs versus Sigma Grasp; selected lane023B reconstruction."""

import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import absorption, combat, deflection
from ow174.game.collision import World
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script.driver import ULT_COST


class MoiraAbsorptionTests(unittest.TestCase):
    def fight(self, *, ally=False, offset=0, distance=6, recipient=False):
        run = Run(names=('Moira', 'Sigma', 'Recipient') if recipient else ('Moira', 'Sigma'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, sigma = run.players[:2]
        match.switch_hero(source, heroes()[fixtures.MOIRA])
        source.body_script.component.set(12918, 0)  # isolate health-based credit from passive pulses
        match.switch_hero(sigma, heroes()[absorption.HERO])
        sigma.team = source.team if ally else 1 - source.team
        x, y, z = source.mover.position
        sigma.mover.position = [x + offset, y, z + distance]
        if recipient:
            run.players[2].team = source.team
            run.players[2].mover.position = [x, y, z + 10]
        for _ in range(20):
            self.step(run, source, sigma)
        self.step(run, source, sigma, grasp=True)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == absorption.GRASP)
        self.assertTrue(instance.states[1].active)
        targets = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(targets[p]) for p in run.players]
        return run, source, sigma, instance, health

    @staticmethod
    def step(run, source, sigma, buttons=0, *, grasp=False):
        run.step({source: (0, 0, buttons), sigma: (-32768, 0, 8 if grasp else 0)})

    def launch(self, run, source, sigma, *, healing=False):
        self.step(run, source, sigma, 16)
        for _ in range(40):
            self.step(run, source, sigma, 1 if healing else 2)
            if run.match.combat.moira_orbs:
                return run.match.combat.moira_orbs[-1]
        self.fail('actual Moira volley emitted no orb')

    def consume(self, run, source, sigma, orb):
        for _ in range(80):
            self.step(run, source, sigma)
            if orb not in run.match.combat.moira_orbs:
                return
        self.fail('active Grasp never consumed the real orb')

    def grant(self, run, source, sigma, health):
        for _ in range(200):
            if absorption.SHIELD_TAG in health.temporary_shields:
                return health.temporary_shields[absorption.SHIELD_TAG]
            self.step(run, source, sigma)
        self.fail('authored Grasp end did not request tagged shields')

    def test_both_actual_orbs_are_removed_once_and_feed_authored_absorption_output(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, sigma, instance, health = self.fight()
                orb = self.launch(run, source, sigma, healing=healing)
                nominal = 0 if healing else 100  # actual emitted cfg; not300/200 aura budget
                self.assertEqual(orb.absorption_amount, nominal)
                self.consume(run, source, sigma, orb)
                self.assertEqual(instance.vars[21312].value(), nominal)
                self.assertEqual(instance.vars[6584].value(), nominal)
                self.assertEqual(orb.healing, healing)
                self.assertGreater(orb.left, 0)
                self.assertGreater(orb.energy, 0)
                before = health[1].total, instance.vars[21312].value()
                charge = source.body_script.component.entity_var(342).value()
                for _ in range(10):
                    self.step(run, source, sigma)
                self.assertEqual((health[1].total, instance.vars[21312].value()), before)
                self.assertEqual(source.body_script.component.entity_var(342).value(), charge)
                if not healing:
                    pool = self.grant(run, source, sigma, health[1])
                    self.assertAlmostEqual(pool.now, 60, places=4)  # actual0.6 conversion request
                    before = pool.now
                    self.assertTrue(sigma.body_script.world.play_compatible_script(instance,
                                                                                    instance.graph.node(34)))
                    self.assertEqual(pool.now, before)
                else:
                    for _ in range(150):
                        self.step(run, source, sigma)
                    self.assertNotIn(absorption.SHIELD_TAG, health[1].temporary_shields)

    def test_grasp_protects_moira_from_actual_control_projectiles_and_helix(self):
        if any(guid not in deflection.tags() for guid in (0x04000000000015E8, 0x040000000000037D,
                                                          0x040000000000072C)):
            self.skipTest('requires external build104319 projectile tags')
        for caster, button, damage in ((0x02E000000000013B, 8, 5), (0x02E0000000000042, 16, 25),
                                        (SOLDIER, 2, 120)):
            for scenario in ('grasp', 'off_axis', 'ally', 'expired', 'wall'):
                with self.subTest(caster=caster, scenario=scenario):
                    run = Run(KANEZAKA_DM, names=('Caster', 'Sigma', 'Moira'))
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    source, sigma, recipient = run.players
                    sigma_team = 0 if scenario == 'ally' else 1
                    sigma_x = 2.5 if scenario == 'off_axis' else 0.85
                    for player, hero, team, position in (
                        (source, heroes()[caster], 0, [0, 1, 0]),
                        (sigma, heroes()[absorption.HERO], sigma_team, [sigma_x, 1, 5]),
                        (recipient, heroes()[fixtures.MOIRA], 1, [0, 1, 6.5]),
                    ):
                        self.assertTrue(match.switch_hero(player, hero))
                        player.team = team
                        player.mover.position = position
                        player.mover.body = None
                    for _ in range(30):
                        self.step(run, source, sigma)
                    self.step(run, source, sigma, grasp=True)
                    instance = next(i for i in sigma.body_script.component.instances.values()
                                    if i.graph.index == absorption.GRASP)
                    self.assertTrue(instance.state(1).active)
                    if scenario == 'expired':
                        for _ in range(160):
                            self.step(run, source, sigma)
                        self.assertFalse(instance.state(1).active)
                    fight = match.combat
                    targets = {t.owner: t for t in fight._targets()}
                    victim = fight.vitals_of(targets[recipient])
                    sigma_health = fight.vitals_of(targets[sigma])
                    before = victim.total
                    world = fight._collision()
                    if scenario == 'wall':
                        world = World([(-10, -5, 1, 10, -5, 1, 10, 10, 1),
                                       (-10, -5, 1, 10, 10, 1, -10, 10, 1)])
                    controlled = False
                    asleep = False
                    rocket = None
                    with (patch.object(fight, '_collision', return_value=world),
                          patch.object(absorption, 'absorbed', wraps=absorption.absorbed) as consume,
                          patch.object(fight, '_landed', wraps=fight._landed) as landed):
                        for frame in range(65):
                            self.step(run, source, sigma, button if frame == 0 else 0)
                            if fight.rockets:
                                rocket = fight.rockets[0]
                            controlled |= fight.is_stunned(recipient)
                            asleep |= fight.is_asleep(recipient)
                        self.assertIsNotNone(rocket)
                        self.assertNotIn(rocket, fight.rockets)
                        if scenario == 'grasp':
                            consume.assert_called_once_with(instance.state(1), damage)
                            landed.assert_not_called()
                        else:
                            consume.assert_not_called()
                            landed.assert_called_once_with(rocket)
                    impacted = scenario not in ('grasp', 'wall')
                    self.assertEqual(before - victim.total, damage if impacted else 0)
                    self.assertEqual(controlled, impacted and caster != SOLDIER)
                    self.assertEqual(asleep, impacted and caster == 0x02E000000000013B)
                    if asleep:
                        self.assertAlmostEqual(victim.sleep_until - victim.sleep_wake_after, 4.5)
                    if scenario == 'grasp':
                        self.assertEqual(instance.vars[21312].value(), damage)
                        pool = self.grant(run, source, sigma, sigma_health)
                        self.assertAlmostEqual(pool.now, damage * 0.6, places=4)
                    else:
                        self.assertNotIn(absorption.SHIELD_TAG, sigma_health.temporary_shields)

    def test_ally_and_off_axis_orbs_are_not_consumed(self):
        for ally, offset in ((True, 0), (False, 3)):
            with self.subTest(ally=ally, offset=offset):
                run, source, sigma, instance, _health = self.fight(ally=ally, offset=offset)
                orb = self.launch(run, source, sigma)
                for _ in range(60):
                    self.step(run, source, sigma)
                self.assertIn(orb, run.match.combat.moira_orbs)
                self.assertEqual(instance.vars[21312].value(), 0)

    def test_live_ice_wall_and_grasp_resolve_by_first_contact(self):
        for healing in (False, True):
            for wall_first in (False, True):
                with self.subTest(healing=healing, wall_first=wall_first):
                    run, source, sigma, instance, health = self.fight(distance=8)
                    match, fight = run.match, run.match.combat
                    x, y, z = source.mover.position
                    hero = find_hero("mei")
                    mei = match.add_player(3, "Mei", hero.guid, source.team, False)
                    in_world(match, mei)
                    run.players.append(mei)
                    self.assertTrue(match.switch_hero(mei, hero))
                    mei.team = source.team
                    mei.mover.position = [x, y, z + 16]
                    mei.mover.body = None
                    for _ in range(20):
                        self.step(run, source, sigma)
                    wall_aim = aim(fight._eye(mei), (x, y, z + (2 if wall_first else 10)))
                    for button in (16, 0, 1, 0):
                        run.step({mei: (*wall_aim, button), sigma: (-32768, 0, 0)})
                    self.assertEqual(len(fight.walls.pieces), 5)
                    self.assertTrue(instance.states[1].active)
                    origin, direction = fight._eye(source), combat.aim(0, 0)
                    wall = fight.walls.cast(origin, direction, 20)
                    grasp = absorption.grasp_intersection(fight, source, origin, direction, 20)
                    self.assertIsNotNone(wall)
                    self.assertIsNotNone(grasp)
                    self.assertEqual(wall[0] < grasp[0], wall_first)
                    orb = self.launch(run, source, sigma, healing=healing)
                    energy = orb.energy
                    before = health[1].total
                    for _ in range(70):
                        if orb.bounces or orb not in fight.moira_orbs:
                            break
                        self.step(run, source, sigma)
                    self.assertEqual(orb in fight.moira_orbs, wall_first)
                    self.assertEqual(orb.bounces, int(wall_first))
                    self.assertAlmostEqual(energy - orb.energy, before - health[1].total, places=4)
                    if wall_first or healing:
                        self.assertEqual(orb.energy, energy)
                    self.assertEqual(instance.vars[21312].value(), 0 if wall_first or healing else 100)
                    self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values()))

    def test_static_wall_before_grasp_prevents_absorption(self):
        run, source, sigma, instance, _health = self.fight()
        orb = self.launch(run, source, sigma)
        x, y, z = source.mover.position
        z += 1.5
        wall = World([
            (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
            (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
        ])
        with patch.object(run.match.combat, '_collision', return_value=wall):
            for _ in range(60):
                self.step(run, source, sigma)
        self.assertIn(orb, run.match.combat.moira_orbs)
        self.assertGreater(orb.bounces, 0)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_expired_or_dead_grasp_does_not_consume_later_orb(self):
        for dead in (False, True):
            with self.subTest(dead=dead):
                run, source, sigma, instance, _health = self.fight()
                if dead:
                    target = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(source, target, 1000, False, None, run.frame)
                else:
                    for _ in range(150):
                        self.step(run, source, sigma)
                    self.assertFalse(instance.states[1].active)
                orb = self.launch(run, source, sigma)
                for _ in range(40):
                    self.step(run, source, sigma)
                self.assertIn(orb, run.match.combat.moira_orbs)
                self.assertEqual(instance.vars[21312].value(), 0)

    def test_grasp_does_not_absorb_moira_beams_or_melee(self):
        for attack in ('grasp', 'ultimate', 'melee'):
            with self.subTest(attack=attack):
                run, source, sigma, instance, health = self.fight(distance=2 if attack == 'melee' else 6)
                if attack == 'ultimate':
                    source.body_script.set_ult_charge(source.body_script.component.entity_var(ULT_COST).value())
                    self.step(run, source, sigma, 32)
                    for _ in range(60):
                        ult = next(i for i in source.body_script.component.instances.values()
                                   if i.graph.index == 0x1007)
                        if ult.states.get(20) is not None and ult.states[20].active:
                            break
                        self.step(run, source, sigma)
                before = health[1].total
                for frame in range(20):
                    buttons = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and frame == 0 else 0
                    self.step(run, source, sigma, buttons)
                self.assertAlmostEqual(before - health[1].total,
                                       {'grasp': 16, 'ultimate': 22.4, 'melee': 30}[attack], places=4)
                self.assertEqual(instance.vars[21312].value(), 0)

    def test_spray_passes_enemy_grasp_to_an_ally_without_absorption(self):
        run, source, sigma, instance, health = self.fight(recipient=True)
        health[2].damage(100)
        for _ in range(40):
            self.step(run, source, sigma, 1)
        self.assertGreater(health[2].total, 100)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_temporary_shields_are_not_refilled_and_decay_without_base_shield_loss(self):
        run, source, sigma, _instance, health = self.fight()
        orb = self.launch(run, source, sigma)
        self.consume(run, source, sigma, orb)
        pool = self.grant(run, source, sigma, health[1])
        self.assertAlmostEqual(pool.now, 60, places=4)
        parts = health[1].component(health[1].ids)[51]
        slot = combat.SHIELDS_POOL * combat.POOLS
        base_record = struct.unpack('<IffI4B', parts[slot])
        extra_record = struct.unpack('<IffI4B', parts[slot + 1])
        self.assertNotEqual(base_record[0], extra_record[0])
        self.assertEqual(base_record[1], health[1].most[combat.SHIELDS_POOL])
        self.assertAlmostEqual(extra_record[1], 60, places=4)
        self.assertAlmostEqual(extra_record[2], 60, places=4)
        health[1].damage(30)
        self.assertAlmostEqual(pool.now, 30, places=4)
        before = dict(health[1].now)
        health[1].heal(100)
        self.assertAlmostEqual(pool.now, 30, places=4)
        base = dict(health[1].now)
        self.assertGreaterEqual(sum(base.values()), sum(before.values()))
        for _ in range(1000):
            self.step(run, source, sigma)
        self.assertEqual(pool.now, 0)
        self.assertEqual(health[1].now, base)
        cleared = struct.unpack('<IffI4B', health[1].component(health[1].ids)[51][slot + 1])
        self.assertEqual(cleared[0], extra_record[0])
        self.assertEqual(cleared[1:3], (0, 0))

    def test_moira_healing_orb_restores_missing_base_health_despite_extra_temporary_shields(self):
        run, source, sigma, _instance, health = self.fight()
        health[1].damage(5)
        orb = self.launch(run, source, sigma)
        self.consume(run, source, sigma, orb)
        pool = self.grant(run, source, sigma, health[1])
        health[1].damage(30)  # remove only temporary HP; base deficit stays
        self.assertGreater(health[1].total, sum(health[1].most.values()))
        self.assertLess(sum(health[1].now.values()), sum(health[1].most.values()))
        sigma.team = source.team
        # A fresh actual Moira supplies an available orb without changing the
        # original launcher's cooldown, Sigma grant, or decay clock.
        healer = run.match.add_player(3, 'Healer', fixtures.MOIRA, 2, False)
        in_world(run.match, healer)
        run.players.append(healer)
        run.match.switch_hero(healer, heroes()[fixtures.MOIRA])
        healer.team = source.team
        x, y, z = source.mover.position
        healer.mover.position = [x + 2, y, z]
        for _ in range(20):
            self.step(run, healer, sigma)
        before = sum(health[1].now.values()), pool.now
        self.launch(run, healer, sigma, healing=True)
        for _ in range(30):
            self.step(run, healer, sigma)
        self.assertGreater(sum(health[1].now.values()), before[0])
        self.assertEqual(pool.now, before[1])
