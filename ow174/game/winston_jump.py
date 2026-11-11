"""Host Winston's 010A Jump Pack using authored cast and impact states."""

import math

from ow174.game import defenses
from ow174.game.mover import FELL, JUMPED, REAL_JUMP
from ow174.game.script import expr, nodes

GRAPH = 0x010A


class JumpPack:
    def __init__(self, fight):
        self.fight = fight
        self.seen = {}
        self.flights = {}

    def retire(self, body):
        self.cancel(body)
        self.seen.pop(body, None)

    def cancel(self, body):
        flight = self.flights.pop(body, None)
        if flight is not None:
            instance, _ = flight
            state = instance.states.get(2)
            if state is not None and state.active:
                state.end(False)
                instance.component.run_now()

    def command(self, player, command):
        instance = next((i for i in player.body_script.component.instances.values()
                         if i.graph.index == GRAPH), None)
        if instance is None:
            return
        flight = self.flights.get(player.body)
        if flight is not None and command.frame > flight[1] and player.mover.landed:
            self.impact(player, flight[0], command.frame, landing=True)
            self.cancel(player.body)
        ability = instance.states.get(19)
        if not isinstance(ability, nodes.Ability) or ability.cur <= 0:
            return
        token = (instance.id, ability.last)
        if self.seen.get(player.body) == token:
            return
        self.seen[player.body] = token
        self.cancel(player.body)
        self.launch(player, instance, command)
        self.flights[player.body] = (instance, command.frame)

    def launch(self, player, instance, command):
        state = instance.state(2)
        if not state.active:
            state.begin()
            instance.component.run_now()
        # Interpret authored node85's angle bounds as elevation above ground.
        config = instance.graph.node(85).field("m_value")["m_configVars"][0]
        lower = expr.to_float(instance.evaluate(config["m_E26E5E09"]))
        upper = expr.to_float(instance.evaluate(config["m_5023B9B0"]))
        speed = expr.to_float(instance.find_var(0, 1262).value())
        yaw, pitch = command.aim()
        elevation = math.radians(max(lower, min(upper, -pitch * 360 / 65536)))
        heading = yaw * 2 * math.pi / 65536
        horizontal = speed * math.cos(elevation)
        mover = player.mover
        mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                          math.cos(heading) * horizontal)
        mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
        mover.air_ticks = 0
        mover.landed = False
        self.impact(player, instance, command.frame, landing=False)

    def impact(self, player, instance, frame, *, landing):
        from ow174.game.combat import EPSILON, _sub, hit_def, ray_world, segment_distance

        search = instance.state(4 if landing else 6)
        state = instance.state(3 if landing else 7)
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        # Landing's maximum uses the weapon-skill value rather than the st3
        # unit amount. The host supplies the capsule-distance falloff.
        amount = expr.to_float(instance.find_var(0, 476).value()) if landing else definition.amount
        if radius <= 0 or amount <= 0:
            return
        horizontal, vertical = (
            expr.to_float(state.evaluate(state.node.field(f"m_modifyHealth.{key}"), watch=False))
            for key in ("m_E33EFEE1", "m_583C208B")
        )
        centre = tuple(player.mover.position)
        origin = (centre[0], centre[1] + .1, centre[2])
        shields = {}
        victims = []
        for target in self.fight._enemies(player):
            distance = max(0.0, segment_distance(centre, *target.segment()) - target.radius)
            if distance >= radius:
                continue
            scale = 1.0 - distance / radius if landing else 1.0
            point = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(point, origin)
            length = math.hypot(*delta)
            if length > EPSILON:
                ray = tuple(axis / length for axis in delta)
                edge = max(0.0, length - target.radius)
                if (ray_world(self.fight._collision(), origin, ray, edge) is not None
                        or self.fight.walls.cast(origin, ray, edge) is not None):
                    continue
                contact = self.fight._barrier_contact(player, origin, ray, edge)
                if contact is not None:
                    previous = shields.get(contact[1].body)
                    if previous is None or scale > previous[1]:
                        shields[contact[1].body] = (contact, scale)
                    continue
            victims.append((target, scale))
        for contact, scale in shields.values():
            self.fight._damage_barrier(contact, amount * scale)
        for target, scale in victims:
            affected = self.fight._damage(player, target, amount * scale, False, definition.loadout, frame,
                                          tags=definition.tags)
            if affected and not defenses.control_immune(target.owner):
                self.fight.radial_knockback(target, centre, horizontal * scale, vertical * scale, frame)
