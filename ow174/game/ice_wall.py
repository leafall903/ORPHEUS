"""Selected 104319 Ice Wall placement and pillar lifecycle for static combat."""
import math
from copy import copy
from functools import lru_cache

from ow174.game import actors, contacts
from ow174.game.collision import MOVER
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, play, runtime

MEI = 0x02E00000000000DD
WALL_GRAPH = 0x0622
PILLAR_GRAPH = 0x0636
PILLAR = 0x0400000000000932
_SAMPLE_OFFSETS = {36: 0, 35: -1, 34: -2, 33: 1, 32: 2}
_CREATE_NODES = (133, 136, 139, 142, 145)
_PLAY_NODES = (66, 137, 138, 143, 144)


def pillar_actors(fight):
    return tuple(actor for actor in fight.deployables.values()
                 if actor.definition == PILLAR and not actor.owner.gone)


def movement_world(fight, tick):
    base = fight.match.collision_world
    pillars = tuple((actor.position, actor.radius, actor.height) for actor in pillar_actors(fight)
                    if actor.created_tick <= tick < actor.expires_tick)
    return _with_pillars(base, pillars) if base is not None and pillars else base


@lru_cache(maxsize=8)
def _with_pillars(base, pillars):
    """Derived immutable movement scene; map rays still use the original World."""
    world = copy(base)
    shapes = list(base.engine.shapes)
    order = max((shape.order for shape in shapes), default=-1) + 1
    for (x, y, z), radius, height in pillars:
        a, b = (x, y + radius, z), (x, y + max(radius, height - radius), z)
        shapes.append(contacts.StaticShape(contacts.Capsule(a, b, radius, MOVER), order=order))
        order += 1
    world.engine = contacts.StaticWorld(shapes)
    return world


def route_stride(bot, stride, pillars):
    """Clip a patrol stride at the first pillar, using standing capsule dimensions."""
    mover = mover_data(bot.body)
    radius = mover.stand_radius
    x, y, z = bot.position
    dx, dz = bot.direction
    lower, upper = y + radius, y + max(radius, mover.stand_height - radius)
    for actor in pillars:
        px, py, pz = actor.position
        top = py + max(actor.radius, actor.height - actor.radius)
        gap = max(0., py + actor.radius - upper, lower - top)
        combined = radius + actor.radius
        if gap >= combined:
            continue
        ox, oz = px - x, pz - z
        projection = ox * dx + oz * dz
        if projection <= 0.:
            continue  # The route may move away from an overlapping pillar.
        separation = ox * dz - oz * dx
        room = combined * combined - gap * gap - separation * separation
        if room > 0.:
            entry = projection - math.sqrt(room)
            if entry <= stride:
                stride = max(0., entry - 0.001)
    return stride


def owner(fight, instance):
    if instance.graph.index == WALL_GRAPH and instance.component.world.hero == MEI:
        return next((p for p in fight.match.players
                     if p.has_body and p.body == instance.component.entity), None)
    return None


def value(instance, variable):
    slot = instance.find_var(runtime.INSTANCE_SCOPE, variable, create=False)
    return slot.value() if slot is not None else None


def write(state, field, result):
    state.instance.write(expr.lvalue(state.node.field(field)), result)


def native(fight, instance, cfg):
    from ow174.game.combat import aim

    player = owner(fight, instance)
    if player is None or cfg["$"] not in ("STU_66F3B89E", "STU_021D01CD", "STU_E72F6348"):
        return NotImplemented
    entity = instance.evaluate(cfg.get("m_entity"))
    if cfg["$"] == "STU_E72F6348":
        actor = fight.deployables.get(entity.id) if isinstance(entity, expr.Entity) else None
        return actor is not None and actor.definition == PILLAR and actor.source_body == player.body
    if entity != expr.Entity(player.body):
        return NotImplemented
    x, _, z = aim(player.mover.yaw, 0)
    return expr.Vec3(z, 0., -x) if cfg["$"] == "STU_66F3B89E" else expr.Vec3(x, 0., z)


def confirm(fight, state):
    from ow174.game.combat import aim

    player = owner(fight, state.instance)
    if player is None or state.node.pos != 3:
        return NotImplemented
    world = fight.match.collision_world
    if world is None or fight.is_dead(player) or fight.is_stunned(player):
        return False
    limit = state.evaluate(state.node.field("m_6386E7B4"), watch=False)
    slope = state.evaluate(state.node.field("m_2ADABA5F"), watch=False)
    hit = world.cast_ray(fight._eye(player), aim(player.mover.yaw, player.mover.pitch), limit)
    valid = hit is not None and hit.normal[1] >= math.cos(slope)
    write(state, "m_E21062B4", valid)
    if valid:
        write(state, "m_30710B76", expr.Vec3(*hit.point))
        write(state, "m_2339572B", expr.Vec3(*hit.normal))
        write(state, "m_1549F0F9", expr.Entity(0))
    return valid


def probe(fight, state):
    instance = state.instance
    player = owner(fight, instance)
    pos = state.node.pos
    if player is None or (pos != 30 and pos not in _SAMPLE_OFFSETS):
        return False
    if pos == 30:
        # Reconstructed horizontal reach; the individual vertical casts below reject missing support.
        span = state.evaluate(state.node.field("m_8CAE4EBD"), watch=False)
        write(state, "m_182435FB", span)
        write(state, "m_2B8E9543", span)
    else:
        anchor, direction = value(instance, 2478), value(instance, 3192)
        if not isinstance(anchor, expr.Vec3) or not isinstance(direction, expr.Vec3):
            return True
        width = state.evaluate(state.node.field("m_width"), watch=False)
        step = state.evaluate(state.node.field("m_FE7D7AE1"), watch=False)
        height = state.evaluate(state.node.field("m_AB733228"), watch=False)
        offset = width * _SAMPLE_OFFSETS[pos]
        x, y, z = anchor
        support = fight._collision().ground(x + offset * direction.x, z + offset * direction.z,
                                            y + step, y - step, player.mover.category)
        clearance = height + support.y - y if support is not None else 0.
        write(state, "m_179FED70", clearance)
    instance.follow(state.node, "m_9B77AA38", state)
    return True


def spawn(fight, instance, node):
    player = owner(fight, instance)
    if player is None or node.pos not in _CREATE_NODES:
        return NotImplemented
    definition = instance.evaluate(node.field("m_entityDefinition"))
    point = instance.evaluate(node.field("m_position"))
    duration, width, height = (value(instance, var) for var in (567, 6274, 1165))
    child = graph.graph(PILLAR_GRAPH)
    if (definition != expr.Asset(PILLAR) or not isinstance(point, expr.Vec3) or child is None
            or not all(math.isfinite(v) for v in point)
            or not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                       for v in (duration, width, height))):
        return NotImplemented
    health = child.node(1).field("m_amount")["m_value"]
    actor = fight.create_deployable(player, PILLAR, "Ice Wall pillar", tuple(point), health=health,
                                    radius=width / 2, height=height, seconds=duration)
    actor.body_script = actors.PillarScript(runtime.Component(actor.entity))
    actor.body_script.component.run_frame(fight.tick)
    actor.body_script.set_health(health, health)
    return expr.Entity(actor.entity)


def play_pillar(fight, instance, node):
    player = owner(fight, instance)
    if player is None or node.pos not in _PLAY_NODES:
        return False
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x0580000000000636:
        return False
    request = play.resolve_request(instance, node, resource, context_provider=lambda:
        play.InvocationContext(player.body, player.body, player.body))
    if request is None:
        return True
    child = graph.graph(PILLAR_GRAPH)
    for entity in request.targets:
        actor = fight.deployables.get(entity)
        if (actor is None or actor.source_body != player.body
                or not isinstance(actor.body_script, actors.PillarScript)):
            continue
        component = actor.body_script.component
        component.play_loaded_graph(child, source=player.body, instigator=player.body,
                                    params=request.params, overrides=resource.overrides,
                                    policy=request.policy)
        component.run_now()
    return True




def cancel(fight, target, message, params, sender):
    if message != 0x024000000000003F:
        return False
    actor = fight.deployables.get(target)
    if (actor is None or actor.definition != PILLAR or actor.source_body != sender
            or not isinstance(actor.body_script, actors.PillarScript)):
        return False
    component = actor.body_script.component
    runtime.send_message(component, message, params, sender)
    component.run_now()
    fight.destroy_deployable(target)
    return True


def retired(actor):
    if actor.definition != PILLAR or actor.owner.body != actor.source_body:
        return
    script = actor.owner.body_script
    if script is None:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == WALL_GRAPH:
            for var in range(6275, 6280):
                if value(instance, var) == expr.Entity(actor.entity):
                    instance.write((runtime.INSTANCE_SCOPE, var), expr.Entity(0))
