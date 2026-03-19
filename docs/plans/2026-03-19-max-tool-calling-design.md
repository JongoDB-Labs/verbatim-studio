# Max Tool-Calling Architecture Design

**Goal:** Give Max (Verbatim Studio's AI assistant) a formal tool-calling system that replaces implicit behaviors (heuristic web search, keyword help detection, manual-only context attachment) with explicit, model-agnostic tool calls — and adds 12 new tools for document generation, search, analysis, annotations, and organization.

**Architecture:** Model-agnostic `<tool_call>` protocol parsed from LLM output. Central `ToolRegistry` with async handlers. Multi-turn execution loop (max 5 calls/turn). Project-scoped by default via `X-Active-Project` integration with the workspaces plan.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), SSE streaming, existing services (export.py, web_search.py, etc.) wrapped by thin tool handlers.

---

## Tool-Calling Protocol

Max runs on local models (llama-cpp) and cloud APIs alike, so the protocol must be model-agnostic. No OpenAI-style function calling — instead, Max emits structured JSON blocks in its response:

```
I'll search your transcripts for mentions of quarterly revenue.

<tool_call>
{"tool": "search_transcripts", "args": {"query": "quarterly revenue"}}
</tool_call>
```

The backend's streaming loop detects `<tool_call>` blocks, pauses the stream, executes the tool, then feeds the result back to Max in a second LLM call so it can incorporate the result into its response.

**Why this approach:**
- Works with any LLM (local GGUF models, OpenAI, Anthropic, etc.)
- No model-specific function-calling API required
- The `<tool_call>` delimiter is easy to parse and unlikely to appear in normal text
- Multi-turn: Max can call multiple tools sequentially if needed

**Project scoping:** Every tool call automatically inherits the `project_id` from the chat request. Tools operate within the active project by default unless they explicitly cross boundaries (e.g., `global_search`).

---

## Tool Registry

A central `ToolRegistry` holds all tool definitions. Each tool is a dataclass with a name, description (for the LLM prompt), parameter schema (for validation), and an async handler function.

```python
# services/tool_registry.py

@dataclass
class ToolDef:
    name: str
    description: str           # Injected into system prompt
    parameters: dict            # JSON Schema for args
    handler: Callable           # async fn(args, ctx) -> ToolResult
    project_scoped: bool = True # Auto-filter by active project

@dataclass
class ToolResult:
    content: str                # Text fed back to Max
    artifacts: list[Artifact]   # Files, links, UI actions

@dataclass
class Artifact:
    type: str                   # "file_download", "link", "notification"
    data: dict                  # e.g. {"url": "/documents/xxx/file", "filename": "report.pdf"}

@dataclass
class ToolContext:
    project_id: str | None      # Active project
    conversation_id: str | None
    recording_ids: list[str]    # Currently attached
    document_ids: list[str]     # Currently attached
    db: AsyncSession
    ai_service: AIService       # For tools that need sub-LLM calls
```

Registration happens at app startup — each tool module registers itself:

```python
registry = ToolRegistry()
registry.register(search_tool)
registry.register(generate_document_tool)
registry.register(web_search_tool)
```

The registry auto-generates the tools section for the system prompt from registered `ToolDef` objects. Tools that are disabled (e.g., `web_search` when the toggle is off) are excluded from the prompt entirely.

---

## Execution Loop

The chat streaming endpoint becomes a loop instead of a single LLM call:

```
User message
    |
LLM generates response (streaming)
    |
Backend detects <tool_call> in output?
    +-- No  -> stream tokens to frontend as normal, done
    +-- Yes -> pause stream, execute tool
                |
            Send SSE: {tool_call: {name, args}}     <- UI shows activity card
            Execute handler(args, ctx)
            Send SSE: {tool_result: {name, summary}} <- UI shows result
                |
            Feed tool result back to LLM as a new message:
            [{"role": "assistant", "content": "...<tool_call>..."},
             {"role": "tool",     "content": "<tool_result>...</tool_result>"}]
                |
            LLM continues generating (may call another tool)
                |
            Loop (max 5 iterations to prevent runaway)
```

**Key details:**
- Max 5 tool calls per turn — hard cap prevents infinite loops
- Partial text before a tool call gets streamed normally (Max can say "Let me search for that" before the `<tool_call>`)
- Artifacts (file downloads, links) accumulate across tool calls and are sent with the `done` event
- Token buffer — the streaming loop buffers output to detect `<tool_call>` opening tags before flushing, so the raw tag never reaches the frontend

---

## Tool Catalog (15 tools)

### Converted from Implicit Behaviors

| Tool | Replaces | Description |
|---|---|---|
| `web_search` | Heuristic auto-trigger via regex | Provider-agnostic internet search (Tavily/Brave/SearXNG/custom). Max decides when to search instead of heuristics. Provider factory pattern unchanged. |
| `get_context` | Manual-only attachment picker | Pull any file from active project workspace. Semantic search within project to find relevant content Max wasn't explicitly given. |
| `app_help` | Keyword detection + full blob injection | Look up specific Verbatim Studio help sections. Returns only relevant section instead of injecting entire help context. |

### New Tools

| Tool | Description |
|---|---|
| `project_search` | Search transcripts, documents, OCR text, notes, and chats within the active project workspace. Uses the same `project_id` filtering the workspaces plan builds into search endpoints. |
| `global_search` | Cross-project search when user asks for broad results or names a specific project. Bypasses the `X-Active-Project` filter. |
| `generate_document` | Create a PDF or DOCX from structured content Max generates (reports, summaries, meeting notes). Uses existing reportlab/python-docx libraries. |
| `export_transcript` | Export an attached transcript as TXT/SRT/VTT/DOCX/PDF. Wraps existing `services/export.py`. |
| `summarize_transcript` | Trigger AI summarization — key points, action items, topics, named entities. Wraps existing summarization pipeline. |
| `quality_review` | Run two-pass transcription error detection (heuristic + LLM). Wraps existing `services/quality_review.py`. |
| `highlight_segments` | Apply highlight colors (yellow, green, blue, pink, orange, purple) to specific segments. |
| `add_note` | Create a note anchored to a timestamp (recordings) or page (documents). |
| `create_project` | Create a new project and optionally assign content to it. |
| `tag_recordings` | Add or remove tags from recordings. |
| `get_recording_info` | Query recording metadata, list recent recordings. |
| `system_status` | Check GPU availability, loaded model, storage health. |

---

## Converting Existing Implicit Behaviors

### web_search — from heuristic auto-trigger to explicit tool

**Today:** `extract_search_query()` runs regex heuristics on every message. If keywords match, search fires automatically. The user has no control and Max has no say.

**After:** Max decides when to search. The system prompt tells it when to use the tool. The heuristic detection code (`extract_search_query()`, `_TEMPORAL_KEYWORDS`, `_SEARCH_COMMANDS`, `_FACTUAL_PATTERNS`, `_NON_SEARCH_PATTERNS`) is removed — Max's judgment replaces it.

**Risk:** Max might forget to search when it should. **Mitigation:** Clear system prompt guidance on when to search, and `web_search_enabled` toggle controls whether the tool is even available.

### get_context — from manual attachment to project-wide pull

**Today:** User clicks the attachment picker, selects specific transcripts/documents. Their extracted text is injected into the prompt. Max can only see what the user explicitly attached.

**After:** Max can call `get_context(query="quarterly revenue", content_types=["transcript", "document"])` to semantically search the active project and pull in relevant content itself. User attachments still work — they're injected as before. But Max can also proactively pull in files it wasn't given.

This pairs directly with the workspaces plan — the `project_id` scoping means Max searches within the active workspace, and the semantic search index (embeddings) lets it find relevant content without loading everything.

### app_help — from keyword detection to explicit tool

**Today:** `is_help_intent()` checks for keywords like "how do I", "where is", "settings". If matched, `MAX_HELP_CONTEXT` (~500 tokens) is injected into the system prompt on every matching request.

**After:** Max calls `app_help(topic="export")` and gets back the relevant section. The help content is broken into sections so Max only pulls what's relevant instead of injecting the entire help blob every time. Saves tokens.

---

## System Prompt Design

The system prompt is dynamically assembled from the tool registry. Tools that are disabled are excluded entirely.

```
You are Max, the Verbatim Studio assistant. You can analyze transcripts,
documents, and help users with the app. You operate within the user's
active project workspace.

## Tools

You have access to the following tools. To use a tool, output a <tool_call>
block. You may include text before the block to explain what you're doing.
Wait for the result before continuing your response.

<tool_call>
{"tool": "tool_name", "args": {"param": "value"}}
</tool_call>

### Available Tools

- web_search(query: str) — Search the internet for current information.
  Use when the user asks about current events, recent data, or says
  "search for", "look up", "what's the latest".

- project_search(query: str) — Search transcripts, documents, notes,
  and chats within the current project workspace.

- global_search(query: str, project_name?: str) — Search across ALL
  projects. Use when the user says "across all", "in every project",
  or names a specific different project.

- get_context(query: str, content_types?: str[]) — Pull relevant
  content from the active project. Use when you need more context
  to answer a question about the user's content.

- generate_document(title: str, format: "pdf"|"docx", sections:
  [{heading: str, content: str}]) — Create a downloadable document.

- export_transcript(transcript_id: str, format: "txt"|"srt"|"vtt"|
  "docx"|"pdf") — Export a transcript in the specified format.

- summarize_transcript(transcript_id: str) — Generate AI summary with
  key points, action items, topics, and named entities.

- quality_review(transcript_id: str) — Run two-pass quality review to
  detect transcription errors and propose corrections.

- highlight_segments(segment_ids: str[], color: str) — Highlight
  transcript segments. Colors: yellow, green, blue, pink, orange, purple.

- add_note(content: str, anchor_type: "timestamp"|"page",
  anchor_id: str, anchor_value: str) — Add a note anchored to a
  timestamp or page.

- project_search(query: str) — Search within the active project.

- global_search(query: str, project_name?: str) — Search across all projects.

- create_project(name: str, description?: str) — Create a new project.

- tag_recordings(recording_ids: str[], tags: str[], action: "add"|"remove") —
  Add or remove tags from recordings.

- get_recording_info(recording_id?: str, list_recent?: bool) — Get
  recording metadata or list recent recordings.

- system_status() — Check GPU, model, and storage health.

- app_help(topic?: str) — Look up Verbatim Studio features, navigation,
  and shortcuts.

## Guidelines
- Call ONE tool at a time. Wait for the result before deciding next steps.
- Prefer project_search over global_search unless the user asks broadly.
- Use get_context proactively when the user asks about content you
  haven't seen yet.
- For generate_document, structure content with clear headings and sections.
- Always explain what you're doing before calling a tool.
```

Token cost: ~400 tokens for the full tools section, comparable to the old `MAX_HELP_CONTEXT` blob.

---

## SSE Protocol & Frontend UX

### SSE Events

```
data: {"tool_call": {"name": "project_search", "args": {"query": "..."}}}
data: {"tool_result": {"name": "project_search", "summary": "Found 7 matches..."}}
data: {"token": "Based on "}
data: {"token": "the search results..."}
data: {"artifact": {"type": "file_download", "url": "...", "filename": "report.pdf"}}
data: {"done": true, "compressed_memory": "...", "artifacts": [...]}
```

### UI States

| Event | UI renders |
|---|---|
| `tool_call` | Activity card: icon + "Searching project..." + query text |
| `tool_result` | Card updates to show result summary |
| `artifact` | Download card with file icon, filename, download button |
| `token` | Normal streaming text |

Activity cards appear above the streaming response, stack vertically for multi-tool turns. Artifacts persist on the final message as download cards.

### ChatMessage Type Extension

```typescript
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  webSources?: Array<{ title: string; url: string }>;
  artifacts?: Array<{ type: string; url: string; filename: string }>;
  toolCalls?: Array<{ name: string; summary: string }>;
}
```

---

## File Structure

### New Files

```
packages/backend/
  services/
    tool_registry.py              # ToolDef, ToolResult, Artifact, ToolContext, ToolRegistry
    tools/
      __init__.py                 # register_all_tools()
      web_search_tool.py          # Migrated from implicit heuristic
      context_tool.py             # Migrated from attachment-only injection
      help_tool.py                # Migrated from keyword detection
      search_tools.py             # project_search + global_search
      document_tools.py           # generate_document + export_transcript
      analysis_tools.py           # summarize_transcript + quality_review
      annotation_tools.py         # highlight_segments + add_note
      organization_tools.py       # create_project, tag_recordings, get_recording_info, system_status

packages/frontend/src/
  components/ai/
    ToolActivityCard.tsx          # Tool execution display component
```

### Modified Files

```
packages/backend/
  api/routes/ai.py               # Chat endpoint gets tool execution loop
  services/context_manager.py     # build_messages() injects tool definitions

packages/frontend/src/
  components/ai/ChatMessages.tsx  # Tool activity cards, artifact downloads
  components/ai/ChatPanel.tsx     # SSE event handling for tool_call/tool_result/artifact
  lib/api.ts                      # ChatStreamToken type extended
```

### Integration Points

- **Tool handlers wrap existing services** — no business logic duplication
- **`ToolContext.project_id`** comes from the same `X-Active-Project` header the workspaces plan adds
- **`project_search`** passes through the project_id filter the workspaces plan builds into search endpoints
- **`global_search`** bypasses the filter explicitly
- **Startup registration** in `api/main.py`: `register_all_tools(get_registry())`

---

## Testing Strategy

### Unit Tests (`tests/test_tool_registry.py`)
- Tool registration/deregistration
- Prompt generation from registered tools
- `<tool_call>` parsing from LLM output (valid JSON, malformed, nested, partial)
- Max iteration cap (5 tool calls per turn)
- Tool availability filtering (disabled tools excluded from prompt)

### Tool Handler Tests (`tests/test_tools/`)
- Each handler tested in isolation with mocked services
- Verify ToolResult content and artifacts
- Verify project scoping (project_search filters, global_search doesn't)
- Error handling: service failures return user-friendly messages, don't crash stream

### Integration Tests (`tests/test_tool_execution.py`)
- Full loop: mock LLM output with `<tool_call>` -> tool executes -> result fed back -> final response
- Multi-tool turn: LLM calls two tools sequentially
- SSE event sequence: verify tool_call -> tool_result -> token -> done ordering
- Artifact accumulation across multiple tool calls
- Graceful degradation: tool fails mid-stream, Max continues with error context

### Migration Parity Tests
- Web search tool produces identical results to old heuristic path
- get_context with explicit attachments matches old attachment injection
- app_help returns same content as old MAX_HELP_CONTEXT injection

### Frontend Manual Testing Checklist
- [ ] Tool activity cards appear and update during streaming
- [ ] Artifact download cards persist on final message
- [ ] Multiple tool calls in one turn display correctly
- [ ] Toggling web search off removes it from available tools
- [ ] Old web search source cards still work during migration
