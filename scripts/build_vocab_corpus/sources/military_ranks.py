"""US military rank abbreviations across all six services.

DTA Manual Appendix M (DOD 7000.14-R) lists ranks across all branches.
Bundling rank abbreviations gives Whisper the right form on first try
("LCpl" not "L. Cpl.", "MGySgt" not "M G I sergeant").

Source: DOD 7000.14-R Appendix M (public domain US gov work).
License: Public domain.
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


# Each entry: (canonical_form, full_name, branch). Branch in subcategory.
# Both the abbreviation and the spelled-out form get added.
_RANKS: list[tuple[str, str, str]] = [
    # Officer ranks — Army (O-1 to O-10 + WO)
    ("2LT", "Second Lieutenant", "army"),
    ("1LT", "First Lieutenant", "army"),
    ("CPT", "Captain", "army"),
    ("MAJ", "Major", "army"),
    ("LTC", "Lieutenant Colonel", "army"),
    ("COL", "Colonel", "army"),
    ("BG", "Brigadier General", "army"),
    ("MG", "Major General", "army"),
    ("LTG", "Lieutenant General", "army"),
    ("GEN", "General", "army"),
    ("WO1", "Warrant Officer 1", "army"),
    ("CW2", "Chief Warrant Officer 2", "army"),
    ("CW3", "Chief Warrant Officer 3", "army"),
    ("CW4", "Chief Warrant Officer 4", "army"),
    ("CW5", "Chief Warrant Officer 5", "army"),
    # Officer ranks — Navy / Coast Guard (sea service abbreviations)
    ("ENS", "Ensign", "navy"),
    ("LTJG", "Lieutenant Junior Grade", "navy"),
    ("LT", "Lieutenant", "navy"),
    ("LCDR", "Lieutenant Commander", "navy"),
    ("CDR", "Commander", "navy"),
    ("CAPT", "Captain", "navy"),
    ("RDML", "Rear Admiral Lower Half", "navy"),
    ("RADM", "Rear Admiral Upper Half", "navy"),
    ("VADM", "Vice Admiral", "navy"),
    ("ADM", "Admiral", "navy"),
    ("FADM", "Fleet Admiral", "navy"),
    # Officer ranks — Air Force / Space Force (use Army-equivalent)
    ("Maj", "Major", "air_force"),
    ("Lt Col", "Lieutenant Colonel", "air_force"),
    ("Col", "Colonel", "air_force"),
    ("Brig Gen", "Brigadier General", "air_force"),
    ("Maj Gen", "Major General", "air_force"),
    ("Lt Gen", "Lieutenant General", "air_force"),
    ("Gen", "General", "air_force"),
    # Officer ranks — Marine Corps
    ("2ndLt", "Second Lieutenant", "marines"),
    ("1stLt", "First Lieutenant", "marines"),
    ("Capt", "Captain", "marines"),
    ("LtCol", "Lieutenant Colonel", "marines"),
    ("BGen", "Brigadier General", "marines"),
    ("MajGen", "Major General", "marines"),
    ("LtGen", "Lieutenant General", "marines"),
    # Enlisted — Army (E-1 to E-9)
    ("PVT", "Private", "army"),
    ("PV2", "Private 2", "army"),
    ("PFC", "Private First Class", "army"),
    ("SPC", "Specialist", "army"),
    ("CPL", "Corporal", "army"),
    ("SGT", "Sergeant", "army"),
    ("SSG", "Staff Sergeant", "army"),
    ("SFC", "Sergeant First Class", "army"),
    ("MSG", "Master Sergeant", "army"),
    ("1SG", "First Sergeant", "army"),
    ("SGM", "Sergeant Major", "army"),
    ("CSM", "Command Sergeant Major", "army"),
    ("SMA", "Sergeant Major of the Army", "army"),
    # Enlisted — Marine Corps
    ("PFC", "Private First Class", "marines"),
    ("LCpl", "Lance Corporal", "marines"),
    ("Cpl", "Corporal", "marines"),
    ("Sgt", "Sergeant", "marines"),
    ("SSgt", "Staff Sergeant", "marines"),
    ("GySgt", "Gunnery Sergeant", "marines"),
    ("MSgt", "Master Sergeant", "marines"),
    ("1stSgt", "First Sergeant", "marines"),
    ("MGySgt", "Master Gunnery Sergeant", "marines"),
    ("SgtMaj", "Sergeant Major", "marines"),
    ("SgtMajMC", "Sergeant Major of the Marine Corps", "marines"),
    # Enlisted — Navy
    ("SR", "Seaman Recruit", "navy"),
    ("SA", "Seaman Apprentice", "navy"),
    ("SN", "Seaman", "navy"),
    ("PO3", "Petty Officer Third Class", "navy"),
    ("PO2", "Petty Officer Second Class", "navy"),
    ("PO1", "Petty Officer First Class", "navy"),
    ("CPO", "Chief Petty Officer", "navy"),
    ("SCPO", "Senior Chief Petty Officer", "navy"),
    ("MCPO", "Master Chief Petty Officer", "navy"),
    ("MCPON", "Master Chief Petty Officer of the Navy", "navy"),
    # Enlisted — Air Force
    ("AB", "Airman Basic", "air_force"),
    ("Amn", "Airman", "air_force"),
    ("A1C", "Airman First Class", "air_force"),
    ("SrA", "Senior Airman", "air_force"),
    ("SSgt", "Staff Sergeant", "air_force"),
    ("TSgt", "Technical Sergeant", "air_force"),
    ("MSgt", "Master Sergeant", "air_force"),
    ("SMSgt", "Senior Master Sergeant", "air_force"),
    ("CMSgt", "Chief Master Sergeant", "air_force"),
    ("CMSAF", "Chief Master Sergeant of the Air Force", "air_force"),
    # Coast Guard (mostly mirrors Navy)
    ("MCPOCG", "Master Chief Petty Officer of the Coast Guard", "coast_guard"),
    # Space Force
    ("Spc1", "Specialist 1", "space_force"),
    ("Spc2", "Specialist 2", "space_force"),
    ("Spc3", "Specialist 3", "space_force"),
    ("Spc4", "Specialist 4", "space_force"),
    ("CMSSF", "Chief Master Sergeant of the Space Force", "space_force"),
]


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, full, branch in _RANKS:
        # Abbreviated form
        if canonical not in seen:
            seen.add(canonical)
            yield RawTerm(
                term=canonical,
                canonical_form=canonical,
                category="military",
                subcategory=f"rank_{branch}",
                context_blurb=f"{full} ({branch.replace('_', ' ').title()})",
                popularity_score=0.6,
                source="DOD 7000.14-R Appendix M (public domain)",
            )
        # Full spelled-out form (e.g. "Lance Corporal" — Whisper gets
        # this right but having it primed helps in noisy audio)
        if full not in seen:
            seen.add(full)
            yield RawTerm(
                term=full,
                canonical_form=full,
                category="military",
                subcategory=f"rank_{branch}",
                context_blurb=f"{branch.replace('_', ' ').title()} rank, abbreviated {canonical}",
                popularity_score=0.5,
                source="DOD 7000.14-R Appendix M (public domain)",
            )
    logger.info("Military ranks: %d entries yielded", len(seen))


name = "Military Ranks"
category = "military"
