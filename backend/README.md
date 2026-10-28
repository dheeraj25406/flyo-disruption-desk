# Flyo Disruption Desk — Backend

A FastAPI and SQLite backend for a synthetic flight-disruption demo. All passengers, bookings, flights, inventory, and availability are fictional. The service does not connect to airlines, payment providers, or messaging services and does not change real reservations.

## Setup

Python 3.9 or newer is required. Runtime dependencies are declared in `pyproject.toml`; the development extra adds pytest and HTTPX. From this directory:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e '.[dev]'

On application startup, the backend creates its SQLite tables and additively seeds 25 namespaced synthetic Indian disruption cases. Repeated startup does not reset existing rows or overwrite user-created records. The default database is `backend/flyo.db` when launched from this directory; it is ignored by Git. Set `DATABASE_URL` in the process environment to use another SQLAlchemy database URL.

The project does not use a general migration framework. Startup creates missing tables and applies the specific SQLite compatibility additions in `app/db.py`; it does not migrate arbitrary future schema changes.

## Run and inspect

    python -m uvicorn app.main:app --reload

Inspect the 25 seeded synthetic Indian disruption cases:

    curl http://127.0.0.1:8000/cases
    curl http://127.0.0.1:8000/cases/1

Submit an additional synthetic cancellation and inspect the new case:

    curl -X POST http://127.0.0.1:8000/webhooks/flight-alerts \
      -H 'Content-Type: application/json' \
      -d '{"event_id":"evt-cancel-001","fid":"DEMO-AI-2101","changes":{"status":"cancel"}}'

Evaluate a case and inspect its recovery decision:

    curl -X POST http://127.0.0.1:8000/cases/1/evaluate

Propose a simulated rebooking action using an inventory_id returned by evaluation:

    curl -X POST http://127.0.0.1:8000/cases/1/actions \
      -H 'Content-Type: application/json' \
      -d '{"action_type":"REBOOK","idempotency_key":"demo-case-1-rebook-1","inventory_id":"SIM-001"}'

The response contains an action ID. Approval requires a separate, explicit request:

    curl -X POST http://127.0.0.1:8000/actions/ACTION_ID/approve \
      -H 'Content-Type: application/json' \
      -d '{"approved":true,"approved_by":"demo-operator"}'
    curl http://127.0.0.1:8000/actions/ACTION_ID

Reject a pending proposal with POST /actions/{action_id}/reject; inspect its audit history in GET /cases/{case_id}. Approval and rejection are local API operations for this demo. Authentication and real multi-user authorization are not implemented.

Generate a case summary and passenger-message draft:

    curl -X POST http://127.0.0.1:8000/cases/1/draft-message

Run the bounded agent workflow (proposing is disabled unless explicitly enabled in this request):

    curl -X POST http://127.0.0.1:8000/cases/1/agent/run \
      -H 'Content-Type: application/json' \
      -d '{"objective":"Review simulated recovery options","allow_proposal":false}'

The deterministic recovery engine applies itinerary risk checks, connection feasibility, and versioned policy rules to persisted synthetic alternatives. Its eligibility and ranking do not depend on Gemini. Copilot analysis and case questions, plus the bounded Recovery Agent, can use Gemini when configured; Gemini-generated outputs are validated and do not replace deterministic policy decisions.

When `GEMINI_API_KEY` is absent, Copilot and Agent return explicitly identified deterministic fallback results. With a key, the backend lazily creates a client using the pinned `google-genai` SDK and configured model. The required model is `gemini-3.5-flash-lite`; the default request timeout is 15 seconds (range 1–120), and the default output limit is 1200 tokens (range 128–8192). Provider failures, invalid structured output, or invalid Agent tool calls are distinguished from validated Gemini results. No alternate model is selected automatically. Live Gemini behavior has not been verified in this audit.

Configuration is read from the backend process environment; the application does not load `.env` files or `--env-file` settings. `.env.example` contains placeholders/reference defaults only and is not loaded automatically. To configure the running process, export variables in the same shell before starting Uvicorn (never put an actual key in source control):

    export GEMINI_API_KEY='replace-with-your-gemini-api-key'
    export GEMINI_MODEL='gemini-3.5-flash-lite'
    export GEMINI_TIMEOUT_SECONDS='15'
    export GEMINI_MAX_OUTPUT_TOKENS='1200'
    python -m uvicorn app.main:app --reload

`GEMINI_API_KEY` is optional. `DATABASE_URL` is also read from the process environment; by default the backend uses SQLite. Keep actual credentials out of logs, API responses, and Git.

Set `CORS_ORIGINS` in the backend process environment to a comma-separated list of exact deployed frontend origins, such as `https://your-app.vercel.app`. Entries are trimmed and empty values ignored. The two local development origins (`http://localhost:5173` and `http://127.0.0.1:5173`) remain allowed. Wildcard origins are rejected.

## API routes

The current endpoints are `GET /health`, `POST /webhooks/flight-alerts`, `GET /cases`, `GET /cases/{case_id}`, `POST /cases/{case_id}/evaluate`, `POST /cases/{case_id}/copilot/analyze`, `POST /cases/{case_id}/copilot/ask`, `POST /cases/{case_id}/agent/run`, `POST /cases/{case_id}/actions`, `GET /actions/{action_id}`, `POST /actions/{action_id}/approve`, `POST /actions/{action_id}/reject`, and `POST /cases/{case_id}/draft-message`. There is no dedicated Copilot comparison endpoint.

The Agent may internally inspect and compare alternatives using its fixed allowlist, but those alternatives and eligibility come from the persisted deterministic policy results.

## Action and decision behavior

- REBOOK proposals require a saved recommend_for_review decision and one of that decision's eligible simulated alternatives.
- REFUND_REVIEW proposals are available for escalated or manual-review decisions. Simulated execution records a review request; it does not refund money.
- SEND_MESSAGE proposals must match a draft saved for the case and decision. Simulated execution records an outcome; no real message is transmitted.
- New actions start as PENDING_APPROVAL. Only explicit approval moves one through APPROVED and EXECUTING to SUCCEEDED or FAILED. Rejected and terminal actions cannot execute again.
- Action IDs and caller-provided idempotency keys are unique in SQLite. Repeating the same proposal with the same key returns the existing action; reusing the key for a different proposal returns a conflict.
- Each case response exposes a chronological audit timeline, including event receipt/processing, evaluation, draft generation, proposal, approval/rejection, and execution result.
- Simulated outcomes explicitly report real_world_completed: false; a simulated refund review never reports money refunded, and a simulated message delivery never reports a real message sent.

SQLite serializes writers, and approval obtains an immediate write transaction before checking and transitioning action state. This prevents duplicate execution within the local SQLite MVP. It is not a distributed lock or a substitute for production queueing/locking across multiple services or database replicas.

## Decision flow

1. Compare timezone-aware itinerary instants in UTC, check airport continuity, and apply the configured minimum connection buffer.
2. Escalate missing schedule/timezone data and risky connections for human review.
3. Filter simulated alternatives against route, schedule, passenger constraints, simulated availability, and connection feasibility; rank eligible candidates with the transparent policy score in app/services/policy.py.
4. Persist decision evidence and policy version. Evaluation alone creates no action.
5. Create an action proposal only through its explicit endpoint. Execution occurs only after an explicit approval request.

## Tests

    python -m pytest -q

The current local suite has 76 passing backend tests. Tests use isolated temporary SQLite databases and mocked Gemini clients; they do not call external APIs. The count can change as the suite evolves.

## Limitations

- Only cancellation webhooks are supported. The frontend, live airline/inventory integrations, real payments, and real message delivery are out of scope.
- Itinerary timing is a screening heuristic, not official airline or airport connection validation.
- Inventory, availability, recovery execution, refund review, and message delivery are simulated. A recommendation is not a confirmed or bookable ticket.
- Gemini is an optional language-model provider for Copilot explanations/questions and bounded Agent tool turns. It does not determine policy eligibility or authorize actions. Unit tests mock the provider; live Gemini behavior has not been verified.
- API approval is not authenticated and uses a caller-supplied display name. It is not suitable for real passenger data or production operations.
- SQLite is scoped to this single-process MVP; production concurrency, authentication, migrations, retention, and monitoring need further design.
