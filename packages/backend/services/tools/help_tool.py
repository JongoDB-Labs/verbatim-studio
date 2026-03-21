"""App help tool — look up Verbatim Studio features and navigation.

Breaks the monolithic help blob into topic sections so Max can
pull just the relevant section instead of injecting everything.
"""

from __future__ import annotations

from services.tool_registry import ToolContext, ToolDef, ToolResult

# Help content organized by topic
HELP_SECTIONS: dict[str, str] = {
    "navigation": """Verbatim Studio Navigation:
- Dashboard: Overview stats, recent items, quick actions, onboarding tour
- Recordings: Upload audio/video, apply templates, transcribe, bulk operations
- Projects: Organize recordings with custom project types and metadata
- Documents: Upload PDFs/images, OCR text extraction, page-anchored notes
- Chats: View and resume saved AI conversations
- Live: Real-time microphone transcription (BETA)
- Search: Keyword or semantic (AI-powered) search across all content
- Files: Browse folder structure, move files between storage locations
- Settings: Transcription, AI models, storage locations, backup/restore""",

    "features": """Core Features:
- Recording Templates: Custom metadata fields (text, date, number, dropdown) for recordings
- Project Types: Custom metadata schemas for projects
- Tags: Color-coded labels for filtering recordings
- Speakers: Auto-detected (diarization), can rename, merge, assign colors
- Highlights: Color-code segments (yellow, green, blue, red, purple, orange)
- Comments: Add notes to transcript segments
- Notes: Anchor to timestamps (recordings) or pages (documents)
- Semantic Search: AI-powered meaning-based search using embeddings
- AI Analysis: Summarization, sentiment, entity extraction, action items""",

    "storage": """Storage Options:
- Local: File system storage
- Network: SMB (Windows shares), NFS
- Cloud (OAuth): Google Drive, OneDrive, Dropbox""",

    "tasks": """Common Tasks:
- Transcribe: Recordings > Upload > (optional) Select template > Transcribe
- Edit transcript: Click segment text to edit, speaker label to reassign
- Highlight: Click highlight icon on segment, choose color
- Merge speakers: In speakers panel, merge duplicates
- Export: Transcript view > Export > TXT/SRT/VTT/DOCX/PDF
- Semantic search: Search page > Enter query > Select "Semantic" match type
- Cloud storage: Settings > Storage > Add > Select provider > Authenticate
- Backup: Settings > Backup/Archive > Export (creates .vbz file)""",

    "shortcuts": """Keyboard Shortcuts (transcript view):
- Space/K: Play/Pause
- J/L: Skip back/forward 10s
- Arrow keys: Skip 5s or jump segments
- Shift+,/.: Skip 1s""",

    "troubleshooting": """Troubleshooting:
- Model not loading: Settings > AI/LLM > Download a model
- Transcription failed: Try smaller model (tiny/base), check file format, switch to CPU
- No speakers: Enable diarization in Settings > Transcription (needs HuggingFace token)
- Cloud auth expired: Settings > Storage > Re-authenticate
- Semantic search empty: Embeddings generate automatically, may take time""",
}

# Keywords that map to sections for fuzzy matching
_SECTION_KEYWORDS: dict[str, list[str]] = {
    "navigation": ["navigate", "sidebar", "dashboard", "menu", "page", "where"],
    "features": ["feature", "template", "tag", "speaker", "highlight", "comment", "note", "search", "analysis"],
    "storage": ["storage", "cloud", "drive", "onedrive", "dropbox", "smb", "nfs", "backup"],
    "tasks": ["how to", "transcribe", "export", "edit", "merge", "upload"],
    "shortcuts": ["shortcut", "keyboard", "key", "hotkey"],
    "troubleshooting": ["error", "fail", "not working", "trouble", "problem", "fix"],
}


def _find_best_section(topic: str) -> str | None:
    """Find the best matching section for a topic string."""
    topic_lower = topic.lower()

    # Direct match
    if topic_lower in HELP_SECTIONS:
        return topic_lower

    # Keyword match
    for section, keywords in _SECTION_KEYWORDS.items():
        if any(kw in topic_lower for kw in keywords):
            return section

    # Substring match in section content
    for section, content in HELP_SECTIONS.items():
        if topic_lower in content.lower():
            return section

    return None


async def handle_app_help(args: dict, ctx: ToolContext) -> ToolResult:
    """Look up Verbatim Studio help by topic."""
    topic = args.get("topic")

    if not topic:
        # Return overview of available topics
        topics_list = ", ".join(HELP_SECTIONS.keys())
        return ToolResult(
            content=f"Available help topics: {topics_list}\n\n"
            "Specify a topic to get detailed help, or ask your question and I'll find the relevant section."
        )

    section = _find_best_section(topic)
    if section:
        return ToolResult(content=HELP_SECTIONS[section])

    # No match — return all sections
    all_content = "\n\n".join(HELP_SECTIONS.values())
    return ToolResult(content=all_content)


help_tool = ToolDef(
    name="app_help",
    description="Look up Verbatim Studio features, navigation, and shortcuts. Use when the user asks how to do something in the app.",
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic to look up: navigation, features, storage, tasks, shortcuts, troubleshooting",
            },
        },
    },
    handler=handle_app_help,
    project_scoped=False,
)
