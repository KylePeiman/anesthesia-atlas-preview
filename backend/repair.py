"""Pure, request-linked repair previews and actionable coverage searches.

Nothing in this module approves requests or writes application state. A caller
must bind a successful preview to the source revision and revalidate it in the
same transaction as any later approval. Only shifts and cases leave this module
in a preview; leave details remain in the caller's authorized snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta
import math
import time as clock

from .solver import (
    UTC, _approved_leave, _eligibility, _interval, _issue, _matching_shift,
    _overlap, _structural_issues, _timezone, _week_parts, coverage_candidates,
    solve_schedule, validate_schedule,
)


def _fingerprint(issue):
    return tuple(issue.get(key) for key in ("code", "resource", "id", "clinician_id", "message"))


def _find(data, resource, identifier):
    return next((item for item in data.get(resource, []) if item.get("id") == identifier), None)


def _apply(data, changes):
    revised = deepcopy(data)
    for change in changes:
        item = _find(revised, change["resource"], change["id"])
        if item is None:
            raise ValueError("A proposed assignment no longer exists.")
        item.update(change["patch"])
    return revised


def simulate_leave(data: dict, request: dict) -> dict:
    """Apply only the hypothetical absence and its exact-interval invalidation.

    Locked cases outside a partial-day absence remain locked even if their
    staffing shift overlaps leave. The optimizer must satisfy their staffing
    requirement or explain why a scheduler must review the lock.
    """
    if request.get("kind") != "leave" or request.get("status") not in {"pending", "approved"}:
        raise ValueError("A repair preview requires pending or approved leave.")
    interval = _interval(request)
    if _find(data, "clinicians", request.get("clinician_id")) is None:
        raise ValueError("The leave request refers to an unknown clinician.")
    revised = deepcopy(data)
    saved = _find(revised, "requests", request.get("id"))
    if saved is None:
        raise ValueError("Save the leave request before previewing its optimized schedule.")
    # The passed request must be the snapshot's request, not a date or owner
    # override supplied independently of the persisted request.
    if any(saved.get(key) != request.get(key) for key in ("kind", "status", "clinician_id", "start", "end", "details")):
        raise ValueError("The leave request changed; regenerate its preview.")
    replaced_id = request.get("details", {}).get("replaces_request_id")
    if replaced_id:
        replaced = _find(revised, "requests", replaced_id)
        if (replaced is None or replaced.get("kind") != "leave"
                or replaced.get("clinician_id") != request["clinician_id"]
                or replaced.get("status") not in ({"approved"} if saved["status"] == "pending" else {"approved", "cancelled"})):
            raise ValueError("The leave being replaced changed; review the request before previewing.")
        replaced["status"] = "cancelled"
    saved["status"] = "approved"
    for resource in ("shifts", "cases"):
        for item in revised.get(resource, []):
            if item.get("clinician_id") == request["clinician_id"] and _overlap(_interval(item), interval):
                item.update(clinician_id=None, locked=False)
    return revised


def _calendar_scope(data, interval):
    tz = _timezone(data)
    first = interval[0].astimezone(tz).date()
    last = (interval[1] - timedelta(microseconds=1)).astimezone(tz).date()
    start_day = first - timedelta(days=first.weekday())
    end_day = last - timedelta(days=last.weekday()) + timedelta(days=7)
    return (datetime.combine(start_day, time.min, tzinfo=tz).astimezone(UTC),
            datetime.combine(end_day, time.min, tzinfo=tz).astimezone(UTC))


def _coverage_slot(case, shift):
    """Whether this slot could support the case if assigned to an eligible person."""
    return _matching_shift(case, dict(shift, clinician_id="candidate"), "candidate",
                           {shift["id"]: _interval(shift)}, _interval(case))


def _diff(original, projected):
    changes = []
    for resource in ("shifts", "cases"):
        for item in projected.get(resource, []):
            old = _find(original, resource, item["id"])
            patch = {key: item.get(key) for key in ("clinician_id", "locked") if old.get(key) != item.get(key)}
            if patch:
                changes.append({"resource": resource, "id": item["id"], "patch": patch})
    return changes


def solve_repair(data: dict, request: dict | None = None, target: dict | None = None,
                 time_limit: float = 60) -> dict:
    """Optimize a bounded, reviewable staffing-and-case repair under one deadline."""
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("Time limit must be positive and finite.")
    if (request is None) == (target is None):
        raise ValueError("Choose one leave request or one coverage record to repair.")
    started = clock.monotonic()
    result = {"status": "MODEL_INVALID", "changes": [], "preview": {"shifts": [], "cases": []},
              "scope": {"shift_ids": [], "case_ids": []},
              "gaps": [], "new_issues": [], "existing_issues": [], "released_locks": [],
              "metrics": {"stage": "repair", "time_limit_seconds": time_limit, "stages": []}, "explanations": []}
    structural = _structural_issues(data)
    if structural:
        result.update(existing_issues=structural, explanations=[item["message"] for item in structural])
        return result
    baseline_issues = validate_schedule(data)
    baseline_keys = {_fingerprint(item) for item in baseline_issues}
    projected = simulate_leave(data, request) if request else deepcopy(data)
    if request:
        interval = _interval(request)
    else:
        if target.get("resource") not in {"shifts", "cases"}:
            raise ValueError("Coverage repair requires a shift or case.")
        record = _find(data, target["resource"], target.get("id"))
        if record is None:
            raise ValueError("The requested coverage record does not exist.")
        interval = _interval(record)
    window = _calendar_scope(data, interval)
    shift_scope = {item["id"] for item in projected.get("shifts", []) if _overlap(_interval(item), window)}
    scoped_shifts = [item for item in projected.get("shifts", []) if item["id"] in shift_scope]
    changed_by_leave = [item for item in data.get("shifts", [])
                        if item.get("clinician_id") != _find(projected, "shifts", item["id"]).get("clinician_id")]
    initial_linked = changed_by_leave if request else ([record] if target["resource"] == "shifts" else [])
    case_scope = {item["id"] for item in data.get("cases", [])
                  if (request and item.get("clinician_id") == request["clinician_id"] and _overlap(_interval(item), interval))
                  or (target and target["resource"] == "cases" and target["id"] == item["id"])
                  or any(_coverage_slot(item, shift) for shift in initial_linked)}
    result["scope"] = {"shift_ids": sorted(shift_scope), "case_ids": sorted(case_scope)}
    for resource in ("shifts", "cases"):
        for item in projected.get(resource, []):
            old = _find(data, resource, item["id"])
            if old.get("locked") and not item.get("locked"):
                result["released_locks"].append({"resource": resource, "id": item["id"]})
    tz = _timezone(data)
    result["metrics"].update(scope_start=window[0].astimezone(tz).isoformat(),
                             scope_end=window[1].astimezone(tz).isoformat(),
                             staffing_slots=len(shift_scope), daily_slots=len(case_scope))

    def finish(status, snapshot, explanations, changes=False):
        issues = validate_schedule(snapshot)
        result.update(status=status,
                      preview={kind: deepcopy(snapshot.get(kind, [])) for kind in ("shifts", "cases")},
                      gaps=[item for item in issues if item["severity"] == "gap"],
                      new_issues=[item for item in issues if _fingerprint(item) not in baseline_keys],
                      existing_issues=[item for item in issues if _fingerprint(item) in baseline_keys],
                      explanations=explanations)
        result["changes"] = _diff(data, snapshot) if changes else []
        relevant = {"shifts": shift_scope, "cases": case_scope}
        scoped_gaps = [item for item in result["gaps"] if item["id"] in relevant.get(item["resource"], set())]
        result["metrics"].update(wall_time_seconds=round(clock.monotonic() - started, 3),
                                 changed_assignments=len(result["changes"]),
                                 uncovered_slots=len(scoped_gaps), full_coverage=not scoped_gaps,
                                 total_uncovered_slots=len(result["gaps"]),
                                 new_uncovered_slots=sum(item["severity"] == "gap" for item in result["new_issues"]))
        return result

    remaining = time_limit - (clock.monotonic() - started)
    if remaining <= 0:
        return finish("UNKNOWN", projected, ["The time budget ended before optimization. No replacement schedule was proposed."])
    # A staffing replacement may affect cases outside the initial case set.
    # Reserve daily-solving time whenever any scoped slot can support cases.
    potential_daily_work = any(_coverage_slot(item, shift) for item in data.get("cases", []) for shift in scoped_shifts)
    staffing_budget = remaining * .65 if potential_daily_work or case_scope else remaining
    staffing = solve_schedule(projected, time_limit=staffing_budget, scope_ids=shift_scope,
                              _defer_case_validation=True)
    result["metrics"]["stages"].append({"stage": "staffing", "status": staffing["status"], **staffing.get("metrics", {})})
    if staffing["status"] not in {"OPTIMAL", "FEASIBLE"}:
        return finish(staffing["status"], projected, staffing["explanations"])
    projected = _apply(projected, staffing["changes"])
    changed_shift_ids = {item["id"] for item in staffing["changes"] if item["resource"] == "shifts"}
    changed_shifts = [item for snapshot in (data, projected) for item in snapshot.get("shifts", [])
                      if item["id"] in changed_shift_ids]
    case_scope.update(item["id"] for item in projected.get("cases", [])
                      if any(_coverage_slot(item, shift) for shift in changed_shifts))
    case_scope.update(item["id"] for item in staffing["changes"] if item["resource"] == "cases")
    result["scope"]["case_ids"] = sorted(case_scope)
    result["metrics"]["daily_slots"] = len(case_scope)
    # Partial-day leave may remove a whole supporting shift while a case starts
    # outside the leave itself. Such unlocked cases need reassignment in stage
    # two; retain all unaffected locked cases and their staffing constraints.
    intervals = {item["id"]: _interval(item) for item in projected.get("shifts", [])}
    for item in projected.get("cases", []):
        if item["id"] in case_scope and item.get("clinician_id") and not item.get("locked"):
            if not any(_matching_shift(item, shift, item["clinician_id"], intervals, _interval(item))
                       for shift in projected.get("shifts", [])):
                item["clinician_id"] = None
    daily_status = "OPTIMAL"
    if case_scope:
        remaining = time_limit - (clock.monotonic() - started)
        if remaining <= 0:
            daily_status = "UNKNOWN"
            result["metrics"]["stages"].append({"stage": "daily", "status": "UNKNOWN"})
        else:
            daily = solve_schedule(projected, stage="daily", time_limit=remaining, scope_ids=case_scope)
            daily_status = daily["status"]
            result["metrics"]["stages"].append({"stage": "daily", "status": daily_status, **daily.get("metrics", {})})
            if daily_status in {"OPTIMAL", "FEASIBLE"}:
                projected = _apply(projected, daily["changes"])
            elif daily_status != "UNKNOWN":
                return finish(daily_status, projected, daily["explanations"])
    errors = [item for item in validate_schedule(projected) if item["severity"] == "error"]
    if errors:
        return finish("INFEASIBLE", projected,
                      ["The proposed staffing and daily assignments do not satisfy every hard scheduling rule."]
                      + [item["message"] for item in errors])
    status = "OPTIMAL" if staffing["status"] == daily_status == "OPTIMAL" else "FEASIBLE"
    result["metrics"]["timed_out"] = daily_status == "UNKNOWN"
    explanations = ["Staffing was repaired within the local calendar weeks intersecting this request; surrounding work remains fixed.",
                    "Daily case assignments were repaired against the resulting staffed shifts. Case times and rooms are unchanged.",
                    "Existing assignments and unaffected locks are preserved wherever the hard rules and coverage requirements permit."]
    if result["released_locks"]:
        explanations.append(f"{len(result['released_locks'])} locked assignment(s) overlap this leave and must be released in this proposal.")
    if daily_status == "UNKNOWN":
        explanations.append("Daily optimization reached the shared deadline. The validated staffing repair is available as a partial proposal; remaining daily gaps are shown explicitly.")
    final = finish(status, projected, explanations, changes=True)
    if not final["metrics"]["full_coverage"]:
        final["explanations"].append("Coverage remains incomplete. Review every remaining gap before approving or publishing.")
    return final


def _workload(data, person, interval, resource="shifts"):
    weeks = set(_week_parts(interval, _timezone(data)))
    minutes = sum(sum(value for week, value in _week_parts(_interval(item), _timezone(data)).items() if week in weeks)
                  for item in data.get(resource, []) if item.get("clinician_id") == person["id"])
    target = max(1, float(person.get("fte", 1)) * float(person.get("target_hours", 144)))
    return minutes / 60, minutes / target


def coverage_recommendations(data: dict, resource: str, identifier: str) -> dict:
    """Rank safe replacements; cases first use existing, valid staffed shifts."""
    result = {"resource": resource, "id": identifier, "candidates": [], "reason": "",
              "requires_staffing_repair": False, "missing_coverage": False,
              "issues": [], "existing_issues": []}
    if resource not in {"shifts", "cases"}:
        raise ValueError("Coverage recommendations require a shift or case.")
    structural = _structural_issues(data)
    if structural:
        result.update(issues=structural, reason="Correct the invalid scheduling data before searching for coverage.")
        return result
    target = _find(data, resource, identifier)
    if target is None:
        result.update(issues=[_issue("unknown_record", "The requested coverage record does not exist.", resource, identifier)],
                      reason="The requested coverage record does not exist.")
        return result
    interval = _interval(target)
    people = {person["id"]: person for person in data.get("clinicians", [])}
    if resource == "shifts":
        checked = coverage_candidates(data, identifier)
        result.update({key: checked[key] for key in ("issues", "existing_issues", "reason")})
        candidates = checked["candidates"]
        for candidate in candidates:
            person = people[candidate["clinician_id"]]
            hours, _ = _workload(data, person, interval)
            candidate["reason"] = (f"Eligible for this role, specialty and facility; passes leave, rest, workload, supervision and linked-case checks. "
                                   f"Currently assigned {hours:g} hours in the affected calendar week(s).")
        candidates.sort(key=lambda item: (len(item["changes"]), _workload(data, people[item["clinician_id"]], interval)[1], item["name"].casefold(), item["clinician_id"]))
        result["candidates"] = candidates
        result["requires_staffing_repair"] = not candidates and not target.get("locked")
        return result
    baseline = validate_schedule(data)
    baseline_errors = [item for item in baseline if item["severity"] == "error"]
    baseline_keys = {_fingerprint(item) for item in baseline_errors}
    result["existing_issues"] = baseline_errors
    result["issues"] = [item for item in baseline if item["resource"] == resource and item["id"] == identifier]
    if target.get("locked"):
        result["issues"].append(_issue("locked", "A scheduler must unlock this case before its assignment can change.", resource, identifier))
        result["reason"] = "This case is locked. A scheduler must review and unlock it before assigning a replacement."
        return result
    slots = [item for item in data.get("shifts", []) if _coverage_slot(target, item)]
    result["missing_coverage"] = not slots
    if not slots:
        result["reason"] = "No required day or primary-call coverage slot matches this case's time, facility, role and specialty. Add the required staffing coverage before requesting a repair."
        return result
    tz, leave = _timezone(data), _approved_leave(data)
    intervals = {item["id"]: _interval(item) for item in data.get("shifts", [])}
    weeks = set(_week_parts(interval, tz))
    for person in people.values():
        pid = person["id"]
        if pid == target.get("clinician_id") or _eligibility(person, target, interval, tz, leave, True):
            continue
        staffed = [slot for slot in slots if _matching_shift(target, slot, pid, intervals, interval)]
        if not staffed:
            continue
        change = {"resource": "cases", "id": identifier, "patch": {"clinician_id": pid}}
        revised = _apply(data, [change])
        relevant_shifts = {item["id"] for item in data.get("shifts", [])
                           if item["site_id"] == target["site_id"] and _overlap(_interval(item), interval)}
        relevant_cases = {item["id"] for item in revised.get("cases", [])
                          if item.get("clinician_id") == pid and _overlap(_interval(item), interval)}
        invalid = False
        for issue in validate_schedule(revised):
            if issue["severity"] != "error":
                continue
            relevant = (issue["resource"] == "cases" and issue["id"] in relevant_cases
                        or issue["resource"] == "shifts" and issue["id"] in relevant_shifts
                        or issue["code"] == "weekly_hours" and issue.get("clinician_id") == pid
                        and any(week in issue["message"] for week in weeks))
            if relevant or _fingerprint(issue) not in baseline_keys:
                invalid = True
                break
        if invalid:
            continue
        hours, _ = _workload(data, person, interval, "cases")
        result["candidates"].append({"clinician_id": pid, "name": person["name"], "role": person["role"],
                                     "reason": ("Already scheduled in matching coverage for the entire case; eligible, available and free of conflicting case assignments. "
                                                f"Currently assigned {hours:g} case hours in the affected calendar week(s)."),
                                     "changes": [change]})
    result["candidates"].sort(key=lambda item: (_workload(data, people[item["clinician_id"]], interval, "cases")[1],
                                              _workload(data, people[item["clinician_id"]], interval)[1],
                                              item["name"].casefold(), item["clinician_id"]))
    count = len(result["candidates"])
    result["requires_staffing_repair"] = count == 0
    result["reason"] = (f"{count} eligible clinician{'s are' if count != 1 else ' is'} already scheduled and can cover this case without changing staffing. Review the proposed case assignment before applying it."
                        if count else "No clinician on the current staffed shifts can safely cover this case. Preview a staffing repair to consider broader changes.")
    return result
