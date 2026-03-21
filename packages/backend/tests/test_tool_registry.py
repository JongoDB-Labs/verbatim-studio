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


from services.tool_registry import parse_tool_call, ToolCallParsed


class TestParseToolCall:
    def test_valid_tool_call(self):
        text = 'Let me search.\n\n<tool_call>\n{"tool": "web_search", "args": {"query": "AI benchmarks"}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert result.tool_name == "web_search"
        assert result.args == {"query": "AI benchmarks"}
        assert result.prefix == "Let me search."

    def test_no_tool_call(self):
        result = parse_tool_call("Just a normal response with no tools.")
        assert result is None

    def test_malformed_json(self):
        text = '<tool_call>\n{not valid json}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is None

    def test_missing_tool_field(self):
        text = '<tool_call>\n{"args": {"query": "test"}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is None

    def test_prefix_preserved(self):
        text = 'I will search for that information.\n\n<tool_call>\n{"tool": "search", "args": {}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert "I will search" in result.prefix

    def test_suffix_after_tool_call_ignored(self):
        text = '<tool_call>\n{"tool": "search", "args": {}}\n</tool_call>\nExtra text after'
        result = parse_tool_call(text)
        assert result is not None
        assert result.tool_name == "search"

    def test_empty_args(self):
        text = '<tool_call>\n{"tool": "system_status", "args": {}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert result.args == {}

    def test_partial_opening_tag_not_matched(self):
        result = parse_tool_call("Here is a <tool_call without closing")
        assert result is None
