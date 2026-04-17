"""Tests for filler word detection service."""

from services.filler_detection import FillerMatch, detect_fillers


def _seg(segment_id: str, text: str) -> dict:
    """Helper to build a minimal segment dict."""
    return {"id": segment_id, "text": text}


# ---------------------------------------------------------------------------
# Single-word fillers
# ---------------------------------------------------------------------------

class TestSingleWordFillers:
    def test_detects_um(self):
        result = detect_fillers([_seg("s1", "I was um thinking about it")])
        assert "s1" in result
        matches = result["s1"]
        assert any(m.word == "um" and m.filler_type == "single" for m in matches)

    def test_detects_uh(self):
        result = detect_fillers([_seg("s1", "So uh what happened")])
        matches = result["s1"]
        assert any(m.word == "uh" and m.filler_type == "single" for m in matches)

    def test_detects_various_single_fillers(self):
        text = "well basically I actually literally went there"
        result = detect_fillers([_seg("s1", text)])
        words = {m.word for m in result["s1"]}
        assert {"well", "basically", "actually", "literally"} <= words

    def test_single_filler_at_start_of_segment(self):
        result = detect_fillers([_seg("s1", "Um I think so")])
        matches = result["s1"]
        um = [m for m in matches if m.word.lower() == "um"]
        assert len(um) == 1
        assert um[0].start_char == 0
        assert um[0].end_char == 2

    def test_single_filler_at_end_of_segment(self):
        result = detect_fillers([_seg("s1", "I think so right")])
        matches = result["s1"]
        assert any(m.word == "right" for m in matches)


# ---------------------------------------------------------------------------
# Multi-word phrase fillers
# ---------------------------------------------------------------------------

class TestPhraseFillers:
    def test_detects_you_know(self):
        result = detect_fillers([_seg("s1", "It was you know pretty hard")])
        matches = result["s1"]
        assert any(m.word == "you know" and m.filler_type == "phrase" for m in matches)

    def test_detects_sort_of(self):
        result = detect_fillers([_seg("s1", "I sort of agree with that")])
        matches = result["s1"]
        assert any(m.word == "sort of" and m.filler_type == "phrase" for m in matches)

    def test_detects_kind_of(self):
        result = detect_fillers([_seg("s1", "It was kind of weird")])
        matches = result["s1"]
        assert any(m.word == "kind of" and m.filler_type == "phrase" for m in matches)

    def test_detects_i_mean(self):
        result = detect_fillers([_seg("s1", "I mean that was unexpected")])
        matches = result["s1"]
        assert any(m.word == "i mean" and m.filler_type == "phrase" for m in matches)

    def test_detects_or_something(self):
        result = detect_fillers([_seg("s1", "Maybe we could go there or something")])
        matches = result["s1"]
        assert any(m.word == "or something" and m.filler_type == "phrase" for m in matches)

    def test_phrase_match_positions(self):
        text = "It was you know pretty hard"
        result = detect_fillers([_seg("s1", text)])
        yk = [m for m in result["s1"] if m.word == "you know"][0]
        assert text[yk.start_char:yk.end_char] == "you know"


# ---------------------------------------------------------------------------
# Context-dependent "like"
# ---------------------------------------------------------------------------

class TestContextDependentLike:
    def test_like_as_filler(self):
        result = detect_fillers([_seg("s1", "It was like really good")])
        matches = result["s1"]
        assert any(m.word == "like" and m.filler_type == "context" for m in matches)

    def test_like_as_verb_i_like(self):
        """'I like pizza' — 'like' is a verb, should NOT be detected."""
        result = detect_fillers([_seg("s1", "I like pizza")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_they_like(self):
        result = detect_fillers([_seg("s1", "They like swimming")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_we_like(self):
        result = detect_fillers([_seg("s1", "We like that idea")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_you_like(self):
        result = detect_fillers([_seg("s1", "You like that song")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_didnt_like(self):
        result = detect_fillers([_seg("s1", "I didn't like it")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_dont_like(self):
        result = detect_fillers([_seg("s1", "I don't like that")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_as_verb_would_like(self):
        result = detect_fillers([_seg("s1", "I would like some water")])
        if "s1" in result:
            assert not any(m.word == "like" for m in result["s1"])

    def test_like_filler_mid_sentence(self):
        result = detect_fillers([_seg("s1", "He was like totally shocked")])
        matches = result["s1"]
        assert any(m.word == "like" and m.filler_type == "context" for m in matches)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_segments_returns_empty_dict(self):
        assert detect_fillers([]) == {}

    def test_segment_with_no_fillers_not_in_results(self):
        result = detect_fillers([_seg("s1", "The quick brown fox jumps")])
        assert "s1" not in result

    def test_segment_with_empty_text(self):
        result = detect_fillers([_seg("s1", "")])
        assert "s1" not in result

    def test_segment_missing_text_key(self):
        result = detect_fillers([{"id": "s1"}])
        assert "s1" not in result

    def test_no_overlapping_matches(self):
        """Phrase 'you know' should not also produce a 'you' single match."""
        result = detect_fillers([_seg("s1", "It was you know great")])
        matches = result["s1"]
        positions = [(m.start_char, m.end_char) for m in matches]
        for i, (s1, e1) in enumerate(positions):
            for j, (s2, e2) in enumerate(positions):
                if i != j:
                    assert e1 <= s2 or e2 <= s1, (
                        f"Overlap between match {i} ({s1}-{e1}) and {j} ({s2}-{e2})"
                    )


# ---------------------------------------------------------------------------
# Multiple fillers in one segment
# ---------------------------------------------------------------------------

class TestMultipleFillers:
    def test_multiple_fillers_in_one_segment(self):
        text = "Um you know I was like sort of confused"
        result = detect_fillers([_seg("s1", text)])
        matches = result["s1"]
        assert len(matches) >= 3  # um, you know, like, sort of

    def test_multiple_segments(self):
        segs = [
            _seg("s1", "Um what happened"),
            _seg("s2", "The sky is blue"),
            _seg("s3", "I mean you know it was kind of weird"),
        ]
        result = detect_fillers(segs)
        assert "s1" in result
        assert "s2" not in result
        assert "s3" in result
        assert len(result["s3"]) >= 3  # i mean, you know, kind of


# ---------------------------------------------------------------------------
# FillerMatch dataclass
# ---------------------------------------------------------------------------

class TestFillerMatchDataclass:
    def test_filler_match_fields(self):
        m = FillerMatch(word="um", start_char=0, end_char=2, filler_type="single")
        assert m.word == "um"
        assert m.start_char == 0
        assert m.end_char == 2
        assert m.filler_type == "single"

    def test_char_positions_accurate(self):
        text = "So I was um thinking about it"
        result = detect_fillers([_seg("s1", text)])
        for m in result["s1"]:
            assert text[m.start_char:m.end_char].lower() == m.word.lower()


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_uppercase_filler(self):
        result = detect_fillers([_seg("s1", "UM I think so")])
        assert "s1" in result
        matches = result["s1"]
        assert any(m.word.lower() == "um" for m in matches)

    def test_mixed_case_phrase(self):
        result = detect_fillers([_seg("s1", "You Know that was hard")])
        assert "s1" in result
        matches = result["s1"]
        assert any(m.word.lower() == "you know" for m in matches)
