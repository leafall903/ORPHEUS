"""Match inputs for the authored Assassin target producer 13C2."""

import os
from pathlib import Path

from ow174.game import assassin, bots, enforcer_controller
from ow174.game.script import assassin_movement, entity_filter, entity_queries, expr, runtime, scene

GRAPH = 0x13C2
ATTACK = 0x02400000000009C6
PREFERENCE = 0x0240000000000A73
PREFERRED_BODY = 14365


def _selected_entity(component):
    slot = component.entity_var(1650, create=False)
    return expr.to_entity(slot.value()) if slot is not None else 0


def _preferred_entity(component):
    slot = component.entity_var(PREFERRED_BODY, create=False)
    return expr.to_entity(slot.value()) if slot is not None else 0


def _eligible_preference(instance, candidates):
    preferred = _preferred_entity(instance.component)
    if preferred and preferred not in candidates:
        instance.write((expr.ENTITY, PREFERRED_BODY), expr.Entity(0))
        return 0
    return preferred


class TargetWorld(runtime.World):
    def __init__(self, fight, bot, candidates):
        super().__init__(bot.hero, fight.match.game_map.mode_guid)
        self.fight = fight
        self.body = bot.entity
        self.candidates = candidates
        self.attack_target = None
        self.windup_target = 0

    def selects_ai_behavior(self, instance):
        return instance.graph.index == GRAPH

    def tracked_targets(self, state):
        if self.selects_ai_behavior(state.instance) and state.node.pos == 4:
            return tuple(expr.Entity(entity) for entity in self.candidates)
        return super().tracked_targets(state)

    def native(self, cfg, instance):
        if instance is not None and self.selects_ai_behavior(instance):
            if (cfg.get("$") == "STU_0AC15F78"
                    and expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == instance.component.entity):
                return expr.Entity(self.body)
            if cfg is instance.graph.node(13).field("m_condition"):
                return expr.to_entity(instance.evaluate(cfg["m_entity"])) in self.candidates
            if cfg is instance.graph.node(10).field("m_condition"):
                value = instance.evaluate(cfg["m_entity"])
                if expr.to_entity(value) not in self.candidates:
                    return False
                with instance.component.parameter_scope({8: value}):
                    return expr.truthy(instance.evaluate(cfg["m_filter"]["m_condition"]))
        return super().native(cfg, instance)

    def entity_var(self, entity, var):
        if entity == self.body and var in (13605, 13372):
            actors = self.fight.match.bots
        elif var == 14695:
            actors = self.fight.match.players
        else:
            return super().entity_var(entity, var)
        for actor in actors:
            if getattr(actor, "body", None) != entity and getattr(actor, "entity", None) != entity:
                continue
            script = actor.body_script
            if script is not None and script.component.fault is None:
                slot = script.component.entity_var(var, create=False)
                if slot is not None:
                    return slot.value()
        return None

    def send_message(self, target, message, params, sender):
        if (target == self.body and message == ATTACK
                and sender == enforcer_controller._owner(self.body)):
            bot = next((bot for bot in self.fight.match.bots if bot.entity == target and not bot.dead), None)
            if bot is not None and bot.body_script is not None:
                controller = bot.assassin_controller
                entity = _selected_entity(controller)
                preferred = _preferred_entity(controller)
                if (entity == self.windup_target and entity in self.candidates
                        and (preferred not in self.candidates or preferred == entity)
                        and runtime.send_message(bot.body_script.component, message, params, sender)):
                    self.attack_target = entity


class Phase2World(scene.SceneWorld):
    """Read live Match positions and health for a configured 1158 scene."""

    def __init__(self, fight, bot, *, native_state_misses=()):
        super().__init__(bot.hero, fight.match.game_map.mode_guid, native_state_misses=native_state_misses)
        self.fight = fight
        self.body = bot.entity

    sample = NotImplemented
    auto_target = False

    auto_scene = False

    def prepare_scene(self):
        """Bind controller identity and possession for an otherwise empty Match scene."""
        owner = enforcer_controller._owner(self.body)
        self.bind(owner)
        self.bind(self.body)
        self.set_possessions(possessors=(owner,), primary={self.body: owner})
        self.set_component44_body_id(owner, self.body)
        possession = entity_queries.Target(owner, entity_queries.Possession(self.body))
        self.set_entity_query_context(owner, entity_queries.Context(
            owner, True, True, True, lookup={owner: possession}.get,
            watch_environment=None, watch_possession=lambda state, target: None,
        ))
        self.auto_scene = True

    def entity_var_slot(self, entity, var, *, create=False):
        if not self.auto_scene or entity == enforcer_controller._owner(self.body):
            return super().entity_var_slot(entity, var, create=create)
        if create:
            return NotImplemented
        if entity not in self.entities:
            return None
        actor = self._source() if entity == self.body else next((
            player for player in self.fight.match.players
            if player.body == entity and player.has_body and not player.gone
        ), None)
        script = actor.body_script if actor is not None else None
        if script is not None and script.component.fault is None:
            return script.component.entity_var(var, create=False)
        return None

    def selects_ai_behavior(self, instance):
        return instance.graph.index == 0x1158

    def hosts_assassin_pursuit_callbacks(self, entity):
        return False if entity == enforcer_controller._owner(self.body) else NotImplemented

    def assassin_movement_sample(self, entity):
        if entity != self.body:
            return NotImplemented
        if self.sample is not NotImplemented:
            return self.sample
        source = self._source()
        if source is None or self.fight.is_asleep(source) or self.fight.is_stunned(source):
            return None
        # Server projection of the current movement input, scoped to 1158.
        return assassin_movement.Sample(0, source.held[1] / bots.FORWARD)

    def _source(self):
        return next((bot for bot in self.fight.match.bots if bot.entity == self.body and not bot.dead), None)

    def entity_filter_record(self, entity):
        if entity not in self.entities:
            return None
        source = self._source()
        if source is None:
            return None
        # Reconstructed classification for the two 1158 include masks.
        if any(target.entity == entity and getattr(target.owner, "client", None) is not None
               and not target.owner.gone for target in self.fight._enemies(source)):
            return entity_filter.Record(1 | (1 << 18), True, False)
        return None

    def target_record_scalar(self, owner, target):
        if owner != enforcer_controller._owner(self.body):
            return NotImplemented
        source = self._source()
        if source is None:
            return None
        return float(assassin.contact(self.fight, source, target) is not None)

    def position(self, entity):
        if entity == enforcer_controller._owner(self.body):
            entity = self.body
        if entity not in self.entities and entity != self.body:
            return None
        return self.fight.match.script_body_position(entity, self.body)

    def body_health(self, entity):
        if entity not in self.entities:
            return None
        return self.fight.body_health(entity)

    def native(self, cfg, instance):
        if instance is not None and instance.graph.index == 0x1158 and cfg.get("$") == "STU_71A87E92":
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            point = self.position(entity)
            return point if point is not None else expr.EVALUATION_FAILED
        return super().native(cfg, instance)


def phase2_definition():
    configured = os.environ.get(enforcer_controller.SUPPLEMENT)
    if not configured:
        raise ValueError("Assassin controller supplement is required")
    return enforcer_controller._definition(Path(configured).resolve(), 0x1158)


def mount_phase2(fight, bot, world, *, target=None):
    """Attach strict 1158 to a live Assassin with an explicit scene provider."""
    if (bot.hero != assassin.HERO or bot not in fight.match.bots or bot.dead
            or bot.body_script is None or not bot.spawner.get("acquire_targets")
            or getattr(bot, "assassin_controller", None) is not None):
        raise ValueError("expected a live Assassin with acquisition and no mounted controller")
    configured = os.environ.get(enforcer_controller.SUPPLEMENT)
    if not configured:
        raise ValueError("Assassin controller supplement is required")
    definition = enforcer_controller._definition(Path(configured).resolve(), 0x1158)
    owner = enforcer_controller._owner(bot.entity)
    if isinstance(world, Phase2World):
        if world.fight is not fight or world.body != bot.entity:
            raise ValueError("Assassin scene belongs to a different Match body")
        if not world.entities:
            world.prepare_scene()
        if world.auto_scene and target and target not in world.entities:
            world.bind(target)
    if world.script_components.get(owner) is not None:
        raise ValueError("Assassin controller scene owner is already bound")
    component = runtime.Component(owner, world=world, strict=True)
    component.frame = fight.tick - 1
    component.now = component.frame * component.frame_ms
    if isinstance(world, Phase2World):
        world.auto_target = target is None
    elif target is None:
        raise ValueError("automatic target selection requires a Match 1158 scene")
    component.set(1650, expr.Entity(target or 0))
    instance = component.create(definition)
    world.bind_script_component(component.entity, component)
    bot.assassin_controller = component
    return instance


def _select_phase2_target(controller, candidates):
    """Keep a valid target, and retire its graph before using a new body."""
    world = controller.world
    current = _selected_entity(controller)
    eligible = candidates if world.auto_scene else [item for item in candidates if item[1] in world.entities]
    selected = current if any(item[1] == current for item in eligible) else (
        min(eligible, key=lambda item: item[:2])[1] if eligible else 0
    )
    if selected != current:
        if expr.valid_entity(current):
            instance, = controller.instances.values()
            definition = instance.graph
            controller.destroy(instance)
            if world.auto_scene:
                world.unbind(current)
            controller.create(definition)
        if selected and world.auto_scene:
            world.bind(selected)
        controller.set(1650, expr.Entity(selected))
    return selected


def apply_phase2_input(fight, bot, candidates):
    """Consume a mounted strict 1158 controller through the existing Match input owner."""
    controller = getattr(bot, "assassin_controller", None)
    if controller is None or not controller.strict:
        return False

    from ow174.game.script.nodes import AssassinButtonRequest

    world = controller.world
    auto_target = isinstance(world, Phase2World) and world.auto_target
    if controller.fault is None:
        try:
            has_target = not auto_target or bool(_select_phase2_target(controller, candidates))
            if has_target and controller.frame < fight.tick:
                controller.run_frame(fight.tick)
        except runtime.UnsupportedScriptError:
            # The strict controller retains its diagnostic. Release its input
            # without retiring the body combat graph.
            pass
    player = None
    if controller.fault is None:
        instance = next((row for row in controller.instances.values() if row.graph.index == 0x1158), None)
        if instance is not None:
            node = instance.graph.node(12)
            request = instance.states.get(node.state) if node is not None else None
            if (isinstance(request, AssassinButtonRequest)
                    and request.button_request() == (bot.entity, assassin.BUTTON)):
                slot = instance.find_var(expr.INSTANCE, 536, create=False)
                entity = expr.to_entity(slot.value()) if slot is not None else 0
                target = assassin.contact(fight, bot, entity) if (
                    not auto_target or entity == _selected_entity(controller)
                ) else None
                if target is not None:
                    player = target.owner
    assassin.acquire(fight, bot, player)
    return True


def select(fight, bot, candidates):
    """Return the selected candidate and whether its authored handoff was queued."""
    ordered = sorted(candidates, key=lambda item: item[:2])
    configured = os.environ.get(enforcer_controller.SUPPLEMENT)
    if not configured:
        return (ordered[0], True) if ordered else (None, False)
    entities = tuple(candidate[1] for candidate in ordered)
    component = getattr(bot, "assassin_controller", None)
    if component is None:
        definition = enforcer_controller._definition(Path(configured).resolve(), GRAPH)
        world = TargetWorld(fight, bot, entities)
        component = runtime.Component(enforcer_controller._owner(bot.entity), world=world)
        bot.assassin_controller = component
        component.frame = fight.tick - 1
        component.now = component.frame * component.frame_ms
        component.create(definition)
    elif component.world.candidates != entities:
        world = component.world
        world.candidates = entities
        instance, = component.instances.values()
        _eligible_preference(instance, entities)
        previous = _selected_entity(component)
        if previous and previous not in entities:
            world.attack_target = None
            world.windup_target = 0
            # Retire the old Wait6 before the tracker can publish a replacement.
            branch = instance.node_state(instance.graph.node(15))
            if branch is not None:
                branch.end(False)
        tracker = instance.node_state(instance.graph.node(4))
        if previous not in entities and tracker is not None and not tracker.active:
            tracker.request(runtime.BEGIN)
    if component.frame < fight.tick:
        component.run_frame(fight.tick)
    else:
        component.run_now()
    instance, = component.instances.values()
    world = component.world
    entity = _selected_entity(component)
    preferred = _eligible_preference(instance, entities)
    if preferred and preferred != entity:
        # A second valid A73 does not toggle node 13's boolean. Run its authored
        # selection action for the new body even if the condition stayed true.
        instance.run_node(instance.graph.node(9), None)
        component.run_now()
        entity = _selected_entity(component)
    if world.windup_target and world.windup_target != entity:
        # Node 15 can be re-entered while active. Its previous wait must never
        # authorize the newly published body.
        branch = instance.node_state(instance.graph.node(15))
        if branch is not None:
            branch.end(False)
            if entity in entities:
                branch.request(runtime.BEGIN)
        component.run_now()
    world.windup_target = entity if entity in entities else 0
    if world.attack_target != entity:
        world.attack_target = None
    selected = next((candidate for candidate in ordered if candidate[1] == entity), None)
    return selected, selected is not None and world.attack_target == entity
