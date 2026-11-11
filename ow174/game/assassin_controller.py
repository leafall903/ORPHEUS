"""Match host for the authored Assassin 1158 controller."""

import math

from ow174.game import assassin, bots
from ow174.game.collision import TEAM0, TEAM1
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import assassin_movement, entity_filter, entity_queries, expr, graph, message_receive, runtime, scene

GRAPH = 0x1158
TARGET_GRAPH = 0x13C2
TARGET_BODY_GRAPH = 0x13C3
ATTACK = 0x02400000000009C6
STANDOFF = 1.25  # Local approach policy. The graph owns the attack threshold.


def definition(index=GRAPH):
    found = graph.graph(index)
    if found is None:
        raise ValueError(f"authored Assassin requires the {index:04X} graph supplement")
    return found


class ControllerWorld(scene.SceneWorld):
    native_state_misses = ((GRAPH, 77, "STU_356628FC"),)

    def __init__(self, host):
        super().__init__(native_state_misses=self.native_state_misses)
        self.host = host
        owner, body = host.entity, host.bot.entity
        self.bind(owner)
        self.bind(body)
        self.bind_script_component(body, None)
        self.set_possessions(possessors=(owner,), primary={body: owner})
        self.set_component44_body_id(owner, body)
        records = {owner: entity_queries.Target(owner, entity_queries.Possession(body))}
        self.set_entity_query_context(owner, entity_queries.Context(
            owner, True, True, True, lookup=records.get, watch_environment=None,
            watch_possession=lambda state, target: None,
        ))

    def selects_ai_behavior(self, instance):
        return instance.graph.index == self.host.definition.index

    def hosts_assassin_pursuit_callbacks(self, entity):
        # Movement is supplied by the Match. Native pursuit timers are unhosted.
        return False if entity == self.host.entity else NotImplemented

    def assassin_movement_sample(self, entity):
        if entity == self.host.bot.entity:
            if self.host.mover is not None and self.host.mover.airborne:
                return None
            return assassin_movement.Sample(0, self.host.bot.held[1] / bots.FORWARD)
        return NotImplemented

    def entity_filter_record(self, entity):
        target = self.host.target
        if target is not None and target.entity == entity:
            return entity_filter.Record(1 | (1 << 18), True, False)
        return None

    def target_record_scalar(self, owner, target):
        if owner != self.host.entity:
            return NotImplemented
        selected = self.host.target
        return 1.0 if selected is not None and selected.entity == target else None

    def position(self, entity):
        body = self.host.bot.entity
        if entity == self.host.entity:
            entity = body
        if entity not in self.entities:
            return None
        return self.host.fight.match.script_body_position(entity, body)

    def body_health(self, entity):
        vitals = self.host.fight.vitals.get(entity)
        return (vitals.total, vitals.maximum) if vitals is not None else None

    def native(self, cfg, instance):
        if instance is not None and instance.graph.index == GRAPH and cfg.get("$") == "STU_71A87E92":
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            point = self.position(entity)
            return point if point is not None else expr.EVALUATION_FAILED
        return super().native(cfg, instance)


class Controller:
    def __init__(self, fight, bot, definition):
        self.fight = fight
        self.bot = bot
        self.definition = definition
        self.entity = 0xB0000000 | (bot.entity & 0x0FFFFFFF)
        self.target = None
        self.world = None
        self.component = None
        self.instance = None
        self.mover = None

    def candidates(self):
        bot, fight = self.bot, self.fight
        if fight.is_stunned(bot) or bot.effect_mover is not None:
            return []
        from ow174.game.combat import EYE, capsule_visible

        origin = (bot.position[0], bot.position[1] + EYE, bot.position[2])
        candidates = []
        for target in fight._enemies(bot):
            player = target.owner
            if isinstance(player, bots.Bot) or player.gone or player.client is None:
                continue
            start, end = target.segment()
            if capsule_visible(fight.match.collision_world, origin, start, end, target.radius):
                candidates.append(target)
        return candidates

    def select(self):
        candidates = self.candidates()
        if self.target is not None:
            for target in candidates:
                if target.entity == self.target.entity:
                    return target
        return min(candidates, key=self.candidate_order, default=None)

    def candidate_order(self, target):
        return math.dist(self.bot.position, target.feet), target.entity

    def change_target(self, target, tick):
        self.retire()
        self.target = target
        if target is None:
            return
        self.world = ControllerWorld(self)
        self.world.bind(target.entity)
        self.world.bind_script_component(target.entity, None)
        self.component = runtime.Component(self.entity, world=self.world, strict=True)
        self.component.frame = tick - 1
        self.component.now = (tick - 1) * self.component.frame_ms
        self.world.bind_script_component(self.entity, self.component)
        self.component.set(1650, expr.Entity(target.entity))
        self.instance = self.component.create(self.definition)

    def retire(self):
        try:
            if self.instance is not None:
                self.component.destroy(self.instance)
        except runtime.UnsupportedScriptError:
            # Even an unsupported exit cannot retain a dead body's scene bindings.
            pass
        finally:
            if self.world is not None:
                for entity in tuple(self.world.entities):
                    self.world.unbind(entity)
            self.target = self.instance = self.component = self.world = self.mover = None

    def move(self, target, tick):
        bot = self.bot
        match = self.fight.match
        previous = (bot.position, bot.yaw, bot.held)
        if self.mover is None or tuple(self.mover.position) != bot.position:
            self.mover = Mover(bot.position, round(bot.yaw), match.collision_world,
                               mover_data(bot.body), (TEAM0, TEAM1)[bot.team])
        else:
            self.mover.use(world=match.collision_world)
        dx, dz = target.feet[0] - bot.position[0], target.feet[2] - bot.position[2]
        yaw = bots.heading(dx, dz) if dx or dz else round(bot.yaw)
        forward = bots.FORWARD if math.hypot(dx, dz) > STANDOFF and not bot.attack_input else 0
        self.mover.step_pose(Command(tick, yaw=yaw, forward=forward), self.component.frame_ms / 1000, tick)
        bot.position = tuple(self.mover.position)
        bot.velocity, bot.yaw = self.mover.velocity, float(self.mover.yaw)
        bot.held = (0, forward)
        if forward:
            bot.last_held, bot.input_tick = bot.held, tick
        if (bot.position, bot.yaw, bot.held) != previous:
            bot.moved_tick = tick

    def stop_movement(self):
        bot = self.bot
        if bot.held != (0, 0):
            bot.moved_tick = self.fight.tick
        bot.held = (0, 0)
        if bot.effect_mover is None:
            bot.velocity = (0.0, 0.0, 0.0)
        self.mover = None

    def apply_input(self, target):
        bot = self.bot
        component = bot.body_script.component
        desired = target.entity if target is not None else None
        if component.world.manual_target != desired:
            assassin.bind(component.world, component, desired)
            bot.attack_input = False
        bot.attack_target = target.owner.entity if target is not None else None
        pressed = target is not None
        if bot.attack_input != pressed or (assassin.BUTTON in component.held) != pressed:
            component.edges([(assassin.BUTTON, pressed)])
        bot.attack_input = pressed
        bot.firing = pressed
        return pressed

    def update(self, tick):
        target = self.select()
        old = self.target.entity if self.target is not None else None
        if (target.entity if target is not None else None) != old:
            self.apply_input(None)
            self.change_target(target, tick)
        else:
            self.target = target
        if target is None or self.component.fault is not None:
            self.stop_movement()
            return self.apply_input(None)
        self.move(target, tick)
        from ow174.game.script.nodes import AssassinButtonRequest

        try:
            self.component.run_frame(tick)
            node = self.instance.graph.node(12)
            request = self.instance.states.get(node.state)
            bound = self.instance.find_var(expr.INSTANCE, 536, create=False)
            held = (not self.mover.airborne and isinstance(request, AssassinButtonRequest)
                    and request.button_request() == (self.bot.entity, assassin.BUTTON)
                    and bound is not None and expr.to_entity(bound.value()) == target.entity)
        except runtime.UnsupportedScriptError:
            # The controller retains its fault. The Match body is still authoritative.
            held = False
        return self.apply_input(target if held else None)


class TargetWorld(ControllerWorld):
    native_state_misses = ((TARGET_GRAPH, 5, "STU_E834E718"),)

    def __init__(self, host):
        super().__init__(host)
        self.candidates = ()
        self.admitted = 0
        self.windup_target = 0

    def update_candidates(self, candidates):
        entities = tuple(target.entity for target in candidates)
        for entity in self.candidates:
            if entity not in entities:
                self.unbind(entity)
        for entity in entities:
            if entity not in self.entities:
                self.bind(entity)
                self.bind_script_component(entity, None)
        self.candidates = entities

    def tracked_targets(self, state):
        if self.selects_ai_behavior(state.instance) and state.node.pos == 4:
            return self.candidates
        return super().tracked_targets(state)

    def entity_filter_record(self, entity):
        if entity in self.candidates:
            return entity_filter.Record(1 | (1 << 18), True, False)
        return None

    def entity_var_slot(self, entity, var, *, create=False):
        if entity == self.host.bot.entity and var in (13605, 13372):
            return self.host.bot.body_script.component.entity_var(var, create=create)
        return super().entity_var_slot(entity, var, create=create)

    def send_game_message_packet(self, instance, node, target, packet):
        if (self.selects_ai_behavior(instance) and node.pos == 2
                and target == self.host.bot.entity and packet.message == ATTACK):
            source = self._game_message_component(instance, node)
            selected = self.host.selected_entity()
            if (packet.caller == source.entity and selected == self.windup_target
                    and selected in self.candidates):
                receiver = self.host.bot.body_script.component
                if receiver.fault is None and message_receive.deliver(receiver, packet):
                    self.admitted = selected
            return
        super().send_game_message_packet(instance, node, target, packet)


class TargetController(Controller):
    """Run the 13C2 target and windup graph with Match candidates and 1157 contact."""

    def end_handoff(self):
        if self.world is None or not self.world.admitted:
            return
        self.world.admitted = 0
        script = self.bot.body_script
        if script is None or script.component.fault is not None:
            return
        for instance in script.component.instances.values():
            if instance.graph.index == TARGET_BODY_GRAPH:
                # Ending the attack animation also exits its movement modifier
                # subgraph and starts the authored recovery wait.
                attack = instance.states.get(instance.graph.node(105).state)
                if attack is not None:
                    attack.end(False)
                break

    def retire(self):
        try:
            self.end_handoff()
        finally:
            super().retire()

    def selected_entity(self):
        slot = self.component.entity_var(1650, create=False)
        return expr.to_entity(slot.value()) if slot is not None else 0

    def update(self, tick):
        from ow174.game.combat import EYE

        previous = self.target.entity if self.target is not None else 0
        candidates = sorted(self.candidates(), key=lambda target: (
            target.entity != previous, *self.candidate_order(target),
        ))
        lost_target = previous and all(target.entity != previous for target in candidates)
        if self.component is not None and (not candidates or lost_target):
            # Start a fresh authored windup for a new target. A previous wait
            # or packet must never authorize input against a different body.
            self.apply_input(None)
            self.retire()
        if not candidates:
            self.stop_movement()
            return False
        if self.component is None:
            self.world = TargetWorld(self)
            self.component = runtime.Component(self.entity, world=self.world, strict=True)
            self.component.frame = tick - 1
            self.component.now = (tick - 1) * self.component.frame_ms
            self.world.bind_script_component(self.entity, self.component)
            self.instance = self.component.create(self.definition)
        self.world.update_candidates(candidates)
        if self.component.fault is None:
            try:
                self.component.run_frame(tick)
                selected = self.selected_entity()
                if self.world.windup_target and self.world.windup_target != selected:
                    self.end_handoff()
                    branch = self.instance.node_state(self.instance.graph.node(15))
                    branch.end(False)
                    if selected:
                        branch.request(runtime.BEGIN)
                        self.component.run_now()
                self.world.windup_target = selected
            except runtime.UnsupportedScriptError:
                pass  # Preserve the diagnostic and release the hosted attack.
        if self.component.fault is not None:
            self.stop_movement()
            return self.apply_input(None)
        selected = self.selected_entity()
        target = next((candidate for candidate in candidates if candidate.entity == selected), None)
        self.target = target
        if target is None:
            self.stop_movement()
            return self.apply_input(None)
        self.move(target, tick)
        x, y, z = self.bot.position
        in_range = math.dist((x, y + EYE, z), self.fight._eye(target.owner)) <= assassin.PIN_RANGE
        held = self.world.admitted == selected and in_range and not self.mover.airborne
        return self.apply_input(target if held else None)
