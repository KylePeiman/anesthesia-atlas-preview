# Atlas review-fixes verification — September 13, 2026

Working installation: `/Users/kylepeiman/Documents/ChatGPT/Scheduler`.

The release is running from the selected installation on port 8000. The original installation and database remain intact as the pre-cutover rollback copy.

## Automated checks completed

The final combined Python suite passed **177 tests and eight subtests in 18.17 seconds**. Two dependency deprecation warnings remain; there were no failures. This includes 56 solver tests, 25 repair tests, 40 API tests, and 56 chat tests.

Repair tests cover two-week vacation, partial-day and overnight absence, month and week boundaries, crossing shifts, restoration after replaced leave, approved-leave repair, exact lock release, unaffected locks, fixed surrounding rest requirements, and safe partial proposals when eligible coverage is insufficient. An unrelated open case in the same week remains unchanged. Staffing timeout returns no proposed changes; a daily timeout can retain only an independently validated safe staffing result.

Recommendation checks cover matching staffed shifts, role/site/specialty restrictions, overlapping cases, approved leave, supervision, locked records, missing coverage slots, broader repair availability, hypothetical leave contexts, and workload ranking. Ordinary solver scope tests confirm that unselected shifts and cases remain fixed.

API integration checks include request ownership, manager-only application, request-linked preview access, atomic leave approval with schedule application, stale revisions and requests, shortage policies, invalid proposals, preview-specific candidates, and preventing independent application of leave-dependent changes. The existing authentication, import, backup, persistence, and permission regression coverage remains in the suite.

Chat regression coverage includes the reported date, room, named-clinician, swap, and coverage-gap prompts; incorrect model action classifications and `all` sentinels; possessives; room distinction; exact record links; clarification choices and follow-ups; stale clarification context; and shared swap validation. Final live-model behavior is recorded separately below.

All eight frontend tests passed, and the TypeScript check and production build succeeded. The final bundle, `index-KOHwkrmu.js`, was served and checked in the relocated browser application.

## Representative 50-clinician repair

An in-memory fictional dataset used the seeded month, extended to 50 clinicians. All **170 staffing slots** were filled within an eight-second solve budget. A subsequent two-week leave repair was **OPTIMAL in 0.408 seconds**, including both staffing and daily stages.

The repair replaced five staffing assignments and assigned ten linked cases. It had **zero remaining gaps in its repair scope, zero new gaps, and zero hard-rule violations**. The 122 unrelated existing open cases stayed visible and unchanged. No case outside the returned repair scope was modified. This benchmark did not change the saved application database.

## Browser and local AI checks

The browser tool accessed the isolated QA installation on port 8001 at desktop width 1280 and narrow width 390. The earlier release's browser-tool restriction no longer prevented these checks.

The coverage-gap chat response correctly reported 54 uncovered cases and zero uncovered shifts. It showed eight unique readable record cards initially and provided access to more results. Following a link selected the exact date, facility, and case; the details included the room and an eligible recommendation.

A real background leave-preview job produced 27 proposed changes, four new findings, and 106 existing findings on the isolated QA data. Record navigation retained the hypothetical schedule context, and **Back to preview** returned to the same review. At width 390, the dialog scrolled within a document width of 390, without widening the page.

The combined approval workflow was exercised in the QA browser. It required acknowledgement of the four remaining gaps, then approved the leave and applied the reviewed schedule in one commit. The schedule revision advanced exactly once, from 4 to 5. All 27 applied patches exactly matched the reviewed proposal; the request was approved, the proposal was marked applied, and the administrator actor was recorded. Independent validation found zero hard-rule violations afterward.

The installed `qwen3.5:4b` model was available through local Ollama. Actual model requests returned a valid preference preview for 24-hour shifts and OB in **38.284 seconds**, and a valid inclusive September 14–28 leave preview in **24.875 seconds**, identifying nine affected shifts and two cases. Neither preview was applied to the user's schedule.

Raw model-output checks reproduced the original failures: the swap prompt took **24.792 seconds** and returned a clinician value of `all` while missing the names/date; the OR question took **15.125 seconds** and omitted the room, facility, and date. The application's deterministic grounding recovered the explicit records and filters, rather than trusting those incomplete model fields. Common read-only and swap-clarification routes completed in **1–13 milliseconds** without invoking Ollama. These fast routes verify application behavior and are not evidence that the underlying model itself became more accurate.

Final browser checks confirmed that previously empty case assignments read **Unassigned**, overnight comparisons include the ending date (for example, September 10 at 7 AM through September 11 at 7 AM), and room labels appear beside the facility. At narrow width, the header chat button retains the accessible name **Ask Atlas**. The nine-result date lookup displayed eight readable cards and a control for the remaining result. Selecting a shift clarification retained the swap question and rejected an ineligible swap through the shared rules without applying anything. Repeated failure explanations were deduplicated and regression-tested. No browser console warnings or errors were recorded during the QA interactions.

No changes should be applied to the user's schedule while testing read-only chat prompts. Mutation checks use the isolated QA data or disposable test databases.

## Relocation, persistence, and network access

The transfer helper was inspected: it opens the source SQLite database read-only, uses the online backup API to include committed WAL data, checks the destination's integrity, refuses to overwrite an existing destination database, and records its state revision, hash, and record counts. It does not copy virtual environments or runtime process IDs. Preserved demo credentials require placement at the destination launcher's documented `.runtime/credentials.txt` path.

The old server was confirmed stopped with no active optimization jobs before the final transfer at **2026-09-13 18:05 UTC**. The copied state remained at revision **4**, with SHA-256 `7cc37435c20808755f759bb38687c68d0afba9f9900c7c11e6951ee5e55b4312`. It contained 20 clinicians, 170 shifts, 132 cases, and one pending leave request. All original columns in three users, two sessions, three proposals, four jobs, four conversations, and three audit records matched the original database exactly. Schema migration `0002` completed and SQLite integrity checks passed.

The preserved admin, scheduler, and clinician credentials all signed in successfully. A read-only room lookup returned the two expected case links. A new leave preview was generated in the relocated installation and left **pending and unapplied**; it did not change the saved schedule revision. The request owner could access the completed preview and its linked proposal after restart. Testing created only additional sessions, a lookup conversation, and this reviewable preview in the live database; approval tests used the isolated QA copy.

All saved state, users, sessions, proposals, jobs, conversations, and audit rows remained identical across two subsequent restarts. A real backup was restored into a disposable database and matched every saved record; restored sessions were cleared as intended. The original QA server on port 8001 was stopped. Process ownership confirms the remaining Atlas server runs from `/Users/kylepeiman/Documents/ChatGPT/Scheduler`.

The restart check exposed a launcher port probe that mistook recently closed connections for a live listener. The probe now uses `SO_REUSEADDR`, matching Uvicorn; an immediate stop/start sequence succeeded afterward.

Both **http://localhost:8000** and **http://192.168.1.153:8000** returned the application and health endpoint, including after restart. LAN-address access was tested from this Mac. No second device was available for independent-device verification; no such test is claimed. Browser access was available and desktop/narrow verification completed without a browser-tool restriction.

Rollback starts the preserved original installation after stopping the new one. Its database is the pre-cutover state. Back up the new installation first so later edits remain recoverable.

## Local-only operation and prior-release evidence

Interface assets are built locally. Chat transport remains fixed to loopback Ollama with no cloud fallback; normal browser requests use the application origin. No physically disconnected network test has yet been recorded for this revision.

The September 12 release previously passed 109 automated checks, generated the 170-shift September demo and two six-case daily boards, and verified a real restart and local Ollama responses. The checks above verify the updated release separately.
