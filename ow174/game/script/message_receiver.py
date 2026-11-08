"""104319 persistent receiving State with explicitly supplied engine policies."""

from dataclasses import dataclass

from ow174.game.script import (
    array_actions,
    expr,
    message_bindings,
    message_manager,
    message_parameters,
    message_scope,
    message_state,
    play,
)
from ow174.game.script.graph import guid as parse_guid
from ow174.game.script.runtime import CHANGED, Event

RECEIVE, IDLE = 'm_452862E6', 'm_33DA5163'


@dataclass(frozen=True)
class Policy:
    mode: bool
    instance_flag3: bool
    state_mode: bool
    local: bool
    world_a: bool
    world_b: bool
    authoritative: bool
    instance_flag19: bool
    skip_application: bool
    deferred_begin: bool


def _unsupported(state, feature):
    message_state._unsupported(state, 'message receiver ' + feature)


def _context(state, *, cleanup=False):
    return message_state._context(state, cleanup=cleanup)


def _policy(state, context, *, cleanup=False):
    if not callable(context.receiver_policy):
        _unsupported(state, 'engine policy service')
    value = context.receiver_policy(state)
    _context(state, cleanup=cleanup)
    if type(value) is not Policy or any(type(item) is not bool for item in vars(value).values()):
        _unsupported(state, 'engine policy')
    return value


def _mode(state):
    value = state.node.field('m_39B47960')
    value = 0 if value is None else value
    if type(value) is not int or value not in (0, 1, 2):
        _unsupported(state, 'parameter mode')
    return value


def _rows(state):
    rows = state.node.field('m_params') or ()
    if type(rows) not in (tuple, list) or any(row is not None and not isinstance(row, dict) for row in rows):
        _unsupported(state, 'parameter rows')
    return rows


def _secondary(policy):
    return policy.mode and (policy.state_mode or not policy.instance_flag3)


def _tick(state, enabled):
    if enabled:
        state.ticks()
    elif state in state.instance.ticking:
        state.instance.ticking.remove(state)


def native_dependency(state):
    state.polled = True
    state.message_last_epoch = state.message_epoch


def _current(state):
    return state.polled and state.message_last_epoch == state.message_epoch


def _advance(state):
    state.message_epoch = (state.message_epoch + 1) % 255


def notify(state):
    """Host notification adapter: queue dirtiness at the receiving VM's clock."""
    _context(state)
    if state.active and not state.ending and not state.flags_changed:
        state.flags_changed = True
        state.instance.post(Event(state.now, 0, CHANGED, state=state))


def _key(state, entity=None):
    return message_manager.Key(state.instance.component.entity if entity is None else entity,
                               state.message_flags, state.message_key)


def _remove(state, context):
    if state.message_key:
        with context.host.use(context) as manager:
            for entity in (state.instance.component.entity, 0):
                manager.unregister_receiver(_key(state, entity), state)
                _context(state, cleanup=True)
        # D51420 clears the entire qword, including channel flags248.
        state.message_key = state.message_flags = 0


def _predicate(state, context):
    cfg = state.node.field('m_filter')
    if cfg is None:
        return None

    def accept(record):
        sender = record.state
        message_scope.check(state, sender)
        with state.instance.component.parameter_scope(message_scope.ParamScope(state, sender)):
            result = message_state._call(state, context, context.receiver_filter, state, sender, cfg)
            message_scope.check(state, sender)
        if type(result) is not bool:
            _unsupported(state, 'compiled filter result')
        return result

    return accept


def _generation(state, context):
    value = message_state._call(state, context, context.receiver_generation, state)
    if type(value) is not int or not -0x80000000 <= value <= 0x7FFFFFFF:
        _unsupported(state, 'selection generation')
    return value


def _select(state, context):
    generation = _generation(state, context)
    if generation != state.message_generation:
        with context.host.use(context) as manager:
            result = manager.select(_key(state), predicate=_predicate(state, context),
                                    count_matches=state.node.field('m_82B03EC1') is not None)
        _context(state)
        state.message_selected = result.state
        if result.matches is not None:
            state.message_count = result.matches
        state.message_generation = generation
    if state.message_selected is not None:
        message_scope.check(state, state.message_selected)
    return state.message_selected


def _cache(state, context):
    mode = _mode(state)
    if mode == 0:
        # Native Source vector includes its null selection too.
        sources = (_select(state, context),)
    else:
        with context.host.use(context) as manager:
            sources = manager.collect(_key(state), predicate=_predicate(state, context))
        _context(state)
    state.message_sources = sources
    state.message_cache = message_parameters.collect(state, sources, mode,
        previous=state.message_cache, stacked=state.message_stacked)
    _context(state)


def _message(state):
    value, success = play.evaluate_scalar_result(
        state.instance, state.node.field('m_gameMessage'), watcher=state)
    _context(state)
    return (play._asset(value) if success else 0) & 0xFFFFFFFF


def _identity(state, cfg, sender, *, subject=False):
    if cfg is None:
        return
    old, success = play.evaluate_scalar_result(state.instance, cfg)
    _context(state)
    old = expr.to_entity(old) if success else 0
    value = 0
    if sender is not None:
        with message_scope.evaluation(state, sender):
            value = sender.instance.source_entity()
            if subject:
                value = sender.instance.component.world.game_message_subject(
                    sender.instance, sender.node, value)
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            _unsupported(state, 'source identity')
    if old != value:
        message_bindings.write(state, cfg, expr.Entity(value))
        _context(state)


def _enter(state, path):
    state.instance.follow(state.node, path, state)
    _context(state)


def _exit(state, path, *, cleanup=False):
    state.instance.exit_subgraph(state.node, path)
    _context(state, cleanup=cleanup)


def _apply(state, policy, sender, force):
    stacked, previous = _current(state), state.message_sender
    owner = sender.instance.component.entity if sender is not None else 0
    if not force and stacked == state.message_stacked and owner == previous:
        return
    old_stacked = state.message_stacked
    if stacked:
        state.message_stacked = True
        for index, row in enumerate(_rows(state)):
            state.instance.component.consume_work('message receiver output binding')
            if row is not None:
                message_bindings.bind(state, index, row.get('m_out_Var'))
                _context(state)
    else:
        if old_stacked or expr.valid_entity(previous) or expr.valid_entity(owner):
            for index, row in enumerate(_rows(state)):
                state.instance.component.consume_work('message receiver output assignment')
                pairs = state.message_cache[index]
                if len(pairs) == 1 and not array_actions._key(pairs[0][0]) and _mode(state) == 0:
                    value = pairs[0][1]
                    if value is not None:
                        if old_stacked:
                            message_bindings.remove(state, index, value=value)
                        else:
                            if row is None:
                                _unsupported(state, 'null assignment row')
                            message_bindings.write(state, row.get('m_out_Var'), value)
                    elif old_stacked:
                        message_bindings.remove(state, index)
                else:
                    if row is None:
                        _unsupported(state, 'null array assignment row')
                    if old_stacked:
                        message_bindings.remove(state, index)
                    message_bindings.array(state, row.get('m_out_Var'), pairs)
                _context(state)
        state.message_stacked = False
    if previous != owner:
        state.message_sender = owner
        if policy.authoritative or policy.instance_flag19 or (policy.mode and policy.state_mode):
            message_bindings.write(state, state.node.field('m_B60ABDA4'), expr.Entity(owner))
            _context(state)
            old_valid, valid = expr.valid_entity(previous), expr.valid_entity(owner)
            if old_valid and valid:
                if message_state._byte(state, state.node.field('m_586B4905')):
                    _exit(state, RECEIVE)
                    _enter(state, RECEIVE)
            elif old_valid:
                _exit(state, RECEIVE)
                _enter(state, IDLE)
            elif valid:
                _exit(state, IDLE)
                _enter(state, RECEIVE)
    message_bindings.write(state, state.node.field('m_82B03EC1'), state.message_count)
    _context(state)
    # EntityInstance's post-update virtualE0 is a verified null callback.


def refresh(state, *, force=True):
    if state.message_updating:
        return
    component = state.instance.component
    with component.operation():
        context = _context(state)
        state.message_updating = True
        try:
            _advance(state)
            message = _message(state)
            if _current(state):
                _tick(state, True)
                _advance(state)
            else:
                _tick(state, False)
            if message != state.message_key:
                _remove(state, context)
                state.message_key = message
                if message:
                    with context.host.use(context) as manager:
                        for entity in (component.entity, 0):
                            manager.register_receiver(_key(state, entity), state)
                            _context(state)
            state.message_generation = -1
            sender = _select(state, context)
            state.message_refresh = _current(state)
            if state.message_refresh:
                _tick(state, True)
                _advance(state)
            _identity(state, state.node.field('m_FF703F20'), sender)
            _identity(state, state.node.field('m_44D0D060'), sender, subject=True)
            _cache(state, context)
            if _current(state):
                _tick(state, True)
            policy = _policy(state, context)
            if not policy.skip_application:
                _apply(state, policy, sender, force)
        except ValueError:
            _unsupported(state, 'manager or context lifecycle')
        finally:
            state.message_generation = -1
            state.message_updating = False


def begin(state):
    context = _context(state)
    state.message_sender, state.message_stacked, state.message_key = 0, False, 0
    policy = _policy(state, context)
    secondary = _secondary(policy)
    state.message_flags = ((8 if secondary else 4) if policy.local else 16 if policy.world_a else
                           32 if policy.world_b else 2 if secondary else 1)
    state.message_selected, state.message_generation, state.message_count = None, -1, 0
    if not state.message_cache:
        state.message_cache = tuple(((0, None),) for _ in _rows(state))
    state.message_refresh = state.message_updating = state.message_getting = False
    if not policy.deferred_begin:
        refresh(state)
        if (not expr.valid_entity(state.message_sender)
                and (policy.authoritative or policy.instance_flag19 or secondary)):
            _enter(state, IDLE)


def tick(state):
    context = _context(state)
    with state.instance.component.operation():
        try:
            if _message(state) != state.message_key:
                refresh(state)
            elif state.message_refresh:
                refresh(state, force=False)
            else:
                _cache(state, context)
        finally:
            state.message_generation = -1


def output(state, slot):
    with state.instance.component.operation():
        context = _context(state)
        rows = _rows(state)
        if type(slot) is not int or not 0 <= slot < len(rows):
            _unsupported(state, 'output slot')
        if _mode(state) != 0 and state in state.instance.ticking:
            pairs = state.message_cache[slot]
            if any(array_actions._key(key) for key, _ in pairs):
                _unsupported(state, 'keyed type12 output array')
            return tuple(value for _, value in pairs)
        if state.message_getting:
            return expr.EVALUATION_FAILED
        sender = _select(state, context)
        row = rows[slot]
        raw = row.get('m_B5051BCE') if row is not None else None
        key = parse_guid(raw) if raw is not None else 0
        if key is None:
            _unsupported(state, 'output parameter key')
        if sender is None or not key:
            return expr.EVALUATION_FAILED
        state.message_getting = True
        try:
            source = message_scope.find_parameter(state, sender, key)
            if source is None or source.get('m_value') is None:
                return expr.EVALUATION_FAILED
            with message_scope.evaluation(state, sender):
                return play.evaluate_value(sender.instance, source['m_value'], initial=expr.EVALUATION_FAILED)
        finally:
            state.message_getting = False
            state.message_generation = -1


def cleanup(state):
    context = state.message_context
    if context is None or context.retired:
        abandon(state)
        return
    context = _context(state, cleanup=True)
    try:
        _remove(state, context)
        policy = _policy(state, context, cleanup=True)
        if policy.authoritative or policy.instance_flag19 or _secondary(policy):
            if state.message_stacked:
                for index, row in enumerate(_rows(state)):
                    if row is None:
                        _unsupported(state, 'null cleanup row')
                    if row.get('m_out_Var') is not None:
                        message_bindings.remove(state, index)
                state.message_stacked = False
            _exit(state, RECEIVE, cleanup=True)
            _exit(state, IDLE, cleanup=True)
    except ValueError:
        _unsupported(state, 'cleanup lifecycle')


def abandon(state, *, retired=False):
    context = state.message_context
    if context is not None:
        context.host.manager.discard_state(state)
        context.states.discard(state)
        if not retired:
            state.message_context = None
    message_bindings.abandon(state)
