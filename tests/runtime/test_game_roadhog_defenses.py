"""Actual defensive casts versus Roadhog; scoped immunity/collision reconstructed."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, defenses
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes, runtime

ROADHOG = 0x02E0000000000040
CASTS = (("Reaper", 8, True), ("Moira", 8, True), ("Mei", 8, False), ("Zenyatta", 32, False))


class RoadhogDefenseTests(unittest.TestCase):
    def session(self, name, *, distance=2):
        run = Run(KANEZAKA_DM, ("Roadhog", name, "Behind"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, defender, behind = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        match.switch_hero(defender, next(h for h in heroes().values() if h.name == name))
        y = hog.mover.position[1]
        for actor, z in ((hog, 0), (defender, distance), (behind, distance + 2)):
            actor.mover.position[:] = (0, y, z)
            actor.mover.body = None
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        # Retain the actual body/protection graph for repeated and ultimate hits.
        match.combat.vitals[defender.body] = combat.Vitals(defender.body, 20000)
        match.combat.vitals[behind.body] = combat.Vitals(behind.body, 20000)
        for _ in range(5):
            self.step(run, hog, defender)
        return run, hog, defender, behind

    def health(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target).total

    def step(self, run, hog, defender, *, fire=0, cast=0):
        x, y, z = defender.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 1, z))
        run.step({hog: (*angles, fire), defender: (-32768, 0, cast)})

    def cast(self, run, hog, defender, button):
        if button == 32:
            defender.body_script.set_ult_charge(defender.body_script.component.entity_var(338).value())
        self.step(run, hog, defender, cast=button)
        for _ in range(5):
            self.step(run, hog, defender)
        self.assertTrue(defenses.protection(defender)[0])

    def launch_mei_blizzard(self, run, hog, mei):
        fight = run.match.combat
        x, y, z = hog.mover.position
        throw = aim(fight._eye(mei), (x, y, z))
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        run.step({mei: (*throw, 32)})
        for _ in range(100):
            run.step({mei: (*throw, 0)})
            if fight.blizzard.areas:
                break
        area, = fight.blizzard.areas.values()
        return area

    def test_mei_blizzard_lands_freezes_and_interrupts_whole_hog(self):
        run, hog, mei, _ = self.session("Mei", distance=5)
        fight = run.match.combat
        before = self.health(run, hog)
        run.step({mei: (0, 0, 32)})
        for _ in range(35):
            run.step({})
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        area = self.launch_mei_blizzard(run, hog, mei)
        self.assertFalse(fight.blizzard.flights)
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertEqual(self.health(run, hog), before)

        while fight.tick * 16 < area.starts + 1700:
            run.step({})
        self.assertLess(self.health(run, hog), before)
        self.assertFalse(fight.is_stunned(hog))
        ultimate, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        run.step({hog: (16384, 0, 32)})
        self.assertTrue(ultimate.flags & 1)

        for _ in range(100):
            run.step({})
            if fight.is_stunned(hog):
                break
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.control_deadline(hog.body) - fight.tick, 82)
        self.assertGreaterEqual(fight.tick * 16, area.starts + 2600)
        self.assertFalse(ultimate.flags & 1)

        while fight.tick * 16 <= area.ends:
            run.step({})
        self.assertFalse(fight.blizzard.areas)
        self.assertFalse(fight.is_stunned(hog))
        self.assertAlmostEqual(self.health(run, hog), before - 85)
        for _ in range(70):
            run.step({})
        self.assertNotIn((mei.body, hog.body, area.entity), fight.mei_frost)

    def test_mei_blizzard_interrupts_breather_hook_flight_and_pull(self):
        for ability_graph, button, lead, distance, caught in (
            (0x0240, 16, 720, 18, False),
            (0x01C8, 8, 240, 18, False),
            (0x01C8, 8, 464, 14, True),
        ):
            with self.subTest(ability=ability_graph, caught=caught):
                run, hog, mei, victim = self.session("Mei", distance=5)
                fight = run.match.combat
                x, y, z = hog.mover.position
                victim.mover.position[:] = (x + distance, y, z)
                victim.mover.body = None
                area = self.launch_mei_blizzard(run, hog, mei)
                threshold = area.starts + 2600
                while fight.tick * 16 < threshold - lead:
                    run.step({})
                run.step({hog: (16384, 0, button)})
                state = next(state for inst in hog.body_script.component.instances.values()
                             if inst.graph.index == ability_graph for state in inst.states.values()
                             if isinstance(state, nodes.Ability))
                active = False
                for _ in range(60):
                    if ability_graph == 0x0240:
                        active = fight.incoming_damage_scale(hog) == 0.5
                    else:
                        flight = fight.hooks.flights.get(hog.body)
                        active = flight is not None and flight.arrived_at is None
                    tethered = fight.hooks.controlled(victim.body)
                    run.step({hog: (16384, 0, 0)})
                    if fight.is_stunned(hog):
                        break
                self.assertTrue(active)
                self.assertEqual(tethered, caught)
                self.assertTrue(fight.is_stunned(hog))
                self.assertFalse(state.flags & nodes.Ability.RUNNING)
                self.assertGreater(state.remaining(), 0)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(victim.body))

    def test_mei_blizzard_rechecks_cover_range_and_team_against_roadhog(self):
        for condition in ("cover", "range", "team"):
            with self.subTest(condition=condition):
                run, hog, mei, _ = self.session("Mei", distance=5)
                fight = run.match.combat
                before = self.health(run, hog)
                area = self.launch_mei_blizzard(run, hog, mei)
                x, y, z = area.position
                if condition == "cover":
                    hog.mover.position[:] = (x, y, z - 4)
                    hog.mover.body = None
                    wall = z - 2
                    run.match.collision_world = World([
                        (x - 8, y - 2, wall, x + 8, y - 2, wall, x + 8, y + 5, wall),
                        (x - 8, y - 2, wall, x + 8, y + 5, wall, x - 8, y + 5, wall),
                    ])
                elif condition == "range":
                    hog.mover.position[:] = (x + 12, y, z)
                    hog.mover.body = None
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = mei.team
                while fight.tick * 16 <= area.starts + 450:
                    run.step({})
                self.assertEqual(self.health(run, hog), before)
                self.assertNotIn((mei.body, hog.body, area.entity), fight.mei_frost)
                self.assertFalse(fight.is_stunned(hog))

    def test_mei_primary_frost_slows_ground_movement_until_refresh_expires(self):
        run, hog, mei, _ = self.session("Mei", distance=4)
        fight = run.match.combat
        pair = mei.body, hog.body, 0
        x, y, z = hog.mover.position
        angles = aim(fight._eye(mei), (x, y + 1, z))
        for _ in range(110):
            run.step({mei: (*angles, 1)})
            if fight.mei_frost.get(pair, (0, 0))[0] >= 7000:
                break
        self.assertFalse(fight.is_stunned(hog))
        self.assertGreaterEqual(fight.mei_frost[pair][0], 7000)

        def move_forward(ticks):
            for _ in range(ticks):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                for player in run.players:
                    player.client.queue_entities(run.match.remote_movements(player))
                    player.take_commands([Command(run.frame, yaw=16384, forward=127 if player is hog else 0)])

        move_forward(30)
        base_speed = hog.mover.data.run_forward
        self.assertAlmostEqual(hog.mover.values.run_forward, base_speed * 0.3, places=5)
        slow_x = hog.mover.position[0]
        move_forward(10)
        self.assertGreater(hog.mover.position[0], slow_x)
        self.assertLess(hog.mover.position[0] - slow_x, base_speed * 10 * combat.TICK * 0.5)
        move_forward(55)
        self.assertNotIn(pair, fight.mei_frost)
        self.assertEqual(hog.mover.values.run_forward, base_speed)
        normal_x = hog.mover.position[0]
        move_forward(10)
        self.assertGreater(hog.mover.position[0] - normal_x, base_speed * 10 * combat.TICK * 0.8)

    def test_mei_primary_freeze_interrupts_whole_hog_and_expires(self):
        run, hog, mei, _ = self.session("Mei", distance=4)
        fight = run.match.combat
        pair = mei.body, hog.body, 0
        x, y, z = hog.mover.position
        angles = aim(fight._eye(mei), (x, y + 1, z))
        initial = self.health(run, hog)
        for _ in range(130):
            run.step({mei: (*angles, 1)})
            if fight.mei_frost.get(pair, (0, 0))[0] >= 9000:
                break
        self.assertLess(self.health(run, hog), initial)
        self.assertGreaterEqual(fight.mei_frost[pair][0], 9000)
        self.assertFalse(fight.is_stunned(hog))

        ultimate, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        run.step({mei: (*angles, 1), hog: (-32768, 0, 32)})
        self.assertTrue(ultimate.flags & 1)
        for _ in range(20):
            run.step({mei: (*angles, 1)})
            if fight.is_stunned(hog):
                break
        self.assertEqual(fight.mei_frost[pair][0], 10000)
        self.assertEqual(fight.control_deadline(hog.body) - fight.tick, 82)
        self.assertFalse(ultimate.flags & 1)
        deadline = fight.control_deadline(hog.body)
        for _ in range(85):
            run.step({})
            self.assertEqual(fight.is_stunned(hog), fight.tick < deadline)
        self.assertNotIn(pair, fight.mei_frost)

    def test_mei_primary_and_blizzard_discard_buffered_melee_at_freeze(self):
        for blizzard in (False, True):
            with self.subTest(blizzard=blizzard):
                self.check_freeze_discards_melee(blizzard)

    def check_freeze_discards_melee(self, blizzard):
        run, hog, mei, victim = self.session("Mei", distance=4)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, mei.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        if blizzard:
            area = self.launch_mei_blizzard(run, hog, mei)
            channel = area.entity
            frost = {}
        else:
            channel = 0
            frost = {mei: (*aim(fight._eye(mei), (x, y + 1, z)), 1)}
        pair = mei.body, hog.body, channel
        for _ in range(225):
            run.step(frost)
            if fight.mei_frost.get(pair, (0, 0))[0] >= 9000:
                break
        self.assertGreaterEqual(fight.mei_frost[pair][0], 9000)
        self.assertFalse(fight.is_stunned(hog))

        melee_input = (16384, 0, 0x800)
        held = {**frost, hog: melee_input}
        run.step(held)
        first = run.frame
        run.step(frost)
        run.step(held)
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        hit = melee.instance.state(4)
        self.assertFalse(fight.is_stunned(hog))
        self.assertTrue(melee.presses)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, victim), 19970)

        while run.frame < first + 61 and not fight.is_stunned(hog):
            run.step(held)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.mei_frost[pair][0], 10000)
        self.assertFalse(melee.presses)
        deadline = fight.control_deadline(hog.body)
        for _ in range(6):
            run.step({hog: melee_input})
            self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(hit.counter, 1)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, victim), 19970)
        run.step({hog: melee_input})
        self.assertEqual(hit.counter, 2)
        self.assertEqual(self.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_mei_primary_freeze_stops_breather_without_refunding_cooldown(self):
        run, hog, mei, _ = self.session("Mei", distance=4)
        fight = run.match.combat
        x, y, z = hog.mover.position
        angles = aim(fight._eye(mei), (x, y + 1, z))
        ability = next(state for instance in hog.body_script.component.instances.values()
                       if instance.graph.index == 0x0240 for state in instance.states.values()
                       if isinstance(state, nodes.Ability))
        active_reduction = False
        for offset in range(130):
            run.step({mei: (*angles, 1), hog: (0, 0, 16 if offset == 52 else 0)})
            if fight.is_stunned(hog):
                break
            active_reduction |= fight.incoming_damage_scale(hog) == 0.5
        self.assertTrue(active_reduction)
        self.assertTrue(fight.is_stunned(hog))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        stopped = self.health(run, hog)
        for _ in range(100):
            run.step({hog: (0, 0, 16)})
            current = self.health(run, hog)
            self.assertLessEqual(current, stopped)
            stopped = current
        self.assertFalse(fight.is_stunned(hog))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_mei_primary_freeze_cancels_hook_flight_and_pull(self):
        for victim_z, cast_at, caught in ((18, 8840, False), (14, 7130, True)):
            with self.subTest(caught=caught):
                run, hog, mei, victim = self.session("Mei", distance=4)
                fight = run.match.combat
                x, y, z = hog.mover.position
                victim.mover.position[:] = (x + victim_z, y, z)
                victim.mover.body = None
                mei_aim = aim(fight._eye(mei), (x, y + 1, z))
                pair = mei.body, hog.body, 0
                started = False
                for _ in range(135):
                    amount = fight.mei_frost.get(pair, (0, 0))[0]
                    cast = not started and amount >= cast_at
                    started |= cast
                    flight = fight.hooks.flights.get(hog.body)
                    tethered = fight.hooks.controlled(victim.body)
                    run.step({mei: (*mei_aim, 1), hog: (16384, 0, 8 if cast else 0)})
                    if fight.is_stunned(hog):
                        break
                self.assertTrue(fight.is_stunned(hog))
                self.assertIsNotNone(flight)
                self.assertIsNone(flight.arrived_at)
                self.assertEqual(tethered, caught)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(victim.body))

    def test_primary_close_slug_and_expired_fragments_respect_live_protection(self):
        for name, button, ghost in CASTS:
            for attack, distance in ((1, 2), (2, 2), (2, 10)):
                with self.subTest(hero=name, attack=attack, distance=distance):
                    run, hog, defender, behind = self.session(name, distance=distance)
                    self.cast(run, hog, defender, button)
                    self.assertEqual(defenses.protection(defender), (True, ghost))
                    before = self.health(run, defender)
                    weapon = next(i for i in hog.body_script.component.instances.values()
                                  if i.graph.index == 0x01C6)
                    ammo = weapon.vars[53].value()
                    self.step(run, hog, defender, fire=attack)
                    for _ in range(25):
                        self.step(run, hog, defender)
                    self.assertEqual(self.health(run, defender), before)
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                    charge = hog.body_script.component.entity_var(342).value()
                    if ghost:
                        self.assertLess(self.health(run, behind), 20000)
                        self.assertGreater(charge, 0)
                    else:
                        self.assertEqual(self.health(run, behind), 20000)
                        self.assertEqual(charge, 0)
                        self.assertFalse(hog.client.messages(combat.HIT))

    def test_melee_cannot_damage_or_fund_charge_against_mapped_protection(self):
        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=1.5)
                behind.mover.position[0] = 20
                self.cast(run, hog, defender, button)
                before = self.health(run, defender)
                self.step(run, hog, defender, fire=0x800)
                for _ in range(25):
                    self.step(run, hog, defender)
                self.assertEqual(self.health(run, defender), before)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(hog.client.messages(combat.HIT))

    def test_hook_ghost_passes_to_rear_but_solid_protection_intercepts_without_control(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name)
                self.cast(run, hog, defender, button)
                self.step(run, hog, defender, fire=8)
                controlled = False
                for _ in range(30):
                    self.step(run, hog, defender)
                    self.assertFalse(run.match.combat.hooks.controlled(defender.body))
                    controlled |= run.match.combat.hooks.controlled(behind.body)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(controlled, ghost)
                self.assertEqual(self.health(run, behind), 19970 if ghost else 20000)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 30 if ghost else 0)
                hook = next(s for i in hog.body_script.component.instances.values()
                            if i.graph.index == 0x01C8 for s in i.states.values() if s.node.pos == 11)
                self.assertGreater(hook.remaining(), 0)

    def test_projectile_launched_before_defensive_cast_uses_impact_time_protection(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=10)
                self.step(run, hog, defender, fire=1)
                self.assertEqual(len(run.match.combat.rockets), 25)
                self.cast(run, hog, defender, button)
                for _ in range(20):
                    self.step(run, hog, defender)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(self.health(run, behind) < 20000, ghost)

    def launch_hook(self, run, hog, defender):
        hooks = run.match.combat.hooks
        self.step(run, hog, defender, fire=8)
        for _ in range(25):
            flight = hooks.flights.get(hog.body)
            if flight is not None:
                self.assertIsNone(flight.target)
                return flight
            self.step(run, hog, defender)
        self.fail("Hook did not launch")

    def test_flying_hook_uses_protection_activated_after_launch(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=14)
                fight = run.match.combat
                self.assertEqual(defenses.protection(defender), (False, False))
                flight = self.launch_hook(run, hog, defender)
                self.cast(run, hog, defender, button)
                self.assertEqual(defenses.protection(defender), (True, ghost))
                self.assertIs(fight.hooks.flights.get(hog.body), flight)
                self.assertIsNone(flight.target)
                caught = False
                for _ in range(40):
                    self.step(run, hog, defender)
                    self.assertFalse(fight.hooks.controlled(defender.body))
                    caught |= fight.hooks.controlled(behind.body)
                self.assertEqual(caught, ghost)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(self.health(run, behind), 19970 if ghost else 20000)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 30 if ghost else 0)

    def test_live_hook_refuses_protection_input_and_does_not_replay_it_on_release(self):
        for name, button, ghost in (*CASTS, ("Doomfist", 32, True)):
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=12)
                fight = run.match.combat
                script = defender.body_script
                charge = script.component.entity_var(342)
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                before_charge = charge.value()
                self.launch_hook(run, hog, defender)
                for _ in range(35):
                    self.step(run, hog, defender)
                    if fight.hooks.controlled(defender.body):
                        break
                self.assertTrue(fight.hooks.controlled(defender.body))
                self.assertEqual(self.health(run, defender), 19970)
                start = defender.mover.position[2]
                delayed = Command(run.frame + 10, yaw=-32768, buttons=button)
                attempted = 0

                # Skip some command frames, including the command delivered only after release.
                for offset in range(1, 85):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if not fight.hooks.controlled(defender.body):
                        break
                    if offset <= 9 and offset % 3 == 0:
                        defender.take_commands([Command(run.frame, yaw=-32768, buttons=button)])
                        attempted += 1
                    self.assertEqual(defenses.protection(defender), (False, False))
                    self.assertEqual(charge.value(), before_charge)
                self.assertEqual(attempted, 3)
                self.assertFalse(fight.hooks.controlled(defender.body))
                self.assertLess(defender.mover.position[2], start - 5)
                self.assertGreaterEqual(fight.hooks.player_command_cutoffs[defender.body], delayed.frame)
                self.assertLess(defender.commands.last_frame, delayed.frame)
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                with patch.object(defender, "_advance_body_frame", wraps=defender._advance_body_frame) as advance:
                    defender.take_commands([delayed, Command(run.frame)])
                delayed_steps = [call.args[0] for call in advance.call_args_list if call.args[0].frame == delayed.frame]
                self.assertEqual([command.buttons for command in delayed_steps], [0])
                for _ in range(8):
                    self.step(run, hog, defender)
                self.assertEqual(defenses.protection(defender), (False, False))
                self.assertEqual(charge.value(), before_charge)
                self.assertEqual(self.health(run, defender), 19970)
                self.assertEqual(self.health(run, behind), 20000)

                self.step(run, hog, defender, cast=button)
                for _ in range(12):
                    if defenses.protection(defender)[0]:
                        break
                    self.step(run, hog, defender)
                self.assertEqual(defenses.protection(defender), (True, ghost))
                if name == "Zenyatta":
                    self.assertEqual(charge.value(), 0)

    def test_mei_cancel_during_flight_allows_same_hook_to_hit_and_pull(self):
        run, hog, mei, behind = self.session("Mei", distance=14)
        fight = run.match.combat
        self.cast(run, hog, mei, 8)
        for _ in range(25):
            self.step(run, hog, mei)
        flight = self.launch_hook(run, hog, mei)
        self.assertEqual(defenses.protection(mei), (True, False))
        self.step(run, hog, mei, cast=1)
        self.assertEqual(defenses.protection(mei), (False, False))
        self.assertIs(fight.hooks.flights.get(hog.body), flight)
        for _ in range(40):
            self.step(run, hog, mei)
            if fight.hooks.controlled(mei.body):
                break
        self.assertTrue(fight.hooks.controlled(mei.body))
        self.assertFalse(fight.hooks.controlled(behind.body))
        self.assertEqual(self.health(run, mei), 19970)
        self.assertEqual(self.health(run, behind), 20000)

    def test_actual_mei_cancel_ends_protection_before_authored_expiry(self):
        run, hog, defender, _ = self.session("Mei")
        self.cast(run, hog, defender, 8)
        for _ in range(30):
            self.step(run, hog, defender)
        self.step(run, hog, defender, cast=1)
        self.assertEqual(defenses.protection(defender), (False, False))
        before = self.health(run, defender)
        self.step(run, hog, defender, fire=1)
        for _ in range(20):
            self.step(run, hog, defender)
        self.assertLess(self.health(run, defender), before)

    def test_actual_flashbang_does_not_interrupt_mapped_protection(self):
        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, caster = self.session(name)
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                caster.mover.position[:] = (2.5, hog.mover.position[1], 2)
                self.cast(run, hog, defender, button)
                before = self.health(run, defender)
                helper = flashbang_tests.FlashbangTests()
                helper.step(run, caster, defender, cast=16)
                for _ in range(25):
                    helper.step(run, caster, defender)
                    self.assertFalse(run.match.combat.is_stunned(defender))
                self.assertTrue(defenses.protection(defender)[0])
                self.assertEqual(self.health(run, defender), before)

    def test_active_protection_graphs_export_without_evaluation_or_reentering_callbacks(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest

        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                case = stored.ServerSnapshotTests()
                session, peers, players = case.scene()
                player = players[0]
                hero = next(h for h in heroes().values() if h.name == name)
                session.apply("switch_hero", {"account": player.account_lo, "hero": hero.guid})
                if button == 32:
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                peers[0].commands(player.body_script.frame + 1, 1, button)
                self.assertTrue(defenses.protection(player)[0])
                with (
                    patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
                    patch.object(defenses, "advance_entry", side_effect=AssertionError("no callback")),
                ):
                    first = snapshot_digest(case.capture(session))
                    self.assertEqual(snapshot_digest(case.capture(session)), first)

    def test_running_whole_hog_respects_active_phase_and_solid_invulnerability(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name)
                hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                self.step(run, hog, defender, fire=32)
                for _ in range(80):
                    self.step(run, hog, defender)
                self.assertLess(self.health(run, defender), 20000)
                self.cast(run, hog, defender, button)
                before, rear = self.health(run, defender), self.health(run, behind)
                for _ in range(25):
                    self.step(run, hog, defender)
                self.assertTrue(defenses.protection(defender)[0])
                self.assertEqual(self.health(run, defender), before)
                self.assertEqual(self.health(run, behind) < rear, ghost)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_same_whole_hog_resumes_damage_and_force_when_protection_ends(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                self.check_whole_hog_protection_exit(name, button, ghost)

    def test_protection_onset_retires_an_existing_whole_hog_impulse(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                self.check_whole_hog_protection_onset(name, button, ghost)

    def check_whole_hog_protection_onset(self, name, button, ghost):
        run, hog, defender, behind = self.session(name, distance=4)
        fight = run.match.combat
        script = hog.body_script
        charge = script.component.entity_var(342)
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, hog, defender, fire=32)
        for _ in range(50):
            if defender.body in fight.impulses.live:
                break
            self.step(run, hog, defender)
        old_motion = fight.impulses.live.get(defender.body)
        self.assertIsNotNone(old_motion)
        self.assertTrue(any(old_motion.velocity))
        self.assertLess(self.health(run, defender), 20000)
        if button == 32:
            defender.body_script.set_ult_charge(defender.body_script.component.entity_var(338).value())
        self.step(run, hog, defender, cast=button)
        self.assertEqual(defenses.protection(defender), (True, ghost))
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

        before = self.health(run, defender)
        rear = self.health(run, behind)
        x, _, z = defender.mover.position
        for _ in range(405):
            if not defenses.protection(defender)[0]:
                break
            self.step(run, hog, defender)
            self.assertNotIn(defender.body, fight.impulses.live)
            self.assertEqual(self.health(run, defender), before)
            self.assertEqual((defender.mover.position[0], defender.mover.position[2]), (x, z))
        self.assertFalse(defenses.protection(defender)[0])
        self.assertEqual(self.health(run, behind) < rear, ghost)
        if name == "Zenyatta":
            # Transcendence covers the rest of the firing window, including the last volley.
            for _ in range(45):
                self.step(run, hog, defender)
                self.assertNotIn(defender.body, fight.impulses.live)
                self.assertEqual(self.health(run, defender), before)
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
            self.assertEqual(charge.value(), 0)
            self.step(run, hog, defender, fire=1)
            for _ in range(20):
                self.step(run, hog, defender)
            self.assertLess(self.health(run, defender), before)
            return

        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(45):
            self.step(run, hog, defender)
            if defender.body in fight.impulses.live:
                break
        new_motion = fight.impulses.live.get(defender.body)
        self.assertIsNotNone(new_motion)
        self.assertIsNot(new_motion, old_motion)
        self.assertLess(self.health(run, defender), before)
        self.assertEqual(charge.value(), 0)
        self.step(run, hog, defender)
        self.assertGreater(defender.mover.position[2], z)

    def check_whole_hog_protection_exit(self, name, button, ghost):
        run, hog, defender, behind = self.session(name)
        fight = run.match.combat
        self.cast(run, hog, defender, button)
        if name == "Zenyatta":
            for _ in range(125):
                self.step(run, hog, defender)
        self.assertTrue(defenses.protection(defender)[0])
        before, rear = self.health(run, defender), self.health(run, behind)
        ultimate, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        self.step(run, hog, defender, fire=32)
        for _ in range(45):
            self.step(run, hog, defender)
        self.assertTrue(ultimate.flags & 1)
        self.assertTrue(defenses.protection(defender)[0])
        self.assertEqual(self.health(run, defender), before)
        self.assertNotIn(defender.body, fight.impulses.live)
        self.assertEqual(self.health(run, behind) < rear, ghost)

        if name == "Mei":
            self.step(run, hog, defender, cast=1)
        for _ in range(380):
            if not defenses.protection(defender)[0]:
                break
            self.step(run, hog, defender)
        self.assertFalse(defenses.protection(defender)[0])
        self.assertTrue(ultimate.flags & 1)
        for _ in range(45):
            self.step(run, hog, defender)
            if self.health(run, defender) < before:
                break
        self.assertLess(self.health(run, defender), before)
        self.assertIn(defender.body, fight.impulses.live)
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_expiry_restores_damage_and_hero_replacement_cannot_keep_protection(self):
        for name, button, _ in CASTS:
            for replace in (False, True):
                with self.subTest(hero=name, replace=replace):
                    run, hog, defender, _ = self.session(name)
                    self.cast(run, hog, defender, button)
                    if replace:
                        old = defender.body_script
                        run.match.switch_hero(defender, heroes()[ROADHOG])
                        defender.mover.position[:] = (0, hog.mover.position[1], 2)
                        self.assertTrue(old.retired)
                    else:
                        for _ in range(420):
                            self.step(run, hog, defender)
                    self.assertEqual(defenses.protection(defender), (False, False))
                    before = self.health(run, defender)
                    self.step(run, hog, defender, fire=1)
                    for _ in range(20):
                        self.step(run, hog, defender)
                    self.assertLess(self.health(run, defender), before)


if __name__ == "__main__":
    unittest.main()
