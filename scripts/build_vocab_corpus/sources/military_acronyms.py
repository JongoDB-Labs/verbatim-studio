"""Curated military acronyms — the actually-spoken ones.

Hand-curated from DoD usage. Covers the high-value terms users
actually say in transcripts (separations, assignments, pay, training)
without the long tail of doctrinal jargon. Originally driven by the
real customer complaint: MCTSSA, ADSEP transcribed as "mctissa", "adset".
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


# (canonical, expansion, subcategory)
_ACRONYMS: list[tuple[str, str, str]] = [
    # Marine Corps units / commands
    ("MCTSSA", "Marine Corps Tactical Systems Support Activity", "usmc_unit"),
    ("MARFORPAC", "Marine Forces Pacific", "usmc_unit"),
    ("MARFORLANT", "Marine Forces Atlantic", "usmc_unit"),
    ("MCESG", "Marine Corps Embassy Security Group", "usmc_unit"),
    ("MCRC", "Marine Corps Recruiting Command", "usmc_unit"),
    ("MARSOC", "Marine Forces Special Operations Command", "usmc_unit"),
    ("MEU", "Marine Expeditionary Unit", "usmc_unit"),
    ("MEF", "Marine Expeditionary Force", "usmc_unit"),
    ("MAW", "Marine Aircraft Wing", "usmc_unit"),
    ("MAGTF", "Marine Air-Ground Task Force", "usmc_unit"),
    ("MARDIV", "Marine Division", "usmc_unit"),
    # Joint commands
    ("USCENTCOM", "United States Central Command", "joint_command"),
    ("USPACOM", "United States Pacific Command", "joint_command"),
    ("USINDOPACOM", "United States Indo-Pacific Command", "joint_command"),
    ("USEUCOM", "United States European Command", "joint_command"),
    ("USAFRICOM", "United States Africa Command", "joint_command"),
    ("USNORTHCOM", "United States Northern Command", "joint_command"),
    ("USSOUTHCOM", "United States Southern Command", "joint_command"),
    ("USSOCOM", "United States Special Operations Command", "joint_command"),
    ("USTRANSCOM", "United States Transportation Command", "joint_command"),
    ("USCYBERCOM", "United States Cyber Command", "joint_command"),
    ("USSTRATCOM", "United States Strategic Command", "joint_command"),
    ("USSPACECOM", "United States Space Command", "joint_command"),
    ("CENTCOM", "Central Command", "joint_command"),
    ("PACOM", "Pacific Command", "joint_command"),
    ("INDOPACOM", "Indo-Pacific Command", "joint_command"),
    ("SOUTHCOM", "Southern Command", "joint_command"),
    ("NORTHCOM", "Northern Command", "joint_command"),
    ("AFRICOM", "Africa Command", "joint_command"),
    ("EUCOM", "European Command", "joint_command"),
    ("SOCOM", "Special Operations Command", "joint_command"),
    ("TRANSCOM", "Transportation Command", "joint_command"),
    ("CYBERCOM", "Cyber Command", "joint_command"),
    ("STRATCOM", "Strategic Command", "joint_command"),
    ("SPACECOM", "Space Command", "joint_command"),
    ("NORAD", "North American Aerospace Defense Command", "joint_command"),
    ("JCS", "Joint Chiefs of Staff", "joint_command"),
    ("OSD", "Office of the Secretary of Defense", "joint_command"),
    # Personnel / administrative
    ("ADSEP", "Administrative Separation", "personnel"),
    ("PCS", "Permanent Change of Station", "personnel"),
    ("TAD", "Temporary Additional Duty", "personnel"),
    ("TDY", "Temporary Duty", "personnel"),
    ("BAH", "Basic Allowance for Housing", "personnel"),
    ("BAS", "Basic Allowance for Subsistence", "personnel"),
    ("ETS", "Expiration Term of Service", "personnel"),
    ("EAS", "End of Active Service", "personnel"),
    ("MOS", "Military Occupational Specialty", "personnel"),
    ("MPF", "Military Personnel Flight", "personnel"),
    ("RDML", "Rear Admiral Lower Half", "personnel"),
    ("OCS", "Officer Candidate School", "personnel"),
    ("ROTC", "Reserve Officer Training Corps", "personnel"),
    ("OPMS", "Officer Personnel Management System", "personnel"),
    ("EPR", "Enlisted Performance Report", "personnel"),
    ("OER", "Officer Evaluation Report", "personnel"),
    ("LES", "Leave and Earnings Statement", "personnel"),
    ("DD214", "Certificate of Release or Discharge from Active Duty", "personnel"),
    ("UCMJ", "Uniform Code of Military Justice", "personnel"),
    ("NJP", "Non-Judicial Punishment", "personnel"),
    ("AWOL", "Absent Without Leave", "personnel"),
    ("UA", "Unauthorized Absence", "personnel"),
    # Operations / tactical
    ("OPORD", "Operation Order", "operations"),
    ("FRAGO", "Fragmentary Order", "operations"),
    ("WARNO", "Warning Order", "operations"),
    ("CONOP", "Concept of Operations", "operations"),
    ("ROE", "Rules of Engagement", "operations"),
    ("AAR", "After Action Review", "operations"),
    ("SITREP", "Situation Report", "operations"),
    ("SPOTREP", "Spot Report", "operations"),
    ("MEDEVAC", "Medical Evacuation", "operations"),
    ("CASEVAC", "Casualty Evacuation", "operations"),
    ("LZ", "Landing Zone", "operations"),
    ("DZ", "Drop Zone", "operations"),
    ("HVT", "High Value Target", "operations"),
    ("IED", "Improvised Explosive Device", "operations"),
    ("VBIED", "Vehicle-Borne IED", "operations"),
    ("CCP", "Casualty Collection Point", "operations"),
    ("FOB", "Forward Operating Base", "operations"),
    ("COB", "Contingency Operating Base", "operations"),
    ("PB", "Patrol Base", "operations"),
    ("OP", "Observation Post", "operations"),
    ("RP", "Release Point", "operations"),
    ("LD", "Line of Departure", "operations"),
    ("PL", "Phase Line", "operations"),
    ("EA", "Engagement Area", "operations"),
    ("NAI", "Named Area of Interest", "operations"),
    ("TAI", "Targeted Area of Interest", "operations"),
    # Equipment / radio
    ("SINCGARS", "Single Channel Ground and Airborne Radio System", "equipment"),
    ("MRAP", "Mine-Resistant Ambush Protected", "equipment"),
    ("HMMWV", "High Mobility Multipurpose Wheeled Vehicle", "equipment"),
    ("JLTV", "Joint Light Tactical Vehicle", "equipment"),
    ("ACOG", "Advanced Combat Optical Gunsight", "equipment"),
    ("EOTECH", "Electro-Optics Technology", "equipment"),
    ("PEQ-15", "Aiming Laser", "equipment"),
    ("NVG", "Night Vision Goggles", "equipment"),
    ("PVS-14", "Night Vision Monocular", "equipment"),
    ("PNVS", "Pilot Night Vision System", "equipment"),
    ("FLIR", "Forward Looking Infrared", "equipment"),
    # Common procedural
    ("LMR", "Land Mobile Radio", "procedural"),
    ("RFI", "Request for Information", "procedural"),
    ("RFF", "Request for Forces", "procedural"),
    ("CDR", "Commander", "procedural"),
    ("CG", "Commanding General", "procedural"),
    ("XO", "Executive Officer", "procedural"),
    ("S-1", "Personnel Officer", "procedural"),
    ("S-2", "Intelligence Officer", "procedural"),
    ("S-3", "Operations Officer", "procedural"),
    ("S-4", "Logistics Officer", "procedural"),
    ("S-6", "Communications Officer", "procedural"),
    ("J-1", "Joint Personnel", "procedural"),
    ("J-2", "Joint Intelligence", "procedural"),
    ("J-3", "Joint Operations", "procedural"),
    ("J-4", "Joint Logistics", "procedural"),
    ("J-6", "Joint Communications", "procedural"),
    ("G-1", "General Staff Personnel", "procedural"),
    ("G-2", "General Staff Intelligence", "procedural"),
    ("G-3", "General Staff Operations", "procedural"),
    ("G-4", "General Staff Logistics", "procedural"),
    # Pay grade
    ("E-1", "Pay Grade Enlisted 1", "paygrade"),
    ("E-2", "Pay Grade Enlisted 2", "paygrade"),
    ("E-3", "Pay Grade Enlisted 3", "paygrade"),
    ("E-4", "Pay Grade Enlisted 4", "paygrade"),
    ("E-5", "Pay Grade Enlisted 5", "paygrade"),
    ("E-6", "Pay Grade Enlisted 6", "paygrade"),
    ("E-7", "Pay Grade Enlisted 7", "paygrade"),
    ("E-8", "Pay Grade Enlisted 8", "paygrade"),
    ("E-9", "Pay Grade Enlisted 9", "paygrade"),
    ("O-1", "Pay Grade Officer 1", "paygrade"),
    ("O-2", "Pay Grade Officer 2", "paygrade"),
    ("O-3", "Pay Grade Officer 3", "paygrade"),
    ("O-4", "Pay Grade Officer 4", "paygrade"),
    ("O-5", "Pay Grade Officer 5", "paygrade"),
    ("O-6", "Pay Grade Officer 6", "paygrade"),
    ("O-7", "Pay Grade Officer 7", "paygrade"),
    ("O-8", "Pay Grade Officer 8", "paygrade"),
    ("O-9", "Pay Grade Officer 9", "paygrade"),
    ("O-10", "Pay Grade Officer 10", "paygrade"),
]


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, expansion, subcat in _ACRONYMS:
        if canonical in seen:
            continue
        seen.add(canonical)
        # High popularity for short common acronyms; lower for longer specific ones.
        score = 0.8 if len(canonical) <= 5 else 0.6
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="military",
            subcategory=subcat,
            context_blurb=expansion[:140],
            popularity_score=score,
            source="Curated military acronyms (DoD usage, public domain)",
        )
    logger.info("Military acronyms (curated): %d yielded", len(seen))


name = "Military Acronyms (Curated)"
category = "military"
