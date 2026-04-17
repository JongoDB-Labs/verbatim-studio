"""Tests for custom dictionary service (build_initial_prompt)."""

from services.custom_dictionary import CustomDictionaryEntry, build_initial_prompt


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _entry(term: str, category: str = "general", project_id: str | None = None) -> CustomDictionaryEntry:
    return CustomDictionaryEntry(term=term, category=category, project_id=project_id)


# ---------------------------------------------------------------------------
# Basic prompt building
# ---------------------------------------------------------------------------

class TestBuildInitialPrompt:
    def test_builds_prompt_from_entries(self):
        entries = [_entry("Kubernetes"), _entry("gRPC"), _entry("Terraform")]
        result = build_initial_prompt(entries)
        assert result == "Kubernetes, gRPC, Terraform"

    def test_returns_none_for_empty_list(self):
        assert build_initial_prompt([]) is None

    def test_returns_none_for_no_matching_entries(self):
        """Only project-specific entries that don't match should yield None."""
        entries = [_entry("Docker", project_id="proj-A")]
        result = build_initial_prompt(entries, project_id="proj-B")
        assert result is None

    def test_single_entry(self):
        entries = [_entry("HIPAA")]
        result = build_initial_prompt(entries)
        assert result == "HIPAA"


# ---------------------------------------------------------------------------
# max_chars truncation
# ---------------------------------------------------------------------------

class TestMaxCharsTruncation:
    def test_respects_max_chars(self):
        # Each term is 10 chars; with ", " separators the prompt grows quickly
        entries = [_entry(f"Term{i:05d}x") for i in range(100)]
        result = build_initial_prompt(entries, max_chars=50)
        assert result is not None
        assert len(result) <= 50

    def test_truncates_at_comma_boundary(self):
        entries = [_entry("Alpha"), _entry("BetaBetaBeta"), _entry("Gamma")]
        # "Alpha, BetaBetaBeta, Gamma" = 26 chars
        result = build_initial_prompt(entries, max_chars=20)
        assert result is not None
        # Should keep "Alpha, BetaBetaBeta" (19 chars) or just "Alpha" (5 chars)
        # depending on where the boundary falls
        assert not result.endswith(",")
        assert len(result) <= 20

    def test_max_chars_no_truncation_needed(self):
        entries = [_entry("A"), _entry("B")]
        result = build_initial_prompt(entries, max_chars=800)
        assert result == "A, B"

    def test_first_term_exceeds_max_chars(self):
        """If even the first term is longer than max_chars, return it truncated-free (None)."""
        entries = [_entry("A" * 900)]
        result = build_initial_prompt(entries, max_chars=50)
        # First term alone exceeds max_chars, so nothing fits
        assert result is None


# ---------------------------------------------------------------------------
# Project filtering
# ---------------------------------------------------------------------------

class TestProjectFiltering:
    def test_includes_global_entries(self):
        entries = [_entry("Kubernetes"), _entry("Docker")]  # both global
        result = build_initial_prompt(entries, project_id="proj-A")
        assert result == "Kubernetes, Docker"

    def test_includes_matching_project_entries(self):
        entries = [
            _entry("Kubernetes"),  # global
            _entry("MyApp", project_id="proj-A"),  # matches
        ]
        result = build_initial_prompt(entries, project_id="proj-A")
        assert "Kubernetes" in result
        assert "MyApp" in result

    def test_excludes_other_project_entries(self):
        entries = [
            _entry("Kubernetes"),  # global
            _entry("MyApp", project_id="proj-A"),  # matches
            _entry("OtherApp", project_id="proj-B"),  # does NOT match
        ]
        result = build_initial_prompt(entries, project_id="proj-A")
        assert "OtherApp" not in result

    def test_no_project_id_includes_only_global(self):
        entries = [
            _entry("Global1"),
            _entry("ProjOnly", project_id="proj-A"),
        ]
        result = build_initial_prompt(entries, project_id=None)
        assert result == "Global1"

    def test_all_entries_for_matching_project(self):
        entries = [
            _entry("Global1"),
            _entry("Global2"),
            _entry("ProjTerm", project_id="proj-X"),
        ]
        result = build_initial_prompt(entries, project_id="proj-X")
        assert "Global1" in result
        assert "Global2" in result
        assert "ProjTerm" in result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_deduplicates_case_insensitive(self):
        entries = [_entry("kubernetes"), _entry("Kubernetes"), _entry("KUBERNETES")]
        result = build_initial_prompt(entries)
        # Should keep only first occurrence
        assert result == "kubernetes"

    def test_preserves_order_on_dedup(self):
        entries = [_entry("Alpha"), _entry("beta"), _entry("ALPHA"), _entry("Beta")]
        result = build_initial_prompt(entries)
        assert result == "Alpha, beta"

    def test_dedup_across_global_and_project(self):
        entries = [
            _entry("Docker"),  # global
            _entry("docker", project_id="proj-A"),  # project-specific duplicate
        ]
        result = build_initial_prompt(entries, project_id="proj-A")
        assert result == "Docker"


# ---------------------------------------------------------------------------
# Category is stored but does not affect prompt building
# ---------------------------------------------------------------------------

class TestCategoryIgnored:
    def test_different_categories_all_included(self):
        entries = [
            _entry("HIPAA", category="medical"),
            _entry("Kubernetes", category="tech"),
            _entry("John Smith", category="names"),
        ]
        result = build_initial_prompt(entries)
        assert result == "HIPAA, Kubernetes, John Smith"
