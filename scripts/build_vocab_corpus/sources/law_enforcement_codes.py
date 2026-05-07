"""APCO ten-codes + common law enforcement abbreviations.

Whisper sees "10-4" but transcribes it as "ten-four", "ten four", or
"ten-for". The numeric form is what officers say and write, and what
ranges of police-procedural transcripts will need. APCO ten-codes
have been in public domain use since the 1930s.

Source: https://en.wikipedia.org/wiki/Ten-code (CC-BY-SA — code
        definitions are factual; the descriptions are editorial)
License: CC-BY-SA on text descriptions; codes themselves are factual.
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


# APCO standard ten-codes (most common subset, plus a few well-known
# regional variants).
_TEN_CODES: list[tuple[str, str]] = [
    ("10-1", "receiving poorly"),
    ("10-2", "receiving well"),
    ("10-3", "stop transmitting"),
    ("10-4", "acknowledged; affirmative"),
    ("10-5", "relay message"),
    ("10-6", "busy; stand by"),
    ("10-7", "out of service"),
    ("10-8", "in service"),
    ("10-9", "repeat last message"),
    ("10-10", "negative; off duty"),
    ("10-12", "stand by; visitors present"),
    ("10-13", "advise weather and road conditions"),
    ("10-14", "convoy or escort"),
    ("10-15", "prisoner in custody"),
    ("10-16", "pick up prisoner"),
    ("10-17", "pick up papers"),
    ("10-18", "complete assignment as quickly as possible"),
    ("10-19", "return to station"),
    ("10-20", "location"),
    ("10-21", "telephone"),
    ("10-22", "disregard"),
    ("10-23", "arrived at scene"),
    ("10-24", "assignment completed"),
    ("10-25", "report to / meet with"),
    ("10-26", "detaining suspect; expedite"),
    ("10-27", "driver's license check"),
    ("10-28", "vehicle registration check"),
    ("10-29", "wants/warrants check"),
    ("10-30", "unauthorized use of radio"),
    ("10-31", "crime in progress"),
    ("10-32", "person with gun"),
    ("10-33", "emergency; all units stand by"),
    ("10-34", "riot"),
    ("10-35", "major crime alert"),
    ("10-36", "current time"),
    ("10-37", "investigate suspicious vehicle"),
    ("10-38", "stopping suspicious vehicle"),
    ("10-39", "respond with siren and red lights"),
    ("10-40", "respond without siren and red lights"),
    ("10-41", "begin tour of duty"),
    ("10-42", "end tour of duty"),
    ("10-43", "information"),
    ("10-44", "permission to leave"),
    ("10-45", "animal carcass"),
    ("10-46", "assist motorist"),
    ("10-47", "emergency road repair"),
    ("10-48", "traffic standard repair"),
    ("10-49", "traffic light out"),
    ("10-50", "vehicle accident"),
    ("10-51", "wrecker needed"),
    ("10-52", "ambulance needed"),
    ("10-53", "road blocked"),
    ("10-54", "livestock on roadway"),
    ("10-55", "intoxicated driver"),
    ("10-56", "intoxicated pedestrian"),
    ("10-57", "hit and run"),
    ("10-58", "direct traffic"),
    ("10-59", "convoy or escort"),
    ("10-60", "squad in vicinity"),
    ("10-61", "personnel in area"),
    ("10-62", "reply to message"),
    ("10-63", "prepare to copy"),
    ("10-64", "local message"),
    ("10-65", "net message assignment"),
    ("10-66", "message cancellation"),
    ("10-67", "clear to read net message"),
    ("10-68", "dispatch information"),
    ("10-69", "message received"),
    ("10-70", "fire alarm"),
    ("10-71", "advise nature of fire"),
    ("10-72", "report progress on fire"),
    ("10-73", "smoke report"),
    ("10-74", "negative"),
    ("10-75", "in contact with"),
    ("10-76", "en route"),
    ("10-77", "estimated time of arrival"),
    ("10-78", "request assistance"),
    ("10-79", "notify coroner"),
    ("10-80", "chase in progress"),
    ("10-81", "breathalyzer report"),
    ("10-82", "reserve lodging"),
    ("10-83", "school crossing detail"),
    ("10-84", "estimated time of arrival"),
    ("10-85", "delay due to"),
    ("10-86", "officer/operator on duty"),
    ("10-87", "pick up"),
    ("10-88", "advise telephone number"),
    ("10-89", "bomb threat"),
    ("10-90", "bank alarm"),
    ("10-91", "pick up subject"),
    ("10-92", "improperly parked vehicle"),
    ("10-93", "blockade"),
    ("10-94", "drag racing"),
    ("10-95", "subject in custody"),
    ("10-96", "mental subject"),
    ("10-97", "check signal"),
    ("10-98", "prison/jail break"),
    ("10-99", "officer held hostage / records indicate wanted or stolen"),
    ("10-100", "in restroom"),
    ("10-200", "police needed at scene"),
    ("10-2000", "officer needs assistance"),
]

# Plus: common LE / first-responder shorthand
_OTHER: list[tuple[str, str]] = [
    ("BOLO", "Be On The Lookout"),
    ("APB", "All Points Bulletin"),
    ("DOA", "Dead On Arrival"),
    ("DUI", "Driving Under the Influence"),
    ("DWI", "Driving While Intoxicated"),
    ("OUI", "Operating Under the Influence"),
    ("Code 3", "respond with lights and sirens"),
    ("Code 4", "all clear / no further assistance needed"),
    ("Code 5", "stakeout"),
    ("Code 6", "stay out of area"),
    ("Code 7", "out of service for meal"),
    ("Code 8", "officer needs help"),
    ("Code 9", "set up perimeter"),
    ("Signal 0", "officer needs assistance"),
    ("Signal 13", "officer needs immediate assistance"),
    ("CHP", "California Highway Patrol"),
    ("PD", "Police Department"),
    ("SO", "Sheriff's Office"),
    ("HP", "Highway Patrol"),
    ("SWAT", "Special Weapons and Tactics"),
    ("SRT", "Special Response Team"),
    ("K-9", "police dog unit"),
    ("MDT", "Mobile Data Terminal"),
    ("AVL", "Automatic Vehicle Locator"),
    ("CAD", "Computer-Aided Dispatch"),
    ("RMS", "Records Management System"),
]


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, gloss in _TEN_CODES:
        if canonical in seen:
            continue
        seen.add(canonical)
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="law_enforcement",
            subcategory="ten_code",
            context_blurb=gloss[:140],
            popularity_score=0.6,
            source="APCO ten-codes (Wikipedia CC-BY-SA)",
        )
    for canonical, gloss in _OTHER:
        if canonical in seen:
            continue
        seen.add(canonical)
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="law_enforcement",
            subcategory="abbreviation",
            context_blurb=gloss[:140],
            popularity_score=0.7,
            source="Curated LE abbreviations (Wikipedia + agency public)",
        )
    logger.info("Law enforcement codes: %d entries yielded", len(seen))


name = "Law Enforcement Codes"
category = "law_enforcement"
