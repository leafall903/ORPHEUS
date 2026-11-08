"""9D7BF987's declarative variables must agree with compiled synchronization metadata.

Its 104319 runtime allocation is the common State; no config is evaluated and
no variable is created or assigned by this data-only declaration.
"""

from ow174.game.script import expr


def validate(instance, node):
    def unsupported(feature):
        instance.component.unsupported(instance, feature, node.cls, node)

    def rows(field):
        value = node.field(field)
        if value is None:
            return ()
        if type(value) not in (list, tuple):
            unsupported('shared variable list')
        return value

    declared = set()
    for cfg in rows('m_6F0C0343'):
        instance.component.consume_work('shared variable declaration')
        if (type(cfg) is not dict or cfg.get('$') != 'STUConfigVarDynamic'
                or type(cfg.get('m_identifier')) is not int
                or not 0 < cfg['m_identifier'] < 1 << 16
                or type(cfg.get('m_60DB8F99', 0)) is not int
                or cfg.get('m_60DB8F99', 0) not in (0, 1)):
            unsupported('shared variable declaration')
        target = (int(bool(cfg.get('m_60DB8F99', 0))), cfg['m_identifier'])
        declared.add(target)
    for field in ('m_BF5B22B7', 'm_8BF03679'):
        compiled = set()
        for row in rows(field):
            instance.component.consume_work('shared variable compiled declaration')
            if type(row) is not dict or row.get('$') != 'STUStatescriptRemoteSyncVar':
                unsupported('shared variable compiled metadata')
            scope, raw = row.get('m_56341592'), row.get('m_0D09D2D9')
            try:
                guid = expr.guid_of(raw)
            except (ValueError, TypeError):
                unsupported('shared variable compiled metadata')
            if (type(scope) is not int or scope not in (0, 1) or guid >> 48 != 0x0D80
                    or not 0 < guid & 0xFFFFFFFFFFFF < 1 << 16):
                unsupported('shared variable compiled metadata')
            compiled.add((scope, guid & 0xFFFF))
        if compiled != declared:
            unsupported('shared variable compiled metadata')
    graph_vars = {(entry.scope, entry.var) for entry in instance.graph.sync_vars}
    if not declared <= graph_vars:
        unsupported('shared variable graph metadata')
