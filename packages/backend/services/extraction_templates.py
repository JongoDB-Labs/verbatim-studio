"""Domain templates for entity extraction.

Each template provides a prompt, example input, and example output
to guide the LLM's structured extraction from transcript text.
"""

TEMPLATES: dict[str, dict] = {
    "meeting": {
        "prompt": (
            "Extract structured entities from the following meeting transcript. "
            "Identify action items, decisions, questions, participants mentioned by name, "
            "dates/deadlines, and any key topics discussed. "
            "Return a JSON array of objects with keys: entity_type, text, attributes."
        ),
        "example_input": (
            "[Alice] Okay, let's schedule the launch for March 15th. "
            "Bob, can you finalize the marketing deck by Friday?\n"
            "[Bob] Sure, I'll have it ready. We also need to decide on the pricing tier."
        ),
        "example_output": [
            {
                "entity_type": "action_item",
                "text": "finalize the marketing deck by Friday",
                "attributes": {"assignee": "Bob", "deadline": "Friday"},
            },
            {
                "entity_type": "decision",
                "text": "schedule the launch for March 15th",
                "attributes": {"date": "March 15th"},
            },
            {
                "entity_type": "question",
                "text": "decide on the pricing tier",
                "attributes": {},
            },
            {
                "entity_type": "participant",
                "text": "Alice",
                "attributes": {},
            },
            {
                "entity_type": "participant",
                "text": "Bob",
                "attributes": {},
            },
            {
                "entity_type": "deadline",
                "text": "March 15th",
                "attributes": {"context": "product launch"},
            },
        ],
    },
    "medical": {
        "prompt": (
            "Extract structured medical entities from the following clinical transcript. "
            "Identify medications, dosages, diagnoses, symptoms, procedures, "
            "patient instructions, and follow-up items. "
            "Return a JSON array of objects with keys: entity_type, text, attributes."
        ),
        "example_input": (
            "[Dr. Smith] The patient presents with persistent cough and mild fever. "
            "I'm prescribing Amoxicillin 500mg three times daily for 10 days. "
            "We should schedule a follow-up in two weeks. "
            "If symptoms worsen, go to the ER immediately."
        ),
        "example_output": [
            {
                "entity_type": "symptom",
                "text": "persistent cough",
                "attributes": {},
            },
            {
                "entity_type": "symptom",
                "text": "mild fever",
                "attributes": {},
            },
            {
                "entity_type": "medication",
                "text": "Amoxicillin",
                "attributes": {"dosage": "500mg", "frequency": "three times daily", "duration": "10 days"},
            },
            {
                "entity_type": "follow_up",
                "text": "schedule a follow-up in two weeks",
                "attributes": {"timeframe": "two weeks"},
            },
            {
                "entity_type": "instruction",
                "text": "If symptoms worsen, go to the ER immediately",
                "attributes": {"urgency": "high"},
            },
        ],
    },
    "legal": {
        "prompt": (
            "Extract structured legal entities from the following legal transcript. "
            "Identify parties, case references, statutes, rulings, objections, "
            "exhibits, dates, and key legal arguments. "
            "Return a JSON array of objects with keys: entity_type, text, attributes."
        ),
        "example_input": (
            "[Judge] The court will now hear arguments in Case No. 2024-CV-1234, "
            "Smith v. Acme Corp. Counsel for the plaintiff, you may proceed.\n"
            "[Plaintiff Attorney] Your Honor, under Section 402A of the Restatement, "
            "the defendant is strictly liable. I'd like to submit Exhibit A, "
            "the product safety report dated January 10, 2024."
        ),
        "example_output": [
            {
                "entity_type": "case_reference",
                "text": "Case No. 2024-CV-1234",
                "attributes": {"parties": "Smith v. Acme Corp"},
            },
            {
                "entity_type": "party",
                "text": "Smith",
                "attributes": {"role": "plaintiff"},
            },
            {
                "entity_type": "party",
                "text": "Acme Corp",
                "attributes": {"role": "defendant"},
            },
            {
                "entity_type": "statute",
                "text": "Section 402A of the Restatement",
                "attributes": {"topic": "strict liability"},
            },
            {
                "entity_type": "exhibit",
                "text": "Exhibit A",
                "attributes": {"description": "product safety report", "date": "January 10, 2024"},
            },
        ],
    },
}


def get_template(name: str) -> dict | None:
    """Get a domain template by name.

    Args:
        name: Template name (e.g. 'meeting', 'medical', 'legal').

    Returns:
        Template dict with prompt, example_input, example_output, or None if not found.
    """
    return TEMPLATES.get(name)


def list_templates() -> list[str]:
    """List available template names.

    Returns:
        List of template name strings.
    """
    return list(TEMPLATES.keys())
