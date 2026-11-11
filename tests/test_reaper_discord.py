"""Actual Discord command and 0176 selection with Reaper combat."""

import math
import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import ana, combat, discord, nano, reaper, stats, zarya_barriers
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


class ReaperDiscordTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Enemy", "Zenyatta")):
        run, owner, enemy, _ = reaper_tests.ReaperTests().scenario(team_mode=True, names=names)
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[discord.HERO]))
        owner.team = 0
        enemy.team = caster.team = 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        for _ in range(26):
            run.step({})
        return run, owner, enemy, caster

    def cast(self, run, caster, target, *, button=8):
        fight = run.match.combat
        angles = aim(fight._eye(caster), fight._eye(target))
        run.step({caster: (*angles, button)})
        for _ in range(10):
            run.step({caster: (*angles, 0)})

    def test_actual_discord_increases_incoming_shell_damage(self):
        run, owner, enemy, caster = self.scene()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        for _ in range(26):
            run.step({})
        fight = run.match.combat
        self.cast(run, caster, owner)
        orb = fight.discord.active[caster.body]
        self.assertEqual(orb.target, owner.body)
        self.assertEqual(orb.grace, 3)
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 100)

    def test_actual_ally_discord_increases_reaper_shell_and_reaping(self):
        run, owner, enemy, caster = self.scene()
        caster.team = owner.team
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        pool = combat.Vitals(enemy.body, 600)
        fight.vitals[enemy.body] = pool
        self.cast(run, caster, enemy)
        self.assertEqual(fight.discord.active[caster.body].target, enemy.body)
        reaper_tests.ReaperTests().fire(run, owner, enemy)
        self.assertAlmostEqual(pool.total, 450)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 202.5, places=4)

    def test_actual_wraith_cleanses_and_rejects_discord_reapplication(self):
        run, owner, _, caster = self.scene()
        fight = run.match.combat
        self.cast(run, caster, owner)
        self.assertEqual(fight.discord.active[caster.body].target, owner.body)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertNotIn(caster.body, fight.discord.active)
        for _ in range(55):
            run.step({})
        self.cast(run, caster, owner)
        self.assertTrue(reaper.intangible(owner))
        self.assertNotIn(caster.body, fight.discord.active)

    @staticmethod
    def block_caster(run, owner):
        x, y, z = owner.mover.position
        wall = x - 2
        run.match.collision_world = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (wall,y,z-8,wall,y+5,z-8,wall,y+5,z+8),
            (wall,y,z-8,wall,y+5,z+8,wall,y,z+8),
        ], name="Discord cover")

    def test_actual_wall_refuses_new_orb_and_existing_orb_expires_after_grace(self):
        run, owner, _, caster = self.scene()
        fight = run.match.combat
        self.cast(run, caster, owner)
        orb = fight.discord.active[caster.body]
        self.block_caster(run, owner)
        run.frame = math.floor((orb.last_visible + 2.9) / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.discord.scale(owner.body), 1.25)
        run.frame += 20
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.discord.scale(owner.body), 1)
        self.cast(run, caster, owner)
        self.assertNotIn(caster.body, fight.discord.active)

    def test_actual_helix_resolves_discord_cover_expiry_across_world_gap(self):
        def scene():
            run, owner, shooter, caster = self.scene()
            fight = run.match.combat
            x, y, z = owner.mover.position
            shooter.mover.position[:] = (x, y, z + 8)
            shooter.mover.body = None
            self.cast(run, caster, owner)
            orb = fight.discord.active[caster.body]
            self.assertEqual((orb.target, orb.grace), (owner.body, 3))
            self.block_caster(run, owner)
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            return run, owner, shooter, caster, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, owner, shooter, caster, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[owner.body].total, 100)
        travel = run.frame - fired
        for _ in range(200):
            advance(run, run.frame + 1)
            if caster.body not in fight.discord.active:
                break
        self.assertNotIn(caster.body, fight.discord.active)
        expiry = run.frame

        for amplified in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(amplified=amplified, skipped=skipped):
                    run, owner, shooter, caster, heading = scene()
                    fight = run.match.combat
                    own = fight.vitals[owner.body]
                    own.damage(115)
                    contact = expiry - int(amplified)
                    advance(run, contact - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertEqual(fight.discord.scale(owner.body), 1.25)
                    impacts = []

                    def observe(missile, *, world=fight, recipient=owner, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, world.discord.scale(recipient.body)))
                        landed(missile)

                    end = expiry + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(impacts, [(contact, 1.25 if amplified else 1)])
                    self.assertFalse(fight.rockets)
                    self.assertNotIn(caster.body, fight.discord.active)
                    self.assertEqual(own.total, 0 if amplified else 15)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 135 if amplified else 120)
                    if amplified:
                        self.assertEqual((own.death_tick, own.killer), (contact, shooter.body))
                    else:
                        self.assertFalse(own.dead)
                    outcomes.append((impacts, own.death_tick))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def blossom_discord_expiry_scene(self):
        run, owner, enemy, caster = self.scene()
        caster.team = owner.team
        x, y, z = owner.mover.position
        for actor, position in ((enemy, (x, y, z + 4)), (caster, (x - 4, y, z + 4))):
            actor.mover.position[:] = position
            actor.mover.body = None
        self.cast(run, caster, enemy)
        fight = run.match.combat
        orb = fight.discord.active[caster.body]
        self.assertEqual((orb.target, orb.grace), (enemy.body, 3))
        self.block_caster(run, enemy)
        return run, owner, enemy, caster

    def blossom_discord_expiry_trace(self, expiry, *, amplified, skipped):
        run, owner, enemy, caster = self.blossom_discord_expiry_scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        victim = fight.vitals[enemy.body]
        own.damage(own.total - 100)
        victim.damage(victim.total - 83.5)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(2100)
        second_pulse = expiry - int(amplified)
        first_pulse = second_pulse - math.ceil(reaper.BLOSSOM_INTERVAL / combat.TICK)
        run.frame = first_pulse - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.discord.scale(enemy.body), 1.25)
        pulses = []
        damage = fight._damage

        def observe(source, target, amount, *args, **kwargs):
            scale = fight.discord.scale(target.entity)
            result = damage(source, target, amount, *args, **kwargs)
            if source is owner and target.owner is enemy:
                pulses.append((fight.tick, amount, scale, victim.total, own.total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({owner: (0, 0, 32)})
            self.assertIsNotNone(reaper.blossom_state(owner.body_script))
            end = run.frame + 205
            for tick in (end,) if skipped else range(run.frame + 1, end + 1):
                run.match.update(tick * combat.TICK, tick, frames=False)
        base = reaper.BLOSSOM_DAMAGE
        expected = [(first_pulse, base, 1.25, 41)]
        if amplified:
            expected.append((second_pulse, base, 1.25, 0))
        else:
            third_pulse = first_pulse + math.ceil(2 * reaper.BLOSSOM_INTERVAL / combat.TICK)
            expected.extend(((second_pulse, base, 1, 7), (third_pulse, base, 1, 0)))
        self.assertEqual([row[:4] for row in pulses], expected)
        for _, _, _, health, reaping in pulses:
            self.assertAlmostEqual(reaping, 100 + (83.5 - health) * reaper.REAPING_RATIO, places=4)
        self.assertTrue(victim.dead)
        self.assertEqual((victim.death_tick, victim.killer), (expected[-1][0], owner.body))
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertNotIn(caster.body, fight.discord.active)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return pulses, victim.death_tick, own.total

    def test_actual_blossom_second_pulse_resolves_discord_expiry_across_world_gap(self):
        run, _, _, caster = self.blossom_discord_expiry_scene()
        fight = run.match.combat
        for _ in range(210):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if caster.body not in fight.discord.active:
                break
        self.assertNotIn(caster.body, fight.discord.active)
        expiry = run.frame
        for amplified in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(amplified=amplified, skipped=skipped):
                    outcome = self.blossom_discord_expiry_trace(expiry, amplified=amplified, skipped=skipped)
                    outcomes.append(outcome)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_discord_and_nano_compose_on_incoming_shell(self):
        import test_reaper_nano as nano_tests

        run, owner, enemy, caster = self.scene(names=("Reaper", "Enemy", "Zenyatta", "Ana"))
        support = run.players[3]
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        self.assertTrue(run.match.switch_hero(support, heroes()[ana.HERO]))
        support.team = owner.team
        x, y, z = owner.mover.position
        support.mover.position[:] = (x + 4, y, z)
        support.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        self.cast(run, caster, owner)
        nano_tests.ReaperNanoTests().cast(run, support, owner)
        self.assertEqual(fight.discord.scale(owner.body), 1.25)
        self.assertTrue(nano.active(fight, fight.vitals[owner.body]))
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 175)

    def test_actual_projected_barrier_cleanses_discord_and_blocks_reapply(self):
        run, owner, _, caster = self.scene(names=("Reaper", "Enemy", "Zenyatta", "Zarya"))
        ally = run.players[3]
        self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_barriers.HERO]))
        ally.team = owner.team
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        self.cast(run, caster, owner)
        self.assertEqual(fight.discord.scale(owner.body), 1.25)
        angles = aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (*angles, 16)})
        for _ in range(50):
            run.step({})
        self.assertIsNotNone(fight.zarya_bubbles.for_target(owner))
        self.assertEqual(fight.discord.scale(owner.body), 1)
        self.cast(run, caster, owner)
        self.assertNotIn(caster.body, fight.discord.active)

    def test_actual_repress_refuses_retarget_until_075_second_recovery(self):
        run, owner, enemy, caster = self.scene()
        enemy.team = owner.team
        fight = run.match.combat
        self.cast(run, caster, owner)
        first = fight.discord.active[caster.body]
        self.assertAlmostEqual(fight.discord.ready[caster.body] - (fight.now - 10 * combat.TICK), 0.75)
        self.cast(run, caster, enemy)
        self.assertIs(fight.discord.active[caster.body], first)
        for _ in range(45):
            run.step({})
        self.cast(run, caster, enemy)
        self.assertEqual(fight.discord.active[caster.body].target, enemy.body)
        self.assertEqual(fight.discord.scale(owner.body), 1)

    def test_actual_discord_search_rejects_beyond_40_metres(self):
        run, owner, _, caster = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x - 42, y, z)
        caster.mover.body = None
        self.cast(run, caster, owner)
        self.assertNotIn(caster.body, fight.discord.active)
        caster.mover.position[:] = (x - 39, y, z)
        caster.mover.body = None
        self.cast(run, caster, owner)
        self.assertEqual(fight.discord.active[caster.body].target, owner.body)

    def test_two_actual_discords_do_not_compound_and_caster_replacement_removes_only_its_orb(self):
        from ow174.game.content import SOLDIER

        run, owner, enemy, caster = self.scene(names=("Reaper", "Enemy", "Zen", "Second Zen"))
        other = run.players[3]
        self.assertTrue(run.match.switch_hero(other, heroes()[discord.HERO]))
        other.team = caster.team
        x, y, z = owner.mover.position
        other.mover.position[:] = (x + 4, y, z)
        other.mover.body = None
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        self.cast(run, caster, owner)
        self.cast(run, other, owner)
        self.assertEqual({orb.target for orb in fight.discord.active.values()}, {owner.body})
        self.assertEqual(len(fight.discord.active), 2)
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 100)
        first_body = caster.body
        self.assertTrue(run.match.switch_hero(caster, heroes()[SOLDIER]))
        self.assertNotIn(first_body, fight.discord.active)
        self.assertEqual(fight.discord.scale(owner.body), 1.25)
