"""Curated business / finance acronyms.

The "Wikipedia list of business and finance abbreviations" is short
enough that hand-curating it gives better quality than automated
parsing. Source vocabulary cross-checked against:
- SEC filings standard terminology
- Investor-day/earnings-call common usage
- Bloomberg / WSJ style guides
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..pronunciation import derive_acronym_pronunciations
from ..types import RawTerm

logger = logging.getLogger(__name__)


# (canonical, expansion). All upper-cased. Subcategory left empty —
# everything here goes under business/acronym.
_TERMS: list[tuple[str, str]] = [
    # Financial reporting
    ("EBITDA", "Earnings Before Interest, Taxes, Depreciation, and Amortization"),
    ("EBIT", "Earnings Before Interest and Taxes"),
    ("CAPEX", "Capital Expenditure"),
    ("OPEX", "Operating Expenditure"),
    ("COGS", "Cost of Goods Sold"),
    ("SG&A", "Selling, General, and Administrative Expenses"),
    ("FCF", "Free Cash Flow"),
    ("ROI", "Return on Investment"),
    ("ROIC", "Return on Invested Capital"),
    ("ROE", "Return on Equity"),
    ("ROA", "Return on Assets"),
    ("EPS", "Earnings Per Share"),
    ("P/E", "Price-to-Earnings Ratio"),
    ("DCF", "Discounted Cash Flow"),
    ("NPV", "Net Present Value"),
    ("IRR", "Internal Rate of Return"),
    ("WACC", "Weighted Average Cost of Capital"),
    ("LTV", "Lifetime Value"),
    ("CAC", "Customer Acquisition Cost"),
    ("MRR", "Monthly Recurring Revenue"),
    ("ARR", "Annual Recurring Revenue"),
    ("NRR", "Net Revenue Retention"),
    ("GRR", "Gross Revenue Retention"),
    ("ACV", "Annual Contract Value"),
    ("TCV", "Total Contract Value"),
    ("DAU", "Daily Active Users"),
    ("MAU", "Monthly Active Users"),
    ("WAU", "Weekly Active Users"),
    ("ARPU", "Average Revenue Per User"),
    ("ARPPU", "Average Revenue Per Paying User"),
    ("CTR", "Click-Through Rate"),
    ("CPC", "Cost Per Click"),
    ("CPM", "Cost Per Mille"),
    ("CPA", "Cost Per Acquisition"),
    # Common abbreviations
    ("YoY", "Year over Year"),
    ("QoQ", "Quarter over Quarter"),
    ("MoM", "Month over Month"),
    ("YTD", "Year to Date"),
    ("MTD", "Month to Date"),
    ("QTD", "Quarter to Date"),
    ("FY", "Fiscal Year"),
    ("Q1", "First Quarter"),
    ("Q2", "Second Quarter"),
    ("Q3", "Third Quarter"),
    ("Q4", "Fourth Quarter"),
    ("H1", "First Half"),
    ("H2", "Second Half"),
    # Org / strategy
    ("KPI", "Key Performance Indicator"),
    ("OKR", "Objectives and Key Results"),
    ("BHAG", "Big Hairy Audacious Goal"),
    ("PMF", "Product-Market Fit"),
    ("GTM", "Go to Market"),
    ("MVP", "Minimum Viable Product"),
    ("POC", "Proof of Concept"),
    ("RFP", "Request for Proposal"),
    ("RFI", "Request for Information"),
    ("RFQ", "Request for Quote"),
    ("SOW", "Statement of Work"),
    ("MSA", "Master Services Agreement"),
    ("NDA", "Non-Disclosure Agreement"),
    ("LOI", "Letter of Intent"),
    ("TOS", "Terms of Service"),
    ("SLA", "Service Level Agreement"),
    ("SLO", "Service Level Objective"),
    ("EULA", "End User License Agreement"),
    # Accounting / tax
    ("GAAP", "Generally Accepted Accounting Principles"),
    ("IFRS", "International Financial Reporting Standards"),
    ("FASB", "Financial Accounting Standards Board"),
    ("IRS", "Internal Revenue Service"),
    ("EIN", "Employer Identification Number"),
    ("W-2", "Wage and Tax Statement"),
    ("W-4", "Employee's Withholding Certificate"),
    ("W-9", "Request for Taxpayer Identification Number"),
    ("1099", "Information Return"),
    ("401(k)", "Defined Contribution Retirement Plan"),
    ("IRA", "Individual Retirement Account"),
    ("Roth IRA", "After-Tax Retirement Account"),
    ("HSA", "Health Savings Account"),
    ("FSA", "Flexible Spending Account"),
    # M&A / corporate finance
    ("M&A", "Mergers and Acquisitions"),
    ("LBO", "Leveraged Buyout"),
    ("MBO", "Management Buyout"),
    ("IPO", "Initial Public Offering"),
    ("DPO", "Direct Public Offering"),
    ("SPAC", "Special Purpose Acquisition Company"),
    ("SPV", "Special Purpose Vehicle"),
    ("PE", "Private Equity"),
    ("VC", "Venture Capital"),
    ("HF", "Hedge Fund"),
    # Banking / regulatory
    ("FDIC", "Federal Deposit Insurance Corporation"),
    ("SEC", "Securities and Exchange Commission"),
    ("FINRA", "Financial Industry Regulatory Authority"),
    ("CFTC", "Commodity Futures Trading Commission"),
    ("OCC", "Office of the Comptroller of the Currency"),
    ("FRB", "Federal Reserve Board"),
    ("Fed", "Federal Reserve"),
    ("FOMC", "Federal Open Market Committee"),
    ("ECB", "European Central Bank"),
    ("BOJ", "Bank of Japan"),
    ("PBOC", "People's Bank of China"),
    ("BoE", "Bank of England"),
    ("CFPB", "Consumer Financial Protection Bureau"),
    ("AML", "Anti-Money Laundering"),
    ("KYC", "Know Your Customer"),
    ("PCI", "Payment Card Industry"),
    ("PCI-DSS", "Payment Card Industry Data Security Standard"),
    ("SOC", "Service Organization Control"),
    ("SOX", "Sarbanes-Oxley Act"),
    # Common workplace
    ("WFH", "Work From Home"),
    ("OOO", "Out of Office"),
    ("EOD", "End of Day"),
    ("COB", "Close of Business"),
    ("ETA", "Estimated Time of Arrival"),
    ("ETD", "Estimated Time of Departure"),
    ("FYI", "For Your Information"),
    ("FYR", "For Your Reference"),
    ("ICYMI", "In Case You Missed It"),
    ("LMK", "Let Me Know"),
    ("TBA", "To Be Announced"),
    ("TBD", "To Be Determined"),
    ("TBC", "To Be Confirmed"),
    ("HR", "Human Resources"),
    ("PR", "Public Relations"),
    ("IR", "Investor Relations"),
    ("BD", "Business Development"),
    ("CRM", "Customer Relationship Management"),
    ("ERP", "Enterprise Resource Planning"),
    ("SCM", "Supply Chain Management"),
    ("PM", "Project Manager"),
    ("PO", "Product Owner"),
    ("QA", "Quality Assurance"),
    ("QC", "Quality Control"),
    ("UX", "User Experience"),
    ("UI", "User Interface"),
    ("DEI", "Diversity, Equity, and Inclusion"),
    ("ESG", "Environmental, Social, and Governance"),
    # Insurance / benefits
    ("PPO", "Preferred Provider Organization"),
    ("HMO", "Health Maintenance Organization"),
    ("HDHP", "High-Deductible Health Plan"),
    ("EOB", "Explanation of Benefits"),
    ("COBRA", "Consolidated Omnibus Budget Reconciliation Act"),
]


# Business acronyms commonly pronounced as words.
_WORD_PRONOUNCED: dict[str, list[str]] = {
    "EBITDA": ["ee-bit-duh", "ee bit duh"],
    "GAAP": ["gap"],
    "SOX": ["socks"],
    "FOMC": ["ef oh em see"],
    "WACC": ["whack"],
    "OPEX": ["oh-pex", "op ex"],
    "CAPEX": ["cap-ex", "cap ex"],
    "COGS": ["cogs"],
    "BHAG": ["bee hag", "bee-hag"],
    "EULA": ["yu-luh"],
    "SCRUM": ["scrum"],
    "AML": ["ay em ell"],
    "KYC": ["kay why see"],
    "HSA": ["aitch ess ay"],
    "FSA": ["ef ess ay"],
    "IRA": ["eye ar ay", "eye-ar-ay"],
    "PPO": ["pee pee oh"],
    "HMO": ["aitch em oh"],
    "HDHP": ["aitch dee aitch pee"],
    "PCI-DSS": ["pee see eye dee ess ess"],
    "COBRA": ["koh-bruh", "koh bruh"],
    "FOMO": ["foh moh"],
    "Fed": ["fed"],
    "SPAC": ["spack"],
    "SPV": ["ess pee vee"],
    "REIT": ["reet", "ree it"],
    "FOMC": ["ef oh em see"],
}


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, expansion in _TERMS:
        key = canonical.upper()
        if key in seen:
            continue
        seen.add(key)
        sounds_like = derive_acronym_pronunciations(
            canonical,
            extra_hints=_WORD_PRONOUNCED.get(canonical, []),
        )
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="business",
            subcategory="acronym",
            sounds_like=sounds_like,
            context_blurb=expansion[:140],
            popularity_score=0.7,
            source="Curated business / finance abbreviations",
        )
    logger.info("Business acronyms (curated): %d yielded", len(seen))


name = "Business Acronyms (Curated)"
category = "business"
