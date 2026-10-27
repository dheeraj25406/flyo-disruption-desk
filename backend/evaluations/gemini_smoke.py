"""Optional live Gemini smoke test. Never required by the unit test suite."""

import os
import sys
import tempfile

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.copilot import REQUIRED_MODEL


def main():
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("Skipped: GEMINI_API_KEY is not configured in this process.")
        return 2
    with tempfile.TemporaryDirectory(prefix="flyo-gemini-smoke-") as folder:
        app = create_app("sqlite:///{}".format(os.path.join(folder, "smoke.db")))
        with TestClient(app) as client:
            event = client.post("/webhooks/flight-alerts", json={
                "event_id": "evt-live-gemini-smoke", "fid": "FL-1001", "changes": {"status": "cancel"}
            })
            if event.status_code != 200:
                print("Smoke test failed while preparing synthetic data (HTTP {}).".format(event.status_code))
                return 1
            cases = client.get("/cases").json()
            case_id = next(item["id"] for item in cases if item["booking_ref"] == "BK-101")
            evaluated = client.post("/cases/{}/evaluate".format(case_id))
            if evaluated.status_code != 200:
                print("Smoke test failed while evaluating the synthetic case.")
                return 1
            response = client.post("/cases/{}/copilot/analyze".format(case_id))
            if response.status_code != 200:
                print("Gemini smoke request failed (HTTP {}).".format(response.status_code))
                return 1
            metadata = response.json().get("metadata", {})
            if metadata.get("provider") != "gemini" or metadata.get("model") != REQUIRED_MODEL or metadata.get("fallback_used"):
                print("No live Gemini response was verified; the request used fallback or an unexpected model.")
                return 1
    print("Gemini live smoke test passed for the configured required model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
