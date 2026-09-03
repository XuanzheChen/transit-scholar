from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance, AgenticWikiStore


def test_maintenance_boundary_is_deterministic_and_no_llm():
    calls = []
    maintenance = AgenticWikiMaintenance(AgenticWikiStore())
    maintenance._resolve = lambda source, workspace_id: source
    def hook(workspace_id):
        calls.append(workspace_id)
        return maintenance(workspace_id)
    hook("w1")
    assert calls == ["w1"] and maintenance.calls == 1
