"""104319 entity queries with explicit native lookup services.

The owner supplies the active namespace/environment and both lookup registries.
Scene bindings and the reverse primary-possessor catalog do not imply these inputs.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph, play

EXISTS = 'STU_E72F6348'
POSSESSED = 'STU_0AC15F78'
POSSESSOR = 'STU_F7262B36'
BODY = 'STU_00707E1E'
LINKED = INDEXED_TARGET = 'STU_A37A910D'
PARTICIPANT = 'STU_0340A42A'
DEFINITION = 'STU_1A3FA5B0'
TARGET_RECORD_SCALAR = 'STU_D379F515'
COMPONENT76_KEY = 'STU_25ECB55F'


@dataclass(frozen=True)
class Component76Keys:
    primary: frozenset[int] = frozenset()
    fallback: frozenset[int] = frozenset()
    suppressed: frozenset[int] = frozenset()

    def __post_init__(self):
        for entries in (self.primary, self.fallback, self.suppressed):
            if type(entries) is not frozenset or any(
                    type(key) is not int or not 0 <= key <= 0xFFFFFFFF for key in entries):
                raise ValueError("component76 keys must be uint32 sets")

    def contains(self, key: int) -> bool:
        return key not in self.suppressed if key in self.primary else key in self.fallback


@dataclass
class Possession:
    current: int

    def __post_init__(self):
        if type(self.current) is not int or not 0 <= self.current <= 0xFFFFFFFF:
            raise ValueError('current possession must be a native uint32 identity')


@dataclass
class BodyField:
    entity: int

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('component44 body field must be a native uint32 identity')


@dataclass(frozen=True)
class Target:
    entity: int
    possession: object = NotImplemented  # None declares component44 absence.
    possessor: object = NotImplemented  # None declares component43 absence.
    body_field: object = NotImplemented  # None declares component44 absence.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('query target must have a native uint32 identity')
        if (self.possession is not NotImplemented and self.possession is not None
                and type(self.possession) is not Possession):
            raise ValueError('expected current possession or known component44 absence')
        if (self.possessor is not NotImplemented and self.possessor is not None
                and type(self.possessor) is not Possession):
            raise ValueError('expected primary possessor or known component43 absence')
        if (self.body_field is not NotImplemented and self.body_field is not None
                and type(self.body_field) is not BodyField):
            raise ValueError('expected body field or known component44 absence')


@dataclass
class Context:
    owner: int
    namespace_present: bool
    environment_present: bool
    possession_context_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares a missing dependency hub.
    watch_possession: object = NotImplemented
    retired: bool = False
    lookup_linked: object = NotImplemented  # Callable (entity, selector) -> entity or None.
    lookup_participant: object = NotImplemented  # Callable (entity, watcher) -> resolved entity or None.
    watch_possessor: object = NotImplemented

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid entity-query owner')
        for value in (self.namespace_present, self.environment_present, self.possession_context_present):
            if type(value) is not bool:
                raise ValueError('entity-query context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    """Return NotImplemented only when this world has not installed the service."""
    cls = cfg.get('$')
    if cls in (LINKED, TARGET_RECORD_SCALAR):
        result = _target_query(evaluation, cfg, cls)
        if result is not NotImplemented or cls == TARGET_RECORD_SCALAR:
            return result
    if cls == COMPONENT76_KEY:
        instance, watcher = evaluation.instance, evaluation.watcher
        component = instance.component
        resource = cfg.get("m_88B0050A")
        key = 0 if resource is None else graph.guid(resource)
        if key is None:
            component.unsupported(instance, "component76 resource key", cls)
        value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0), watcher=watcher)
        record = component.world.component76_keys(expr.to_entity(value))
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "component76 query owner retired", cls)
        if record is NotImplemented:
            return NotImplemented
        if record is None:
            return False
        if type(record) is not Component76Keys:
            component.unsupported(instance, "component76 keys", cls)
        return record.contains(key & 0xFFFFFFFF)
    if cls == DEFINITION:
        instance, watcher = evaluation.instance, evaluation.watcher
        component = instance.component
        value, success = play.evaluate_scalar_result(instance, cfg.get("m_entity"), watcher=watcher)
        identifier = expr.to_entity(value) if success else expr.INVALID_ENTITY
        definition = component.world.entity_definition_resource(identifier)
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "entity definition lookup owner retired", cls)
        if definition is NotImplemented:
            return NotImplemented
        if type(definition) is not int or not 0 <= definition <= 0xFFFFFFFFFFFFFFFF:
            component.unsupported(instance, "entity definition resource", cls)
        return expr.Asset(definition)
    if cls not in (EXISTS, POSSESSED, LINKED, PARTICIPANT, POSSESSOR, BODY):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    provider = (component.world.entity_possessor_context if cls == POSSESSOR
                else component.world.entity_query_context)
    context = provider(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, cls)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or provider(component.entity) is not context):
            unsupported('entity query owner retired during callback')
        if any(type(value) is not bool for value in (
                context.namespace_present, context.environment_present, context.possession_context_present)):
            unsupported('entity query context availability')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        value = callback(*args)
        check()
        return value

    def watch_environment(kind):
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, 'entity query environment subscription', watcher, kind)

    check()
    if cls == PARTICIPANT:
        if context.lookup_participant is NotImplemented:
            return NotImplemented
        if not context.namespace_present:
            return expr.EVALUATION_FAILED
        value = play.evaluate_scalar(instance, cfg.get('m_entity'), initial=expr.Entity(0), watcher=watcher)
        check()
        # CF0660 first resolves the queried entity, then searches the
        # participant bindings. The host owns those relationships.
        entity = expr.to_entity(value) & 0xFFFFFFFF
        resolved = call(context.lookup_participant, 'participant entity lookup', entity, watcher)
        if resolved is None:
            watch_environment(0 if context.environment_present else 46)
            return expr.EVALUATION_FAILED
        if type(resolved) is not int or not 0 <= resolved <= 0xFFFFFFFF:
            unsupported('participant entity lookup result')
        return expr.Entity(resolved)

    if cls == LINKED:
        if context.lookup_linked is NotImplemented:
            return NotImplemented
        # CCB2B0 subscribes before reading either operand. The game lookup
        # at 99B9D0 returns zero when the entity or keyed record is absent.
        watch_environment(0x20)
        entity = play.evaluate_scalar(instance, cfg.get('m_152B6961'),
                                      initial=expr.Entity(0), watcher=watcher)
        check()
        selector = play.evaluate_scalar(instance, cfg.get('m_A60150E2'), initial=0, watcher=watcher)
        check()
        linked = call(context.lookup_linked, 'linked entity lookup',
                      expr.to_entity(entity) & 0xFFFFFFFF, expr.to_int(selector) & 0xFFFFFFFF)
        if linked is None:
            return expr.Entity(0)
        if type(linked) is not int or not 0 <= linked <= 0xFFFFFFFF:
            unsupported('linked entity lookup result')
        return expr.Entity(linked)

    if cls == EXISTS:
        if not context.namespace_present:
            return expr.EVALUATION_FAILED
        if not context.environment_present:
            watch_environment(46)
            return False
        watch_environment(0)
    elif cls != BODY and not context.possession_context_present:
        return expr.EVALUATION_FAILED

    # Native +20 selection uses the first config of 06610EBC, then F64C20
    # unwraps only the first member of nested arrays. Failure/non-entity => 0.
    selector = {POSSESSOR: 'm_801B298F', BODY: 'm_00903303'}.get(cls, 'm_entity')
    value = play.evaluate_scalar(instance, cfg.get(selector), initial=expr.Entity(0), watcher=watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    if cls == EXISTS and not expr.valid_entity(identifier):
        return False
    target = call(context.lookup, 'entity query lookup', identifier)
    if target is not None and (type(target) is not Target or target.entity != identifier):
        unsupported('entity query lookup result')
    if cls == EXISTS:
        return target is not None
    if target is None:
        watch_environment(0 if context.environment_present else 46)
        return expr.Entity(0) if cls == BODY else expr.EVALUATION_FAILED
    if cls == BODY:
        field = target.body_field
        if field is None:
            return expr.Entity(0)
        if type(field) is not BodyField:
            unsupported('entity query component44 body field')
        if watcher is not None:
            call(context.watch_possession, 'entity query possession subscription', watcher, target)
        if type(field.entity) is not int or not 0 <= field.entity <= 0xFFFFFFFF:
            unsupported('entity query component44 body field')
        return expr.Entity(field.entity)
    relation = target.possessor if cls == POSSESSOR else target.possession
    if relation is None:
        return expr.Entity(0)
    if type(relation) is not Possession:
        unsupported('entity query primary possessor' if cls == POSSESSOR
                    else 'entity query current possession')
    if watcher is not None:
        callback = context.watch_possessor if cls == POSSESSOR else context.watch_possession
        call(callback, 'entity query possession subscription', watcher, target)
    if type(relation.current) is not int or not 0 <= relation.current <= 0xFFFFFFFF:
        unsupported('entity query current possession')
    return expr.Entity(relation.current)


def _target_query(evaluation, cfg, cls):
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    world = component.world

    def check_owner():
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "target query owner retired", cls)

    def entity_input(field):
        value = play.evaluate_scalar(instance, cfg.get(field), initial=expr.Entity(0), watcher=watcher)
        return expr.to_entity(value)

    if cls == LINKED:
        lookup = world.indexed_target_lookup(component.entity)
        check_owner()
        if lookup is NotImplemented:
            return NotImplemented
        if lookup is None:
            return expr.EVALUATION_FAILED
        if not callable(lookup):
            component.unsupported(instance, "indexed target lookup service", cls)
        owner = entity_input("m_152B6961")
        index = expr.to_int(play.evaluate_scalar(instance, cfg.get("m_A60150E2"), watcher=watcher))
        check_owner()
        target = lookup(owner, index)
        check_owner()
        if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
            component.unsupported(instance, "indexed target identity", cls)
        return expr.Entity(target)

    target = entity_input("m_target")
    owner = entity_input("m_7826B991")
    check_owner()
    value = world.target_record_scalar(owner, target)
    check_owner()
    if value is NotImplemented:
        return NotImplemented
    if value is None:
        return expr.EVALUATION_FAILED
    if type(value) not in (float, int):
        component.unsupported(instance, "target record scalar", cls)
    return expr.f32(value)


@dataclass(frozen=True)
class TaggedCandidate:
    """One candidate with the host's effective uint32 tag keys."""

    entity: int
    tags: frozenset[int]
    suppressed: bool = False


def collect_tagged(instance, node, *, watcher=None, _world_available=NotImplemented):
    """CB8EC0/D3FE60 with explicit world services and no authored filter."""
    component = instance.component
    output = node.field("m_4AEEE6A1")
    if output is None:
        return
    if watcher is not None:
        present = (component.world.tagged_entity_world_available(instance, node)
                   if _world_available is NotImplemented else _world_available)
        _check_tag_owner(instance, node, watcher)
        if type(present) is not bool:
            component.unsupported(instance, "tag query world availability", node.cls, node)
        if not present:
            return
    target = expr.dynamic(output)
    if target is None:
        if isinstance(output, dict) and output.get("$") in expr.CONSTANTS:
            return
        component.unsupported(instance, "tag query output target", node.cls, node)
    if not target[1]:
        return
    variable = instance.find_var(*target)
    if variable.links:
        component.unsupported(instance, "tag query linked output", node.cls, node)
    if node.field("m_filter") is not None:
        component.unsupported(instance, "tag query filter", node.cls, node)
    before = variable.value()
    variable.base = ()
    configs = [node.field(field) for field in ("m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3")]
    if any(cfg is not None for cfg in configs):
        any_tags, all_tags, excluded_tags = (
            frozenset(asset & 0xFFFFFFFF for item in play._iter_values(instance, cfg, watcher=watcher)
                      if (asset := play._asset(item)))
            for cfg in configs
        )
        candidates = component.world.tagged_entity_candidates(instance, node)
        _check_tag_owner(instance, node, watcher)
        if type(candidates) is not tuple:
            component.unsupported(instance, "tag query enumeration", node.cls, node)
        matches = []
        for candidate in candidates:
            component.consume_work("tag query candidate")
            if type(candidate) is not TaggedCandidate:
                component.unsupported(instance, "tag query candidate", node.cls, node)
            tags = candidate.tags
            if (not candidate.suppressed and (not any_tags or any_tags & tags)
                    and all_tags <= tags and not excluded_tags & tags):
                matches.append(expr.Entity(candidate.entity))
        variable.base = tuple(matches)
    instance.changed(variable, before)


def _check_tag_owner(instance, node, watcher):
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or component.instances.get(instance.id) is not instance
            or (watcher is not None and not watcher.active)):
        component.unsupported(instance, "tag query owner retired", node.cls, node)
