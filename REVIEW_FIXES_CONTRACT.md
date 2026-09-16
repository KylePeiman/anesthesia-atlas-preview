# Review fixes implementation contract

Work only in /Users/kylepeiman/Documents/ChatGPT/Scheduler. The original installation remains running until cutover. Multiple agents are editing disjoint modules; do not revert others' work.

## Ownership

- Root: backend/main.py, backend/services.py, backend/schemas.py, backend/db.py, migrations, tests/test_api.py, installation/docs.
- Repair worker: backend/solver.py, new backend/repair.py, tests/test_solver.py, new tests/test_repair.py.
- Chat worker: backend/chat.py, tests/test_chat.py.
- Frontend worker: frontend/ only.

## Pure repair interface

backend/repair.py exports:
- simulate_leave(data, request) -> hypothetical data: approve the specified pending leave, cancel any replaced approved leave, clear overlapping assignments and release only their locks. No DB mutation or permissions side effects.
- solve_repair(data, request=None, target=None, time_limit=60) -> result. target is {resource:'shifts'|'cases',id}. Repair staffing within local calendar weeks intersecting leave/target and crossing shifts, preserving outside work. Then repair affected/linked cases. Return status (OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN/MODEL_INVALID), changes relative to original state, preview:{shifts,cases}, gaps, new_issues, existing_issues, released_locks:[{resource,id}], metrics, explanations. Do not include private requests in preview. Full validation before proposing. No silent relaxation or unlocking of unaffected records. Bound all stages together.
- coverage_recommendations(data, resource, identifier) -> {resource,id,candidates:[{clinician_id,name,role,reason,changes}],reason,requires_staffing_repair,missing_coverage,issues,existing_issues}. Cases require matching actual staffed shifts. Rank safe candidates, explain reason; never double-count clinicians. Root supplies hypothetical leave data when previewing.

## API additions (root)

- POST /api/requests/{id}/repair-preview {revision} -> persisted Job, stage:'repair', context:{kind:'leave_repair',request_id}. Pending saved leave only, owner or manager; approved leave may generate repair with unchanged approval (context kind still identifies leave, UI applies through dedicated request action).
- POST /api/coverage/repair-preview {resource,id,revision} -> Job, context:{kind:'coverage_repair',target:{resource,id}}; manager only.
- GET /api/jobs/{id} -> Job including context, result, proposal_id, base_revision. Owners may read own leave preview jobs; managers read all. Snapshot never returned.
- GET /api/coverage/candidates?resource=cases&id=...&revision=N&preview_job_id=... -> recommendations + base_revision. preview_job_id optional and permission/revision checked. Recommendations from completed preview include approved leave in hypothetical data. No mutation.
- POST /api/requests/{id}/action adds repair_proposal_id?:string. Pending leave action:'approve' with proposal atomically approves and applies it after revision/fingerprint/group-policy validation. Approved leave action:'apply_repair' applies a request-linked repair without reapproving. Leave-dependent proposals cannot use generic proposal apply. Uncovered result requires existing shortage acknowledgement/policy. Existing leave-only path remains available.
- GET /api/state gains job context; clinicians get only authorized own leave repair jobs, proposals retain normal filtering plus authorized leave-linked previews. Jobs/proposals carry metadata linking request and its fingerprint without modifying schedule revision for preview creation.

## Frontend preview/navigation

Result.preview contains hypothetical shifts/cases, to combine with current clinician/site/settings records. Every preview panel/record must be clearly labeled hypothetical. Current versus proposed changes use original state + patches, show room/location/date/name and released locks. No generic Apply for leave_repair proposals; use request action with repair_proposal_id. Stale jobs/proposals must regenerate. Shared Issues component opens precise record and fetches recommendations for visible cases/shifts; candidate changes go through ordinary proposal preview in live context. Preview-context recommendation cards are read-only; apply the combined repair proposal, not isolated hypothetical changes.

## Chat extension

chat_reply(message,data,user,history=None,context=None,selection=None) -> {reply,references,query?,clarification?,action?,history?,context?}. Root persists context separately and removes history/context from API response. Existing callers without new optional arguments must work. Root sets base_revision on the response and stored pending clarification context.
- references unique typed {resource:'shifts'|'cases'|'clinicians'|'requests',id,label}, optional display fields/reasons are allowed. Readable labels without internal IDs. reply is a short count/date summary, not duplicated record lines. Return all filtered reference records; frontend displays incremental pages.
- clarification:{id,prompt,choices:[{id,label,resource?,record_id?}]}. Add optional selection:{clarification_id,choice_id} to ChatInput; message may be empty only with a selection. Root checks stored clarification context's revision; chat validates choices. Natural followups also use the pending clarification context, and a new unrelated question clears it. Keep no unsafe implicit action on selection.
- query includes resource ('shifts'|'cases'|'both'), coverage ('all'|'gaps'), date_from,date_to,total. Common date/name/room questions are deterministically grounded read-only before Ollama routing. Correct model sentinels ('all') and wrong action classifications; no name hallucination. Gaps use canonical solver findings including cases and supervision. Swaps use shared swap validation before actionable preview; ambiguity asks for the exact second shift.

## Runtime commands

Python .venv/bin/python (new environment in this folder). Node /Users/kylepeiman/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node; pnpm /Users/kylepeiman/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm, with bundled node bin on PATH. Root installs dependencies. Do not touch original live database, start another server, or copy original runtime PID files.
