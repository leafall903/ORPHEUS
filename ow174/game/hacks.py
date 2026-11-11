"""Host targeting and lifecycle for Sombra's authored body Hack."""

import math

from ow174.game.script import expr, graph, runtime

SOMBRA = 0x02E000000000012E
HACK = 0x0B1B
APPLICATION = 0x0B1F
EMP = 0x0B31
EMP_HIT = 0x0B32
WINSTON_SKILLS = frozenset((0x010A, 0x00FA, 0x0109))
WINSTON_SKILL_BUTTONS = frozenset((35, 36, 37, 110, 114, 116))


def plays(instance, node, source, position, destination):
    return (instance.graph.index == source and node.pos == position
            and graph.guid(node.field("m_script.m_graph")) == (0x0580000000000000 | destination))


class Hacks:
    def __init__(self, combat):
        self.combat = combat
        self.effects = {}

    def caster(self, body):
        return next((p for p in self.combat.match.players
                     if p.has_body and not p.gone and p.body == body), None)

    def active(self, entity):
        return any(self.combat.tick < deadline and not application.stopped
                   for application, deadline in self.effects.get(entity, ()))

    def allows(self, state):
        instance = state.instance
        restricted = instance.graph.index in WINSTON_SKILLS or (
            instance.graph.index == HACK and state.index == 53
        ) or instance.graph.index == EMP
        return not restricted or not self.active(instance.component.entity)

    def button_allowed(self, body, hero, button):
        restricted = ((hero == 0x02E0000000000009 and button in WINSTON_SKILL_BUTTONS)
                      or (hero == SOMBRA and button in (29, 37, 112, 116)))
        return not restricted or not self.active(body)

    def track(self, caster, state):
        from ow174.game import combat

        if caster is None or self.combat.is_dead(caster) or self.combat.actions_blocked(caster):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        locked = state.instance.find_var(expr.INSTANCE, 375).value()
        maintaining = state.node.pos == 52
        origin, direction = self.combat._eye(caster), combat.aim(*state.instance.component.world.aim)
        found = []
        for target in self.combat._enemies(caster):
            if self.combat.zarya_barriers.protects(target.owner):
                continue
            if maintaining and (not isinstance(locked, tuple) or expr.Entity(target.entity) not in locked):
                continue
            a, b = target.segment()
            ray = direction
            if maintaining:
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                offset = combat._sub(point, origin)
                distance = math.sqrt(combat._dot(offset, offset))
                if distance <= combat.EPSILON or combat._dot(direction, offset) <= 0:
                    continue
                ray = tuple(axis / distance for axis in offset)
            hit = combat.ray_capsule(origin, ray, a, b, target.radius)
            if hit is None or hit > radius:
                continue
            wall = combat.ray_world(self.combat._collision(), origin, ray, hit)
            if wall is not None and wall < hit:
                continue
            if self.combat.barriers.first(caster, origin, ray, hit) is None:
                found.append((hit, target.entity))
        return (min(found)[1],) if found else ()

    def _emp_visible(self, origin, point, surface_radius, radius):
        from ow174.game import combat

        offset = combat._sub(point, origin)
        distance = math.sqrt(combat._dot(offset, offset))
        length = max(0.0, distance - surface_radius)
        if length > radius:
            return False
        if length <= combat.EPSILON:
            return True
        ray = tuple(axis / distance for axis in offset)
        wall = combat.ray_world(self.combat._collision(), origin, ray, length)
        return wall is None or wall >= length

    def emp_targets(self, caster, state):
        if caster is None or self.combat.actions_blocked(caster):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        origin = self.combat._eye(caster)
        found = []
        for target in self.combat._enemies(caster):
            a, b = target.segment()
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            if self._emp_visible(origin, point, target.radius, radius):
                found.append(target.entity)
        return tuple(found)

    def emp_barriers(self, instance):
        caster = self.caster(instance.component.entity)
        if caster is None or self.combat.actions_blocked(caster):
            return
        tracker = instance.node_state(instance.graph.node(40))
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        origin = self.combat._eye(caster)
        free_for_all = self.combat.match.game_map.free_for_all
        # Reconstructed sphere admission. The native barrier target tag is unresolved.
        for actor in self.combat.barriers.snapshot():
            if actor.owner is caster or (not free_for_all and actor.owner.team == caster.team):
                continue
            if self._emp_visible(origin, actor.centre, actor.radius, radius):
                actor.damage(actor.health)
        self.combat.barriers.update()

    def emp_health(self, state):
        from ow174.game.combat import SHIELDS_POOL

        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        for target in self.combat._targets():
            if target.entity != state.instance.component.entity:
                continue
            vitals = self.combat.vitals_of(target)
            drained = min(vitals.now[SHIELDS_POOL], amount)
            if drained > 0:
                vitals.now[SHIELDS_POOL] -= drained
                vitals.changed.add(SHIELDS_POOL)
                self.combat._sync_body_health(target.owner, vitals)
                self.combat.zarya_shields.damaged(target.owner, self.combat.tick)
            break

    def submit(self, instance, request):
        source = instance.graph.index
        if source == EMP_HIT:
            for target in self.combat._targets():
                if (target.entity == instance.component.entity and target.entity in request.targets
                        and not self.combat.vitals_of(target).dead):
                    self._apply(target, request, APPLICATION)
                    return
            return
        caster = self.caster(instance.component.entity)
        if caster is None or self.combat.actions_blocked(caster):
            return
        if source == EMP:
            tracker = instance.node_state(instance.graph.node(40))
            admissible = self.emp_targets(caster, tracker)
            destination = EMP_HIT
            barrier_amount = expr.to_float(instance.evaluate(
                graph.graph(EMP_HIT).node(3).field("m_modifyHealth.m_amount")
            ))
        else:
            tracker = instance.node_state(instance.graph.node(52))
            admissible = self.track(caster, tracker)
            destination = APPLICATION
            barrier_amount = 0.0
        for target in self.combat._enemies(caster):
            if target.entity not in request.targets or target.entity not in admissible:
                continue
            if barrier_amount and self.combat.zarya_barriers.intercept(
                target.owner, barrier_amount, caster.body
            ):
                continue
            self._apply(target, request, destination)

    def _apply(self, target, request, destination):
        from ow174.game import sniper_reposition
        from ow174.game.combat import TICK

        script = target.owner.body_script
        if script is None or script.component.owner_dead:
            return
        definition = graph.graph(destination)
        application = script.component.play_loaded_graph(
            definition, source=request.context.caller, instigator=request.context.instigator,
            params=request.params, overrides=request.resource.overrides, policy=request.policy,
        )
        if application is None:
            return
        if destination == EMP_HIT:
            script.component.run_now()
            return
        duration = expr.to_float(application.evaluate(definition.node(96).field("m_value")))
        if duration > 0:
            deadline = self.combat.tick + math.ceil(duration / TICK)
            self.effects.setdefault(target.entity, []).append((application, deadline))
            component = script.component
            component.edges([(button, False) for button in tuple(component.held)
                             if not self.button_allowed(target.entity, component.world.hero, button)])
            self.combat.assassins.interrupt(target.owner)
            if target.entity in self.combat.sniper_repositions:
                sniper_reposition.cancel(self.combat, target.owner)

    def received_damage(self, victim, source):
        component = getattr(getattr(victim, "body_script", None), "component", None)
        if component is None or component.world.hero != SOMBRA:
            return
        runtime.send_message(component, 0x0240000000000011, {0x003D: ()}, source)

    def retire(self, entity):
        for application, _ in self.effects.pop(entity, ()):
            if application.component.instances.get(application.id) is application:
                application.component.destroy(application)

    def update(self):
        for entity, effects in tuple(self.effects.items()):
            keep = []
            for application, deadline in effects:
                if self.combat.tick < deadline and not application.stopped:
                    keep.append((application, deadline))
                elif application.component.instances.get(application.id) is application:
                    application.component.destroy(application)
            if keep:
                self.effects[entity] = keep
            else:
                self.effects.pop(entity, None)
