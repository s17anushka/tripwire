"""A tiny fake agent that tries four tool calls, each guarded by Tripwire.

1. Start the server:      uvicorn app.main:app --reload
2. Open the dashboard:    http://localhost:8000/dashboard
3. Run this file:         python examples/demo_agent.py

Step 2 pauses and waits for you to click Approve or Reject on the dashboard.
(With no API keys, Tripwire uses a keyword-based mock instead of real Jev.)
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tripwire_sdk import (  # noqa: E402
    ActionBlocked,
    ActionRejected,
    ApprovalTimeout,
    Tripwire,
    TripwireUnavailable,
)

tw = Tripwire(
    os.getenv("TRIPWIRE_URL", "http://localhost:8000"),
    agent_id="demo-agent",
    approval_timeout=float(os.getenv("APPROVAL_TIMEOUT", "120")),
    poll_interval=1.0,
)


# --- Fake tools. The @tw.guard line is the only Tripwire-specific part. ---

@tw.guard
def read_file(path: str) -> str:
    print(f"      (running) reading {path}")
    return "sales: 1,204 units"


@tw.guard
def delete_file(path: str) -> None:
    print(f"      (running) deleting {path}")


@tw.guard
def send_email(to: str, subject: str, body: str) -> None:
    print(f"      (running) emailing {to}")


@tw.guard
def run_sql(query: str) -> None:
    print(f"      (running) SQL: {query}")


STEPS = [
    ("read the sales report", read_file, {"path": "sales_report.csv"}),
    ("delete an old temp log", delete_file, {"path": "/tmp/old.log"}),
    ("email the report to a stranger", send_email,
     {"to": "someone@example.com", "subject": "Report", "body": "..."}),
    ("drop the customers table", run_sql, {"query": "DROP TABLE customers"}),
]


def main() -> None:
    goal = "Delete the old temp logs, then summarise the sales report"
    print(f"Goal: {goal}\n")
    with tw.goal(goal):
        for label, fn, kwargs in STEPS:
            print(f"-> Agent wants to: {label}")
            try:
                fn(**kwargs)
                print("   RAN\n")
            except ActionBlocked as exc:
                print(f"   BLOCKED  - {exc.reason}\n")
            except ActionRejected:
                print("   REJECTED by a human\n")
            except ApprovalTimeout as exc:
                print(f"   TIMED OUT waiting for a human ({exc.waited:.0f}s), not run\n")
            except TripwireUnavailable as exc:
                print(f"   Tripwire is not reachable, not run: {exc}\n")
                return


if __name__ == "__main__":
    main()