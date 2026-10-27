# Flyo Disruption Desk — Milestones 1, 2A, and 2B

A small FastAPI and SQLite portfolio demo for synthetic flight disruption events, case management, itinerary risk screening, deterministic recovery recommendations, human approval, simulated actions, and a deterministic AI copilot fallback. All passengers, bookings, flights, inventory, and availability are fictional. The service does not connect to airlines, payment providers, or messaging services and does not change real reservations.

## Setup

Python 3.9 or newer is required. From this directory:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e '.[dev]'

The app creates its SQLite tables and seeds the synthetic scenario on startup. By default the database is backend/flyo.db (ignored by Git). Set DATABASE_URL in the process environment to use another SQLAlchemy database URL.

Milestone 2B adds only a new recovery_actions table. SQLAlchemy create_all() creates that table in an existing Milestone 1/2A database without deleting existing rows or requiring a database reset. It does not migrate arbitrary future column changes; this project has no migration framework yet.

## Run and inspect

    python -m uvicorn app.main:app --reload

Submit a cancellation and inspect the resulting cases:

    curl -X POST http://127.0.0.1:8000/webhooks/flight-alerts \
      -H 'Content-Type: application/json' \
      -d '{"event_id":"evt-cancel-001","fid":"FL-1001","changes":{"status":"cancel"}}'
    curl http://127.0.0.1:8000/cases
    curl http://127.0.0.1:8000/cases/1

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

The provider-independent CopilotProvider interface currently uses DeterministicCopilot, which needs no API key or external LLM SDK. The endpoint records that it generated a draft; it never sends it. A SEND_MESSAGE action can only be proposed from a saved draft and requires its own explicit approval before the simulator records a delivery outcome.

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

    python -m pytest -v

The suite uses isolated temporary SQLite databases and deterministic synthetic data. It does not call external APIs.

## Limitations

- Only cancellation webhooks are supported. The frontend, live airline/inventory integrations, real payments, and real message delivery are out of scope.
- Itinerary timing is a screening heuristic, not official airline or airport connection validation.
- Inventory, availability, recovery execution, refund review, and message delivery are simulated. A recommendation is not a confirmed or bookable ticket.
- The fallback copilot is deterministic. No real LLM provider is configured; any future provider must keep policy decisions and human approvals separate.
- API approval is not authenticated and uses a caller-supplied display name. It is not suitable for real passenger data or production operations.
- SQLite is scoped to this single-process MVP; production concurrency, authentication, migrations, retention, and monitoring need further design.
