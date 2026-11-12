"""Moira orb reconstruction through actual ability-selection/volley input."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import Run, aim, in_world

from ow174.game import combat, cryo, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.world import HEALTH_POOL


class MoiraOrbTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    def launch(self, run, source, *, healing=False):
        run.step({source: (0, 0, 16)})
        for _ in range(40):
            run.step({source: (0, 0, 1 if healing else 2)})
            if run.match.combat.moira_orbs:
                return run.match.combat.moira_orbs[-1]
        self.fail("authored orb selection emitted no projectile")

    def test_actual_orb_release_reads_only_its_launch_owners_budget_scalar(self):
        for healing, key, other_key, base in ((True, 12618, 12619, 300), (False, 12619, 12618, 200)):
            for scalar in (None, 0, 0.5, 1.5):
                with self.subTest(healing=healing, scalar=scalar):
                    run, source, target, health = self.setup_fight(ally=healing)
                    component = source.body_script.component
                    component.set(other_key, 8)
                    if scalar is not None:
                        component.set(key, scalar)
                    before = health.total
                    orb = self.launch(run, source, healing=healing)
                    expected = base if scalar is None else base * scalar
                    self.assertEqual(orb.energy, expected)
                    self.assertEqual(orb.body, source.body)
                    component.set(key, 7)
                    self.assertEqual(orb.energy, expected)
                    if not expected:
                        for _ in range(45):
                            run.step({})
                        self.assertNotIn(orb, run.match.combat.moira_orbs)
                        self.assertEqual(health.total, before)

    def test_scaled_orb_budget_is_shared_by_multiple_actual_recipients(self):
        for healing, key, base in ((True, 12618, 300), (False, 12619, 200)):
            with self.subTest(healing=healing):
                run, source, first, first_health = self.setup_fight(ally=healing)
                match = run.match
                fight = match.combat
                second = match.add_player(2, "Other target", first.owner.hero.guid, first.team, False)
                in_world(match, second)
                run.players.append(second)
                self.assertTrue(match.switch_hero(second, first.owner.hero))
                second.team = first.team
                x, y, z = first.owner.mover.position
                second.mover.position = [x + 1, y, z]
                for _ in range(12):
                    run.step({})
                second_target = next(target for target in fight._targets() if target.owner is second)
                second_health = fight.vitals_of(second_target)
                second_health.damage(100)
                totals = (first_health, second_health)
                before = [health.total for health in totals]
                stat = stats.HEALING if healing else stats.HERO_DAMAGE
                previous_stat = source.stats.value(stat)
                source.body_script.component.set(key, 0.015625)
                orb = self.launch(run, source, healing=healing)
                budget = base / 64
                self.assertEqual(orb.energy, budget)
                source.body_script.component.set(key, 1.5)

                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(25):
                        run.step({})
                        if orb not in fight.moira_orbs:
                            break
                self.assertEqual(orb.energy, 0)
                self.assertNotIn(orb, fight.moira_orbs)
                deltas = [(health.total - old) * (1 if healing else -1)
                          for health, old in zip(totals, before, strict=True)]
                self.assertTrue(all(delta > 0 for delta in deltas))
                self.assertAlmostEqual(sum(deltas), budget, places=5)
                self.assertAlmostEqual(source.stats.value(stat) - previous_stat, budget, places=5)
                if healing:
                    self.assertFalse(damage.called)
                else:
                    self.assertEqual({call.args[1].entity for call in damage.call_args_list},
                                     {first.entity, second.body})
                    for call in damage.call_args_list:
                        self.assertEqual(call.args[4], 0x0B9000000000016D)
                        self.assertEqual(call.kwargs["source_body"], source.body)
                        self.assertEqual(tuple(tag & 0xFFFF for tag in call.kwargs["damage_tags"]),
                                         (0x2DFD, 0x2F99, 0x4D29, 0x4910))

    def test_both_actual_orbs_pass_live_enemy_cryo_without_spending_energy_on_ice(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run = Run(names=("Moira", "Mei", "Behind"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                source, mei, behind = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(match.switch_hero(mei, heroes()[cryo.HERO]))
                mei.team = 1 - source.team
                behind.team = source.team if healing else mei.team
                x, y, z = source.mover.position
                mei.mover.position = [x, y, z + 3]
                behind.mover.position = [x, y, z + 9]
                for actor in run.players:
                    actor.mover.body = None
                for _ in range(30):
                    run.step({})
                run.step({mei: (-32768, 0, 8)})
                fight = match.combat
                self.assertTrue(fight.damage_immune(mei))
                self.assertIsNotNone(fight._cryo_contact(source, fight._eye(source), combat.aim(0, 0), 9))
                targets = {t.owner: t for t in fight._targets()}
                front = fight.vitals_of(targets[mei])
                back = fight.vitals_of(targets[behind])
                if healing:
                    back.damage(120)
                before = front.total, back.total
                orb = self.launch(run, source, healing=healing)
                for _ in range(38):
                    run.step({})
                self.assertTrue(fight.cryo_actors)
                self.assertGreater(orb.position[2], mei.mover.position[2] + combat.CRYO_RADIUS)
                self.assertEqual(front.total, before[0])
                applied = back.total - before[1] if healing else before[1] - back.total
                self.assertGreater(applied, 0)
                self.assertAlmostEqual((300 if healing else 200) - orb.energy, applied)

    def test_actual_damage_orb_is_tethered_damage_not_a_direct_rocket_hit(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        orb = self.launch(run, source)
        self.assertFalse(orb.healing)
        self.assertEqual(run.match.combat.rockets, [])
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(before - vitals.total, 16, places=4)
        self.assertAlmostEqual(200 - orb.energy, 16, places=4)
        self.assertAlmostEqual(orb.flown, 0.32, places=4)

    def test_actual_healing_orb_heals_ally_and_obeys_shared_budget(self):
        run, source, _target, vitals = self.setup_fight()
        orb = self.launch(run, source, healing=True)
        self.assertTrue(orb.healing)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 20.8, places=4)
        self.assertAlmostEqual(300 - orb.energy, 20.8, places=4)

    def test_healing_orb_can_heal_caster_without_healing_enemies(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        health = fight.vitals_of(own)
        health.damage(100)
        self.launch(run, source, healing=True)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(health.total, 120.8, places=4)
        self.assertEqual(vitals.total, 100)

    def test_damage_orb_does_not_hit_allies(self):
        run, source, _target, vitals = self.setup_fight()
        orb = self.launch(run, source)
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(orb.energy, 200)

    def test_actual_ice_wall_bounces_both_orbs_and_blocks_aura(self):
        from ow174.game import mei_wall

        for healing in (False, True):
            for friendly_wall in (False, True):
                with self.subTest(healing=healing, friendly_wall=friendly_wall):
                    run = Run(names=("Moira", "Mei", "Recipient"))
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    source, mei, recipient = run.players
                    self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                    self.assertTrue(match.switch_hero(mei, heroes()[mei_wall.MEI]))
                    mei.team = source.team if friendly_wall else 1 - source.team
                    recipient.team = source.team if healing else 1 - source.team
                    x, y, z = source.mover.position
                    mei.mover.position = [x, y, z + 9]
                    recipient.mover.position = [x, y, z + 6]
                    for player in run.players:
                        player.mover.body = None
                    for _ in range(40):
                        run.step({})
                    fight = match.combat
                    direction = aim(fight._eye(mei), (x, y, z + 3))
                    for button in (16, 0, 1, 0):
                        run.step({mei: (*direction, button)})
                    self.assertEqual(len(fight.walls.pieces), 5)
                    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is recipient))
                    health.damage(100)
                    orb = self.launch(run, source, healing=healing)
                    lifetime = orb.left
                    for _ in range(30):
                        if orb.bounces:
                            break
                        run.step({})
                    self.assertEqual(orb.bounces, 1)
                    self.assertLess(orb.direction[2], -0.95)
                    self.assertLess(orb.left, lifetime)
                    self.assertEqual(orb.energy, 300 if healing else 200)
                    self.assertEqual(health.total, 100)
                    self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values()))
                    self.assertIs(orb.shooter, source)
                    self.assertEqual(orb.body, source.body)

    def test_wall_bounces_and_blocks_tethers(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        orb = self.launch(run, source)
        x, y, z = source.mover.position
        z += 1.5
        wall = World([(x - 10, y - 10, z, x + 10, y - 10, z, x + 10, y + 10, z),
                      (x - 10, y - 10, z, x + 10, y + 10, z, x - 10, y + 10, z)])
        before = vitals.total
        with patch.object(run.match.combat, "_collision", return_value=wall):
            for _ in range(10):
                run.step({})
        self.assertGreaterEqual(orb.bounces, 1)
        self.assertLess(orb.direction[2], 0)
        self.assertEqual(vitals.total, before)

    def test_lifetime_expires_and_repeated_world_tick_cannot_double_apply(self):
        run, source, _target, vitals = self.setup_fight(ally=False, distance=40)
        orb = self.launch(run, source)
        fight = run.match.combat
        run.step({})
        before = orb.position, orb.left, orb.energy, vitals.total
        fight.update(fight.now, fight.tick, frames=False)
        self.assertEqual((orb.position, orb.left, orb.energy, vitals.total), before)
        for _ in range(440):
            run.step({})
        self.assertNotIn(orb, fight.moira_orbs)
        self.assertAlmostEqual(orb.flown, 7.0, places=4)

    def test_orb_survives_caster_death_and_limited_energy_does_not_overdraw(self):
        run, source, target, vitals = self.setup_fight(ally=False)
        orb = self.launch(run, source)
        orb.energy = 0.3  # controlled near-exhaustion suffix of the actual flight
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target.owner, own, 1000, False, None, run.frame)
        before = vitals.total
        run.step({})
        self.assertAlmostEqual(before - vitals.total, 0.3, places=4)
        self.assertEqual(orb.energy, 0)
        self.assertNotIn(orb, fight.moira_orbs)
        self.assertTrue(fight.is_dead(source))

    def test_damage_budget_exhausts_after_200_in_a_bouncing_chamber(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        # A declared larger health pool prevents death from hiding energy overdraw.
        vitals.most[HEALTH_POOL] = vitals.now[HEALTH_POOL] = 1000.0
        orb = self.launch(run, source)
        x, y, z = source.mover.position
        faces = []
        for plane in (z - 1, z + 5.5):
            faces.extend([(x - 10, y - 10, plane, x + 10, y - 10, plane, x + 10, y + 10, plane),
                          (x - 10, y - 10, plane, x + 10, y + 10, plane, x - 10, y + 10, plane)])
        before = vitals.total
        with patch.object(run.match.combat, "_collision", return_value=World(faces)):
            for _ in range(260):
                run.step({})
        self.assertAlmostEqual(before - vitals.total, 200, places=4)
        self.assertEqual(orb.energy, 0)
        self.assertNotIn(orb, run.match.combat.moira_orbs)
        self.assertGreater(orb.bounces, 1)
        self.assertAlmostEqual(orb.flown, 4.0, places=4)

    def test_orb_cooldown_rejects_repeated_selection_and_allows_new_cast(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        old = self.launch(run, source)
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        volley = instance.states[11]
        count = volley.counter
        for _ in range(40):
            run.step({})
        self.assertGreater(instance.states[0].remaining(), 7)
        for frame in range(60):
            run.step({source: (0, 0, 16 if frame % 3 == 0 else 2 if frame % 3 == 1 else 0)})
            self.assertEqual(volley.counter, count)
        for _ in range(440):
            run.step({})
        self.assertEqual(instance.states[0].remaining(), 0)
        new = self.launch(run, source)
        self.assertIsNot(new, old)
        self.assertEqual(volley.counter, count + 1)

    def test_orb_variants_share_cooldown_under_alternating_and_chorded_inputs(self):
        for healing in (False, True):
            with self.subTest(first_healing=healing):
                run, source, _target, _vitals = self.setup_fight(ally=healing)
                fight = run.match.combat
                first = self.launch(run, source, healing=healing)
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0x10E4)
                ability = instance.states[0]
                inputs = (16, 1, 0, 16, 2, 0, 16 | 1 | 2, 0)
                longest_remaining = 0
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for frame in range(560):
                        remaining = ability.remaining()
                        longest_remaining = max(longest_remaining, remaining)
                        if remaining == 0 and not ability.flags & nodes.Ability.RUNNING:
                            break
                        button = inputs[frame % len(inputs)] if remaining > 0.25 else 0
                        run.step({source: (0, 0, button)})
                    else:
                        self.fail('orb cooldown did not expire')
                    self.assertGreater(longest_remaining, 7)
                    self.assertFalse(any(call.args[2].instance is instance for call in shots.call_args_list))
                    second = self.launch(run, source, healing=not healing)
                    self.assertEqual(sum(call.args[2].instance is instance for call in shots.call_args_list), 1)
                self.assertIsNot(first, second)
                self.assertEqual(first.healing, healing)
                self.assertEqual(second.healing, not healing)

    def test_shared_orb_readiness_survives_long_input_gaps(self):
        def cast(healing):
            run, source, _, _ = self.setup_fight(ally=healing)
            orb = self.launch(run, source, healing=healing)
            instance = next(i for i in source.body_script.component.instances.values()
                            if i.graph.index == 0x10E4)
            return run, source, instance.states[0], orb

        for healing in (False, True):
            run, _, ability, _ = cast(healing)
            start = run.frame
            for _ in range(570):
                run.step({})
                if not ability.flags & nodes.Ability.RUNNING and ability.remaining() == 0:
                    break
            else:
                self.fail("shared orb cooldown never became ready")
            delay = run.frame - start
            for elapsed in (delay - 3, delay + 2):
                with self.subTest(healing=healing, ready=elapsed > delay):
                    run, source, ability, old = cast(healing)
                    run.frame += elapsed
                    run.match.update(run.frame * combat.TICK, run.frame)
                    command = Command(run.frame, buttons=16)
                    source.take_commands([command, command])
                    self.assertEqual(bool(ability.flags & nodes.Ability.RUNNING), elapsed > delay)
                    self.assertNotIn(old, run.match.combat.moira_orbs)
                    self.assertFalse(run.match.combat.moira_orbs)

    def test_open_orb_selection_cannot_release_both_variants_on_same_frame(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        ability = instance.states[0]
        run.step({source: (0, 0, 16)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
            for _ in range(70):
                run.step({source: (0, 0, 1 | 2)})
        releases = [call for call in shots.call_args_list if call.args[2].instance is instance]
        self.assertEqual(len(releases), 1)
        self.assertGreater(ability.remaining(), 6)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_cancel_selection_has_no_orb_or_cooldown_cost(self):
        run, source, _target, _vitals = self.setup_fight()
        for frame in range(75):
            run.step({source: (0, 0, 16 if frame in (0, 20) else 0)})
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        self.assertEqual(run.match.combat.moira_orbs, [])
        self.assertEqual(instance.states[0].remaining(), 0)

    def test_selection_press_inside_actual_early_window_is_buffered(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        self.launch(run, source)
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        ability = instance.states[0]
        while ability.remaining() > 0.1:
            run.step({})
        self.assertGreater(ability.remaining(), 0)
        run.step({source: (0, 0, 16)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(10):
            run.step({})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_selection_too_early_short_press_expires_and_fresh_press_after_cooldown_works(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        self.launch(run, source)
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        ability = instance.states[0]
        # Emission precedes the authored selection/recovery exit. Exercise
        # can_start's cooldown buffer only after that running cast has ended.
        for _ in range(60):
            if not ability.flags & nodes.Ability.RUNNING:
                break
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 6)
        run.step({source: (0, 0, 16)})
        run.step({})  # release a short press many seconds before readiness
        while ability.remaining() > 0:
            run.step({})
        for _ in range(2):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({source: (0, 0, 16)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_selection_held_through_cooldown_starts_when_ready_without_new_press(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        self.launch(run, source)
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        ability = instance.states[0]
        # Emission precedes the authored selection/recovery exit. Exercise
        # can_start's cooldown buffer only after that running cast has ended.
        for _ in range(60):
            if not ability.flags & nodes.Ability.RUNNING:
                break
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 6)
        run.step({source: (0, 0, 16)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        while ability.remaining() > 0:
            run.step({source: (0, 0, 16)})
        for _ in range(2):
            run.step({source: (0, 0, 16)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_death_during_selection_prevents_release(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        run.step({source: (0, 0, 16)})
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target.owner, own, 1000, False, None, run.frame)
        for _ in range(40):
            run.step({source: (0, 0, 2)})
        self.assertEqual(fight.moira_orbs, [])
