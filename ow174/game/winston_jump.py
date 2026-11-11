"""Host launch and contact handling for Winston's authored 010A Jump Pack."""

import math

from ow174.game import assassin
from ow174.game.mover import FELL, JUMPED, REAL_JUMP
from ow174.game.script import expr, nodes

HERO = 0x02E0000000000009
GRAPH = 0x010A


class JumpPack:
    """Consume each accepted cast once and resolve its first actual landing."""

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.flights = {}

    def retire(self, body):
        self.seen.pop(body, None)
        self.flights.pop(body, None)

    def cancel(self, body):
        flight = self.flights.pop(body, None)
        if flight is None:
            return
        instance, _ = flight
        state = instance.states.get(2)
        if state is not None and state.active:
            state.end(False)
            instance.component.run_now()

    def command(self, player, command, script):
        if script.hero != HERO or self.combat.match.ended:
            return
        flight = self.flights.get(player.body)
        if flight is not None and command.frame > flight[1] and player.mover.landed:
            self.impact(player, flight[0], command.frame, landing=True)
            self.cancel(player.body)
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        cast = instance.states.get(26) if instance is not None else None
        if not isinstance(cast, nodes.Wait) or cast.counter == 0:
            return
        identity = instance.id, cast.counter, cast.start
        if self.seen.get(player.body) == identity:
            return
        self.seen[player.body] = identity
        self.cancel(player.body)
        self.launch(player, instance, command)
        self.flights[player.body] = instance, command.frame

    def launch(self, player, instance, command):
        state = instance.state(2)
        if not state.active:
            state.begin()
            instance.component.run_now()
        # 010A/node85 bounds the launch elevation. Node data supplies speed via var1262.
        config = instance.graph.node(85).field("m_value")["m_configVars"][0]
        lower = expr.to_float(instance.evaluate(config["m_E26E5E09"]))
        upper = expr.to_float(instance.evaluate(config["m_5023B9B0"]))
        speed = expr.to_float(instance.find_var(0, 1262).value())
        yaw, pitch = command.aim()
        elevation = math.radians(max(lower, min(upper, -pitch * 360 / 65536)))
        heading = yaw * math.tau / 65536
        horizontal = speed * math.cos(elevation)
        mover = player.mover
        mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                          math.cos(heading) * horizontal)
        mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
        mover.driven = False
        mover.air_ticks = 0
        mover.landed = False
        self.impact(player, instance, command.frame, landing=False)

    def impact(self, player, instance, frame, *, landing):
        from ow174.game.combat import segment_distance

        search = instance.state(4 if landing else 6)
        state = instance.state(3 if landing else 7)
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        modify = state.node.field("m_modifyHealth")
        amount = (expr.to_float(instance.find_var(0, 476).value()) if landing
                  else expr.to_float(state.evaluate(modify.get("m_amount"), watch=False)))
        if radius <= 0 or amount <= 0:
            return
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        horizontal = expr.to_float(state.evaluate(modify.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(state.evaluate(modify.get("m_583C208B"), watch=False))
        centre = tuple(player.mover.position)
        origin = (centre[0], centre[1] + 0.1, centre[2])
        fight = self.combat
        for target in fight._enemies(player):
            a, b = target.segment()
            distance = max(0.0, segment_distance(centre, a, b) - target.radius)
            if distance > radius or not fight.combat_visible(origin, a, b, target.radius, exclude=player):
                continue
            scale = max(0.0, 1 - distance / radius) if landing else 1.0
            if (fight._damage(player, target, amount * scale, False, loadout, frame)
                    and not fight.vitals_of(target).dead):
                fight.radial_impulse(target, centre, horizontal, vertical, scale=scale)
                assassin.interrupt_pin(fight, target)
