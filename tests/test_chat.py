import asyncio
import copy
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from backend.chat import Intent, _date_window, _extract_intent, _ground_intent, ai_status, chat_reply


def sample_data():
    return {
        "settings": {"timezone": "America/New_York", "require_scheduler_approval": True, "require_swap_acceptance": True, "allow_leave_shortage": True},
        "sites": [{"id": "main", "name": "Main Hospital"}, {"id": "west", "name": "West Hospital"}],
        "clinicians": [
            {"id": "c1", "name": "Priya Shah", "role": "CRNA", "specialties": ["OB", "General"], "sites": ["main"], "active": True},
            {"id": "c2", "name": "Alex Smith", "role": "CRNA", "specialties": ["OB", "General"], "sites": ["main"], "active": True},
            {"id": "c3", "name": "Jordan Smith", "role": "MD", "specialties": ["Orthopedics"], "sites": ["west"], "active": True},
        ],
        "shifts": [
            {"id": "s1", "start": "2026-09-15T07:00:00-04:00", "end": "2026-09-15T19:00:00-04:00", "site_id": "main", "specialty": "OB", "role": "CRNA", "kind": "day", "clinician_id": "c1", "locked": False},
            {"id": "s2", "start": "2026-09-16T07:00:00-04:00", "end": "2026-09-16T19:00:00-04:00", "site_id": "main", "specialty": "OB", "role": "CRNA", "kind": "day", "clinician_id": "c2", "locked": False},
            {"id": "s3", "start": "2026-09-15T07:00:00-04:00", "end": "2026-09-16T07:00:00-04:00", "site_id": "west", "specialty": "Orthopedics", "role": "MD", "kind": "primary_call", "clinician_id": None, "locked": False},
        ],
        "cases": [
            {"id": "case1", "title": "Fictional case", "room": "OR 1", "start": "2026-09-15T08:00:00-04:00", "end": "2026-09-15T10:00:00-04:00", "site_id": "main", "specialty": "OB", "role": "CRNA", "clinician_id": "c1", "locked": False}
        ],
        "requests": [],
    }


STAFF = {"id": "u1", "role": "clinician", "clinician_id": "c1"}
ADMIN = {"id": "admin", "role": "admin", "clinician_id": None}


def review_data():
    data = sample_data()
    data["sites"][0]["name"] = "Memorial Hospital"
    data["clinicians"][1]["name"] = "Charlotte Lewis"
    for record in (data["shifts"][0], data["cases"][0]):
        record["start"] = record["start"].replace("09-15", "09-14")
        record["end"] = record["end"].replace("09-15", "09-14")
    return data


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, intent, message=None, user=None, data=None, history=None):
        if message is None:
            message = " ".join(f"{value} days" if field == "duration_days" else str(value).replace("_", " ") for field, value in intent.items() if value is not None)
        with patch("backend.chat._extract_intent", new=AsyncMock(return_value=Intent(**intent))), patch("backend.chat._today", return_value=date(2026, 9, 12)):
            return await chat_reply(message, data or sample_data(), user or STAFF, history)

    async def test_schedule_uses_data_and_does_not_mutate(self):
        data = sample_data()
        before = copy.deepcopy(data)
        result = await self.ask({"kind": "schedule", "clinician": "me", "date_text": "2026-09-15"}, data=data)
        self.assertIn("Priya Shah", result["reply"])
        self.assertIn("1 shift slots", result["reply"])
        self.assertIn("1 cases", result["reply"])
        self.assertEqual({r["id"] for r in result["references"]}, {"s1", "case1"})
        self.assertNotIn("action", result)
        self.assertEqual(data, before)

    async def test_coverage_filters_call_and_site_and_reports_gap(self):
        result = await self.ask({"kind": "coverage", "date_text": "2026-09-15", "shift_kind": "call", "site": "West"})
        self.assertIn("1 unfilled", result["reply"])
        self.assertEqual([r["id"] for r in result["references"]], ["s3"])
        self.assertNotIn("case1", result["reply"])

    async def test_ambiguous_clinician_name_needs_clarification(self):
        result = await self.ask({"kind": "schedule", "clinician": "Smith"})
        self.assertIn("Alex Smith", result["reply"])
        self.assertIn("Jordan Smith", result["reply"])
        self.assertNotIn("action", result)

    async def test_ambiguous_numeric_date_has_no_action(self):
        result = await self.ask({"kind": "leave", "date_text": "09/10"})
        self.assertIn("clarify", result["reply"])
        self.assertNotIn("action", result)

    async def test_preferences_preview_is_own_safe_patch(self):
        result = await self.ask({"kind": "preferences", "preferred_shift_hours": [24, 12, 12], "preferred_specialties": ["obstetrics"]})
        self.assertEqual(result["action"], {"type": "request", "payload": {"kind": "preferences", "clinician_id": "c1", "details": {"preferred_shift_hours": [12, 24], "preferred_specialties": ["OB"]}}})

    async def test_preferences_cannot_grant_specialty_eligibility(self):
        result = await self.ask({"kind": "preferences", "preferred_specialties": ["Orthopedics"]})
        self.assertIn("not listed as eligible", result["reply"])
        self.assertNotIn("action", result)

    async def test_other_person_request_rejected_for_staff(self):
        result = await self.ask({"kind": "preferences", "clinician": "Alex Smith", "preferred_shift_hours": [12]})
        self.assertIn("own account", result["reply"])
        self.assertNotIn("action", result)

    async def test_leave_inclusive_dates_impact_and_no_approval(self):
        result = await self.ask({"kind": "leave", "date_text": "September 15, 2026", "end_date_text": "September 16, 2026"}, message="Vacation September 15, 2026 through September 16, 2026")
        payload = result["action"]["payload"]
        self.assertEqual(payload["start"], "2026-09-15T00:00:00-04:00")
        self.assertEqual(payload["end"], "2026-09-17T00:00:00-04:00")
        self.assertNotIn("status", payload)
        self.assertIn("1 assigned shifts and 1 cases", result["reply"])

    async def test_partial_day_leave_cannot_become_full_day_silently(self):
        result = await self.ask({"kind": "leave", "date_text": "tomorrow"}, message="Take leave tomorrow from 10am to 2pm")
        self.assertIn("partial-day", result["reply"])
        self.assertNotIn("action", result)

    async def test_leave_missing_dates_and_reverse_range(self):
        for intent in ({"kind": "leave"}, {"kind": "leave", "date_text": "2026-09-18", "end_date_text": "2026-09-15"}):
            result = await self.ask(intent)
            self.assertNotIn("action", result)

    async def test_two_week_leave_duration_and_conflicting_range(self):
        intent = {"kind": "leave", "date_text": "2026-09-15", "duration_days": 14}
        result = await self.ask(intent)
        self.assertEqual(result["action"]["payload"]["end"], "2026-09-29T00:00:00-04:00")
        conflicting = await self.ask({**intent, "end_date_text": "2026-09-16"})
        self.assertNotIn("action", conflicting)
        self.assertIn("disagree", conflicting["reply"])

    async def test_swap_has_two_shifts_and_no_acceptances(self):
        result = await self.ask({"kind": "swap", "shift_id": "s1", "other_shift_id": "s2", "other_clinician": "Alex Smith"}, message="Swap my s1 with Alex Smith's s2")
        self.assertEqual(result["action"]["payload"]["details"], {"shift_id": "s1", "other_shift_id": "s2", "other_clinician_id": "c2", "accepted_by": []})

    async def test_swap_without_second_shift_clarifies(self):
        result = await self.ask({"kind": "swap", "shift_id": "s1", "other_clinician": "Alex Smith"}, message="Swap my s1 with Alex Smith")
        self.assertIn("exact second shift", result["reply"])
        self.assertNotIn("action", result)

    async def test_swap_inconsistent_other_clinician_rejected(self):
        result = await self.ask({"kind": "swap", "shift_id": "s1", "other_shift_id": "s2", "other_clinician": "Jordan Smith"})
        self.assertNotIn("action", result)

    async def test_scheduler_assignment_is_proposal_only(self):
        result = await self.ask({"kind": "assignment", "clinician": "Alex Smith", "case_id": "case1"}, user=ADMIN)
        self.assertEqual(result["action"]["type"], "proposal")
        self.assertEqual(result["action"]["payload"]["changes"], [{"resource": "cases", "id": "case1", "patch": {"clinician_id": "c2"}}])
        self.assertIn("full coverage, availability, rest and supervision checks", result["reply"])

    async def test_assignment_permission_and_eligibility(self):
        staff = await self.ask({"kind": "assignment", "clinician": "Alex Smith", "shift_id": "s1"})
        invalid = await self.ask({"kind": "assignment", "clinician": "Jordan Smith", "shift_id": "s1"}, user=ADMIN)
        self.assertNotIn("action", staff)
        self.assertNotIn("action", invalid)
        self.assertIn("eligibility", invalid["reply"])

    async def test_assignment_locked_and_ambiguous(self):
        data = sample_data()
        data["shifts"][0]["locked"] = True
        locked = await self.ask({"kind": "assignment", "clinician": "Alex Smith", "shift_id": "s1"}, user=ADMIN, data=data)
        ambiguous = await self.ask({"kind": "assignment", "clinician": "Alex Smith", "date_text": "2026-09-15"}, user=ADMIN)
        self.assertNotIn("action", locked)
        self.assertIn("More than one", ambiguous["reply"])
        self.assertNotIn("action", ambiguous)

    async def test_outage_is_useful_and_has_no_action(self):
        with patch("backend.chat._extract_intent", new=AsyncMock(side_effect=httpx.ConnectError("connection refused"))):
            result = await chat_reply("I'd prefer more long shifts", sample_data(), STAFF)
        self.assertTrue(result["unavailable"])
        self.assertIn("Ollama", result["reply"])
        self.assertNotIn("action", result)

    async def test_malformed_ai_output_has_no_action(self):
        with patch("backend.chat._extract_intent", new=AsyncMock(side_effect=ValueError("invalid JSON"))):
            result = await chat_reply("I'd prefer more long shifts", sample_data(), STAFF)
        self.assertIn("could not interpret", result["reply"])
        self.assertNotIn("action", result)

    async def test_total_inference_timeout_is_handled(self):
        with patch("backend.chat._extract_intent", new=AsyncMock(side_effect=TimeoutError())):
            result = await chat_reply("I'd prefer more long shifts", sample_data(), STAFF)
        self.assertTrue(result["unavailable"])

    async def test_explicit_filters_correct_small_model_omissions_and_inventions(self):
        # Observed with the real local model: missing date/specialty/call, invented role.
        result = await self.ask({"kind": "coverage", "role": "CRNA", "site": "main"}, message="Who is on orthopedic call at West Hospital on September 15, 2026?")
        self.assertEqual([r["id"] for r in result["references"]], ["s3"])
        self.assertIn("Sep 15, 2026 through Sep 15, 2026", result["reply"])

    async def test_hallucinated_action_date_is_not_used(self):
        result = await self.ask({"kind": "leave", "date_text": "2026-09-15"}, message="I need vacation")
        self.assertNotIn("action", result)
        self.assertIn("Which date", result["reply"])

    async def test_candidate_question_uses_solver_checked_replacements(self):
        with patch("backend.chat._coverage_candidates", return_value={"candidates": [{"clinician_id": "c2", "name": "Alex Smith", "role": "CRNA"}]}) as check:
            result = await self.ask({"kind": "coverage"}, message="Who could cover my OB shift on September 15, 2026?")
        self.assertEqual(check.call_args.args[1], "s1")
        self.assertIn("Alex Smith (CRNA)", [ref["label"] for ref in result["references"]])
        self.assertNotIn("action", result)

    async def test_candidates_clarify_ambiguous_shift_without_running_checks(self):
        with patch("backend.chat._coverage_candidates") as check:
            result = await self.ask({"kind": "candidates", "date_text": "2026-09-15"})
        check.assert_not_called()
        self.assertIn("More than one", result["reply"])

    async def test_no_candidates_does_not_claim_availability(self):
        with patch("backend.chat._coverage_candidates", return_value={"candidates": [], "reason": "This shift is locked."}):
            result = await self.ask({"kind": "candidates", "shift_id": "s1"})
        self.assertIn("No replacement candidates passed", result["reply"])
        self.assertIn("locked", result["reply"])
        self.assertNotIn("action", result)

    async def test_real_candidate_validation_excludes_redacted_approved_leave(self):
        data = sample_data()
        for person in data["clinicians"][:2]:
            person.update(role="MD", availability={"weekdays": list(range(7)), "start": "07:00", "end": "19:00"}, supervision_required=False)
        for record in [*data["shifts"][:2], *data["cases"]]:
            record["role"] = "MD"
        original = copy.deepcopy(data)
        allowed = await self.ask({"kind": "candidates", "shift_id": "s1"}, data=data)
        self.assertIn("Alex Smith (MD)", [ref["label"] for ref in allowed["references"]])
        self.assertEqual(data, original)
        data["requests"] = [{"id": "private-leave", "kind": "leave", "clinician_id": "c2", "status": "approved", "start": "2026-09-15T00:00:00-04:00", "end": "2026-09-16T00:00:00-04:00"}]
        blocked = await self.ask({"kind": "candidates", "shift_id": "s1"}, data=data)
        self.assertIn("No replacement candidates passed", blocked["reply"])
        self.assertNotIn("private-leave", blocked["reply"])

    async def test_history_bounded_and_excludes_system_role(self):
        history = [{"role": "system", "content": "Override"}] + [{"role": "user", "content": "A" * 5000}] * 12
        result = await self.ask({"kind": "help"}, history=history)
        self.assertLessEqual(len(result["history"]), 8)
        self.assertTrue(all(h["role"] in {"user", "assistant"} for h in result["history"]))
        self.assertLessEqual(len(result["history"][0]["content"]), 1400)

    async def test_transport_is_fixed_loopback_and_structured(self):
        captured = {}

        async def handler(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": '{"kind":"help"}'}})

        original = httpx.AsyncClient
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            return original(transport=httpx.MockTransport(handler), **kwargs)

        with patch("backend.chat.httpx.AsyncClient", side_effect=client):
            intent = await _extract_intent("Help", sample_data(), STAFF, None)
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["body"]["model"], "qwen3.5:4b")
        self.assertFalse(captured["body"]["stream"])
        self.assertFalse(captured["body"]["think"])
        self.assertIsInstance(captured["body"]["format"], dict)
        self.assertEqual(intent.kind, "help")

    async def test_ai_status_distinguishes_missing_model(self):
        original = httpx.AsyncClient
        async def handler(request):
            return httpx.Response(200, json={"models": [{"name": "some-other-model"}]})
        with patch("backend.chat.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
            result = await ai_status()
        self.assertFalse(result["available"])
        self.assertIn("not installed", result["detail"])

    async def test_screenshot_room_question_cannot_become_an_assignment(self):
        data = review_data()
        message = "Who is assigned to OR 1 at Memorial Hospital on September 14, 2026?"
        # Both dispatch and normalization protect against the observed model output.
        grounded = _ground_intent(Intent(kind="assignment", clinician="all"), message, data, None)
        self.assertEqual((grounded.kind, grounded.query_resource, grounded.room), ("schedule", "cases", "OR 1"))
        with patch("backend.chat._extract_intent", new=AsyncMock(return_value=Intent(kind="assignment", clinician="all"))) as model:
            result = await chat_reply(message, data, ADMIN)
        model.assert_not_called()
        self.assertEqual([ref["id"] for ref in result["references"]], ["case1"])
        self.assertIn("Priya Shah", result["references"][0]["label"])
        self.assertNotIn("action", result)
        self.assertNotIn("matching 'all'", result["reply"])

    async def test_screenshot_swap_resolves_both_names_then_asks_second_shift(self):
        data = review_data()
        result = await chat_reply("Can I swap Priya Shahs September 14th shift with Charlotte Lewis? Check both clinicians' eligibility, rest requirements, and approval rules.", data, ADMIN)
        self.assertEqual(result["context"]["intent"]["clinician"], "Priya Shah")
        self.assertEqual(result["context"]["intent"]["other_clinician"], "Charlotte Lewis")
        self.assertEqual(result["context"]["field"], "other_shift_id")
        self.assertEqual([choice["record_id"] for choice in result["clarification"]["choices"]], ["s2"])
        self.assertNotIn("action", result)

    async def test_who_is_on_shift_for_date_works_without_local_model(self):
        with patch("backend.chat._extract_intent", new=AsyncMock(side_effect=httpx.ConnectError("offline"))) as model:
            result = await chat_reply("Who is on shift for September 15, 2026?", sample_data(), ADMIN)
        model.assert_not_called()
        self.assertEqual(result["query"]["resource"], "shifts")
        self.assertEqual({ref["id"] for ref in result["references"]}, {"s1", "s3"})
        self.assertNotIn("action", result)

    async def test_named_clinician_questions_and_possessives(self):
        for message in ("Show Priya Shah's schedule on September 15, 2026", "Show Priya Shahs schedule on September 15, 2026", "When is Priya working on September 15, 2026?", "What shifts does Priya Shah have on September 15, 2026?"):
            with self.subTest(message=message):
                result = await chat_reply(message, sample_data(), ADMIN)
                self.assertIn("Priya Shah", result["reply"])
                self.assertEqual({ref["id"] for ref in result["references"]}, {"s1"} if "shifts" in message else {"s1", "case1"})
                self.assertNotIn("action", result)

    async def test_unknown_clinician_is_not_silently_team_scope(self):
        result = await chat_reply("Show Morgan Lee's schedule on September 15, 2026", sample_data(), ADMIN)
        self.assertIn("could not find", result["reply"])
        self.assertEqual(result["context"]["field"], "clinician")
        self.assertEqual(len(result["clarification"]["choices"]), 3)

    async def test_unknown_full_name_does_not_resolve_to_someone_sharing_surname(self):
        for message in ("Show Morgan Smith's schedule on September 15, 2026", "Show shifts for Morgan Smith on September 15, 2026"):
            result = await chat_reply(message, sample_data(), ADMIN)
            self.assertIn("could not find", result["reply"])
            self.assertNotIn("action", result)
            self.assertEqual(result["references"], [])

    async def test_all_staff_is_team_scope_and_not_a_person(self):
        result = await chat_reply("Show all staff shifts on September 15, 2026", sample_data(), ADMIN)
        self.assertEqual({ref["id"] for ref in result["references"]}, {"s1", "s3"})
        self.assertNotIn("clarification", result)

    async def test_room_number_and_site_filters_are_exact(self):
        data = review_data()
        original = data["cases"][0]
        data["cases"] += [{**original, "id": "case10", "room": "OR 10"}, {**original, "id": "caseWest", "site_id": "west"}]
        result = await chat_reply("Who is assigned to OR 1 at Memorial Hospital on September 14, 2026?", data, ADMIN)
        self.assertEqual([ref["id"] for ref in result["references"]], ["case1"])
        other = await chat_reply("Who is assigned to OR 1 at West Hospital on September 14, 2026?", data, ADMIN)
        self.assertEqual([ref["id"] for ref in other["references"]], ["caseWest"])

    async def test_gap_query_includes_case_when_every_shift_is_filled(self):
        data = sample_data()
        data["shifts"] = data["shifts"][:2]
        data["cases"][0]["clinician_id"] = None
        result = await chat_reply("What coverage gaps do we have on September 15, 2026?", data, ADMIN)
        self.assertEqual(result["query"]["coverage"], "gaps")
        self.assertEqual([ref["id"] for ref in result["references"]], ["case1"])
        self.assertIn("no assigned clinician", result["references"][0]["reasons"][0])
        self.assertNotIn("case1", result["reply"])
        self.assertNotIn("case1", result["references"][0]["label"])

    async def test_gap_query_groups_multiple_rule_findings_per_record(self):
        data = sample_data()
        data["shifts"] = data["shifts"][:2]
        data["clinicians"][0].update(supervision_required=True, availability={"weekdays": [], "start": "07:00", "end": "19:00"})
        result = await chat_reply("What coverage gaps do we have on September 15, 2026?", data, ADMIN)
        shift_refs = [ref for ref in result["references"] if ref["id"] == "s1"]
        self.assertEqual(len(shift_refs), 1)
        self.assertGreaterEqual(len(shift_refs[0]["reasons"]), 2)
        self.assertTrue(any("supervision" in reason.lower() for reason in shift_refs[0]["reasons"]))
        self.assertNotIn("s2", {ref["id"] for ref in result["references"]})

    async def test_empty_gap_query_does_not_list_healthy_work(self):
        data = sample_data()
        data["shifts"] = data["shifts"][:2]
        result = await chat_reply("What coverage gaps do we have on September 15, 2026?", data, ADMIN)
        self.assertEqual(result["references"], [])
        self.assertEqual(result["query"]["total"], 0)
        self.assertIn("No coverage gaps", result["reply"])

    async def test_record_summaries_are_not_duplicated_or_truncated(self):
        data = sample_data()
        case = data["cases"][0]
        beginning = datetime.fromisoformat(case["start"])
        data["cases"] = [{**case, "id": f"case-{number}", "start": (beginning + timedelta(minutes=5 * number)).isoformat(), "end": (beginning + timedelta(minutes=5 * (number + 1))).isoformat()} for number in range(40)]
        result = await chat_reply("Who is assigned to OR 1 on September 15, 2026?", data, ADMIN)
        self.assertEqual(len(result["references"]), 40)
        self.assertEqual(result["query"]["total"], 40)
        self.assertLess(len(result["reply"]), 300)
        self.assertEqual(len({(ref["resource"], ref["id"]) for ref in result["references"]}), 40)
        self.assertTrue(all(ref["label"] not in result["reply"] and ref["id"] not in ref["label"] for ref in result["references"]))

    async def test_date_overlap_includes_previous_day_call_and_month_boundary(self):
        data = sample_data()
        overnight = data["shifts"][2]
        overnight.update(start="2026-08-31T07:00:00-04:00", end="2026-09-01T07:00:00-04:00")
        result = await chat_reply("Who is on call on September 1, 2026?", data, ADMIN)
        self.assertEqual([ref["id"] for ref in result["references"]], ["s3"])
        after = await chat_reply("Who is on call on September 2, 2026?", data, ADMIN)
        self.assertEqual(after["references"], [])

    async def test_week_of_date_has_seven_days(self):
        result = await chat_reply("Who is on shift the week of September 14, 2026?", sample_data(), ADMIN)
        self.assertEqual((result["query"]["date_from"], result["query"]["date_to"]), ("2026-09-14", "2026-09-20"))

    async def test_clarification_button_and_natural_date_share_validated_swap(self):
        data = review_data()
        before = copy.deepcopy(data)
        initial = await chat_reply("Swap Priya Shah's September 14 shift with Charlotte Lewis", data, ADMIN)
        clarification = initial["clarification"]
        selected = await chat_reply("", data, ADMIN, context=initial["context"], selection={"clarification_id": clarification["id"], "choice_id": "s2"})
        natural = await chat_reply("September 16, 2026", data, ADMIN, context=initial["context"])
        for result in (selected, natural):
            self.assertEqual(result["action"]["payload"]["details"], {"shift_id": "s1", "other_shift_id": "s2", "other_clinician_id": "c2", "accepted_by": []})
            self.assertIn("scheduler approval", result["reply"])
            self.assertIn("both clinicians' acceptance", result["reply"])
            self.assertIsNone(result["context"])
        self.assertEqual(data, before)

    async def test_name_clarification_keeps_date_and_natural_answer(self):
        initial = await chat_reply("Show Smith's shifts on September 16, 2026", sample_data(), ADMIN)
        self.assertEqual(len(initial["clarification"]["choices"]), 2)
        result = await chat_reply("Alex Smith", sample_data(), ADMIN, context=initial["context"])
        self.assertEqual([ref["id"] for ref in result["references"]], ["s2"])
        self.assertEqual(result["query"]["date_from"], "2026-09-16")

    async def test_unrelated_question_clears_pending_filters_and_history(self):
        initial = await chat_reply("Show Smith's shifts on September 16, 2026", sample_data(), ADMIN)
        result = await chat_reply("Who is on shift on September 15, 2026?", sample_data(), ADMIN, initial["history"], initial["context"])
        self.assertEqual({ref["id"] for ref in result["references"]}, {"s1", "s3"})
        self.assertIsNone(result["context"])

    async def test_forged_or_retried_clarification_cannot_make_action(self):
        initial = await chat_reply("Swap my s1 with Alex Smith", sample_data(), STAFF)
        for selection, context in (({"clarification_id": "stale", "choice_id": "s2"}, initial["context"]), ({"clarification_id": initial["clarification"]["id"], "choice_id": "s3"}, initial["context"]), ({"clarification_id": initial["clarification"]["id"], "choice_id": "s2"}, None)):
            result = await chat_reply("", sample_data(), STAFF, context=context, selection=selection)
            self.assertNotIn("action", result)
            self.assertIsNone(result["context"])

    async def test_stale_clarification_blocks_followup_but_allows_new_query(self):
        initial = await chat_reply("Swap my s1 with Alex Smith", sample_data(), STAFF)
        context = {**initial["context"], "base_revision": 1, "stale": True}
        followup = await chat_reply("September 16, 2026", sample_data(), STAFF, context=context)
        self.assertIn("schedule changed", followup["reply"])
        self.assertNotIn("action", followup)
        self.assertIsNone(followup["context"])
        question = await chat_reply("Who is on shift on September 15, 2026?", sample_data(), STAFF, context=context)
        self.assertEqual({ref["id"] for ref in question["references"]}, {"s1", "s3"})
        self.assertIsNone(question["context"])

    async def test_swap_validation_is_shared_and_transfers_linked_cases(self):
        from backend.services import swap_changes
        with patch("backend.services.swap_changes", wraps=swap_changes) as shared:
            result = await chat_reply("Swap my s1 with Alex Smith's s2", sample_data(), STAFF)
        self.assertIn("action", result)
        shared.assert_called_once()
        changes = swap_changes(*shared.call_args.args)
        self.assertIn({"resource": "cases", "id": "case1", "patch": {"clinician_id": "c2"}}, changes)

    async def test_fallback_all_person_on_mutation_cannot_change_own_preferences(self):
        result = await self.ask({"kind": "preferences", "clinician": "all", "preferred_shift_hours": [12]}, message="Change everyone's preferred shifts to 12 hours")
        self.assertNotIn("action", result)
        self.assertIn("one person", result["reply"])

    async def test_case_replacement_query_requires_existing_staffing(self):
        data = sample_data()
        data["cases"][0]["clinician_id"] = None
        from backend.repair import coverage_recommendations
        with patch("backend.repair.coverage_recommendations", wraps=coverage_recommendations) as shared:
            result = await chat_reply("Who could cover OR 1 at Main Hospital on September 15, 2026?", data, ADMIN)
        shared.assert_called_once_with(data, "cases", "case1")
        self.assertEqual({ref["id"] for ref in result["references"]}, {"case1", "c1"})
        self.assertNotIn("action", result)

    async def test_swap_checks_scheduling_rules_before_preview(self):
        for problem in ("leave", "locked", "qualification", "rest"):
            data = sample_data()
            if problem == "leave":
                data["requests"].append({"id": "leave", "kind": "leave", "status": "approved", "clinician_id": "c2", "start": "2026-09-15T00:00:00-04:00", "end": "2026-09-16T00:00:00-04:00"})
            elif problem == "locked":
                data["shifts"][0]["locked"] = True
            elif problem == "qualification":
                data["clinicians"][1]["role"] = "MD"
                data["shifts"][1]["role"] = "MD"
            else:
                data["shifts"].append({**data["shifts"][0], "id": "rest-check", "start": "2026-09-15T20:00:00-04:00", "end": "2026-09-15T23:00:00-04:00", "clinician_id": "c2"})
            with self.subTest(problem=problem):
                result = await chat_reply("Swap my s1 with Alex Smith's s2", data, STAFF)
                self.assertNotIn("action", result)
                self.assertIn("cannot be proposed", result["reply"])

    async def test_swap_for_another_person_requires_manager(self):
        result = await chat_reply("Swap Priya Shah's s1 with Charlotte Lewis's s2", review_data(), {"id": "u2", "role": "clinician", "clinician_id": "c2"})
        self.assertNotIn("action", result)
        self.assertIn("own account", result["reply"])

    async def test_swap_failure_deduplicates_linked_assignment_messages(self):
        data = sample_data()
        data["requests"].append({"id": "leave", "kind": "leave", "status": "approved", "clinician_id": "c2", "start": "2026-09-15T00:00:00-04:00", "end": "2026-09-16T00:00:00-04:00"})
        result = await chat_reply("Swap my s1 with Alex Smith's s2", data, STAFF)
        self.assertEqual(result["reply"].count("Assignment overlaps approved time off."), 1)
        self.assertEqual([ref["id"] for ref in result["references"]], ["s1", "s2"])
        self.assertNotIn("action", result)


class ValidationTests(unittest.TestCase):
    def test_extra_action_fields_and_invalid_types_rejected(self):
        for payload in ({"kind": "preferences", "sql": "UPDATE clinicians"}, {"kind": "assignment", "role": "Surgeon"}, {"kind": "delete"}):
            with self.assertRaises(ValidationError):
                Intent(**payload)

    def test_date_windows_have_explicit_deterministic_boundaries(self):
        today = date(2026, 9, 12)
        self.assertEqual(_date_window("next week", today), (date(2026, 9, 14), date(2026, 9, 21)))
        self.assertEqual(_date_window("next Monday", today), (date(2026, 9, 14), date(2026, 9, 15)))
        self.assertEqual(_date_window("this month", today), (date(2026, 9, 1), date(2026, 10, 1)))
        self.assertEqual(_date_window("September 15th, 2026", today), (date(2026, 9, 15), date(2026, 9, 16)))
        with self.assertRaises(ValueError):
            _date_window("Monday", today)


if __name__ == "__main__":
    unittest.main()
