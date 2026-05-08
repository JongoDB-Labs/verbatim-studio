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


# Pronunciation hints for the most-mispronounced Latin terms.
# Whisper's training corpus underweights legal Latin; respellings
# come from American legal-pronunciation conventions.
_LATIN_PRONUNCIATIONS: dict[str, list[str]] = {
    "ad hoc": ["ad hok"],
    "ad infinitum": ["ad in fih nye tum"],
    "ad litem": ["ad lye tem"],
    "a fortiori": ["ay for shee or eye"],
    "a posteriori": ["ay pos teer ee or eye"],
    "a priori": ["ay pry or eye", "ah pree or ee"],
    "amicus curiae": ["uh mee kus koor ee eye"],
    "bona fide": ["bow nuh fide", "boh nuh fee day"],
    "caveat emptor": ["kav ee at emp tor"],
    "certiorari": ["sur shee uh rar ee"],
    "corpus delicti": ["kor pus dih lik tye"],
    "de facto": ["day fak toh", "dee fak toh"],
    "de jure": ["dee joor ee", "day yoo ray"],
    "de minimis": ["dee min ih mis"],
    "de novo": ["day no voh", "dee no voh"],
    "dicta": ["dik tuh"],
    "ex parte": ["ex par tee"],
    "ex post facto": ["ex post fak toh"],
    "fait accompli": ["fay ack ohm plee"],
    "forum non conveniens": ["for um non con vee nee enz"],
    "habeas corpus": ["hay bee us kor pus"],
    "in camera": ["in kam er uh"],
    "in flagrante delicto": ["in fluh gran tay dih lik toh"],
    "in loco parentis": ["in loh koh puh ren tis"],
    "in personam": ["in per soh nam"],
    "in rem": ["in rem"],
    "in situ": ["in sit oo", "in sye too"],
    "inter alia": ["in ter ay lee uh"],
    "inter vivos": ["in ter vee vohs"],
    "ipso facto": ["ip soh fak toh"],
    "locus standi": ["loh kus stan dye"],
    "mens rea": ["mens ray uh"],
    "modus operandi": ["moh dus op er an dee"],
    "nolo contendere": ["noh loh kon ten der ee"],
    "non sequitur": ["non sek wih ter"],
    "obiter dictum": ["oh bih ter dik tum"],
    "pari passu": ["par ee pas oo"],
    "pendente lite": ["pen den tay lye tay"],
    "per curiam": ["per koor ee um"],
    "per diem": ["per dee em"],
    "per se": ["per say"],
    "post hoc": ["post hok"],
    "prima facie": ["pry muh fay shuh", "pree muh fah see ay"],
    "pro bono": ["pro boh noh"],
    "pro se": ["pro say"],
    "quid pro quo": ["kwid pro kwoh"],
    "res ipsa loquitur": ["rays ip suh loh kwih ter"],
    "res judicata": ["rays joo dih kah tuh"],
    "respondeat superior": ["res pon dee at soo per ee or"],
    "scienter": ["sye en ter"],
    "sine die": ["sye nee dye", "see nay dee ay"],
    "stare decisis": ["star ay dih sye sis"],
    "status quo": ["stay tus kwoh"],
    "subpoena": ["suh pee nah"],
    "subpoena ad testificandum": ["suh pee nah ad tes tih fih kan dum"],
    "subpoena duces tecum": ["suh pee nah doo ses tee kum"],
    "sua sponte": ["soo uh spon tay"],
    "ultra vires": ["ul truh veer eez"],
    "voir dire": ["vwar deer", "vor deer"],
    "writ of certiorari": ["rit of sur shee uh rar ee"],
    "writ of mandamus": ["rit of man day mus"],
    "nunc pro tunc": ["nunk pro tunk"],
    "erga omnes": ["er guh om nez"],
    "ratio decidendi": ["rah shee oh deh sih den dee"],
    "vis-à-vis": ["vee zah vee"],
    "affidavit": ["af ih day vit"],
    "alibi": ["al ih bye"],
    "dictum": ["dik tum"],
    "guardian ad litem": ["gar dee un ad lye tem"],
    "res gestae": ["rays jes tay"],
    "malum in se": ["mal um in say"],
    "malum prohibitum": ["mal um proh hib ih tum"],
    "force majeure": ["force muh zhur"],
    "habeas data": ["hay bee us day tuh"],
}


# Critical-tier English legal acronyms — courts, discovery, statutes,
# litigation. Each has explicit sounds_like for the misread shapes
# Whisper produces. These are catastrophic-loss class for legal
# transcripts where the right canonical is essential for case work.
_LEGAL_ACRONYMS: list[tuple[str, str, list[str]]] = [
    # Court rules / procedure
    ("FRCP", "Federal Rules of Civil Procedure", ["ef ar see pee", "fer-cup", "fer-cap"]),
    ("FRCrP", "Federal Rules of Criminal Procedure", ["ef ar see ar pee", "fer-crip"]),
    ("FRE", "Federal Rules of Evidence", ["ef ar ee", "fre", "free"]),
    ("FRAP", "Federal Rules of Appellate Procedure", ["frap", "ef-rap"]),
    ("FRBP", "Federal Rules of Bankruptcy Procedure", ["fer-bp"]),
    # Discovery / e-discovery
    ("ESI", "Electronically Stored Information", ["ee ess eye", "esi", "essee"]),
    ("MSJ", "Motion for Summary Judgment", ["em ess jay", "msj"]),
    ("MIL", "Motion in Limine", ["mill", "em eye ell", "limine"]),
    ("TRO", "Temporary Restraining Order", ["tee ar oh", "trow", "trio"]),
    ("PI", "Preliminary Injunction", ["pee eye"]),
    # Courts
    ("SCOTUS", "Supreme Court of the United States", ["scoh tuss", "scotus", "skoh-tuss"]),
    ("SDNY", "Southern District of New York", ["ess dee en why"]),
    ("EDNY", "Eastern District of New York", ["ee dee en why"]),
    ("CDCA", "Central District of California", ["see dee see ay"]),
    ("NDCA", "Northern District of California", ["en dee see ay"]),
    ("DDC", "District of Columbia (federal court)", ["dee dee see"]),
    # Statutes / regulators
    ("ADA", "Americans with Disabilities Act", ["ay dee ay", "ada"]),
    ("FOIA", "Freedom of Information Act", ["foya", "foy-uh", "foi-uh"]),
    ("DMCA", "Digital Millennium Copyright Act", ["dee em see ay", "dee-em-see-ay"]),
    ("CFAA", "Computer Fraud and Abuse Act", ["see ef double-a", "see-ef-double-a"]),
    ("ECPA", "Electronic Communications Privacy Act", ["ee see pee ay"]),
    ("ITAR", "International Traffic in Arms Regulations", ["eye tar", "i-tar"]),
    ("EAR", "Export Administration Regulations", ["ee ay ar", "ear"]),
    ("FCPA", "Foreign Corrupt Practices Act", ["ef see pee ay"]),
    ("HIPAA", "Health Insurance Portability and Accountability Act", ["hippa", "hippo"]),
    ("FERPA", "Family Educational Rights and Privacy Act", ["fer-pa", "ferpah"]),
    ("FERC", "Federal Energy Regulatory Commission", ["furk", "ferk"]),
    ("FCC", "Federal Communications Commission", ["ef see see"]),
    ("FTC", "Federal Trade Commission", ["ef tee see"]),
    ("SEC", "Securities and Exchange Commission", ["sec", "ess ee see"]),
    ("DOJ", "Department of Justice", ["dee oh jay"]),
    ("DOL", "Department of Labor", ["dee oh ell"]),
    ("USPTO", "United States Patent and Trademark Office", ["yoo ess pee tee oh"]),
    ("USCIS", "US Citizenship and Immigration Services", ["yoo ess see eye ess"]),
    # Practice areas / titles
    ("AG", "Attorney General", ["ay gee"]),
    ("DA", "District Attorney", ["dee ay"]),
    ("AUSA", "Assistant US Attorney", ["aw-sah", "au-sa", "ay you ess ay"]),
    ("USAO", "US Attorney's Office", ["yoo ess ay oh"]),
    ("PD", "Public Defender", ["pee dee"]),
    ("ADR", "Alternative Dispute Resolution", ["ay dee ar"]),
    # Filings
    ("MTD", "Motion to Dismiss", ["em tee dee"]),
    ("MFR", "Motion for Reconsideration", ["em ef ar"]),
    ("MTC", "Motion to Compel", ["em tee see"]),
    ("MTQ", "Motion to Quash", ["em tee cue"]),
]


def iter_terms() -> Iterable[RawTerm]:
    for term, definition in _LATIN_TERMS:
        yield RawTerm(
            term=term,
            canonical_form=term,
            category="legal",
            subcategory="latin",
            sounds_like=_LATIN_PRONUNCIATIONS.get(term, []),
            context_blurb=definition[:140],
            popularity_score=0.7,
            source="Curated Latin legal terms (public domain)",
        )
    for term, definition, hints in _LEGAL_ACRONYMS:
        # Auto-derive letter-by-letter + word-form variants on top of
        # the curated misread-shape hints.
        from ..pronunciation import derive_acronym_pronunciations
        if term.replace("-", "").isalnum() and term.isupper():
            sl = derive_acronym_pronunciations(term, extra_hints=hints)
        else:
            sl = list(hints)
        yield RawTerm(
            term=term,
            canonical_form=term,
            category="legal",
            subcategory="acronym",
            sounds_like=sl,
            context_blurb=definition[:140],
            popularity_score=0.95,  # critical-tier
            source="Curated legal acronyms (public domain)",
        )
    logger.info(
        "Legal terms: %d Latin + %d acronyms yielded",
        len(_LATIN_TERMS), len(_LEGAL_ACRONYMS),
    )


name = "Latin Legal Terms"
category = "legal"
