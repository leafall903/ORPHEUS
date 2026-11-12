"""Scoped NPC body/AI-root services reconstructed from actual entity associations.

This does not implement the native D15520/DA6910 registries or general AI. The
associated .003 AI definition runs on its own Component and identity. Only this
pair's known relationships, variable slots, body health and position are exposed.
Unknown predicates (including B96D3A98) are never forced true.
"""

from ow174.game.script import expr, runtime
from ow174.game.script.player_controller import ControllerVM


class NpcContext:
    def __init__(self, body, controller, definition, component):
        if (type(body) is not int or type(controller) is not int or body == controller
                or not 0 <= body <= 0xFFFFFFFF or not 0 <= controller <= 0xFFFFFFFF
                or not expr.valid_entity(body) or not expr.valid_entity(controller)):
            raise ValueError("NPC body and controller require distinct live identities")
        if component.entity != body:
            raise ValueError("NPC context body component identity mismatch")
        self.body = body
        self.controller = controller
        self.definition = definition
        self.body_component = component
        self.controller_component = None
        self.position = expr.Vec3(0.0, 0.0, 0.0)
        self.retired = False
        self.target_entity = 0
        self.target_host = None

    def component_of(self, entity):
        if self.retired:
            return None
        if entity == self.body:
            return self.body_component
        if entity == self.controller:
            return self.controller_component
        return None

    def variable(self, entity, identifier, *, create=False):
        if not self.retired and entity == self.target_entity and self.target_host is not None:
            host = self.target_host
            if not host.retired and host.component.fault is None:
                return host.component.entity_var(identifier, create=create)
            return None
        if entity not in (self.body, self.controller):
            return NotImplemented
        component = self.component_of(entity)
        return component.entity_var(identifier, create=create) if component is not None else None

    def play_script_context(self, instance, node):
        """Declared entity-instance binding; no target queue/transport policy."""
        from ow174.game.script import play

        component = instance.component

        def validate():
            if (self.retired
                    or self.component_of(component.entity) is not component
                    or getattr(component.world, "control_context", None) is not self
                    or component.instances.get(instance.id) is not instance
                    or instance.stopped or instance.teardown):
                component.unsupported(instance, "NPC PlayScript source lifecycle", node.cls, node)
            component.ensure_valid()

        validate()
        inherited = instance.source_entity()
        # Effective variable values may execute State callbacks and detach the
        # source. Identity must still match after that read, as in SceneWorld.
        validate()
        return play.InvocationContext(component.entity, component.entity, inherited)

    def native(self, cfg, instance):
        kind = cfg.get("$")
        field = "m_801B298F" if kind == "STU_F7262B36" else "m_entity"
        if kind not in ("STU_F7262B36", "STU_0AC15F78", "STU_DB742151",
                         "STU_9B7648C3", "STU_71A87E92"):
            return NotImplemented
        if kind == "STU_DB742151":
            from ow174.game.script import play

            value = play.evaluate_scalar(instance, cfg.get(field), initial=expr.Entity(0))
        else:
            value = instance.evaluate(cfg.get(field))
        entity = expr.to_entity(value)
        if kind == "STU_DB742151" and not expr.valid_entity(entity):
            return expr.Entity(0)
        if entity not in (self.body, self.controller):
            return NotImplemented
        if self.retired:
            if kind == "STU_DB742151":
                return expr.Entity(0)
            return 0.0 if kind == "STU_9B7648C3" else None
        if kind in ("STU_F7262B36", "STU_DB742151"):
            return expr.Entity(self.controller)
        if kind == "STU_0AC15F78":
            return expr.Entity(self.body) if entity == self.controller else None
        if kind == "STU_71A87E92":
            return self.position
        if kind == "STU_9B7648C3" and entity == self.body:
            # Same explicit pool-total input set by Combat._sync_health, not
            # an invented health/difficulty table or an always-healthy predicate.
            from ow174.game.script.driver import OWNER_HEALTH

            slot = self.body_component.entity_var(OWNER_HEALTH, create=False)
            return slot.value() if slot is not None else 0.0
        return NotImplemented

    def native_filter(self, cfg, instance, *, watcher=None):
        """Opt-in manual13C2 target profile, not general native AI/filter services."""
        from ow174.game.script import filter_game, play

        host = self.target_host
        if (self.retired or host is None or host.retired or host.component.fault is not None
                or host.component.entity != self.target_entity
                or instance.component is not self.controller_component
                or instance.graph.index != 0x13C2 or instance.stopped or instance.teardown):
            return NotImplemented
        if cfg is not instance.graph.node(10).field("m_condition"):
            return NotImplemented
        inputs = host.filter_inputs
        if (inputs is None or inputs.retired or inputs.owner != self.target_entity
                or inputs.bound_host is not host):
            return NotImplemented
        if not filter_game.source_independent(cfg.get("m_filter")):
            return NotImplemented
        value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0), watcher=watcher)
        target = expr.to_entity(value)
        if not expr.valid_entity(target):
            return False
        if target != self.target_entity:
            return NotImplemented
        try:
            result = filter_game.evaluate_target(instance, cfg["m_filter"], target,
                                                 inputs=inputs, watcher=watcher)
        except NotImplementedError:
            return NotImplemented
        if (self.retired or self.target_host is not host or host.retired or host.filter_inputs is not inputs
                or host.component.fault is not None or self.target_entity != target):
            return NotImplemented
        return result

    def set_target(self, target=None, *, host=None):
        """Manual command input, not native autonomous target acquisition.

        Actual13C2/node7 selects entity14365 when present before its candidate
        list; it writes target1650 itself. Never substitute its output directly.
        The manual command supplies the selected player's controller entity;
        the authored category40000 gate excludes this server's body-only bits.
        """
        value = 0 if target is None else target
        if (type(value) is not int or not 0 <= value <= 0xFFFFFFFF
                or (value and not expr.valid_entity(value)) or value in (self.body, self.controller)):
            raise ValueError("invalid manual NPC target entity")
        if host is not None and (type(host) is not ControllerVM or host.retired
                                 or host.component.fault is not None
                                 or host.component.entity != value):
            raise ValueError("manual NPC target requires its live independent controller host")
        if self.retired:
            return
        if self.target_host is not host:
            self._detach_target()
            self.target_host = host
            if host is not None:
                host.component.world.readers.append(self)
        self.target_entity = value
        root = self.controller_component
        if root is not None:
            root.set(14365, expr.Entity(value))

    def _detach_target(self):
        host = self.target_host
        if host is None:
            return
        from ow174.game.assassin import retire_pin_effect

        retire_pin_effect(host, self.body)
        # Explicit manual-binding retirement policy: remove only this pair's
        # foreign reads before dropping the reverse snapshot/lifetime edge.
        for var in host.component.vars.values():
            for state in list(var.watchers):
                if state.instance.component not in (self.body_component, self.controller_component):
                    continue
                var.watchers.pop(state, None)
                state.watched.pop(var, None)
                if state.active and not state.flags_changed:
                    state.flags_changed = True
                    state.instance.post(runtime.Event(state.now, 0, runtime.CHANGED, state=state))
        host.component.world.readers.remove(self)
        self.target_host = None

    def selects(self, instance):
        # Scoped, explicit manual selection of Assassin's actual13C2 candidate.
        # Its authored criteria remain in the decision; other behaviors remain
        # unsupported rather than being enabled indiscriminately.
        if (self.retired or not self.target_entity or instance.component is not self.controller_component
                or instance.graph.index != 0x13C2):
            return False
        candidate = next((node for node in instance.graph.nodes if node.cls == "STU_91EF24A9"), None)
        return candidate is not None and all(expr.truthy(instance.evaluate(config))
                                            for config in candidate.field("m_AAAC951E") or [])

    def send(self, target, message, params, sender):
        component = self.component_of(target)
        if component is None:
            return False
        runtime.send_message(component, message, params, sender)
        return True

    def advance(self, frame, position=None):
        if self.retired:
            return
        if position is not None:
            self.position = expr.Vec3(*position)
        component = self.controller_component
        if component is not None and component.frame < frame:
            from ow174.game.script.nodes import AIBehavior

            for instance in list(component.instances.values()):
                for state in list(instance.states.values()):
                    if isinstance(state, AIBehavior):
                        state.refresh_selection()
            component.run_frame(frame)
            # AI action reports are not body weapon reports. The actual body
            # keeps its own input/volley path; do not substitute root shots.
            component.shots.clear()

    def retire(self):
        if self.retired:
            return
        self._detach_target()
        self.retired = True
        self.target_entity = 0
        component = self.controller_component
        if component is not None:
            component.owner_dead = True
            component.held.clear()
            component.shots.clear()
            for instance in list(component.instances.values()):
                component.destroy(instance)
