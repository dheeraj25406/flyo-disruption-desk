"""Run the deterministic scenario suite and print a stable category summary."""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


CATEGORIES = {
    "webhook": "Webhook handling",
    "risk": "Itinerary risk",
    "policy": "Recovery policy",
    "action": "Action lifecycle and safety",
    "audit": "Audit trail",
}


def scenario_category(test_name):
    prefix = test_name.removeprefix("test_").split("__", 1)[0]
    return CATEGORIES.get(prefix, "Other")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_file = os.path.join(root, "evaluations", "test_scenarios.py")
    with tempfile.TemporaryDirectory(prefix="flyo-evaluation-") as temp_dir:
        report_path = os.path.join(temp_dir, "scenarios.xml")
        command = [
            sys.executable, "-m", "pytest", "-q", test_file,
            "--junitxml", report_path,
        ]
        process = subprocess.run(command, cwd=root, text=True, capture_output=True)
        if not os.path.exists(report_path):
            sys.stderr.write(process.stdout)
            sys.stderr.write(process.stderr)
            print("Scenario runner could not read pytest's JUnit report.", file=sys.stderr)
            return process.returncode or 2

        suite = ET.parse(report_path).getroot()
        cases = list(suite.iter("testcase"))
        failures = []
        grouped = {}
        for case in cases:
            name = case.attrib.get("name", "unnamed")
            category = scenario_category(name)
            totals = grouped.setdefault(category, [0, 0, 0])
            totals[0] += 1
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            if failure is None:
                totals[1] += 1
            else:
                totals[2] += 1
                detail = failure.attrib.get("message", "") + "\n" + (failure.text or "")
                failures.append((name, detail.strip()))

        total = len(cases)
        passed = total - len(failures)
        rate = (passed / total * 100.0) if total else 0.0
        print("Flyo Disruption Desk deterministic scenario evaluation")
        print("=" * 54)
        print("Scenarios: {}  Passed: {}  Failed: {}  Pass rate: {:.1f}%".format(
            total, passed, len(failures), rate
        ))
        print("\nResults by category:")
        for category in sorted(grouped):
            count, category_passed, category_failed = grouped[category]
            category_rate = category_passed / count * 100.0 if count else 0.0
            print("  {:34s} {:2d} total  {:2d} passed  {:2d} failed  {:5.1f}%".format(
                category, count, category_passed, category_failed, category_rate
            ))

        if failures:
            print("\nFailed scenarios (expected versus actual details):")
            for name, detail in failures:
                print("\n- {}".format(name))
                print("  {}".format(detail.replace("\n", "\n  ")))
        print("\nScenario command: {} -m pytest -q {}".format(sys.executable, test_file))
        return 1 if failures or process.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
