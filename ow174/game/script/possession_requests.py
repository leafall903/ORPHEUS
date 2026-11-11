"""Ordered component 70 requests produced by the possession States."""

from dataclasses import dataclass

from ow174.game.script import expr


@dataclass(frozen=True)
class Inputs:
    owner: int
    clock: int

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 < self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError("possession request needs a valid owner")
        if type(self.clock) is not int or not 0 <= self.clock <= 0xFFFFFFFF:
            raise ValueError("possession request clock must be uint32")


@dataclass(frozen=True)
class Selection:
    slot: int
    identifier: int
    flag: bool


@dataclass(frozen=True)
class Request:
    owner: int
    clock: int
    kind: int = 2
    priority: float = 9999.0
    above_equal: bool = False
    payload: tuple[float, float] | None = None
    selection: Selection | None = None


class Registry:
    def __init__(self, inputs, *, accepted_selections=NotImplemented):
        if not callable(inputs):
            raise ValueError("possession request inputs must be supplied by the host")
        self.inputs = inputs
        self.accepted_selections = (
            NotImplemented if accepted_selections is NotImplemented else frozenset(accepted_selections)
        )
        self._requests = []
        self._selections = []

    @property
    def requests(self):
        return tuple(self._requests)

    @property
    def selection_requests(self):
        return tuple(self._selections)

    def _list(self, request: Request):
        return self._requests if request.selection is None else self._selections

    def insert(self, request: Request) -> None:
        entries = self._list(request)
        for index, previous in enumerate(entries):
            if (request.priority > previous.priority
                    or (request.above_equal and request.priority == previous.priority)):
                entries.insert(index, request)
                return
        entries.append(request)

    def remove(self, request: Request) -> None:
        entries = self._list(request)
        entries[:] = [previous for previous in entries if previous is not request]

    def clear(self) -> None:
        self._requests.clear()
        self._selections.clear()
