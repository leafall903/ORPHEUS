"""Original0068 projected break/world cooldown assertion on the same native Pet."""
import unittest
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
        return actor is not None and actor.recipient is recipient and actor.vitals.total > 0

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
