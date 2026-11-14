"""Moira attacks versus actual Genji Deflect commands; declared contact geometry."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_moira as fixtures
import test_game_moira_charge as charge_fixtures
from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import combat, deflection, stats
from ow174.game.collision import World
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.world import HEALTH_POOL

GENJI = 0x02E0000000000029


class MoiraDeflectTests(unittest.TestCase):
    start_ult = fixtures.MoiraOpenAcceptanceTests.start_ult

    def fight(self, *, distance=2, ally=False, facing=-32768, active=True):
        run = Run(names=('Moira', 'Genji'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, genji = run.players
        match.switch_hero(source, heroes()[fixtures.MOIRA])
        match.switch_hero(genji, heroes()[GENJI])
        genji.team = source.team if ally else 1 - source.team
        x, y, z = source.mover.position
        genji.mover.position = [x, y, z + distance]
        for _ in range(20):
            self.step(run, genji, facing=facing)
        for frame in range(5):
            self.step(run, genji, facing=facing, deflect=active and frame == 0)
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x3A3)
        self.assertEqual(10 in instance.states and instance.states[10].active, active)
        targets = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(targets[p]) for p in (source, genji)]
        charge_fixtures.MoiraCombatChargeTests.reset(source)
        return run, source, genji, health

    @staticmethod
    def step(run, genji, source=None, button=0, *, facing=-32768, deflect=False):
        commands = {genji: (facing, 0, 16 if deflect else 0)}
        if source is not None:
            commands[source] = (0, 0, button)
        run.step(commands)

    def launch(self, run, source, genji, *, healing=False, facing=-32768):
        self.step(run, genji, source, 16, facing=facing)
        for _ in range(40):
            self.step(run, genji, source, 1 if healing else 2, facing=facing)
            if run.match.combat.moira_orbs:
                return run.match.combat.moira_orbs[-1]
        self.fail('authored orb emitted no flight')

    def reflect(self, run, source, genji, orb):
        for _ in range(80):
            self.step(run, genji)
            if orb.shooter is genji:
                return
        self.fail('frontal active Deflect never transferred the real orb')

    def test_damage_orb_transfers_owner_body_direction_budget_and_damage_credit(self):
        run, source, genji, health = self.fight()
        orb = self.launch(run, source, genji)
        shot = orb.shot
        fight = run.match.combat
        with patch.object(fight, '_damage', wraps=fight._damage) as damage:
            self.reflect(run, source, genji, orb)
        self.assertEqual(orb.launch_body, source.body)
        self.assertTrue(damage.call_args_list)
        reflected = [call for call in damage.call_args_list if call.args[0] is genji]
        unreflected = [call for call in damage.call_args_list if call.args[0] is source]
        self.assertTrue(reflected)
        self.assertTrue(unreflected)
        for calls, owner, tags in (
                (unreflected, source, (0x2DFD, 0x2F99, 0x4D29, 0x4910)),
                (reflected, genji, (0x2DFD, 0x2F99, 0x19C4, 0x4D29, 0x4910))):
            for call in calls:
                self.assertEqual(call.kwargs['source_body'], owner.body)
                self.assertEqual(tuple(tag & 0xFFFF for tag in call.kwargs['hit_tags']), tags)
        # Reflection preserves the live cast's delivery. Native reflected
        # motion/source rebinding remains separately unaccepted.
        self.assertIs(orb.shot, shot)
        self.assertEqual(orb.body, genji.body)
        self.assertAlmostEqual(orb.direction[2], -1)
        self.assertFalse(orb.healing)
        self.assertLess(orb.energy, 200)  # aura can act before body contact
        self.assertAlmostEqual(orb.left + orb.flown, 7)
        before = health[0].total, health[1].total, orb.energy
        old_charge = charge_fixtures.MoiraCombatChargeTests.charge(source)
        old_damage = source.stats.value(stats.HERO_DAMAGE)
        for _ in range(10):
            self.step(run, genji)
        self.assertAlmostEqual(before[0] - health[0].total, 8, places=4)
        self.assertEqual(health[1].total, before[1])
        self.assertAlmostEqual(before[2] - orb.energy, 8, places=4)
        self.assertGreater(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), old_damage)
        self.assertEqual(charge_fixtures.MoiraCombatChargeTests.charge(source), old_charge)
        self.assertAlmostEqual(orb.left + orb.flown, 7)

    def test_actual_projectiles_reflect_and_recheck_allegiance(self):
        for hero, button, damage, definition in (
            (0x02E000000000013B, 8, 5, 0x04000000000015E8),
            (0x02E0000000000042, 16, 25, 0x040000000000037D),
            (SOLDIER, 2, 120, 0x040000000000072C),
        ):
            if definition not in deflection.tags():
                self.skipTest('requires external build104319 projectile tags')
            for scenario in ('return', 'redirect', 'fade', 'friendly_redirect', 'rear', 'ally', 'wall'):
                with self.subTest(hero=hero, scenario=scenario):
                    run = Run(KANEZAKA_DM, names=('Caster', 'Genji', 'Moira'))
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    source, genji, moira = run.players
                    moira_team = 0 if scenario in ('redirect', 'fade') else 1
                    genji_team = 0 if scenario == 'ally' else 1
                    for player, guid, team, position in (
                        (source, hero, 0, [0, 1, 0]),
                        (genji, GENJI, genji_team, [0, 1, 3]),
                        (moira, fixtures.MOIRA, moira_team, [2.7, 1, 2.1]),
                    ):
                        self.assertTrue(match.switch_hero(player, heroes()[guid]))
                        player.team = team
                        player.mover.position = position
                        player.mover.body = None
                    for _ in range(30):
                        run.step({})
                    fight = match.combat
                    facing = (0 if scenario == 'rear' else -32768), 0
                    if scenario in ('redirect', 'fade', 'friendly_redirect'):
                        x, _, z = fight._eye(genji)
                        facing = aim((x, fight._eye(source)[1], z - 0.4), fight._eye(moira))
                    run.step({genji: (*facing, 16)})
                    instance = next(i for i in genji.body_script.component.instances.values()
                                    if i.graph.index == 0x03A3)
                    self.assertTrue(instance.state(10).active)
                    targets = {target.owner: target for target in fight._targets()}
                    health = [fight.vitals_of(targets[player]) for player in run.players]
                    before = [vitals.total for vitals in health]
                    world = fight._collision()
                    if scenario == 'wall':
                        world = World([(-10, -5, 0.2, 10, -5, 0.2, 10, 10, 0.2),
                                       (-10, -5, 0.2, 10, 10, 0.2, -10, 10, 0.2)])
                    control = set()
                    asleep = set()
                    budget = None
                    continued_in_contact_step = None
                    with (patch.object(fight, '_collision', return_value=world),
                          patch.object(fight, '_reflect_projectile', wraps=fight._reflect_projectile) as reflected,
                          patch.object(fight, '_apply_hit', wraps=fight._apply_hit) as hits):
                        for frame in range(75):
                            run.step({source: (0, 0, button if frame == 0 else 0), genji: (*facing, 0),
                                      moira: (0, 0, 8 if scenario == 'fade' and frame == 0 else 0)})
                            control.update(player for player in run.players if fight.is_stunned(player))
                            asleep.update(player for player in run.players if fight.is_asleep(player))
                            if fight.rockets:
                                live = fight.rockets[0]
                                if budget is None:
                                    budget = live.left + live.flown
                                self.assertAlmostEqual(live.left + live.flown, budget)
                                if reflected.called and continued_in_contact_step is None:
                                    continued_in_contact_step = live.position != reflected.call_args.args[2]
                            elif reflected.called and continued_in_contact_step is None:
                                continued_in_contact_step = False
                        if scenario in ('return', 'redirect', 'fade', 'friendly_redirect'):
                            reflected.assert_called_once()
                            rocket = reflected.call_args.args[0]
                            self.assertIs(rocket.shooter, genji)
                            self.assertEqual(rocket.source_body, genji.body)
                            self.assertEqual(rocket.team, genji.team)
                            self.assertEqual(rocket.definition, definition)
                            self.assertIsNone(rocket.shot)
                            self.assertTrue(continued_in_contact_step)
                            victim = source if scenario == 'return' else moira
                            index = run.players.index(victim)
                            protected = scenario in ('fade', 'friendly_redirect')
                            self.assertEqual(before[index] - health[index].total, 0 if protected else damage)
                            self.assertEqual(victim in control, not protected and hero != SOLDIER)
                            self.assertEqual(asleep, {victim} if not protected and definition == 0x04000000000015E8
                                             else set())
                            if victim in asleep:
                                self.assertAlmostEqual(health[index].sleep_until - health[index].sleep_wake_after,
                                                       4.5)
                            self.assertEqual(before[1], health[1].total)
                            self.assertNotIn(genji, control)
                            attributed = any(call.args[0] is genji and call.args[1].owner is victim
                                             and call.kwargs['source_body'] == genji.body
                                             for call in hits.call_args_list)
                            self.assertEqual(attributed, scenario != 'friendly_redirect')
                        else:
                            reflected.assert_not_called()
                            if scenario == 'rear':
                                self.assertEqual(before[1] - health[1].total, damage)
                                self.assertEqual(genji in control, hero != SOLDIER)
                                self.assertEqual(asleep, {genji} if definition == 0x04000000000015E8 else set())
                            else:
                                self.assertEqual(before[1], health[1].total)
                                self.assertNotIn(genji, control)
                                self.assertFalse(asleep)

    def test_healing_orb_retains_operation_and_changes_healable_allegiance(self):
        run, source, genji, health = self.fight()
        for vitals in health:
            vitals.damage(100)
        orb = self.launch(run, source, genji, healing=True)
        self.reflect(run, source, genji, orb)
        self.assertTrue(orb.healing)
        before = health[0].total, health[1].total, orb.energy
        for _ in range(10):
            self.step(run, genji)
        self.assertEqual(health[0].total, before[0])
        self.assertAlmostEqual(health[1].total - before[1], 10.4, places=4)
        self.assertAlmostEqual(before[2] - orb.energy, 10.4, places=4)

    def test_rear_or_ally_deflect_does_not_transfer_orb(self):
        for ally, facing in ((False, 0), (True, -32768)):
            with self.subTest(ally=ally, facing=facing):
                run, source, genji, _health = self.fight(ally=ally, facing=facing)
                orb = self.launch(run, source, genji, facing=facing)
                for _ in range(25):
                    self.step(run, genji, facing=facing)
                self.assertIs(orb.shooter, source)

    def test_live_ice_wall_and_deflect_resolve_by_first_contact(self):
        for healing in (False, True):
            for wall_first in (False, True):
                with self.subTest(healing=healing, wall_first=wall_first):
                    run, source, genji, health = self.fight(distance=4, active=False)
                    match = run.match
                    fight = match.combat
                    x, y, z = source.mover.position
                    hero = find_hero("mei")
                    mei = match.add_player(3, "Mei", hero.guid, source.team, False)
                    in_world(match, mei)
                    run.players.append(mei)
                    self.assertTrue(match.switch_hero(mei, hero))
                    mei.team = source.team
                    mei.mover.position = [x, y, z + 12]
                    mei.mover.body = None
                    for _ in range(25):
                        self.step(run, genji)
                    wall_aim = aim(fight._eye(mei), (x, y, z + (2 if wall_first else 7)))
                    for button in (16, 0, 1, 0):
                        run.step({mei: (*wall_aim, button), genji: (-32768, 0, 0)})
                    self.assertEqual(len(fight.walls.pieces), 5)
                    self.step(run, genji, deflect=True)
                    deflect = next(i for i in genji.body_script.component.instances.values()
                                   if i.graph.index == 0x3A3)
                    self.assertTrue(deflect.states[10].active)
                    orb = self.launch(run, source, genji, healing=healing)
                    before = sum(pool.total for pool in health)
                    energy = orb.energy
                    for _ in range(80):
                        if orb.bounces or orb.shooter is genji:
                            break
                        self.step(run, genji)
                    owner = source if wall_first else genji
                    self.assertIs(orb.shooter, owner)
                    self.assertEqual(orb.body, owner.body)
                    self.assertEqual(orb.bounces, int(wall_first))
                    self.assertEqual(orb.healing, healing)
                    self.assertLess(orb.direction[2], -0.95)
                    self.assertAlmostEqual(orb.left + orb.flown, 7)
                    self.assertAlmostEqual(energy - orb.energy, before - sum(pool.total for pool in health),
                                           places=4)
                    if wall_first:
                        self.assertEqual(orb.energy, energy)
                    self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values()))

    def test_static_wall_precedes_deflect_contact(self):
        run, source, genji, _health = self.fight()
        orb = self.launch(run, source, genji)
        x, y, z = source.mover.position
        z += 1
        wall = World([
            (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
            (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
        ])
        with patch.object(run.match.combat, '_collision', return_value=wall):
            for _ in range(25):
                self.step(run, genji)
        self.assertIs(orb.shooter, source)
        self.assertGreater(orb.bounces, 0)

    def test_grasp_and_coalescence_damage_active_deflect_while_melee_is_only_blocked(self):
        for attack in ('grasp', 'beam', 'melee'):
            with self.subTest(attack=attack):
                run, source, genji, health = self.fight(distance=1.5)
                if attack == 'beam':
                    # Start the actual ultimate without losing defender facing.
                    from ow174.game.script.driver import ULT_COST
                    source.body_script.set_ult_charge(
                        source.body_script.component.entity_var(ULT_COST).value())
                    self.step(run, genji, source, 32)
                    for _ in range(60):
                        instance = next(i for i in source.body_script.component.instances.values()
                                        if i.graph.index == 0x1007)
                        if instance.states.get(20) is not None and instance.states[20].active:
                            break
                        self.step(run, genji)
                    self.assertTrue(instance.states[20].active)
                velocity = genji.mover.velocity
                self.step(run, genji, source, 0x800 if attack == 'melee' else 2 if attack == 'grasp' else 0)
                if attack == 'melee':
                    self.assertEqual(genji.mover.velocity, velocity)
                before = health[1].total
                for _ in range(20):
                    self.step(run, genji, source, 2 if attack == 'grasp' else 0)
                if attack == 'melee':
                    self.assertEqual(health[1].total, 200)
                    self.assertEqual(health[0].total, 200)
                else:
                    self.assertAlmostEqual(before - health[1].total,
                                           16 if attack == 'grasp' else 22.4, places=4)

    def test_expired_deflect_cannot_transfer_a_later_orb(self):
        run, source, genji, _health = self.fight()
        for _ in range(140):
            self.step(run, genji)
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x3A3)
        self.assertFalse(instance.states[10].active)
        orb = self.launch(run, source, genji)
        for _ in range(25):
            self.step(run, genji)
        self.assertIs(orb.shooter, source)

    def test_dead_defender_cannot_transfer_an_incoming_orb(self):
        run, source, genji, health = self.fight()
        orb = self.launch(run, source, genji)
        target = next(t for t in run.match.combat._targets() if t.owner is genji)
        run.match.combat._damage(source, target, 1000, False, None, run.frame)
        self.assertTrue(health[1].dead)
        for _ in range(25):
            self.step(run, genji)
        self.assertIs(orb.shooter, source)

    def test_reflection_cannot_reset_or_bypass_moiras_orb_cooldown(self):
        run, source, genji, _health = self.fight()
        orb = self.launch(run, source, genji)
        ability = next(i for i in source.body_script.component.instances.values()
                       if i.graph.index == 0x10E4).states[0]
        before = ability.remaining()
        self.reflect(run, source, genji, orb)
        for frame in range(20):
            self.step(run, genji, source, 16 if frame % 2 == 0 else 0)
        self.assertGreater(ability.remaining(), 0)
        self.assertLess(ability.remaining(), before)
        self.assertEqual(run.match.combat.moira_orbs, [orb])

    def test_reflected_orb_kill_is_credited_to_genji(self):
        run, source, genji, health = self.fight()
        orb = self.launch(run, source, genji)
        self.reflect(run, source, genji, orb)
        health[0].now[HEALTH_POOL] = 1
        for _ in range(2):
            self.step(run, genji)
        self.assertTrue(health[0].dead)
        self.assertEqual(health[0].killer, genji.body)
        self.assertEqual(source.client.messages(combat.DEATH_NOTICE)[-1]['+0x7C']['+0x0'], genji.body)
        self.assertEqual(genji.client.messages(combat.KILL_NOTICE)[-1]['+0x80']['+0x0'], genji.body)

    def test_spray_keeps_healing_allegiance_without_deflect_transfer(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, genji, health = self.fight(ally=ally)
                health[1].damage(100)
                for _ in range(30):
                    self.step(run, genji, source, 1)
                if ally:
                    self.assertGreater(health[1].total, 100)
                else:
                    self.assertEqual(health[1].total, 100)
                self.assertEqual(health[0].total, 200)
                self.assertEqual(run.match.combat.moira_orbs, [])
