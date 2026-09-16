"""Pure scheduling rules and bounded CP-SAT optimization.

Times are compared in UTC as half-open intervals. Availability and calendar
weeks are interpreted in the group's timezone. No database or AI is involved.
The policy values are configurable demonstration rules, not clinical guidance.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date as Date, datetime, time, timedelta, timezone
import math
import time as clock
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
ROLES = {"MD", "CRNA", "CAA", "PA"}
CALL_KINDS = {"primary_call", "backup_call"}


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamps must include their UTC offset.")
    return result.astimezone(UTC)


def _interval(record: dict) -> tuple[datetime, datetime]:
    start, end = _instant(record["start"]), _instant(record["end"])
    if end <= start:
        raise ValueError("End must be later than start.")
    return start, end


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _minutes(interval: tuple) -> int:
    # Round up for hard limits, so seconds cannot evade an hours limit.
    return math.ceil((interval[1] - interval[0]).total_seconds() / 60)


def _timezone(data: dict) -> ZoneInfo:
    return ZoneInfo(data.get("settings", {}).get("timezone", "America/New_York"))


def _number(settings: dict, key: str, default: float) -> float:
    value = float(settings.get(key, default))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must be a finite non-negative number.")
    return value


def _policy(data: dict) -> dict:
    settings = data.get("settings", {})
    return {
        "rest": _number(settings, "min_rest_hours", 10),
        "post_call": _number(settings, "post_call_rest_hours", 12),
        "weekly": _number(settings, "max_weekly_hours", 60),
        "ratio": _number(settings, "supervision_ratio", 3),
    }


def _issue(code: str, message: str, resource: str, identifier: str,
           severity: str = "error", clinician_id: str | None = None) -> dict:
    result = {"code": code, "message": message, "resource": resource,
              "id": identifier, "severity": severity}
    if clinician_id:
        result["clinician_id"] = clinician_id
    return result


def _availability_windows(clinician: dict, interval: tuple, tz: ZoneInfo) -> list[tuple]:
    availability = clinician.get("availability", {})
    weekdays = set(availability.get("weekdays", range(5)))
    start_clock = time.fromisoformat(availability.get("start", "07:00"))
    end_clock = time.fromisoformat(availability.get("end", "19:00"))
    day = interval[0].astimezone(tz).date() - timedelta(days=1)
    final = interval[1].astimezone(tz).date()
    windows = []
    while day <= final:
        if day.weekday() in weekdays:
            begin = datetime.combine(day, start_clock, tzinfo=tz).astimezone(UTC)
            end_day = day + timedelta(days=end_clock <= start_clock)
            finish = datetime.combine(end_day, end_clock, tzinfo=tz).astimezone(UTC)
            if finish > begin and _overlap((begin, finish), interval):
                windows.append((begin, finish))
        day += timedelta(days=1)
    return _merge(windows)


def _merge(intervals: list[tuple]) -> list[tuple]:
    merged: list[list] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _available(clinician: dict, interval: tuple, tz: ZoneInfo) -> bool:
    return any(start <= interval[0] and interval[1] <= end
               for start, end in _availability_windows(clinician, interval, tz))


def _approved_leave(data: dict) -> dict[str, list[tuple]]:
    leave: dict[str, list[tuple]] = {}
    for request in data.get("requests", []):
        if request.get("kind") == "leave" and request.get("status") == "approved":
            leave.setdefault(request["clinician_id"], []).append(_interval(request))
    return leave


def _eligibility(clinician: dict, item: dict, interval: tuple, tz: ZoneInfo,
                 leave: dict, is_case: bool = False) -> list[tuple[str, str]]:
    reasons = []
    if not clinician.get("active", True):
        reasons.append(("inactive", "Clinician is inactive."))
    if clinician.get("role") != item.get("role"):
        reasons.append(("role", f"Requires professional role {item.get('role')}."))
    if item.get("site_id") not in clinician.get("sites", []):
        reasons.append(("site", "Clinician does not have privileges at this site."))
    if item.get("specialty") not in clinician.get("specialties", []):
        reasons.append(("specialty", f"Requires {item.get('specialty')} competency."))
    if any(_overlap(interval, absence) for absence in leave.get(clinician["id"], [])):
        reasons.append(("leave", "Assignment overlaps approved time off."))
    if not is_case:
        if item.get("kind") in CALL_KINDS:
            if not clinician.get("call_eligible", False):
                reasons.append(("call_eligibility", "Clinician is not eligible for call."))
        elif not _available(clinician, interval, tz):
            reasons.append(("availability", "Shift falls outside recurring availability."))
    return reasons


def _week_parts(interval: tuple, tz: ZoneInfo) -> dict[str, int]:
    day = interval[0].astimezone(tz).date()
    monday = day - timedelta(days=day.weekday())
    parts = {}
    while True:
        start = datetime.combine(monday, time.min, tzinfo=tz).astimezone(UTC)
        end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=tz).astimezone(UTC)
        if start >= interval[1]:
            break
        segment = (max(start, interval[0]), min(end, interval[1]))
        if segment[1] > segment[0]:
            parts[monday.isoformat()] = _minutes(segment)
        monday += timedelta(days=7)
    return parts


def _conflict(left: dict, right: dict, intervals: dict, policy: dict) -> str | None:
    a, b = intervals[left["id"]], intervals[right["id"]]
    if _overlap(a, b):
        return "overlap"
    earlier, later = (left, right) if a[0] < b[0] else (right, left)
    gap = (intervals[later["id"]][0] - intervals[earlier["id"]][1]).total_seconds() / 3600
    required = max(policy["rest"], policy["post_call"] if earlier.get("kind") in CALL_KINDS else 0)
    return "post_call_rest" if gap < required and earlier.get("kind") in CALL_KINDS else (
        "rest" if gap < required else None)


def _matching_shift(case: dict, shift: dict, clinician_id: str, intervals: dict,
                    case_interval: tuple) -> bool:
    return (shift.get("clinician_id") == clinician_id
            and shift.get("kind") in {"day", "primary_call"}
            and shift.get("site_id") == case.get("site_id")
            and shift.get("specialty") == case.get("specialty")
            and shift.get("role") == case.get("role")
            and intervals[shift["id"]][0] <= case_interval[0]
            and case_interval[1] <= intervals[shift["id"]][1])


def _structural_issues(data: dict) -> list[dict]:
    issues = []
    try:
        _timezone(data)
        policy = _policy(data)
        if policy["ratio"] < 1 or policy["ratio"] != int(policy["ratio"]):
            raise ValueError("supervision_ratio must be a positive integer.")
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        return [_issue("settings", str(exc), "clinicians", "settings")]
    sites = {site.get("id") for site in data.get("sites", [])}
    clinicians = {person.get("id") for person in data.get("clinicians", [])}
    for resource in ("clinicians", "shifts", "cases"):
        seen = set()
        for record in data.get(resource, []):
            identifier = record.get("id", "missing")
            if not record.get("id") or identifier in seen:
                issues.append(_issue("invalid_id", "IDs must be present and unique.", resource, identifier))
            seen.add(identifier)
            if record.get("role") not in ROLES:
                issues.append(_issue("invalid_role", "Professional role must be MD, CRNA, CAA, or PA.", resource, identifier))
            if resource == "clinicians":
                try:
                    window = record.get("availability", {})
                    if any(type(day) is not int or day not in range(7) for day in window.get("weekdays", range(5))):
                        raise ValueError("Availability weekdays must be integers from 0 to 6.")
                    for name, default in (("start", "07:00"), ("end", "19:00")):
                        value = time.fromisoformat(window.get(name, default))
                        if value.tzinfo is not None:
                            raise ValueError("Availability times use the group timezone, without an offset.")
                    for name, default in (("fte", 1), ("target_hours", 144)):
                        _number(record, name, default)
                    if any(site not in sites for site in record.get("sites", [])):
                        raise ValueError("Clinician references an unknown site.")
                    if record.get("role") == "MD" and record.get("supervision_required"):
                        raise ValueError("MD supervisors cannot themselves require supervision in this model.")
                except (ValueError, TypeError) as exc:
                    issues.append(_issue("invalid_clinician", str(exc), resource, identifier))
                continue
            try:
                _interval(record)
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(_issue("invalid_interval", str(exc), resource, identifier))
            if record.get("site_id") not in sites:
                issues.append(_issue("unknown_site", "Assignment references an unknown site.", resource, identifier))
            if not record.get("specialty"):
                issues.append(_issue("missing_specialty", "Assignment requires a specialty.", resource, identifier))
            if record.get("clinician_id") and record["clinician_id"] not in clinicians:
                issues.append(_issue("unknown_clinician", "Assignment references an unknown clinician.", resource, identifier))
            if resource == "shifts" and record.get("kind") not in {"day", *CALL_KINDS}:
                issues.append(_issue("invalid_shift_kind", "Unknown shift kind.", resource, identifier))
    for request in data.get("requests", []):
        if request.get("kind") == "leave" and request.get("status") in {"approved", "pending"}:
            try:
                _interval(request)
                if request.get("clinician_id") not in clinicians:
                    raise ValueError("Time-off request references an unknown clinician.")
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(_issue("invalid_leave", str(exc), "clinicians", request.get("clinician_id", "missing")))
    return issues


def validate_schedule(data: dict) -> list[dict]:
    """Report uncovered slots separately from safety and data errors."""
    issues = _structural_issues(data)
    if issues:
        return issues
    tz, policy, leave = _timezone(data), _policy(data), _approved_leave(data)
    clinicians = {person["id"]: person for person in data.get("clinicians", [])}
    shifts, cases = data.get("shifts", []), data.get("cases", [])
    intervals = {item["id"]: _interval(item) for item in shifts}
    case_intervals = {item["id"]: _interval(item) for item in cases}
    by_clinician: dict[str, list] = {}
    for shift in shifts:
        identifier, person_id = shift["id"], shift.get("clinician_id")
        if not person_id:
            issues.append(_issue("uncovered_shift", "Required shift has no assigned clinician.", "shifts", identifier, "gap"))
            continue
        person = clinicians[person_id]
        for code, message in _eligibility(person, shift, intervals[identifier], tz, leave):
            issues.append(_issue(code, message, "shifts", identifier, clinician_id=person_id))
        by_clinician.setdefault(person_id, []).append(shift)
    for person_id, assignments in by_clinician.items():
        assignments.sort(key=lambda shift: intervals[shift["id"]][0])
        weekly: dict[str, int] = {}
        for index, shift in enumerate(assignments):
            for week, minutes in _week_parts(intervals[shift["id"]], tz).items():
                weekly[week] = weekly.get(week, 0) + minutes
            for other in assignments[index + 1:]:
                code = _conflict(shift, other, intervals, policy)
                if code:
                    issues.append(_issue(code, f"Conflicts with shift {shift['id']} ({code.replace('_', ' ')}).", "shifts", other["id"], clinician_id=person_id))
        for week, minutes in weekly.items():
            if minutes > math.floor(policy["weekly"] * 60):
                issues.append(_issue("weekly_hours", f"Week beginning {week}: {minutes / 60:g} assigned hours exceeds {policy['weekly']:g}.", "clinicians", person_id, clinician_id=person_id))
    # Count each simultaneous staffing slot exactly once. Backup call is a
    # reservation, not direct care, and cannot provide supervision.
    supervision_reported = set()
    for site_id in {shift["site_id"] for shift in shifts}:
        direct = [shift for shift in shifts if shift["site_id"] == site_id
                  and shift.get("clinician_id") and shift["kind"] != "backup_call"]
        boundaries = sorted({point for shift in direct for point in intervals[shift["id"]]})
        for start, end in zip(boundaries, boundaries[1:]):
            concurrent = [shift for shift in direct if _overlap((start, end), intervals[shift["id"]])]
            supervised = [shift for shift in concurrent if clinicians[shift["clinician_id"]].get("supervision_required", False)]
            supervisors = {shift["clinician_id"] for shift in concurrent if clinicians[shift["clinician_id"]]["role"] == "MD"}
            if len(supervised) > len(supervisors) * policy["ratio"]:
                for shift in supervised:
                    if shift["id"] not in supervision_reported:
                        issues.append(_issue("supervision", f"Insufficient MD supervision at {site_id} during {start.astimezone(tz).isoformat()}–{end.astimezone(tz).isoformat()}.", "shifts", shift["id"], clinician_id=shift["clinician_id"]))
                        supervision_reported.add(shift["id"])
    for index, case in enumerate(cases):
        identifier, person_id = case["id"], case.get("clinician_id")
        if not person_id:
            issues.append(_issue("uncovered_case", "Case has no assigned clinician.", "cases", identifier, "gap"))
        else:
            for code, message in _eligibility(clinicians[person_id], case, case_intervals[identifier], tz, leave, True):
                issues.append(_issue(code, message, "cases", identifier, clinician_id=person_id))
            if not any(_matching_shift(case, shift, person_id, intervals, case_intervals[identifier]) for shift in shifts):
                issues.append(_issue("outside_staffing", "Case must fit a matching assigned day or primary-call shift.", "cases", identifier, clinician_id=person_id))
        for other in cases[index + 1:]:
            if not _overlap(case_intervals[identifier], case_intervals[other["id"]]):
                continue
            if case.get("room") and case.get("room") == other.get("room") and case["site_id"] == other["site_id"]:
                issues.append(_issue("room_overlap", f"Room is already occupied by case {identifier}.", "cases", other["id"]))
            if person_id and person_id == other.get("clinician_id"):
                issues.append(_issue("case_overlap", f"Clinician is already assigned to case {identifier}.", "cases", other["id"], clinician_id=person_id))
    return issues


def _leave_capacity_risks(data: dict, projected: dict, clinician_id: str,
                         leave_interval: tuple) -> tuple[list[dict], list[dict]]:
    """Compare concurrent open-slot capacity without running a monthly solve.

    Matching accounts for each clinician once, regardless of overlapping
    specialties or site privileges. Weekly max flow also bounds contracted
    hard-hour capacity across all open slots. These are capacity bounds:
    supervision choices and rest between still-unassigned future shifts need
    the complete optimizer. A reduction is therefore labeled a capacity risk.
    """
    tz, policy = _timezone(data), _policy(data)
    people = {person["id"]: person for person in data.get("clinicians", [])}
    original_shifts = data.get("shifts", [])
    intervals = {shift["id"]: _interval(shift) for shift in original_shifts}
    week_parts = {identifier: _week_parts(interval, tz) for identifier, interval in intervals.items()}
    open_slots = [shift for shift in original_shifts if not shift.get("clinician_id") and not shift.get("locked")]
    proposed_targets = [shift for shift in open_slots if _overlap(intervals[shift["id"]], leave_interval)]
    if not proposed_targets:
        return [], []

    def existing_work(snapshot):
        by_person = {identifier: [] for identifier in people}
        hours = {identifier: {} for identifier in people}
        for shift in snapshot.get("shifts", []):
            person_id = shift.get("clinician_id")
            if not person_id:
                continue
            by_person[person_id].append(shift)
            for week, minutes in week_parts[shift["id"]].items():
                hours[person_id][week] = hours[person_id].get(week, 0) + minutes
        return by_person, hours

    before_work, before_hours = existing_work(data)
    after_work, after_hours = existing_work(projected)
    before_leave, after_leave = _approved_leave(data), _approved_leave(projected)

    def eligible(person_id, shift, absences, work, hours):
        if _eligibility(people[person_id], shift, intervals[shift["id"]], tz, absences):
            return False
        if any(_conflict(shift, other, intervals, policy) for other in work[person_id]):
            return False
        return all(hours[person_id].get(week, 0) + minutes <= math.floor(policy["weekly"] * 60)
                   for week, minutes in week_parts[shift["id"]].items())

    affected = [shift for shift in proposed_targets
                if eligible(clinician_id, shift, before_leave, before_work, before_hours)
                and not eligible(clinician_id, shift, after_leave, after_work, after_hours)]
    if not affected:
        return [], []
    affected_ids = {shift["id"] for shift in affected}
    affected_weeks = {week for shift in affected for week in week_parts[shift["id"]]}
    contenders = [shift for shift in open_slots if affected_weeks.intersection(week_parts[shift["id"]])]
    before_edges, after_edges = {}, {}
    for shift in contenders:
        before_edges[shift["id"]] = [pid for pid in sorted(people) if eligible(pid, shift, before_leave, before_work, before_hours)]
        after_edges[shift["id"]] = [pid for pid in sorted(people) if eligible(pid, shift, after_leave, after_work, after_hours)]

    def matching_size(identifiers, edges):
        assigned = {}
        def augment(identifier, seen):
            for person_id in edges[identifier]:
                if person_id in seen:
                    continue
                seen.add(person_id)
                if person_id not in assigned or augment(assigned[person_id], seen):
                    assigned[person_id] = identifier
                    return True
            return False
        for identifier in sorted(identifiers, key=lambda identifier: (len(edges[identifier]), identifier)):
            augment(identifier, set())
        return len(assigned)

    boundaries = sorted({point for shift in contenders for point in intervals[shift["id"]]})
    risks, slices, reported = [], [], set()
    for start, end in zip(boundaries, boundaries[1:]):
        active = [shift["id"] for shift in contenders if _overlap(intervals[shift["id"]], (start, end))]
        impacted = affected_ids.intersection(active)
        if not impacted:
            continue
        before_count = matching_size(active, before_edges)
        after_count = matching_size(active, after_edges)
        if after_count >= before_count:
            continue
        slices.append({"scope": "interval", "start": start.astimezone(tz).isoformat(), "end": end.astimezone(tz).isoformat(),
                       "open_slots": len(active), "coverable_before": before_count,
                       "coverable_after": after_count, "capacity_loss": before_count - after_count,
                       "certainty": "risk"})
        for identifier in sorted(impacted - reported):
            message = (f"Capacity risk: leave reduces simultaneous eligible coverage for open shifts from {before_count} to {after_count} "
                       f"of {len(active)} slots during {start.astimezone(tz).isoformat()}–{end.astimezone(tz).isoformat()}. "
                       "Each clinician is counted once. Optimize the schedule to assess the final coverage gap.")
            issue = _issue("leave_capacity_risk", message, "shifts", identifier, "gap", clinician_id)
            issue["certainty"] = "risk"
            risks.append(issue)
            reported.add(identifier)
    # Fractional assignment-hours are an upper bound on a real schedule. A
    # max-flow reduction can identify weekly shortages even when every single
    # time slice still has enough clinicians (e.g. continuous 24-hour call).
    # Interval and weekly records describe overlapping bounds, not quantities
    # to add together.
    from ortools.graph.python import max_flow

    def weekly_flow(week, edges, hours):
        selected = [shift for shift in contenders if week in week_parts[shift["id"]]]
        graph = max_flow.SimpleMaxFlow()
        person_nodes = {pid: index + 1 for index, pid in enumerate(sorted(people))}
        slot_nodes = {shift["id"]: index + len(people) + 1 for index, shift in enumerate(selected)}
        sink = len(people) + len(selected) + 1
        for pid, node in person_nodes.items():
            graph.add_arc_with_capacity(0, node, max(0, math.floor(policy["weekly"] * 60) - hours[pid].get(week, 0)))
        for shift in selected:
            identifier = shift["id"]
            minutes = week_parts[identifier][week]
            graph.add_arc_with_capacity(slot_nodes[identifier], sink, minutes)
            for pid in edges[identifier]:
                graph.add_arc_with_capacity(person_nodes[pid], slot_nodes[identifier], minutes)
        status = graph.solve(0, sink)
        if status != graph.OPTIMAL:
            raise ValueError("Could not calculate weekly coverage capacity.")
        return graph.optimal_flow(), sum(week_parts[shift["id"]][week] for shift in selected)

    for week in sorted(affected_weeks):
        before_minutes, required_minutes = weekly_flow(week, before_edges, before_hours)
        after_minutes, _ = weekly_flow(week, after_edges, after_hours)
        if after_minutes >= before_minutes:
            continue
        slices.append({"scope": "week", "week_start": week, "required_hours": required_minutes / 60,
                       "coverable_hours_before": before_minutes / 60, "coverable_hours_after": after_minutes / 60,
                       "capacity_loss_hours": (before_minutes - after_minutes) / 60, "certainty": "risk"})
        impacted = {shift["id"] for shift in affected if week in week_parts[shift["id"]]}
        for identifier in sorted(impacted - reported):
            message = (f"Capacity risk: eligible coverage in the week beginning {week} falls from {before_minutes / 60:g} "
                       f"to {after_minutes / 60:g} of {required_minutes / 60:g} required open-shift hours. "
                       "Weekly clinician hours are counted once across specialties and sites. Optimize the schedule to assess the final gap.")
            issue = _issue("leave_capacity_risk", message, "shifts", identifier, "gap", clinician_id)
            issue["certainty"] = "risk"
            risks.append(issue)
            reported.add(identifier)
    return risks, slices


def leave_impact(data: dict, clinician_id: str, start: str, end: str) -> dict:
    """Preview leave without changing normal availability or the input state."""
    interval, tz = _interval({"start": start, "end": end}), _timezone(data)
    person = next((person for person in data.get("clinicians", []) if person["id"] == clinician_id), None)
    if person is None:
        raise ValueError("Unknown clinician.")
    affected_shifts = [deepcopy(item) for item in data.get("shifts", [])
                       if item.get("clinician_id") == clinician_id and _overlap(_interval(item), interval)]
    affected_cases = [deepcopy(item) for item in data.get("cases", [])
                      if item.get("clinician_id") == clinician_id and _overlap(_interval(item), interval)]
    baseline = _availability_windows(person, interval, tz)
    baseline.extend(_interval(item) for item in affected_shifts if item["kind"] in CALL_KINDS)
    baseline = _merge([(max(a, interval[0]), min(b, interval[1])) for a, b in baseline])
    # An already approved absence must not inflate the newly lost capacity.
    existing_leave = _merge(_approved_leave(data).get(clinician_id, []))
    lost_seconds = 0.0
    for a, b in baseline:
        lost_seconds += (b - a).total_seconds()
        for absent_a, absent_b in existing_leave:
            if _overlap((a, b), (absent_a, absent_b)):
                lost_seconds -= (min(b, absent_b) - max(a, absent_a)).total_seconds()
    projected = deepcopy(data)
    projected.setdefault("requests", []).append({"id": "leave-impact-preview", "kind": "leave", "status": "approved",
                                                 "clinician_id": clinician_id, "start": start, "end": end})
    for resource in ("shifts", "cases"):
        for item in projected.get(resource, []):
            if item.get("clinician_id") == clinician_id and _overlap(_interval(item), interval):
                item.update(clinician_id=None, locked=False)
    before = {(issue["code"], issue["resource"], issue["id"], issue["message"])
              for issue in validate_schedule(data)}
    affected_ids = {"shifts": {item["id"] for item in affected_shifts},
                    "cases": {item["id"] for item in affected_cases}}
    impact_issues = [issue for issue in validate_schedule(projected)
                     if issue["id"] in affected_ids.get(issue["resource"], set())
                     or (issue["code"], issue["resource"], issue["id"], issue["message"]) not in before]
    capacity_risks, capacity_slices = _leave_capacity_risks(data, projected, clinician_id, interval)
    return {"affected_shifts": affected_shifts, "affected_cases": affected_cases,
            "lost_hours": round(max(0, lost_seconds) / 3600, 2),
            "issues": impact_issues + capacity_risks, "projected_capacity": capacity_slices}


def _in_scope(item: dict, day: Date | None, tz: ZoneInfo) -> bool:
    if day is None:
        return True
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return _overlap(_interval(item), (start, end))


def coverage_candidates(data: dict, shift_id: str) -> dict:
    """Find validated single-shift replacements without changing the snapshot.

    Related unlocked cases transfer with the shift. Existing unrelated gaps
    and errors are reported separately; a candidate must introduce no errors
    and must resolve all relevant errors in the affected coverage interval.
    Each candidate includes the exact changes needed for a subsequent ordinary
    proposal preview. This function does not request, approve, or apply them.
    """
    result = {"shift_id": shift_id, "candidates": [], "issues": [],
              "existing_issues": [], "rejections": [], "reason": ""}
    structural = _structural_issues(data)
    if structural:
        result.update(issues=structural, reason="Correct the invalid scheduling data before searching for coverage.")
        return result
    target = next((shift for shift in data.get("shifts", []) if shift["id"] == shift_id), None)
    if target is None:
        result.update(issues=[_issue("unknown_shift", "The requested shift does not exist.", "shifts", shift_id)],
                      reason="The requested shift does not exist.")
        return result
    baseline_errors = [issue for issue in validate_schedule(data) if issue["severity"] == "error"]
    result["existing_issues"] = baseline_errors
    if target.get("locked"):
        result.update(issues=[_issue("locked", "A scheduler must unlock this shift before its assignment can change.", "shifts", shift_id)],
                      reason="This shift is locked; no replacement can be proposed until a scheduler unlocks it.")
        return result
    tz, policy, leave = _timezone(data), _policy(data), _approved_leave(data)
    interval = _interval(target)
    shift_intervals = {shift["id"]: _interval(shift) for shift in data.get("shifts", [])}
    original_id = target.get("clinician_id")
    linked = [case for case in data.get("cases", []) if original_id
              and _matching_shift(case, target, original_id, shift_intervals, _interval(case))]
    locked = [case for case in linked if case.get("locked")]
    if locked:
        result.update(issues=[_issue("locked", "A linked case must be unlocked before its clinician can change.", "cases", case["id"]) for case in locked],
                      reason="A linked daily case is locked; ask a scheduler to review it before replacing this shift.")
        return result
    def fingerprint(issue):
        return issue["code"], issue["resource"], issue["id"], issue.get("clinician_id"), issue["message"]
    baseline_keys = {fingerprint(issue) for issue in baseline_errors}
    weeks = set(_week_parts(interval, tz))
    for person in sorted(data.get("clinicians", []), key=lambda person: (person["name"].casefold(), person["id"])):
        person_id = person["id"]
        if person_id == original_id:
            continue
        reasons = [_issue(code, message, "shifts", shift_id, clinician_id=person_id)
                   for code, message in _eligibility(person, target, interval, tz, leave)]
        changes = [{"resource": "shifts", "id": shift_id, "patch": {"clinician_id": person_id}}]
        changes.extend({"resource": "cases", "id": case["id"], "patch": {"clinician_id": person_id}} for case in linked)
        if not reasons:
            projected = deepcopy(data)
            for change in changes:
                next(item for item in projected[change["resource"]] if item["id"] == change["id"]).update(change["patch"])
            relevant_shifts = {shift_id}
            for other in projected.get("shifts", []):
                if other["id"] == shift_id:
                    continue
                if (other.get("clinician_id") == person_id and _conflict(target, other, shift_intervals, policy)):
                    relevant_shifts.add(other["id"])
                if other["site_id"] == target["site_id"] and _overlap(shift_intervals[other["id"]], interval):
                    relevant_shifts.add(other["id"])
            relevant_cases = {case["id"] for case in linked}
            relevant_cases.update(case["id"] for case in projected.get("cases", [])
                                  if case.get("clinician_id") == person_id and _overlap(_interval(case), interval))
            for issue in validate_schedule(projected):
                if issue["severity"] != "error":
                    continue
                relevant = (issue["resource"] == "shifts" and issue["id"] in relevant_shifts
                            or issue["resource"] == "cases" and issue["id"] in relevant_cases
                            or issue["code"] == "weekly_hours" and issue.get("clinician_id") == person_id
                            and any(week in issue["message"] for week in weeks))
                if relevant or fingerprint(issue) not in baseline_keys:
                    reasons.append(issue)
        if reasons:
            result["rejections"].append({"clinician_id": person_id, "name": person["name"], "issues": reasons})
        else:
            result["candidates"].append({"clinician_id": person_id, "name": person["name"], "role": person["role"],
                                         "changes": changes})
    count = len(result["candidates"])
    result["reason"] = (f"{count} replacement{'s' if count != 1 else ''} pass the current eligibility, time-off, rest, weekly-hours, supervision, and linked-case checks. Review a proposal before applying any change."
                        if count else "No replacement passes the current scheduling rules. Review the candidate-specific conflicts or ask a scheduler to optimize several assignments together.")
    if baseline_errors:
        result["reason"] += " The current schedule also has existing errors listed separately; passing this check does not resolve unrelated errors."
    return result


def solve_schedule(data: dict, stage: str = "staffing", date: str | None = None,
                   time_limit: float = 30, month: str | None = None, *,
                   scope_ids: set[str] | list[str] | None = None,
                   _defer_case_validation: bool = False) -> dict:
    """Optimize safe assignments, retaining explicit gaps if demand cannot fit.

    The objective is lexicographic by bounded integer weights: first minimize
    uncovered slots, then changes to existing assignments, then workload and
    preferences. Fixed assignments are never silently unlocked or relaxed.
    """
    started = clock.monotonic()
    if stage not in {"staffing", "daily"}:
        raise ValueError("Stage must be staffing or daily.")
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("Time limit must be positive and finite.")
    day = Date.fromisoformat(date) if date else None
    selected_month = Date.fromisoformat(f"{month}-01") if month else None
    if stage == "staffing" and selected_month and day:
        raise ValueError("Choose a staffing month or a staffing date, not both.")
    if scope_ids is not None and (day or selected_month):
        raise ValueError("Choose explicit assignment IDs or a calendar scope, not both.")
    if _defer_case_validation and stage != "staffing":
        raise ValueError("Case validation can only be deferred during staffing repair.")
    result: dict[str, Any] = {"status": "INFEASIBLE", "changes": [], "gaps": [], "metrics": {"stage": stage}, "explanations": []}
    if stage == "staffing" and selected_month:
        result["metrics"]["month"] = selected_month.strftime("%Y-%m")
    structural = _structural_issues(data)
    if structural:
        result["status"] = "MODEL_INVALID"
        result["explanations"] = [issue["message"] for issue in structural]
        result["metrics"]["errors"] = structural
        return result
    from ortools.sat.python import cp_model

    tz, policy, leave = _timezone(data), _policy(data), _approved_leave(data)
    people = {person["id"]: person for person in data.get("clinicians", [])}
    shifts, cases = data.get("shifts", []), data.get("cases", [])
    shift_intervals = {shift["id"]: _interval(shift) for shift in shifts}
    items = shifts if stage == "staffing" else cases
    resource = "shifts" if stage == "staffing" else "cases"
    intervals = {item["id"]: _interval(item) for item in items}
    if scope_ids is not None:
        scope = set(scope_ids)
        if not scope <= set(intervals):
            raise ValueError("The optimization scope contains unknown assignment IDs.")
    elif stage == "staffing" and selected_month:
        scope = {item["id"] for item in items if intervals[item["id"]][0].astimezone(tz).date().replace(day=1) == selected_month}
    else:
        scope = {item["id"] for item in items if _in_scope(item, day, tz)}
    model = cp_model.CpModel()
    variables: dict[tuple[str, str], Any] = {}
    item_vars: dict[str, list] = {}
    clinician_vars: dict[str, list] = {person_id: [] for person_id in people}
    gaps, preserved, preference_terms = [], [], []
    fixed_errors = []
    baseline_issues = validate_schedule(data)
    # Room conflicts are fixed surgical input. Daily staffing cannot be solved
    # safely around an already invalid staffing roster.
    blocking = [issue for issue in baseline_issues if issue["code"] == "room_overlap"
                or (stage == "daily" and issue["resource"] != "cases" and issue["severity"] == "error")]
    if blocking:
        result["explanations"] = [issue["message"] for issue in blocking]
        result["metrics"]["errors"] = blocking
        return result
    for item in items:
        identifier = item["id"]
        editable = identifier in scope and not item.get("locked", False)
        candidate_vars = []
        for person_id, person in people.items():
            reasons = _eligibility(person, item, intervals[identifier], tz, leave, stage == "daily")
            if not reasons and stage == "daily" and not any(
                    _matching_shift(item, shift, person_id, shift_intervals, intervals[identifier]) for shift in shifts):
                reasons = [("outside_staffing", "No matching staffed shift covers this case.")]
            if reasons:
                continue
            variable = model.new_bool_var(f"assign_{identifier}_{person_id}")
            variables[identifier, person_id] = variable
            candidate_vars.append(variable)
            clinician_vars[person_id].append((item, variable))
            if not editable:
                model.add(variable == int(item.get("clinician_id") == person_id))
            duration = _minutes(intervals[identifier]) / 60
            preferred_durations = person.get("preferred_shift_hours", [])
            preference_cost = (20 if stage == "staffing" and preferred_durations
                               and not any(abs(float(value) - duration) < .02 for value in preferred_durations) else 0)
            preferred_specialties = person.get("preferred_specialties", [])
            if preferred_specialties and item["specialty"] not in preferred_specialties:
                preference_cost += 10
            if preference_cost:
                preference_terms.append(preference_cost * variable)
        item_vars[identifier] = candidate_vars
        model.add(sum(candidate_vars) <= 1)
        if not editable and item.get("clinician_id"):
            if (identifier, item["clinician_id"]) not in variables:
                fixed_errors.append(f"Fixed {resource[:-1]} {identifier} is not eligible for its current clinician; resolve it before optimizing.")
            else:
                model.add(sum(candidate_vars) == 1)
        if identifier in scope:
            gap = model.new_bool_var(f"gap_{identifier}")
            model.add(sum(candidate_vars) + gap == 1)
            gaps.append(gap)
            previous = item.get("clinician_id")
            if editable and previous:
                preserved.append(1 - variables.get((identifier, previous), 0))
        if editable and item.get("clinician_id") and (identifier, item["clinician_id"]) in variables:
            model.add_hint(variables[identifier, item["clinician_id"]], 1)
    if fixed_errors:
        result["explanations"] = fixed_errors
        return result
    for person_id, entries in clinician_vars.items():
        entries.sort(key=lambda pair: intervals[pair[0]["id"]][0])
        weekly: dict[str, list] = {}
        for index, (item, variable) in enumerate(entries):
            if stage == "staffing":
                for week, minutes in _week_parts(intervals[item["id"]], tz).items():
                    weekly.setdefault(week, []).append(minutes * variable)
            for other, other_variable in entries[index + 1:]:
                if stage == "staffing":
                    # Entries are ordered; later starts cannot conflict once
                    # they pass the longest possible required rest interval.
                    if intervals[other["id"]][0] >= intervals[item["id"]][1] + timedelta(hours=max(policy["rest"], policy["post_call"])):
                        break
                    incompatible = _conflict(item, other, intervals, policy)
                else:
                    if intervals[other["id"]][0] >= intervals[item["id"]][1]:
                        break
                    incompatible = _overlap(intervals[item["id"]], intervals[other["id"]])
                if incompatible:
                    model.add(variable + other_variable <= 1)
        for terms in weekly.values():
            model.add(sum(terms) <= math.floor(policy["weekly"] * 60))
    if stage == "staffing":
        for site_id in {shift["site_id"] for shift in shifts}:
            direct = [shift for shift in shifts if shift["site_id"] == site_id and shift["kind"] != "backup_call"]
            boundaries = sorted({point for shift in direct for point in intervals[shift["id"]]})
            seen_sets = set()
            for start, end in zip(boundaries, boundaries[1:]):
                concurrent = [shift for shift in direct if _overlap((start, end), intervals[shift["id"]])]
                signature = tuple(shift["id"] for shift in concurrent)
                if signature in seen_sets:
                    continue
                seen_sets.add(signature)
                supervised, supervisors = [], []
                for shift in concurrent:
                    for person_id, person in people.items():
                        variable = variables.get((shift["id"], person_id))
                        if variable is None:
                            continue
                        if person.get("supervision_required", False):
                            supervised.append(variable)
                        if person["role"] == "MD":
                            supervisors.append(variable)
                if supervised:
                    model.add(sum(supervised) <= int(policy["ratio"]) * sum(supervisors))
        # Locked daily assignments also lock the need for their staffing slot.
        for case in cases:
            if case.get("locked") and case.get("clinician_id"):
                supported = []
                case_interval = _interval(case)
                for shift in shifts:
                    hypothetical = dict(shift, clinician_id=case["clinician_id"])
                    variable = variables.get((shift["id"], case["clinician_id"]))
                    if variable is not None and _matching_shift(case, hypothetical, case["clinician_id"], intervals, case_interval):
                        supported.append(variable)
                model.add(sum(supported) >= 1)
    # Normalize demand within each professional role by contracted workload.
    # Comparing everyone directly to an unreachable full-time target makes
    # absolute-deviation cost constant when demand is below total capacity.
    # Using demand shares preserves meaningful fairness in that situation.
    total_minutes = sum(_minutes(value) for value in intervals.values())
    role_minutes = {role: sum(_minutes(intervals[item["id"]]) for item in items if item["role"] == role)
                    for role in ROLES}
    shares = {person_id: float(person.get("fte", 1)) * float(person.get("target_hours", 144))
              if person.get("active", True) else 0 for person_id, person in people.items()}
    fairness, fairness_bound = [], 0
    for person_id, person in people.items():
        role_share = sum(shares[pid] for pid, other in people.items() if other["role"] == person["role"]) or 1
        target = round(role_minutes[person["role"]] * shares[person_id] / role_share)
        bound = total_minutes + target
        deviation = model.new_int_var(0, bound, f"workload_deviation_{person_id}")
        load = sum(_minutes(intervals[item["id"]]) * variable for item, variable in clinician_vars[person_id])
        model.add_abs_equality(deviation, load - target)
        fairness.append(deviation)
        fairness_bound += bound
    # Additional call/weekend balance uses contracted FTE share. It is kept
    # below preservation, just like specialty and shift-duration preferences.
    if stage == "staffing":
        for label, selected in (
            ("call", [item for item in items if item.get("kind") in CALL_KINDS]),
            ("weekend", [item for item in items if intervals[item["id"]][0].astimezone(tz).weekday() >= 5]),
        ):
            selected_ids = {item["id"] for item in selected}
            for person_id, person in people.items():
                if label == "call" and not person.get("call_eligible"):
                    continue
                eligible_share = sum(shares[pid] for pid, other in people.items() if other["role"] == person["role"]
                                     and (label != "call" or other.get("call_eligible"))) or 1
                role_count = sum(item["role"] == person["role"] for item in selected)
                target = round(role_count * 60 * shares[person_id] / eligible_share)
                bound = len(selected) * 60 + target
                deviation = model.new_int_var(0, bound, f"{label}_deviation_{person_id}")
                model.add_abs_equality(deviation, sum(60 * variable for item, variable in clinician_vars[person_id]
                                                      if item["id"] in selected_ids) - target)
                fairness.append(deviation)
                fairness_bound += bound
    lower_bound = fairness_bound + 30 * len(items) + 1
    preserve_weight = lower_bound
    gap_weight = (len(preserved) + 1) * preserve_weight
    model.minimize(gap_weight * sum(gaps) + preserve_weight * sum(preserved)
                   + sum(fairness) + sum(preference_terms))
    solver = cp_model.CpSolver()
    remaining = time_limit - (clock.monotonic() - started)
    if remaining <= 0:
        result.update(status="UNKNOWN", gaps=[issue for issue in baseline_issues if issue["severity"] == "gap"],
                      explanations=["The time budget ended while preparing the optimization model. No changes were proposed."])
        result["metrics"].update(solver_status="UNKNOWN", wall_time_seconds=round(clock.monotonic() - started, 3),
                                 total_slots=len(scope), time_limit_seconds=time_limit)
        return result
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_search_workers = 4
    solver.parameters.random_seed = 17
    status = solver.solve(model)
    result["metrics"].update(solver_status=solver.status_name(status),
                              wall_time_seconds=round(clock.monotonic() - started, 3),
                              total_slots=len(scope), time_limit_seconds=time_limit)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["status"] = "INFEASIBLE" if status == cp_model.INFEASIBLE else "UNKNOWN" if status == cp_model.UNKNOWN else "MODEL_INVALID"
        result["gaps"] = [issue for issue in baseline_issues if issue["severity"] == "gap"]
        result["explanations"] = ["The fixed assignments and hard scheduling rules cannot all be satisfied. Resolve locks or existing conflicts before trying again."
                                  if status == cp_model.INFEASIBLE else "The time limit was reached before a safe solution was found. No changes were proposed."
                                  if status == cp_model.UNKNOWN else "The optimization model could not be solved."]
        return result
    projected = deepcopy(data)
    for item in projected.get(resource, []):
        if item["id"] not in scope or item.get("locked", False):
            continue
        assigned = next((person_id for person_id in people
                         if (item["id"], person_id) in variables and solver.value(variables[item["id"], person_id])), None)
        if assigned != item.get("clinician_id"):
            item["clinician_id"] = assigned
            result["changes"].append({"resource": resource, "id": item["id"], "patch": {"clinician_id": assigned}})
    if stage == "staffing":
        changed_ids = {change["id"] for change in result["changes"]}
        changed_original_shifts = [shift for shift in shifts if shift["id"] in changed_ids]
        for case in projected.get("cases", []):
            if not case.get("clinician_id") or case.get("locked"):
                continue
            case_interval = _interval(case)
            if not any(_matching_shift(case, shift, case["clinician_id"], intervals, case_interval)
                       for shift in changed_original_shifts):
                continue
            if not any(_matching_shift(case, shift, case["clinician_id"], intervals, case_interval)
                       for shift in projected.get("shifts", [])):
                case["clinician_id"] = None
                result["changes"].append({"resource": "cases", "id": case["id"], "patch": {"clinician_id": None}})
    issues = validate_schedule(projected)
    errors = [issue for issue in issues if issue["severity"] == "error"
              and not (_defer_case_validation and issue["resource"] == "cases")]
    if errors:
        # Final independent validation is authoritative, including constraints
        # not expressible as assignment choices (e.g. malformed locked cases).
        result["changes"] = []
        result["status"] = "INFEASIBLE"
        result["metrics"]["errors"] = errors
        result["explanations"] = [issue["message"] for issue in errors]
        result["gaps"] = [issue for issue in baseline_issues if issue["severity"] == "gap"]
        return result
    result["status"] = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
    result["gaps"] = [issue for issue in issues if issue["severity"] == "gap"]
    scope_gaps = [issue for issue in result["gaps"] if issue["resource"] == resource and issue["id"] in scope]
    result["metrics"].update(assigned_slots=len(scope) - len(scope_gaps),
                              uncovered_slots=len(scope_gaps), full_coverage=not scope_gaps,
                              changed_assignments=len(result["changes"]),
                              objective=solver.objective_value)
    result["explanations"] = [f"{len(scope) - len(scope_gaps)} of {len(scope)} {stage} slots covered; {len(scope_gaps)} remain open.",
                               "Eligibility, approved leave, overlapping work, rest, weekly hours, supervision, and locked assignments remain mandatory.",
                               "The objective prioritizes coverage, then preserving assignments, then FTE-adjusted workload, call/weekend balance, and preferences."]
    if scope_gaps:
        result["explanations"].append("This is a partial proposal with explicit coverage gaps; it is not ready for publication.")
    if status == cp_model.FEASIBLE:
        result["explanations"].append("A safe solution was found within the time limit; optimality was not proven.")
    return result
