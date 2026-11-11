"""Actual Ana Nano cast and Reaper combat modifiers."""

import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import ana, combat, mercy_beam, nano, reaper, stats
from ow174.game.collision import World
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperNanoTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Enemy", "Ana")):
        run, owner, enemy, _ = reaper_tests.ReaperTests().scenario(team_mode=True, names=names)
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
        owner.team = caster.team = 0
        enemy.team = 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        caster.body_script.ult_seconds = 0
        for _ in range(28):
            run.step({})
        return run, owner, enemy, caster

    def cast(self, run, caster, target, *, charge=None):
        fight = run.match.combat
        component = caster.body_script.component
        cost = component.entity_var(ULT_COST).value()
        caster.body_script.set_ult_charge(cost if charge is None else charge)
        x, y, z = target.mover.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, 32)})
        for _ in range(20):
            run.step({caster: (yaw, pitch, 0)})

    def test_actual_nano_heals_then_boosts_shell_and_reaping(self):
        run, owner, enemy, caster = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(180)
        self.cast(run, caster, owner)
        self.assertEqual(own.total, 250)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(nano.active(fight, own))
        self.assertAlmostEqual(own.nano_until - fight.now, 8, delta=0.32)
        own.damage(100)
        pool = combat.Vitals(enemy.body, 600)
        fight.vitals[enemy.body] = pool
        reaper_tests.ReaperTests().fire(run, owner, enemy)
        self.assertAlmostEqual(pool.total, 420)
        self.assertAlmostEqual(own.total, 213, places=4)

    def test_actual_nano_halves_incoming_shell_then_world_expiry_restores_damage(self):
        run, owner, enemy, caster = self.scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        x, y, z = owner.mover.position
        enemy.mover.position[:] = (x, y, z + 1.5)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        own = fight.vitals[owner.body]
        self.cast(run, caster, owner)
        self.assertTrue(nano.active(fight, own))
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertAlmostEqual(own.total, 190)
        run.frame += 510
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(nano.active(fight, own))
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertAlmostEqual(own.total, 70)

    def test_actual_helix_resolves_incoming_nano_expiry_across_world_gap(self):
        def scene():
            run, owner, shooter, caster = self.scene()
            fight = run.match.combat
            x, y, z = owner.mover.position
            shooter.mover.position[:] = (x, y, z + 8)
            shooter.mover.body = None
            self.cast(run, caster, owner)
            own = fight.vitals[owner.body]
            self.assertTrue(nano.active(fight, own))
            own.damage(160)
            expiry = round(own.nano_until / combat.TICK)
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            return run, shooter, own, expiry, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, shooter, own, _, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(own.total, 30)
        travel = run.frame - fired
        self.assertGreater(travel, 1)

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, shooter, own, expiry, heading = scene()
                    fight = run.match.combat
                    contact = expiry - int(protected)
                    advance(run, contact - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(nano.active(fight, own))
                    impacts = []

                    def observe(missile, *, world=fight, pool=own, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, nano.active(world, pool)))
                        landed(missile)

                    end = expiry + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(impacts, [(contact, protected)])
                    self.assertFalse(fight.rockets)
                    self.assertFalse(nano.active(fight, own))
                    self.assertEqual(own.total, 30 if protected else 0)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 60 if protected else 90)
                    if protected:
                        self.assertFalse(own.dead)
                    else:
                        self.assertEqual((own.death_tick, own.killer), (contact, shooter.body))
                    outcomes.append((impacts, own.death_tick))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_nano_amplifies_full_blossom_without_damage_charge(self):
        run, owner, enemy, caster = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(enemy.body, 1000)
        fight.vitals[enemy.body] = pool
        self.cast(run, caster, owner)
        self.assertTrue(nano.active(fight, fight.vitals[owner.body]))
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertAlmostEqual(pool.total, 949)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 167.85, places=4)
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertAlmostEqual(pool.total, 235)
        self.assertLess(owner.body_script.component.vars[342].value(), 30)

    def test_actual_blossom_resolves_nano_then_fortify_expiry(self):
        outcomes = {}
        for mode in ("posed", "idle", "skipped"):
            with self.subTest(mode=mode):
                run, owner, orisa, caster = self.scene(names=("Reaper", "Orisa", "Ana"))
                self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
                caster.team = orisa.team = 1
                x, y, z = owner.mover.position
                caster.mover.position[:] = (x + 12, y, z)
                orisa.mover.position[:] = (x, y, z + 2)
                caster.mover.body = orisa.mover.body = None
                fight = run.match.combat
                pool = fight.vitals[orisa.body] = combat.Vitals(orisa.body, 1000)
                self.cast(run, caster, orisa)
                self.assertTrue(nano.active(fight, pool))
                nano_expiry = round(pool.nano_until / combat.TICK)
                while run.frame < nano_expiry - 190:
                    run.step({})
                run.step({orisa: (0, 0, 8)})
                while run.frame < nano_expiry - 65:
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is orisa)
                self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
                own = fight.vitals[owner.body]
                own.damage(220)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({owner: (0, 0, 32)})
                self.assertAlmostEqual(1000 - pool.total, 10.2, places=4)
                frame = orisa.commands.last_frame
                end = run.frame + 200
                while run.frame < end:
                    if mode == "posed":
                        run.step({})
                    else:
                        run.frame = end if mode == "skipped" else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                if mode != "posed":
                    self.assertEqual(orisa.commands.last_frame, frame)
                self.assertFalse(nano.active(fight, pool))
                self.assertIsNone(fight._fortify_scale(target))
                self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
                hits = [(hit["+0x84"], hit["+0x78"]["+0x4"]) for hit in owner.client.messages(combat.HIT)]
                self.assertEqual([damage for _, damage in hits], [10] * 6 + [20] * 4 + [34] * 5)
                total = 6 * 10.2 + 4 * 20.4 + 5 * 34
                self.assertAlmostEqual(1000 - pool.total, total, places=4)
                self.assertAlmostEqual(own.total, 30 + total * reaper.REAPING_RATIO, places=4)
                self.assertLess(script.component.entity_var(ULT_CHARGE).value(), 30)
                outcomes[mode] = hits, pool.total, own.total
        if len(outcomes) == 3:
            self.assertEqual(outcomes["posed"], outcomes["idle"])
            self.assertEqual(outcomes["idle"], outcomes["skipped"])

    def test_actual_blossom_resolves_blue_beam_release_then_nano_expiry(self):
        outcomes = []
        for skipped in (False, True):
            run, owner, enemy, caster = self.scene(names=("Reaper", "Enemy", "Ana", "Mercy"))
            mercy = run.players[3]
            self.assertTrue(run.match.switch_hero(mercy, heroes()[mercy_beam.HERO]))
            mercy.team = owner.team
            x, y, z = owner.mover.position
            mercy.mover.position[:] = (x + 4, y, z)
            mercy.mover.body = None
            fight = run.match.combat
            self.cast(run, caster, owner)
            own = fight.vitals[owner.body]
            expiry = round(own.nano_until / combat.TICK)
            while run.frame < expiry - 86:
                run.step({})
            beam = {mercy: (*aim(fight._eye(mercy), (x, y + 1, z)), 2)}
            while run.frame < expiry - 66:
                run.step(beam)
            own.damage(240)
            pool = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
            script = owner.body_script
            script.ult_seconds = mercy.body_script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            mercy.body_script.set_ult_charge(0)
            run.step(beam | {owner: (0, 0, 32)})
            end = run.frame + 200
            for _ in range(26):
                run.step(beam)
            frame = mercy.commands.last_frame
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            hits = [(hit["+0x84"], hit["+0x78"]["+0x4"]) for hit in owner.client.messages(combat.HIT)]
            self.assertEqual([damage for _, damage in hits], [61] * 3 + [51] * 3 + [34] * 9)
            self.assertEqual(mercy.commands.last_frame, frame)
            self.assertAlmostEqual(pool.total, 357.4, places=4)
            self.assertAlmostEqual(own.total, 10 + 642.6 * reaper.REAPING_RATIO, places=4)
            self.assertAlmostEqual(mercy.body_script.component.entity_var(ULT_CHARGE).value(), 30.6, places=4)
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
            self.assertIsNone(reaper.blossom_state(script))
            outcomes.append((hits, pool.total, own.total))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_nano_and_blue_beam_add_to_216_shell_damage(self):
        run, owner, enemy, caster = self.scene(names=("Reaper", "Enemy", "Ana", "Mercy"))
        mercy = run.players[3]
        self.assertTrue(run.match.switch_hero(mercy, heroes()[mercy_beam.HERO]))
        mercy.team = owner.team
        x, y, z = owner.mover.position
        mercy.mover.position[:] = (x + 4, y, z)
        mercy.mover.body = None
        fight = run.match.combat
        self.cast(run, caster, owner)
        own = fight.vitals[owner.body]
        self.assertTrue(nano.active(fight, own))
        own.damage(100)
        pool = combat.Vitals(enemy.body, 600)
        fight.vitals[enemy.body] = pool
        support_aim = aim(fight._eye(mercy), (x, y + 1, z))
        for _ in range(20):
            run.step({mercy: (*support_aim, 2)})
        self.assertEqual(mercy_beam.factor(fight, owner), 1.3)
        x, y, z = enemy.mover.position
        shot_aim = aim(fight._eye(owner), (x, y + 1, z))
        run.step({mercy: (*support_aim, 2), owner: (*shot_aim, 1)})
        self.assertAlmostEqual(pool.total, 384)
        self.assertAlmostEqual(own.total, 225.6, places=4)

    def test_actual_nano_refuses_undercharge_enemy_and_static_cover(self):
        run, owner, enemy, caster = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        component = caster.body_script.component
        cost = component.entity_var(ULT_COST).value()
        own.damage(100)
        self.cast(run, caster, owner, charge=cost - 1)
        self.assertFalse(nano.active(fight, own))
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost - 1)
        self.cast(run, caster, enemy)
        self.assertFalse(nano.active(fight, own))
        self.assertFalse(nano.active(fight, fight.vitals[enemy.body]))
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        x, y, z = owner.mover.position
        wall = x - 2
        run.match.collision_world = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (wall,y,z-8,wall,y+5,z-8,wall,y+5,z+8),
            (wall,y,z-8,wall,y+5,z+8,wall,y,z+8),
        ], name="Nano target cover")
        self.cast(run, caster, owner)
        self.assertEqual(own.total, 150)
        self.assertFalse(nano.active(fight, own))
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)

    def test_actual_nano_survives_caster_replacement_and_wraith_not_recipient_replacement(self):
        run, owner, _, caster = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        self.cast(run, caster, owner)
        self.assertTrue(nano.active(fight, own))
        old_until = own.nano_until
        self.assertTrue(run.match.switch_hero(caster, heroes()[SOLDIER]))
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(own.nano_until, old_until)
        self.assertTrue(nano.active(fight, own))
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        fresh = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
        self.assertIsNot(fresh, own)
        self.assertFalse(nano.active(fight, fresh))

    def test_actual_antigrenade_blocks_nano_heal_and_reaping_but_keeps_boost(self):
        import test_reaper_ana as ana_tests

        run, owner, enemy, caster = self.scene()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[ana.HERO]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        ana_tests.ReaperAnaTests().grenade(run, enemy, owner)
        self.assertEqual(own.total, 90)
        self.cast(run, caster, owner)
        self.assertTrue(nano.active(fight, own))
        self.assertEqual(own.total, 90)
        self.assertIn("anti", fight.heal_modifiers[owner.body])
        target = next(t for t in fight._targets() if t.owner is enemy)
        pool = fight.vitals_of(target)
        before = pool.total
        reaper_tests.ReaperTests().fire(run, owner, enemy)
        self.assertAlmostEqual(pool.total, before - 180)
        self.assertEqual(own.total, 90)

    def test_actual_lethal_shell_clears_recipient_nano_with_kill_attribution(self):
        run, owner, enemy, caster = self.scene()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        own = fight.vitals[owner.body]
        self.cast(run, caster, owner)
        self.assertTrue(nano.active(fight, own))
        own.damage(200)
        reaper_tests.ReaperTests().fire(run, enemy, owner)
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, enemy.body)
        self.assertEqual(own.nano_until, 0)
