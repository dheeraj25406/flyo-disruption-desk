# Flyo Disruption Desk

Flyo Disruption Desk is a portfolio demo of an operations workflow for synthetic flight disruption events. It combines a FastAPI and SQLite backend with a React, TypeScript, Vite, and Tailwind dashboard.

All passenger records, flights, inventory options, availability, approvals, and downstream outcomes are synthetic. The app does not connect to airlines, payment providers, or messaging services. No real reservation, refund, or passenger message is created or changed.

## Requirements

- Python 3.9 or newer
- Node.js 20.19 or newer (the current environment uses Node 25)
- npm

The deterministic recovery engine works without an API key. A Gemini Developer API key is optional and enables Gemini-powered Copilot and Recovery Agent requests; without it, those workflows use clearly identified deterministic fallbacks.

## Start the backend

From the repository root:

    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    python -m uvicorn app.main:app --reload

The backend creates its SQLite tables and synthetic seed data on startup. Its default database is backend/flyo.db. To use another SQLAlchemy URL, set DATABASE_URL in the environment before starting the server.

On startup, the backend adds 25 namespaced synthetic Indian disruption cases, with fictional passenger/booking data and `DEMO-` flight identifiers. Seeding is repeatable and adds missing demo records without resetting existing database rows. The dashboard loads all cases from the backend API and searches passenger name, flight ID, PNR, case ID, and itinerary airport codes.

### Configure Gemini (optional)

The backend reads `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, and `GEMINI_MAX_OUTPUT_TOKENS` from the process environment. It does not load `.env` files. `.env.example` is a placeholder/reference only; supplying values to the shell that starts Uvicorn is required. For example, from the repository root in a POSIX shell:

    export GEMINI_API_KEY='replace-with-your-gemini-api-key'
    export GEMINI_MODEL='gemini-3.5-flash-lite'
    export GEMINI_TIMEOUT_SECONDS='15'
    export GEMINI_MAX_OUTPUT_TOKENS='1200'
    cd backend
    python -m uvicorn app.main:app --reload

The key is optional; omit it to use deterministic Copilot/Agent fallbacks. The Gemini client is created lazily on the first applicable request. The model defaults to and must equal `gemini-3.5-flash-lite`; the default timeout is 15 seconds (accepted range 1–120), and the default output-token limit is 1200 (accepted range 128–8192). Provider errors and invalid model output are reported as fallbacks; there is no alternate-model fallback. Live Gemini behavior has not been verified as part of this project audit.

## Start the frontend

In a second terminal, from the repository root:

    cd frontend
    npm install
    npm run dev

Open the Vite URL shown in the terminal (normally http://localhost:5173). The development server proxies /api requests to http://127.0.0.1:8000. For a separately hosted API, set VITE_API_BASE_URL to its base URL; the default is /api.

## Try the workflow

1. Use **Trigger demo event** to submit a synthetic cancellation for DEMO-AI-2101 (or another seeded DEMO flight). This invokes the existing cancellation webhook and creates a new event-linked case.
2. Open a case and select **Evaluate case** to persist a deterministic risk assessment and recovery recommendation.
3. Inspect connection findings, policy version, reason codes, and simulated alternatives.
4. Create an eligible action proposal. It remains pending until you select **Approve** and confirm, or reject it and confirm.
5. Inspect the result and case activity timeline. The simulator reports its outcome as synthetic and never claims real-world completion.
6. Use the case copilot for structured analysis and case-scoped questions. Each response shows whether Gemini or the deterministic fallback handled it and includes evidence references.
7. Generate a passenger-message draft, edit it locally if needed, and note the **Draft — not sent** label. Drafting alone does not create or execute an action.

### Copilot architecture and boundaries

- Deterministic itinerary risk checks and the versioned recovery policy are the recovery engine: they determine eligibility, connection feasibility, and alternative ranking from persisted synthetic data. They work independently of Gemini.
- Gemini-powered Copilot can explain verified case facts and recovery results, and answer case-scoped questions. The bounded Gemini Recovery Agent can request only allowlisted tools; the backend validates tool calls and remains authoritative for eligibility and actions. With no key, these workflows use deterministic fallbacks.
- Gemini uses Google's official `google-genai` Python SDK (`google-genai==1.47.0`, compatible with Python 3.9) and the exact model ID `gemini-3.5-flash-lite`. No other model is used as a fallback.
- Gemini receives a minimal case context through structured-output requests. Responses are validated with Pydantic and evidence references are checked against the exact context supplied. Invalid output, unsupported draft claims, missing keys, timeouts, quota errors, and provider errors use deterministic responses with explicit metadata.
- Passenger names are sent only for message drafting; draft context excludes itinerary risk details, candidate scores, and internal policy reason codes. Copilot prompts and questions are not stored in audit records. Audit records store operation and provider metadata.
- Analysis, Q&A, and draft endpoints have no action tools. The bounded agent can request only an eligible simulated rebooking proposal when the request explicitly sets `allow_proposal` to true. Proposals still use the existing separate human approval workflow; no Copilot endpoint approves or executes. Message drafts are labeled unsent and are not delivered.
- Without `GEMINI_API_KEY`, fallback responses provide simpler case-grounded summaries and conservative answers. Fallback output is never labeled as a Gemini response.

### API endpoints

The current backend routes are:

- `GET /health`
- `POST /webhooks/flight-alerts`
- `GET /cases` and `GET /cases/{case_id}`
- `POST /cases/{case_id}/evaluate`
- `POST /cases/{case_id}/copilot/analyze`
- `POST /cases/{case_id}/copilot/ask` (JSON body: `{"question":"..."}`)
- `POST /cases/{case_id}/agent/run` (JSON body includes `objective` and optional `allow_proposal`)
- `POST /cases/{case_id}/actions`
- `GET /actions/{action_id}`
- `POST /actions/{action_id}/approve`
- `POST /actions/{action_id}/reject`
- `POST /cases/{case_id}/draft-message`

There is no dedicated Copilot comparison endpoint. The Recovery Agent's internal allowlisted tools may inspect and compare eligible alternatives from the persisted deterministic decision; the model cannot create candidate availability or change eligibility.

### Bounded recovery agent

`POST /cases/{case_id}/agent/run` accepts `{"objective":"Review options","allow_proposal":false}`. The backend may use Gemini function calling with a fixed allowlist: search persisted synthetic alternatives, check persisted connection assessments, compare eligible alternatives in deterministic policy rank order, and (only when `allow_proposal` is explicitly true) propose a simulated rebooking through the existing action service. The agent is limited to four tool calls and four model turns; tool arguments are validated and SDK automatic function execution is disabled. An evaluation is persisted first if the case has none.

The agent cannot approve, reject, or execute actions. A proposal remains `PENDING_APPROVAL` and requires the existing separate human approval endpoint. Tool results are generated from the stored policy decision and synthetic inventory, not model-supplied flight facts. With no Gemini key or on provider/validation failure, a deterministic summary and read-only tool results are returned; fallback mode never creates proposals.

### Testing

Unit tests use mocked Gemini clients and do not make live provider requests. Live Gemini behavior has not been verified. See the complete test commands under [Verification](#verification).

The demo event control creates cases only when explicitly triggered. Refreshing the page only reads existing data; it does not seed or create more cases. Intentionally triggering another event with a new event ID creates another synthetic event and a new case set for matching bookings.

## Verification

Frontend production build:

    cd frontend
    npm run build

Backend tests (temporary SQLite databases):

    cd backend
    python3 -m pytest -v

Current local results for this worktree: **76 backend tests passed** and **10 frontend tests passed**. The test count may change as tests are added. Unit tests use isolated SQLite databases and mocked Gemini clients; they do not make external API requests.

Frontend lint:

    cd frontend
    npm run lint

Frontend tests:

    cd frontend
    npm test

Frontend production build:

    cd frontend
    npm run build

Deterministic reliability evaluation:

    cd backend
    python3 evaluations/run_scenarios.py

The runner creates an isolated temporary SQLite database for every scenario, executes the scenario suite with pytest, and prints totals, pass rate, category results, and expected/actual details for failures. It exits nonzero if a scenario fails. The suite currently contains 32 scenarios:

- **Webhook handling (8):** cancellation fan-out, unrelated bookings, replay and repeated identifiers, unknown flights, unsupported statuses, partial updates, and ordering metadata.
- **Itinerary risk (6):** safe, short, impossible, missing-schedule, invalid-timezone, and mismatched-route connections.
- **Recovery policy (4):** eligible simulated alternatives, route rejection, passenger arrival deadlines, and persisted re-evaluation evidence.
- **Action lifecycle and safety (12):** evaluation-only behavior, pending proposals, simulated approval, repeated approval, rejection, idempotency, decision/candidate evidence, simulated failure, and unsent drafts.
- **Audit trail (2):** distinct proposal/approval/execution/result records and rejected state-transition records.

Measured evaluation result for the current implementation: **32/32 passed (100.0%)**; 8/8 webhook, 6/6 itinerary risk, 4/4 recovery policy, 12/12 action lifecycle, and 2/2 audit scenarios passed. This is a test-suite pass rate, not a production reliability or real-world accuracy measure.

## Limitations

- This is a local interview/portfolio demo with synthetic data, not an airline operations system.
- Connection checks are deterministic timing screens, not official airport or carrier validation.
- Inventory, availability, rebooking, refund review, and message delivery are simulated. Candidate flights are not confirmed or bookable.
- Human approval is an explicit API request but is not authenticated or tied to a real user identity.
- Recovery decisions come from versioned deterministic policy rules and synthetic inventory. Gemini explanations and drafts are advisory text only; they cannot change the policy decision or send messages.
- The webhook contract currently supports cancellation status only. It has no event timestamp/version or partial-update semantics, so out-of-order updates are not processed; unexpected ordering metadata and partial update payloads are rejected by request validation. Reusing an event ID returns the original processed result and does not compare the new payload with the stored payload.
- SQLite and its local write lock are appropriate for this single-process demo, not distributed production concurrency.
- Database uniqueness constraints and transaction guards protect local replay/idempotency behavior. The app does not claim distributed exactly-once execution, and its simulated actions do not call external downstream systems.
- API authentication, real integrations, production schema migrations, frontend hosting, and monitoring are out of scope.
- Gemini free-tier availability and quotas depend on the Google project and can change. Review Google's [rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits); quota exhaustion and outages use the deterministic fallback. No paid-model or alternate-model fallback is configured.
- This is not a production security boundary: authentication is absent, and Gemini processes synthetic case fields needed for each request. Do not put real passenger data in this demo.
