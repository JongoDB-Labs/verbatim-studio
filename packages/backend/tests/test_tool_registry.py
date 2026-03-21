"""Tests for ToolRegistry data structures and registration."""

import pytest
from services.tool_registry import (
    ToolDef,
    ToolResult,
    Artifact,
    ToolContext,
    ToolRegistry,
)


class TestToolDefDataclass:
    def test_tool_def_creation(self):
        def dummy_handler(args, ctx):
            pass

        tool = ToolDef(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=dummy_handler,
        )
        assert tool.name == "test_tool"
        assert tool.project_scoped is True  # default

    def test_tool_def_not_project_scoped(self):
        tool = ToolDef(
            name="global",
            description="Global tool",
            parameters={},
            handler=lambda a, c: None,
            project_scoped=False,
        )
        assert tool.project_scoped is False


class TestToolResult:
    def test_result_with_artifacts(self):
        result = ToolResult(
            content="Found 5 results",
            artifacts=[Artifact(type="file_download", data={"url": "/files/x", "filename": "report.pdf"})],
        )
        assert len(result.artifacts) == 1
        assert result.artifacts[0].type == "file_download"

    def test_result_empty_artifacts(self):
        result = ToolResult(content="Done", artifacts=[])
        assert result.artifacts == []


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = ToolDef(
            name="my_tool",
            description="desc",
            parameters={},
            handler=lambda a, c: None,
        )
        registry.register(tool)
        assert registry.get("my_tool") is tool

    def test_get_unknown_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_register_duplicate_raises(self):
        registry = ToolRegistry()
        tool = ToolDef(name="dup", description="", parameters={}, handler=lambda a, c: None)
        registry.register(tool)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool)

    def test_list_tools(self):
        registry = ToolRegistry()
        t1 = ToolDef(name="a", description="Tool A", parameters={}, handler=lambda a, c: None)
        t2 = ToolDef(name="b", description="Tool B", parameters={}, handler=lambda a, c: None)
        registry.register(t1)
        registry.register(t2)
        names = [t.name for t in registry.list_tools()]
        assert "a" in names
        assert "b" in names

    def test_unregister(self):
        registry = ToolRegistry()
        tool = ToolDef(name="removable", description="", parameters={}, handler=lambda a, c: None)
        registry.register(tool)
        registry.unregister("removable")
        assert registry.get("removable") is None

    def test_list_tools_with_filter(self):
        registry = ToolRegistry()
        t1 = ToolDef(name="web", description="", parameters={}, handler=lambda a, c: None)
        t2 = ToolDef(name="search", description="", parameters={}, handler=lambda a, c: None)
        registry.register(t1)
        registry.register(t2)
        filtered = registry.list_tools(names=["web"])
        assert len(filtered) == 1
        assert filtered[0].name == "web"
