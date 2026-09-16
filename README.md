# Anesthesia Atlas

A local anesthesia staffing application with a FastAPI backend, React interface, OR-Tools optimization, and an Ollama chatbot. Start with fictional data; demonstration policies are editable examples, not verified clinical or legal rules.

## Run on this Mac

The working installation is `/Users/kylepeiman/Documents/ChatGPT/Scheduler`. Open launchers from this folder; the earlier installation is retained as a rollback copy.

Open **Start.command**. The launcher reports `http://localhost:8000` and this Mac's network address. Other devices use the network address, not their own localhost. The Mac must remain awake, connected to the network, and allow inbound connections. **Stop.command** stops only this application's server, leaving Ollama untouched.

The local demo sign-ins are `admin` / `admin` for the administrator view and `user` / `user` for the clinician view (Lucas Rivera). `scheduler` and `clinician` remain available with the randomly generated password in `.runtime/credentials.txt`; the clinician account belongs to Priya Shah. Administrators can create additional accounts; passwords can be changed in account settings.

## Public visual preview

The GitHub Pages workflow builds a separate static preview with fictional, in-browser data. It is intentionally read-only: scheduling, authentication, data imports, optimization, approvals, backups, and local AI remain available only in the local Atlas installation. Set `VITE_STATIC_DEMO=true` when building the static preview; the workflow in `.github/workflows/deploy-pages.yml` does this automatically.

## Daily workflow

1. Review Staff and Rules, then coverage in the staffing calendar. Normal availability and approved leave are separate.
2. Generate staffing. Review the proposal and any open slots, then apply it. Publish requires all staffing coverage and safety checks to pass.
3. Choose a day on the OR board, enter fixed cases, and generate daily assignments. Each case clearly identifies its room and facility. Staff must already have a qualifying shift at the same site.
4. Open a saved leave request and choose **Preview optimized schedule**. Review the proposed replacements and remaining coverage findings before approving it.
5. Open any coverage finding to inspect its exact shift or case and the recommended clinician. Review the proposed change before applying it.
6. Use **Ask Atlas** for schedule questions or action previews. Readable result links open the exact record. Clarification choices identify the intended clinician or shift before a change is proposed.

## Time-off schedule previews

**Preview optimized schedule** creates a hypothetical schedule. It does not approve the request, publish a schedule, or change current assignments. A clinician can inspect their own request's preview; schedulers control approval and application.

The repair first optimizes staffing within local calendar weeks intersecting the absence, including shifts crossing those boundaries. Surrounding assignments stay fixed for rest and workload checks. It then repairs cases affected by the absence or changed staffing; unrelated open cases remain untouched. Case times and rooms do not move.

The preview shows current and proposed clinicians, rooms, facilities, dates, locks that must be released, and remaining gaps. Assignments directly overlapping leave can lose their locks; other locks remain mandatory. A locked case outside a partial-day absence can prevent repair when its supporting shift is lost. Review that lock explicitly rather than assuming the optimizer will remove it.

Use **Approve leave and apply this schedule** to commit approval and the reviewed assignment changes together. For already approved leave, use **Apply this repair**. The application checks permissions, request details, and the original schedule revision again when saving. Any intervening edit requires a regenerated preview. A leave-dependent proposal cannot be applied separately through ordinary schedule review.

Shortage policies are checked against the proposed result. Remaining problems in the repair's scope require acknowledgement when the group permits approval despite shortages; otherwise approval is blocked. Unrelated pre-existing gaps are shown separately. **Approve leave without repair** remains available under the same group's leave policies when a repair cannot be completed.

Repair stages share a 60-second budget. A complete or partial valid proposal is distinct from proven infeasibility, timeout before a valid proposal, or failure. If daily optimization times out after a safe staffing repair, the validated partial proposal identifies its remaining daily gaps. Open coverage remains visible until resolved.

When following a record link from a preview, the calendar or OR board remains labeled **Hypothetical schedule**. Recommendations use that same hypothetical state, including the approved absence. Use **Back to preview** to continue review or **Show current schedule** to leave the preview. Individual hypothetical recommendations cannot be applied separately from the combined repair.

## Coverage checks and recommendations

Coverage findings link to the exact record, select its date and facility, and open its details. Case recommendations first search clinicians already assigned to a matching staffed shift for the entire case. Candidates must also pass qualification, approved leave, competing-case, supervision, and workload checks. Candidates are ranked with an explanation and the precise changes needed.

If no currently scheduled clinician qualifies, **Preview staffing repair** considers broader assignment changes while preserving hard rules and locks. If no staffing slot covers the case's required time, location, role, and specialty, add the required coverage first. Staffing repair changes personnel assignments; it does not create a new staffing requirement or move a surgical case.

## Policies and limits

- Supports roles MD, CRNA, CAA, and PA with explicit specialties and site privileges. Job titles do not confer eligibility by themselves.
- Demo CRNAs/CAAs require same-site MD coverage. The ratio and rest/hour limits are demonstration settings. Backup call reserves a clinician but does not supply supervision; the initial version does not model concurrent home-call duty.
- A coverage slot requires one clinician. Cases are fixed-time work inside a matching shift, not additional staffing demand. No patient identifiers are seeded.
- All intervals include an offset. Local day/week boundaries use the configured timezone, including daylight-saving changes.
- Approved leave blocks overlapping assignments. Pending leave is a forecast. Changing approved leave dates uses a replacement request; cancelling approved leave as a clinician requests scheduler review.
- Leave previews also screen unassigned coverage using concurrent clinician matching and weekly hour bounds. These are capacity risks, not a proof of schedule infeasibility. Overlapping interval/week estimates must not be added together; run optimization for a complete scheduling result.
- Approval groups can override the default swap and leave-shortage policies. Cross-group swaps satisfy the stricter requirements of both groups. Leave approval always requires a scheduler; disabling scheduler approval enables otherwise-valid self-service swaps.
- Swap consent applies to the exact shift dates, location, role, and specialty reviewed. A changed shift requires a new request and fresh acceptance.
- Optimization preserves locked assignments, favors filling coverage and minimal changes, then balances workload/preferences. A bounded solve may return a valid partial schedule; open slots remain visible and cannot be published as complete coverage.
- The app serves synthetic demo data over local HTTP. Configure HTTPS (`ATLAS_HTTPS=1` behind a trusted HTTPS reverse proxy), validate departmental rules, and replace demo data before operational use with real staff data.
- Repeated local times during the autumn daylight-saving change need an explicit offset via CSV/API import. Editors reject ambiguous new times and nonexistent spring-forward times, while preserving existing exact timestamps if unchanged.

## CSV files

Import supports `clinicians`, `shifts`, and `cases`. Export a resource first to obtain the complete column layout. Lists may be JSON arrays or semicolon-separated values; `availability` is a JSON object. Dates must include timezone offsets. Imports have a preview and reject stale revisions, unknown sites, duplicate IDs, invalid assignments, and changes to published shifts. IDs determine whether records are added or updated. A blank `clinician_id` leaves a coverage slot unassigned.

## Local AI

Ollama must be running at `http://127.0.0.1:11434` with `qwen3.5:4b` installed. The backend calls only that loopback service. The interface uses locally built assets and has no external font or script dependencies. No cloud fallback, external search, arbitrary SQL, or model-generated executable code is exposed. Chat actions are validated and previewed; it cannot directly apply an assignment.

Common date, room, and clinician questions resolve against current records before model action routing. “All clinicians” means the team; it is not treated as a name. A room question searches daily cases, a shift question searches staffing, and a coverage-gap question returns only uncovered or affected records, including daily cases and supervision findings. Answers show one short summary and readable record links, with additional results available in the panel.

Try these against the fictional September 2026 data:

- “Who is on shift on September 14, 2026?”
- “Who is assigned to OR 1 at Memorial Hospital on September 14, 2026?”
- “Show Priya Shah's shifts for September 2026.”
- “What coverage gaps do we have?”
- “Can I swap Priya Shahs September 14th shift with Charlotte Lewis? Check eligibility, rest requirements, and approval rules.”

For an ambiguous swap, select or name the exact other shift when asked. Both shifts must pass the same application checks and approval rules used by the calendar. Clarification choices retain the pending question; changed schedule revisions require a fresh question or preview. Broader language requests still use Ollama and may take tens of seconds. Calendar functions remain available if Ollama is unavailable.

## Data, backups, and restore

SQLite data lives in `data/atlas.db`. Browsers access it through FastAPI; do not share the database file over the network. Database changes use Alembic migrations. Backups use SQLite's online backup API, including committed WAL data.

Use **Backup** as an administrator, or run `.venv/bin/python run.py backup`. Backups go into `data/backups/`.

To restore, stop the app, then run `.venv/bin/python run.py restore /absolute/path/to/backup.db`. Restore checks integrity, backs up the existing database, restores atomically through SQLite's backup API, and invalidates existing sessions. Keep backups on another disk for protection against loss of this Mac.

## Relocation and rollback

The previous installation is retained at `/Users/kylepeiman/.codex/.chatgpt-projects/g-p-6aa5d94f3ca08191bf0b4b56bce3c3f2/scheduler`. The new installation uses its own environment, interface build, runtime files, and database. Do not copy a running server's PID file or reuse a virtual environment moved from another path.

For a future relocation, prepare and build the destination application first. Stop the source installation and confirm that it has finished any optimization work before taking the final transfer. From the destination folder, run:

```sh
.venv/bin/python scripts/transfer_installation.py --source /absolute/path/to/source-installation --destination /absolute/path/to/new-installation/data
```

The helper requires an empty database destination and will not overwrite an existing `atlas.db`. It uses SQLite's backup API to include committed WAL data, checks database integrity, and records the revision, state hash, and record counts in `data/transfer-manifest.json`. Accounts, requests, proposals, optimization history, and chat history remain in the transferred database. The source is read-only during transfer.

The helper also preserves the source demo credential file as `data/credentials.txt`, when available. Place a copy in the destination's `.runtime/credentials.txt` with owner-only permissions so the launcher points to the preserved credentials. Do not regenerate passwords for existing accounts. Start the destination, verify its sign-in and schedule, and use its newly reported network address. Only one installation should serve port 8000.

To roll back, stop the new installation and retain a backup of its database before starting **Start.command** from the old folder. The old installation contains the state from before cutover; later edits in the new installation are not automatically present there. Keep that newer backup for recovery and review before resuming scheduling. Do not point the older application at the newer database or overwrite either copy to resolve a port conflict.

## Development and future hosting

Python 3.12, Node 24, and pnpm were used. **Setup.command** installs the locked dependencies and builds the interface; it accepts `ATLAS_PYTHON`, `ATLAS_NODE_DIR`, and `ATLAS_PNPM` overrides. No Docker daemon is required.

Run backend checks with `.venv/bin/python -m pytest tests`. The frontend builds with `pnpm run build` in `frontend/`. `DATABASE_URL`, `ATLAS_DATA_DIR`, and `ATLAS_PORT` configure deployment. Cloud deployment will require HTTPS, durable storage, process supervision, backups, and a host for the same local AI model; the domain services and API can be retained. PostgreSQL requires its driver and deployment validation before switching. EHR, payroll, enterprise sign-in, and automatic public hosting are outside this release.
