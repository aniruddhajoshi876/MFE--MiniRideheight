"""
run_sil.py -- SIL test automation entry point.

Compiles the REAL firmware sources into a host DLL, runs every test in its
own subprocess (fresh static state per test), and reports:

  PASS     conformance check met
  PASS*    checks passed under one or more explicit SIL accommodations
  FAIL     observed behavior contradicts the expected contract
  OBSERVE  characterization only -- reported, never affects the exit code
  ERROR    the worker crashed (e.g. access violation inside the DLL)
  TIMEOUT  the worker exceeded the watchdog

Exit code is nonzero when any FAIL / ERROR / TIMEOUT occurred.  With
--fail-on-accommodated, PASS* also makes the run nonzero for release CI.

Failure output is symptom + transaction evidence only: observed vs. expected
plus the simulated I2C/CAN/GPIO transactions around the failure. The harness
never names or applies a fix.

Usage:
  python sil/run_sil.py                     run everything
  python sil/run_sil.py --filter can        run tests whose id contains "can"
  python sil/run_sil.py --build-only        just compile the DLL
  python sil/run_sil.py --verbose           all retained trace lines
  python sil/run_sil.py --fail-on-accommodated
  python sil/run_sil.py --json out.json --junit out.xml
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.sax.saxutils as sx

SIL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SIL_DIR)
RESULTS_DIR = os.path.join(SIL_DIR, "build", "results")

BAD = ("FAIL", "ERROR", "TIMEOUT")
TRACE_TAIL = 25


def effective_verdict(result: dict) -> str:
    """Return the schema-v2 classification, with fallback for old workers."""
    if result.get("effective_verdict"):
        return result["effective_verdict"]
    if result.get("verdict") == "PASS" and result.get("accommodations"):
        return "PASS_ACCOMMODATED"
    return result.get("verdict", "ERROR")


def empty_result(test_id: str, verdict: str, error: str,
                 duration_s: float) -> dict:
    return {
        "id": test_id, "title": "", "verdict": verdict,
        "effective_verdict": verdict, "accommodated": None,
        "strict_model": None, "model_mode_known": False,
        "checks": [], "trace": [],
        "accommodations": [],
        "trace_stats": {
            "total_events": 0, "captured_events": 0,
            "omitted_events": 0, "stored_lines": 0,
            "collapsed_runs": 0, "repeated_events_collapsed": 0,
            "truncated": False,
        },
        "stdout": "", "error": error, "duration_s": duration_s,
    }


def collect_test_ids() -> list[str]:
    sys.path.insert(0, SIL_DIR)
    import tests
    return list(tests.TESTS.keys())


def run_one(test_id: str, timeout_s: int) -> dict:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = os.path.join(RESULTS_DIR, test_id.replace(".", "_") + ".json")
    if os.path.isfile(out_json):
        os.remove(out_json)
    cmd = [sys.executable, os.path.join(SIL_DIR, "worker.py"), test_id, out_json]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, timeout=timeout_s,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return empty_result(test_id, "TIMEOUT",
                            f"worker exceeded the {timeout_s}s watchdog",
                            round(time.time() - t0, 2))
    duration = round(time.time() - t0, 2)

    if not os.path.isfile(out_json):
        result = empty_result(
            test_id, "ERROR",
            f"worker exited with code {proc.returncode} and no result file "
            f"(likely a crash inside the DLL).\nstderr:\n{proc.stderr}",
            duration)
        result["stdout"] = proc.stdout
        return result
    try:
        with open(out_json, "r", errors="replace") as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        result = empty_result(test_id, "ERROR",
                              f"unreadable result file: {exc}", duration)
        result["stdout"] = proc.stdout
        return result
    result["duration_s"] = duration
    result.setdefault("model_mode_known",
                      result.get("strict_model") is not None)
    if "accommodated" not in result:
        result["accommodated"] = (bool(result.get("accommodations"))
                                  if result["model_mode_known"] else None)
    if "strict_model" not in result:
        result["strict_model"] = (not result["accommodated"]
                                  if result["model_mode_known"] else None)
    result.setdefault("effective_verdict", effective_verdict(result))
    result.setdefault("trace_stats", empty_result("", "", "", 0)["trace_stats"])
    return result


def print_details(r: dict, verbose: bool) -> None:
    interesting = r["verdict"] in BAD or verbose
    for c in r.get("checks", []):
        mark = {"PASS": " ok ", "FAIL": "FAIL", "OBSERVE": "obs "}.get(c["verdict"], "??? ")
        if c["verdict"] == "FAIL" or verbose or c["verdict"] == "OBSERVE":
            print(f"    [{mark}] {c['name']}")
            if c["observed"]:
                print(f"           observed: {c['observed']}")
            if c["expected"]:
                print(f"           expected: {c['expected']}")
            if c.get("note"):
                print(f"           note:     {c['note']}")
    for a in r.get("accommodations", []):
        print(f"    (i) {a}")
    if r.get("error"):
        print("    worker error:")
        for line in r["error"].splitlines():
            print(f"      {line}")
    if interesting and r.get("trace"):
        stats = r.get("trace_stats", {})
        total = stats.get("total_events", len(r["trace"]))
        captured = stats.get("captured_events", len(r["trace"]))
        omitted = stats.get("omitted_events", max(0, total - captured))
        n = len(r["trace"]) if verbose else min(TRACE_TAIL, len(r["trace"]))
        scope = "all retained" if verbose else "last"
        print(f"    --- transaction trace ({scope} {n} stored line(s); "
              f"{captured}/{total} raw events represented, {omitted} omitted) ---")
        for line in r["trace"][-n:]:
            print(f"      {line}")
    if interesting and r.get("stdout", "").strip():
        print("    --- captured firmware printf output ---")
        for line in r["stdout"].splitlines()[:20]:
            print(f"      {line}")


def _one_line(text: str, limit: int = 200) -> str:
    """Collapse whitespace/newlines to one deterministic short line."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."


def _accommodation_label(text: str) -> str:
    """Short label for a PASS* accommodation sentence."""
    prefix = "SIL accommodation:"
    if text.startswith(prefix):
        text = text[len(prefix):]
    return _one_line(text, 70)


def build_issue_summary(results: list[dict]) -> dict:
    """Classify results once, for console AND JSON, in execution order.

    failures / errors / timeouts carry the failed-check evidence
    (expected vs observed) plus a one-line explanation; accommodated
    lists every PASS* test with its accommodation labels.
    """
    summary: dict[str, list] = {
        "failures": [], "errors": [], "timeouts": [], "accommodated": [],
    }
    for r in results:
        classification = effective_verdict(r)
        failed_checks = [
            {"name": c["name"], "expected": c.get("expected", ""),
             "observed": c.get("observed", ""), "note": c.get("note", "")}
            for c in r.get("checks", []) if c["verdict"] == "FAIL"
        ]
        if classification == "FAIL":
            if failed_checks:
                first = failed_checks[0]
                explanation = _one_line(
                    f"{first['name']} -- observed: {first['observed']}")
            else:
                explanation = _one_line(
                    r.get("error", "") or
                    "test reported FAIL with no failed check recorded")
            summary["failures"].append({
                "id": r["id"], "title": r.get("title", ""),
                "verdict": "FAIL", "explanation": explanation,
                "failed_checks": failed_checks,
            })
        elif classification in ("ERROR", "TIMEOUT"):
            entry = {
                "id": r["id"], "title": r.get("title", ""),
                "verdict": classification,
                "explanation": _one_line(
                    r.get("error", "") or "worker failed with no error text"),
                "worker_error": r.get("error", ""),
                "failed_checks": failed_checks,
            }
            key = "errors" if classification == "ERROR" else "timeouts"
            summary[key].append(entry)
        elif classification == "PASS_ACCOMMODATED":
            summary["accommodated"].append({
                "id": r["id"], "title": r.get("title", ""),
                "accommodations": list(r.get("accommodations", [])),
                "labels": [_accommodation_label(a)
                           for a in r.get("accommodations", [])],
            })
    return summary


def print_issue_summary(summary: dict) -> None:
    issues = summary["failures"] + summary["errors"] + summary["timeouts"]
    if issues:
        print("\n==== Failure summary " + "=" * 41)
        for entry in issues:
            print(f"  {entry['verdict']:7s} {entry['id']}  {entry['title']}")
            print(f"    Issue: {entry['explanation']}")
            for c in entry["failed_checks"]:
                print(f"    - {c['name']}")
                if c["expected"]:
                    print(f"        expected: {c['expected']}")
                if c["observed"]:
                    print(f"        observed: {c['observed']}")
                if c["note"]:
                    print(f"        note:     {c['note']}")
            if entry["verdict"] in ("ERROR", "TIMEOUT") and entry["worker_error"]:
                print(f"    worker error: {_one_line(entry['worker_error'])}")
    if summary["accommodated"]:
        print("\n==== Accommodated passes (PASS*) " + "=" * 29)
        for entry in summary["accommodated"]:
            print(f"  {entry['id']}")
            for label in entry["labels"]:
                print(f"    * {label}")


def write_json(path: str, results: list[dict],
               fail_on_accommodated: bool = False) -> None:
    classifications: dict[str, int] = {}
    for result in results:
        classification = effective_verdict(result)
        classifications[classification] = classifications.get(classification, 0) + 1
    issue_summary = build_issue_summary(results)
    with open(path, "w") as f:
        json.dump({
            "schema_version": 3,
            "policy": {"fail_on_accommodated": fail_on_accommodated},
            "summary": {"classifications": classifications,
                        "total": len(results),
                        "failures": issue_summary["failures"],
                        "errors": issue_summary["errors"],
                        "timeouts": issue_summary["timeouts"],
                        "accommodated": issue_summary["accommodated"]},
            "results": results,
        }, f, indent=1)


def write_junit(path: str, results: list[dict],
                fail_on_accommodated: bool = False) -> None:
    def tristate(value) -> str:
        if value is None:
            return "unknown"
        return str(bool(value)).lower()

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    fails = sum(r["verdict"] == "FAIL" or
                (fail_on_accommodated and
                 effective_verdict(r) == "PASS_ACCOMMODATED")
                for r in results)
    errors = sum(r["verdict"] in ("ERROR", "TIMEOUT") for r in results)
    lines.append(f'<testsuite name="sil" tests="{len(results)}" '
                 f'failures="{fails}" errors="{errors}">')
    lines.append('  <properties>')
    lines.append('    <property name="sil.schema_version" value="3"/>')
    lines.append('    <property name="sil.fail_on_accommodated" '
                 f'value="{str(fail_on_accommodated).lower()}"/>')
    lines.append('  </properties>')
    for r in results:
        cls, name = (r["id"].split(".", 1) + [""])[:2]
        lines.append(f'  <testcase classname="sil.{sx.escape(cls)}" '
                     f'name="{sx.escape(name)}" time="{r.get("duration_s", 0)}">')
        classification = effective_verdict(r)
        lines.append('    <properties>')
        lines.append(f'      <property name="sil.verdict" '
                     f'value="{sx.escape(r["verdict"])}"/>')
        lines.append(f'      <property name="sil.effective_verdict" '
                     f'value="{sx.escape(classification)}"/>')
        lines.append(f'      <property name="sil.accommodated" '
                     f'value="{tristate(r.get("accommodated"))}"/>')
        lines.append(f'      <property name="sil.strict_model" '
                     f'value="{tristate(r.get("strict_model"))}"/>')
        lines.append(f'      <property name="sil.model_mode_known" '
                     f'value="{tristate(r.get("model_mode_known"))}"/>')
        for index, accommodation in enumerate(r.get("accommodations", []), 1):
            lines.append(f'      <property name="sil.accommodation.{index}" '
                         f'value="{sx.escape(accommodation)}"/>')
        lines.append('    </properties>')
        failed = [c for c in r.get("checks", []) if c["verdict"] == "FAIL"]
        if r["verdict"] == "FAIL":
            msg = "; ".join(c["name"] for c in failed)
            body = "\n".join(f"{c['name']}\n  observed: {c['observed']}\n"
                             f"  expected: {c['expected']}" for c in failed)
            lines.append(f'    <failure message="{sx.escape(msg)}">'
                         f'{sx.escape(body)}</failure>')
        elif (fail_on_accommodated and
              classification == "PASS_ACCOMMODATED"):
            body = "\n".join(r.get("accommodations", []))
            lines.append('    <failure message="SIL accommodations are '
                         'forbidden by CI policy" type="SILAccommodatedPass">'
                         f'{sx.escape(body)}</failure>')
        elif r["verdict"] in ("ERROR", "TIMEOUT"):
            lines.append(f'    <error message="{sx.escape(r["verdict"])}">'
                         f'{sx.escape(r.get("error", ""))}</error>')
        obs = [c for c in r.get("checks", []) if c["verdict"] == "OBSERVE"]
        system_out = [
            f"SIL effective verdict: {classification}",
            f"SIL model mode known: {tristate(r.get('model_mode_known'))}",
            f"SIL accommodated: {tristate(r.get('accommodated'))}",
        ]
        system_out.extend(
            f"SIL accommodation {index}: {text}"
            for index, text in enumerate(r.get("accommodations", []), 1))
        system_out.extend(f"{c['name']}: {c['observed']}" for c in obs)
        lines.append(f'    <system-out>'
                     f'{sx.escape(chr(10).join(system_out))}</system-out>')
        lines.append('  </testcase>')
    lines.append('</testsuite>')
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description="MiniRideHeight SIL test runner")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--force-build", action="store_true")
    ap.add_argument("--filter", default="", help="substring match on test id")
    ap.add_argument("--timeout", type=int, default=120, help="per-test watchdog (s)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--fail-on-accommodated", action="store_true",
        help="return nonzero and mark JUnit failures for PASS* results")
    ap.add_argument("--json", dest="json_path", default="")
    ap.add_argument("--junit", dest="junit_path", default="")
    args = ap.parse_args()

    sys.path.insert(0, SIL_DIR)
    import build as sil_build
    print("building SIL DLL from the real firmware sources...")
    dll = sil_build.build(force=args.force_build)
    print(f"  {dll}")
    if args.build_only:
        return 0

    ids = [i for i in collect_test_ids() if args.filter in i]
    if not ids:
        print(f"no tests match filter {args.filter!r}")
        return 2

    print(f"running {len(ids)} test(s), one subprocess each "
          f"(watchdog {args.timeout}s)...\n")
    results = []
    for tid in ids:
        r = run_one(tid, args.timeout)
        results.append(r)
        shown = ("PASS*" if effective_verdict(r) == "PASS_ACCOMMODATED"
                 else r["verdict"])
        print(f"  {shown:7s} {tid:35s} {r.get('title', '')}")
        print_details(r, args.verbose)

    counts = {}
    for r in results:
        classification = effective_verdict(r)
        counts[classification] = counts.get(classification, 0) + 1
    print("\n==== SIL summary " + "=" * 45)
    for v in ("PASS", "PASS_ACCOMMODATED", "FAIL", "OBSERVE", "ERROR", "TIMEOUT"):
        if counts.get(v):
            label = "PASS*" if v == "PASS_ACCOMMODATED" else v
            print(f"  {label:8s} {counts[v]}")
    bad = sum(counts.get(v, 0) for v in BAD)
    accommodated_bad = (counts.get("PASS_ACCOMMODATED", 0)
                        if args.fail_on_accommodated else 0)
    print(f"  total    {len(results)}")
    print_issue_summary(build_issue_summary(results))
    print()
    if counts.get("PASS_ACCOMMODATED"):
        print("  PASS* means the model was deliberately loosened for that test")
        if args.fail_on_accommodated:
            print("  --fail-on-accommodated is active: PASS* fails this run")
    print("  note: a fully green run validates the driver LOGIC against the")
    print("  datasheet model only -- electrical behavior, real EXTI/NVIC")
    print("  timing and model accuracy are outside what SIL can prove.")

    if args.json_path:
        write_json(args.json_path, results, args.fail_on_accommodated)
        print(f"  json  -> {args.json_path}")
    if args.junit_path:
        write_junit(args.junit_path, results, args.fail_on_accommodated)
        print(f"  junit -> {args.junit_path}")

    return 1 if bad or accommodated_bad else 0


if __name__ == "__main__":
    sys.exit(main())
