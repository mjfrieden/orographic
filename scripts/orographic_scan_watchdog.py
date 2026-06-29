from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from typing import Any
from urllib import error, request


DEFAULT_SLOT_HOURS = (14, 17, 20)
DEFAULT_SLOT_MINUTE = 7


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def expected_slot_start(
    now_utc: datetime,
    *,
    slot_hours: tuple[int, ...] = DEFAULT_SLOT_HOURS,
    slot_minute: int = DEFAULT_SLOT_MINUTE,
) -> datetime | None:
    now = now_utc.astimezone(UTC)
    candidates: list[datetime] = []
    for days_back in range(0, 7):
        day = (now - timedelta(days=days_back)).date()
        if day.weekday() >= 5:
            continue
        for hour in slot_hours:
            candidate = datetime(day.year, day.month, day.day, hour, slot_minute, tzinfo=UTC)
            if candidate <= now:
                candidates.append(candidate)
    return max(candidates) if candidates else None


def _run_counts_for_slot(run: dict[str, Any], slot_start: datetime, now_utc: datetime) -> bool:
    created_at = _parse_dt(run.get("created_at"))
    if created_at is None:
        return False
    if created_at < slot_start or created_at > now_utc:
        return False
    head_branch = str(run.get("head_branch") or "")
    if head_branch and head_branch != "main":
        return False
    return True


def decide_watchdog_action(
    *,
    now_utc: datetime,
    runs: list[dict[str, Any]],
    slot_hours: tuple[int, ...] = DEFAULT_SLOT_HOURS,
    slot_minute: int = DEFAULT_SLOT_MINUTE,
    grace_minutes: int = 15,
) -> dict[str, Any]:
    now = now_utc.astimezone(UTC)
    slot_start = expected_slot_start(now, slot_hours=slot_hours, slot_minute=slot_minute)
    if slot_start is None:
        return {
            "should_dispatch": False,
            "reason": "no_expected_weekday_slot",
            "slot_start_utc": None,
            "matching_runs": [],
        }
    slot_age_minutes = (now - slot_start).total_seconds() / 60.0
    matching_runs = [run for run in runs if _run_counts_for_slot(run, slot_start, now)]
    if slot_age_minutes < grace_minutes:
        return {
            "should_dispatch": False,
            "reason": "within_grace_window",
            "slot_start_utc": slot_start.isoformat().replace("+00:00", "Z"),
            "slot_age_minutes": round(slot_age_minutes, 2),
            "matching_runs": matching_runs,
        }
    if matching_runs:
        return {
            "should_dispatch": False,
            "reason": "scan_run_present_for_slot",
            "slot_start_utc": slot_start.isoformat().replace("+00:00", "Z"),
            "slot_age_minutes": round(slot_age_minutes, 2),
            "matching_runs": matching_runs,
        }
    return {
        "should_dispatch": True,
        "reason": "missing_scan_run_for_slot",
        "slot_start_utc": slot_start.isoformat().replace("+00:00", "Z"),
        "slot_age_minutes": round(slot_age_minutes, 2),
        "matching_runs": [],
    }


def _github_request(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "orographic-scan-watchdog",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    with request.urlopen(req) as response:
        body = response.read().decode("utf-8").strip()
    if not body:
        return {}
    return json.loads(body)


def fetch_workflow_runs(*, repo: str, workflow_file: str, branch: str, token: str) -> list[dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
        f"?branch={branch}&per_page=20"
    )
    payload = _github_request("GET", url, token=token)
    workflow_runs = payload.get("workflow_runs")
    return workflow_runs if isinstance(workflow_runs, list) else []


def dispatch_workflow(*, repo: str, workflow_file: str, ref: str, token: str) -> None:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    _github_request("POST", url, token=token, payload={"ref": ref})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch a fallback Orographic Scan run when a scheduled slot is missed.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--workflow-file", default="orographic_scan.yml")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--grace-minutes", type=int, default=15)
    parser.add_argument("--slot-minute", type=int, default=DEFAULT_SLOT_MINUTE)
    parser.add_argument("--slot-hours", default="14,17,20")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = str(args.repo or "").strip()
    if not repo:
        raise SystemExit("GITHUB_REPOSITORY or --repo is required")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")

    slot_hours = tuple(int(part.strip()) for part in str(args.slot_hours).split(",") if part.strip())
    runs = fetch_workflow_runs(repo=repo, workflow_file=args.workflow_file, branch=args.branch, token=token)
    decision = decide_watchdog_action(
        now_utc=datetime.now(UTC),
        runs=runs,
        slot_hours=slot_hours,
        slot_minute=max(int(args.slot_minute), 0),
        grace_minutes=max(int(args.grace_minutes), 1),
    )
    result: dict[str, Any] = {
        "repo": repo,
        "workflow_file": args.workflow_file,
        "branch": args.branch,
        "ref": args.ref,
        "dry_run": bool(args.dry_run),
        **decision,
    }
    if decision["should_dispatch"] and not args.dry_run:
        dispatch_workflow(repo=repo, workflow_file=args.workflow_file, ref=args.ref, token=token)
        result["dispatch_status"] = "triggered"
    else:
        result["dispatch_status"] = "skipped"
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
