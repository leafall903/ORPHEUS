"""Explicit normal startup of already-loaded component graphs (104319 BE8E10).

The owner supplies native authority, actual entity creation and world services.
This does not select Match policy, allocate network entities, load resources or
replicate instances. Component-level schemas support authored float literals.
"""

import math

from ow174.game.script import expr, graph, play, runtime


def _rows(schema, *, graph_id=None):
    if schema is None:
        return ()
    if not isinstance(schema, dict) or schema.get('$') not in ('STUStatescriptSchema', 'STU_77990ED7'):
        raise ValueError('unsupported component startup schema')
    reference = schema.get('m_89EAAC40')
    if reference is not None and (graph_id is None or schema['$'] != 'STU_77990ED7'
                                  or expr.guid_of(reference) != graph_id):
        raise ValueError('unsupported component startup schema')
    rows = schema.get('m_entries') or ()
    if not isinstance(rows, (list, tuple)):
        raise ValueError('invalid component startup schema entries')
    return rows


def _graphs(component, field, work):
    rows = component.get(field) or ()
    if not isinstance(rows, (list, tuple)):
        raise ValueError('invalid component startup graph list')
    result = []
    for row in rows:
        work('component startup graph configuration')
        if not isinstance(row, dict) or row.get('$') != 'STUStatescriptGraphWithOverrides':
            raise ValueError('invalid component startup graph configuration')
        if row.get('m_75408640', 0):
            raise ValueError('component startup graph flags are not implemented')
        guid = expr.guid_of(row.get('m_graph'))
        if guid and guid >> 48 != graph.GRAPH_TYPE:
            raise ValueError('component startup resource must be a graph')
        entries = row.get('m_1EB5A024') or ()
        if not isinstance(entries, (list, tuple)):
            raise ValueError('invalid component startup override entries')
        result.append((guid, entries))
    return result


def _queue_rows(instance, rows):
    """BE9AC0: evaluate in the callee, skip absent/failed configs, queue writes."""
    component = instance.component
    for row in rows:
        component.consume_work('component startup schema evaluation')
        if not isinstance(row, dict):
            component.unsupported(instance, 'component startup schema row', str(type(row)))
        key = expr.guid_of(row.get('m_0D09D2D9'))
        config = row.get('m_value')
        # The original checks the full identifier before evaluating its config.
        if not key or config is None:
            continue
        failed = object()
        value = play.evaluate_value(instance, config, initial=failed)
        if value is not failed:
            instance.post(runtime.Event(component.now, 0, runtime.ASSIGN,
                                         assignment=(graph.INSTANCE, key & 0xFFFF, value)))


def _component_values(component, schema):
    """BE98C0 queues owner variables independently of any graph instance."""
    values = []
    for row in _rows(schema):
        component.consume_work('component startup entity schema')
        if not isinstance(row, dict):
            raise ValueError('invalid component startup entity schema row')
        config = row.get('m_value')
        if config is None:
            continue
        value = config.get('m_value') if isinstance(config, dict) else None
        if (type(value) not in (float, int) or config.get('$') != 'STUConfigVarFloat'
                or abs(value) > 3.402823466e38 or not math.isfinite(value)):
            raise ValueError('unsupported component startup entity value')
        key = expr.guid_of(row.get('m_0D09D2D9')) & 0xFFFF
        values.append((key, expr.f32(value)))
    return values


class ComponentStartup:
    """One owner-selected startup of a fresh component, with loaded graph data.

    Definitions create graphs in order. Instance configurations only override
    the first existing matching graph; additional graphs are created afterward.
    Schema evaluation precedes assignment and ordinary Entry execution. New
    instances initialize at the first component update's supplied clock.
    The caller still owns execution, world services and replication/lifetimes.
    """

    def __init__(self, component: runtime.Component, definition: dict,
                 instance_data: dict | None = None, *, graph_loader=graph.graph):
        self.component = component
        self.definition = definition
        self.instance_data = instance_data
        self.graph_loader = graph_loader
        self.created: tuple[runtime.Instance, ...] | None = None

    def start(self) -> tuple[runtime.Instance, ...]:
        component = self.component
        component.ensure_valid()
        if self.created is not None:
            return self.created
        with component.operation():
            return self._start()

    def _start(self) -> tuple[runtime.Instance, ...]:
        component = self.component
        if component.instances:
            raise ValueError('component startup needs a fresh component')
        definition, placed = self.definition, self.instance_data
        if not isinstance(definition, dict) or definition.get('$') != 'STUStatescriptComponent':
            raise ValueError('component startup needs its explicit definition')
        if definition.get('m_clientOnly', 0):
            raise ValueError('client-only component startup policy is not implemented')
        if placed is not None and (not isinstance(placed, dict)
                                   or placed.get('$') != 'STUStatescriptComponentInstanceData'):
            raise ValueError('invalid component startup instance data')
        placed = placed or {}
        values = _component_values(component, definition.get('m_2D9815BA'))
        values.extend(_component_values(component, placed.get('m_2D9815BA')))
        defaults = _graphs(definition, 'm_B634821A', component.consume_work)
        overrides = _graphs(placed, 'm_6D10093E', component.consume_work)
        additional = _graphs(placed, 'm_84D0983B', component.consume_work)
        # Preflight loaded resources before mutating the VM. Unmatched instance
        # overrides do not request resources, matching BEA2D0/1B8D510.
        loaded = {}
        for guid, _ in (*defaults, *additional):
            component.consume_work('component startup resource lookup')
            if guid and guid not in loaded:
                model = self.graph_loader(guid & 0xFFFFFFFFFFFF)
                if not isinstance(model, graph.Graph) or model.index != guid & 0xFFFFFFFFFFFF:
                    raise ValueError(f'component startup graph {guid:016X} is not loaded')
                _rows(model.fields.get('m_publicSchema'), graph_id=guid)
                loaded[guid] = model
        created = []

        def create(guid, rows):
            if not guid:
                return
            component.consume_work('component startup graph allocation')
            instance = component.create(loaded[guid], init=False)
            created.append(instance)
            _queue_rows(instance, _rows(instance.graph.fields.get('m_publicSchema'), graph_id=guid))
            _queue_rows(instance, rows)

        with component.operation():
            for guid, rows in defaults:
                create(guid, rows)
            for guid, rows in overrides:
                for instance in created:
                    component.consume_work('component startup matching graph')
                    if instance.graph.index == guid & 0xFFFFFFFFFFFF and guid:
                        _queue_rows(instance, rows)
                        break
            for guid, rows in additional:
                create(guid, rows)
            # Native 977F20 marks new instances for initialization; BE9DE0 and
            # BEA2D0 queue schemas before that init posts ordinary Entry events.
            # Component's ordinary preparation initializes these at its first
            # update clock. No entry or assignment executes during startup.
            for instance in created:
                instance.initialization_pending = True
            component.startup_entity_values.extend(values)
            self.created = tuple(created)
        return self.created
