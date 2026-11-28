"""Import the .003 bindings named by persistent projectile volleys."""


from runtime_graph_closure import dependency_issue, import_graph_dependencies


def persistent_entities(bundle):
    result = set()
    for graph in bundle.get("graphs", []):
        for node in graph.get("nodes", ()):
            if node["class"] != "STUStatescriptStateWeaponVolley":
                continue
            fields = node["fields"]
            persist = (fields.get("m_55E8761C") or {}).get("m_147777B3")
            if not persist or (persist.get("$") == "STUConfigVarBool" and not persist.get("m_value")):
                continue
            source = (fields.get("m_projectileEntity") or {}).get("m_entityDef") or {}
            if isinstance(source, dict) and source.get("$") == "STU_8556841E" and source.get("m_entityDef"):
                result.add(source["m_entityDef"])
    return result


def persistent_entity_issue(bundle):
    """Return the first missing definition or graph reachable from a persistent volley."""
    records = bundle.get("persistent_projectiles", {})
    edges = bundle.get("projectile_graph_dependencies", {})
    if not isinstance(records, dict) or not isinstance(edges, dict):
        return "Invalid persistent projectile catalog or graph dependency table."
    loaded = {graph["index"] for graph in bundle.get("graphs", [])}
    pending = set()
    for guid in sorted(persistent_entities(bundle)):
        record = records.get(guid)
        if not isinstance(record, dict) or not all(
            isinstance(record.get(key), list) for key in ("components", "graphs", "graph_refs")
        ):
            return f"Missing persistent projectile definition or script bindings for {guid}."
        for binding in record["graphs"]:
            if not isinstance(binding, dict):
                return f"Invalid StateScript binding for persistent projectile {guid}."
            if (index := binding.get("graph")) is not None:
                if type(index) is not int:
                    return f"Invalid StateScript root for persistent projectile {guid}."
                pending.add(index)
        if any(type(index) is not int for index in record["graph_refs"]):
            return f"Invalid graph references for persistent projectile {guid}."
        pending.update(record["graph_refs"])
    return dependency_issue(pending, edges, loaded, "persistent projectile")


def import_persistent_entities(bundle, xml, enums, extractor):
    records = {}
    edges = {}
    known = {g["index"] for g in bundle["graphs"]}
    additional = []
    pending_entities = persistent_entities(bundle)
    while pending_entities:
        guid = min(pending_entities)
        pending_entities.remove(guid)
        index = int(guid, 16) & 0xffffffffffff
        path = xml / f"{index:012X}.003.xml"
        if not path.is_file():
            raise ValueError(f"Missing persistent projectile definition {path.name}")
        record, references = extractor.body_record(path, enums, None, f"entity {index:04X}")
        source = extractor.StuXml(path, enums)
        components = source.field(source.root, "m_componentMap")
        if components is None:
            raise ValueError(f"Missing component map in {path.name}")
        records[guid] = {"graphs": record["graphs"], "graph_refs": sorted(references),
                         "components": sorted(extractor.tag(entry) for entry in components)}
        pending = set(references)
        pending.update(binding["graph"] for binding in record["graphs"] if binding.get("graph") is not None)
        new_graphs = import_graph_dependencies(pending, xml.parent / "01B", enums, extractor, known, edges)
        additional.extend(new_graphs)
        pending_entities.update(persistent_entities({"graphs": new_graphs}) - records.keys())
    bundle["graphs"] = sorted([*bundle["graphs"], *additional], key=lambda g: g["index"])
    recovered = {graph["index"] for graph in additional}
    bundle["missing"] = sorted(set(bundle.get("missing", [])) - recovered)
    bundle["persistent_projectiles"] = records
    bundle["projectile_graph_dependencies"] = edges
    return recovered
