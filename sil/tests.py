"""
tests.py -- SIL test suites.

Every test runs the REAL compiled driver code against the Python peripheral
models. Failure messages state observed vs. expected only -- symptoms and
transaction evidence, never the fix.

Suites:
  i2c.*  sensor register protocol through the student's platform.c
  irq.*  GPIO1 / EXTI4 / data_ready handshake
  can.*  FDCAN queue, framing, ACK semantics
  sys.*  BoardManager integration + end-to-end data path
"""

from __future__ import annotations

import ctypes as ct

from harness import (Sil, Report, VL53ResultsData, DEVICE_INSTANCE,
                     FRONT_CAN_ID, GPIO_PIN_4)
from models import (HAL_ERROR, HAL_TIMEOUT, reg_name,
                    REG_SYSTEM_START, REG_SYSTEM_INTERRUPT_CLEAR,
                    REG_INTERMEASUREMENT_MS, REG_RESULT_RANGE_STATUS,
                    REG_RESULT_DISTANCE, REG_RESULT_SPAD_NB,
                    REG_RESULT_SIGNAL_RATE, REG_RESULT_AMBIENT_RATE,
                    REG_RESULT_SIGMA, REG_FIRMWARE_SYSTEM_STATUS,
                    REG_IDENTIFICATION_MODEL_ID)

TESTS: dict[str, dict] = {}


def sil_test(test_id: str, title: str):
    def deco(fn):
        TESTS[test_id] = dict(fn=fn, title=title)
        return fn
    return deco


ACCOMMODATED = {DEVICE_INSTANCE}   # see VL53Model docstring; always logged


def _wr_value(sil, reg) -> int | None:
    """Value byte of the most recent write to `reg`, from the log."""
    writes = sil.i2c_writes_to(reg)
    if not writes:
        return None
    bytes_part = writes[-1].split("bytes=[")[1].rstrip("]")
    return int(bytes_part.split()[0], 16)


# ======================================================================
# i2c.* -- sensor register protocol
# ======================================================================

@sil_test("i2c.address_convention",
          "I2C DevAddress argument vs. the address the device answers to")
def t_address_convention(rep: Report):
    sil = Sil(accept_raw_devaddr=set())        # STRICT: no accommodation
    dev7 = sil.sensor.DEVICE_7BIT
    val = ct.c_uint8(0)

    # what the firmware actually does: DEVICE_INSTANCE as the DevAddress arg
    st = sil.dll.VL53L4CD_RdByte(DEVICE_INSTANCE, REG_IDENTIFICATION_MODEL_ID,
                                 ct.byref(val))
    wire7 = (DEVICE_INSTANCE >> 1) & 0x7F
    rep.check(
        "device acknowledges the driver's transactions",
        st == 0,
        observed=(f"platform.c passed DevAddress=0x{DEVICE_INSTANCE:02X} to "
                  f"HAL_I2C_Mem_Read; the HAL drives wire 7-bit address "
                  f"0x{wire7:02X} (DevAddress>>1); the device NACKed and the "
                  f"platform shim returned {st}"),
        expected=(f"a transaction the device acknowledges -- it answers wire "
                  f"7-bit 0x{dev7:02X}, i.e. HAL DevAddress form 0x{dev7 << 1:02X}"),
        note="HAL_I2C_Mem_* documents DevAddress as the already-left-shifted "
             "(8-bit) address; the model interprets it exactly that way")

    # model self-check so a FAIL above cannot be a simulator artifact
    st2 = sil.dll.VL53L4CD_RdByte(dev7 << 1, REG_IDENTIFICATION_MODEL_ID,
                                  ct.byref(val))
    rep.check(
        "model self-check: device reachable at its own configured address",
        st2 == 0 and val.value == 0xEB,
        observed=f"status={st2}, first model-id byte=0x{val.value:02X}",
        expected="status=0, first model-id byte=0xEB")


@sil_test("i2c.rw_widths", "platform.c transfer width per Rd/Wr function")
def t_rw_widths(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    b, w, dw = ct.c_uint8(0), ct.c_uint16(0), ct.c_uint32(0)

    cases = [
        ("RdByte", lambda: d.VL53L4CD_RdByte(DEVICE_INSTANCE, 0x0031, ct.byref(b)), 1),
        ("RdWord", lambda: d.VL53L4CD_RdWord(DEVICE_INSTANCE, 0x0096, ct.byref(w)), 2),
        ("RdDWord", lambda: d.VL53L4CD_RdDWord(DEVICE_INSTANCE, 0x006C, ct.byref(dw)), 4),
        ("WrByte", lambda: d.VL53L4CD_WrByte(DEVICE_INSTANCE, 0x0008, 0x09), 1),
        ("WrWord", lambda: d.VL53L4CD_WrWord(DEVICE_INSTANCE, 0x0064, 0x1234), 2),
        ("WrDWord", lambda: d.VL53L4CD_WrDWord(DEVICE_INSTANCE, 0x006C, 0), 4),
    ]
    for name, call, width in cases:
        before = len(sil.log.entries)
        call()
        moved = None
        for e in sil.log.entries[before:]:
            if " len=" in e:
                moved = int(e.split(" len=")[1].split()[0])
                break
        rep.check(
            f"VL53L4CD_{name} moves the register width",
            moved == width,
            observed=f"transferred {moved} byte(s): {sil.log.entries[before] if moved is not None else 'no transaction seen'}",
            expected=f"{width} byte(s) -- this register is {width} bytes wide")

    # byte order of a 16-bit write (device stores MSB first, Table 8)
    stored = sil.sensor.reg_bytes(0x0064, 2)
    rep.check(
        "WrWord(0x1234) byte order on the wire",
        stored == [0x12, 0x34],
        observed=f"register 0x0064 now holds bytes [{stored[0]:02X} {stored[1]:02X}]",
        expected="[12 34] -- multibyte registers are addressed MSB first "
                 "(datasheet DS13812 Table 8)")


@sil_test("i2c.sensor_init_sequence", "VL53L4CD_SensorInit against a booting sensor")
def t_sensor_init(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    status = sil.dll.VL53L4CD_SensorInit(DEVICE_INSTANCE)

    rep.check("SensorInit returns 0",
              status == 0,
              observed=f"returned {status}",
              expected="0 (VL53L4CD_ERROR_NONE)")

    boot_polls = len(sil.i2c_reads_of(REG_FIRMWARE_SYSTEM_STATUS))
    rep.check("boot state was polled before configuring",
              boot_polls >= 2,
              observed=f"{boot_polls} read(s) of FIRMWARE__SYSTEM_STATUS "
                       f"(model booted after {sil.sensor.BOOT_MS} sim-ms)",
              expected=">= 2 reads (poll until 0x03)")

    sweep = [e for e in sil.log.find("WR 0x00")
             if any(f"WR 0x{a:04X}" in e for a in range(0x2D, 0x88))]
    # count distinct swept addresses 0x2D..0x87 (91 registers)
    swept = set()
    for a in range(0x2D, 0x88):
        if sil.log.find(f"WR {reg_name(a)} len=1") or sil.log.find(f"WR 0x{a:04X} len=1"):
            swept.add(a)
    rep.check("default configuration sweep covers 0x2D..0x87",
              len(swept) == 0x88 - 0x2D,
              observed=f"{len(swept)} of {0x88 - 0x2D} registers written",
              expected=f"{0x88 - 0x2D} single-byte writes")

    rep.check("VHV start observed",
              any("SYSTEM_START" in e and "40" in e.split("bytes=[")[-1]
                  for e in sil.i2c_writes_to(REG_SYSTEM_START)),
              observed="; ".join(sil.i2c_writes_to(REG_SYSTEM_START)[:3]) or "none",
              expected="a write of 0x40 to SYSTEM_START")
    rep.check("interrupt cleared and ranging stopped afterwards",
              bool(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) and
              _wr_value(sil, REG_SYSTEM_START) == 0x80,
              observed=f"last SYSTEM_START write value: "
                       f"{_wr_value(sil, REG_SYSTEM_START)!r}; "
                       f"{len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR))} "
                       "interrupt-clear write(s)",
              expected="SYSTEM__INTERRUPT_CLEAR written, then SYSTEM_START=0x80")


@sil_test("i2c.get_sensor_id", "GetSensorId reads model id 0xEBAA")
def t_get_sensor_id(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    sensor_id = ct.c_uint16(0)
    st = sil.dll.VL53L4CD_GetSensorId(DEVICE_INSTANCE, ct.byref(sensor_id))
    reads = sil.i2c_reads_of(REG_IDENTIFICATION_MODEL_ID)
    rep.check("GetSensorId returns 0", st == 0,
              observed=f"returned {st}", expected="0")
    rep.check(
        "sensor id equals 0xEBAA",
        sensor_id.value == 0xEBAA,
        observed=(f"driver returned 0x{sensor_id.value:04X}; on-wire bytes were "
                  f"{reads[-1].split('bytes=')[-1] if reads else 'none'} "
                  "(device sends MSB first)"),
        expected="0xEBAA (datasheet reference registers: 0x010F=0xEB, 0x0110=0xAA)")
    rep.observe("who calls GetSensorId",
                "no function in BoardManager.c or main.c calls "
                "VL53L4CD_GetSensorId; the id is never checked in the "
                "as-wired firmware")


@sil_test("i2c.get_result_roundtrip",
          "GetResult returns the values the sensor measured")
def t_get_result(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)
    truth = dict(distance_mm=300, user_status=0, signal_kcps=2048,
                 sigma_mm=4, spads=5)
    sil.sensor.program_measurement(**{k: v for k, v in truth.items()})
    d.VL53L4CD_StartRanging(DEVICE_INSTANCE)
    sil.sensor.complete_measurement()

    res = VL53ResultsData()
    st = d.VL53L4CD_GetResult(DEVICE_INSTANCE, ct.byref(res))
    rep.check("GetResult returns 0", st == 0,
              observed=f"returned {st}", expected="0")

    dist_bytes = sil.sensor.reg_bytes(REG_RESULT_DISTANCE, 2)
    rep.check(
        "distance_mm matches the measured distance",
        res.distance_mm == truth["distance_mm"],
        observed=(f"sensor measured {truth['distance_mm']} mm; device register "
                  f"holds bytes [{dist_bytes[0]:02X} {dist_bytes[1]:02X}] "
                  f"(MSB first); driver returned {res.distance_mm} mm"),
        expected=f"{truth['distance_mm']} mm")
    rep.check("range_status maps to 0 (valid)",
              res.range_status == 0,
              observed=f"range_status={res.range_status}",
              expected="0")
    rep.check(
        "signal_rate_kcps matches",
        res.signal_rate_kcps == truth["signal_kcps"],
        observed=f"sensor measured {truth['signal_kcps']} kcps; driver returned "
                 f"{res.signal_rate_kcps} kcps",
        expected=f"{truth['signal_kcps']} kcps")
    rep.check(
        "number_of_spad matches",
        res.number_of_spad == truth["spads"],
        observed=f"sensor used {truth['spads']} SPADs; driver returned "
                 f"{res.number_of_spad}",
        expected=f"{truth['spads']}")


@sil_test("i2c.byte_order_boundaries",
          "asymmetric boundary values through WrWord/RdWord/WrDWord/RdDWord")
def t_byte_order_boundaries(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    SCRATCH = 0x0072      # THRESH_HIGH: harmless scratch register
    values = [0x0000, 0x0001, 0x00FF, 0x0100, 0x1234, 0x8000, 0xFFFF]

    wr_bad, wr_lines = [], []
    for v in values:
        d.VL53L4CD_WrWord(DEVICE_INSTANCE, SCRATCH, v)
        stored = sil.sensor.reg_bytes(SCRATCH, 2)
        want = [(v >> 8) & 0xFF, v & 0xFF]
        if stored != want:
            wr_bad.append(v)
            wr_lines.append(f"0x{v:04X} stored as [{stored[0]:02X} {stored[1]:02X}], "
                            f"device convention is [{want[0]:02X} {want[1]:02X}]")
    rep.check(
        "WrWord: device receives MSB-first for all boundary values",
        not wr_bad,
        observed=(f"{len(wr_bad)}/{len(values)} values arrive byte-swapped: "
                  + "; ".join(wr_lines)) if wr_bad else "all values stored MSB-first",
        expected="MSB first on the wire for every value (Table 8)",
        note="symmetric values (0x0000, 0xFFFF) cannot reveal a swap; "
             "the asymmetric ones can")

    rd_bad, rd_lines = [], []
    for v in values:
        sil.sensor._store(SCRATCH, v, 2)      # ground truth, MSB-first
        out = ct.c_uint16(0)
        d.VL53L4CD_RdWord(DEVICE_INSTANCE, SCRATCH, ct.byref(out))
        if out.value != v:
            rd_bad.append(v)
            rd_lines.append(f"wrote 0x{v:04X}, driver read back 0x{out.value:04X}")
    rep.check(
        "RdWord: driver reconstructs the value the device holds",
        not rd_bad,
        observed=(f"{len(rd_bad)}/{len(values)} values differ: "
                  + "; ".join(rd_lines)) if rd_bad else "all values match",
        expected="read-back equals the device value for every boundary value")

    sil.sensor._store(0x00DE, 0x1234, 2)  # leave scratch; use calib reg pair
    dw = ct.c_uint32(0)
    sil.sensor._store(REG_INTERMEASUREMENT_MS, 0x12345678, 4)
    d.VL53L4CD_RdDWord(DEVICE_INSTANCE, REG_INTERMEASUREMENT_MS, ct.byref(dw))
    rep.check(
        "RdDWord(0x12345678) round-trip",
        dw.value == 0x12345678,
        observed=f"device holds bytes [12 34 56 78]; driver returned 0x{dw.value:08X}",
        expected="0x12345678 (4 bytes, MSB first)")
    d.VL53L4CD_WrDWord(DEVICE_INSTANCE, REG_INTERMEASUREMENT_MS, 0x12345678)
    stored = sil.sensor.reg_bytes(REG_INTERMEASUREMENT_MS, 4)
    rep.check(
        "WrDWord(0x12345678) byte order",
        stored == [0x12, 0x34, 0x56, 0x78],
        observed="register holds [" + " ".join(f"{b:02X}" for b in stored) + "]",
        expected="[12 34 56 78]")


@sil_test("i2c.boot_timeout", "sensor never boots: init must fail, not hang")
def t_boot_timeout(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, boot_forever=True)
    st = sil.dll.VL53L4CD_SensorInit(DEVICE_INSTANCE)
    polls = len(sil.i2c_reads_of(REG_FIRMWARE_SYSTEM_STATUS))
    rep.check(
        "SensorInit returns a timeout error for a sensor stuck in boot",
        st != 0,
        observed=f"returned {st} after {polls} boot polls "
                 f"({sil.clock.now_ms} sim-ms)",
        expected="nonzero (VL53L4CD_ERROR_TIMEOUT is 255)")
    ok = sil.dll.sensor_start()
    rep.check(
        "sensor_start() reports the failure",
        bool(ok) is False,
        observed=f"sensor_start() returned {bool(ok)}",
        expected="False when SensorInit fails")
    rep.check("no ranging was started on the dead sensor",
              not sil.sensor.ranging,
              observed=f"ranging={sil.sensor.ranging}", expected="False")


@sil_test("i2c.command_writes", "Start/Stop/ClearInterrupt write the documented commands")
def t_command_writes(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)

    before = len(sil.log.entries)
    d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)
    v = _wr_value(sil, REG_SYSTEM_INTERRUPT_CLEAR)
    rep.check("ClearInterrupt writes 0x01 to SYSTEM__INTERRUPT_CLEAR",
              v == 0x01, observed=f"wrote {v!r}", expected="0x01")

    d.VL53L4CD_StartRanging(DEVICE_INSTANCE)
    v = _wr_value(sil, REG_SYSTEM_START)
    rep.check("StartRanging writes a start command to SYSTEM_START",
              v in (0x21, 0x40),
              observed=f"wrote 0x{v:02X}" if v is not None else "no write seen",
              expected="0x21 (continuous) or 0x40 (autonomous)")
    im_reads = [e for e in sil.log.entries[before:]
                if f"RD {reg_name(REG_INTERMEASUREMENT_MS)}" in e]
    rep.observe("StartRanging mode decision input",
                im_reads[-1] if im_reads else "no read of INTERMEASUREMENT_MS seen",
                note="the mode branch reads this 4-byte register; compare the "
                     "transferred length against the register width")

    d.VL53L4CD_StopRanging(DEVICE_INSTANCE)
    v = _wr_value(sil, REG_SYSTEM_START)
    rep.check("StopRanging writes 0x80 to SYSTEM_START",
              v == 0x80, observed=f"wrote 0x{v:02X}" if v is not None else "none",
              expected="0x80")


# ======================================================================
# irq.* -- interrupt path
# ======================================================================

@sil_test("irq.callback_direct", "EXTI callback logic sets data_ready (unit)")
def t_callback_direct(rep: Report):
    sil = Sil()
    rep.check("data_ready starts false", sil.data_ready is False,
              observed=str(sil.data_ready), expected="False")
    sil.dll.HAL_GPIO_EXTI_Callback(ct.c_uint16(GPIO_PIN_4))
    rep.check("callback on PA4 sets data_ready",
              sil.data_ready is True,
              observed=f"data_ready={sil.data_ready}", expected="True")
    sil.data_ready = False
    sil.dll.HAL_GPIO_EXTI_Callback(ct.c_uint16(0x0020))  # a different pin
    rep.check("callback on a different pin leaves data_ready untouched",
              sil.data_ready is False,
              observed=f"data_ready={sil.data_ready}", expected="False")


@sil_test("irq.gpio_config_capture", "real MX_GPIO_Init() configuration capture")
def t_gpio_config(rep: Report):
    sil = Sil()
    sil.dll.MX_GPIO_Init()
    cfg = sil.gpio.pa4_config()
    rep.check("MX_GPIO_Init configures PA4",
              cfg is not None,
              observed="no PA4 configuration recorded" if cfg is None else
                       f"pin=0x{cfg['pin']:04X} mode=0x{cfg['mode']:08X} pull={cfg['pull']}",
              expected="a HAL_GPIO_Init call covering PA4")
    if cfg:
        rep.check("PA4 is in an EXTI interrupt mode",
                  bool(cfg["mode"] & sil.gpio.EXTI_IT_BIT),
                  observed=sil.gpio.describe_mode(cfg["mode"]),
                  expected="an EXTI interrupt mode")
        rep.observe("captured PA4 trigger configuration",
                    sil.gpio.describe_mode(cfg["mode"]),
                    note="compare with how the sensor drives GPIO1 "
                         "(see irq.transition_integration)")


@sil_test("irq.transition_integration",
          "completed measurement -> GPIO1 transition -> EXTI4 -> data_ready")
def t_transition_integration(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)   # NO exti accommodation here
    d = sil.dll
    d.MX_GPIO_Init()                              # real PA4 config captured
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)
    sil.sensor.program_measurement(distance_mm=250)
    d.VL53L4CD_StartRanging(DEVICE_INSTANCE)
    sil.data_ready = False
    sil.sensor.complete_measurement()

    cfg = sil.gpio.pa4_config()
    pol = "active-low (asserts by driving GPIO1 high->low)" \
        if sil.sensor._active_low() else \
        "active-high (asserts by driving GPIO1 low->high)"
    gpio_evts = [e for e in sil.log.entries if "GPIO1" in e or "PA4" in e]
    rep.check(
        "a completed measurement raises data_ready via EXTI4",
        sil.data_ready is True,
        observed=("measurement completed; sensor interrupt is " + pol +
                  f"; PA4 is configured {sil.gpio.describe_mode(cfg['mode']) if cfg else 'not at all'}"
                  f"; EXTI4 fired {sil.gpio.exti_fired} time(s); "
                  f"data_ready={sil.data_ready}. GPIO evidence: " +
                  " | ".join(gpio_evts[-3:])),
        expected="data_ready == True after the sensor signals data-ready on GPIO1",
        note="GPIO_HV_MUX__CTRL bit 4 (written by the init sweep, value 0x11) "
             "selects the interrupt polarity the sensor uses")


@sil_test("irq.handshake_cycle",
          "main-loop handshake: consume, clear, re-arm, next event")
def t_handshake_cycle(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    rep.check("sensor_start() succeeds", bool(d.sensor_start()),
              observed="returned False", expected="True")

    sil.sensor.program_measurement(distance_mm=111)
    sil.sensor.complete_measurement()
    rep.check("event 1: data_ready set", sil.data_ready is True,
              observed=f"data_ready={sil.data_ready}", expected="True")

    clears_before = len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR))
    reads_before = len(sil.i2c_reads_of(REG_RESULT_DISTANCE))
    fired_before = sil.gpio.exti_fired
    d.sil_main_step()                       # verbatim main.c loop body
    fired_during = sil.gpio.exti_fired - fired_before
    gpio_evts = [e for e in sil.log.entries if "PA4 transition" in e][-2:]
    rep.check("loop iteration consumed the event (data_ready back to 0)",
              sil.data_ready is False,
              observed=(f"data_ready={sil.data_ready}; EXTI4 fired "
                        f"{fired_during} time(s) DURING the loop iteration "
                        f"itself; GPIO evidence: " + " | ".join(gpio_evts)),
              expected="False -- one event in, one consumed; the handshake's "
                       "own interrupt-clear should not raise a new event")
    rep.check("loop iteration read the result registers",
              len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) > reads_before,
              observed=f"{len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) - reads_before} "
                       "new distance read(s)",
              expected=">= 1")
    rep.check("loop iteration cleared the sensor interrupt",
              len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) > clears_before,
              observed=f"{len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) - clears_before} "
                       "new interrupt-clear write(s)",
              expected=">= 1")
    rep.check("GPIO1 released after clear",
              sil.sensor.gpio1_level == (1 if sil.sensor._active_low() else 0),
              observed=f"GPIO1 level = {sil.sensor.gpio1_level}",
              expected="idle level")

    sil.sensor.program_measurement(distance_mm=222)
    sil.sensor.complete_measurement()
    rep.check("event 2 after re-arm: data_ready set again",
              sil.data_ready is True,
              observed=f"data_ready={sil.data_ready}", expected="True")


@sil_test("irq.clear_semantics",
          "ClearInterrupt deasserts + re-arms; no spurious re-trigger")
def t_clear_semantics(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    d.sensor_start()
    sil.sensor.program_measurement(distance_mm=100)
    sil.sensor.complete_measurement()
    assert_level = 0 if sil.sensor._active_low() else 1
    rep.check("interrupt asserted after completion",
              sil.sensor.gpio1_level == assert_level,
              observed=f"GPIO1={sil.sensor.gpio1_level}",
              expected=f"{assert_level} (asserted)")

    d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)
    rep.check("clear deasserts GPIO1",
              sil.sensor.gpio1_level == 1 - assert_level,
              observed=f"GPIO1={sil.sensor.gpio1_level}",
              expected=f"{1 - assert_level} (idle)")

    completions = sil.sensor.completions
    half = sil.sensor.RANGING_PERIOD_MS // 2
    sil.clock.advance(half)
    rep.check("no new interrupt before the next measurement completes",
              sil.sensor.completions == completions and
              sil.sensor.gpio1_level == 1 - assert_level,
              observed=f"completions={sil.sensor.completions}, "
                       f"GPIO1={sil.sensor.gpio1_level} after {half} sim-ms",
              expected="unchanged until the ranging period elapses")

    sil.clock.advance(sil.sensor.RANGING_PERIOD_MS)
    rep.check("next completed measurement re-asserts the interrupt",
              sil.sensor.completions == completions + 1 and
              sil.sensor.gpio1_level == assert_level,
              observed=f"completions={sil.sensor.completions}, "
                       f"GPIO1={sil.sensor.gpio1_level}",
              expected=f"one more completion, GPIO1={assert_level}")


@sil_test("irq.missed_clear_stall",
          "without ClearInterrupt the sensor withholds further results")
def t_missed_clear(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)
    sil.sensor.program_measurement(distance_mm=100)
    d.VL53L4CD_StartRanging(DEVICE_INSTANCE)
    sil.sensor.complete_measurement()
    res = VL53ResultsData()
    d.VL53L4CD_GetResult(DEVICE_INSTANCE, ct.byref(res))   # read, NO clear

    completions = sil.sensor.completions
    sil.clock.advance(5 * sil.sensor.RANGING_PERIOD_MS)
    rep.check(
        "sensor produces no further results while the interrupt is uncleared",
        sil.sensor.completions == completions,
        observed=f"completions still {sil.sensor.completions} after "
                 f"{5 * sil.sensor.RANGING_PERIOD_MS} sim-ms without a clear",
        expected="no new completions (device ranging is on hold; "
                 "datasheet section 2.9)")
    d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)
    sil.clock.advance(2 * sil.sensor.RANGING_PERIOD_MS)
    rep.check("ranging resumes once the interrupt is cleared",
              sil.sensor.completions == completions + 1,
              observed=f"completions={sil.sensor.completions}",
              expected=f"{completions + 1}")


@sil_test("irq.event_loss",
          "interrupt, interrupt, step, step -- how many samples get processed?")
def t_event_loss(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    d.sensor_start()

    # interrupt #1
    sil.sensor.program_measurement(distance_mm=100)
    sil.sensor.complete_measurement()
    # interrupt #2 before the loop runs (sensor side re-armed directly,
    # simulating a loop that was busy elsewhere)
    d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)
    sil.sensor.program_measurement(distance_mm=200)
    sil.sensor.complete_measurement()
    events = sil.sensor.completions

    reads_before = len(sil.i2c_reads_of(REG_RESULT_DISTANCE))
    d.sil_main_step()      # step 1
    d.sil_main_step()      # step 2
    processed = len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) - reads_before

    rep.observe(
        "events produced vs. samples processed",
        f"{events} measurements completed and raised events; "
        f"{processed} sample(s) were processed by two loop iterations; "
        f"data_ready is declared 'bool' in main.c, so a second event while "
        f"the flag is already set leaves no record; the value read back was "
        f"{sil.data.distance_mm} (the later measurement)",
        note="not judged as a defect: 'latest sample wins' may be the "
             "intended policy for ride height -- but it should be a decision, "
             "not an accident")


@sil_test("irq.timing_boundary",
          "data-ready appears exactly at the ranging period, not before")
def t_timing_boundary(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)
    d.VL53L4CD_StartRanging(DEVICE_INSTANCE)
    d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)   # re-arm; next period starts
    period = sil.sensor.RANGING_PERIOD_MS
    completions = sil.sensor.completions

    sil.clock.advance(period - 1)
    rep.check("one tick BEFORE the period: no data-ready",
              sil.sensor.completions == completions,
              observed=f"completions={sil.sensor.completions} at t=+{period - 1}ms",
              expected=f"{completions} (nothing before {period}ms)")
    sil.clock.advance(1)
    rep.check("AT the period boundary: data-ready",
              sil.sensor.completions == completions + 1,
              observed=f"completions={sil.sensor.completions} at t=+{period}ms",
              expected=f"{completions + 1}")
    ready = ct.c_uint8(0)
    d.VL53L4CD_CheckForDataReady(DEVICE_INSTANCE, ct.byref(ready))
    rep.check("CheckForDataReady agrees with the pin-level state",
              ready.value == 1,
              observed=f"is_data_ready={ready.value}",
              expected="1")


# ======================================================================
# can.* -- FDCAN driver
# ======================================================================

def _can_handle(sil):
    h = sil.dll.CAN_create(sil.dll.sil_get_hfdcan1(), 0x0, 0x7FF)
    sil.dll.CAN_initialize(h)
    sil.dll.CAN_configureTransmission(h)
    return h


@sil_test("can.bringup", "CANDriver initialize/configure against the mock FDCAN")
def t_can_bringup(rep: Report):
    sil = Sil()
    ok = sil.dll.initializeCAN()
    rep.check("initializeCAN() returns true", bool(ok),
              observed=f"returned {bool(ok)}", expected="True")
    rep.check("Rx filter configured as a range filter to FIFO0",
              any(c["id1"] == 0x0 and c["id2"] == 0x7FF
                  for c in sil.bus.filter_configs),
              observed=str(sil.bus.filter_configs),
              expected="FilterID1=0x000, FilterID2=0x7FF")
    rep.check("peripheral started and Rx notification activated",
              "Start" in sil.bus.ops and "ActivateNotification" in sil.bus.ops,
              observed=f"ops seen: {sil.bus.ops}",
              expected="ConfigGlobalFilter, Start, ActivateNotification")


@sil_test("can.frame_content", "queued frame arrives with the same id/dlc/payload")
def t_can_frame(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    payload = (ct.c_uint8 * 5)(0x11, 0x22, 0x33, 0x44, 0x55)
    sil.dll.CAN_addMessageToQueue(h, 0x123, payload, 5)
    sil.dll.CAN_transmitMessage(h)
    rep.check("exactly one frame delivered", len(sil.bus.delivered) == 1,
              observed=f"{len(sil.bus.delivered)} frame(s)", expected="1")
    if sil.bus.delivered:
        f = sil.bus.delivered[0]
        rep.check("frame id", f["identifier"] == 0x123,
                  observed=f"0x{f['identifier']:03X}", expected="0x123")
        rep.check("frame dlc", f["nbytes"] == 5,
                  observed=str(f["nbytes"]), expected="5")
        rep.check("frame payload", f["payload"] == [0x11, 0x22, 0x33, 0x44, 0x55],
                  observed=str([f"{b:02X}" for b in f["payload"]]),
                  expected="['11','22','33','44','55']")
    rep.check("checkACK reports delivered", bool(sil.dll.CAN_checkACK(h)),
              observed="False", expected="True")


@sil_test("can.payload_boundaries", "payload lengths 0,1,2,8 and >8")
def t_can_boundaries(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    src = list(range(0x40, 0x50))
    for length, expect_len in [(0, 0), (1, 1), (2, 2), (8, 8), (12, 8)]:
        buf = (ct.c_uint8 * max(1, len(src)))(*src)
        before = len(sil.bus.delivered)
        sil.dll.CAN_addMessageToQueue(h, 0x200 + length, buf, length)
        sil.dll.CAN_transmitMessage(h)
        new = sil.bus.delivered[before:]
        f = new[0] if new else None
        rep.check(
            f"length {length}: emitted DLC",
            f is not None and f["nbytes"] == expect_len,
            observed="no frame" if f is None else f"dlc={f['nbytes']}",
            expected=f"{expect_len}" + (" (wrapper clamps >8 to 8)" if length > 8 else ""))
        if f:
            rep.check(
                f"length {length}: only the DLC bytes are carried, values intact",
                f["payload"] == src[:expect_len],
                observed=str([f"{b:02X}" for b in f["payload"]]),
                expected=str([f"{b:02X}" for b in src[:expect_len]]))


@sil_test("can.fifo_vs_ack", "FIFO acceptance is not delivery: no-ACK bus")
def t_fifo_vs_ack(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    sil.bus.node_acks = False          # nobody on the bus acknowledges
    payload = (ct.c_uint8 * 2)(0xAA, 0xBB)
    sil.dll.CAN_addMessageToQueue(h, 0x300, payload, 2)
    ret = sil.dll.CAN_transmitMessage(h)

    rep.check("frame was FIFO-accepted (HAL add returned OK)",
              len(sil.bus.accepted) == 1 and bool(ret),
              observed=f"accepted={len(sil.bus.accepted)}, transmitMessage={bool(ret)}",
              expected="accepted=1")
    rep.check("but nothing was delivered",
              len(sil.bus.delivered) == 0,
              observed=f"delivered={len(sil.bus.delivered)}, TXBRP=0x{sil.bus.txbrp():X}, "
                       f"TXBTO=0x{sil.bus.txbto():X}, LastErrorCode={sil.bus.last_error_code} "
                       f"(3=ACK error), TxErrorCnt={sil.bus.tx_error_cnt}",
              expected="0 delivered; request still pending")
    rep.check("checkACK() distinguishes queued from delivered",
              not sil.dll.CAN_checkACK(h),
              observed=f"checkACK returned {bool(sil.dll.CAN_checkACK(h))}",
              expected="False while the frame is pending but never ACKed")

    sil.bus.node_acks = True           # a node appears; retransmission succeeds
    sil.bus._attempt_transmissions()
    rep.check("after a node ACKs, checkACK turns true",
              bool(sil.dll.CAN_checkACK(h)),
              observed=f"delivered={len(sil.bus.delivered)}",
              expected="True")


@sil_test("can.queue_full", "51st message is rejected at MAX_QUEUE_CAPACITY=50")
def t_queue_full(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    payload = (ct.c_uint8 * 2)(1, 2)
    results = [bool(sil.dll.CAN_addMessageToQueue(h, 0x100 + i, payload, 2))
               for i in range(51)]
    rep.check("first 50 enqueues accepted", all(results[:50]),
              observed=f"{sum(results[:50])}/50 accepted", expected="50/50")
    rep.check("51st enqueue rejected", results[50] is False,
              observed=f"returned {results[50]}", expected="False")
    rep.check("queue size is 50", sil.dll.sil_can_queue_size(h) == 50,
              observed=str(sil.dll.sil_can_queue_size(h)), expected="50")


@sil_test("can.transmit_on_empty", "transmitMessage() on an empty queue")
def t_transmit_empty(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    size_before = sil.dll.sil_can_queue_size(h)
    ret = sil.dll.CAN_transmitMessage(h)
    size_after = sil.dll.sil_can_queue_size(h)

    rep.check(
        "queue size stays valid after transmitting from an empty queue",
        size_after >= 0,
        observed=f"queue size went {size_before} -> {size_after}; "
                 f"transmitMessage returned {bool(ret)}",
        expected="size >= 0 (an empty queue holds nothing to transmit)")
    if sil.bus.accepted:
        f = sil.bus.accepted[-1]
        rep.observe("what went on the bus",
                    f"a frame was emitted from the empty queue: "
                    f"ID=0x{f['identifier']:03X} dlc={f['nbytes']} "
                    f"payload={[f'{b:02X}' for b in f['payload']]} "
                    "(contents of an unused queue slot)")


@sil_test("can.hal_add_failure", "HAL rejects the Tx FIFO add (injected)")
def t_hal_add_failure(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    payload = (ct.c_uint8 * 2)(0xDE, 0xAD)
    sil.dll.CAN_addMessageToQueue(h, 0x400, payload, 2)
    sil.bus.inject_add_status = HAL_ERROR
    ret = sil.dll.CAN_transmitMessage(h)
    rep.check(
        "transmitMessage() reports the HAL failure",
        ret is False or ret == 0,
        observed=f"HAL_FDCAN_AddMessageToTxFifoQ returned 1 (HAL_ERROR); "
                 f"transmitMessage() returned {bool(ret)}; "
                 f"delivered={len(sil.bus.delivered)}",
        expected="False when the frame never entered the Tx FIFO")


@sil_test("can.fifo_ordering", "queued frames are transmitted in FIFO order")
def t_can_ordering(rep: Report):
    sil = Sil()
    h = _can_handle(sil)
    ids = [0x201, 0x202, 0x203]
    for i, ident in enumerate(ids):
        payload = (ct.c_uint8 * 1)(i)
        sil.dll.CAN_addMessageToQueue(h, ident, payload, 1)
    for _ in ids:
        sil.dll.CAN_transmitMessage(h)
    seen = [f["identifier"] for f in sil.bus.delivered]
    rep.check("frames leave in the order they were queued",
              seen == ids,
              observed=f"delivered order: {[hex(i) for i in seen]}",
              expected=f"{[hex(i) for i in ids]}")
    rep.check("queue drained to zero",
              sil.dll.sil_can_queue_size(h) == 0,
              observed=str(sil.dll.sil_can_queue_size(h)), expected="0")


# ======================================================================
# sys.* -- BoardManager integration / end-to-end
# ======================================================================

@sil_test("sys.startup_as_wired", "what the production startup actually does")
def t_startup_as_wired(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    # main.c order: MX_GPIO_Init, MX_FDCAN1_Init, MX_I2C2_Init, MX_USART1,
    # then the initializeCAN() retry loop. main.c/i2c.c/fdcan.c are not
    # linked; MX_I2C2/FDCAN1 are SIL no-ops (models replace them).
    d.MX_GPIO_Init()                    # real (Core/Src/gpio.c)
    ok = d.initializeCAN()              # real (Core/Src/BoardManager.c)
    rep.check("initializeCAN() succeeds at startup", bool(ok),
              observed=f"returned {bool(ok)}", expected="True")
    rep.observe(
        "sensor state after the as-wired startup",
        f"ranging={sil.sensor.ranging}; "
        f"SYSTEM_START writes seen: {len(sil.i2c_writes_to(REG_SYSTEM_START))}; "
        f"I2C transactions total: {len([e for e in sil.log.entries if ' i2c ' in e or e.split()[1] == 'i2c'])}",
        note="main() calls initializeCAN() but nothing in main.c calls "
             "sensor_start(); with the sensor never started, no measurement "
             "ever completes and data_ready can never be raised by the sensor")
    sil.clock.advance(100)
    rep.observe("after 100 sim-ms of main-loop time",
                f"completions={sil.sensor.completions}, data_ready={sil.data_ready}")


@sil_test("sys.sensor_start_success", "sensor_start() brings the sensor to ranging")
def t_sensor_start_success(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    ok = sil.dll.sensor_start()
    rep.check("sensor_start() returns true", bool(ok),
              observed=f"returned {bool(ok)}", expected="True")
    v = _wr_value(sil, REG_SYSTEM_START)
    rep.check("sensor is ranging afterwards",
              sil.sensor.ranging,
              observed=f"model ranging={sil.sensor.ranging}; last SYSTEM_START "
                       f"write: {f'0x{v:02X}' if v is not None else 'none'}",
              expected="ranging=True")


@sil_test("sys.sensor_start_injected_failure",
          "sensor_start() when StartRanging cannot talk to the sensor")
def t_sensor_start_failure(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    # StartRanging begins by READING INTERMEASUREMENT_MS; SensorInit only
    # WRITES it. Failing reads of that register therefore hits StartRanging
    # and nothing before it.
    sil.sensor.add_fault(REG_INTERMEASUREMENT_MS, op="read", status=HAL_ERROR)
    ret = sil.dll.sensor_start()
    fault_hits = sil.log.find("injected fault")
    rep.check(
        "sensor_start() reports the StartRanging failure",
        bool(ret) is False,
        observed=(f"the read inside VL53L4CD_StartRanging failed (evidence: "
                  f"{fault_hits[-1] if fault_hits else 'none'}); "
                  f"sensor_start() returned {bool(ret)}"),
        expected="False when starting the ranging session fails",
        note="compare what sensor_start() returns on the SensorInit-failed "
             "branch vs. the StartRanging-failed branch in BoardManager.c")


@sil_test("sys.push_on_bus", "push_on_bus() emits the front ride-height frame")
def t_push_on_bus(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    rep.check("initializeCAN() (prerequisite: sets can_handle)",
              bool(d.initializeCAN()), observed="False", expected="True")
    payload_value = 0x01AB
    ret = d.push_on_bus(payload_value)
    rep.check("push_on_bus() returns true", bool(ret),
              observed=f"returned {bool(ret)}", expected="True")
    rep.check("exactly one frame on the bus", len(sil.bus.delivered) == 1,
              observed=f"{len(sil.bus.delivered)}", expected="1")
    if sil.bus.delivered:
        f = sil.bus.delivered[0]
        rep.check("frame id is FRONT_CAN_ID",
                  f["identifier"] == FRONT_CAN_ID,
                  observed=f"0x{f['identifier']:03X}",
                  expected=f"0x{FRONT_CAN_ID:03X}")
        lo, hi = payload_value & 0xFF, payload_value >> 8
        rep.check(
            "first two payload bytes carry the 16-bit reading",
            f["payload"][:2] == [lo, hi],
            observed=str([f"{b:02X}" for b in f["payload"][:2]]),
            expected=f"['{lo:02X}', '{hi:02X}'] (little-endian uint16)")
        rep.check(
            "frame DLC matches the size of the value being sent",
            f["nbytes"] == 2,
            observed=(f"push_on_bus passed length PAYLOAD_LENGTH=16 for a "
                      f"2-byte uint16_t; the wrapper clamped 16 to 8; the "
                      f"emitted frame has DLC={f['nbytes']}. The source object "
                      f"holds 2 valid bytes, so {f['nbytes'] - 2} byte(s) were "
                      f"read from memory beyond it (observed values: "
                      f"{[f'{b:02X}' for b in f['payload'][2:]]}) -- an "
                      "out-of-bounds read on the sender and stale bytes on "
                      "the bus"),
            expected="2 -- the payload object is a uint16_t (2 bytes)",
            note="worth deciding what unit PAYLOAD_LENGTH is meant to be in, "
                 "and what the receiving node will assume the frame layout is")


@sil_test("sys.e2e_component_assisted",
          "sensor -> interrupt -> main loop -> (CAN?) with real entry points")
def t_e2e(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    rep.check("initializeCAN()", bool(d.initializeCAN()),
              observed="False", expected="True")
    rep.check("sensor_start()", bool(d.sensor_start()),
              observed="False", expected="True")

    truth_mm = 300
    sil.sensor.program_measurement(distance_mm=truth_mm, user_status=0)
    sil.sensor.complete_measurement()
    rep.check("interrupt raised data_ready", sil.data_ready is True,
              observed=f"data_ready={sil.data_ready}", expected="True")

    frames_before = len(sil.bus.delivered)
    d.sil_main_step()

    rep.check("result registers were read",
              len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) >= 1 and
              len(sil.i2c_reads_of(REG_RESULT_RANGE_STATUS)) >= 1,
              observed=f"distance reads={len(sil.i2c_reads_of(REG_RESULT_DISTANCE))}, "
                       f"status reads={len(sil.i2c_reads_of(REG_RESULT_RANGE_STATUS))}",
              expected=">= 1 of each")
    rep.check(
        "decoded distance equals the measured distance",
        sil.data.distance_mm == truth_mm,
        observed=f"sensor measured {truth_mm} mm; data.distance_mm={sil.data.distance_mm}",
        expected=f"{truth_mm} mm")
    rep.check("interrupt was cleared by the loop",
              len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) >= 2,
              observed=f"{len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR))} "
                       "clear write(s) total (>=1 from init, >=1 from the loop)",
              expected=">= 2")
    rep.observe(
        "CAN bus during the loop iteration",
        f"{len(sil.bus.delivered) - frames_before} frame(s) emitted",
        note="get_data_it() prints the distance but contains no call into the "
             "CAN path; push_on_bus() exists in BoardManager.c and is tested "
             "separately (sys.push_on_bus)")

    # terminal output is asserted only when the worker's capture works;
    # otherwise it is dropped from the chain rather than silently passed.
    import os
    cap_path = os.environ.get("SIL_CAPTURE_PATH")
    captured = ""
    if cap_path and os.path.isfile(cap_path):
        sil.dll.sil_flush()
        try:
            with open(cap_path, "r", errors="replace") as f:
                captured = f.read()
        except OSError:
            captured = ""
    if captured:
        line = next((ln for ln in captured.splitlines() if "Distance" in ln
                     or "Measurement" in ln), "")
        rep.check("terminal output evidence",
                  ("Distance:" in captured) or ("Measurement Error" in captured),
                  observed=f"captured printf line: {line!r}",
                  expected="a 'Distance: <mm> (mm)' line for a valid measurement")
    else:
        rep.observe("terminal output", "stdout capture unavailable in this run; "
                                       "printf evidence dropped from the chain")


@sil_test("sys.fault_propagation_i2c", "injected I2C faults propagate as statuses")
def t_fault_propagation(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED)
    d = sil.dll
    d.VL53L4CD_SensorInit(DEVICE_INSTANCE)

    sil.sensor.add_fault(REG_RESULT_RANGE_STATUS, op="read",
                         status=HAL_ERROR, times=1)
    res = VL53ResultsData()
    st = d.VL53L4CD_GetResult(DEVICE_INSTANCE, ct.byref(res))
    rep.check("GetResult propagates a NACKed read as a nonzero status",
              st != 0,
              observed=f"platform shim returned 255 for the faulted read; "
                       f"GetResult returned {st}",
              expected="nonzero")

    sil.sensor.add_fault(REG_SYSTEM_INTERRUPT_CLEAR, op="write",
                         status=HAL_TIMEOUT, times=1)
    st = d.VL53L4CD_ClearInterrupt(DEVICE_INSTANCE)
    rep.check("ClearInterrupt propagates a timed-out write",
              st != 0,
              observed=f"returned {st}", expected="nonzero")

    from models import HAL_BUSY
    sil.sensor.add_fault(REG_IDENTIFICATION_MODEL_ID, op="read",
                         status=HAL_BUSY, times=1)
    sensor_id = ct.c_uint16(0)
    st = d.VL53L4CD_GetSensorId(DEVICE_INSTANCE, ct.byref(sensor_id))
    rep.check("GetSensorId propagates a busy bus",
              st != 0,
              observed=f"HAL returned HAL_BUSY; GetSensorId returned {st}",
              expected="nonzero")


@sil_test("sys.e2e_strict",
          "STRICT end-to-end: firmware alone must move a measurement to CAN")
def t_e2e_strict(rep: Report):
    """
    Python performs ONLY what the outside world does: it lets the firmware
    run its own startup (the calls main.c actually makes) and advances time.
    No component is invoked on the firmware's behalf. The contract comes
    from the firmware's own stated intent: get_data_it() carries the comment
    '//send data along CAN', and BoardManager.h defines FRONT_CAN_ID for it.
    """
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll

    # -- the startup main.c actually performs (see main():99-108) --
    d.MX_GPIO_Init()                    # real gpio.c
    started = d.initializeCAN()         # real BoardManager.c
    rep.check("startup: initializeCAN()", bool(started),
              observed=f"returned {bool(started)}", expected="True")

    # -- the outside world: a target in front of the sensor + passing time --
    sil.sensor.program_measurement(distance_mm=180, user_status=0)
    frames_before = len(sil.bus.delivered)
    for _ in range(50):                 # 50 main-loop iterations, 1ms apart
        sil.clock.advance(1)
        d.sil_main_step()

    chain = []
    chain.append(("sensor began ranging", sil.sensor.ranging or
                  sil.sensor.completions > 0,
                  f"ranging={sil.sensor.ranging}, completions={sil.sensor.completions}, "
                  f"SYSTEM_START ranging writes={sum(1 for e in sil.i2c_writes_to(REG_SYSTEM_START) if ('21' in e.split('bytes=[')[-1] or '40' in e.split('bytes=[')[-1]))}"))
    chain.append(("a measurement completed", sil.sensor.completions > 0,
                  f"completions={sil.sensor.completions}"))
    chain.append(("data_ready was raised", sil.data_ready or
                  len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) > 0,
                  f"data_ready={sil.data_ready}"))
    chain.append(("the loop read the result",
                  len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) > 0,
                  f"distance reads={len(sil.i2c_reads_of(REG_RESULT_DISTANCE))}"))
    chain.append((f"a CAN frame with the reading appeared at 0x{FRONT_CAN_ID:03X}",
                  len(sil.bus.delivered) > frames_before,
                  f"frames delivered={len(sil.bus.delivered) - frames_before}"))

    first_break = next((name for name, ok, _ in chain if not ok), None)
    evidence = "; ".join(f"{name}: {info}" for name, _, info in chain)
    rep.check(
        "complete chain: sensor measurement -> CAN frame, firmware-driven",
        first_break is None,
        observed=f"chain broke at: '{first_break}'. Full chain state: {evidence}",
        expected="every link present with no Python assistance",
        note="contract source: the '//send data along CAN' comment in "
             "get_data_it() and the unused-in-main sensor_start()/push_on_bus() "
             "in BoardManager.c. Compare this with sys.e2e_component_assisted, "
             "where Python starts the sensor by hand and the chain runs "
             "until its next missing link.")


# ======================================================================
# scn.* -- scenario tests (realistic multi-step situations)
# ======================================================================

@sil_test("scn.normal_100_cycles", "100 measurement cycles without a reset")
def t_scn_100_cycles(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    d.initializeCAN()
    rep.check("sensor_start()", bool(d.sensor_start()),
              observed="returned False", expected="True")

    mismatches = 0
    stalls = 0
    for i in range(100):
        truth = 50 + 3 * i
        sil.sensor.program_measurement(distance_mm=truth)
        sil.sensor.complete_measurement()
        if not sil.data_ready:
            stalls += 1
            continue
        d.sil_main_step()
        if sil.data.distance_mm != truth:
            mismatches += 1

    rep.check("all 100 cycles completed (no stall/deadlock)",
              sil.sensor.completions >= 100 and stalls == 0,
              observed=f"completions={sil.sensor.completions}, "
                       f"cycles with data_ready never set: {stalls}",
              expected="100 completions, 0 stalls")
    rep.check("interrupt cleared on every cycle",
              len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) >= 100,
              observed=f"{len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR))} "
                       "clear writes",
              expected=">= 100")
    rep.observe("decoded values over the run",
                f"{mismatches}/100 decoded distances differed from the "
                "measured distance (value decoding has its own test: "
                "i2c.get_result_roundtrip)")


@sil_test("scn.sensor_absent_boot", "sensor missing from the bus at power-up")
def t_scn_sensor_absent(rep: Report):
    sil = Sil()          # STRICT addressing: nothing answers the driver
    ok = sil.dll.sensor_start()
    rep.check(
        "sensor_start() fails cleanly with no sensor on the bus",
        bool(ok) is False,
        observed=f"every transaction NACKed; sensor_start() returned {bool(ok)}",
        expected="False")
    rep.check("no ranging state was entered",
              not sil.sensor.ranging,
              observed=f"ranging={sil.sensor.ranging}", expected="False")
    rep.check("data_ready untouched",
              sil.data_ready is False,
              observed=f"data_ready={sil.data_ready}", expected="False")
    rep.check("nothing was sent on CAN",
              len(sil.bus.delivered) == 0,
              observed=f"{len(sil.bus.delivered)} frame(s)", expected="0")


@sil_test("scn.transient_i2c_fault_recovery",
          "one transient I2C timeout mid-stream, then a clean cycle")
def t_scn_transient(rep: Report):
    sil = Sil(accept_raw_devaddr=ACCOMMODATED, exti_force_fire=True)
    d = sil.dll
    d.MX_GPIO_Init()
    d.sensor_start()

    # cycle 1: the status read times out ONCE
    sil.sensor.program_measurement(distance_mm=140)
    sil.sensor.complete_measurement()
    sil.sensor.add_fault(REG_RESULT_RANGE_STATUS, op="read",
                         status=HAL_TIMEOUT, times=1)
    frames_before = len(sil.bus.delivered)
    d.sil_main_step()
    rep.observe(
        "cycle with the fault",
        f"GetResult's first read timed out; get_data_it() does not check "
        f"GetResult's return status; data.range_status={sil.data.range_status} "
        f"and data.distance_mm={sil.data.distance_mm} were consumed anyway; "
        f"{len(sil.bus.delivered) - frames_before} CAN frame(s) sent",
        note="the values written into `data` on the failed cycle came from a "
             "call that reported failure -- whether that is acceptable is a "
             "policy decision the firmware should make explicitly")

    # cycle 2: clean. Recovery is judged by the loop's I2C activity, not by
    # the data_ready flag state -- the flag's post-clear behavior is the
    # subject of irq.handshake_cycle and would double-report here.
    sil.sensor.program_measurement(distance_mm=160)
    sil.sensor.complete_measurement()
    raised = sil.data_ready
    reads_before = len(sil.i2c_reads_of(REG_RESULT_DISTANCE))
    clears_before = len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR))
    d.sil_main_step()
    rep.check(
        "the next cycle proceeds normally after the transient fault",
        raised and
        len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) > reads_before and
        len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) > clears_before,
        observed=f"event raised={raised}; new result reads="
                 f"{len(sil.i2c_reads_of(REG_RESULT_DISTANCE)) - reads_before}; "
                 f"new interrupt clears="
                 f"{len(sil.i2c_writes_to(REG_SYSTEM_INTERRUPT_CLEAR)) - clears_before}; "
                 f"completions={sil.sensor.completions}",
        expected="event raised, result read, interrupt cleared -- no lasting "
                 "stall from one transient fault")
