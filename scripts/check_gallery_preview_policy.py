"""Compile the production preview policy against a source-only synthetic contract."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="cl" if os.name == "nt" else "c++")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "fixtures/behavior/gallery-preview-policy-v1.json").read_text())
    assert fixture["contract"] == "gallery-preview-policy-v1"
    assert len(fixture["selection"]) == 18 and len(fixture["trees"]) == 6
    assert len({case["name"] for case in fixture["selection"]}) == 18
    assert len({case["name"] for case in fixture["trees"]}) == 6
    lines = [
        '#include "gallery_preview_policy.h"',
        "#include <cassert>",
        "#include <vector>",
        "using namespace gallery_preview;",
        "int main() {",
        "assert(sizeof(resource_rules)/sizeof(resource_rules[0]) == 14);",
        "assert(!needs_display_filter(0));",
        "for (const auto& rule : resource_rules) assert(needs_display_filter(rule.display));",
    ]
    for case in fixture["selection"]:
        fields = [case[k] + "ULL" for k in ("hero", "preview", "gameplay")]
        lines.append(
            f"assert(select_resource({fields[0]}, {str(case['playable']).lower()}, "
            f"{fields[1]}, {fields[2]}) == {case['expected']}ULL);"
        )
    lines.append("assert(!retain_display_components(nullptr, 0));")
    for case in fixture["trees"]:
        lines += ["{", f"std::vector<DisplayBuilder> nodes({case['count']});"]
        lines.append("for (auto& n : nodes) { for (auto& e : n.entries) e.index = 255; }")
        if case.get("chain"):
            lines.append("for (size_t i=1; i<nodes.size(); ++i) nodes[i-1].first_child=&nodes[i];")
        for node, child, sibling in case.get("links", []):
            for field, target in (("first_child", child), ("next_sibling", sibling)):
                if target >= 0:
                    lines.append(f"nodes[{node}].{field} = &nodes[{target}];")
        for node, index, definition in case["entries"]:
            lines += [
                f"nodes[{node}].entries[{index}].index = {index};",
                f"nodes[{node}].entries[{index}].definition = {definition};",
                f"nodes[{node}].component_mask[{index // 32}] |= uint32_t(1) << {index % 32};",
            ]
        lines += ["const auto before = nodes;", "const bool ok = retain_display_components(&nodes[0], 255);"]
        lines.append(f"assert(ok == {str(case['success']).lower()});")
        if not case["success"]:
            lines.append("assert(std::memcmp(before.data(), nodes.data(), nodes.size()*sizeof(DisplayBuilder)) == 0);")
        else:
            retained = {(n, i): d for n, i, d in case["retained"]}
            for node in range(case["count"]):
                lines += [f"for (size_t i=0; i<256; ++i) {{ uintptr_t expected=0; uint8_t index=255;"]
                for (n, i), definition in retained.items():
                    if n == node:
                        lines.append(f"if (i=={i}) {{ expected={definition}; index={i}; }}")
                lines += [
                    f"assert(nodes[{node}].entries[i].definition == expected);",
                    f"assert(nodes[{node}].entries[i].index == index);",
                    f"assert(bool(nodes[{node}].component_mask[i/32] & (uint32_t(1) << (i%32))) == (index != 255));",
                    "}",
                    f"assert(nodes[{node}].first_child == before[{node}].first_child);",
                    f"assert(nodes[{node}].next_sibling == before[{node}].next_sibling);",
                ]
        lines.append("}")
    lines.append("}")
    with tempfile.TemporaryDirectory(prefix="gallery-preview-") as folder:
        folder = Path(folder)
        source, executable = folder / "policy.cpp", folder / "policy.exe"
        source.write_text("\n".join(lines) + "\n")
        if Path(args.compiler).stem.lower() == "cl":
            command = [args.compiler, "/nologo", "/W4", "/WX", "/EHsc", "/std:c++17",
                       f"/I{root / 'relay'}", str(source), f"/Fe:{executable}", f"/Fo:{folder / 'policy.obj'}"]
        else:
            command = [args.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-I", str(root / "relay"), str(source), "-o", str(executable)]
        subprocess.run(command, cwd=folder, check=True)
        subprocess.run([str(executable)], check=True)
    print(f"Preview policy: {len(fixture['selection'])} resource cases, {len(fixture['trees'])} tree cases passed")


if __name__ == "__main__":
    main()
