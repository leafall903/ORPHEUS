"""104319 D51FC0 persistent receiver parameter caches, with ordered pair rows."""

from ow174.game.script import message_scope, play
from ow174.game.script.graph import guid as parse_guid


def _unsupported(receiver, feature):
    receiver.instance.component.unsupported(receiver.instance, feature, receiver.node.cls, receiver.node)


def collect(receiver, senders, mode, *, previous=(), stacked=False):
    """Build cache rows; variable application and live binding getters are separate.

    Mode0 uses one selected sender and preserves an unmatched stacked cache.
    Mode1 collects generic iterator pairs; mode2 appends one value per sender.
    Keyed native type12 storage remains an unsupported upstream VM service; this
    collector retains keys exposed by the iterator API instead of projecting them.
    """
    component = receiver.instance.component
    if type(mode) is not int or mode not in (0, 1, 2) or type(stacked) is not bool:
        _unsupported(receiver, 'message receiver parameter collection mode')
    rows = receiver.node.field('m_params') or ()
    if type(rows) not in (tuple, list) or type(senders) not in (tuple, list):
        _unsupported(receiver, 'message receiver parameter or sender array')
    with component.operation():
        if mode == 0:
            cache = [list(previous[index]) if stacked and index < len(previous) else [(0, None)]
                     for index in range(len(rows))]
        else:
            cache = [[] for _ in rows]
        sources = senders
        if mode == 0 and len(sources) > 1:
            _unsupported(receiver, 'single message receiver has multiple selected senders')
        for sender in sources:
            component.consume_work('message receiver selected sender')
            if sender is None:
                continue
            message_scope.check(receiver, sender)
            for index, row in enumerate(rows):
                component.consume_work('message receiver parameter cache row')
                if row is None:
                    continue
                if not isinstance(row, dict):
                    _unsupported(receiver, 'message receiver parameter row')
                raw = row.get('m_B5051BCE')
                key = parse_guid(raw) if raw is not None else 0
                if key is None:
                    _unsupported(receiver, 'message receiver parameter key')
                if not key:
                    continue
                source_row = message_scope.find_parameter(receiver, sender, key)
                if source_row is None:
                    continue
                cfg = source_row.get('m_value')
                with message_scope.evaluation(receiver, sender):
                    if mode == 1:
                        for entry_key, value in play._iter_pairs(sender.instance, cfg, watcher=receiver):
                            message_scope.check(receiver, sender)
                            cache[index].append((0, 0) if value is None else (entry_key, value))
                    else:
                        value = (play.evaluate_value(sender.instance, cfg, initial=0, watcher=receiver)
                                 if cfg is not None else 0)
                        if mode == 0:
                            cache[index].clear()
                            cache[index].append((0, 0 if value is None else value))
                        else:
                            cache[index].append((0, value))
        return tuple(tuple(entries) for entries in cache)
