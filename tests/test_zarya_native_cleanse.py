"""Original0068 cleanse assertions on the existing sole native Pet/Vitals host."""
import unittest
from test_game_combat import Run, aim, in_world
from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.commands import Command
from ow174.game.script import expr
ZARYA = 0x02E0000000000068

class ZaryaNativeCleanseTests(unittest.TestCase):
    @staticmethod
    def block_end(fight, victim):
        # Current single HealingEffects stores the same absolute tick deadline.
        effect = fight.healing.anti.get(victim.body)
        return effect.end if effect is not None else 0

    def encounter(self, projected, *, close=False, protect=True):
        run = Run(names=("Attacker", "Caster", "Ally"))
        attacker, caster, ally = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        caster.team = ally.team = 1
        attacker.team = 0
        attacker.mover.position[:] = [0, 1, 3.5 if close else 0]
        caster.mover.position[:] = [3 if projected else 0, 1, 5]
        ally.mover.position[:] = [0, 1, 5 if projected else 20]
        victim = ally if projected else caster
        run.step({caster: (-16384 if projected else 0, 0, (16 if projected else 8) if protect else 0)})
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == (0x025C if projected else 0x0244))
        self.assertEqual(instance.state(10 if projected else 2).active, protect)
        return run, fight, attacker, caster, victim

    @staticmethod
    def health(fight, owner):
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target).total

    @staticmethod
    def energy(caster):
        return caster.body_script.component.entity_var(87).value()

    def throw_biotic_grenade(self, run, ana, victim):
        fight = run.match.combat
        x, y, z = victim.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*angles, 16)})
        launched = False
        for _ in range(60):
            for rocket in fight.rockets:
                if rocket.definition == 0x0400000000001426:
                    launched = True
                    self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
            run.step({ana: (*angles, 0)})
            if launched and not fight.rockets:
                break
        self.assertTrue(launched)
        self.assertFalse(fight.rockets)

    def test_either_barrier_cleanses_existing_antiheal_on_cast(self):
        from test_game_combat import in_world

        for projected in (False, True):
            for cast in (False, True):
                with self.subTest(projected=projected, cast=cast):
                    run, fight, enemy, caster, victim = self.encounter(projected, protect=False)
                    ana_guid = 0x02E000000000013B
                    ally = run.match.add_player(4, "Healer", ana_guid, caster.team, False)
                    in_world(run.match, ally)
                    run.players.append(ally)
                    for ana, position in ((enemy, [0, 1, 0]), (ally, [-4, 1, 5])):
                        self.assertTrue(run.match.switch_hero(ana, heroes()[ana_guid]))
                        ana.mover.position[:] = position
                        ana.mover.body = None
                    if projected:
                        caster.mover.position[:] = [6, 1, 5]
                        caster.mover.body = None
                    before = self.health(fight, victim)
                    self.throw_biotic_grenade(run, enemy, victim)
                    vitals = fight.vitals[victim.body]
                    self.assertEqual(vitals.total, before - 50)
                    deadline = self.block_end(fight, victim)
                    self.assertGreater(deadline, fight.tick)
                    buttons = (16 if projected else 8) if cast else 0
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, buttons)})
                    self.assertEqual((fight.barriers.authored_for(caster.body) is not None), cast)
                    self.assertEqual(self.block_end(fight, victim), 0 if cast else deadline)
                    self.assertEqual(vitals.total, before - 50)
                    self.assertEqual(expr.to_float(self.energy(caster)), 0)
                    self.throw_biotic_grenade(run, ally, victim)
                    self.assertLess(fight.tick, deadline)
                    self.assertEqual(vitals.total, before if cast else before - 50)

    def test_queued_projection_cleanse_requires_recipient_still_allied(self):
        for changes_team in (False, True):
            with self.subTest(changes_team=changes_team):
                run, fight, ana, caster, victim = self.encounter(True, protect=False)
                match = run.match
                self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = (0, 1, 0)
                caster.mover.position[:] = (6, 1, 5)
                caster.mover.body = None
                self.throw_biotic_grenade(run, ana, victim)
                vitals = fight.vitals[victim.body]
                deadline = self.block_end(fight, victim)
                activation = run.frame + 10
                self.assertGreater(deadline, activation)
                match.tick = activation
                yaw, pitch = aim(fight._eye(caster), fight._eye(victim))
                caster.take_commands([Command(activation, yaw=yaw, pitch=pitch, buttons=16)])
                self.assertIn((caster.body, True), {(b.parent_body, b.recipient is not None) for b in fight.barriers.scripted.values()})
                if changes_team:
                    victim.team = ana.team
                match.update(activation * combat.TICK, activation, frames=False)
                self.assertEqual((fight.barriers.authored_for(caster.body) is not None), not changes_team)
                self.assertEqual(self.block_end(fight, victim), deadline if changes_team else 0)



    @staticmethod
    def boost_end(fight, victim):
        effect = fight.healing.amp.get(victim.body)
        return effect.end if effect is not None else 0

    def test_antiheal_blocks_passive_shield_recovery_without_catchup(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, fight, ana, _, zarya = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                self.throw_biotic_grenade(run, ana, zarya)
                vitals = fight.vitals[zarya.body]
                deadline = self.block_end(fight, zarya)
                self.assertEqual(deadline - run.frame, 250)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)

                if not skipped:
                    while run.frame < deadline - 1:
                        run.step({})
                        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)
                    self.assertEqual(self.block_end(fight, zarya), deadline)
                run.frame = deadline
                run.match.update(deadline * combat.TICK, deadline)
                self.assertEqual(self.block_end(fight, zarya), 0)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 150.48)
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 160.08)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_friendly_grenade_boosts_passive_shields_only_before_expiry(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, fight, attacker, zarya, _ = self.encounter(False, protect=False)
                ana = run.players[2]
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [4, 1, 5]
                ana.mover.body = None
                attacker.body_script.component.set(87, 100)
                for _ in range(61):
                    run.step({attacker: (0, 0, 1)})
                damaged_tick = run.frame
                vitals = fight.vitals[zarya.body]
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 36.8, places=4)
                self.throw_biotic_grenade(run, ana, zarya)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 136.8, places=4)
                expiry = self.boost_end(fight, zarya)
                self.assertEqual(expiry, run.frame + 250)
                ready = damaged_tick + 188
                if not skipped:
                    while run.frame < ready - 1:
                        run.step({})
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 136.8, places=4)
                    run.step({})
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 137.16, places=4)
                    while run.frame < expiry - 1:
                        run.step({})
                run.frame = expiry
                run.match.update(expiry * combat.TICK, expiry)
                expected = 136.8 + (expiry - ready - 0.5) * 0.72 + 0.48
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], expected, places=4)
                self.assertEqual(self.boost_end(fight, zarya), 0)
                run.step({})
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], expected + 0.48, places=4)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)


class NativePetApplicationTests(unittest.TestCase):
    def encounter(self, projected):
        source = ZaryaNativeCleanseTests()
        run, fight, enemy, caster, victim = source.encounter(projected, protect=False)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E000000000013B]))
        enemy.mover.position[:] = [0, 1, 0]
        if projected:
            caster.mover.position[:] = [6, 1, 5]
            caster.mover.body = None
        source.throw_biotic_grenade(run, enemy, victim)
        self.assertEqual(fight.healing.factor(next(t for t in fight._targets() if t.owner is victim)), 0)
        return run, fight, caster, victim

    def test_both_native_pets_cleanse_once_at_actual_application(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, caster, victim = self.encounter(projected)
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                pet = fight.barriers.authored_for(caster.body)
                self.assertIsNotNone(pet)
                self.assertEqual(pet.vitals.total, 200)
                self.assertTrue(pet.cleanse_applied)
                self.assertEqual(fight.healing.factor(next(t for t in fight._targets() if t.owner is victim)), 1)
                # Advancing the same application never moves its original cleanse boundary.
                self.assertNotIn(victim.body, fight.healing.anti)
                for _ in range(4):
                    run.step({})
                self.assertNotIn(victim.body, fight.healing.anti)

    def test_future_pair_defers_cleanse_and_checks_recipient_at_application(self):
        for projected, changes_team in ((False, False), (True, False), (True, True)):
            with self.subTest(projected=projected, changes_team=changes_team):
                run, fight, caster, victim = self.encounter(projected)
                match = run.match
                application = match.tick + 10
                match.tick = application
                yaw, pitch = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                frame = caster.body_script.frame + 1
                caster.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=16 if projected else 8)])
                pet = next(b for b in fight.barriers.scripted.values() if b.parent is caster)
                self.assertIsNotNone(pet)
                self.assertEqual(pet.application_tick, application)
                self.assertNotEqual(pet.source.frame, pet.application_tick)
                self.assertFalse(pet.cleanse_applied)
                self.assertEqual(fight.healing.factor(next(t for t in fight._targets() if t.owner is victim)), 0)
                def negative_status():
                    if not fight.healing.available():
                        return fight.healing.anti.get(victim.body)
                    return next((instance for item in fight.healing.applied.values()
                                 if item.target == victim.body for instance in item.component.instances.values()
                                 if instance.graph.index == 0x0C72 and not instance.stopped), None)
                original_anti = negative_status()
                self.assertIsNotNone(original_anti)
                match.update((application - 1) * combat.TICK, application - 1, frames=False)
                self.assertIs(negative_status(), original_anti)
                self.assertFalse(pet.cleanse_applied)
                if changes_team:
                    victim.team = 1 - caster.team
                match.update(application * combat.TICK, application, frames=False)
                self.assertEqual(fight.healing.factor(next(t for t in fight._targets() if t.owner is victim)),
                                 0 if changes_team else 1)
                self.assertEqual(pet.cleanse_applied, not changes_team)
