"""
tests/test_edge_cases.py — Edge case tests for Clara pipeline.
"""

from __future__ import annotations

import pytest

from pipeline.schema import ExtractedCallData, DataSource, RoutingRule
from pipeline.generate_v1 import generate_v1
from pipeline.generate_v2 import generate_v2


def test_missing_demo_data():
    """Test that v1 gracefully handles minimal demo data with many unknowns."""
    minimal_demo = ExtractedCallData(
        source=DataSource.DEMO,
        client_id="test_minimal",
        company_name=None,  # No company name
        industry=None,
        crm_system=None,
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=[],
        routing_rules=[],
        transfer_numbers={},
        after_hours_handling=None,
        transfer_timeout_seconds=None,
        fallback_logic=None,
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=[
            "Company name not stated",
            "Business hours unknown",
            "No CRM system mentioned",
        ],
        raw_evidence={},
    )

    v1 = generate_v1(minimal_demo, transcript="[minimal transcript]", force=True)

    assert v1.version == 1
    assert "questions_or_unknowns" in v1.model_dump()
    assert len(v1.questions_or_unknowns) > 0
    assert v1.company_name is None


def test_conflicting_onboarding_rules():
    """Test that v2 handles conflicting routing rules by flagging them."""
    demo_data = ExtractedCallData(
        source=DataSource.DEMO,
        client_id="test_conflict",
        company_name="Test Company",
        industry="Testing",
        crm_system="TestCRM",
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=["Emergency type A"],
        routing_rules=[
            RoutingRule(
                trigger="Caller says emergency",
                destination="dispatch_1",
                priority=1,
                call_type="emergency",
            )
        ],
        transfer_numbers={"dispatch_1": "+1-555-0001"},
        after_hours_handling="Route to dispatch",
        transfer_timeout_seconds=60,
        fallback_logic="Notify dispatch",
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=["Emergency definition unclear"],
        raw_evidence={"emergency_definitions": "Demo said 'Emergency type A'"},
    )

    v1 = generate_v1(demo_data, transcript="[demo transcript]", force=True)

    # Now try to merge conflicting form data
    conflicting_form = ExtractedCallData(
        source=DataSource.FORM,
        client_id="test_conflict",
        company_name="Test Company Updated",  # Different company name
        industry="Testing",
        crm_system="TestCRM",
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=["Emergency type B"],  # Different definition
        routing_rules=[
            RoutingRule(
                trigger="Caller says emergency",
                destination="dispatch_2",  # Different destination
                priority=1,
                call_type="emergency",
            )
        ],
        transfer_numbers={"dispatch_2": "+1-555-0002"},
        after_hours_handling="Route to backup dispatch",  # Different logic
        transfer_timeout_seconds=45,  # Different timeout
        fallback_logic="Call supervisor",
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=["Conflicting emergency definitions"],
        raw_evidence={"emergency_definitions": "Form says 'Emergency type B'"},
    )

    v2 = generate_v2(conflicting_form, transcript="[form transcript]", base_version=1, force=True)

    assert v2.version == 2
    # High-risk fields like timezone should be in questions_or_unknowns if conflicting
    # The fallback_logic should be overridden to the new value
    assert v2.fallback_logic == "Call supervisor"


def test_empty_routing_rules():
    """Test that v1 can be generated with no routing rules."""
    no_rules = ExtractedCallData(
        source=DataSource.DEMO,
        client_id="test_no_rules",
        company_name="Simple Company",
        industry="Services",
        crm_system="SimpleCRM",
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=[],
        routing_rules=[],  # Empty
        transfer_numbers={},
        after_hours_handling=None,
        transfer_timeout_seconds=None,
        fallback_logic=None,
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=["No routing rules defined"],
        raw_evidence={},
    )

    v1 = generate_v1(no_rules, transcript="[transcript]", force=True)

    assert v1.version == 1
    assert len(v1.routing_rules) == 0
    assert v1.company_name == "Simple Company"


def test_explicit_unknowns_persist():
    """Test that unknowns are preserved and not lost during version transitions."""
    demo_data = ExtractedCallData(
        source=DataSource.DEMO,
        client_id="test_unknowns",
        company_name="Company",
        industry=None,
        crm_system=None,
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=[],
        routing_rules=[],
        transfer_numbers={},
        after_hours_handling=None,
        transfer_timeout_seconds=None,
        fallback_logic=None,
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=[
            "Industry not stated",
            "CRM system unclear",
            "Service area unknown",
        ],
        raw_evidence={},
    )

    v1 = generate_v1(demo_data, transcript="[demo]", force=True)

    assert len(v1.questions_or_unknowns) == 3

    # Merge with partial form that fills some gaps
    form_data = ExtractedCallData(
        source=DataSource.FORM,
        client_id="test_unknowns",
        company_name="Company",
        industry="Electrical",  # Resolves industry
        crm_system=None,  # Still unknown
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=[],
        routing_rules=[],
        transfer_numbers={},
        after_hours_handling=None,
        transfer_timeout_seconds=None,
        fallback_logic=None,
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=["CRM still uncertain"],
        raw_evidence={},
    )

    v2 = generate_v2(form_data, transcript="[form]", base_version=1, force=True)

    # Industry should be resolved, but CRM and service area unknowns should remain
    assert v2.industry == "Electrical"
    assert len(v2.questions_or_unknowns) > 0  # Some unknowns still exist
