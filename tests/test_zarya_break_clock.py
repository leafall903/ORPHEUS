"""Original0068 projected break/world cooldown assertion on the same native Pet."""
import unittest
import math
import test_ashe_pve_npcs as fixtures
from test_game_combat import FakeClient, Run, aim
from ow174.game import barriers, combat, npc
from ow174.game.commands import Command
from ow174.game.content import heroes

ZARYA = 0x02E0000000000068

class NpcClient(FakeClient):
    """Delivery-model callbacks; native wire/presentation is a separate acceptance gate."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.death_updates.append((entity, event))

    def bot_stream(self, entity):
        return None  # No observer projectile transport in this damage fixture.


class ZaryaBreakClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AshePveNpcTests.setUpClass()
        cls.catalog = fixtures.AshePveNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass()

    @staticmethod
    def protected(service, caster, recipient):
        actor = service.authored_for(caster.body)
        return (actor is not None and (actor.recipient or actor.parent) is recipient
                and actor.vitals.total > 0)

    @staticmethod
    def projection_pitch(fight, caster, bot):
        # Current native025C acquisition also intersects the actual body capsule;
        # the older fixture's horizontal eye ray misses the actual shorter NPC.
        target = next(t for t in fight._targets() if t.owner is bot)
        a, b = target.segment()
        return aim(fight._eye(caster), tuple((lo + hi) / 2 for lo, hi in zip(a, b)))[1]

    def ana_projection_arena(self, definition, *, protected, target_z=5):
        run = Run(names=("Zarya", "Ana"))
        caster, ana = run.players
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for player, hero, point in (
            (caster, ZARYA, [4, 1, target_z]), (ana, 0x02E000000000013B, [0, 1, 0]),
        ):
            player.client = NpcClient(player)
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = point
            player.mover.body = None
        caster.team, ana.team = 0, 1
        bot = npc.spawn(match, definition, (0, 1, target_z), team=caster.team)
        run.step({caster: (-16384, self.projection_pitch(fight, caster, bot), 16 if protected else 0)})
        return run, fight, caster, ana, bot


    def test_projection_cooldown_uses_break_tick_after_a_future_command(self):
        definition = next(iter(self.catalog.values()))
        run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
        service = fight.barriers
        self.assertTrue(self.protected(service, caster, bot))
        pool = service.authored_for(caster.body).vitals
        pool.damage(pool.total - 10)
        expiry = service.expiries[caster.body, True]
        pet = expiry.duration.instance.state(10)
        generation = pet.counter
        before = fight.vitals[bot.entity].total
        launch = run.frame + 65
        run.match.update(launch * combat.TICK, launch, frames=False)
        yaw, pitch = aim(fight._eye(ana), (0, 2, 5))
        ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
        caster.take_commands([Command(expiry.frame + 3)])
        self.assertFalse(pet.active)
        for at in range(launch + 1, expiry.tick):
            run.match.update(at * combat.TICK, at, frames=False)
            if not pool.total:
                break
        self.assertEqual(pool.total, 0)
        self.assertEqual(fight.vitals[bot.entity].total, before)
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 2)
        ready = fight.tick + 500
        self.assertLess(ready, expiry.tick + 500)

        run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        caster.take_commands([Command(ready - 2, yaw=-16384, pitch=self.projection_pitch(fight, caster, bot), buttons=16), Command(ready - 1)])
        self.assertFalse(pet.active)
        self.assertEqual(pet.counter, generation)
        run.match.update(ready * combat.TICK, ready, frames=False)
        caster.take_commands([Command(ready, yaw=-16384, pitch=self.projection_pitch(fight, caster, bot), buttons=16)])
        self.assertTrue(pet.active)
        self.assertEqual(pet.counter, generation + 1)
        self.assertTrue(self.protected(service, caster, bot))



    def test_silent_barrier_break_starts_energy_and_cooldown_at_actual_impact(self):
        cases = [(definition, True) for definition in self.catalog.values()]
        cases.append((next(iter(self.catalog.values())), False))
        for definition, projected in cases:
            with self.subTest(npc=definition.name, projected=projected):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=projected)
                recipient = bot if projected else caster
                cast = {caster: (-16384, self.projection_pitch(fight, caster, bot) if projected else 0, 16 if projected else 8)}
                if not projected:
                    caster.mover.position[:] = (0, 1, 5)
                    caster.mover.body = None
                    bot.position = (10, 1, 5)
                    run.step(cast)
                self.assertTrue(self.protected(fight.barriers, caster, recipient))
                service = fight.barriers
                pool = service.authored_for(caster.body).vitals
                pool.damage(pool.total - 10)
                script = caster.body_script
                instance = next(i for i in script.component.instances.values()
                                if i.graph.index == (0x025C if projected else 0x0244))
                pet = instance.state(10 if projected else 2)
                generation = pet.counter
                start = run.frame
                launch = start + 65
                target = next(t for t in fight._targets() if t.owner is recipient)
                vitals = fight.vitals_of(target)
                before = vitals.total
                yaw, pitch = aim(fight._eye(ana), (0, 2, 5))
                run.match.update(launch * combat.TICK, launch, frames=False)
                ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
                self.assertEqual(len(fight.rockets), 1)
                for hit in range(launch + 1, launch + 15):
                    run.match.update(hit * combat.TICK, hit, frames=False)
                    if not fight.rockets:
                        break
                self.assertEqual(pool.total, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(self.protected(service, caster, recipient))
                self.assertEqual(script.frame, start)
                run.match.update((hit + 1) * combat.TICK, hit + 1, frames=False)
                self.assertFalse(pet.active)
                self.assertEqual(script.frame, hit)
                cooldown = 8 if projected else 10
                self.assertAlmostEqual(instance.vars[61].value(), cooldown, delta=0.025)
                energy = script.component.entity_var(87)
                self.assertEqual(energy.value(), 2)
                run.match.update((hit + 1) * combat.TICK, hit + 1, frames=False)
                self.assertEqual(energy.value(), 2)
                self.assertNotIn((caster.body, projected), service.expiries)

                run.frame = hit + 50
                run.step({})
                self.assertEqual(energy.value(), 2)
                run.frame = hit + math.ceil(cooldown / combat.TICK) - 3
                run.step(cast)
                self.assertFalse(pet.active)
                self.assertEqual(pet.counter, generation)
                self.assertEqual(energy.value(), 0)
                run.step({})
                run.step({})
                run.step(cast)
                self.assertEqual(pet.counter, generation + 1)
                self.assertTrue(self.protected(service, caster, recipient))

