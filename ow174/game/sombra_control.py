"""Selected Sombra body Hack and EMP application against Widowmaker.

The original 0B1B/0B31/0B32/0B1F graphs own cast and application. Body
acquisition and skill-only silence are explicit host projections.
"""

import math

from ow174.game.script import expr, graph, nodes, play, runtime

HERO = 0x02E000000000012E
WIDOW = 0x02E000000000000A
HACK, EMP, EMP_CHILD, EFFECT = 0x0B1B, 0x0B31, 0x0B32, 0x0B1F
SILENCED_BUTTONS = 8 | 16 | 32
WIDOW_ABILITIES = (0x005A, 0x0056, 0x0067)


def actor(fight, state):
    component = state.instance.component
    return next((p for p in fight.match.players if p.has_body and p.body_script is not None
                 and p.body_script.component is component and p.hero.guid == HERO), None)


def targets(fight, state, *, command=None):
    graph_id, pos = state.instance.graph.index, state.node.pos
    if (graph_id, pos) not in ((HACK, 50), (HACK, 52), (HACK, 68), (EMP, 40)):
        return NotImplemented
    player = actor(fight, state)
    if player is None or fight.is_dead(player):
        return ()
    from ow174.game.combat import aim, ray_capsule, ray_world

    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin = fight._eye(player)
    direction = aim(*(command.aim() if command is not None else (player.mover.yaw, player.mover.pitch)))
    locked = state.instance.find_var(expr.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in fight._enemies(player):
        if fight.invulnerable(target.owner) or (pos == 52 and target.entity not in locked_ids):
            continue
        a, b = target.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = tuple(x - y for x, y in zip(point, origin, strict=True))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance - target.radius > radius:
            continue
        if graph_id == EMP or pos == 52:
            if pos == 52 and sum(v * w for v, w in zip(delta, direction, strict=True)) <= 0:
                continue
            ray = tuple(v / distance for v in delta) if distance > 1e-9 else direction
            reach = max(0.0, distance - target.radius)
        else:
            ray = direction
            reach = ray_capsule(origin, ray, a, b, target.radius)
            if reach is None or reach > radius:
                continue
        wall = ray_world(fight._collision(), origin, ray, reach)
        if wall is not None and wall < reach:
            continue
        if graph_id == HACK and fight._shield_hit(player, origin, ray, reach) is not None:
            continue
        found.append((reach, target.entity))
    return tuple(entity for _, entity in (found if graph_id == EMP else sorted(found)[:1]))


def prepare(fight, player, command):
    if player.hero.guid != HERO or not command.buttons & 2 or fight.is_dead(player):
        return
    for instance in player.body_script.component.instances.values():
        if instance.graph.index != HACK:
            continue
        ability = instance.state(53)
        selected = instance.find_var(expr.INSTANCE, 375).value()
        if ability.flags & nodes.Ability.RUNNING and selected:
            return
        tracker = instance.node_state(instance.graph.node(68))
        entities = targets(fight, tracker, command=command)
        instance.write((expr.INSTANCE, 375), tuple(expr.Entity(e) for e in entities))


def ray(fight, state):
    if state.instance.graph.index != HACK or state.node.pos != 59:
        return NotImplemented
    source = actor(fight, state)
    if source is None:
        return False, None
    entity = expr.to_entity(state.instance.find_var(expr.INSTANCE, 8909).value())
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if target is None or fight.invulnerable(target.owner):
        return False, None
    from ow174.game.combat import ray_world

    origin = fight._eye(source)
    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
    distance = math.dist(origin, point)
    if distance <= 1e-9:
        return True, expr.Vec3(*point)
    direction = tuple((x - y) / distance for x, y in zip(point, origin, strict=True))
    reach = max(0.0, distance - target.radius)
    wall = ray_world(fight._collision(), origin, direction, reach)
    clear = wall is None and fight._shield_hit(source, origin, direction, reach) is None
    return clear, expr.Vec3(*point)


def application(world, instance, node):
    pair_source = instance.graph.index
    if world.match is None or pair_source not in (HACK, EMP, EMP_CHILD):
        return False
    if pair_source != EMP_CHILD and world.hero != HERO:
        return False
    resource = play.resolve_resource(instance, node)
    if resource is None:
        return False
    pair = (pair_source, resource.guid & 0xFFFFFFFFFFFF)
    if pair not in ((HACK, EFFECT), (EMP, EMP_CHILD), (EMP_CHILD, EFFECT)):
        return False
    source = instance.source_entity() if pair_source == EMP_CHILD else instance.component.entity
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(pair[1])
    if request is None or definition is None:
        return True
    fight = world.match.combat
    owner = next((p for p in world.match.players
                  if p.has_body and p.body == source and p.hero.guid == HERO), None)
    if owner is None or fight.is_dead(owner):
        return True
    for target in fight._enemies(owner):
        script = getattr(target.owner, "body_script", None)
        if (target.entity not in request.targets or fight.invulnerable(target.owner)
                or getattr(getattr(target.owner, "hero", None), "guid", None) != WIDOW
                or script is None or script.retired or script.hero != WIDOW):
            continue
        applied = script.component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params,
            overrides=resource.overrides, policy=request.policy,
            instigator=request.context.instigator,
        )
        if applied is not None and pair[1] == EFFECT:
            fight.sombra_hacks.apply(target, applied)
    return True


def received_damage(actor, *, source, now):
    """Send the authored body-damage entry only to the selected Hack graph."""
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO:
        return
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.hero != HERO:
        return
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index == HACK:
            runtime.send_instance_message(
                instance, 0x0240000000000011, {0x3D: ()}, source, at_time=round(now * 1000),
            )
    script.component.run_now()


class Hacks:
    def __init__(self, combat):
        self.combat = combat
        self.active = {}
        self.bases = {}

    def silenced(self, body, tick):
        return any(tick < until and not instance.stopped for instance, until in self.active.get(body, ()))

    def apply(self, target, instance):
        from ow174.game.combat import TICK

        duration = expr.to_float(instance.evaluate(instance.graph.node(96).field("m_value")))
        if duration <= 0:
            return
        component = instance.component
        if target.entity not in self.active:
            self.bases[target.entity] = component.entity_var(888).base
        self.active.setdefault(target.entity, []).append(
            (instance, self.combat.tick + math.ceil(duration / TICK)),
        )
        component.set(888, True)
        for owned in tuple(component.instances.values()):
            if owned.graph.index in WIDOW_ABILITIES:
                for state in tuple(owned.states.values()):
                    if isinstance(state, nodes.Ability):
                        state.presses.clear()
                        state.stop()

    @staticmethod
    def _destroy(instance):
        if instance.component.instances.get(instance.id) is instance:
            instance.component.destroy(instance)

    def retire(self, body):
        effects = self.active.pop(body, ())
        for instance, _ in effects:
            self._destroy(instance)
        if effects:
            effects[-1][0].component.set(888, self.bases.pop(body))

    def update(self, tick):
        for entity, effects in tuple(self.active.items()):
            keep = [(instance, until) for instance, until in effects if tick < until and not instance.stopped]
            if not keep:
                self.retire(entity)
                continue
            for instance, until in effects:
                if tick >= until or instance.stopped:
                    self._destroy(instance)
            self.active[entity] = keep
