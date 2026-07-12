# SIL (software-in-the-loop) test harness

Runs the **real firmware driver code** (`VL53L4CD_api.c`, `platform.c`,
`CANDriver.cpp`, `BoardManager.c`, `gpio.c` — compiled unmodified) on the host
PC against simulated peripherals, so the driver can be validated **before
flashing the PCB**:

- a VL53L4CD I2C register model (registers MSB-first per datasheet Table 8,
  interrupt polarity derived from `GPIO_HV_MUX__CTRL` exactly as the ULD
  reads it, time-driven measurement completion),
- an FDCAN Tx model (per-buffer TXBRP/TXBTO state machine — "accepted into
  the FIFO" is tracked separately from "transmitted and ACKed"),
- a GPIO1 → EXTI4 wire that fires only when the pin transition matches the
  configuration captured from the real `MX_GPIO_Init()`.

Nothing under `Core/` or `Drivers/` is modified.

## Usage

```
python sil/run_sil.py                  # build + run everything
python sil/run_sil.py --filter can     # only tests whose id contains "can"
python sil/run_sil.py --verbose        # all retained trace lines
python sil/run_sil.py --build-only     # just compile the DLL
python sil/run_sil.py --json out.json --junit out.xml
python sil/run_sil.py --fail-on-accommodated  # release-CI policy
```

Requires Python 3.10+ and an MSYS2 gcc/g++ (auto-detected; override with
`SIL_GCC`/`SIL_GXX`).

## Reading the results

| Verdict | Meaning | Affects exit code |
|---|---|---|
| PASS | conformance check met, strict model | no |
| PASS* | passes only under a logged SIL accommodation (loosened model); JSON `effective_verdict=PASS_ACCOMMODATED` | no by default; **yes** with `--fail-on-accommodated` |
| FAIL | observed behavior contradicts the expected contract | **yes** |
| OBSERVE | characterization only (reported, not judged) | no |
| ERROR | the worker crashed (e.g. access violation in the DLL) | **yes** |
| TIMEOUT | test exceeded the watchdog | **yes** |

**Strict is the default.** A `PASS*` never means integration-level success:
it means the model was deliberately loosened (e.g. answering a non-standard
I2C address, or force-firing EXTI on the sensor's assertion transition even
when it does not match the configured edge -- never on the release) so one
defect does not mask every downstream test. The strict companions that judge those
loosened aspects are `i2c.address_convention` and
`irq.transition_integration`. Likewise `sys.e2e_component_assisted`
(Python starts components by hand) is the labeled diagnostic sibling of
`sys.e2e_strict`, whose `Sil()` model has no address or EXTI accommodation.
Because `main()` is a non-returning superloop and is not linked, the strict
test runs `sil_main_start()`, a BOUNDED MIRROR of main.c's `initializeCAN()`
and `sensor_start()` retry loops (`sys.startup_mirror_drift` checks the
mirror still matches `main.c`); after that Python only supplies a target and
advances time. It never calls `sensor_start()`, `get_data_it()`, or
`push_on_bus()` directly in that strict test.

### Machine-readable result and CI policy

Each per-test JSON result preserves the ordinary functional `verdict`
(`PASS`, `FAIL`, `OBSERVE`, `ERROR`, or `TIMEOUT`) and also contains:

```json
{
  "effective_verdict": "PASS_ACCOMMODATED",
  "model_mode_known": true,
  "accommodated": true,
  "strict_model": false,
  "accommodations": ["SIL accommodation: ..."]
}
```

`PASS_ACCOMMODATED` is what the terminal renders as `PASS*`. A failed test
remains `FAIL` even if it used an accommodation; `accommodated` still records
the model mode. Runner-level JSON uses `schema_version: 3` and records the
active policy, classification counts, and an issue summary
(`summary.failures` / `errors` / `timeouts` / `accommodated`, each entry
carrying the failed checks' expected-vs-observed evidence and, for `PASS*`,
the accommodation labels). Synthesized `ERROR`/`TIMEOUT`
results use the same fields, but set `model_mode_known: false` and the
`accommodated` / `strict_model` fields to `null`: no worker result exists from
which to infer the model mode.

The backward-compatible default exit policy treats `PASS*` as exit-neutral.
Use `--fail-on-accommodated` for release CI: the process returns nonzero when
any `PASS*` exists. JUnit always carries per-test `sil.effective_verdict`,
`sil.accommodated`, `sil.strict_model`, `sil.model_mode_known`, and the
accommodation text as properties. The effective verdict and model-mode facts
are also duplicated in `<system-out>` for JUnit consumers that ignore
testcase-level properties. With that flag active, each `PASS*` is also emitted
as a JUnit failure so CI dashboards cannot show a fully green release run.

Every FAIL prints **observed vs. expected** plus the simulated I2C/CAN/GPIO
transaction trace around the failure and any captured `printf` output. The
harness reports symptoms and evidence only — tracing a failure to its cause
is deliberately left to you. Start from the trace: find the last transaction
that looks right, and ask what the first wrong one implies.

Lines marked `(i) SIL accommodation` mean the model was deliberately
loosened for that test (e.g. answering a non-standard address) so one bug
does not mask every downstream test. Each accommodation has a strict
companion test that runs without it.

The live `TransactionLog` remains complete while a test is running, so test
counts and searches are unaffected. Persisted JSON traces are bounded to 300
stored lines: consecutive identical events are collapsed, and large traces
retain a head and tail with an explicit omission marker. `trace_stats` records
`total_events`, `captured_events`, `omitted_events`, `stored_lines`, collapsed
run/repetition counts, and whether truncation occurred. Thus a boot-poll or
NACK storm cannot create a huge artifact, but the fact, scale, and surrounding
evidence remain visible. `--verbose` prints all **retained** trace lines; it
does not claim to reconstruct events reported as omitted.

## Scenario contracts

- `scn.normal_100_cycles` requires all 100 handshakes to complete **and** all
  100 decoded distances to equal their programmed measurements. Incorrect
  values can no longer be reported as a normal PASS.
- `scn.sensor_absent_boot` uses `sensor_present=False`. The model NACKs even
  the correct HAL-form address and labels the reason as physical absence, so
  this is distinct from `i2c.address_convention` (present sensor, wrong
  address argument).
- `sys.failed_get_result_not_consumed` first runs a valid control cycle, then
  injects a deterministic timeout on the distance read. Required checks judge
  whether the failed result is presented or transmitted as valid. The
  separate `scn.transient_i2c_fault_recovery` test continues to characterize
  whether a later clean cycle can proceed without deadlock.
- `sys.startup_characterization` is intentionally `OBSERVE`, not PASS: it
  reports the production wiring without implying startup requirements are met.

## What a green run does / does not prove

A fully green run means the driver **logic** — register access widths and
order, init/start/get/clear control flow, the interrupt handshake as coded,
CAN framing and queue behavior — is consistent with this datasheet-derived
model. It does **not** prove: model accuracy vs. the real silicon, real
EXTI/NVIC timing behavior, ISR races, HAL error states beyond the injected
ones, or anything electrical (pull-ups, XSHUT, bus wiring). If a green
driver misbehaves on the board, look there.

## Layout

```
mock_hal/    mock STM32 HAL (stm32g4xx_hal.h shadow); constants copied
             verbatim from the real HAL headers
sil_shim.c   globals main.c would define + verbatim main-loop step
sil_probe.cpp  queue-size probe + constants compiled from firmware headers
models.py    sensor / CAN / GPIO / clock models
harness.py   DLL loading + ctypes wiring
tests.py     the test suites (i2c.* irq.* can.* sys.*)
worker.py    runs ONE test per process (fresh static state each time)
run_sil.py   builds, orchestrates, reports, exits nonzero on findings
```

`sil_shim.c` still mirrors the loop body because changing production code is
outside this harness-only layer. The residual risk is drift if `main.c` is
edited without updating the shim. A future production refactor should expose
shared `application_init()` / `application_step()` functions used by both the
embedded `main()` and SIL; this harness deliberately does not modify
`Core/` or `Drivers/` to create them.
