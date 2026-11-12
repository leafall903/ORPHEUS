"""Actual 025F launch and Moira admission to the reconstructed 03E2 field."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_moira_sleep as sleep_tests
from test_game_combat import Run, aim, in_world

from ow174.game import absorption, combat, deflection, graviton, moira
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class MoiraGravitonTests(unittest.TestCase):
    def scene(self, *, fade=False, friendly=False, offset=2.5, guard=None, coalescing=False):
        run = Run(names=('Moira', 'Zarya', 'Guard') if guard else ('Moira', 'Zarya'))
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        victim, caster = run.players[:2]
        for actor, hero in ((victim, 'moira'), (caster, 'zarya')):
            self.assertTrue(match.switch_hero(actor, find_hero(hero)))
            actor.mover.body = None
        victim.team, caster.team = 0, 0 if friendly else 1
        x, y, z = victim.mover.position
        caster.mover.position[:] = (x, y, z + 8)
        guard_input = {}
        if guard:
            defender = run.players[2]
            hero = find_hero(guard)
            if guard == 'dva':
                hero = replace(hero, body=0x0400000000000407)
            self.assertTrue(match.switch_hero(defender, hero))
            defender.team = victim.team
            defender.mover.position[:] = {
                'dva': (x + 1.1, y, z + 6),
                'mei': (x - 9, y, z),
                'ana': (x - 6, y, z),
            }.get(guard, (x, y, z + 4))
            defender.mover.body = None
            guard_input[defender] = (0, 0, {'dva': 2, 'sigma': 8, 'genji': 16}.get(guard, 0))
        for _ in range(100 if guard == 'dva' else 30):
            run.step({})
        if guard == 'dva':
            for _ in range(4):
                run.step(guard_input)
            matrix = next(i for i in defender.body_script.component.instances.values()
                          if i.graph.index == 0x02CC)
            self.assertTrue(matrix.state(3).active)
        target = next(target for target in fight._targets() if target.owner is victim)
        health = fight.vitals_of(target)
        if coalescing:
            self.start_coalescence(run, victim, caster)
        rocket = self.launch_graviton(run, victim, caster, offset,
                                      {**guard_input, victim: (0, 0, 8 if fade else 0)})
        return run, victim, caster, health, rocket

    def launch_graviton(self, run, victim, caster, offset, inputs):
        fight = run.match.combat
        x, y, z = victim.mover.position
        direction = aim(fight._eye(caster), (x + offset, y + 0.85, z))
        script = caster.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2100)
        script.set_ult_charge(cost)
        run.step({**inputs, caster: (*direction, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        rocket, = fight.rockets
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.direct.amount, 5)
        return rocket

    def until_landed(self, run, rocket):
        fight = run.match.combat
        existing = tuple(fight.zarya_gravitons.fields)
        for _ in range(90):
            run.step({})
            landed = [field for field in fight.zarya_gravitons.fields
                      if all(field is not previous for previous in existing)]
            if landed:
                break
        self.assertNotIn(rocket, fight.rockets)
        field, = landed
        self.assertEqual(field.radius, 6)
        self.assertEqual(field.end_tick - field.last_tick, 250)
        return field

    def start_coalescence(self, run, source, enemy):
        fight = run.match.combat
        script = source.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        direction = aim(fight._eye(source), fight._eye(enemy))
        run.step({source: (*direction, 32)})
        self.assertEqual(charge.value(), 0)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1007)
        for _ in range(60):
            state = instance.states.get(20)
            if state is not None and state.active:
                return instance
            direction = aim(fight._eye(source), fight._eye(enemy))
            run.step({source: (*direction, 0)})
        self.fail("Coalescence did not enter its effect phase")

    def test_actual_graviton_pulls_blocks_fade_but_allows_grasp_then_releases(self):
        run, victim, caster, health, rocket = self.scene()
        fight = run.match.combat
        field = self.until_landed(run, rocket)
        caster_target = next(target for target in fight._targets() if target.owner is caster)
        caster_health = fight.vitals_of(caster_target)
        caster_before = caster_health.total
        start = victim.mover.position[0]
        self.assertEqual(health.total, 200)
        for _ in range(16):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertIn(victim.body, fight.zarya_gravitons.pulled)
            pos = tuple(victim.mover.position)
            direction = aim(fight._eye(victim), fight._eye(caster))
            victim.take_commands([Command(run.frame, yaw=direction[0], pitch=direction[1],
                                          buttons=2 | 8, forward=127, right=127)])
            self.assertEqual(tuple(victim.mover.position), pos)
            self.assertFalse(moira.fading(victim))
        self.assertGreater(victim.mover.position[0], start + 0.4)
        self.assertLess(caster_health.total, caster_before)
        self.assertGreater(health.total, 195)  # Grasp can heal while the field ticks.
        while run.frame < field.end_tick:
            run.step({})
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
        run.step({victim: (0, 0, 8)})
        self.assertTrue(moira.fading(victim))

    def test_active_fade_excludes_moira_until_its_authored_phase_ends(self):
        run, victim, _, health, rocket = self.scene(fade=True, offset=1.5)
        fight = run.match.combat
        field = self.until_landed(run, rocket)
        self.assertTrue(moira.fading(victim))
        for _ in range(60):
            if not moira.fading(victim):
                break
            self.assertEqual(health.total, 200)
            self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
            run.step({})
        self.assertFalse(moira.fading(victim))
        run.step({})
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertAlmostEqual(health.total, 199.92, places=5)

    def test_graviton_rejects_fade_buffered_before_its_first_pull(self):
        run, victim, caster, _, rocket = self.scene(friendly=True)
        launch = run.frame
        field = self.until_landed(run, rocket)
        flight_ticks = field.last_tick - launch
        while run.frame < field.end_tick:
            run.step({})
        caster.team = 1 - victim.team
        instance = next(i for i in victim.body_script.component.instances.values() if i.graph.index == 0x0F7C)
        ability = instance.states[9]
        run.step({victim: (0, 0, 8)})
        self.assertTrue(moira.fading(victim))
        first = instance.states[1].counter
        for _ in range(70):
            run.step({})
        self.assertGreater(ability.remaining(), 5)
        threshold = (flight_ticks + 5) * combat.TICK
        for _ in range(430):
            if ability.remaining() <= threshold:
                break
            run.step({})
        self.assertLessEqual(ability.remaining(), threshold)
        rocket = self.launch_graviton(run, victim, caster, 2.5, {})
        for _ in range(flight_ticks - 1):
            run.step({})
        fight = run.match.combat
        self.assertIn(rocket, fight.rockets)
        self.assertGreater(ability.remaining(), 0)
        self.assertLess(ability.remaining(), 0.15)
        run.step({victim: (0, 0, 8)})
        self.assertTrue(ability.presses)
        self.assertFalse(moira.fading(victim))
        field, = fight.zarya_gravitons.fields
        for _ in range(12):
            run.step({})
            self.assertIn(victim.body, fight.zarya_gravitons.pulled)
            self.assertFalse(ability.presses)
            self.assertFalse(moira.fading(victim))
        self.assertEqual(instance.states[1].counter, first)
        self.assertEqual(ability.remaining(), 0)
        while run.frame < field.end_tick:
            run.step({})
        self.assertFalse(moira.fading(victim))
        run.step({victim: (0, 0, 8)})
        self.assertEqual(instance.states[1].counter, first + 1)
        self.assertTrue(moira.fading(victim))

    def test_coalescence_starts_while_pulled_and_continues_past_field_expiry(self):
        run, victim, caster, health, rocket = self.scene()
        fight = run.match.combat
        field = self.until_landed(run, rocket)
        run.step({})
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        health.damage(90)
        instance = self.start_coalescence(run, victim, caster)
        charge = victim.body_script.component.entity_var(ULT_CHARGE)
        enemy = next(target for target in fight._targets() if target.owner is caster)
        enemy_health = fight.vitals_of(enemy)
        before = enemy_health.total, health.total
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertIn(victim.body, fight.zarya_gravitons.pulled)
            position = tuple(victim.mover.position)
            yaw, pitch = aim(fight._eye(victim), fight._eye(caster))
            victim.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=8, forward=127)])
            self.assertEqual(tuple(victim.mover.position), position)
            self.assertFalse(moira.fading(victim))
        self.assertAlmostEqual(before[0] - enemy_health.total, 22.4, places=4)
        self.assertAlmostEqual(health.total - before[1], 14.4, places=4)
        self.assertEqual(charge.value(), 0)

        while run.frame < field.end_tick:
            direction = aim(fight._eye(victim), fight._eye(caster))
            run.step({victim: (*direction, 0)})
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
        self.assertTrue(instance.states[20].active)
        before = enemy_health.total
        for _ in range(12):
            direction = aim(fight._eye(victim), fight._eye(caster))
            run.step({victim: (*direction, 0)})
        self.assertAlmostEqual(before - enemy_health.total, 13.44, places=4)
        for _ in range(550):
            if not instance.states[0].flags & nodes.Ability.RUNNING:
                break
            run.step({})
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(instance.states[20].active)

    def test_lethal_graviton_retires_already_active_coalescence_and_pull(self):
        run, victim, caster, health, rocket = self.scene(coalescing=True)
        fight = run.match.combat
        script = victim.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1007)
        self.assertTrue(instance.states[20].active)
        self.assertFalse(fight.zarya_gravitons.fields)
        field = self.until_landed(run, rocket)
        enemy = next(target for target in fight._targets() if target.owner is caster)
        enemy_health = fight.vitals_of(enemy)
        before = enemy_health.total
        direction = aim(fight._eye(victim), fight._eye(caster))
        run.step({victim: (*direction, 0)})
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertTrue(instance.states[20].active)
        self.assertAlmostEqual(before - enemy_health.total, 1.12, places=4)

        health.damage(health.total - 0.04)
        before = enemy_health.total
        run.step({victim: (*direction, 32)})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, field.source_body)
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(instance.states[20].active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(20):
            run.step({victim: (*direction, 32)})
            self.assertEqual(enemy_health.total, before)

    def test_lethal_graviton_cancels_pending_orb_but_preserves_released_orb(self):
        for healing in (False, True):
            for released in (False, True):
                with self.subTest(healing=healing, released=released):
                    run, victim, caster, health, rocket = self.scene()
                    match, fight = run.match, run.match.combat
                    field = self.until_landed(run, rocket)
                    recipient = caster
                    if healing:
                        recipient = match.add_player(3, "Ally", caster.hero.guid, victim.team, False)
                        in_world(match, recipient)
                        run.players.append(recipient)
                        self.assertTrue(match.switch_hero(recipient, caster.hero))
                        recipient.team = victim.team
                        x, y, z = victim.mover.position
                        recipient.mover.position[:] = (x, y, z - 7)
                        recipient.mover.body = None
                    run.step({})
                    body = victim.body
                    self.assertIn(body, fight.zarya_gravitons.pulled)
                    target = next(target for target in fight._targets() if target.owner is recipient)
                    recipient_health = fight.vitals_of(target)
                    if healing:
                        recipient_health.damage(100)
                    before = recipient_health.total
                    instance = next(i for i in victim.body_script.component.instances.values()
                                    if i.graph.index == 0x10E4)
                    ability = instance.states[0]
                    button = 1 if healing else 2
                    direction = aim(fight._eye(victim), fight._eye(recipient))
                    run.step({victim: (*direction, 16)})
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.remaining(), 0)
                    self.assertFalse(fight.moira_orbs)

                    if released:
                        for _ in range(40):
                            direction = aim(fight._eye(victim), fight._eye(recipient))
                            run.step({victim: (*direction, button)})
                            if fight.moira_orbs:
                                break
                        orb, = fight.moira_orbs
                        self.assertEqual(orb.body, body)
                        self.assertEqual(orb.healing, healing)
                        self.assertEqual(orb.energy, 300 if healing else 200)
                        self.assertIn(body, fight.zarya_gravitons.pulled)

                    health.damage(health.total - 0.04)
                    run.step({victim: (*direction, button)})
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, field.source_body)
                    self.assertNotIn(body, fight.zarya_gravitons.pulled)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.remaining() > 0, released)

                    for _ in range(40):
                        run.step({})
                    if released:
                        self.assertEqual(fight.moira_orbs, [orb])
                        self.assertEqual(orb.body, body)
                        delta = recipient_health.total - before
                        self.assertGreater(delta if healing else -delta, 0)
                        self.assertGreater(orb.flown, 0)
                        self.assertLess(orb.energy, 300 if healing else 200)
                    else:
                        self.assertFalse(fight.moira_orbs)
                        self.assertEqual(recipient_health.total, before)

    def test_field_damage_forwards_authored_loadout_tags_and_launch_body(self):
        run, victim, caster, health, rocket = self.scene()
        fight = run.match.combat
        field = self.until_landed(run, rocket)
        old_source = field.source_body
        self.assertTrue(run.match.switch_hero(caster, find_hero('soldier: 76')))
        self.assertNotEqual(caster.body, old_source)
        with patch.object(fight, '_damage', wraps=fight._damage) as damage:
            run.step({})
        damage.assert_called_once()
        shooter, target, amount, critical, loadout, frame = damage.call_args.args
        self.assertIs(shooter, caster)
        self.assertIs(target.owner, victim)
        self.assertAlmostEqual(amount, 0.08)
        self.assertFalse(critical)
        self.assertEqual(loadout, 0x0B9000000000005B)
        self.assertEqual(frame, run.frame)
        self.assertEqual(damage.call_args.kwargs['source_body'], old_source)
        self.assertEqual(damage.call_args.kwargs.get('damage_tags', ()),
                         tuple(0x0D80000000000000 | tag
                               for tag in (0x025A, 0x0990, 0x2D60, 0x3FCE, 0x4D29, 0x4D61)))
        self.assertAlmostEqual(health.total, 199.92)

    def test_sleep_locks_view_during_pull_and_field_damage_can_wake_moira(self):
        run, victim, caster, health, rocket = self.scene(guard='ana')
        fight = run.match.combat
        ana = run.players[2]
        ana.team = caster.team
        field = self.until_landed(run, rocket)
        initial_end = sleep_tests.MoiraSleepTests.cast_at(run, ana, victim)
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertAlmostEqual(initial_end - fight.now, 5)

        captured, attempted = (-32000, 8000), (32000, -8000)
        before = victim.mover.position[0]
        for _ in range(9):
            run.step({victim: (*captured, 0)})
            self.assertIsNone(health.sleep_view)
        run.step({victim: (*attempted, 8 | 2)})
        self.assertIsNotNone(health.sleep_view)
        self.assertEqual((victim.mover.yaw, victim.mover.pitch), captured)
        self.assertGreater(victim.mover.position[0], before)
        while fight.now + combat.TICK < health.sleep_wake_after:
            run.step({victim: (*attempted, 8 | 2)})
            self.assertEqual(health.sleep_until, initial_end)
            self.assertEqual((victim.mover.yaw, victim.mover.pitch), captured)
        for _ in range(3):
            if health.sleep_until < initial_end:
                break
            run.step({victim: (*attempted, 0)})
        self.assertAlmostEqual(health.sleep_until - fight.now, 0.5)
        while fight.now + combat.TICK < health.sleep_until - combat.EPSILON:
            run.step({victim: (*attempted, 0)})
            self.assertEqual((victim.mover.yaw, victim.mover.pitch), captured)
        run.step({victim: (*attempted, 8)})
        self.assertFalse(fight.is_asleep(victim))
        self.assertIsNone(health.sleep_view)
        self.assertEqual((victim.mover.yaw, victim.mover.pitch), attempted)
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertFalse(moira.fading(victim))

    def test_field_tracks_team_and_retains_caster_identity_on_lethal_tick(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, victim, caster, health, rocket = self.scene(friendly=friendly)
                match, fight = run.match, run.match.combat
                field = self.until_landed(run, rocket)
                old_source = caster.body
                self.assertEqual(field.source_body, old_source)
                self.assertTrue(match.switch_hero(caster, find_hero('soldier: 76')))
                self.assertNotEqual(caster.body, old_source)
                if not friendly:
                    health.damage(health.total - 0.05)
                run.step({})
                self.assertEqual(health.dead, not friendly)
                self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
                if friendly:
                    self.assertEqual(health.total, 200)
                else:
                    self.assertEqual(health.killer, old_source)

    def test_actual_graviton_admission_uses_radius_and_static_sight(self):
        run, victim, _, health, rocket = self.scene()
        fight = run.match.combat
        field = self.until_landed(run, rocket)
        x, y, z = victim.mover.position
        middle = (x + field.position[0]) / 2
        wall = World([
            (middle, y - 4, z - 8, middle, y + 4, z - 8, middle, y + 4, z + 8),
            (middle, y - 4, z - 8, middle, y + 4, z + 8, middle, y - 4, z + 8),
        ])
        with patch.object(fight, '_collision', return_value=wall):
            for _ in range(8):
                run.step({})
                self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
                self.assertEqual(health.total, 200)
        run.step({})
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertLess(health.total, 200)

        victim.mover.position[:] = (field.position[0] - 8, y, z)
        victim.mover.body = None
        before = health.total
        run.step({})
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertFalse(victim.mover.driven)
        self.assertEqual(health.total, before)

    def test_live_ice_wall_releases_and_readmits_moira_to_the_same_field(self):
        run, victim, _, health, rocket = self.scene(guard='mei')
        fight = run.match.combat
        mei = run.players[2]
        field = self.until_landed(run, rocket)
        run.step({})
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        x, y, z = victim.mover.position
        direction = aim(fight._eye(mei), ((x + field.position[0]) / 2, y, z))
        for button in (16, 0, 1, 0):
            run.step({mei: (*direction, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
        before = health.total, victim.mover.position[0]
        for _ in range(12):
            run.step({})
            self.assertIn(field, fight.zarya_gravitons.fields)
            self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
            self.assertEqual((health.total, victim.mover.position[0]), before)
        run.step({mei: (*direction, 16)})
        self.assertFalse(fight.walls.pieces)
        run.step({})
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertAlmostEqual(before[0] - health.total, 0.08, places=5)
        self.assertGreater(victim.mover.position[0], before[1])

    def test_actual_graviton_gap_matches_sequential_world_ticks(self):
        def sample(gap):
            run, victim, _, health, rocket = self.scene()
            field = self.until_landed(run, rocket)
            for amount in ([30] if gap else [1] * 30):
                run.frame += amount
                run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(field.last_tick, run.frame)
            self.assertAlmostEqual(health.total, 197.6, places=5)
            return tuple(victim.mover.position), health.total
        self.assertEqual(sample(gap=False), sample(gap=True))

    def test_matrix_and_grasp_remove_actual_graviton_before_field_creation(self):
        for guard in ('dva', 'sigma'):
            with self.subTest(guard=guard):
                run, victim, _, health, rocket = self.scene(offset=0, guard=guard)
                fight = run.match.combat
                self.assertTrue(deflection.absorbable(rocket.definition))
                for _ in range(60):
                    if rocket not in fight.rockets:
                        break
                    commands = {run.players[2]: (0, 0, 2)} if guard == 'dva' else {}
                    run.step(commands)
                self.assertNotIn(rocket, fight.rockets)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
                self.assertEqual(health.total, 200)
                if guard == 'sigma':
                    sigma = run.players[2]
                    state = next(i for i in sigma.body_script.component.instances.values()
                                 if i.graph.index == absorption.GRASP)
                    self.assertEqual(state.vars[21312].value(), 5)

    def test_deflect_transfers_actual_graviton_field_to_genji_and_spares_moira(self):
        run, victim, caster, health, rocket = self.scene(offset=0, guard='genji')
        fight = run.match.combat
        self.assertTrue(deflection.absorbable(rocket.definition))
        field = self.until_landed(run, rocket)
        defender = run.players[2]
        self.assertIs(field.shooter, defender)
        self.assertEqual(field.source_body, defender.body)
        self.assertEqual(field.team, victim.team)
        caster_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
        before = caster_health.total
        run.step({})
        self.assertIn(caster.body, fight.zarya_gravitons.pulled)
        self.assertLess(caster_health.total, before)
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertEqual(health.total, 200)
