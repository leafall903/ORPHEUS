"""104319 A6B80C63 collected fold, scalar1B77D80/provider1B76A60.

The native evaluator collects before installing its temporary parameter scope.
Child evaluation preserves its destination on failure; the fold itself succeeds.
"""

from dataclasses import dataclass

from ow174.game.script import play

CLASS = "STU_A6B80C63"


@dataclass
class Scope:
    values: tuple
    accumulator: object = None
    index: int = 0

    def lookup_parameter(self, identifier, *, initial=None):
        if type(identifier) is not int:
            return False, initial
        if identifier == 0xC:
            return True, self.index
        if identifier == 0x41A:
            return True, len(self.values)
        if identifier == 0x50D:
            return True, self.accumulator
        if identifier == 0x50E and 0 <= self.index < len(self.values):
            return True, self.values[self.index]
        return False, initial


def evaluate(instance, cfg, *, initial=None, watcher=None):
    component = instance.component
    with component.operation():
        # Host bound, not a native gameplay rule. This also bounds recursive
        # container configs before any provider has been pushed.
        if component._operation_depth > play.MAX_CONFIG_DEPTH:
            component.budget_exceeded("config fold depth budget exhausted")
        component.consume_work("config fold collection")
        values = tuple(play._iter_values(instance, cfg.get("m_814F8EC3"), watcher=watcher))
        scope = Scope(values)
        with component.parameter_scope(scope):
            if values and (config := cfg.get("m_418D9D9D")) is not None:
                scope.accumulator = play.evaluate_scalar(instance, config, initial=scope.accumulator,
                                                        watcher=watcher)
            for index in range(len(values)):
                component.consume_work("config fold reduction")
                scope.index = index
                if (config := cfg.get("m_C239F989")) is not None:
                    scope.accumulator = play.evaluate_scalar(instance, config, initial=scope.accumulator,
                                                            watcher=watcher)
            scope.index = len(values)
            if (config := cfg.get("m_8054375D")) is not None:
                return play.evaluate_scalar(instance, config, initial=initial, watcher=watcher)
            return scope.accumulator
