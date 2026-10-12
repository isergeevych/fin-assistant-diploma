"""Б6.3 — Mermaid-схемы обоих графов → docs/agent-graph-custom.mmd, docs/agent-graph-prebuilt.mmd (+ PNG, если получится).

    python -m scripts.visualize_graph
"""

from pathlib import Path

from app.services.agent_graph import custom_graph, prebuilt_graph

DOCS = Path(__file__).resolve().parents[1] / "docs"


def main() -> None:
    for name, graph in (("custom", custom_graph), ("prebuilt", prebuilt_graph)):
        path = DOCS / f"agent-graph-{name}.mmd"
        path.write_text(graph.get_graph().draw_mermaid(), encoding="utf-8")
        print("→", path.relative_to(DOCS.parent))
    try:  # PNG рендерит mermaid.ink (сеть) — не обязательно, .mmd открывается в mermaid.live
        (DOCS / "agent-graph.png").write_bytes(custom_graph.get_graph().draw_mermaid_png())
        print("→ docs/agent-graph.png")
    except Exception as e:  # noqa: BLE001
        print(f"PNG не создан ({type(e).__name__}) — остаётся .mmd")


if __name__ == "__main__":
    main()
