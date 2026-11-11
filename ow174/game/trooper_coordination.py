"""Match-clock host for the opt-in authored Trooper coordination graph."""

import math

from ow174.game import combat, npc
from ow174.game.script import controller_queries, expr, graph, message_receiver, message_state, play, runtime
from ow174.game.script.bot import BotWorld

GRAPH = 0x1034
COORDINATION_FIELDS = frozenset((11183, 11184, 11185, 11190, 14084, 14179))


class CoordinationWorld(BotWorld):
    def __init__(self, host, body, possessor):
        super().__init__(npc.TROOPER_HERO, host.match.game_map.mode_guid, {})
        self.host = host
        self.match = host.match
        self.body = body
        self.possessor = possessor
        self.context = None

    def tracked_targets(self, state):
        candidates = super().tracked_targets(state)
        if candidates is NotImplemented:
            return NotImplemented
        return tuple(candidate for candidate in candidates if candidate.id in self.host.components)

    def controller_record(self, entity):
        if entity == self.body:
            return controller_queries.Record(entity, frozenset((43,)), self.possessor)
        if entity == self.possessor:
            return controller_queries.Record(entity, frozenset((44,)))
        return super().controller_record(entity)

    def message_state_context(self, entity, *, component=None):
        if entity == self.body and self.context is not None and component is self.context.component:
            return self.context
        return NotImplemented

    def voice_context(self, entity):
        return None if entity == self.body else NotImplemented

    def entity_var_slot(self, entity, var, *, create=False):
        if var not in COORDINATION_FIELDS:
            return NotImplemented
        component = self.host.components.get(entity)
        return component.entity_var(var, create=create) if component is not None else None

    def array_filter_accepts(self, state, value):
        if state.instance.graph.index != GRAPH or state.node.pos not in (36, 38, 45, 47):
            return NotImplemented
        if not isinstance(value, expr.Entity) or value.id not in self.host.components:
            return False
        condition = (state.node.field("m_filter") or {}).get("m_condition")
        if condition is None:
            return True
        with state.instance.component.parameter_scope({8: value}):
            result, success = play.evaluate_scalar_result(state.instance, condition, watcher=state)
        return not success or expr.truthy(result)

    def native(self, cfg, instance):
        if instance is not None and instance.graph.index == GRAPH:
            if (cfg.get("$") == "STU_DB742151"
                    and expr.to_entity(instance.evaluate(cfg["m_entity"])) == self.body):
                return expr.Entity(self.possessor)
            if cfg is instance.graph.node(28).field("m_value")["m_configVars"][0]:
                return self._local_vector(instance, cfg)
            if cfg is instance.graph.node(27).field("m_value")["m_configVars"][0]:
                direction = instance.evaluate(cfg["m_3ABD589C"])
                x, y, z = direction if isinstance(direction, expr.Vec3) else (0, 0, 1)
                length = math.hypot(x, y, z)
                yaw = math.degrees(math.atan2(x, z)) if x or z else 0
                pitch = math.degrees(math.asin(-y / length)) if length else 0
                return expr.Vec3(expr.f32(yaw), expr.f32(pitch), 0)
        return super().native(cfg, instance)

    def _local_vector(self, instance, cfg):
        owner = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        target = expr.to_entity(instance.evaluate(cfg["m_CFEC5F76"]["m_entity"]))
        body = next((bot for bot in self.match.bots if bot.entity == owner and not bot.dead), None)
        if body is None:
            return expr.EVALUATION_FAILED
        other = next((bot for bot in self.match.bots if bot.entity == target and not bot.dead), None)
        point = other.position if other is not None else (0, 0, 1)
        dx, dy, dz = (b - a for a, b in zip(body.position, point, strict=True))
        sine, _, cosine = combat.aim(body.yaw, 0)
        return expr.Vec3(expr.f32(dx * cosine - dz * sine), expr.f32(dy),
                         expr.f32(dx * sine + dz * cosine))


class Coordination:
    def __init__(self, match):
        self.match = match
        self.messages = message_state.Host(notify=message_receiver.notify)
        self.components = {}
        self.faults = {}

    def admit(self, bot, tick):
        controller = getattr(bot, "trooper_controller", None)
        if controller is None or bot.entity in self.components or bot.entity in self.faults:
            return
        world = CoordinationWorld(self, bot.entity, controller.entity)
        component = runtime.Component(bot.entity, world=world, strict=True, seed=bot.entity)
        world.context = message_state.Context(
            component, self.messages,
            policy=lambda state: message_state.Policy(False, False, False),
            frame=lambda state: state.instance.component.frame,
            priority=lambda state, resource: None,
            receiver_policy=lambda state: message_receiver.Policy(
                False, False, False, False, False, False, True, False, False, False,
            ),
            receiver_generation=lambda state: state.instance.component.frame,
        )
        component.frame = tick - 1
        component.now = component.frame * component.frame_ms
        component.create(graph.graph(GRAPH))
        self.components[bot.entity] = component

    def advance(self, tick):
        for entity, component in tuple(self.components.items()):
            if component.frame >= tick:
                continue
            try:
                component.run_frame(tick)
            except (runtime.UnsupportedScriptError, runtime.ScriptBudgetError) as error:
                self.retire(entity)
                self.faults[entity] = error

    def retire(self, entity):
        self.faults.pop(entity, None)
        component = self.components.get(entity)
        if component is None:
            return
        if component.fault is None:
            for instance in tuple(component.instances.values()):
                component.destroy(instance)
        else:
            for instance in component.instances.values():
                for state in instance.states.values():
                    state.unwatch()
        for var in component.vars.values():
            for watcher in tuple(var.watchers):
                watcher.watched.pop(var, None)
                var.watchers.pop(watcher, None)
        component.world.context.retire()
        del self.components[entity]
