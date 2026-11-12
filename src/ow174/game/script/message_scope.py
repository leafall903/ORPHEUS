"""104319 D9B0F0 persistent sender parameter provider, separate from packets."""

from contextlib import contextmanager
from dataclasses import dataclass

from ow174.game.script import expr, message_state, play
from ow174.game.script.graph import guid as parse_guid
from ow174.game.script.runtime import ScriptBudgetError, UnsupportedScriptError

PARAMETER_TYPE = 0x0A40000000000000


def parameter_key(identifier):
    if type(identifier) is not int or not 0 <= identifier <= 0xFFFF:
        return None
    return (PARAMETER_TYPE | identifier) if identifier else 0


def _unsupported(receiver, feature):
    receiver.instance.component.unsupported(receiver.instance, feature, receiver.node.cls, receiver.node)


def check(receiver, sender):
    contexts = []
    for state in (receiver, sender):
        instance, component = state.instance, state.instance.component
        component.ensure_valid()
        context = component.world.message_state_context(component.entity, component=component)
        if (type(context) is not message_state.Context or context.component is not component
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or instance.states.get(state.index) is not state
                or getattr(state, 'message_context', None) not in (None, context)):
            _unsupported(receiver, 'message parameter source or receiver lifecycle')
        try:
            context.check()
        except ValueError:
            _unsupported(receiver, 'message parameter owner context')
        contexts.append(context)
    if contexts[0].host is not contexts[1].host:
        _unsupported(receiver, 'message parameter manager ownership')


@contextmanager
def evaluation(receiver, sender):
    """Source-instance evaluation, charged to the receiver's enclosing operation."""
    receiving, sending = receiver.instance.component, sender.instance.component
    with receiving.operation():
        check(receiver, sender)
        if receiving is sending:
            yield
            check(receiver, sender)
            return
        previous, nested = sending._work_left, sending._operation_depth > 0
        with sending.operation():
            allowance = min(sending._work_left, receiving._work_left)
            sending._work_left = allowance
            try:
                yield
                check(receiver, sender)
            except (ScriptBudgetError, UnsupportedScriptError) as error:
                if receiving.fault is None:
                    if isinstance(error, ScriptBudgetError):
                        receiving.budget_exceeded('message parameter source exhausted the shared budget')
                    _unsupported(receiver, 'message parameter source evaluation failed')
                raise
            finally:
                consumed = max(0, allowance - sending._work_left)
                receiving._work_left -= consumed
                if nested:
                    sending._work_left = previous - consumed
                if receiving._work_left < 0 and receiving.fault is None:
                    receiving.budget_exceeded('cross-component message parameter budget exhausted')


def find_parameter(receiver, sender, key):
    for row in sender.node.field('m_params') or ():
        receiver.instance.component.consume_work('message State parameter key matching')
        if row is None:
            continue
        if not isinstance(row, dict):
            _unsupported(receiver, 'message State parameter row')
        raw = row.get('m_B5051BCE')
        candidate = parse_guid(raw) if raw is not None else 0
        if candidate is None:
            _unsupported(receiver, 'message State parameter key')
        if candidate == key:
            return row
    return None


@dataclass(frozen=True)
class ParamScope:
    receiver: object
    sender: object

    def lookup_parameter(self, identifier, *, initial=None):
        key = parameter_key(identifier)
        return (False, initial) if key is None else self.lookup_guid(key, initial=initial)

    def lookup_guid(self, key, *, initial=None):
        receiver, sender = self.receiver, self.sender
        with receiver.instance.component.operation():
            check(receiver, sender)
            if key == PARAMETER_TYPE + 9:
                return True, expr.Entity(sender.instance.component.entity)
            if key in (PARAMETER_TYPE + 10, PARAMETER_TYPE + 11):
                with evaluation(receiver, sender):
                    source = sender.instance.source_entity()
                    if key == PARAMETER_TYPE + 11:
                        source = sender.instance.component.world.game_message_subject(
                            sender.instance, sender.node, source)
                    if type(source) is not int or not 0 <= source <= 0xFFFFFFFF:
                        _unsupported(receiver, 'message State parameter source identity')
                return True, expr.Entity(source)
            row = find_parameter(receiver, sender, key)
            if row is None:
                return True, 0
            cfg = row.get('m_value')
            if cfg is None:
                return False, initial
            with evaluation(receiver, sender):
                value, success = play.evaluate_scalar_result(
                    sender.instance, cfg, initial=initial, watcher=receiver)
            return success, value
