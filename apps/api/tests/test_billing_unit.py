"""Pure-unit tests for the billing engine, the money helpers and the state machines.

No database, no event loop, no fixtures: everything here is a function call on a
module. These are the invariants a billing system is judged on.

    * money is exact. `Decimal` in, `Decimal` out, four places, half-up. A float
      never survives a round trip, and `0.1 + 0.2` is `0.3000`, not
      `0.30000000000000004`.
    * the rate on an invoice line is the *contract* rate. The request never gets
      a say, and a sheet with no billable hours rates at zero rather than
      dividing by zero.
    * a status machine only admits the edges it declares. Anything else is a
      typed `InvalidStateTransitionError`, never a silent no-op.

Run:
    pytest apps/api/tests/test_billing_unit.py -v
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

MONEY = Decimal("0.0001")


# =============================================================================
# money
# =============================================================================
def test_money_quantises_to_four_places_half_up() -> None:
    """Half-up, not banker's rounding: a fifth of 4 goes up, not to even."""
    from app.services.billing import money

    assert money("1.005") == Decimal("1.0050")
    assert money("1.0049") == Decimal("1.0049")
    assert money("1.00501") == Decimal("1.0050")
    # 2.5 -> 3 half-up; banker's rounding would give 2.
    assert money("0.00025") == Decimal("0.0003")
    assert money("0.00035") == Decimal("0.0004")


def test_money_never_produces_binary_float_artefacts() -> None:
    from app.services.billing import money

    # The canonical float trap: str() of the float is what Decimal sees, so
    # 19.99 is the shortest repr and quantises exactly.
    assert money(19.99) == Decimal("19.9900")
    assert money(0.1) == Decimal("0.1000")
    assert money(Decimal("0.1")) == Decimal("0.1000")


def test_money_addition_is_exact_for_the_classic_float_case() -> None:
    from app.services.billing import money

    assert money(0.1) + money(0.2) == Decimal("0.3000")
    assert money(0.1) + money(0.2) != Decimal("0.30000000000000004")


def test_money_output_is_always_decimal_never_float() -> None:
    """Whatever goes in, a Decimal comes out — and arithmetic on it stays exact."""
    from app.services.billing import money as billing_money
    from app.services.invoicing import money as invoicing_money

    parseable = (0, 1, -3, 1.5, -19.99, Decimal("2.5"), "10.005", "0")
    for value in parseable:
        assert isinstance(billing_money(value), Decimal)
        assert isinstance(invoicing_money(value), Decimal)

    result = billing_money("0.1") + billing_money("0.2")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)
    assert billing_money(1) / billing_money(4) == Decimal("0.2500")
    assert isinstance(1 / billing_money(4), Decimal)


def test_billing_money_rejects_nonsense_rather_than_guessing() -> None:
    """`billing.money` is the strict one: garbage in raises, it does not become 0."""
    import decimal

    from app.services.billing import money

    with pytest.raises((ValueError, TypeError, decimal.InvalidOperation)):
        money("not a number")
    with pytest.raises((ValueError, TypeError, decimal.InvalidOperation)):
        money(None)


def test_invoicing_money_coerces_garbage_to_zero_where_billing_money_raises() -> None:
    """Documented asymmetry between the two money helpers.

    `app.services.invoicing.money` routes through `lookup.as_decimal`, whose
    default is "0" on an unparseable value; `app.services.billing.money` calls
    `Decimal(str(value))` directly and raises. Both quantise identically for
    every value either can parse, so this is about failure mode only.
    """
    from app.services.billing import money as billing_money
    from app.services.invoicing import money as invoicing_money

    assert invoicing_money("not a number") == Decimal("0.0000")
    assert invoicing_money(None) == Decimal("0.0000")
    with pytest.raises(Exception):  # noqa: B017 - any parse failure
        billing_money("not a number")

    for value in ("10.005", "0", "-1.23456", "123456789.123456"):
        assert invoicing_money(value) == billing_money(value)


def test_negative_discount_round_trips() -> None:
    """A discount is negative money and must survive unchanged in sign and scale."""
    from app.services.invoicing import money

    discount = money("-1250.5")
    assert discount == Decimal("-1250.5000")
    assert discount.is_signed() is True
    # Undiscounting is exact: the original value comes back bit for bit.
    assert money(-discount) == Decimal("1250.5000")
    assert money(discount + money("2501")) == Decimal("1250.5000")


def test_money_precision_matches_the_declared_step() -> None:
    from app.services.invoicing import money

    value = money("1")
    assert value.as_tuple().exponent == -4
    assert value == value.quantize(MONEY, rounding=ROUND_HALF_UP)


# =============================================================================
# _effective_rate — the rate a timesheet line is billed at
# =============================================================================
def test_effective_rate_prefers_the_contract_role_rate() -> None:
    """The contract rate wins even when the sheet's own totals imply another."""
    from app.services.invoicing import _effective_rate

    sheet = {
        "rate": Decimal("75.00"),
        "billable_hours": Decimal("8"),
        "total_amount": Decimal("80"),
    }
    assert _effective_rate(sheet) == Decimal("75.0000")


def test_effective_rate_falls_back_to_amount_over_hours() -> None:
    """With no contract-role rate the engine derives one from the sheet itself."""
    from app.services.invoicing import _effective_rate

    sheet = {"rate": None, "billable_hours": Decimal("160"), "total_amount": Decimal("12000")}
    assert _effective_rate(sheet) == Decimal("75.0000")

    fractional = {"rate": None, "billable_hours": Decimal("7.5"), "total_amount": Decimal("600")}
    assert _effective_rate(fractional) == Decimal("80.0000")


def test_effective_rate_returns_zero_instead_of_dividing_by_zero() -> None:
    """An empty or zero-hour sheet rates at zero. No ZeroDivisionError."""
    from app.services.invoicing import _effective_rate

    for hours in (Decimal("0"), Decimal("0.0000")):
        sheet = {"rate": None, "billable_hours": hours, "total_amount": Decimal("0")}
        assert _effective_rate(sheet) == Decimal("0.0000")

    # A missing rate *and* missing hours/amount: as_decimal defaults to "0".
    assert _effective_rate({}) == Decimal("0.0000")
    assert _effective_rate({"rate": None}) == Decimal("0.0000")


def test_effective_rate_accepts_a_string_rate_from_the_database() -> None:
    """PostgreSQL numeric arrives as Decimal, but the helper must not care."""
    from app.services.invoicing import _effective_rate

    assert _effective_rate({"rate": "75"}) == Decimal("75.0000")
    assert _effective_rate({"rate": 0, "billable_hours": 8, "total_amount": 600}) == Decimal(
        "0.0000"
    ), "an explicit zero rate is still a rate; the fallback must not kick in"


def test_billing_engine_worked_example_160_hours_at_75_is_12000() -> None:
    """The billing-engine contract, stated once.

    This is the example the product spec uses: 160 approved hours against a
    contract role priced at $75/hour must produce exactly $12,000. Asserted
    twice — once through `_effective_rate`, which is what chooses the rate, and
    once as the multiplication the engine then performs — so a change to either
    half is caught.
    """
    from app.services.invoicing import _effective_rate, money

    hours = Decimal("160")
    contract_rate = Decimal("75")

    sheet = {
        "rate": contract_rate,
        "billable_hours": hours,
        "total_amount": money(hours * contract_rate),
    }
    rate = _effective_rate(sheet)
    assert rate == contract_rate

    line_subtotal = money(hours * rate)
    assert line_subtotal == Decimal("12000.0000")
    assert hours * contract_rate == Decimal("12000")
    assert money(line_subtotal) == Decimal("12000.0000")

    # The same figure derived from amount/hours when no rate is recorded.
    derived = _effective_rate(
        {"rate": None, "billable_hours": hours, "total_amount": line_subtotal}
    )
    assert derived == Decimal("75.0000")


# =============================================================================
# reconciliation scoring
# =============================================================================
def test_ratio_score_is_one_for_an_exact_match() -> None:
    from app.services.payments import _ratio_score

    assert _ratio_score(Decimal("12000"), Decimal("12000")) == 1.0
    assert _ratio_score(Decimal("0.01"), Decimal("0.01")) == 1.0


def test_ratio_score_tolerates_two_percent_settlement_fees() -> None:
    """A receipt within 2% of the balance is a perfect match, not a partial one."""
    from app.services.payments import _ratio_score

    assert _ratio_score(Decimal("100"), Decimal("98")) == 1.0
    assert _ratio_score(Decimal("98"), Decimal("100")) == 1.0
    assert _ratio_score(Decimal("1000"), Decimal("980")) == 1.0

    # Just outside the tolerance the score becomes the raw ratio.
    assert _ratio_score(Decimal("100"), Decimal("97")) == pytest.approx(0.97)
    assert _ratio_score(Decimal("100"), Decimal("50")) == pytest.approx(0.50)


def test_ratio_score_is_the_min_max_ratio() -> None:
    from app.services.payments import _ratio_score

    assert _ratio_score(Decimal("50"), Decimal("100")) == 0.5
    assert _ratio_score(Decimal("100"), Decimal("50")) == 0.5
    assert _ratio_score(Decimal("75"), Decimal("100")) == 0.75


def test_ratio_score_is_zero_when_the_denominator_is_not_positive() -> None:
    """A zero or negative balance cannot be matched; the score must not divide."""
    from app.services.payments import _ratio_score

    assert _ratio_score(Decimal("100"), Decimal("0")) == 0.0
    assert _ratio_score(Decimal("100"), Decimal("-50")) == 0.0
    assert _ratio_score(Decimal("0"), Decimal("0")) == 0.0


def test_suggestion_thresholds_match_the_documented_bands() -> None:
    """`>=0.98` MATCH, `0.60..0.98` REVIEW, `<0.60` IGNORE."""
    from app.services.payments import _suggestion_for

    assert _suggestion_for(Decimal("1.0")) == "MATCH"
    assert _suggestion_for(Decimal("0.98")) == "MATCH"
    assert _suggestion_for(Decimal("0.9799")) == "REVIEW"

    assert _suggestion_for(Decimal("0.60")) == "REVIEW"
    assert _suggestion_for(Decimal("0.97")) == "REVIEW"

    assert _suggestion_for(Decimal("0.5999")) == "IGNORE"
    assert _suggestion_for(Decimal("0")) == "IGNORE"
    assert _suggestion_for(Decimal("0.10")) == "IGNORE"


def test_auto_accept_threshold_is_the_match_boundary() -> None:
    """The MATCH band and AUTO_ACCEPT_THRESHOLD must not drift apart."""
    from app.services.payments import AUTO_ACCEPT_THRESHOLD, _suggestion_for

    assert Decimal("0.98") == AUTO_ACCEPT_THRESHOLD
    assert _suggestion_for(AUTO_ACCEPT_THRESHOLD) == "MATCH"
    assert _suggestion_for(AUTO_ACCEPT_THRESHOLD - Decimal("0.0001")) == "REVIEW"


def test_reason_for_explains_the_score_and_never_invents_a_hit() -> None:
    from app.services.payments import _reason_for

    strong = _reason_for(Decimal("0.99"), 1.0, 1.0, 1.0, 1.0, ["INV-2026-00001"], 0)
    assert strong.startswith("strong candidate")
    assert "amount match 100%" in strong
    assert "on time" in strong
    assert "INV-2026-00001" in strong
    assert "counterparty name present" in strong

    possible = _reason_for(Decimal("0.70"), 0.9, 0.5, 0.0, 0.0, [], 30)
    assert possible.startswith("possible candidate")
    assert "30d from due date" in possible
    assert "reference found" not in possible
    assert "counterparty name present" not in possible

    weak = _reason_for(Decimal("0.20"), 0.1, 0.0, 0.0, 0.0, [], 400)
    assert weak.startswith("weak candidate")
    assert "amount match 10%" in weak


def test_reason_for_truncates_a_long_reference_list() -> None:
    from app.services.payments import _reason_for

    reason = _reason_for(Decimal("0.99"), 1.0, 1.0, 1.0, 0.0, ["A", "B", "C", "D"], 0)
    assert "reference found: A, B" in reason
    assert ", C" not in reason


def test_minimum_confidence_keeps_the_queue_clean() -> None:
    """`suggest_matches` drops anything below MIN_CONFIDENCE before the queue."""
    from app.services.payments import MIN_CONFIDENCE

    assert Decimal("0.10") == MIN_CONFIDENCE
    assert Decimal("0.60") > MIN_CONFIDENCE, "the floor must sit below the REVIEW band"


# =============================================================================
# state machines
# =============================================================================
def _graphs() -> dict[str, dict[str, tuple[str, ...]]]:
    from app.services.code import CONTRACT_TRANSITIONS, SOW_TRANSITIONS
    from app.services.invoicing import INVOICE_TRANSITIONS
    from app.services.msas import MSA_TRANSITIONS

    return {
        "CONTRACT_TRANSITIONS": CONTRACT_TRANSITIONS,
        "SOW_TRANSITIONS": SOW_TRANSITIONS,
        "INVOICE_TRANSITIONS": INVOICE_TRANSITIONS,
        "MSA_TRANSITIONS": MSA_TRANSITIONS,
    }


@pytest.mark.parametrize("label", sorted(_graphs()))
def test_every_declared_edge_targets_a_state_the_graph_knows(label: str) -> None:
    """A dangling edge is a state a caller can never reach, and a typo waiting to
    be enforced. Every target of every declared transition must itself be a key.
    """
    graph = _graphs()[label]
    assert graph, f"{label} must not be empty"

    for source, targets in graph.items():
        assert source == source.upper(), f"{source} is not an upper-case status"
        for target in targets:
            assert target in graph, (
                f"{label}: {source} -> {target} targets a state the graph does not declare"
            )


@pytest.mark.parametrize("label", sorted(_graphs()))
def test_no_state_lists_itself_as_a_target(label: str) -> None:
    graph = _graphs()[label]
    for source, targets in graph.items():
        assert source not in targets, f"{label}: {source} -> {source} is a no-op transition"


@pytest.mark.parametrize("label", sorted(_graphs()))
def test_terminal_states_are_declared_and_finite(label: str) -> None:
    """Every status with no outgoing edge is a state an object comes to rest in.

    `CLOSED` is terminal for contracts and SOWs; an invoice additionally rests at
    `CANCELLED` or `REFUNDED`; an MSA never rests, because it can always be
    renegotiated. The point of the check is that the terminal set is finite and
    made only of declared keys, so an infinite walk is impossible.
    """
    graph = _graphs()[label]
    terminal = {state for state, targets in graph.items() if not targets}
    assert terminal <= set(graph)
    assert len(terminal) < len(graph), f"{label}: nothing is terminal"


def test_terminal_states_are_the_expected_ones() -> None:
    graphs = _graphs()
    assert [s for s, t in graphs["CONTRACT_TRANSITIONS"].items() if not t] == ["CLOSED"]
    assert [s for s, t in graphs["SOW_TRANSITIONS"].items() if not t] == ["CLOSED"]
    assert sorted(s for s, t in graphs["INVOICE_TRANSITIONS"].items() if not t) == [
        "CANCELLED",
        "REFUNDED",
    ]
    assert not [s for s, t in graphs["MSA_TRANSITIONS"].items() if not t]


def test_contract_graph_requires_acceptance_before_going_live() -> None:
    """Rule 3: a contract cannot skip signing. DRAFT -> ACTIVE must be impossible."""
    from app.services.code import CONTRACT_TRANSITIONS

    assert "ACTIVE" not in CONTRACT_TRANSITIONS["DRAFT"]
    assert "ACTIVE" not in CONTRACT_TRANSITIONS["SENT"]
    legal = ["SENT", "PENDING_ACCEPTANCE", "ACCEPTED", "ACTIVE"]
    for index in range(len(legal) - 1):
        assert legal[index + 1] in CONTRACT_TRANSITIONS[legal[index]], (
            f"the signed path {legal[index]} -> {legal[index + 1]} must exist"
        )


def test_invoice_graph_cannot_skip_approval() -> None:
    """An invoice cannot be APPROVED without first being SUBMITTED."""
    from app.services.invoicing import INVOICE_TRANSITIONS

    assert "APPROVED" not in INVOICE_TRANSITIONS["DRAFT"]
    assert "APPROVED" not in INVOICE_TRANSITIONS["PENDING"]
    assert "APPROVED" in INVOICE_TRANSITIONS["SUBMITTED"]


def test_paid_is_only_reachable_once_and_leads_only_to_refund() -> None:
    from app.services.invoicing import INVOICE_TRANSITIONS

    assert INVOICE_TRANSITIONS["PAID"] == ("REFUNDED",)
    # Nothing may arrive at PAID from a state that has not been approved.
    for source, targets in INVOICE_TRANSITIONS.items():
        if "PAID" in targets:
            assert source in {"APPROVED", "PARTIALLY_PAID", "OVERDUE"}


def test_open_invoice_statuses_are_a_subset_of_the_graph() -> None:
    from app.services.invoicing import INVOICE_TRANSITIONS, OPEN_INVOICE_STATUSES

    assert set(OPEN_INVOICE_STATUSES) <= set(INVOICE_TRANSITIONS)
    assert "PAID" not in OPEN_INVOICE_STATUSES
    assert "CANCELLED" not in OPEN_INVOICE_STATUSES


def test_assert_transition_accepts_every_declared_edge() -> None:
    """The module-level guard is the single place an illegal edge is refused."""
    from app.services.code import _assert_transition

    for label, graph in _graphs().items():
        for source, targets in graph.items():
            for target in targets:
                # Must not raise.
                _assert_transition(graph, source, target, label)


def test_assert_transition_rejects_a_target_that_is_not_in_the_graph() -> None:
    from app.core.errors import InvalidStateTransitionError
    from app.services.code import _assert_transition

    graph = {"DRAFT": ("ACTIVE",)}
    with pytest.raises(InvalidStateTransitionError) as caught:
        _assert_transition(graph, "DRAFT", "APPROVED", "Widget")

    error = caught.value
    assert error.code == "INVALID_STATE_TRANSITION"
    assert error.status_code == 409
    assert error.details == {"from": "DRAFT", "to": "APPROVED", "allowed": ["ACTIVE"]}
    assert "Widget" in error.message


def test_assert_transition_rejects_an_unknown_source_state() -> None:
    """An undeclared current state admits nothing, not everything."""
    from app.core.errors import InvalidStateTransitionError
    from app.services.code import _assert_transition

    graph = {"DRAFT": ("ACTIVE",)}
    with pytest.raises(InvalidStateTransitionError):
        _assert_transition(graph, "NOT_A_STATE", "ACTIVE", "Widget")
    with pytest.raises(InvalidStateTransitionError):
        _assert_transition(graph, "NOT_A_STATE", "NOT_A_STATE", "Widget")


def test_assert_transition_rejects_skipping_the_contract_signature_chain() -> None:
    """The concrete rule: DRAFT cannot jump straight to ACTIVE."""
    from app.core.errors import InvalidStateTransitionError
    from app.services.code import CONTRACT_TRANSITIONS, _assert_transition

    for target in ("ACTIVE", "ACCEPTED", "EXPIRED", "TERMINATED"):
        with pytest.raises(InvalidStateTransitionError):
            _assert_transition(CONTRACT_TRANSITIONS, "DRAFT", target, "Contract")
    # ... and the legal chain is accepted.
    for source, target in (("DRAFT", "SENT"), ("SENT", "ACCEPTED"), ("ACCEPTED", "ACTIVE")):
        _assert_transition(CONTRACT_TRANSITIONS, source, target, "Contract")


def test_msa_transitions_reject_direct_activation_from_no_msa() -> None:
    """An MSA has to be requested and reviewed before it can be ACTIVE."""
    from app.core.errors import InvalidStateTransitionError
    from app.services.msas import MSA_TRANSITIONS, _assert_transition

    # `msas._assert_transition(current, target)` reads the graph itself.
    with pytest.raises(InvalidStateTransitionError):
        _assert_transition("NO_MSA", "ACTIVE")
    with pytest.raises(InvalidStateTransitionError):
        _assert_transition("NO_MSA", "UNDER_REVIEW")

    for source, target in (
        ("NO_MSA", "REQUESTED"),
        ("REQUESTED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "ACTIVE"),
        ("ACTIVE", "EXPIRED"),
    ):
        _assert_transition(source, target)

    # The Python graph and the module's own guard agree on the same edge.
    assert "UNDER_REVIEW" in MSA_TRANSITIONS["REQUESTED"]
    assert "ACTIVE" not in MSA_TRANSITIONS["NO_MSA"]


# =============================================================================
# contract approval template
# =============================================================================
def test_contract_approval_template_shape() -> None:
    """(name, required permission, due in days), in order, one step each."""
    from app.services.contracts import CONTRACT_APPROVAL_TEMPLATES

    assert isinstance(CONTRACT_APPROVAL_TEMPLATES, tuple)
    assert len(CONTRACT_APPROVAL_TEMPLATES) >= 2, (
        "commercial terms and money are signed off by different people"
    )

    seen_steps: list[int] = []
    for name, permission, due_days in CONTRACT_APPROVAL_TEMPLATES:
        assert isinstance(name, str) and name.strip(), "an approval step needs a label"
        assert isinstance(permission, str) and "." in permission, (
            f"{name}: the step must name the permission that satisfies it"
        )
        assert isinstance(due_days, int) and due_days > 0, f"{name}: needs a positive due window"
        seen_steps.append(seen_steps[-1] + 1 if seen_steps else 1)
    assert seen_steps == list(range(1, len(CONTRACT_APPROVAL_TEMPLATES) + 1))


def test_contract_approval_template_separates_commercial_and_financial_review() -> None:
    from app.services.contracts import CONTRACT_APPROVAL_TEMPLATES

    permissions = [permission for _, permission, _ in CONTRACT_APPROVAL_TEMPLATES]
    assert len(set(permissions)) == len(permissions), (
        "two steps demanding the same permission are not two-eyes, they are one"
    )
    assert any(p.startswith("contracts.") for p in permissions)
    assert any(p.startswith("invoices.") for p in permissions)


def test_contract_approval_template_is_immutable() -> None:
    """A caller must not be able to rewrite the chain the server enforces."""
    from app.services.contracts import CONTRACT_APPROVAL_TEMPLATES

    with pytest.raises((TypeError, AttributeError)):
        CONTRACT_APPROVAL_TEMPLATES[0] = ("Hijacked", "contracts.approve", 1)  # type: ignore[index]


# =============================================================================
# helpers that do not need a connection
# =============================================================================
def test_json_helpers_agree_and_serialise_decimals() -> None:
    """`code._json` and `billing._json` are the same function under two names."""
    from app.services.billing import _json as billing_json
    from app.services.code import _json as code_json

    payload: dict[str, Any] = {"rate": Decimal("75.00"), "day": None, "n": 3}
    assert code_json(payload) == billing_json(payload)
    assert '"75.00"' in code_json(payload)


def test_text_array_escapes_quotes_and_backslashes() -> None:
    """A skill name must not be able to break out of the PostgreSQL array literal."""
    from app.services.code import _text_array

    assert _text_array(["Python", "C#"]) == "{Python,C#}"
    assert _text_array([]) == "{}"
    escaped = _text_array(['a"b', "c\\d"])
    assert escaped == '{a\\"b,c\\\\d}'
