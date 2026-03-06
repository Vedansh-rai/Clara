"""
prompt_builder.py — AgentConfig → Retell voice agent system prompt.

Generates a concise, production-ready Retell prompt using a canonical template:

  You are Clara, an AI assistant for {{company_name}}.

  Business Hours Flow      (5 numbered steps)
  After-Hours Flow         (3 numbered steps, emergency + non-emergency paths)

Known values from AgentConfig are substituted directly.
Values not yet confirmed are emitted as {{retell_variable}} placeholders so that
Retell can inject them at call-time, or they appear in the Open Questions section.
"""

from __future__ import annotations

from pipeline.schema import AgentConfig

# Retell runtime placeholder format — used when a value is unknown at build time
_VAR = staticmethod(lambda name: "{{" + name + "}}")


def build_prompt(config: AgentConfig) -> str:
    """Generate the full Retell system prompt for a Clara voice agent."""
    sections = [
        _identity(config),
        _business_hours_flow(config),
        _after_hours_flow(config),
        _routing_rules(config),
        _integration_and_constraints(config),
        _general_rules(),
        _open_questions(config),
    ]
    return "\n\n".join(s for s in sections if s.strip())


def build_final_prompt_from_v2(config: AgentConfig) -> str:
    """Generate the assignment-style deployment prompt from a v2 AgentConfig."""
    company = config.company_name or _VAR("company_name")
    transfer_timeout = config.transfer_timeout_seconds or 60
    emergency_destination = _emergency_transfer_target_label(config)
    fallback = config.fallback_logic or "Apologize and confirm dispatch will follow up."

    return "\n".join(
        [
            "Agent: Clara",
            "",
            f"Company: {company}",
            "",
            "BUSINESS HOURS FLOW",
            "",
            "1. Greet the caller warmly.",
            "2. Ask the purpose of the call.",
            "3. Collect caller name and phone number.",
            "4. Route call based on purpose:",
            f"   - Emergency \u2192 transfer immediately to {emergency_destination}.",
            "   - Non-emergency \u2192 collect service request details and confirm next steps.",
            f"5. If transfer fails after {transfer_timeout} seconds: {fallback}",
            "6. Confirm next steps with the caller.",
            "7. Ask if the caller needs anything else.",
            "8. Close the call professionally.",
            "",
            "AFTER HOURS FLOW",
            "",
            "1. Greet the caller and identify yourself as Clara.",
            "2. Ask the purpose of the call.",
            "3. Confirm whether this is an emergency.",
            "",
            "If emergency:",
            "   - Collect caller name, phone number, and service address.",
            f"   - Attempt transfer to {emergency_destination}.",
            f"   - If transfer fails after {transfer_timeout} seconds: {fallback}",
            "",
            "If non-emergency:",
            "   - Collect service request details.",
            "   - Confirm follow-up next business day.",
            "",
            "8. Ask if the caller needs anything else.",
            "9. Close the call.",
        ]
    )


# ─── Sections ────────────────────────────────────────────────────────────────


def _identity(cfg: AgentConfig) -> str:
    company = cfg.company_name or _VAR("company_name")
    return f"You are Clara, an AI assistant for {company}."


def _business_hours_flow(cfg: AgentConfig) -> str:
    timeout = (
        str(cfg.transfer_timeout_seconds)
        if cfg.transfer_timeout_seconds
        else _VAR("transfer_timeout")
    )
    dispatch = _emergency_transfer_target(cfg)
    fallback = cfg.fallback_logic or "inform the caller and leave a message for dispatch"

    hours_block = _hours_summary(cfg)
    emergency_block = _emergency_definitions_block(cfg)

    return f"""Business Hours Flow:
{hours_block}
1. Greeting
2. Ask purpose of call
3. Collect name and phone number
4. Route call / Transfer
5. If transfer fails after {timeout} seconds: {fallback}
6. Ask if caller needs anything else
7. Close call if no

{_routing_block(cfg)}
If emergency:
Transfer immediately to {dispatch}."""


def _after_hours_flow(cfg: AgentConfig) -> str:
    company = cfg.company_name or _VAR("company_name")
    dispatch = _emergency_transfer_target(cfg)
    timeout = (
        str(cfg.transfer_timeout_seconds)
        if cfg.transfer_timeout_seconds
        else _VAR("transfer_timeout")
    )
    fallback = cfg.fallback_logic or "leave a message and assure the caller an on-call technician will follow up"

    hours_block = _hours_summary(cfg)

    return f"""After-Hours Flow:
{hours_block}
1. Greeting
2. Ask purpose
3. Confirm emergency
4. If emergency: collect name, number, address immediately. Attempt transfer to {dispatch}. If transfer fails after {timeout} seconds: {fallback}.
5. If non-emergency: collect details and confirm follow-up during business hours.
6. Ask if they need anything else
7. Close"""


def _routing_rules(cfg: AgentConfig) -> str:
    if not cfg.routing_rules:
        return ""

    lines = ["Routing Rules:"]
    for rule in sorted(cfg.routing_rules, key=lambda r: r.priority):
        dest = cfg.transfer_numbers.get(rule.destination, rule.destination) if cfg.transfer_numbers else rule.destination
        line = f"- {rule.trigger} → {dest}"
        if rule.notes:
            line += f" ({rule.notes})"
        lines.append(line)
    return "\n".join(lines)


def _integration_and_constraints(cfg: AgentConfig) -> str:
    rules = (cfg.integration_rules or []) + (cfg.special_constraints or [])
    if not rules:
        return ""
    lines = ["Special Rules:"]
    lines.extend(f"- {r}" for r in rules)
    return "\n".join(lines)


def _general_rules() -> str:
    return (
        "Always be polite, calm, and professional.\n"
        "Never reveal you are an AI unless directly asked.\n"
        "Never provide pricing, legal, or technical advice.\n"
        "Always confirm caller information by reading it back.\n"
        "If you cannot help, say: \"I'll make sure someone follows up with you.\""
    )


def _open_questions(cfg: AgentConfig) -> str:
    if not cfg.questions_or_unknowns:
        return ""
    lines = ["Open Questions (resolve before deployment):"]
    lines.extend(f"- {q}" for q in cfg.questions_or_unknowns)
    return "\n".join(lines)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _hours_summary(cfg: AgentConfig) -> str:
    """One-line business hours summary or Retell variable."""
    if not cfg.business_hours:
        return ""

    day_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    abbr = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
            "friday": "Fri", "saturday": "Sat", "sunday": "Sun"}

    parts = []
    for day in day_order:
        h = cfg.business_hours.get(day)
        if h is None:
            continue
        if h.closed:
            parts.append(f"{abbr[day]}: Closed")
        elif h.open and h.close:
            parts.append(f"{abbr[day]}: {h.open}–{h.close}")

    tz = cfg.timezone or _VAR("timezone")
    if not parts:
        return ""
    return "Hours (" + tz + "): " + ", ".join(parts)


def _emergency_definitions_block(cfg: AgentConfig) -> str:
    """Inline emergency definition hints, if available."""
    if not cfg.emergency_definitions:
        return ""
    examples = "; ".join(cfg.emergency_definitions[:4])
    return f"\n   (Emergencies include: {examples})"


def _emergency_transfer_target(cfg: AgentConfig) -> str:
    """Return the best emergency/on-call transfer target label or number."""
    if not cfg.transfer_numbers:
        return _VAR("dispatch_number")

    # Prefer keys that look like on-call / emergency contacts
    priority_keywords = ["on_call", "oncall", "emergency", "dispatch", "after_hours"]
    for key in cfg.transfer_numbers:
        if any(kw in key.lower() for kw in priority_keywords):
            return cfg.transfer_numbers[key]

    # Fall back to the first entry
    first_key = next(iter(cfg.transfer_numbers))
    return cfg.transfer_numbers[first_key]


def _routing_block(cfg: AgentConfig) -> str:
    """Short inline routing hint for the business hours flow."""
    if not cfg.routing_rules:
        return ""
    lines = []
    for rule in sorted(cfg.routing_rules, key=lambda r: r.priority):
        dest = cfg.transfer_numbers.get(rule.destination, rule.destination) if cfg.transfer_numbers else rule.destination
        lines.append(f"  - {rule.trigger} → {dest}")
    return "\n" + "\n".join(lines) + "\n"


def _emergency_transfer_target_label(cfg: AgentConfig) -> str:
    if not cfg.transfer_numbers:
        return "dispatch"

    priority_keywords = ["on_call", "oncall", "emergency", "dispatch", "after_hours"]
    for key in cfg.transfer_numbers:
        if any(keyword in key.lower() for keyword in priority_keywords):
            return key.replace("_", " ")

    first_key = next(iter(cfg.transfer_numbers))
    return first_key.replace("_", " ")
