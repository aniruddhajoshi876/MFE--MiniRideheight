# MiniRideHeight SIL Goal

## Primary goal

Build and maintain a software-in-the-loop (SIL) test harness that validates the
MiniRideHeight firmware driver logic on a PC **before the firmware is flashed to
the PCB**.

The harness must compile and execute the real C/C++ firmware sources. Python is
the automation and peripheral-simulation layer; the production sensor and CAN
drivers must not be rewritten in Python.

## What the SIL must validate

The automated tests should exercise the complete intended firmware data path:

```text
simulated VL53L4CD measurement
    -> I2C register transactions
    -> GPIO1 transition
    -> STM32 EXTI callback
    -> data-ready handling
    -> result decoding
    -> CAN frame construction
    -> simulated CAN transmission and acknowledgement
```

The harness must check:

- VL53L4CD I2C address convention, register addresses, transfer widths, byte
  order, initialization, ranging, result reads, and interrupt clearing.
- GPIO interrupt polarity, configured edge, callback behavior, event
  consumption, clear/re-arm behavior, and repeated measurement cycles.
- CAN initialization, identifier, DLC, payload bytes, queue limits, FIFO
  ordering, controller acceptance, bus acknowledgement, and error handling.
- Propagation and safe handling of sensor, I2C, interrupt, and CAN failures.
- The real firmware startup and loop path, including strict end-to-end tests in
  which Python only supplies external events and advances simulated time.

## Test philosophy

- Strict hardware-contract models are the source of truth.
- Tests must use deterministic inputs so every failure is reproducible.
- A compatibility accommodation may bypass one known defect to expose later
  behavior, but it must be labeled `PASS*` / `PASS_ACCOMMODATED` and must never
  be presented as a strict integration success.
- Tests must report what was expected, what happened, and the relevant I2C,
  GPIO, CAN, and firmware-output evidence.
- A failing test is useful evidence. The objective is to reveal firmware
  defects, not to make the suite green by weakening the model.
- JSON and JUnit output must preserve strict versus accommodated results so the
  harness can be used in CI.

## Definition of success

A fully successful strict run means:

1. The actual firmware sources build for the host without replacing their
   application logic with Python equivalents.
2. Every required I2C, interrupt, result-decoding, CAN, fault, and end-to-end
   contract passes without accommodations.
3. No test ends in an unexpected worker error, timeout, crash, undefined memory
   access, or unreported peripheral failure.
4. Repeated executions produce the same inputs, traces, and verdicts.
5. A failure identifies the broken stage clearly enough to diagnose before PCB
   flashing.

Run the suite from the repository root with:

```powershell
python sil/run_sil.py
```

Use the stricter release-CI policy with:

```powershell
python sil/run_sil.py --fail-on-accommodated
```

## Boundaries

SIL validates firmware logic against a simulated, datasheet-derived model. It
does not prove physical PCB behavior, voltage levels, pull-ups, signal
integrity, sensor optics, CAN-transceiver behavior, real interrupt latency,
clock accuracy, or electromagnetic robustness. Those require target and
hardware-in-the-loop testing after the SIL logic is sound.

