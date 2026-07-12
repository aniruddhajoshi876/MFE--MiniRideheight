"""
worker.py -- runs exactly ONE SIL test in this process and writes a JSON
result file. One process per test gives every test a fresh DLL: the
function-local `static CANDriver` and the main.c globals cannot leak
between tests.

stdout handling: fd 1 is redirected to a capture file BEFORE the DLL is
loaded, so the firmware's printf() output becomes recorded evidence. The
JSON result travels through a separate file, never through stdout.

Usage: python sil/worker.py <test-id> <out.json>
"""

from __future__ import annotations

import json
import os
import sys
import traceback

SIL_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    test_id, out_path = sys.argv[1], sys.argv[2]
    sys.path.insert(0, SIL_DIR)

    capture_path = out_path + ".stdout.txt"
    os.environ["SIL_CAPTURE_PATH"] = capture_path
    saved_fd = os.dup(1)
    cap = open(capture_path, "w", buffering=1, errors="replace")
    os.dup2(cap.fileno(), 1)   # Python and the DLL share the CRT fd table

    result = {
        "id": test_id, "title": "", "verdict": "ERROR",
        "effective_verdict": "ERROR", "accommodated": None,
        "strict_model": None, "model_mode_known": False,
        "checks": [], "trace": [], "accommodations": [],
        "trace_stats": {
            "total_events": 0, "captured_events": 0,
            "omitted_events": 0, "stored_lines": 0,
            "collapsed_runs": 0, "repeated_events_collapsed": 0,
            "truncated": False,
        },
        "stdout": "", "error": "",
    }
    model_mode_known = False
    try:
        from harness import Report
        import harness
        import tests

        if test_id not in tests.TESTS:
            raise KeyError(f"unknown test id: {test_id}")
        result["title"] = tests.TESTS[test_id]["title"]
        rep = Report()
        tests.TESTS[test_id]["fn"](rep)
        result["verdict"] = rep.verdict()
        result["checks"] = [c.as_dict() for c in rep.checks]
    except Exception:
        result["error"] = traceback.format_exc()
        result["verdict"] = "ERROR"
    finally:
        try:
            import harness
            last = getattr(harness, "LAST_SIL", None)
            if last is not None:
                model_mode_known = True
                last.dll.sil_flush()
                result["trace"], result["trace_stats"] = last.log.export()
                result["accommodations"] = last.accommodations
        except Exception:
            pass
        sys.stdout.flush()
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
        cap.close()
        try:
            with open(capture_path, "r", errors="replace") as f:
                result["stdout"] = f.read()
            os.remove(capture_path)
        except OSError:
            pass

    # Preserve the ordinary functional verdict while making the model mode
    # and an accommodated success explicit to JSON/JUnit consumers.
    result["model_mode_known"] = model_mode_known
    result["accommodated"] = (bool(result["accommodations"])
                              if model_mode_known else None)
    result["strict_model"] = (not result["accommodated"]
                              if model_mode_known else None)
    if result["verdict"] == "PASS" and result["accommodated"] is True:
        result["effective_verdict"] = "PASS_ACCOMMODATED"
    else:
        result["effective_verdict"] = result["verdict"]

    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
