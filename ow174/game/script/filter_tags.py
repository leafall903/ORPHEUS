"""104319 D0EC70/DB2C10 tag collection and DB2FD0 compiled clauses."""

from dataclasses import dataclass

from ow174.game.script import play
from ow174.game.script.entity_tags import Tags


@dataclass(frozen=True)
class Clauses:
    any_tags: tuple = ()  # m_9C7BD8B8, filter+58
    all_tags: tuple = ()  # m_B486603E, filter+60
    no_tags: tuple = ()  # m_8B2A4DF3, filter+68

    def __post_init__(self):
        for values in (self.any_tags, self.all_tags, self.no_tags):
            if type(values) is not tuple:
                raise ValueError('compiled tag clauses must be explicit tuples')
            if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in values):
                raise ValueError('compiled tag clauses contain native uint64 resource IDs')

    def matches(self, tags):
        """Known absent component76 cannot meet positive tag clauses."""
        if not (self.any_tags or self.all_tags or self.no_tags):
            return True
        if tags is not None and type(tags) is not Tags:
            raise ValueError('target-tag component availability unknown')
        if tags is None:
            return not (self.any_tags or self.all_tags)
        def present(value):
            return tags.contains(value & 0xFFFFFFFF)
        return ((not self.any_tags or any(present(value) for value in self.any_tags))
                and all(present(value) for value in self.all_tags)
                and not any(present(value) for value in self.no_tags))


def collect(instance, filter_cfg, *, watcher=None, check=None):
    """D0EC70/DB2C10: ordered collecting interface, nonzero resource IDs, first occurrence."""
    fields = ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3')
    groups = []
    with instance.component.operation():
        for field in fields:
            keys = []
            values = play._iter_values(instance, filter_cfg.get(field), watcher=watcher, check=check)
            try:
                for value in values:
                    instance.component.consume_work('filter tag resource collection')
                    key = play._asset(value)
                    if key and key not in keys:
                        keys.append(key)
            finally:
                values.close()
            groups.append(tuple(keys))
    return Clauses(*groups)
