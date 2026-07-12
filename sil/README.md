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
python sil/run_sil.py --verbose        # full traces for passing tests too
python sil/run_sil.py --build-only     # just compile the DLL
python sil/run_sil.py --json out.json --junit out.xml
```

Requires Python 3.10+ and an MSYS2 gcc/g++ (auto-detected; override with
`SIL_GCC`/`SIL_GXX`).

## Reading the results

| Verdict | Meaning | Affects exit code |
|---|---|---|
| PASS | conformance check met, strict model | no |
| PASS* | passes only under a logged SIL accommodation (loosened model) | no |
| FAIL | observed behavior contradicts the expected contract | **yes** |
| OBSERVE | characterization only (reported, not judged) | no |
| ERROR | the worker crashed (e.g. access violation in the DLL) | **yes** |
| TIMEOUT | test exceeded the watchdog | **yes** |

**Strict is the default.** A `PASS*` never means integration-level success:
it means the model was deliberately loosened (e.g. answering a non-standard
I2C address, or firing EXTI regardless of the configured edge) so one defect
does not mask every downstream test. The strict companions that judge those
loosened aspects are `i2c.address_convention` and
`irq.transition_integration`. Likewise `sys.e2e_component_assisted`
(Python starts components by hand) is the labeled diagnostic sibling of
`sys.e2e_strict`, where the firmware alone must move a measurement from the
sensor to a CAN frame while Python only advances time.

Every FAIL prints **observed vs. expected** plus the simulated I2C/CAN/GPIO
transaction trace around the failure and any captured `printf` output. The
harness reports symptoms and evidence only — tracing a failure to its cause
is deliberately left to you. Start from the trace: find the last transaction
that looks right, and ask what the first wrong one implies.

Lines marked `(i) SIL accommodation` mean the model was deliberately
loosened for that test (e.g. answering a non-standard address) so one bug
does not mask every downstream test. Each accommodation has a strict
companion test that runs without it.

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
sil_probe.cpp  queue-size probe via the public CANDriver API
models.py    sensor / CAN / GPIO / clock models
harness.py   DLL loading + ctypes wiring
tests.py     the test suites (i2c.* irq.* can.* sys.*)
worker.py    runs ONE test per process (fresh static state each time)
run_sil.py   builds, orchestrates, reports, exits nonzero on findings
```
