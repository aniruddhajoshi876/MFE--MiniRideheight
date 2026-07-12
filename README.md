# MFE Mini Ride Height

STM32 firmware for measuring vehicle ride height with an ST VL53L4CD
time-of-flight sensor and transmitting the result over CAN.

```text
VL53L4CD -> I²C2 + GPIO1 interrupt -> STM32G491 -> FDCAN1
```

The sensor supports short-range measurements up to roughly 1200 mm. In this
project, valid measurements are sent as an unsigned 16-bit distance in
millimetres.

## How the driver works

The application uses ST's VL53L4CD Ultra Lite Driver (ULD). The main project
files are:

- `Core/Src/main.c`: startup and main loop
- `Core/Src/BoardManager.c`: sensor-to-CAN application logic
- `Drivers/Platform/platform.c`: connects the ULD to STM32 HAL I²C
- `Core/Src/gpio.c`: configures the sensor interrupt on PA4
- `Core/Src/CANDriver.cpp`: queues and transmits CAN frames

### Startup

The firmware initializes GPIO, CAN, I²C, and UART. It then retries until both
CAN and the sensor start successfully:

```text
initialize peripherals
        |
        v
initializeCAN() --failure--> wait 20 ms and retry
        |
      success
        v
sensor_start()  --failure--> wait 20 ms and retry
        |
      success
        v
wait for measurements
```

`sensor_start()` performs:

1. `VL53L4CD_SensorInit()`
2. `VL53L4CD_StartRanging()`

The sensor uses I²C address `0x52` in STM32 HAL/ULD form, equivalent to the
7-bit address `0x29`.

### Measurement state machine

The VL53L4CD GPIO1 output is active-low. PA4 is therefore configured for a falling-edge interrupt.

```text
RANGING
   |
   | measurement completes
   v
GPIO1 ASSERTED LOW
   |
   | EXTI4 callback sets data_ready = true
   v
DATA PENDING
   |
   | main loop clears data_ready and calls get_data_it()
   v
READ RESULT
   |
   +-- I²C read failed --------> clear sensor interrupt -> RANGING
   |
   +-- range_status != 0 ------> clear interrupt, report error -> RANGING
   |
   +-- valid result -----------> clear interrupt -> send CAN -> RANGING
```

Clearing the sensor interrupt is required. Until it is cleared, the VL53L4CD
holds the ranging handshake and does not provide the next result.

The application uses a Boolean `data_ready` flag. Multiple interrupts arriving
before the main loop consumes the flag collapse into one pending event.

### CAN format

The supplied DBC defines the ride-height messages as:

| Position   |          CAN ID | Payload                                   |
| ---------- | --------------: | ----------------------------------------- |
| Front      | `0x262` / 610 | 2-byte unsigned little-endian millimetres |
| Rear left  | `0x263` / 611 | 2-byte unsigned little-endian millimetres |
| Rear right | `0x264` / 612 | 2-byte unsigned little-endian millimetres |

Only the front message is currently transmitted. For example:

```text
300 mm = 0x012C -> CAN bytes 2C 01
```

The DBC range is 0–500 mm, while the sensor can measure farther. The firmware
currently does not clamp or reject values above 500 mm.

## Software-in-the-loop testing

The `sil/` harness compiles the real firmware drivers into a Windows DLL and
runs them against Python hardware models.

```text
real firmware C/C++
        |
        v
     host DLL
        |
        +-- simulated VL53L4CD registers and timing
        +-- simulated GPIO1 and EXTI behavior
        +-- simulated FDCAN queue, transmission, and ACK
        +-- transaction log and fault injection
```

Python supplies external events such as a target distance, elapsed time, an
absent sensor, I²C errors, or missing CAN acknowledgement. The real firmware
still performs the register accesses, interrupt handling, result decoding, and
CAN frame construction.

### Running SIL

From the repository root:

```powershell
python sil/run_sil.py
python sil/run_sil.py --filter can
python sil/run_sil.py --verbose
python sil/run_sil.py --json results.json --junit results.xml
python sil/run_sil.py --fail-on-accommodated
```

The harness requires Python 3.10+ and MinGW-w64 `gcc`/`g++`.

Each test runs in a new subprocess so firmware globals and static state do not
leak between tests.

### Results

| Result      | Meaning                                      |
| ----------- | -------------------------------------------- |
| `PASS`    | Passed against the strict model              |
| `PASS*`   | Passed with a documented SIL accommodation   |
| `FAIL`    | Firmware behavior violated the test contract |
| `OBSERVE` | Information recorded without judging it      |
| `ERROR`   | Worker or DLL crashed                        |
| `TIMEOUT` | Test exceeded its watchdog                   |

SIL prints expected-versus-observed evidence and the related I²C, GPIO, and CAN
trace. JSON and JUnit reports are available for CI.

Production `main()` is an infinite loop and is not linked into the DLL. SIL
uses bounded mirrors of startup and one main-loop iteration. A drift test checks
that the mirrored call structure still matches `main.c`.

## SIL strengths

- Runs the real sensor, platform, application, GPIO, and CAN driver code.
- Tests the full logical path from measurement to CAN frame.
- Checks I²C widths and byte order, interrupt handshake, result decoding, CAN
  queue behavior, acknowledgement, and DBC payload bytes.
- Injects sensor absence, NACKs, timeouts, CAN failures, and missing ACKs.
- Produces deterministic traces and isolates every test in its own process.
- Finds failures before firmware is flashed to the PCB.

## SIL limitations

SIL validates logic against a software model. It does not prove:

- electrical wiring, pull-ups, voltage levels, or signal integrity;
- real sensor optics, road reflectivity, sunlight, water, debris, or vibration;
- actual interrupt latency, timing races, or NVIC behavior;
- physical I²C behavior such as rise time or clock stretching;
- CAN transceiver, termination, arbitration, or vehicle-bus behavior;
- that the simulated sensor perfectly matches every real-silicon behavior;
- the literal embedded `main()` startup path, because SIL uses a bounded mirror.

A passing SIL run should be followed by testing on the STM32 target and then
hardware-in-the-loop or vehicle testing.

## Known issues

- A missing sensor currently causes many NACKed transactions and can end in a
  divide-by-zero instead of failing startup cleanly.
- Rear-left and rear-right CAN messages are defined but not transmitted.
- The DBC accepts 0–500 mm, but firmware can transmit larger values.
- The Boolean `data_ready` flag can collapse multiple pending events.

## References

- ST VL53L4CD datasheet, DS13812
- ST VL53L4CD Ultra Lite Driver guide, UM2931
- `MFE26_sensor (2) (1).dbc`
