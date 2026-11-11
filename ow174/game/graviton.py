"""Scoped static host for the 025F projectile and its 0260/0261 area."""

from dataclasses import dataclass

from ow174.game import bots, defenses, heavy_charge, zarya_barrier
from ow174.game.mover import Mover, mover_data

DEFINITION = 0x04000000000008CE
LOADOUT = 0x0B9000000000005B
RADIUS, DURATION, DAMAGE_RATE = 6.0, 4.0, 5.0
PULL_SPEED = 3.0  # scoped interpretation of the 0261/node15 scalar


def pull_goal(position, mover, eye):
    x, y, z = position
    feet = y - eye
    return (x, feet if mover.airborne else max(feet, mover.position[1]), z)


@dataclass
class Field:
    shooter: object
    body: int
    team: int
    position: tuple
    end_tick: int
    last_tick: int


class Gravitons:
    def __init__(self, fight):
        self.fight = fight
        self.fields: list[Field] = []
        self.pulled: set[int] = set()
        self.npc_movers: dict[int, Mover] = {}

    def landed(self, rocket, point):
        from ow174.game.combat import TICK

        tick = self.fight.tick
        self.fields.append(Field(rocket.shooter, rocket.shooter.body, rocket.team, point,
                                 tick + round(DURATION / TICK), tick))

    def retire(self, body):
        self.fields[:] = [field for field in self.fields if field.body != body]
        self.pulled.discard(body)
        self.npc_movers.pop(body, None)

    def pull_npc(self, bot, tick, elapsed, position, eye):
        fight = self.fight
        if (bot.attack_action == "charge"
                and (bot.entity not in self.pulled or bot.entity in fight.charge_contacts)):
            heavy_charge.finish(fight, bot, heavy_charge.instance_of(bot))
        fight.knockbacks.pop(bot.entity, None)
        start = tuple(bot.position)
        mover = self.npc_movers.get(bot.entity)
        if mover is None:
            mover = self.npc_movers[bot.entity] = Mover(
                start, world=fight._collision(), data=mover_data(bot.body))
        else:
            mover.use(world=fight._collision())
            if tuple(mover.position) != start:
                mover.position[:] = start
                mover.body = None
        mover._move_to(elapsed, pull_goal(position, mover, eye), PULL_SPEED)
        after, normals, _ = fight.walls.constrain(start, mover.position, mover.values.stand_radius,
                                              mover.values.stand_height)
        if normals:
            mover.position[:] = after
            mover.body = None
            mover.velocity = tuple((new - old) / elapsed for new, old in zip(after, start, strict=True))
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.last_tick = bot.moved_tick = tick
        bot.pace, bot.held = 0.0, (0, 0)

    def update(self, tick):
        from ow174.game.combat import TICK, capsule_visible, segment_distance

        if not self.fields and not self.pulled:
            return
        fight = self.fight
        moved = set()
        advanced = False
        for field in tuple(self.fields):
            if field.shooter.gone or field.shooter.body != field.body:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            elapsed = (until - field.last_tick) * TICK
            if elapsed > 0:
                advanced = True
                field.last_tick = until
                for target in fight._enemies(field.shooter, team=field.team):
                    a, b = target.segment()
                    if (segment_distance(field.position, a, b) > RADIUS + target.radius
                            or not capsule_visible(fight._collision(), field.position, a, b, target.radius)
                            or fight.walls.blocked(field.position, (a[0], (a[1] + b[1]) / 2, a[2]))):
                        continue
                    protected = zarya_barrier.active(fight, target.entity) is not None
                    fight._damage(field.shooter, target, DAMAGE_RATE * elapsed, False, LOADOUT, tick,
                                  credit_charge=False, bypass_armour=True)
                    if (tick >= field.end_tick or protected or target.entity in moved
                            or fight.vitals_of(target).dead
                            or defenses.control_immune(target.owner) or fight.pinned(target.entity)
                            or fight.hooks.pulling(target.entity)):
                        continue
                    moved.add(target.entity)
                    owner = target.owner
                    if isinstance(owner, bots.Bot):
                        self.pull_npc(owner, tick, elapsed, field.position, target.eye)
                        position = owner.position
                    else:
                        fight.charge_blocked(owner, impact=False)
                        start = tuple(owner.mover.position)
                        destination = pull_goal(field.position, owner.mover, target.eye)
                        owner.mover._move_to(elapsed, destination, PULL_SPEED)
                        fight.walls.move_player(owner, start)
                        position = tuple(owner.mover.position)
                    history = fight.history.get(target.entity)
                    if history:
                        frame, _, height, eye = history[-1]
                        history[-1] = frame, position, height, eye
            if tick >= field.end_tick:
                self.fields.remove(field)
        if self.fields and not advanced:
            return
        for player in fight.match.players:
            if player.body in self.pulled and player.body not in moved:
                player.mover.velocity = (0.0, player.mover.velocity[1], 0.0)
        for entity in self.npc_movers.keys() - moved:
            mover = self.npc_movers.pop(entity)
            bot = next((actor for actor in fight.match.bots if actor.entity == entity), None)
            if bot is not None and entity not in fight.knockbacks and bot.velocity == mover.velocity:
                bot.velocity = (0.0, 0.0, 0.0)
                if mover.airborne and not bot.dead:
                    mover.velocity = bot.velocity
                    fight.knockbacks[entity] = mover
        self.pulled = moved
