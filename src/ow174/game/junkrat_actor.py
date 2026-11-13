"""Original landed Steel Trap VM on its existing 103A actor identity.

The compatibility combat host still owns capture/damage/root retirement. Its
accepted target is an input to the original09BB search, not a second search or
damage producer. Component72's factory-written relation remains unbound.
"""

from ow174.game import observers
from ow174.game.script import expr, graph, runtime


class TrapWorld(runtime.World):
    def __init__(self, manager, trap):
        super().__init__(mode=manager.combat.match.game_map.mode_guid)
        self.manager, self.trap = manager, trap
        self.damage_receipt = None

    @property
    def match(self):
        return self.manager.combat.match

    def position(self, entity):
        if entity == self.trap.entity:
            return expr.Vec3(*self.trap.position)
        return self.manager.combat.match.script_body_position(entity, self.trap.entity)

    def entity_var(self, entity, var):
        if entity == self.trap.entity:
            value = self.trap.actor_component.entity_var(var, create=False)
            return value.value() if value is not None else None
        return self.trap.script.component.world.entity_var(entity, var)

    def native(self, cfg, instance):
        if cfg.get("$") == "STU_40AC5AE4":
            entity = instance.evaluate(cfg.get("m_entity"))
            if entity == expr.Entity(self.trap.entity):
                # CC8200 -> D1C220 -> C61F70 queries pitch in degrees,
                # not scale. Current103A CREATE uses this identity rotation.
                # Other rotation-to-native-pitch mappings remain unproved.
                if self.trap.rotation in ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, -1.0)):
                    return 0.0
            instance.component.unsupported(instance, "trap pose pitch", cfg["$"])
        return super().native(cfg, instance)

    def linked_tracked_targets(self, state):
        if state.instance.graph.index == 0x09BB and state.node.pos == 2:
            victim = self.trap.victim_entity
            return () if victim is None else (expr.Entity(victim),)
        return NotImplemented

    def modify_health(self, state, elapsed_ms):
        if state.instance.graph.index != 0x09BB or state.node.pos != 4:
            return NotImplemented
        targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        receipt = (targets, amount)
        if elapsed_ms == 0 and self.damage_receipt == receipt:
            # Advance the original success branch only after Combat accepted
            # this exact damage. Never call Combat again from the projection.
            self.damage_receipt = None
            state.request_finish()
            return True
        return NotImplemented


def landed(manager, trap):
    """103A initial slots, then09BA's projectile constructor's09BB play."""
    component = runtime.Component(trap.entity, world=TrapWorld(manager, trap))
    trap.actor_component = component
    component.now, component.frame = manager.combat.tick * 16, manager.combat.tick
    for index, values in ((0x0033, {(graph.INSTANCE, 199): 100.0}), (0x25B4, None)):
        definition = graph.graph(index)
        if definition is None:
            raise RuntimeError(f"missing Steel Trap initial graph {index:04X}")
        initial = component.create(definition, values=values, init=False)
        initial.set_source_entity(trap.source_body)
        initial.init()
    script = component.create(graph.graph(0x09BB), init=False)
    script.set_source_entity(trap.source_body)
    script.init()
    script.start_entries((), trap.source_body)
    trap.actor_script = script
    component.run_now()
    manager.combat.match.register_body_component(component)
    trap.actor_projection = observers.SceneBody(component)


def captured(manager, trap):
    if trap.actor_component is not None:
        trap.actor_component.world.damage_receipt = ((expr.Entity(trap.victim_entity),), trap.damage.amount)
        trap.actor_component.run_frame(manager.combat.tick)


def sync(manager):
    for trap in manager.traps.values():
        if trap.actor_component is None:
            continue
        trap.actor_component.run_frame(manager.combat.tick)
        projection = trap.actor_projection
        projection.refresh()
        for viewer in manager.combat.match.players:
            client = viewer.client
            if (not viewer.spawned or viewer.gone or client is None
                    or not callable(getattr(client, "bind_actor_component", None))):
                continue
            generation = client.actor_generation(trap.entity)
            if generation is not None and client.bind_actor_component(
                    trap.entity, projection, generation=generation):
                client.queue_bot_live(trap.entity, projection.latest_live)


def retire(trap):
    component = trap.actor_component
    if component is not None:
        component.world.match.unregister_body_component(component)
        for instance in tuple(component.instances.values()):
            component.destroy(instance)
