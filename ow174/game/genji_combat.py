"""Genji's authored fan, Swift Strike movement contact and sword swings."""

import math
from dataclasses import dataclass, field

from ow174.game import reinhardt_barrier, shot_guards, stats
from ow174.game.script import expr, runtime

HERO = shot_guards.GENJI
WEAPON, STRIKE, BLADE = 0x0304, 0x01DB, 0x0317
SHURIKEN = 0x04000000000007CA
BLADE_LOADOUT = 0x0B900000000000D8
ASSIST_TICKS = 313  # reconstructed five-second contribution window


def fan(state, direction):
    """0304's native D808F0 fan direction order."""
    cfg = state.node.field("m_5EC1EB38") or {}
    if (state.instance.graph.index != WEAPON or cfg.get("$") != "STU_1145C42E"
            or expr.to_int(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)) != 3):
        return (direction,)
    half = math.radians(expr.to_float(state.evaluate(cfg.get("m_spread"), watch=False))) / 2
    rotation = math.radians(expr.to_float(state.evaluate(cfg.get("m_A2A9890A"), watch=False)))
    x, y, z = direction
    horizontal = math.hypot(x, z)
    if horizontal > 1e-9:
        right = z / horizontal, 0., -x / horizontal
        up = -y * x / horizontal, horizontal, -y * z / horizontal
    else:
        right, up = (-1., 0., 0.), (0., 0., math.copysign(1., y))
    tangent = tuple(u * math.cos(rotation) - r * math.sin(rotation) for u, r in zip(up, right, strict=True))
    return tuple(tuple(d * math.cos(offset) + t * math.sin(offset)
                       for d, t in zip(direction, tangent, strict=True))
                 for offset in (-half, 0., half))


def raycast(world, request):
    cfg = request.filter_config or {}
    resource = cfg.get("m_C1903771") or {}
    if (world.hero != HERO or world.match is None or world.match.collision_world is None
            or resource.get("m_C1903771") != "0x0690000000000029"
            or request.ignored_entities != (world.body,)):
        return None
    hit = world.match.combat._line_collision().cast_ray(
        tuple(request.origin), tuple(request.direction), request.length,
    )
    if hit is None:
        return runtime.RaycastResult(False, None, None, None)
    return runtime.RaycastResult(True, None, expr.Vec3(*hit.point), expr.Vec3(*hit.normal))


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance is None
            or instance.graph.index != STRIKE):
        return NotImplemented
    cls = cfg.get("$")
    if cls in ("STU_21A13145", "STU_2200A66D") and expr.to_entity(
        instance.evaluate(cfg.get("m_entity")),
    ) == world.body:
        from ow174.game.combat import aim

        actor = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
        if actor is not None:
            point = (world.match.combat._eye(actor) if cls == "STU_21A13145"
                     else aim(actor.mover.yaw, actor.mover.pitch))
            return expr.Vec3(*point)
    if cls != "STU_AC829876":
        return NotImplemented
    signature = {key: value for key, value in (cfg.get("m_filter") or {}).items()
                 if value not in (0, None)}
    if signature != {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    return entity != world.body and any(target.entity == entity and not target.is_device
                                        for target in world.match.combat._targets())


def _instance(player, graph):
    script = getattr(player, "body_script", None)
    if getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == graph), None)


@dataclass
class Dash:
    activation: tuple
    centre: tuple
    victims: set = field(default_factory=set)


@dataclass
class Swing:
    activation: tuple
    victims: set = field(default_factory=set)


class Combat:
    def __init__(self, fight):
        self.fight = fight
        self.dashes = {}
        self.swings = {}
        self.assists = {}

    def retire(self, body):
        self.dashes.pop(body, None)
        for track in (66, 67):
            self.swings.pop((body, track), None)
        self.assists.pop(body, None)
        for contributors in self.assists.values():
            contributors.pop(body, None)

    def interrupt(self, player, body):
        self.dashes.pop(body, None)
        instance = _instance(player, STRIKE)
        movement = instance.states.get(1) if instance is not None else None
        if movement is not None and movement.active:
            movement.request(runtime.ABORT)
            instance.component.run_now()

    def damage(self, shooter, target, source, tick, killed):
        fight = self.fight
        if (source == getattr(shooter, "body", None) and _instance(shooter, STRIKE) is not None
                and shooter is not target.owner and not target.is_device
                and (fight.match.game_map.free_for_all or shooter.team != target.team)):
            self.assists.setdefault(target.entity, {})[source] = (shooter, tick)
        if not killed:
            return
        for body, (actor, last) in self.assists.pop(target.entity, {}).items():
            if (actor.body != body or actor.gone or not 0 <= tick - last <= ASSIST_TICKS
                    or actor not in fight.match.players):
                continue
            instance = _instance(actor, STRIKE)
            if instance is None:
                continue
            if actor is shooter and body == source:
                message, parameter = 0x0240000000000093, 0x15
            else:
                message, parameter = 0x024000000000169F, 0x45
                actor.stats.add(stats.ELIMINATIONS)
                fight._kill_notice(actor, target.owner, actor.body)
            runtime.send_instance_message(instance, message, {parameter: expr.Entity(target.entity)}, body)
            instance.component.run_now()

    @staticmethod
    def definition(state):
        from ow174.game.combat import hit_def

        return hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})

    def apply(self, player, target, definition, centre, command, *, parry=False):
        fight = self.fight
        if parry and shot_guards.parries_melee(fight, target, centre, definition.tags):
            return
        amount = definition.amount * fight.outgoing_scale(player.body)
        if not reinhardt_barrier.protect_body(fight, target, amount, attacker=player.body):
            fight._damage(player, target, amount, False, definition.loadout, command.frame,
                          tags=definition.tags, source_scaled=True)

    def strike(self, player, command):
        from ow174.game.combat import EPSILON, _sub, capsule_visible, ray_capsule

        fight = self.fight
        instance = _instance(player, STRIKE)
        move = instance.states.get(1) if instance is not None else None
        if move is None:
            return
        x, y, z = player.mover.position
        centre = x, y + 1, z
        activation = instance.id, move.counter
        dash = self.dashes.get(player.body)
        if dash is None or dash.activation != activation:
            if move.move_request() is None:
                return
            dash = self.dashes[player.body] = Dash(activation, centre)
        delta = _sub(centre, dash.centre)
        reach = math.hypot(*delta)
        if not move.active and reach <= EPSILON:
            self.dashes.pop(player.body, None)
            return
        direction = tuple(axis / reach for axis in delta) if reach > EPSILON else (0., 0., 1.)
        track = instance.state(33)
        radius = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
        hit = self.definition(instance.state(3))
        for target in fight._enemies(player):
            if target.entity in dash.victims:
                continue
            a, b = target.segment()
            distance = ray_capsule(dash.centre, direction, a, b, target.radius + radius)
            if (distance is not None and distance <= reach
                    and capsule_visible(fight._line_collision(), centre, a, b, target.radius)):
                dash.victims.add(target.entity)
                self.apply(player, target, hit, centre, command)
        if move.active:
            dash.centre = centre
        else:
            self.dashes.pop(player.body, None)

    def blade(self, player, command):
        from ow174.game.combat import EPSILON, _dot, _sub, aim, capsule_visible, segment_distance

        instance = _instance(player, WEAPON)
        if instance is None:
            return
        fight = self.fight
        x, y, z = player.mover.position
        centre = x, y + 1, z
        direction = aim(*command.aim())
        for track_index, damage in ((66, 21), (67, 22)):
            track = instance.states.get(track_index)
            key = player.body, track_index
            if track is None or not track.active:
                self.swings.pop(key, None)
                continue
            activation = instance.id, track.counter, track.start
            swing = self.swings.get(key)
            if swing is None or swing.activation != activation:
                swing = self.swings[key] = Swing(activation)
            radius = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
            angle = expr.to_float(track.evaluate(track.node.field("m_A30B383E"), watch=False))
            threshold = math.cos(math.radians(angle))
            hit = self.definition(instance.state(damage))
            for target in fight._enemies(player):
                if target.entity in swing.victims:
                    continue
                a, b = target.segment()
                delta = _sub(tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True)), centre)
                if (segment_distance(centre, a, b) - target.radius > radius
                        or _dot(direction, delta) + EPSILON < threshold * math.hypot(*delta)
                        or not capsule_visible(fight._line_collision(), centre, a, b, target.radius)):
                    continue
                swing.victims.add(target.entity)
                self.apply(player, target, hit, centre, command, parry=True)
