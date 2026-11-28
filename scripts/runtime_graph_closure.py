"""Resolve and validate the native graph dependencies of runtime entry points."""


def dependency_issue(roots, edges, loaded, label):
    pending = set(roots)
    checked = set()
    while pending:
        index = pending.pop()
        if index in checked:
            continue
        children = edges.get(str(index))
        if index not in loaded or not isinstance(children, list):
            return f"Missing {label} graph or dependency record {index:04X}."
        if any(type(child) is not int or child <= 0 for child in children):
            return f"Invalid child reference in {label} graph {index:04X}."
        checked.add(index)
        pending.update(children)
    return None


def import_graph_dependencies(roots, folder, enums, extractor, known, edges):
    """Read authored edges even for existing graphs. Preserve those existing graph records."""
    pending = set(roots)
    added = []
    while pending:
        index = pending.pop()
        if str(index) in edges:
            continue
        path = folder / f"{index:012X}.01B.xml"
        if not path.is_file():
            raise ValueError(f"Missing referenced StateScript definition {path.name}")
        graph = extractor.GraphXml(path, enums)
        children = graph.graph_refs()
        edges[str(index)] = sorted(children)
        pending.update(children)
        if index not in known:
            added.append(graph.record(index))
            known.add(index)
    return added
