from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance, AgenticWikiStore


def test_maintenance_runs_without_llm():
    maintenance = AgenticWikiMaintenance(AgenticWikiStore())
    assert maintenance("workspace-1") == []
