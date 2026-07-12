"""
run_sil.py -- SIL test automation entry point.

Compiles the REAL firmware sources into a host DLL, runs every test in its
own subprocess (fresh static state per test), and reports:

  PASS     conformance check met
  FAIL     observed behavior contradicts the expected contract
  OBSERVE  characterization only -- reported, never affects the exit code
  ERROR    the worker crashed (e.g. access violation inside the DLL)
  TIMEOUT  the worker exceeded the watchdog

Exit code is nonzero when any FAIL / ERROR / TIMEOUT occurred.

Failure output is symptom + transaction evidence only: observed vs. expected
plus the simulated I2C/CAN/GPIO transactions around the failure. The harness
never names or applies a fix.

Usage:
  python sil/run_sil.py                     run everything
  python sil/run_sil.py --filter can        run tests whose id contains "can"
  python sil/run_sil.py --build-only        just compile the DLL
  python sil/run_sil.py --verbose           full traces for passing tests too
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
        return dict(id=test_id, verdict="TIMEOUT", checks=[], trace=[],
                    accommodations=[], stdout="", title="",
                    error=f"worker exceeded the {timeout_s}s watchdog",
                    duration_s=round(time.time() - t0, 2))
    duration = round(time.time() - t0, 2)

    if not os.path.isfile(out_json):
        return dict(id=test_id, verdict="ERROR", checks=[], trace=[],
                    accommodations=[], stdout=proc.stdout, title="",
                    error=(f"worker exited with code {proc.returncode} and no "
                           f"result file (likely a crash inside the DLL).\n"
                           f"stderr:\n{proc.stderr}"),
                    duration_s=duration)
    try:
        with open(out_json, "r", errors="replace") as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return dict(id=test_id, verdict="ERROR", checks=[], trace=[],
                    accommodations=[], stdout=proc.stdout, title="",
                    error=f"unreadable result file: {exc}", duration_s=duration)
    result["duration_s"] = duration
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
        n = len(r["trace"]) if verbose else min(TRACE_TAIL, len(r["trace"]))
        print(f"    --- transaction trace (last {n} of {len(r['trace'])}) ---")
        for line in r["trace"][-n:]:
            print(f"      {line}")
    if interesting and r.get("stdout", "").strip():
        print("    --- captured firmware printf output ---")
        for line in r["stdout"].splitlines()[:20]:
            print(f"      {line}")


def write_json(path: str, results: list[dict]) -> None:
    with open(path, "w") as f:
        json.dump(dict(results=results), f, indent=1)


def write_junit(path: str, results: list[dict]) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    fails = sum(r["verdict"] == "FAIL" for r in results)
    errors = sum(r["verdict"] in ("ERROR", "TIMEOUT") for r in results)
    lines.append(f'<testsuite name="sil" tests="{len(results)}" '
                 f'failures="{fails}" errors="{errors}">')
    for r in results:
        cls, name = (r["id"].split(".", 1) + [""])[:2]
        lines.append(f'  <testcase classname="sil.{sx.escape(cls)}" '
                     f'name="{sx.escape(name)}" time="{r.get("duration_s", 0)}">')
        failed = [c for c in r.get("checks", []) if c["verdict"] == "FAIL"]
        if r["verdict"] == "FAIL":
            msg = "; ".join(c["name"] for c in failed)
            body = "\n".join(f"{c['name']}\n  observed: {c['observed']}\n"
                             f"  expected: {c['expected']}" for c in failed)
            lines.append(f'    <failure message="{sx.escape(msg)}">'
                         f'{sx.escape(body)}</failure>')
        elif r["verdict"] in ("ERROR", "TIMEOUT"):
            lines.append(f'    <error message="{sx.escape(r["verdict"])}">'
                         f'{sx.escape(r.get("error", ""))}</error>')
        obs = [c for c in r.get("checks", []) if c["verdict"] == "OBSERVE"]
        if obs:
            body = "\n".join(f"{c['name']}: {c['observed']}" for c in obs)
            lines.append(f'    <system-out>{sx.escape(body)}</system-out>')
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
    compat_passes = 0
    for tid in ids:
        r = run_one(tid, args.timeout)
        results.append(r)
        # STRICT-BY-DEFAULT: a PASS earned under a SIL accommodation
        # (loosened model) is marked PASS* so it can never be read as an
        # ordinary, integration-level pass.
        shown = r["verdict"]
        if shown == "PASS" and r.get("accommodations"):
            shown = "PASS*"
            compat_passes += 1
        print(f"  {shown:7s} {tid:35s} {r.get('title', '')}")
        print_details(r, args.verbose)

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n==== SIL summary " + "=" * 45)
    for v in ("PASS", "FAIL", "OBSERVE", "ERROR", "TIMEOUT"):
        if counts.get(v):
            extra = ""
            if v == "PASS" and compat_passes:
                extra = (f"  ({compat_passes} marked PASS*: earned under a "
                         "SIL accommodation)")
            print(f"  {v:8s} {counts[v]}{extra}")
    bad = sum(counts.get(v, 0) for v in BAD)
    print(f"  total    {len(results)}")
    if compat_passes:
        print("  PASS* means the model was deliberately loosened for that test")
        print("  (see its '(i) SIL accommodation' lines); the strict companions")
        print("  are i2c.address_convention and irq.transition_integration.")
    print("  note: a fully green run validates the driver LOGIC against the")
    print("  datasheet model only -- electrical behavior, real EXTI/NVIC")
    print("  timing and model accuracy are outside what SIL can prove.")

    if args.json_path:
        write_json(args.json_path, results)
        print(f"  json  -> {args.json_path}")
    if args.junit_path:
        write_junit(args.junit_path, results)
        print(f"  junit -> {args.junit_path}")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
