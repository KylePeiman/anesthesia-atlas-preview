from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from backend import repair
from backend.repair import coverage_recommendations, simulate_leave, solve_repair
from backend.solver import validate_schedule
from test_solver import apply, case, clinician, no_errors, shift, state


def leave(start="2026-09-14T00:00:00-04:00", end="2026-09-28T00:00:00-04:00", **patch):
    value = dict(id="vacation", kind="leave", status="pending", clinician_id="md",
                 start=start, end=end, details={})
    value.update(patch)
    return value


def projected(data, result, request=None):
    return apply(simulate_leave(data, request) if request else data, result)


def test_two_week_preview_repairs_staffing_and_daily_without_applying_leave():
    request = leave()
    items, cases = [], []
    for n in range(16):
        day = datetime.fromisoformat("2026-09-13T07:00:00-04:00") + timedelta(days=n)
        items.append(shift(f"s{n}", day.isoformat(), (day + timedelta(hours=12)).isoformat(), clinician_id="md"))
        cases.append(case(f"c{n}", (day + timedelta(hours=1)).isoformat(), (day + timedelta(hours=4)).isoformat(), clinician_id="md"))
    data = state([clinician(), clinician("replacement")], items, cases, [request], max_weekly_hours=100)
    original = deepcopy(data)
    result = solve_repair(data, request, time_limit=5)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    after = projected(data, result, request)
    assert all(item["clinician_id"] == "replacement" for item in after["shifts"][1:15])
    assert all(item["clinician_id"] == "replacement" for item in after["cases"][1:15])
    assert after["shifts"][0]["clinician_id"] == after["shifts"][-1]["clinician_id"] == "md"
    assert after["cases"][0]["clinician_id"] == after["cases"][-1]["clinician_id"] == "md"
    assert result["scope"]["shift_ids"] == sorted(f"s{n}" for n in range(1, 15))
    assert "requests" not in result["preview"]
    assert result["metrics"]["full_coverage"]
    assert not result["new_issues"]
    no_errors(after)
    assert data == original
    assert data["requests"][0]["status"] == "pending"


def test_partial_day_releases_only_exact_overlap_locks_and_repairs_orphaned_case():
    request = leave(start="2026-09-14T12:00:00-04:00", end="2026-09-14T14:00:00-04:00")
    data = state([clinician(), clinician("replacement")],
                 [shift(clinician_id="md", locked=True), shift("next", "2026-09-15T07:00:00-04:00", "2026-09-15T19:00:00-04:00", clinician_id="md", locked=True)],
                 [case(clinician_id="md"), case("during", "2026-09-14T12:00:00-04:00", "2026-09-14T15:00:00-04:00", clinician_id="md", locked=True)], [request])
    simulated = simulate_leave(data, request)
    assert simulated["shifts"][0]["clinician_id"] is None
    assert simulated["cases"][0]["clinician_id"] == "md"
    assert simulated["cases"][1]["clinician_id"] is None
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["released_locks"] == [{"resource": "shifts", "id": "s1"}, {"resource": "cases", "id": "during"}]
    after = projected(data, result, request)
    assert after["shifts"][1]["locked"] is True
    assert all(item["clinician_id"] == "replacement" for item in after["cases"])
    no_errors(after)


def test_unaffected_locked_case_that_loses_staffing_requires_explicit_review():
    request = leave(start="2026-09-14T12:00:00-04:00", end="2026-09-14T14:00:00-04:00")
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md")],
                 [case(clinician_id="md", locked=True)], [request])
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] == "INFEASIBLE"
    assert result["changes"] == []
    assert result["preview"]["cases"][0]["locked"]
    assert result["released_locks"] == []


def test_cross_month_leave_includes_crossing_shift_and_keeps_surrounding_work():
    request = leave("2026-10-01T02:00:00-04:00", "2026-10-01T04:00:00-04:00")
    data = state([clinician(), clinician("replacement")], [
        shift("before", "2026-09-27T07:00:00-04:00", "2026-09-27T19:00:00-04:00", clinician_id="md"),
        shift("crossing", "2026-09-30T19:00:00-04:00", "2026-10-01T07:00:00-04:00", clinician_id="md", kind="primary_call"),
        shift("after", "2026-10-05T07:00:00-04:00", "2026-10-05T19:00:00-04:00", clinician_id="md"),
    ], [case("overnight", "2026-09-30T23:00:00-04:00", "2026-10-01T03:00:00-04:00", clinician_id="md")], [request])
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["scope"]["shift_ids"] == ["crossing"]
    assert result["metrics"]["scope_start"] == "2026-09-28T00:00:00-04:00"
    assert result["metrics"]["scope_end"] == "2026-10-05T00:00:00-04:00"
    after = projected(data, result, request)
    assert after["shifts"][1]["clinician_id"] == "replacement"
    assert after["cases"][0]["clinician_id"] == "replacement"
    assert after["shifts"][0] == data["shifts"][0] and after["shifts"][2] == data["shifts"][2]
    no_errors(after)


def test_calendar_scope_includes_shift_starting_before_week_and_linked_case():
    request = leave("2026-09-14T01:00:00-04:00", "2026-09-14T02:00:00-04:00")
    data = state([clinician(), clinician("replacement")], [
        shift("crossing", "2026-09-13T19:00:00-04:00", "2026-09-14T07:00:00-04:00", clinician_id="md", kind="primary_call")],
        [case("before_week", "2026-09-13T20:00:00-04:00", "2026-09-13T22:00:00-04:00", clinician_id="md")], [request])
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["scope"] == {"shift_ids": ["crossing"], "case_ids": ["before_week"]}
    assert projected(data, result, request)["cases"][0]["clinician_id"] == "replacement"


def test_no_eligible_replacement_returns_safe_partial_and_classifies_existing_gap():
    request = leave()
    data = state(shifts=[shift(clinician_id="md")], cases=[case(clinician_id="md"),
                  case("existing", "2026-10-05T08:00:00-04:00", "2026-10-05T11:00:00-04:00")], requests=[request])
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert not result["metrics"]["full_coverage"]
    assert {(item["resource"], item["id"]) for item in result["new_issues"]} == {("shifts", "s1"), ("cases", "case1")}
    assert {(item["resource"], item["id"]) for item in result["existing_issues"]} == {("cases", "existing")}
    assert result["metrics"]["uncovered_slots"] == 2
    assert result["metrics"]["total_uncovered_slots"] == 3
    no_errors(projected(data, result, request))


def test_leave_repair_does_not_fill_unrelated_open_case_in_same_week():
    request = leave("2026-09-14T00:00:00-04:00", "2026-09-15T00:00:00-04:00")
    data = state([clinician(), clinician("replacement"), clinician("other")],
                 [shift(clinician_id="md"), shift("other_site", site_id="other", clinician_id="other")],
                 [case(clinician_id="md"), case("unrelated_open", site_id="other")], [request])
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    after = projected(data, result, request)
    assert after["cases"][0]["clinician_id"] == "replacement"
    assert after["cases"][1] == data["cases"][1]
    assert result["scope"]["case_ids"] == ["case1"]
    assert result["metrics"]["daily_slots"] == 1
    assert result["metrics"]["full_coverage"]
    assert any(item["id"] == "unrelated_open" for item in result["existing_issues"])


def test_replacement_leave_restores_old_interval_and_preserves_current_data():
    old = leave("2026-09-14T00:00:00-04:00", "2026-09-15T00:00:00-04:00", id="old", status="approved")
    new = leave("2026-09-15T00:00:00-04:00", "2026-09-16T00:00:00-04:00", details={"replaces_request_id": "old"})
    data = state(shifts=[shift()], requests=[old, new])
    result = solve_repair(data, new, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    after = projected(data, result, new)
    assert after["requests"][0]["status"] == "cancelled"
    assert after["requests"][1]["status"] == "approved"
    assert after["shifts"][0]["clinician_id"] == "md"
    assert data["requests"][0]["status"] == "approved"


def test_approved_leave_can_be_repaired_again_and_cannot_override_saved_request():
    request = leave(status="approved")
    data = state([clinician(), clinician("replacement")], [shift()], requests=[request])
    assert solve_repair(data, request, time_limit=3)["status"] in {"OPTIMAL", "FEASIBLE"}
    with pytest.raises(ValueError, match="changed"):
        simulate_leave(data, {**request, "clinician_id": "replacement"})


def test_staffing_timeout_does_not_offer_unvalidated_changes(monkeypatch):
    request = leave()
    data = state(shifts=[shift(clinician_id="md")], requests=[request])
    monkeypatch.setattr(repair, "solve_schedule", lambda *args, **kwargs: {"status": "UNKNOWN", "changes": [], "metrics": {}, "explanations": ["Timeout"]})
    result = solve_repair(data, request, time_limit=.1)
    assert result["status"] == "UNKNOWN"
    assert result["changes"] == []
    assert result["gaps"]


def test_daily_timeout_offers_only_independently_safe_staffing_partial(monkeypatch):
    request = leave()
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md")], [case(clinician_id="md")], [request])
    actual = repair.solve_schedule
    def timeout_daily(*args, **kwargs):
        if kwargs.get("stage") == "daily":
            return {"status": "UNKNOWN", "changes": [], "metrics": {}, "explanations": ["Timeout"]}
        return actual(*args, **kwargs)
    monkeypatch.setattr(repair, "solve_schedule", timeout_daily)
    result = solve_repair(data, request, time_limit=3)
    assert result["status"] == "FEASIBLE"
    assert result["metrics"]["timed_out"]
    after = projected(data, result, request)
    assert after["shifts"][0]["clinician_id"] == "replacement"
    assert after["cases"][0]["clinician_id"] is None
    no_errors(after)


def test_scope_does_not_relax_post_call_rest_at_boundary():
    request = leave("2026-09-14T07:00:00-04:00", "2026-09-14T19:00:00-04:00")
    data = state([clinician(), clinician("replacement")], [
        shift("prior", "2026-09-13T07:00:00-04:00", "2026-09-14T00:00:00-04:00", clinician_id="replacement", kind="primary_call"),
        shift(clinician_id="md")], requests=[request])
    result = solve_repair(data, request, time_limit=3)
    after = projected(data, result, request)
    assert after["shifts"][0] == data["shifts"][0]
    assert after["shifts"][1]["clinician_id"] is None
    no_errors(after)


def test_case_recommendations_only_include_current_eligible_staffed_clinicians():
    data = state([clinician(), clinician("unstaffed"), clinician("wrong_site")],
                 [shift(clinician_id="md"), shift("wrong", site_id="other", clinician_id="wrong_site")], [case()])
    original = deepcopy(data)
    result = coverage_recommendations(data, "cases", "case1")
    assert [item["clinician_id"] for item in result["candidates"]] == ["md"]
    assert "Already scheduled" in result["candidates"][0]["reason"]
    assert result["candidates"][0]["changes"] == [{"resource": "cases", "id": "case1", "patch": {"clinician_id": "md"}}]
    assert not result["requires_staffing_repair"]
    assert data == original


def test_case_candidate_rejects_overlapping_case_and_offers_staffing_repair():
    data = state(shifts=[shift(clinician_id="md")], cases=[case(), case("busy", room="OR2", clinician_id="md")])
    result = coverage_recommendations(data, "cases", "case1")
    assert not result["candidates"]
    assert result["requires_staffing_repair"]
    assert not result["missing_coverage"]


def test_case_candidate_rejects_supervision_failure_and_approved_leave():
    person = clinician("crna", "CRNA")
    data = state([person], [shift(role="CRNA", clinician_id="crna")], [case(role="CRNA")])
    assert not coverage_recommendations(data, "cases", "case1")["candidates"]
    person["supervision_required"] = False
    data["requests"] = [leave(clinician_id="crna", status="approved")]
    assert not coverage_recommendations(data, "cases", "case1")["candidates"]


@pytest.mark.parametrize("patch", [{}, {"site_id": "other"}, {"role": "CRNA"}, {"specialty": "General"}, {"kind": "backup_call"}])
def test_missing_case_coverage_explains_requirement_before_repair(patch):
    data = state(shifts=[] if not patch else [shift(**patch)], cases=[case()])
    result = coverage_recommendations(data, "cases", "case1")
    assert result["missing_coverage"]
    assert not result["requires_staffing_repair"]
    assert not result["candidates"]
    assert "Add the required staffing coverage" in result["reason"]


def test_hypothetical_recommendation_excludes_absent_clinician():
    request = leave()
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md")], [case(clinician_id="md")], [request])
    repaired = solve_repair(data, request, time_limit=3)
    hypothetical = projected(data, repaired, request)
    hypothetical["cases"][0]["clinician_id"] = None
    result = coverage_recommendations(hypothetical, "cases", "case1")
    assert [item["clinician_id"] for item in result["candidates"]] == ["replacement"]


def test_locked_case_recommendation_requires_explicit_unlock():
    data = state(shifts=[shift(clinician_id="md")], cases=[case(locked=True)])
    result = coverage_recommendations(data, "cases", "case1")
    assert not result["candidates"]
    assert not result["requires_staffing_repair"]
    assert any(item["code"] == "locked" for item in result["issues"])


def test_target_case_repair_uses_available_slot_without_changing_cases_times():
    data = state(shifts=[shift()], cases=[case()])
    result = solve_repair(data, target={"resource": "cases", "id": "case1"}, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    after = projected(data, result)
    assert after["shifts"][0]["clinician_id"] == "md"
    assert after["cases"][0]["clinician_id"] == "md"
    assert all(set(change["patch"]) <= {"clinician_id", "locked"} for change in result["changes"])
    no_errors(after)


def test_shift_recommendations_rank_lower_workload_and_explain_validity():
    data = state([clinician("busy"), clinician("free")], [shift(),
                  shift("later", "2026-09-16T07:00:00-04:00", "2026-09-16T19:00:00-04:00", clinician_id="busy")])
    result = coverage_recommendations(data, "shifts", "s1")
    assert [item["clinician_id"] for item in result["candidates"]] == ["free", "busy"]
    assert "rest" in result["candidates"][0]["reason"]
    assert result["candidates"][0]["changes"][0]["resource"] == "shifts"


def test_linked_case_lock_can_offer_broader_repair_without_unlocking_it():
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md")], [case(clinician_id="md", locked=True)])
    result = coverage_recommendations(data, "shifts", "s1")
    assert not result["candidates"]
    assert result["requires_staffing_repair"]
    assert any(issue["code"] == "locked" for issue in result["issues"])
    result = solve_repair(data, target={"resource": "shifts", "id": "s1"}, time_limit=3)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["preview"]["cases"][0]["locked"] is True
    assert result["preview"]["cases"][0]["clinician_id"] == "md"
