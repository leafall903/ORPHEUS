"""Selected Sombra Hack and EMP body application against Torbjorn.

The original 0B1B, 0B31, 0B32 and 0B1F graphs own cast and application. Body
acquisition and skill-only silence are explicit host projections.
"""

import math

from ow174.game import phase_defenses
from ow174.game.script import expr, graph, nodes, play, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000012E
TORBJORN = 0x02E0000000000006
HACK, EMP, EMP_CHILD, EFFECT = 0x0B1B, 0x0B31, 0x0B32, 0x0B1F
SILENCED_BUTTONS = 8 | 16 | 32


def actor(fight, state):
    component = state.instance.component
    return next((p for p in fight.match.players if p.has_body and p.body_script is not None
                 and p.body_script.component is component and p.hero.guid == HERO), None)


def targets(fight, state, *, command=None):
    index, pos = state.instance.graph.index, state.node.pos
    if state.instance.component.strict or (index, pos) not in ((HACK, 50), (HACK, 52), (HACK, 68), (EMP, 40)):
        return NotImplemented
    player = actor(fight, state)
    if player is None or fight.is_dead(player):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if index == EMP:
        return tuple(expr.Entity(target.entity) for target in fight._enemies(player)
                     if not phase_defenses.phase(target.owner)[0]
                     and line(fight, player, target, limit=radius, barriers=False)[0])

    from ow174.game.combat import aim, ray_capsule, ray_world

    origin = fight._eye(player)
    direction = aim(*(command.aim() if command is not None else (player.mover.yaw, player.mover.pitch)))
    locked = state.instance.find_var(expr.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in fight._enemies(player):
        if phase_defenses.phase(target.owner)[0] or (pos == 52 and target.entity not in locked_ids):
            continue
        a, b = target.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = tuple(x - y for x, y in zip(point, origin, strict=True))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance - target.radius > radius:
            continue
        if pos == 52:
            if sum(v * w for v, w in zip(delta, direction, strict=True)) <= 0:
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
        if fight._shield_trace(player, origin, ray, reach) is not None:
            continue
        found.append((reach, target.entity))
    return tuple(expr.Entity(entity) for _, entity in sorted(found)[:1])


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
        instance.write((expr.INSTANCE, 375), entities)


def ray(fight, state):
    if state.instance.component.strict or (state.instance.graph.index, state.node.pos) != (HACK, 59):
        return NotImplemented
    source = actor(fight, state)
    if source is None:
        return False, None
    entity = expr.to_entity(state.instance.find_var(expr.INSTANCE, 8909).value())
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if target is None or phase_defenses.phase(target.owner)[0]:
        return False, None
    return line(fight, source, target)


def line(fight, source, target, *, limit=math.inf, barriers=True):
    from ow174.game.combat import ray_world

    origin = fight._eye(source)
    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
    distance = math.dist(origin, point)
    if distance <= 1e-9:
        return True, expr.Vec3(*point)
    direction = tuple((x - y) / distance for x, y in zip(point, origin, strict=True))
    reach = max(0.0, distance - target.radius)
    wall = ray_world(fight._collision(), origin, direction, reach)
    clear = reach <= limit and wall is None and (
        not barriers or fight._shield_trace(source, origin, direction, reach) is None
    )
    return clear, expr.Vec3(*point)


def destroy_emp_barriers(fight, owner, radius):
    """Apply EMP to hostile barrier surfaces visible from the caster."""
    from ow174.game.combat import capsule_visible, ray_world

    origin = fight._eye(owner)
    collision = fight._collision()
    for shell in fight.world_bubbles.enemies(owner):
        distance = math.dist(origin, shell.point)
        reach = max(0.0, distance - shell.radius)
        if reach > radius:
            continue
        if reach:
            direction = tuple((b - a) / distance for a, b in zip(origin, shell.point, strict=True))
            if ray_world(collision, origin, direction, reach) is not None:
                continue
        fight.world_bubbles.damage(shell, shell.health)
    for barriers in (fight.barriers, fight.sigma_barriers, fight.orisa_barriers):
        for barrier in tuple(barriers.enemies(owner)):
            contact = barriers.nearest(barrier, origin)
            if contact is None:
                continue
            distance, surface = contact
            if distance <= radius and capsule_visible(collision, origin, surface, surface, 0):
                barriers.damage(barrier, math.inf)


def application(world, instance, node):
    index = instance.graph.index
    if world.match is None or index not in (HACK, EMP, EMP_CHILD):
        return False
    if index != EMP_CHILD and world.hero != HERO:
        return False
    resource = play.resolve_resource(instance, node)
    child_index = EMP_CHILD if index == EMP else EFFECT
    if resource is None or resource.guid != (0x0580000000000000 | child_index):
        return False
    source = instance.source_entity() if index == EMP_CHILD else instance.component.entity
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(child_index)
    if request is None or definition is None:
        return True
    fight = world.match.combat
    owner = next((p for p in world.match.players
                  if p.has_body and p.body == source and p.hero.guid == HERO), None)
    if owner is None or fight.is_dead(owner):
        return True
    tracker = {HACK: 52, EMP: 40}.get(index)
    limit = (expr.to_float(instance.evaluate(instance.graph.node(tracker).field("m_radius")))
             if tracker is not None else None)
    if index == EMP:
        destroy_emp_barriers(fight, owner, limit)
    for target in fight._enemies(owner):
        script = getattr(target.owner, "body_script", None)
        if (target.entity not in request.targets or phase_defenses.phase(target.owner)[0]
                or (limit is not None and not line(
                    fight, owner, target, limit=limit, barriers=index == HACK,
                )[0])):
            continue
        if index == EMP:
            vitals = fight.vitals_of(target)
            if vitals.now[SHIELDS_POOL] > 0 and fight.bubbles.for_target(target.owner) is None:
                vitals.now[SHIELDS_POOL] = 0.0
                vitals.changed.add(SHIELDS_POOL)
                fight._sync_body_health(target.owner, vitals)
        turret = any(target.owner is mounted.actor for mounted in fight.turrets.values())
        player = getattr(getattr(target.owner, "hero", None), "guid", None) == TORBJORN
        if script is None or not (turret or (player and not script.retired and script.hero == TORBJORN)):
            continue
        applied = script.component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params,
            overrides=resource.overrides, policy=request.policy,
            instigator=request.context.instigator,
        )
        if applied is not None and child_index == EFFECT:
            fight.sombra_hacks.apply(target, applied)
    return True


def received_damage(actor, *, source):
    """Send the authored body-damage entry only to the selected Hack graph."""
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO:
        return
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.hero != HERO:
        return
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index == HACK:
            runtime.send_instance_message(
                instance, 0x0240000000000011, {0x3D: ()}, source,
            )
    script.component.run_now()


class Hacks:
    def __init__(self, combat):
        self.combat = combat
        self.active = {}
        self.bases = {}
        self.suppressed = {}

    def silenced(self, body, tick):
        return any(tick < until and identifier in component.instances
                   for component, identifier, until in self.active.get(body, ()))

    def admitted_buttons(self, body, tick, buttons):
        blocked = self.suppressed.get(body, 0) & buttons
        if self.silenced(body, tick):
            blocked |= buttons & SILENCED_BUTTONS
        if blocked:
            self.suppressed[body] = blocked
        else:
            self.suppressed.pop(body, None)
        return buttons & ~blocked

    def apply(self, target, instance):
        from ow174.game.combat import TICK

        duration = expr.to_float(instance.evaluate(instance.graph.node(96).field("m_value")))
        if duration <= 0:
            return
        component = instance.component
        if target.entity not in self.active:
            self.bases[target.entity] = component.entity_var(888).base
        self.active.setdefault(target.entity, []).append(
            (component, instance.id, self.combat.tick + math.ceil(duration / TICK)),
        )
        component.set(888, True)

    @staticmethod
    def _destroy(component, identifier):
        instance = component.instances.get(identifier)
        if instance is not None:
            component.destroy(instance)

    def retire(self, body):
        self.suppressed.pop(body, None)
        self._clear_effects(body)

    def _clear_effects(self, body):
        effects = self.active.pop(body, ())
        for component, identifier, _ in effects:
            self._destroy(component, identifier)
        if effects:
            effects[-1][0].set(888, self.bases.pop(body))

    def update(self, tick):
        for entity, effects in tuple(self.active.items()):
            keep = [row for row in effects if tick < row[2] and row[1] in row[0].instances]
            if not keep:
                self._clear_effects(entity)
                continue
            for component, identifier, until in effects:
                if tick >= until:
                    self._destroy(component, identifier)
            self.active[entity] = keep
