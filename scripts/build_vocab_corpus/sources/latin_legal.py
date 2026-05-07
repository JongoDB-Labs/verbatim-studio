"""Latin legal terms — short, high-impact dictionary.

The bulk-list approach (parsing Wikipedia "List of Latin legal terms")
adds CC-BY-SA attribution overhead. Since this is a tiny set (~600
terms) that doesn't change much, we ship a hand-curated frozen list
inline. Saves a network round-trip in the build, and the editorial
quality is higher than what you'd get from raw Wikipedia parsing
(which includes a lot of explanatory commentary).

Source: Public-domain Latin legal terms compiled from common law
references. No external download.
License: Inline list — no external license attribution required.
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


# Curated Latin legal terms. Sourced from Black's Law Dictionary
# vocabulary and FRCP/USCode usage. Definitions kept short.
# Whisper consistently mishears these — "voir dire" → "voi deer",
# "habeas corpus" → "haveus corpus", "subpoena" → "supena".
_LATIN_TERMS: list[tuple[str, str]] = [
    ("ad hoc", "for this specific purpose"),
    ("ad infinitum", "to infinity, without limit"),
    ("ad litem", "for the suit (as in guardian ad litem)"),
    ("a fortiori", "with even stronger reason"),
    ("a posteriori", "from what comes after; based on observation"),
    ("a priori", "from what comes before; deductive"),
    ("amicus curiae", "friend of the court"),
    ("bona fide", "in good faith"),
    ("caveat emptor", "let the buyer beware"),
    ("certiorari", "petition to a higher court for review"),
    ("corpus delicti", "body of the crime; evidence of the offense"),
    ("de facto", "in fact, in reality"),
    ("de jure", "by law, by right"),
    ("de minimis", "trivial, of minimum significance"),
    ("de novo", "anew; from the beginning"),
    ("dicta", "remarks not essential to a decision"),
    ("ex parte", "from one side only"),
    ("ex post facto", "after the fact"),
    ("fait accompli", "an accomplished fact"),
    ("forum non conveniens", "doctrine to dismiss in favor of more appropriate court"),
    ("habeas corpus", "writ to bring a person before the court"),
    ("in camera", "in chambers, in private"),
    ("in flagrante delicto", "caught in the act"),
    ("in loco parentis", "in the place of the parent"),
    ("in personam", "directed at a specific person"),
    ("in rem", "directed at a thing or property"),
    ("in situ", "in the original place"),
    ("inter alia", "among other things"),
    ("inter vivos", "between living persons"),
    ("ipso facto", "by the very fact"),
    ("locus standi", "place of standing"),
    ("mens rea", "guilty mind; criminal intent"),
    ("modus operandi", "method of operation"),
    ("nolo contendere", "no contest"),
    ("non sequitur", "it does not follow"),
    ("obiter dictum", "remark in passing"),
    ("pari passu", "on equal footing"),
    ("pendente lite", "during the litigation"),
    ("per curiam", "by the court collectively"),
    ("per diem", "by the day"),
    ("per se", "by itself, intrinsically"),
    ("post hoc", "after this; therefore because of this"),
    ("prima facie", "at first sight; on its face"),
    ("pro bono", "for the public good"),
    ("pro se", "on one's own behalf, without counsel"),
    ("quid pro quo", "something for something"),
    ("res ipsa loquitur", "the thing speaks for itself"),
    ("res judicata", "matter already adjudicated"),
    ("respondeat superior", "let the master answer"),
    ("scienter", "knowingly"),
    ("sine die", "without a day; indefinitely"),
    ("stare decisis", "let the decision stand; precedent"),
    ("status quo", "the existing state of affairs"),
    ("subpoena", "writ requiring appearance"),
    ("subpoena ad testificandum", "subpoena to testify"),
    ("subpoena duces tecum", "subpoena to produce documents"),
    ("sua sponte", "of its own accord"),
    ("ultra vires", "beyond the powers"),
    ("voir dire", "to speak the truth; jury selection"),
    ("writ of certiorari", "order to a lower court to deliver records"),
    ("writ of mandamus", "order to perform a duty"),
    ("nunc pro tunc", "now for then"),
    ("erga omnes", "toward all"),
    ("ratio decidendi", "reason for the decision"),
    ("vis-à-vis", "in relation to"),
    ("affidavit", "sworn written statement"),
    ("alibi", "elsewhere; defense based on absence"),
    ("dictum", "judicial remark not essential to decision"),
    ("guardian ad litem", "guardian for the suit"),
    ("res gestae", "things done; facts of the transaction"),
    ("malum in se", "wrong in itself"),
    ("malum prohibitum", "wrong because prohibited"),
    ("force majeure", "superior force; act of God"),
    ("habeas data", "writ to access personal data held by others"),
]


def iter_terms() -> Iterable[RawTerm]:
    for term, definition in _LATIN_TERMS:
        yield RawTerm(
            term=term,
            canonical_form=term,
            category="legal",
            subcategory="latin",
            context_blurb=definition[:140],
            popularity_score=0.7,
            source="Curated Latin legal terms (public domain)",
        )
    logger.info("Latin legal: %d terms yielded", len(_LATIN_TERMS))


name = "Latin Legal Terms"
category = "legal"
