from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.solver import coverage_candidates, leave_impact, solve_schedule, validate_schedule


def clinician(identifier="md", role="MD", **patch):
    person = dict(id=identifier, name=identifier, role=role, specialties=["OB", "General", "Orthopedics"],
                  sites=["main", "other"], availability=dict(weekdays=list(range(7)), start="07:00", end="19:00"),
                  fte=1, target_hours=144, preferred_shift_hours=[12], preferred_specialties=[],
                  call_eligible=True, supervision_required=role in {"CRNA", "CAA"}, active=True)
    person.update(patch)
    return person


def shift(identifier="s1", start="2026-09-14T07:00:00-04:00", end="2026-09-14T19:00:00-04:00", **patch):
    value = dict(id=identifier, start=start, end=end, site_id="main", specialty="OB", role="MD",
                 kind="day", clinician_id=None, locked=False, status="draft")
    value.update(patch)
    return value


def case(identifier="case1", start="2026-09-14T08:00:00-04:00", end="2026-09-14T11:00:00-04:00", **patch):
    value = dict(id=identifier, title="Example", room="OR1", start=start, end=end, site_id="main",
                 specialty="OB", role="MD", clinician_id=None, locked=False)
    value.update(patch)
    return value


def state(people=None, shifts=None, cases=None, requests=None, **settings):
    policy = dict(timezone="America/New_York", min_rest_hours=10, post_call_rest_hours=12,
                  max_weekly_hours=60, supervision_ratio=3)
    policy.update(settings)
    return dict(settings=policy, sites=[dict(id="main", name="Main"), dict(id="other", name="Other")],
                clinicians=people if people is not None else [clinician()], shifts=shifts or [],
                cases=cases or [], requests=requests or [])


def apply(data, result):
    revised = deepcopy(data)
    for change in result["changes"]:
        next(item for item in revised[change["resource"]] if item["id"] == change["id"]).update(change["patch"])
    return revised


def codes(data):
    return {issue["code"] for issue in validate_schedule(data)}


def no_errors(data):
    assert [issue for issue in validate_schedule(data) if issue["severity"] == "error"] == []


def test_single_slot_filled_without_mutation():
    data = state(shifts=[shift()])
    original = deepcopy(data)
    result = solve_schedule(data, time_limit=2)
    assert result["status"] == "OPTIMAL"
    assert result["metrics"]["full_coverage"] is True
    assert result["changes"][0]["patch"] == {"clinician_id": "md"}
    no_errors(apply(data, result))
    assert data == original


@pytest.mark.parametrize("person_patch,shift_patch,code", [
    ({"role": "CRNA"}, {}, "role"),
    ({"sites": ["other"]}, {}, "site"),
    ({"specialties": ["General"]}, {}, "specialty"),
    ({"active": False}, {}, "inactive"),
    ({"call_eligible": False}, {"kind": "primary_call"}, "call_eligibility"),
    ({"availability": dict(weekdays=[1], start="07:00", end="19:00")}, {}, "availability"),
])
def test_ineligible_assignment_becomes_explicit_gap(person_patch, shift_patch, code):
    data = state([clinician(**person_patch)], [shift(clinician_id="md", **shift_patch)])
    assert code in codes(data)
    result = solve_schedule(data, time_limit=2)
    repaired = apply(data, result)
    assert result["status"] == "OPTIMAL"
    assert repaired["shifts"][0]["clinician_id"] is None
    assert result["gaps"][0]["severity"] == "gap"
    no_errors(repaired)


def test_locked_ineligible_assignment_is_not_relaxed():
    data = state(shifts=[shift(clinician_id="md", locked=True, specialty="Cardiac")])
    result = solve_schedule(data, time_limit=2)
    assert result["status"] == "INFEASIBLE"
    assert result["changes"] == []
    assert "Fixed" in result["explanations"][0]


def test_approved_leave_overrides_call_at_night():
    data = state(shifts=[shift(start="2026-09-14T19:00:00-04:00", end="2026-09-15T07:00:00-04:00",
                              clinician_id="md", kind="primary_call")],
                 requests=[dict(id="r1", kind="leave", clinician_id="md", status="approved",
                                start="2026-09-15T00:00:00-04:00", end="2026-09-15T02:00:00-04:00")])
    assert "leave" in codes(data)
    result = solve_schedule(data, time_limit=2)
    assert apply(data, result)["shifts"][0]["clinician_id"] is None
    data["requests"][0]["status"] = "pending"
    assert "leave" not in codes(data)
    data["requests"][0]["status"] = "cancelled"
    assert "leave" not in codes(data)


def test_two_week_vacation_reduces_exact_recurring_capacity_and_resumes():
    person = clinician(availability=dict(weekdays=[0, 2, 4], start="07:00", end="19:00"))
    data = state([person], [shift(clinician_id="md"),
                           shift("after", "2026-09-28T07:00:00-04:00", "2026-09-28T19:00:00-04:00", clinician_id="md")],
                 [case(clinician_id="md")])
    original = deepcopy(data)
    impact = leave_impact(data, "md", "2026-09-14T00:00:00-04:00", "2026-09-28T00:00:00-04:00")
    assert impact["lost_hours"] == 72
    assert [item["id"] for item in impact["affected_shifts"]] == ["s1"]
    assert len(impact["affected_cases"]) == 1
    assert data == original
    data["requests"].append(dict(id="leave", kind="leave", status="approved", clinician_id="md",
                                  start="2026-09-14T00:00:00-04:00", end="2026-09-28T00:00:00-04:00"))
    assert [issue["id"] for issue in validate_schedule(data) if issue["code"] == "leave"] == ["s1", "case1"]


def test_leave_preview_avoids_double_counting_previous_approved_leave():
    data = state(requests=[dict(id="old", kind="leave", status="approved", clinician_id="md",
                                start="2026-09-14T07:00:00-04:00", end="2026-09-14T10:00:00-04:00")])
    impact = leave_impact(data, "md", "2026-09-14T09:00:00-04:00", "2026-09-14T12:00:00-04:00")
    assert impact["lost_hours"] == 2


def test_intervals_half_open_and_backup_not_overlapping_other_work():
    data = state(shifts=[shift(clinician_id="md"),
                        shift("b", "2026-09-14T19:00:00-04:00", "2026-09-15T07:00:00-04:00",
                              kind="backup_call", clinician_id="md")], min_rest_hours=0)
    no_errors(data)
    data["shifts"][1]["start"] = "2026-09-14T18:59:00-04:00"
    assert "overlap" in codes(data)


def test_post_call_rest_is_checked_across_month_and_scope_boundaries():
    prior = shift("prior", "2026-08-31T07:00:00-04:00", "2026-09-01T07:00:00-04:00",
                  kind="primary_call", clinician_id="md", locked=True)
    next_shift = shift("next", "2026-09-01T07:00:00-04:00", "2026-09-01T19:00:00-04:00", clinician_id="md")
    data = state(shifts=[prior, next_shift])
    assert "post_call_rest" in codes(data)
    result = solve_schedule(data, date="2026-09-01", time_limit=2)
    revised = apply(data, result)
    assert revised["shifts"][0]["clinician_id"] == "md"
    assert revised["shifts"][1]["clinician_id"] is None
    no_errors(revised)


def test_hard_weekly_hours_split_at_local_monday():
    data = state(shifts=[shift("overnight", "2026-09-13T19:00:00-04:00", "2026-09-14T07:00:00-04:00",
                              kind="primary_call", clinician_id="md")], max_weekly_hours=7)
    no_errors(data)  # Five hours Sunday, seven Monday.
    data["shifts"].append(shift("monday", "2026-09-14T19:00:00-04:00", "2026-09-15T07:00:00-04:00",
                                 kind="primary_call", clinician_id="md"))
    assert "weekly_hours" in codes(data)
    result = solve_schedule(data, time_limit=2)
    assert result["metrics"]["assigned_slots"] == 1
    no_errors(apply(data, result))


def test_dst_uses_elapsed_hours_not_wall_clock_hours():
    data = state(shifts=[shift("dst", "2026-11-01T00:00:00-04:00", "2026-11-01T04:00:00-05:00",
                              kind="primary_call", clinician_id="md")], max_weekly_hours=4)
    assert "weekly_hours" in codes(data)  # Fall-back shift is five hours.
    data["settings"]["max_weekly_hours"] = 5
    no_errors(data)


def test_overnight_recurring_availability_belongs_to_start_day():
    person = clinician(availability=dict(weekdays=[0], start="19:00", end="07:00"))
    data = state([person], [shift(start="2026-09-14T19:00:00-04:00", end="2026-09-15T07:00:00-04:00",
                                 clinician_id="md")])
    no_errors(data)
    data["shifts"][0]["end"] = "2026-09-15T08:00:00-04:00"
    assert "availability" in codes(data)


def test_supervision_enforced_for_entire_shift_and_each_site():
    data = state([clinician(), clinician("crna", "CRNA")],
                 [shift(end="2026-09-14T15:00:00-04:00", clinician_id="md"),
                  shift("nurse", role="CRNA", clinician_id="crna")])
    assert "supervision" in codes(data)
    result = solve_schedule(data, time_limit=2)
    revised = apply(data, result)
    assert revised["shifts"][1]["clinician_id"] is None
    no_errors(revised)
    data["shifts"][0]["end"] = "2026-09-14T19:00:00-04:00"
    no_errors(data)
    data["shifts"][0]["site_id"] = "other"
    assert "supervision" in codes(data)


def test_supervision_ratio_and_multispecialty_clinician_not_double_counted():
    people = [clinician()] + [clinician(f"n{i}", "CRNA") for i in range(4)]
    shifts = [shift("doctor")] + [shift(f"s{i}", role="CRNA", specialty="OB" if i % 2 else "Orthopedics") for i in range(4)]
    data = state(people, shifts, supervision_ratio=3)
    result = solve_schedule(data, time_limit=2)
    assert result["metrics"]["assigned_slots"] == 4
    no_errors(apply(data, result))
    # One person with two competencies can fill only one concurrent slot.
    data = state([clinician()], [shift(), shift("other", specialty="Orthopedics")])
    result = solve_schedule(data, time_limit=2)
    assert result["metrics"]["assigned_slots"] == 1


def test_backup_call_cannot_provide_supervision():
    data = state([clinician(), clinician("crna", "CRNA")],
                 [shift(kind="backup_call", clinician_id="md"), shift("nurse", role="CRNA", clinician_id="crna")])
    assert "supervision" in codes(data)


def test_primary_call_can_supervise_primary_call():
    data = state([clinician(), clinician("crna", "CRNA")],
                 [shift(kind="primary_call", clinician_id="md"),
                  shift("nurse", role="CRNA", kind="primary_call", clinician_id="crna")])
    no_errors(data)


def test_daily_assignment_requires_matching_staffing_and_no_case_overlap():
    data = state(shifts=[shift(clinician_id="md")],
                 cases=[case(), case("case2", room="OR2")])
    result = solve_schedule(data, stage="daily", time_limit=2)
    assert result["metrics"]["assigned_slots"] == 1
    no_errors(apply(data, result))
    data["shifts"][0]["specialty"] = "General"
    result = solve_schedule(data, stage="daily", time_limit=2)
    assert result["metrics"]["assigned_slots"] == 0


def test_daily_cases_can_be_sequential_without_shift_rest():
    data = state(shifts=[shift(clinician_id="md")], cases=[case(),
                 case("case2", "2026-09-14T11:00:00-04:00", "2026-09-14T13:00:00-04:00")])
    result = solve_schedule(data, stage="daily", time_limit=2)
    assert result["metrics"]["assigned_slots"] == 2
    no_errors(apply(data, result))


def test_room_overlap_is_fixed_input_error_even_without_assignments():
    data = state(shifts=[shift(clinician_id="md")], cases=[case(), case("case2")])
    assert "room_overlap" in codes(data)
    result = solve_schedule(data, stage="daily", time_limit=2)
    assert result["status"] == "INFEASIBLE"
    assert result["changes"] == []


def test_locked_case_preserves_its_staffing_support():
    data = state([clinician(), clinician("second")], [shift(clinician_id="md")], [case(clinician_id="md", locked=True)])
    result = solve_schedule(data, time_limit=2)
    assert apply(data, result)["shifts"][0]["clinician_id"] == "md"
    no_errors(apply(data, result))


def test_staffing_repair_clears_only_invalid_unlocked_daily_assignments():
    data = state([clinician(active=False), clinician("second")], [shift(clinician_id="md")], [case(clinician_id="md")])
    result = solve_schedule(data, time_limit=2)
    revised = apply(data, result)
    assert revised["shifts"][0]["clinician_id"] == "second"
    assert revised["cases"][0]["clinician_id"] is None
    assert any(change["resource"] == "cases" for change in result["changes"])
    no_errors(revised)


def test_existing_valid_assignment_is_preserved_over_preference_gain():
    data = state([clinician(preferred_shift_hours=[24]), clinician("second", preferred_shift_hours=[12])],
                 [shift(clinician_id="md")])
    result = solve_schedule(data, time_limit=2)
    assert result["changes"] == []


def test_fte_adjusted_workload_balances_even_when_total_capacity_is_high():
    start = datetime(2026, 9, 14, 7, tzinfo=ZoneInfo("America/New_York"))
    shifts = [shift(f"day{i}", (start + timedelta(days=i)).isoformat(),
                    (start + timedelta(days=i, hours=12)).isoformat()) for i in range(3)]
    data = state([clinician(), clinician("parttime", fte=.5)], shifts)
    result = solve_schedule(data, time_limit=2)
    assigned = [item["clinician_id"] for item in apply(data, result)["shifts"]]
    assert assigned.count("md") == 2
    assert assigned.count("parttime") == 1


def test_leave_impact_excludes_unrelated_existing_gaps():
    data = state(shifts=[shift("unrelated"),
                        shift("assigned", "2026-09-15T07:00:00-04:00", "2026-09-15T19:00:00-04:00", clinician_id="md")])
    impact = leave_impact(data, "md", "2026-09-15T09:00:00-04:00", "2026-09-15T12:00:00-04:00")
    assert [issue["id"] for issue in impact["issues"]] == ["assigned"]


def test_leave_preview_flags_lost_capacity_before_staffing_is_generated():
    data = state(shifts=[shift()])
    original = deepcopy(data)
    impact = leave_impact(data, "md", "2026-09-14T07:00:00-04:00", "2026-09-14T19:00:00-04:00")
    assert impact["affected_shifts"] == []
    assert impact["lost_hours"] == 12
    assert [issue["code"] for issue in impact["issues"]] == ["leave_capacity_risk"]
    assert impact["issues"][0]["certainty"] == "risk"
    assert impact["projected_capacity"][0]["coverable_before"] == 1
    assert impact["projected_capacity"][0]["coverable_after"] == 0
    assert data == original


def test_leave_capacity_matching_does_not_double_count_specialties_or_sites():
    data = state(shifts=[shift("ob"), shift("ortho", specialty="Orthopedics", site_id="other")])
    impact = leave_impact(data, "md", "2026-09-14T07:00:00-04:00", "2026-09-14T19:00:00-04:00")
    capacity = impact["projected_capacity"][0]
    assert capacity["open_slots"] == 2
    assert capacity["coverable_before"] == 1
    assert capacity["coverable_after"] == 0
    assert capacity["capacity_loss"] == 1
    assert impact["lost_hours"] == 12


def test_leave_capacity_risk_is_absent_when_spare_qualified_coverage_remains():
    data = state([clinician(), clinician("spare")], [shift()])
    impact = leave_impact(data, "md", "2026-09-14T07:00:00-04:00", "2026-09-14T19:00:00-04:00")
    assert impact["issues"] == []
    assert impact["projected_capacity"] == []


def test_leave_capacity_risk_respects_existing_work_rest_and_weekly_limits():
    for existing, settings in [
        (shift("working", clinician_id="md", site_id="other"), {}),
        (shift("call", "2026-09-13T19:00:00-04:00", "2026-09-14T07:00:00-04:00", kind="primary_call", clinician_id="md"), {}),
        (shift("week", "2026-09-15T07:00:00-04:00", "2026-09-15T19:00:00-04:00", clinician_id="md"), {"max_weekly_hours": 12}),
    ]:
        data = state(shifts=[shift(), existing], **settings)
        impact = leave_impact(data, "md", "2026-09-14T07:00:00-04:00", "2026-09-14T19:00:00-04:00")
        assert all(issue["code"] != "leave_capacity_risk" for issue in impact["issues"])


def test_leave_capacity_matching_finds_joint_specialty_assignment():
    people = [clinician("flex"), clinician("ob_only", specialties=["OB"])]
    data = state(people, [shift("ob"), shift("ortho", specialty="Orthopedics")])
    impact = leave_impact(data, "flex", "2026-09-14T09:00:00-04:00", "2026-09-14T12:00:00-04:00")
    assert impact["projected_capacity"][0]["coverable_before"] == 2
    assert impact["projected_capacity"][0]["coverable_after"] == 1
    assert impact["projected_capacity"][0]["capacity_loss"] == 1
    assert impact["lost_hours"] == 3


def test_leave_capacity_does_not_report_already_unavailable_or_locked_slots():
    request = dict(id="old", kind="leave", status="approved", clinician_id="md",
                   start="2026-09-14T07:00:00-04:00", end="2026-09-14T19:00:00-04:00")
    data = state(shifts=[shift()], requests=[request])
    impact = leave_impact(data, "md", request["start"], request["end"])
    assert impact["lost_hours"] == 0
    assert impact["issues"] == []
    data["requests"] = []
    data["shifts"][0]["locked"] = True
    assert leave_impact(data, "md", request["start"], request["end"])["issues"] == []


def test_capacity_risk_blocks_leave_under_no_shortage_policy():
    from fastapi import HTTPException
    from backend.services import apply_request
    request = dict(id="r", kind="leave", status="pending", clinician_id="md", details={},
                   start="2026-09-14T07:00:00-04:00", end="2026-09-14T19:00:00-04:00")
    data = state(shifts=[shift()], requests=[request], allow_leave_shortage=False,
                 require_scheduler_approval=True, require_swap_acceptance=True)
    with pytest.raises(HTTPException) as exc:
        apply_request(data, request, {"id": "scheduler", "role": "scheduler"}, "approve")
    assert exc.value.status_code == 422


def test_leave_capacity_flags_weekly_limit_even_when_each_shift_has_candidates():
    people = [clinician(), clinician("replacement")]
    start = datetime(2026, 9, 14, 7, tzinfo=ZoneInfo("America/New_York"))
    shifts = [shift(f"call{i}", (start + timedelta(days=i)).isoformat(),
                    (start + timedelta(days=i + 1)).isoformat(), kind="primary_call") for i in range(4)]
    data = state(people, shifts)
    impact = leave_impact(data, "md", "2026-09-14T00:00:00-04:00", "2026-09-19T00:00:00-04:00")
    assert impact["issues"]
    assert all(issue["code"] == "leave_capacity_risk" for issue in impact["issues"])
    assert all(item["scope"] != "interval" for item in impact["projected_capacity"])
    weekly = next(item for item in impact["projected_capacity"] if item["scope"] == "week")
    assert weekly["coverable_hours_before"] == 96
    assert weekly["coverable_hours_after"] == 60
    assert weekly["capacity_loss_hours"] == 36


def test_demo_two_week_ob_vacation_shows_capacity_risk_before_generation():
    from backend.seed import demo_data
    data = demo_data()
    request = data["requests"][0]
    impact = leave_impact(data, request["clinician_id"], request["start"], request["end"])
    assert impact["affected_shifts"] == []
    assert any(issue["code"] == "leave_capacity_risk" for issue in impact["issues"])
    assert any(item["scope"] == "week" for item in impact["projected_capacity"])


def test_coverage_priority_over_existing_assignment_preservation():
    # Flexible MD is on General but is the only OB-qualified clinician.
    data = state([clinician(), clinician("general", specialties=["General"])],
                 [shift("general", specialty="General", clinician_id="md"), shift("ob")])
    result = solve_schedule(data, time_limit=2)
    revised = apply(data, result)
    assert result["metrics"]["assigned_slots"] == 2
    assert revised["shifts"][0]["clinician_id"] == "general"
    assert revised["shifts"][1]["clinician_id"] == "md"


def test_out_of_scope_assignment_is_unchanged():
    data = state([clinician(), clinician("second")],
                 [shift("prior", "2026-09-13T07:00:00-04:00", "2026-09-13T19:00:00-04:00", clinician_id="md"), shift()])
    result = solve_schedule(data, date="2026-09-14", time_limit=2)
    assert all(change["id"] != "prior" for change in result["changes"])
    assert apply(data, result)["shifts"][0]["clinician_id"] == "md"


def test_selected_month_keeps_other_months_fixed_and_checks_boundary_rest():
    previous = shift("previous", "2026-08-31T07:00:00-04:00", "2026-09-01T07:00:00-04:00",
                     kind="primary_call", clinician_id="md")
    selected = shift("selected", "2026-09-01T07:00:00-04:00", "2026-09-01T19:00:00-04:00", clinician_id="md")
    following = shift("following", "2026-10-01T07:00:00-04:00", "2026-10-01T19:00:00-04:00", clinician_id="md")
    data = state(shifts=[previous, selected, following])
    result = solve_schedule(data, "staffing", None, 2, "2026-09")
    revised = apply(data, result)
    assert result["metrics"]["total_slots"] == 1
    assert revised["shifts"][0]["clinician_id"] == "md"
    assert revised["shifts"][1]["clinician_id"] is None
    assert revised["shifts"][2]["clinician_id"] == "md"
    assert {change["id"] for change in result["changes"]} == {"selected"}
    no_errors(revised)


def test_selected_month_uses_local_start_date_not_utc_start_date():
    data = state(shifts=[shift("september", "2026-10-01T03:00:00+00:00", "2026-10-01T07:00:00+00:00", kind="backup_call"),
                        shift("october", "2026-10-01T07:00:00-04:00", "2026-10-01T19:00:00-04:00")])
    result = solve_schedule(data, month="2026-09", time_limit=2)
    assert result["metrics"]["total_slots"] == 1
    assert {change["id"] for change in result["changes"]} == {"september"}
    assert apply(data, result)["shifts"][1]["clinician_id"] is None


def test_missing_timezone_offset_is_rejected_without_crash():
    data = state(shifts=[shift(start="2026-09-14T07:00:00")])
    assert "invalid_interval" in codes(data)
    assert solve_schedule(data, time_limit=2)["status"] == "MODEL_INVALID"


def test_timeout_never_claims_infeasibility_or_proposes_unchecked_assignments():
    from backend.seed import demo_data
    data = demo_data()
    result = solve_schedule(data, time_limit=.000001)
    assert result["status"] == "UNKNOWN"
    assert result["changes"] == []
    assert result["metrics"]["solver_status"] == "UNKNOWN"


def test_coverage_candidates_transfer_cases_without_mutating_data():
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md")], [case(clinician_id="md")])
    original = deepcopy(data)
    result = coverage_candidates(data, "s1")
    assert [person["clinician_id"] for person in result["candidates"]] == ["replacement"]
    assert result["candidates"][0]["changes"] == [
        {"resource": "shifts", "id": "s1", "patch": {"clinician_id": "replacement"}},
        {"resource": "cases", "id": "case1", "patch": {"clinician_id": "replacement"}},
    ]
    no_errors(apply(data, result["candidates"][0]))
    assert data == original


def test_coverage_candidates_respect_shift_and_linked_case_locks():
    data = state([clinician(), clinician("replacement")], [shift(clinician_id="md", locked=True)], [case(clinician_id="md")])
    assert coverage_candidates(data, "s1")["candidates"] == []
    data["shifts"][0]["locked"] = False
    data["cases"][0]["locked"] = True
    result = coverage_candidates(data, "s1")
    assert result["candidates"] == []
    assert result["issues"][0]["resource"] == "cases"
    assert result["issues"][0]["code"] == "locked"


def test_coverage_candidates_do_not_equate_empty_roster_with_eligibility():
    people = [clinician("eligible"), clinician("unqualified", specialties=["General"]),
              clinician("off", availability=dict(weekdays=[2], start="07:00", end="19:00")),
              clinician("wrongsite", sites=["other"]), clinician("leave")]
    data = state(people, [shift()], requests=[dict(id="r", kind="leave", status="approved", clinician_id="leave",
                                                 start="2026-09-14T09:00:00-04:00", end="2026-09-14T12:00:00-04:00")])
    result = coverage_candidates(data, "s1")
    assert [person["clinician_id"] for person in result["candidates"]] == ["eligible"]
    assert {entry["clinician_id"] for entry in result["rejections"]} == {"unqualified", "off", "wrongsite", "leave"}


def test_coverage_candidates_enforce_rest_weekly_limits_and_site_overlap():
    data = state([clinician("rest"), clinician("hours"), clinician("overlap"), clinician("eligible")],
                 [shift(), shift("prior", "2026-09-13T19:00:00-04:00", "2026-09-14T07:00:00-04:00", kind="primary_call", clinician_id="rest"),
                  shift("later", "2026-09-15T07:00:00-04:00", "2026-09-15T19:00:00-04:00", clinician_id="hours"),
                  shift("elsewhere", site_id="other", clinician_id="overlap")], max_weekly_hours=12)
    result = coverage_candidates(data, "s1")
    assert [person["clinician_id"] for person in result["candidates"]] == ["eligible"]
    by_person = {item["clinician_id"]: {issue["code"] for issue in item["issues"]} for item in result["rejections"]}
    assert "post_call_rest" in by_person["rest"]
    assert "weekly_hours" in by_person["hours"]
    assert "overlap" in by_person["overlap"]


def test_coverage_candidates_reject_existing_relevant_supervision_error():
    data = state([clinician("first", "CRNA"), clinician("replacement", "CRNA")],
                 [shift(role="CRNA", clinician_id="first")])
    result = coverage_candidates(data, "s1")
    assert result["candidates"] == []
    assert result["rejections"][0]["issues"][0]["code"] == "supervision"
    assert result["existing_issues"][0]["code"] == "supervision"


def test_coverage_candidates_can_repair_supervision_gap_despite_unrelated_errors():
    people = [clinician(), clinician("nurse", "CRNA"), clinician("unqualified", specialties=["General"])]
    data = state(people, [shift(), shift("nurse_shift", role="CRNA", clinician_id="nurse"),
                         shift("unrelated", "2026-09-21T07:00:00-04:00", "2026-09-21T19:00:00-04:00", clinician_id="unqualified"),
                         shift("unrelated_gap", "2026-09-22T07:00:00-04:00", "2026-09-22T19:00:00-04:00")])
    result = coverage_candidates(data, "s1")
    assert [person["clinician_id"] for person in result["candidates"]] == ["md"]
    assert any(issue["code"] == "specialty" for issue in result["existing_issues"])


def test_coverage_candidates_recheck_linked_case_qualification():
    data = state([clinician(), clinician("replacement", specialties=["OB"])],
                 [shift(clinician_id="md")], [case(clinician_id="md"),
                  case("extra", "2026-09-14T10:00:00-04:00", "2026-09-14T12:00:00-04:00", room="OR2", clinician_id="replacement")])
    result = coverage_candidates(data, "s1")
    assert result["candidates"] == []
    assert any(issue["code"] == "case_overlap" for issue in result["rejections"][0]["issues"])


def test_coverage_candidates_unknown_shift_has_clear_reason():
    result = coverage_candidates(state(), "missing")
    assert result["candidates"] == []
    assert result["issues"][0]["code"] == "unknown_shift"


def test_locked_conflicting_shifts_prove_infeasibility():
    data = state(shifts=[shift(clinician_id="md", locked=True), shift("other", clinician_id="md", locked=True)])
    result = solve_schedule(data, time_limit=2)
    assert result["status"] == "INFEASIBLE"
    assert result["changes"] == []


def test_fifty_clinicians_month_and_daily_board():
    from backend.seed import demo_data
    data = demo_data()
    templates = deepcopy(data["clinicians"])
    for index in range(20, 50):
        person = deepcopy(templates[index % 20])
        person.update(id=f"extra{index}", name=f"Extra {index}")
        data["clinicians"].append(person)
    result = solve_schedule(data, time_limit=8)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}, result
    assert result["metrics"]["full_coverage"], result["metrics"]
    staffed = apply(data, result)
    no_errors(staffed)
    daily = solve_schedule(staffed, stage="daily", time_limit=4)
    assert daily["status"] in {"OPTIMAL", "FEASIBLE"}, daily
    assert daily["metrics"]["full_coverage"], daily["metrics"]
    no_errors(apply(staffed, daily))


def test_explicit_staffing_scope_keeps_unselected_slots_fixed():
    data = state([clinician(), clinician("other")], [shift("selected"),
                  shift("outside", "2026-09-15T07:00:00-04:00", "2026-09-15T19:00:00-04:00")])
    result = solve_schedule(data, scope_ids={"selected"}, time_limit=2)
    assert result["status"] == "OPTIMAL"
    assert {item["id"] for item in result["changes"]} == {"selected"}
    assert apply(data, result)["shifts"][1] == data["shifts"][1]
    assert result["metrics"]["total_slots"] == 1


def test_explicit_daily_scope_keeps_other_open_cases_fixed():
    data = state(shifts=[shift(clinician_id="md")], cases=[case(),
                  case("later", "2026-09-14T12:00:00-04:00", "2026-09-14T15:00:00-04:00")])
    result = solve_schedule(data, stage="daily", scope_ids={"case1"}, time_limit=2)
    assert result["status"] == "OPTIMAL"
    assert {item["id"] for item in result["changes"]} == {"case1"}
    assert apply(data, result)["cases"][1] == data["cases"][1]


def test_explicit_scope_rejects_unknown_ids_and_calendar_combination():
    data = state(shifts=[shift()])
    with pytest.raises(ValueError, match="unknown"):
        solve_schedule(data, scope_ids={"missing"}, time_limit=2)
    with pytest.raises(ValueError, match="calendar"):
        solve_schedule(data, scope_ids={"s1"}, month="2026-09", time_limit=2)
