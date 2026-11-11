"""Actual phase commands against Wrecking Ball attacks."""

import unittest
from dataclasses import replace

import test_wrecking_ball_combat as scenes
from test_game_combat import KANEZAKA_DM, PRACTICE_RANGE, Run, aim
from test_wrecking_ball_control import ANA
from test_wrecking_ball_fireball import add_side_wall, relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_grapple import scene as swing_scene
from test_wrecking_ball_piledriver import lift, pile_instance

from ow174.game import ball, barriers, combat, projectiles, stats
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import HUD_MAX_HEALTH, ULT_CHARGE, ULT_COST

REAPER = 0x02E0000000000002
MOIRA = 0x02E00000000001A2
ZENYATTA = 0x02E0000000000020


def phase_instance(player):
    index, _ = combat.PHASE_STATES[player.hero.guid]
    return next(i for i in player.body_script.component.instances.values() if i.graph.index == index)


class BallPhaseTests(unittest.TestCase):
    def scene(self, hero, distance=1.5):
        run, player, enemy, _, _ = scenes.BallCombatTests().scenario(distance=distance)
        run.match.switch_hero(enemy, heroes()[hero])
        x, y, z = player.mover.position
        enemy.mover.position[:] = [x, y, z + distance]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        return run, player, enemy

    def health(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor)).total

    def test_cannon_passes_wraith_and_credits_the_enemy_behind(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Reaper', 'Behind'))
        player, reaper, behind = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(reaper, heroes()[REAPER])
        x, y, z = player.mover.position
        reaper.mover.position[:] = [x, y, z + 3]
        behind.mover.position[:] = [x, y, z + 6]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        run.step({reaper: (-32768, 0, 8)})
        phase = phase_instance(reaper).states[5]
        self.assertTrue(phase.active)
        angles = aim(run.match.combat._eye(player), (x, y + 1.3, z + 6))
        run.step({player: (*angles, 1)})
        self.assertEqual(self.health(run, reaper), 250)
        self.assertEqual(self.health(run, behind), 195)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        for _ in range(30):
            run.step({})
        run.step({reaper: (-32768, 0, 8)})
        self.assertFalse(phase.active)
        for _ in range(30):
            run.step({})
        self.assertGreater(phase_instance(reaper).vars[61].value(), 0)
        angles = aim(run.match.combat._eye(player), (x, y + 1.1, z + 3))
        run.step({reaper: (-32768, 0, 8), player: (*angles, 1)})
        self.assertFalse(phase.active)
        self.assertEqual(self.health(run, reaper), 245)
        self.assertEqual(self.health(run, behind), 195)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 10)

    def test_fade_rejects_melee_then_expires_and_cannon_hits(self):
        run, player, moira = self.scene(MOIRA)
        fight = run.match.combat
        x, y, z = moira.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({moira: (-32768, 0, 8)})
        phase = phase_instance(moira).states[4]
        self.assertTrue(phase.active)
        run.step({player: (*angles, 0x800)})
        for _ in range(34):
            run.step({})
        self.assertTrue(phase.active)
        self.assertEqual(self.health(run, moira), 200)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(14):
            run.step({})
        self.assertFalse(phase.active)
        self.assertEqual(phase.ended_at - phase.start, 750)
        run.step({player: (*angles, 1)})
        self.assertEqual(self.health(run, moira), 195)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_wraith_and_fade_stop_protecting_cannon_at_the_world_deadline(self):
        for hero, duration in ((REAPER, 3000), (MOIRA, 750)):
            for timing in ('before', 'expiry', 'late'):
                with self.subTest(hero=hero, timing=timing):
                    self.check_phase_world_expiry(hero, duration, timing)

    def check_phase_world_expiry(self, hero, duration, timing):
        run, player, enemy = self.scene(hero, distance=4)
        match = run.match
        fight = match.combat
        if timing == 'late':
            activation = run.frame + 1
            run.frame = activation + 200
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            enemy.take_commands([Command(activation, yaw=-32768, buttons=8)])
        else:
            run.step({enemy: (-32768, 0, 8)})
        script = enemy.body_script
        frozen = script.frame, script.component.now
        timer = phase_instance(enemy).state(3)
        self.assertEqual(timer.ms, duration)
        before = timing == 'before'
        endpoint = max(run.frame, (timer.start + timer.ms) // script.frame_ms - int(before))
        match.update(endpoint * combat.TICK, endpoint, frames=False)
        x, y, z = enemy.mover.position
        angles = aim(fight._eye(player), (x, y + 0.85, z))
        player.take_commands([Command(endpoint, yaw=angles[0], pitch=angles[1], buttons=1)])
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertTrue(self.protection_instance(enemy)[1].active)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        damage = 0 if before else 5
        self.assertEqual(self.health(run, enemy), (250 if hero == REAPER else 200) - damage)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)

    def test_piledriver_resolves_fade_at_world_expiry_without_moira_commands(self):
        for before in (True, False):
            with self.subTest(before_expiry=before):
                run, player, moira = self.scene(MOIRA)
                fight = run.match.combat
                lift(run, player)
                lifted_frame = run.frame
                landing_frame = lifted_frame + 70
                fade_frame = landing_frame - 750 // moira.body_script.frame_ms + int(before)
                while run.frame < fade_frame - 1:
                    press = 128 if run.frame < lifted_frame + 10 else 0
                    run.step({player: (0, 0, press)})
                run.step({moira: (-32768, 0, 8)})
                script = moira.body_script
                timer = phase_instance(moira).state(3)
                self.assertEqual(timer.ms, 750)
                self.assertEqual((timer.start + timer.ms) // script.frame_ms,
                                 landing_frame + int(before))
                frozen = script.frame, script.component.now
                landing = pile_instance(player).state(7)
                while run.frame < landing_frame + 2:
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    player.take_commands([Command(run.frame)])
                    if run.frame == landing_frame:
                        self.assertEqual(self.health(run, moira), 200 if before else 100)
                    elif run.frame < landing_frame:
                        self.assertEqual(landing.counter, 0)
                        self.assertEqual(self.health(run, moira), 200)
                self.assertEqual((script.frame, script.component.now), frozen)
                self.assertTrue(phase_instance(moira).state(4).active)
                self.assertEqual(landing.counter, 1)
                self.assertEqual(landing.start // script.frame_ms, landing_frame)
                damage = 0 if before else 100
                self.assertEqual(self.health(run, moira), 200 - damage)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
                self.assertEqual(moira.mover.airborne, not before)
                self.assertEqual(moira.body in fight.pile_air_locks, not before)


    def test_one_piledriver_filters_overlapping_phase_and_immunity_targets(self):
        for before in (True, False):
            with self.subTest(fade_before_expiry=before):
                run = Run(KANEZAKA_DM, ('Ball', 'Moira', 'Reaper', 'Mei', 'Zen', 'Soldier'))
                player, moira, reaper, mei, zen, soldier = run.players
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                match.switch_hero(player, heroes()[ball.HERO])
                for actor, hero in ((moira, MOIRA), (reaper, REAPER), (mei, barriers.MEI), (zen, ZENYATTA)):
                    match.switch_hero(actor, heroes()[hero])
                x, y, z = player.mover.position
                enemies = (moira, reaper, mei, zen, soldier)
                for actor, (dx, dz) in zip(
                    enemies, ((0, 1.5), (0, -1.5), (1.5, 0), (-1.5, 0), (1.1, 1.1)), strict=True,
                ):
                    relocate(run, actor, (x + dx, y, z + dz))
                for _ in range(90):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                zen_script = zen.body_script
                zen_script.set_ult_charge(zen_script.component.entity_var(ULT_COST).value())
                lift(run, player)
                start = run.frame
                landing_frame = start + 70
                fade_frame = landing_frame - 750 // moira.body_script.frame_ms + int(before)
                while run.frame < fade_frame - 1:
                    run.step({player: (0, 0, 128 if run.frame < start + 10 else 0)})
                run.step({moira: (0, 0, 8), reaper: (0, 0, 8), mei: (0, 0, 8), zen: (0, 0, 32)})
                protected = (moira, reaper, mei, zen)
                self.assertTrue(all(self.protection_instance(actor)[1].active for actor in protected))
                timer = phase_instance(moira).state(3)
                self.assertEqual((timer.start + timer.ms) // moira.body_script.frame_ms,
                                 landing_frame + int(before))
                frozen = tuple(actor.body_script.frame for actor in enemies)
                health = tuple(self.health(run, actor) for actor in enemies)
                expected = (0 if before else 100, 0, 0, 0, 100)

                for frame in range(run.frame + 1, landing_frame + 3):
                    run.frame = frame
                    match.update(frame * combat.TICK, frame, frames=False)
                    player.take_commands([Command(frame)])
                    losses = tuple(hp - self.health(run, actor)
                                   for actor, hp in zip(enemies, health, strict=True))
                    self.assertEqual(losses, expected if frame >= landing_frame else (0,) * len(enemies))
                landing = pile_instance(player).state(7)
                self.assertEqual((landing.counter, landing.start // player.body_script.frame_ms),
                                 (1, landing_frame))
                self.assertEqual(tuple(actor.body_script.frame for actor in enemies), frozen)
                lifted = {zen.body, soldier.body}
                if not before:
                    lifted.add(moira.body)
                self.assertEqual(set(match.combat.pile_air_locks), lifted)
                self.assertEqual({actor.body for actor in enemies if actor.mover.airborne}, lifted)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), sum(expected))


    def test_wraith_rejects_piledriver_damage_and_lift(self):
        run, player, reaper = self.scene(REAPER)
        lift(run, player)
        run.step({reaper: (-32768, 0, 8), player: (0, 0, 128)})
        phase = phase_instance(reaper).states[5]
        self.assertTrue(phase.active)
        for _ in range(89):
            run.step({})
        self.assertEqual(pile_instance(player).states[7].counter, 1)
        self.assertTrue(phase.active)
        self.assertEqual(self.health(run, reaper), 250)
        self.assertFalse(reaper.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_wraith_rejects_actual_high_speed_fireball_contact(self):
        run, player, reaper, _, _ = swing_scene()
        x, y, z = player.mover.position
        run.match.switch_hero(reaper, heroes()[REAPER])
        reaper.mover.position[:] = [x, y, z + 6]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        lift(run, player)
        run.step({reaper: (-32768, 0, 8), player: (0, 0, 2)})
        search = ball.instance(player, 0x1203).state(35)
        entered = False
        for _ in range(100):
            hold(run, player, 1)
            entered |= search.active
        self.assertTrue(entered)
        self.assertTrue(phase_instance(reaper).states[5].active)
        self.assertEqual(self.health(run, reaper), 250)
        self.assertFalse(reaper.mover.airborne)
        self.assertEqual(tuple(reaper.mover.velocity), (0, 0, 0))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_fireball_rechecks_fade_at_world_expiry_without_moira_commands(self):
        for timing in ('expiry', 'overlap', 'separated'):
            with self.subTest(timing=timing):
                run, player, moira, _, _ = swing_scene()
                x, y, z = player.mover.position
                run.match.switch_hero(moira, heroes()[MOIRA])
                relocate(run, moira, (x, y, z + 6))
                for _ in range(90):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                charge = player.body_script.component.entity_var(ULT_CHARGE)
                lift(run, player)

                contact_frame = run.frame + 62
                deadline = contact_frame + int(timing != 'expiry')
                script = moira.body_script
                fade_frame = deadline - 750 // script.frame_ms
                while run.frame < fade_frame - 1:
                    hold(run, player, 1)
                run.step({player: (0, 0, 2), moira: (-32768, 0, 8)})
                timer = phase_instance(moira).state(3)
                self.assertEqual(timer.ms, 750)
                self.assertEqual((timer.start + timer.ms) // script.frame_ms, deadline)
                frozen = script.frame, script.component.now

                fight = run.match.combat
                search = ball.instance(player, 0x1203).state(35)
                while run.frame < contact_frame + 6:
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    player.take_commands([Command(run.frame, buttons=2)])
                    hit = timing != 'separated' and run.frame >= deadline
                    self.assertEqual(self.health(run, moira), 150 if hit else 200)
                    self.assertEqual(charge.value(), 50 if hit else 0)
                    self.assertEqual(moira.body in fight.ball_contacts.get(player.body, ()), hit)
                    if run.frame == contact_frame:
                        self.assertTrue(search.active)
                        if timing == 'separated':
                            relocate(run, moira, (x + 10, y, z + 6))
                    if not hit:
                        self.assertEqual(tuple(moira.mover.velocity), (0, 0, 0))
                    elif run.frame == deadline:
                        self.assertEqual(tuple(moira.mover.velocity), (0, 5, 22))
                self.assertEqual((script.frame, script.component.now), frozen)
                self.assertTrue(phase_instance(moira).state(4).active)


    def test_fireball_damage_and_impulse_during_cryo_and_transcendence(self):
        for hero, cancel in ((barriers.MEI, False), (barriers.MEI, True), (ZENYATTA, False)):
            with self.subTest(hero=hex(hero), cancel=cancel):
                run, player, enemy, _, _ = swing_scene()
                x, y, z = player.mover.position
                run.match.switch_hero(enemy, heroes()[hero])
                enemy.mover.position[:] = [x, y, z + 6]
                for _ in range(90):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                if hero == barriers.MEI:
                    run.step({enemy: (-32768, 0, 8)})
                    _, immunity = self.protection_instance(enemy)
                    self.assertTrue(immunity.active)
                    if cancel:
                        for _ in range(20):
                            run.step({})
                        run.step({enemy: (-32768, 0, 2)})
                        self.assertFalse(immunity.active)
                else:
                    immunity = self.cast_transcendence(run, enemy)

                fight = run.match.combat
                before = self.health(run, enemy)
                lift(run, player)
                for _ in range(100):
                    hold(run, player, 1)
                    if enemy.body in fight.ball_contacts.get(player.body, ()):
                        break
                self.assertIn(enemy.body, fight.ball_contacts.get(player.body, ()))
                self.assertTrue(ball.instance(player, 0x1203).states[35].active)
                self.assertEqual(immunity.active, not cancel)
                damage = 50 if cancel else 0
                self.assertEqual(self.health(run, enemy), before - damage)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
                if hero == barriers.MEI and not cancel:
                    self.assertEqual(tuple(enemy.mover.velocity), (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 4)
                    self.assertGreater(enemy.mover.velocity[2], 20)
                    self.assertTrue(enemy.mover.airborne)

    def retained_scene(self, hero):
        run = Run(KANEZAKA_DM, ('Ball', 'Protected', 'Behind'))
        player, enemy, behind = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(enemy, heroes()[hero])
        x, y, z = player.mover.position
        enemy.mover.position[:] = [x, y, z + 3]
        behind.mover.position[:] = [x, y, z + 6]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        return run, player, enemy, behind

    def protection_instance(self, player):
        index, state = combat.DAMAGE_IMMUNITY_STATES[player.hero.guid]
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == index)
        return instance, instance.state(state)

    def cast_transcendence(self, run, zen):
        script = zen.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertGreater(cost, 0)
        script.set_ult_charge(cost)
        run.step({zen: (-32768, 0, 32)})
        _, immunity = self.protection_instance(zen)
        self.assertTrue(immunity.active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        return immunity

    def test_adaptive_shield_samples_protected_enemy_bodies_once(self):
        for hero in (REAPER, MOIRA, barriers.MEI, ZENYATTA):
            for ally in (False, True):
                with self.subTest(hero=hero, ally=ally):
                    run, player, target = self.scene(hero)
                    if ally:
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        target.team = player.team
                    if hero == ZENYATTA:
                        immunity = self.cast_transcendence(run, target)
                    else:
                        run.step({target: (-32768, 0, 8)})
                        _, immunity = self.protection_instance(target)
                    self.assertTrue(immunity.active)

                    fight = run.match.combat
                    own = next(t for t in fight._targets() if t.owner is player)
                    vitals = fight.vitals_of(own)
                    run.step({player: (0, 0, 16)})
                    self.assertTrue(immunity.active)
                    expected = 100 if ally else 175
                    self.assertEqual(vitals.temporary_shields, expected)
                    activation = vitals.adaptive_activation

                    # Leaving the search radius cannot revoke the sampled bonus.
                    target.mover.position[2] += 20
                    for _ in range(8):
                        run.step({player: (0, 0, 16)})
                    self.assertEqual(vitals.temporary_shields, expected)
                    self.assertEqual(vitals.adaptive_activation, activation)

    def test_adaptive_shield_resamples_only_on_ready_cast_after_protection_changes(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Reaper', 'Moira', 'Mei', 'Zenyatta', 'Soldier'))
        player, reaper, moira, mei, zen, soldier = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team = 0
        for actor, hero in ((player, ball.HERO), (reaper, REAPER), (moira, MOIRA),
                            (mei, barriers.MEI), (zen, ZENYATTA)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        x, y, z = player.mover.position
        protected = (reaper, moira, mei, zen)
        for actor, dx, dz in ((reaper, -4, 0), (moira, 4, 0), (mei, 0, -4), (zen, 0, 4),
                              (soldier, 30, 0)):
            actor.team = 1
            relocate(run, actor, (x + dx, y, z + dz))
        for _ in range(90):
            run.step({})
        soldier.body_script.ult_seconds = 0
        soldier.body_script.set_ult_charge(0)
        self.cast_transcendence(run, zen)
        run.step(dict.fromkeys((reaper, moira, mei), (0, 0, 8)))
        run.step({player: (0, 0, 16)})
        for actor in protected:
            self.assertTrue(self.protection_instance(actor)[1].active)
        fight = match.combat
        own = fight.vitals[player.body]
        instance = ball.instance(player, 0x1566)
        cooldown = instance.state(0)
        self.assertEqual(own.temporary_shields, 400)
        max_health = player.body_script.component.entity_var(HUD_MAX_HEALTH)
        self.assertEqual(max_health.value(), 1000)
        first = own.adaptive_activation

        for index, actor in enumerate(protected):
            relocate(run, actor, (x + 20 + 3 * index, y, z))
        relocate(run, soldier, (x - 6, y, z))
        rifle = aim(fight._eye(soldier), (x, y + 0.85, z))
        run.step({soldier: (*rifle, 1)})
        self.assertEqual(own.temporary_shields, 380)
        self.assertEqual(own.temporary_maximum, 400)
        self.assertEqual(soldier.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 380)
        self.assertEqual(own.adaptive_activation, first)

        # Catch up the actual body graph, then press again once its cooldown allows it.
        run.frame += 930
        run.step({})
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(max_health.value(), 600)
        for _ in range(20):
            if cooldown.remaining() == 0:
                break
            run.step({})
        self.assertEqual(cooldown.remaining(), 0)
        self.assertTrue(all(not self.protection_instance(actor)[1].active for actor in protected))
        run.step({player: (0, 0, 16)})
        self.assertNotEqual(own.adaptive_activation, first)
        self.assertEqual(instance.state(6).counter, 2)
        self.assertEqual(own.temporary_shields, 175)
        self.assertEqual(max_health.value(), 775)

    def test_allied_transcendence_heals_ball_permanent_pools_through_its_lifetime(self):
        run, player, zen, soldier = self.retained_scene(ZENYATTA)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = zen.team = 0
        soldier.team = 1
        x, y, z = player.mover.position
        relocate(run, soldier, (x + 6, y, z))
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        own = fight.vitals_of(targets[player])
        hostile = fight.vitals_of(targets[soldier])
        rifle = aim(fight._eye(soldier), (x, y + 0.8, z))
        cannon = aim(fight._eye(player), (x + 6, y + 0.8, z))
        run.step({player: (*cannon, 1), soldier: (*rifle, 1)})
        self.assertEqual(own.total, 585)
        self.assertEqual(hostile.total, 195)
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 175)
        for _ in range(15):
            run.step({})
        run.step({soldier: (*rifle, 1)})
        self.assertEqual(own.temporary_shields, 155)

        immunity = self.cast_transcendence(run, zen)
        self.assertEqual(zen.stats.value(stats.HEALING), 0)
        run.step({})
        self.assertAlmostEqual(own.total, 585 + 155 + 4.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
        for _ in range(5):
            run.step({})
        self.assertEqual(own.total, 755)
        self.assertEqual(own.temporary_shields, 155)
        self.assertEqual(hostile.total, 195)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 15)

        for _ in range(380):
            run.step({})
            if not immunity.active:
                break
        self.assertFalse(immunity.active)
        before = own.total
        run.step({soldier: (*rifle, 1)})
        for _ in range(20):
            run.step({})
        self.assertLess(own.total, before)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 15)

    def nano_scene(self, *, extra_players=()):
        run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)), ('Ball', 'Ana', 'Soldier', *extra_players))
        player, ana, enemy = run.players[:3]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team = ana.team = 0
        enemy.team = 1
        self.assertTrue(match.switch_hero(player, heroes()[ball.HERO]))
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        ana.body_script.ult_seconds = 0
        x, y, z = player.mover.position
        relocate(run, ana, (x + 5, y, z))
        relocate(run, enemy, (x, y, z + 6))
        for _ in range(40):
            run.step({})
        return run, player, ana, enemy

    def test_actual_nano_heals_and_scales_ball_damage_until_world_deadline(self):
        for grenade_team, healing in ((None, 250), (0, 375), (1, 0)):
            with self.subTest(grenade_team=grenade_team):
                run, player, ana, enemy = self.nano_scene()
                match = run.match
                x, y, z = player.mover.position
                fight = match.combat
                targets = {t.owner: t for t in fight._targets()}
                own, hostile = (fight.vitals_of(targets[p]) for p in (player, enemy))
                own.damage(500)
                fight._sync_body_health(player, own)
                run.step({player: (0, 0, 16)})
                cast_aim = aim(fight._eye(ana), (x, y + 0.8, z))
                if grenade_team is not None:
                    ana.team = grenade_team
                    for frame in range(45):
                        run.step({ana: (*cast_aim, 16 if frame == 0 else 0)})
                        if own.healing_block or own.healing_boost:
                            break
                    self.assertTrue(own.healing_boost if grenade_team == 0 else own.healing_block)
                    ana.team = 0

                charge = ana.body_script.component.entity_var(ULT_CHARGE)
                ana.body_script.set_ult_charge(ana.body_script.component.entity_var(ULT_COST).value())
                total, shield = own.total, own.temporary_shields
                run.step({ana: (*cast_aim, 32)})
                self.assertEqual(charge.value(), 0)
                self.assertEqual(own.total, total)
                self.assertFalse(own.nano_until)
                for _ in range(13):
                    run.step({ana: (*cast_aim, 0)})
                    if own.nano_until:
                        break
                self.assertEqual(own.nano_until, run.frame + 500)
                self.assertAlmostEqual(own.total, total + healing)
                self.assertEqual(own.temporary_shields, shield)

                shot = aim(fight._eye(player), (x, y + 0.8, z + 6))
                rifle = aim(fight._eye(enemy), (x, y + 0.8, z))
                run.step({player: (*shot, 1), enemy: (*rifle, 1)})
                self.assertEqual(hostile.total, 192.5)
                self.assertEqual(own.temporary_shields, shield - 10)
                run.frame = own.nano_until - 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                run.step({player: (*shot, 1), enemy: (*rifle, 1)})
                self.assertEqual(hostile.total, 187.5)
                self.assertEqual(own.total, total + healing - 30)

    def test_nano_rejects_invalid_target_and_caster_death_interrupts_release(self):
        for condition in ('uncharged', 'enemy', 'range', 'wall', 'death'):
            with self.subTest(condition=condition):
                run, player, ana, enemy = self.nano_scene()
                fight = run.match.combat
                x, y, z = player.mover.position
                if condition == 'enemy':
                    player.team = enemy.team
                elif condition == 'range':
                    relocate(run, ana, (x + 42, y, z))
                elif condition == 'wall':
                    add_side_wall(run, ana, x + 2.5, y, z)
                targets = {target.owner: target for target in fight._targets()}
                own, caster = (fight.vitals_of(targets[p]) for p in (player, ana))
                own.damage(200)
                fight._sync_body_health(player, own)
                if condition == 'death':
                    caster.damage(190)
                    fight._sync_body_health(ana, caster)
                before = own.total
                charge = ana.body_script.component.entity_var(ULT_CHARGE)
                cost = ana.body_script.component.entity_var(ULT_COST).value()
                ana.body_script.set_ult_charge(0 if condition == 'uncharged' else cost)
                cast_aim = aim(fight._eye(ana), (x, y + 0.8, z))
                run.step({ana: (*cast_aim, 32)})
                self.assertEqual(charge.value(), 0 if condition in ('uncharged', 'death') else cost)
                if condition == 'death':
                    ax, ay, az = ana.mover.position
                    rifle = aim(fight._eye(enemy), (ax, ay + 0.8, az))
                    run.step({enemy: (*rifle, 1)})
                    self.assertTrue(caster.dead)
                for _ in range(16):
                    run.step({ana: (*cast_aim, 0)})
                self.assertEqual(own.total, before)
                self.assertEqual(own.nano_until, 0)

    def test_nano_survives_caster_death_and_second_cast_refreshes_without_stacking(self):
        run, player, ana, enemy = self.nano_scene(extra_players=('Second Ana',))
        match = run.match
        fight = match.combat
        second = run.players[-1]
        second.team = player.team
        self.assertTrue(match.switch_hero(second, heroes()[ANA]))
        second.body_script.ult_seconds = 0
        x, y, z = player.mover.position
        relocate(run, second, (x - 5, y, z))
        for _ in range(40):
            run.step({})
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        own, hostile, caster = vitals[player], vitals[enemy], vitals[ana]
        deadlines = []
        for source in (ana, second):
            script = source.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            direction = aim(fight._eye(source), (x, y + 0.8, z))
            run.step({source: (*direction, 32)})
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
            for _ in range(13):
                run.step({})
            deadlines.append(own.nano_until)
            if source is ana:
                self.assertGreater(deadlines[0], run.frame)
                caster.damage(190)
                fight._sync_body_health(ana, caster)
                ax, ay, az = ana.mover.position
                rifle = aim(fight._eye(enemy), (ax, ay + 0.8, az))
                run.step({enemy: (*rifle, 1)})
                self.assertTrue(caster.dead)
                self.assertEqual(own.nano_until, deadlines[0])
                self.assertEqual(fight.outgoing_damage_scale(player.body), 1.5)
        self.assertGreater(deadlines[1], deadlines[0])
        cannon = aim(fight._eye(player), (x, y + 0.8, z + 6))
        run.step({player: (*cannon, 1)})
        self.assertEqual(hostile.total, 192.5)
        run.step({})
        for deadline, health in zip(deadlines, (185, 180), strict=True):
            run.frame = deadline - 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            run.step({player: (*cannon, 1)})
            self.assertEqual(hostile.total, health)
            run.step({})

    def test_replaced_ball_does_not_receive_pending_or_active_nano(self):
        for before_release in (False, True):
            with self.subTest(before_release=before_release):
                run, player, ana, enemy = self.nano_scene()
                match = run.match
                fight = match.combat
                x, y, z = player.mover.position
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                old_body = player.body
                script = ana.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                cast_aim = aim(fight._eye(ana), (x, y + 0.8, z))
                run.step({ana: (*cast_aim, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                if not before_release:
                    for _ in range(13):
                        run.step({})
                    self.assertGreater(own.nano_until, run.frame)
                self.assertTrue(match.switch_hero(player, player.hero))
                self.assertNotEqual(player.body, old_body)
                self.assertNotIn(old_body, fight.vitals)
                relocate(run, player, (x, y, z))
                fresh = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                fresh.damage(100)
                fight._sync_body_health(player, fresh)
                before = fresh.total
                for _ in range(30):
                    run.step({})
                targets = {t.owner: t for t in fight._targets()}
                self.assertEqual(fresh.nano_until, 0)
                self.assertEqual(fresh.total, before)
                shot = aim(fight._eye(player), (x, y + 0.8, z + 6))
                run.step({player: (*shot, 1)})
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 195)

    def test_actual_biotic_rifle_heals_ball_over_time_and_respects_grenade_modifiers(self):
        for scoped, status in (
            (False, 'none'), (True, 'none'), (False, 'boost'), (True, 'block'),
            (False, 'cover'), (True, 'cover'),
        ):
            with self.subTest(scoped=scoped, status=status):
                run, player, ana, enemy = self.nano_scene()
                match = run.match
                fight = match.combat
                x, y, z = player.mover.position
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                own.damage(300)
                fight._sync_body_health(player, own)
                run.step({player: (0, 0, 16)})
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                deadline = 0
                if status == 'cover':
                    add_side_wall(run, ana, x + 2.5, y, z)
                if status in ('boost', 'block'):
                    grenadier = ana
                    if status == 'block':
                        grenadier = enemy
                        origin = tuple(enemy.mover.position)
                        self.assertTrue(match.switch_hero(enemy, heroes()[ANA]))
                        relocate(run, enemy, origin)
                        for _ in range(20):
                            run.step({})
                    direction = aim(fight._eye(grenadier), (x, y + 0.8, z))
                    for frame in range(45):
                        run.step({grenadier: (*direction, 16 if frame == 0 else 0)})
                        if own.healing_boost or own.healing_block:
                            break
                    spans = own.healing_block if status == 'block' else own.healing_boost
                    self.assertTrue(spans)
                    deadline = spans[-1][1]
                    if status == 'boost':
                        for _ in range(45):
                            run.step({})
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*shot, 2)})
                if status == 'block':
                    while run.frame < deadline - 11:
                        run.step({ana: (*shot, 2)})
                before, shield = own.total, own.temporary_shields
                credited = ana.stats.value(stats.HEALING)
                run.step({ana: (*shot, 3 if scoped else 1)})
                for _ in range(24):
                    if fight.rifle_doses:
                        break
                    run.step({ana: (*shot, 2 if scoped else 0)})
                if status == 'cover':
                    rifle = next(i for i in ana.body_script.component.instances.values()
                                 if i.graph.index == 0x0B94)
                    self.assertEqual(rifle.vars[53].value(), 11)
                    self.assertFalse(fight.rifle_doses)
                    self.assertEqual(own.total, before)
                    self.assertEqual(ana.stats.value(stats.HEALING), credited)
                    continue
                dose, = fight.rifle_doses
                self.assertEqual(dose.recipient, player.body)
                self.assertEqual(own.total, before)
                run.step({})
                if status == 'block':
                    self.assertEqual(own.total, before)
                    self.assertLess(run.frame, deadline)
                else:
                    self.assertGreater(own.total, before)
                    self.assertLess(own.total - before, 3)
                run.frame += 45
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                healed = 105 if status == 'boost' else 70
                if status == 'block':
                    healed *= 1 - ((deadline - 1) * combat.TICK - dose.start) / 0.6
                self.assertAlmostEqual(own.total, before + healed)
                self.assertAlmostEqual(ana.stats.value(stats.HEALING), credited + healed)
                self.assertEqual(own.temporary_shields, shield)
                self.assertFalse(fight.rifle_doses)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_rifle_heal_is_bound_to_ball_body_and_survives_caster_replacement(self):
        for retire_recipient in (False, True):
            with self.subTest(retire_recipient=retire_recipient):
                run, player, ana, _ = self.nano_scene()
                match = run.match
                fight = match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                own.damage(200)
                fight._sync_body_health(player, own)
                before = own.total
                x, y, z = player.mover.position
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                run.step({ana: (*shot, 1)})
                for _ in range(24):
                    if fight.rifle_doses:
                        break
                    run.step({})
                dose, = fight.rifle_doses
                self.assertEqual(dose.recipient, player.body)
                run.step({})
                self.assertGreater(own.total, before)
                retired = player if retire_recipient else ana
                self.assertTrue(match.switch_hero(retired, retired.hero))
                if retire_recipient:
                    self.assertFalse(fight.rifle_doses)
                    current = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                    current.damage(200)
                    fight._sync_body_health(player, current)
                    before = current.total
                else:
                    current = own
                for _ in range(45):
                    run.step({})
                self.assertAlmostEqual(current.total, before + (0 if retire_recipient else 70))
                self.assertFalse(fight.rifle_doses)

    def test_hostile_ana_rifle_delivers_70_damage_over_time_into_ball_pools(self):
        for scoped, shield in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(scoped=scoped, shield=shield):
                run, player, ana, _ = self.nano_scene()
                ana.team = 1
                fight = run.match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                if shield:
                    run.step({player: (0, 0, 16)})
                    self.assertGreaterEqual(own.temporary_shields, 70)
                x, y, z = player.mover.position
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*shot, 2)})
                before, adaptive = own.total, own.temporary_shields
                run.step({ana: (*shot, 3 if scoped else 1)})
                for _ in range(24):
                    if fight.rifle_doses:
                        break
                    run.step({ana: (*shot, 2 if scoped else 0)})
                dose, = fight.rifle_doses
                self.assertFalse(dose.healing)
                self.assertEqual(dose.recipient, player.body)
                self.assertEqual(own.total, before)
                run.step({})
                self.assertAlmostEqual(before - own.total, 70 * combat.TICK / 0.6)
                run.frame += 45
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertAlmostEqual(own.total, before - 70)
                self.assertAlmostEqual(own.temporary_shields, adaptive - 70 if shield else 0)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 70)
                self.assertFalse(fight.rifle_doses)

    def test_rifle_damage_crossing_nano_expiry_reduces_only_early_ticks(self):
        for gap in (45, 80, 130):
            with self.subTest(gap=gap):
                run, player, ally, ana = self.nano_scene()
                fight = run.match.combat
                origin = tuple(ana.mover.position)
                self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
                relocate(run, ana, origin)
                x, y, z = player.mover.position
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                aim_nano = aim(fight._eye(ally), (x, y + 0.8, z))
                script = ally.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({ally: (*aim_nano, 32)})
                for _ in range(13):
                    run.step({})
                deadline = own.nano_until
                self.assertGreater(deadline, run.frame)
                run.frame = deadline - 55
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                while run.frame < deadline - 12:
                    run.step({ana: (*shot, 2)})
                run.step({ana: (*shot, 3)})
                for _ in range(8):
                    if fight.rifle_doses:
                        break
                    run.step({ana: (*shot, 2)})
                dose, = fight.rifle_doses
                self.assertLess(run.frame, deadline)
                self.assertEqual(own.total, 600)
                run.frame += gap
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                reduced = ((deadline - 1) * combat.TICK - dose.start) * 70 / 0.6
                self.assertAlmostEqual(600 - own.total, 70 - reduced / 2)
                self.assertFalse(fight.rifle_doses)
                settled = own.total
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(own.total, settled)

    def test_rifle_dose_crosses_adaptive_shield_expiry_during_world_catchup(self):
        for gap in (0, 130):
            with self.subTest(gap=gap):
                run, player, _, ana = self.nano_scene()
                match = run.match
                fight = match.combat
                origin = tuple(ana.mover.position)
                self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
                relocate(run, ana, origin)
                x, y, z = player.mover.position
                run.step({player: (0, 0, 16)})
                own = fight.vitals[player.body]
                deadline = run.frame + 562
                self.assertEqual(own.adaptive_until, deadline)
                run.frame = deadline - 55
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                while run.frame < deadline - 12:
                    run.step({ana: (*shot, 2)})
                run.step({ana: (*shot, 3)})
                for _ in range(8):
                    if fight.rifle_doses:
                        break
                    run.step({ana: (*shot, 2)})
                dose, = fight.rifle_doses
                self.assertEqual(dose.recipient, player.body)
                self.assertLess(run.frame, deadline)
                self.assertGreater(own.temporary_shields, 0)
                shielded = ((deadline - 1) * combat.TICK - dose.start) * 70 / 0.6
                frozen = player.body_script.frame
                for elapsed in ((gap,) if gap else (1,) * 130):
                    run.frame += elapsed
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(player.body_script.frame, frozen)
                self.assertEqual(own.temporary_shields, 0)
                self.assertAlmostEqual(own.total, 600 - (70 - shielded))
                self.assertFalse(fight.rifle_doses)

    def test_long_update_preserves_rifle_damage_and_transcendence_order_on_wounded_ball(self):
        for gap, health in ((0, 5), (80, 5), (130, 5), (0, 1), (130, 1)):
            with self.subTest(gap=gap, health=health):
                run, player, _, enemy = self.nano_scene(extra_players=('Zenyatta',))
                match = run.match
                fight = match.combat
                zen = run.players[-1]
                zen.team = player.team
                self.assertTrue(match.switch_hero(zen, heroes()[ZENYATTA]))
                origin = tuple(enemy.mover.position)
                self.assertTrue(match.switch_hero(enemy, heroes()[ANA]))
                relocate(run, enemy, origin)
                x, y, z = player.mover.position
                relocate(run, zen, (x, y, z - 5))
                shot = aim(fight._eye(enemy), (x, y + 0.8, z))
                for _ in range(40):
                    run.step({enemy: (*shot, 2)})
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({zen: (-32768, 0, 32), enemy: (*shot, 2)})
                self.assertTrue(self.protection_instance(zen)[1].active)
                run.step({enemy: (*shot, 3)})
                for _ in range(8):
                    if fight.rifle_doses:
                        break
                    run.step({enemy: (*shot, 2)})
                dose, = fight.rifle_doses
                self.assertEqual(dose.recipient, player.body)
                self.assertFalse(dose.healing)
                own = fight.vitals[player.body]
                self.assertEqual(own.total, 600)
                own.damage(605 - health)
                self.assertEqual(own.total, health)
                fight._sync_body_health(player, own)

                if gap:
                    run.frame += gap
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(130):
                        run.step({})
                elapsed = gap or 130
                if health == 1:
                    self.assertTrue(own.dead)
                    self.assertEqual(own.death_tick, round(dose.start / combat.TICK) + 1)
                    self.assertEqual(zen.stats.value(stats.HEALING), 0)
                    self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 1)
                else:
                    self.assertFalse(own.dead)
                    self.assertAlmostEqual(own.total, health + 4.8 * elapsed - 70)
                    self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8 * elapsed)
                    self.assertAlmostEqual(enemy.stats.value(stats.HERO_DAMAGE), 70)
                self.assertFalse(fight.rifle_doses)

    def test_long_update_preserves_rifle_damage_and_biotic_field_order_on_wounded_ball(self):
        cases = (
            (False, 0, 60), (False, 80, 60), (False, 130, 60), (False, 0, 1), (False, 130, 1),
            (True, 0, 60), (True, 45, 60), (True, 80, 60), (True, 130, 60), (True, 0, 1), (True, 130, 1),
        )
        for opens_during_dose, gap, health in cases:
            with self.subTest(opens_during_dose=opens_during_dose, gap=gap, health=health):
                run, player, soldier, enemy = self.nano_scene()
                match = run.match
                fight = match.combat
                origin = tuple(enemy.mover.position)
                self.assertTrue(match.switch_hero(enemy, heroes()[ANA]))
                relocate(run, enemy, origin)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                x, y, z = player.mover.position
                relocate(run, soldier, (x + 3, y, z))
                shot = aim(fight._eye(enemy), (x, y + 0.8, z))
                for _ in range(40):
                    run.step({enemy: (*shot, 2)})
                run.step({soldier: (0, 0, 16), enemy: (*shot, 2)})
                for _ in range(80):
                    run.step({enemy: (*shot, 2)})
                    if fight.projectiles.shots:
                        field, = fight.projectiles.shots
                        ready = field.field_due if opens_during_dose else field.field_tick
                        if ready is not None:
                            break
                field, = fight.projectiles.shots
                opening = field.field_due
                self.assertIsNotNone(opening)
                run.step({enemy: (*shot, 3)})
                for _ in range(8):
                    if fight.rifle_doses:
                        break
                    run.step({enemy: (*shot, 2)})
                dose, = fight.rifle_doses
                self.assertEqual(dose.recipient, player.body)
                self.assertFalse(dose.healing)
                own = fight.vitals[player.body]
                self.assertEqual(own.total, 600)
                own.damage(605 - health)
                self.assertEqual(own.total, health)
                fight._sync_body_health(player, own)

                admitted = run.frame
                if opens_during_dose:
                    self.assertIsNone(field.field_tick)
                    self.assertLess(admitted, opening)
                else:
                    self.assertEqual(field.field_tick, opening)
                if gap:
                    run.frame += gap
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(130):
                        run.step({})
                self.assertEqual(field.field_tick, opening)
                if health == 1:
                    self.assertTrue(own.dead)
                    self.assertEqual(own.death_tick, round(dose.start / combat.TICK) + 1)
                    self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                    self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 1)
                else:
                    # Outside the retained window, only ticks that overlap the dose can heal.
                    last_dose_tick = round(dose.start / combat.TICK) + 38
                    first_heal_boundary = max(admitted, opening)
                    retained_boundary = run.frame - combat.COMBAT_CATCH_UP
                    skipped = max(0, retained_boundary - max(first_heal_boundary, last_dose_tick))
                    healing_ticks = run.frame - first_heal_boundary - (skipped if gap else 0)
                    healing = healing_ticks * projectiles.HEAL_PER_SECOND * combat.TICK
                    self.assertFalse(own.dead)
                    self.assertAlmostEqual(own.total, health + healing - 70)
                    self.assertAlmostEqual(soldier.stats.value(stats.HEALING), healing)
                    self.assertAlmostEqual(enemy.stats.value(stats.HERO_DAMAGE), 70)
                self.assertFalse(fight.rifle_doses)

    def test_lethal_rifle_dose_retains_original_ana_body_after_replacement(self):
        for gap in (0, 130):
            with self.subTest(gap=gap):
                run, player, ana, _ = self.nano_scene()
                ana.team = 1
                fight = run.match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                own.damage(own.total - 20 + 5)
                self.assertEqual(own.total, 20)
                fight._sync_body_health(player, own)
                x, y, z = player.mover.position
                shot = aim(fight._eye(ana), (x, y + 0.8, z))
                run.step({ana: (*shot, 1)})
                for _ in range(24):
                    if fight.rifle_doses:
                        break
                    run.step({})
                original = ana.body
                dose, = fight.rifle_doses
                self.assertEqual(dose.source, original)
                death_tick = round(dose.start / combat.TICK) + 11
                self.assertTrue(run.match.switch_hero(ana, ana.hero))
                self.assertNotEqual(ana.body, original)
                if gap:
                    run.frame += gap
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(45):
                        run.step({})
                self.assertTrue(own.dead)
                self.assertEqual(own.death_tick, death_tick)
                self.assertEqual(own.killer, original)
                self.assertEqual(player.client.messages(combat.DEATH_NOTICE)[-1]['+0x7C'], {'+0x0': original})
                self.assertEqual(ana.client.messages(combat.KILL_NOTICE)[-1]['+0x80'], {'+0x0': original})
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 20)
                self.assertFalse(fight.rifle_doses)

    def test_actual_ana_grenade_arcs_over_low_cover_and_hits_ball_on_landing(self):
        stepped_impact = None
        for height, skip in ((2.5, False), (2.5, True), (6, False)):
            with self.subTest(height=height, skip=skip):
                run, player, ana = self.scene(ANA)
                match = run.match
                collision = World([
                    (-20, 0, -20, -20, 0, 20, 100, 0, 20),
                    (-20, 0, -20, 100, 0, 20, 100, 0, -20),
                    (20, 0, -8, 20, height, -8, 20, height, 8),
                    (20, 0, -8, 20, height, 8, 20, 0, 8),
                ])
                match.collision_world = collision
                match.collision_provider = StaticCollisionProvider(collision)
                relocate(run, ana, (0, 0, 0))
                relocate(run, player, (42.5, 0, 0))
                for _ in range(10):
                    run.step({})
                fight = match.combat
                origin = fight._eye(ana)
                angles = aim(origin, (40, origin[1] + 7.6, 0))
                run.step({ana: (*angles, 16)})
                for _ in range(40):
                    run.step({ana: (*angles, 0)})
                    grenade = next((r for r in fight.rockets if r.blast and
                                    r.blast.effect == combat.ANA_GRENADE_EFFECT), None)
                    if grenade is not None:
                        break
                self.assertIsNotNone(grenade)
                launch = run.frame
                if skip:
                    for _ in range(35):
                        run.step({})
                    self.assertIn(grenade, fight.rockets)
                    run.frame += 55
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertNotIn(grenade, fight.rockets)
                for _ in range(120):
                    if grenade not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(grenade, fight.rockets)
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                if height < 3:
                    self.assertEqual(own.total, 555)
                    self.assertLess(grenade.position[1], 0.35)
                    self.assertLess(grenade.position[0], 41)
                    impact = own.healing_block[-1][0] - launch, grenade.position
                    if skip:
                        self.assertLess(own.healing_block[-1][0], run.frame)
                        self.assertEqual(impact, stepped_impact)
                    else:
                        stepped_impact = impact
                else:
                    self.assertEqual(own.total, 600)
                    self.assertFalse(own.healing_block)
                    self.assertGreater(grenade.position[0], 19)
                    self.assertLess(grenade.position[0], 20)

    def test_actual_ana_grenade_stops_at_travel_limit_on_a_long_drop(self):
        for platform, lateral, hits in ((293, 0, True), (300, 3, False), (305, 0, False)):
            with self.subTest(platform=platform):
                run, player, ana = self.scene(ANA)
                match = run.match
                collision = World([
                    (-20, 0, -20, -20, 0, 20, 100, 0, 20),
                    (-20, 0, -20, 100, 0, 20, 100, 0, -20),
                    (-5, platform, -5, -5, platform, 5, 0, platform, 5),
                    (-5, platform, -5, 0, platform, 5, 0, platform, -5),
                ])
                match.collision_world = collision
                match.collision_provider = StaticCollisionProvider(collision)
                relocate(run, ana, (-0.05, platform, 0))
                relocate(run, player, (18, 0, lateral))
                for _ in range(10):
                    run.step({})
                fight = match.combat
                x, y, z = fight._eye(ana)
                angles = aim((x, y, z), (x + 1, y - 10, z))
                run.step({ana: (*angles, 16)})
                for _ in range(40):
                    run.step({ana: (*angles, 0)})
                    grenade = next((r for r in fight.rockets if r.blast and
                                    r.blast.effect == combat.ANA_GRENADE_EFFECT), None)
                    if grenade is not None:
                        break
                self.assertIsNotNone(grenade)
                self.assertGreater(grenade.position[1], platform)
                self.assertEqual(grenade.reach, 300)
                self.assertAlmostEqual(grenade.left + grenade.flown, 60)
                run.step({})
                for _ in range(9):
                    if grenade not in fight.rockets:
                        break
                    run.frame += 50
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertNotIn(grenade, fight.rockets)
                target = next(t for t in fight._targets() if t.owner is player)
                own = fight.vitals_of(target)
                self.assertEqual(own.total, 555 if hits else 600)
                self.assertEqual(bool(own.healing_block), hits)
                if hits:
                    self.assertLess(grenade.travelled, 300)
                else:
                    self.assertAlmostEqual(grenade.travelled, 300, places=3)
                    self.assertGreater(grenade.position[1], 0)
                    self.assertLess(grenade.flown, 10)
                    if lateral:
                        self.assertLess(
                            combat.segment_distance(grenade.position, *target.segment()) - target.radius, 4,
                        )

    def test_actual_ana_grenade_changes_transcendence_healing_without_refilling_adaptive_shield(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, player, zen, ana = self.retained_scene(ZENYATTA)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                player.team = zen.team = 0
                ana.team = 0 if ally else 1
                self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
                x, y, z = player.mover.position
                relocate(run, ana, (x + 5, y, z))
                relocate(run, zen, (x, y, z + 6))
                for _ in range(40):
                    run.step({})
                fight = match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                # Leave enough missing permanent health to measure the next pulse.
                own.damage(200)
                fight._sync_body_health(player, own)
                run.step({player: (0, 0, 16)})
                shield = 100 if ally else 175
                self.assertEqual(own.temporary_shields, shield)
                before = own.total
                angles = aim(fight._eye(ana), (x, y + 0.8, z))
                for frame in range(65):
                    run.step({ana: (*angles, 16 if frame == 0 else 0)})
                    if own.total != before:
                        break
                impact = run.frame
                self.assertEqual(own.total, before + (100 if ally else -50))
                shield = shield if ally else shield - 50
                self.assertEqual(own.temporary_shields, shield)
                after = own.total
                self.cast_transcendence(run, zen)
                run.step({})
                self.assertAlmostEqual(own.total, after + (7.2 if ally else 0))
                if not ally:
                    run.frame = impact + 249
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(own.total, after)
                    self.assertEqual(zen.stats.value(stats.HEALING), 0)
                    run.step({})
                    self.assertAlmostEqual(own.total, after + 4.8)
                    self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
                self.assertEqual(own.temporary_shields, shield)

    def test_transcendence_rechecks_cover_and_range_and_does_not_heal_past_its_deadline(self):
        run, player, zen = self.scene(ZENYATTA, distance=4)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        zen.team = player.team
        fight = run.match.combat
        own = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        own.damage(160)
        fight._sync_body_health(player, own)
        before = own.total
        x, y, z = player.mover.position
        clear = run.match.collision_world
        wall = z + 2
        a, b = (x - 2, y, wall), (x + 2, y, wall)
        c, d = (x + 2, y + 3, wall), (x - 2, y + 3, wall)
        run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
        immunity = self.cast_transcendence(run, zen)
        for _ in range(5):
            run.step({})
        self.assertEqual(own.total, before)

        relocate(run, player, (x + 11, y, z))
        run.match.collision_world = clear
        for _ in range(5):
            run.step({})
        self.assertEqual(own.total, before)
        relocate(run, player, (x, y, z))
        run.step({})
        self.assertAlmostEqual(own.total, before + 4.8)

        # No caster command advances the graph during this gap. Cover blocks
        # the last live interval, and clearing it past the deadline cannot heal.
        run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
        run.frame += 380
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(immunity.active)
        run.match.collision_world = clear
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(own.total, before + 4.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)

    def test_transcendence_rebinds_replaced_ball_and_ends_with_caster_body(self):
        run, player, zen, soldier = self.retained_scene(ZENYATTA)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = zen.team = 0
        soldier.team = 1
        fight = run.match.combat
        position = tuple(player.mover.position)
        x, y, z = position
        relocate(run, soldier, (x + 6, y, z))
        for _ in range(15):
            run.step({})
        rifle = aim(fight._eye(soldier), (x, y + 0.8, z))
        run.step({soldier: (*rifle, 1)})
        old_body = player.body
        old_vitals = fight.vitals[old_body]
        self.assertEqual(old_vitals.total, 585)
        old_zen_script = zen.body_script
        old_aura = self.cast_transcendence(run, zen)
        run.step({})
        self.assertAlmostEqual(old_vitals.total, 589.8)

        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old_body)
        relocate(run, player, position)
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, player), 600)
        run.step({soldier: (*rifle, 1)})
        current = fight.vitals[player.body]
        self.assertEqual(current.total, 585)
        run.step({})
        self.assertTrue(old_aura.active)
        self.assertAlmostEqual(current.total, 589.8)
        self.assertAlmostEqual(old_vitals.total, 589.8)

        zen_position = tuple(zen.mover.position)
        self.assertTrue(run.match.switch_hero(zen, zen.hero))
        relocate(run, zen, zen_position)
        self.assertTrue(old_zen_script.retired)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(current.total, 589.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 9.6)
        _, idle = self.protection_instance(zen)
        self.assertFalse(idle.active)
        new_aura = self.cast_transcendence(run, zen)
        self.assertIsNot(old_aura, new_aura)
        for _ in range(4):
            run.step({})
        self.assertEqual(current.total, 600)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 19.8)

    def test_transcendence_retains_cannon_blocker_and_expires(self):
        run, player, zen, behind = self.retained_scene(ZENYATTA)
        x, y, z = zen.mover.position
        angles = aim(run.match.combat._eye(player), (x, y + 1, z))
        immunity = self.cast_transcendence(run, zen)
        run.step({player: (*angles, 1)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, behind), 200)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(380):
            run.step({})
            if not immunity.active:
                break
        self.assertFalse(immunity.active)
        self.assertAlmostEqual(immunity.ended_at - immunity.start, 6000, delta=16)
        run.step({player: (*angles, 1), zen: (-32768, 0, 32)})
        self.assertFalse(immunity.active)
        self.assertEqual(self.health(run, zen), 195)
        self.assertEqual(self.health(run, behind), 200)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_transcendence_world_expiry_opens_cannon_with_caster_clock_frozen(self):
        for before in (False, True):
            with self.subTest(before=before):
                self.check_transcendence_cannon_deadline(before)

    def check_transcendence_cannon_deadline(self, before):
        run, player, zen = self.scene(ZENYATTA, distance=4)
        match = run.match
        fight = match.combat
        immunity = self.cast_transcendence(run, zen)
        source = zen.body_script
        frozen = source.frame, source.component.now
        lifetime = immunity.instance.state(3)
        self.assertEqual(lifetime.start % source.frame_ms, 0)
        deadline = (lifetime.start + 6000) // source.frame_ms
        frame = deadline - int(before)
        match.update(frame * combat.TICK, frame, frames=False)
        x, y, z = zen.mover.position
        angles = aim(fight._eye(player), (x, y + 0.85, z))
        player.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=1)])
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(immunity.active)
        self.assertEqual(self.health(run, zen), 200 if before else 195)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0 if before else 5)

    def test_transcendence_rejects_piledriver_damage_but_keeps_lift(self):
        run, player, zen = self.scene(ZENYATTA)
        immunity = self.cast_transcendence(run, zen)
        lift(run, player)
        run.step({player: (0, 0, 128)})
        for _ in range(89):
            run.step({})
        self.assertEqual(pile_instance(player).states[7].counter, 1)
        self.assertTrue(immunity.active)
        self.assertEqual(self.health(run, zen), 200)
        self.assertTrue(zen.mover.airborne)
        self.assertGreater(zen.mover.position[1], 1.2)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_cryo_pet_stops_cannon_heals_mei_and_retires_on_cancel(self):
        run, player, mei, behind = self.retained_scene(barriers.MEI)
        x, y, z = mei.mover.position
        angles = aim(run.match.combat._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 1)})
        self.assertEqual(self.health(run, mei), 245)
        run.step({mei: (-32768, 0, 8)})
        ability, immunity = self.protection_instance(mei)
        self.assertTrue(immunity.active)
        ice = next(iter(run.match.combat.barriers.values()))
        self.assertEqual(ability.states[11].pet_entity, ice.entity)
        child = next(iter(ice.component.instances.values()))
        self.assertEqual(child.graph.index, 0x623)
        self.assertEqual(child.source_entity(), mei.body)
        origin = run.match.combat._eye(player)
        direction = combat.aim(*angles)
        hit = barriers.ray_hit(run.match.combat, player, origin, direction, 6)
        self.assertIs(hit[1], ice)
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, mei), 250)
        run.step({player: (*angles, 1)})
        self.assertEqual(self.health(run, mei), 250)
        self.assertEqual(self.health(run, behind), 200)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 78)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        run.step({mei: (-32768, 0, 2)})
        self.assertFalse(immunity.active)
        self.assertTrue(ice.retired)
        self.assertFalse(ice.component.instances)
        self.assertIsNone(barriers.ray_hit(run.match.combat, player, origin, direction, 6))
        for _ in range(24):
            run.step({})
        run.step({player: (*angles, 1), mei: (-32768, 0, 8)})
        self.assertFalse(immunity.active)
        self.assertEqual(self.health(run, mei), 245)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 10)

    def test_cryo_ice_and_immunity_expire_without_mei_commands(self):
        for before in (True, False):
            with self.subTest(before=before):
                run, player, mei = self.scene(barriers.MEI, distance=3)
                run.step({mei: (-32768, 0, 8)})
                instance, immunity = self.protection_instance(mei)
                fight = run.match.combat
                ice = fight.barriers[instance.states[11].pet_entity]
                source = mei.body_script
                frozen = source.frame, source.component.now
                deadline = (immunity.start + 4000) // source.frame_ms
                frame = deadline - int(before)
                run.match.update(frame * combat.TICK, frame, frames=False)
                x, y, z = mei.mover.position
                origin = fight._eye(player)
                angles = aim(origin, (x, y + 1, z))
                hit = barriers.ray_hit(fight, player, origin, combat.aim(*angles), 6)
                self.assertEqual(hit is not None, before)
                self.assertEqual(ice.retired, not before)
                player.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                self.assertEqual((source.frame, source.component.now), frozen)
                self.assertTrue(immunity.active)
                self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
                damage = 0 if before else 5
                self.assertEqual(self.health(run, mei), 250 - damage)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)

    def test_late_cryo_cast_does_not_create_expired_ice_or_heal(self):
        run, player, mei = self.scene(barriers.MEI, distance=3)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals = fight.vitals_of(target)
        vitals.damage(220)
        fight._sync_body_health(mei, vitals)
        activation = run.frame + 1
        deadline = activation + 250
        run.match.update(deadline * combat.TICK, deadline, frames=False)
        mei.take_commands([Command(activation, yaw=-32768, buttons=8)])
        instance, immunity = self.protection_instance(mei)
        self.assertTrue(immunity.active)
        self.assertEqual(instance.states[11].counter, 1)
        self.assertFalse(fight.barriers)
        self.assertEqual(vitals.total, 30)
        x, y, z = mei.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        player.take_commands([Command(deadline, yaw=angles[0], pitch=angles[1], buttons=1)])
        self.assertEqual(vitals.total, 25)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        mei.take_commands([Command(deadline, yaw=-32768, buttons=0)])
        self.assertEqual(vitals.total, 25)
        self.assertEqual(mei.stats.value(stats.HEALING), 0)

    def test_cryo_heal_spends_its_authored_budget_without_mei_commands(self):
        run, _, mei = self.scene(barriers.MEI)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals = fight.vitals_of(target)
        vitals.damage(220)
        fight._sync_body_health(mei, vitals)
        run.step({mei: (-32768, 0, 8)})
        instance, immunity = self.protection_instance(mei)
        heal = instance.states[13]
        source = mei.body_script
        frozen = source.frame, source.component.now
        end_ms = heal.start + 3900
        before = end_ms // source.frame_ms
        run.match.update(before * combat.TICK, before, frames=False)
        self.assertAlmostEqual(vitals.total, 30 + 200 * (before * source.frame_ms - heal.start) / 3900)
        run.match.update((before + 1) * combat.TICK, before + 1, frames=False)
        self.assertAlmostEqual(vitals.total, 230)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), 200)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(immunity.active)
        mei.take_commands([Command(before + 1, yaw=-32768, buttons=0)])
        self.assertAlmostEqual(vitals.total, 230)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), 200)

    def test_cryo_blocks_sleep_and_flashbang_until_cancel(self):
        for hero, button, effect, damage in (
            (ANA, 8, combat.SLEEP_EFFECT, 5),
            (0x02E0000000000042, 16, combat.FLASHBANG_EFFECT, 25),
        ):
            for cancel in (False, True):
                with self.subTest(hero=hex(hero), cancel=cancel):
                    run, caster, mei = self.scene(barriers.MEI, distance=3)
                    run.match.switch_hero(caster, heroes()[hero])
                    for _ in range(40):
                        run.step({})
                    run.step({mei: (-32768, 0, 8)})
                    _, immunity = self.protection_instance(mei)
                    self.assertTrue(immunity.active)
                    if cancel:
                        for _ in range(20):
                            run.step({})
                        run.step({mei: (-32768, 0, 2)})
                        self.assertFalse(immunity.active)
                    fight = run.match.combat
                    x, y, z = mei.mover.position
                    angles = aim(fight._eye(caster), (x, y + 1, z))
                    flew = False
                    for frame in range(45):
                        run.step({caster: (*angles, button if frame == 0 else 0)})
                        flew |= any(
                            hit is not None and hit.effect == effect
                            for rocket in fight.rockets for hit in (rocket.direct, rocket.blast)
                        )
                    self.assertTrue(flew)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(immunity.active, not cancel)
                    self.assertEqual(self.health(run, mei), 250 - (damage if cancel else 0))
                    self.assertEqual(fight.is_stunned(mei), cancel)

    def test_cryo_natural_end_retires_ice_and_caps_healing(self):
        run, player, mei = self.scene(barriers.MEI)
        x, y, z = mei.mover.position
        angles = aim(run.match.combat._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, mei), 220)
        run.step({mei: (-32768, 0, 8)})
        _, immunity = self.protection_instance(mei)
        self.assertTrue(immunity.active)
        ice = next(iter(run.match.combat.barriers.values()))
        for _ in range(255):
            run.step({})
            if not immunity.active:
                break
        self.assertFalse(immunity.active)
        self.assertAlmostEqual(immunity.ended_at - immunity.start, 4000, delta=16)
        self.assertTrue(ice.retired)
        self.assertEqual(self.health(run, mei), 250)

    def test_piledriver_leaves_cryo_mei_grounded_and_hits_after_cancel(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, player, mei = self.scene(barriers.MEI)
                run.step({mei: (-32768, 0, 8)})
                _, immunity = self.protection_instance(mei)
                self.assertTrue(immunity.active)
                for _ in range(20):
                    run.step({})
                if cancel:
                    run.step({mei: (-32768, 0, 2)})
                    self.assertFalse(immunity.active)
                lift(run, player)
                run.step({player: (0, 0, 128)})
                for _ in range(89):
                    run.step({})
                self.assertEqual(pile_instance(player).states[7].counter, 1)
                self.assertEqual(immunity.active, not cancel)
                self.assertEqual(self.health(run, mei), 150 if cancel else 250)
                self.assertEqual(mei.mover.airborne, cancel)
                if not cancel:
                    self.assertEqual(tuple(mei.mover.velocity), (0, 0, 0))
                    self.assertNotIn(mei.body, run.match.combat.pile_air_locks)
                charge = player.body_script.component.entity_var(ULT_CHARGE).value()
                self.assertEqual(charge, 100 if cancel else 0)
