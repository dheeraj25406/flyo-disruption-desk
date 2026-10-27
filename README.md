# Flyo Disruption Desk

Flyo Disruption Desk is a portfolio demo of an operations workflow for synthetic flight disruption events. It combines a FastAPI and SQLite backend with a React, TypeScript, Vite, and Tailwind dashboard.

All passenger records, flights, inventory options, availability, approvals, and downstream outcomes are synthetic. The app does not connect to airlines, payment providers, or messaging services. No real reservation, refund, or passenger message is created or changed.

## Requirements

- Python 3.9 or newer
- Node.js 20.19 or newer (the current environment uses Node 25)
- npm

The backend works without an API key using its deterministic copilot fallback. A Gemini Developer API key is optional.

## Start the backend

From the repository root:

    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e '.[dev]'
    python -m uvicorn app.main:app --reload

The backend creates its SQLite tables and synthetic seed data on startup. Its default database is backend/flyo.db. To use another SQLAlchemy URL, set DATABASE_URL in the environment before starting the server.

### Configure Gemini

1. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Copy `.env.example` to `.env` only if you do not already have a `.env`; otherwise edit your existing file without replacing it.
3. Set `GEMINI_API_KEY` in `.env`. Keep the model exactly `gemini-3.5-flash-lite`; the backend rejects a different configured model instead of switching silently.
4. Export values in the terminal used to start the backend:

       set -a
       source ../.env
       set +a

The API key stays in the backend environment and is never sent to the frontend. The Gemini client is created on the first copilot request, not at startup. If the key is missing, invalid, quota-limited, or the provider is unavailable, responses identify the deterministic fallback and never switch to another model.

Defaults are a 15 second request timeout and 1200 output tokens. `GEMINI_TIMEOUT_SECONDS` accepts 1–120 and `GEMINI_MAX_OUTPUT_TOKENS` accepts 128–8192.

## Start the frontend

In a second terminal, from the repository root:

    cd frontend
    npm install
    npm run dev

Open the Vite URL shown in the terminal (normally http://localhost:5173). The development server proxies /api requests to http://127.0.0.1:8000. For a separately hosted API, set VITE_API_BASE_URL to its base URL; the default is /api.

## Try the workflow

1. Use **Trigger demo event** to submit a synthetic cancellation for FL-1001. This invokes the existing cancellation webhook and creates cases for matching seeded bookings.
2. Open a case and select **Evaluate case** to persist a deterministic risk assessment and recovery recommendation.
3. Inspect connection findings, policy version, reason codes, and simulated alternatives.
4. Create an eligible action proposal. It remains pending until you select **Approve** and confirm, or reject it and confirm.
5. Inspect the result and case activity timeline. The simulator reports its outcome as synthetic and never claims real-world completion.
6. Use the case copilot for structured analysis, comparison of policy-eligible simulated options, and case-scoped questions. Each response shows whether Gemini or the deterministic fallback handled it and includes evidence references.
7. Generate a passenger-message draft, edit it locally if needed, and note the **Draft — not sent** label. Drafting alone does not create or execute an action.

### Copilot architecture and boundaries

- Deterministic itinerary risk checks and the versioned recovery policy remain authoritative for eligibility and ranking. Gemini can explain stored evidence but cannot change a decision, add inventory, set prices or availability, or invoke actions.
- Gemini uses Google's official `google-genai` Python SDK (`google-genai==1.47.0`, compatible with Python 3.9) and the exact model ID `gemini-3.5-flash-lite`. No other model is used as a fallback.
- Gemini receives a minimal case context through structured-output requests. Responses are validated with Pydantic and evidence references are checked against the exact context supplied. Invalid output, unsupported draft claims, missing keys, timeouts, quota errors, and provider errors use deterministic responses with explicit metadata.
- Passenger names are sent only for message drafting; draft context excludes itinerary risk details, candidate scores, and internal policy reason codes. Copilot prompts and questions are not stored in audit records. Audit records store operation and provider metadata.
- Analysis, comparison, Q&A, and draft endpoints have no action-execution tools. Proposals still use the existing explicit human approval workflow. Message drafts are labeled unsent and are not delivered.
- Without `GEMINI_API_KEY`, fallback responses provide simpler case-grounded summaries and conservative answers. Fallback output is never labeled as a Gemini response.

Copilot API routes are `POST /cases/{case_id}/copilot/analyze`, `POST /cases/{case_id}/copilot/compare` (JSON body `{ "alternative_ids": ["SIM-001"] }`), `POST /cases/{case_id}/copilot/ask` (JSON body `{ "question": "..." }`), and the existing `POST /cases/{case_id}/draft-message`. Each response includes provider, model when applicable, fallback status, and validation status. Compare requests accept only inventory IDs in the case's latest persisted eligible decision.

### Testing Gemini

Normal unit tests use mocked Gemini clients and never call the external API:

    cd backend
    python3 -m pytest -v

To make one optional live request using a temporary synthetic case, first export a valid `GEMINI_API_KEY`, then run:

    cd backend
    python3 evaluations/gemini_smoke.py

The smoke test verifies response metadata reports the required Gemini model. It exits with a skip status when the key is absent and is not part of pytest.

The demo event control creates cases only when explicitly triggered. Refreshing the page only reads existing data; it does not seed or create more cases. Intentionally triggering another event with a new event ID creates another synthetic event and a new case set for matching bookings.

## Verification

Frontend production build:

    cd frontend
    npm run build

Backend tests (temporary SQLite databases):

    cd backend
    python3 -m pytest -v

The suite currently has 54 passing unit tests and uses mocked Gemini clients; it does not make external API requests.

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
