"""US government / civic / agency acronyms.

Hand-curated from DOJ public list + agency websites + Wikipedia federal
LE list. All entities are public; the acronyms themselves are factual
and not copyrightable. Whisper consistently mishears these as common
words ("FAFSA" → "fast-uh", "OSHA" → "Asia").
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


_TERMS: list[tuple[str, str]] = [
    # Federal departments
    ("DOJ", "Department of Justice"),
    ("DOD", "Department of Defense"),
    ("DOS", "Department of State"),
    ("DOT", "Department of Transportation"),
    ("DOE", "Department of Energy"),
    ("DOI", "Department of the Interior"),
    ("DOL", "Department of Labor"),
    ("DOC", "Department of Commerce"),
    ("DHS", "Department of Homeland Security"),
    ("HHS", "Department of Health and Human Services"),
    ("HUD", "Department of Housing and Urban Development"),
    ("USDA", "United States Department of Agriculture"),
    ("VA", "Department of Veterans Affairs"),
    ("ED", "Department of Education"),
    ("Treasury", "Department of the Treasury"),
    # Federal law enforcement / security
    ("FBI", "Federal Bureau of Investigation"),
    ("DEA", "Drug Enforcement Administration"),
    ("ATF", "Bureau of Alcohol, Tobacco, Firearms and Explosives"),
    ("USMS", "United States Marshals Service"),
    ("USSS", "United States Secret Service"),
    ("ICE", "Immigration and Customs Enforcement"),
    ("CBP", "Customs and Border Protection"),
    ("USCIS", "United States Citizenship and Immigration Services"),
    ("TSA", "Transportation Security Administration"),
    ("FEMA", "Federal Emergency Management Agency"),
    ("USCG", "United States Coast Guard"),
    ("CISA", "Cybersecurity and Infrastructure Security Agency"),
    # Intelligence community
    ("CIA", "Central Intelligence Agency"),
    ("NSA", "National Security Agency"),
    ("DIA", "Defense Intelligence Agency"),
    ("NRO", "National Reconnaissance Office"),
    ("NGA", "National Geospatial-Intelligence Agency"),
    ("ODNI", "Office of the Director of National Intelligence"),
    # Regulatory
    ("EPA", "Environmental Protection Agency"),
    ("FDA", "Food and Drug Administration"),
    ("FCC", "Federal Communications Commission"),
    ("FTC", "Federal Trade Commission"),
    ("SEC", "Securities and Exchange Commission"),
    ("FINRA", "Financial Industry Regulatory Authority"),
    ("OSHA", "Occupational Safety and Health Administration"),
    ("NIOSH", "National Institute for Occupational Safety and Health"),
    ("NHTSA", "National Highway Traffic Safety Administration"),
    ("FAA", "Federal Aviation Administration"),
    ("NTSB", "National Transportation Safety Board"),
    ("CDC", "Centers for Disease Control and Prevention"),
    ("CMS", "Centers for Medicare and Medicaid Services"),
    ("CFPB", "Consumer Financial Protection Bureau"),
    ("EEOC", "Equal Employment Opportunity Commission"),
    ("NLRB", "National Labor Relations Board"),
    ("FERC", "Federal Energy Regulatory Commission"),
    ("USPTO", "United States Patent and Trademark Office"),
    ("USCO", "United States Copyright Office"),
    # Programs / benefits
    ("SSA", "Social Security Administration"),
    ("SSI", "Supplemental Security Income"),
    ("SSDI", "Social Security Disability Insurance"),
    ("Medicare", "federal health insurance for seniors"),
    ("Medicaid", "joint federal-state health insurance for low-income"),
    ("WIC", "Women, Infants, and Children nutrition program"),
    ("SNAP", "Supplemental Nutrition Assistance Program"),
    ("TANF", "Temporary Assistance for Needy Families"),
    ("EBT", "Electronic Benefits Transfer"),
    ("FAFSA", "Free Application for Federal Student Aid"),
    ("HEOA", "Higher Education Opportunity Act"),
    ("PSLF", "Public Service Loan Forgiveness"),
    ("Pell", "Pell Grant"),
    ("Section 8", "Housing Choice Voucher Program"),
    # Misc agencies
    ("NASA", "National Aeronautics and Space Administration"),
    ("NIH", "National Institutes of Health"),
    ("NSF", "National Science Foundation"),
    ("NIST", "National Institute of Standards and Technology"),
    ("USPS", "United States Postal Service"),
    ("IRS", "Internal Revenue Service"),
    ("GSA", "General Services Administration"),
    ("OPM", "Office of Personnel Management"),
    ("OMB", "Office of Management and Budget"),
    ("CBO", "Congressional Budget Office"),
    ("GAO", "Government Accountability Office"),
    ("USAID", "United States Agency for International Development"),
    ("EXIM", "Export-Import Bank"),
    # International / multilateral
    ("UN", "United Nations"),
    ("UNHCR", "United Nations High Commissioner for Refugees"),
    ("UNESCO", "UN Educational, Scientific and Cultural Organization"),
    ("UNICEF", "UN International Children's Emergency Fund"),
    ("WHO", "World Health Organization"),
    ("IMF", "International Monetary Fund"),
    ("WTO", "World Trade Organization"),
    ("NATO", "North Atlantic Treaty Organization"),
    ("OECD", "Organisation for Economic Co-operation and Development"),
    ("ASEAN", "Association of Southeast Asian Nations"),
    ("OPEC", "Organization of the Petroleum Exporting Countries"),
    # Legislative
    ("USC", "United States Code"),
    ("CFR", "Code of Federal Regulations"),
    ("HR", "House Resolution / House of Representatives"),
    ("S", "Senate Bill (when prefixing a number)"),
    ("PL", "Public Law"),
    # Common civic
    ("DMV", "Department of Motor Vehicles"),
    ("RMV", "Registry of Motor Vehicles"),
    ("DOH", "Department of Health"),
    ("ACLU", "American Civil Liberties Union"),
    ("NAACP", "National Association for the Advancement of Colored People"),
    ("AAA", "American Automobile Association"),
    ("AARP", "American Association of Retired Persons"),
    ("AAA", "American Anthropological Association"),
]


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, expansion in _TERMS:
        if canonical in seen:
            continue
        seen.add(canonical)
        # Federal agencies and household-name programs are very high
        # popularity. Lower for the more obscure (NSF, NIST).
        score = 0.85 if canonical in {
            "FBI", "DEA", "ATF", "TSA", "FEMA", "CIA", "NSA", "EPA", "FDA",
            "CDC", "IRS", "USPS", "FAA", "NASA", "WHO", "NATO", "UN", "DOJ",
            "DOD", "DHS", "VA", "OSHA", "FAFSA", "Medicare", "Medicaid",
            "SSA", "SNAP", "DMV",
        } else 0.6
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="government",
            subcategory="agency",
            context_blurb=expansion[:140],
            popularity_score=score,
            source="Curated US government acronyms (DOJ + agency websites)",
        )
    logger.info("Government acronyms: %d yielded", len(seen))


name = "Government Acronyms"
category = "government"
