"""
harness.py -- loads the SIL DLL, wires the Python models to the mock HAL
callbacks, and provides the check/report primitives used by tests.

Verdicts:
  PASS    conformance check met
  FAIL    conformance mismatch (affects exit code)
  OBSERVE characterization only (exit-neutral)
The worker adds machine-readable accommodation metadata; the parent runner
adds ERROR (worker crash) and TIMEOUT (watchdog).
"""

from __future__ import annotations

import ctypes as ct
import os

from models import (SimClock, TransactionLog, VL53Model, CanBusModel,
                    GpioExtiModel, HAL_OK)

SIL_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(SIL_DIR, "build", "minirideheight_sil.dll")

MSYS_BIN_DIRS = [r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin"]

def _load_dll() -> ct.CDLL:
    """Load the built host DLL, including its static MinGW dependencies."""
    if not os.path.isfile(DLL_PATH):
        raise RuntimeError(f"SIL DLL missing: {DLL_PATH}. Run: python sil/build.py")
    try:
        return ct.CDLL(DLL_PATH)
    except OSError:
        for directory in MSYS_BIN_DIRS:
            if os.path.isdir(directory):
                os.add_dll_directory(directory)
        return ct.CDLL(DLL_PATH)


def _compiled_u32(dll: ct.CDLL, symbol: str) -> int:
    """Read a fixed-width constant exported by sil_probe.cpp."""
    fn = getattr(dll, symbol)
    fn.argtypes = []
    fn.restype = ct.c_uint32
    return int(fn())


# These values come from the headers used to compile the current DLL.  They
# are intentionally not duplicated as Python literals.
_CONSTANT_PROBE_DLL = _load_dll()
DEVICE_INSTANCE = _compiled_u32(_CONSTANT_PROBE_DLL, "sil_fw_device_instance")
FRONT_CAN_ID = _compiled_u32(_CONSTANT_PROBE_DLL, "sil_fw_front_can_id")
GPIO_PIN_4 = _compiled_u32(_CONSTANT_PROBE_DLL, "sil_fw_gpio_pin_4")
PAYLOAD_LENGTH = _compiled_u32(_CONSTANT_PROBE_DLL, "sil_fw_payload_length")


class VL53ResultsData(ct.Structure):
    """Mirror of VL53L4CD_ResultsData_t (natural alignment matches C)."""
    _fields_ = [
        ("range_status", ct.c_uint8),
        ("distance_mm", ct.c_uint16),
        ("ambient_rate_kcps", ct.c_uint32),
        ("ambient_per_spad_kcps", ct.c_uint32),
        ("signal_rate_kcps", ct.c_uint32),
        ("signal_per_spad_kcps", ct.c_uint32),
        ("number_of_spad", ct.c_uint16),
        ("sigma_mm", ct.c_uint16),
    ]


# ---- callback C signatures (mirror sil_hal.h) ----
I2C_CB = ct.CFUNCTYPE(ct.c_int32, ct.c_uint16, ct.c_uint16, ct.c_uint16,
                      ct.POINTER(ct.c_uint8), ct.c_uint16, ct.c_int32)
I2C_READY_CB = ct.CFUNCTYPE(ct.c_int32, ct.c_uint16, ct.c_uint32, ct.c_uint32)
DELAY_CB = ct.CFUNCTYPE(None, ct.c_uint32)
GPIO_INIT_CB = ct.CFUNCTYPE(None, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_uint32)
FDCAN_ADD_CB = ct.CFUNCTYPE(ct.c_int32, ct.c_uint32, ct.c_uint32, ct.c_uint32,
                            ct.POINTER(ct.c_uint8))
FDCAN_FILTER_CB = ct.CFUNCTYPE(ct.c_int32, ct.c_uint32, ct.c_uint32, ct.c_uint32,
                               ct.c_uint32, ct.c_uint32, ct.c_uint32)
FDCAN_OP_CB = ct.CFUNCTYPE(ct.c_int32, ct.c_int32)
FDCAN_PROTOCOL_CB = ct.CFUNCTYPE(None, ct.POINTER(ct.c_uint32),
                                 ct.POINTER(ct.c_uint32), ct.POINTER(ct.c_uint32))
FDCAN_COUNTERS_CB = ct.CFUNCTYPE(None, ct.POINTER(ct.c_uint32))


class Check:
    def __init__(self, name: str, verdict: str, observed: str,
                 expected: str, note: str = "") -> None:
        self.name, self.verdict = name, verdict
        self.observed, self.expected, self.note = observed, expected, note

    def as_dict(self) -> dict:
        return dict(name=self.name, verdict=self.verdict, observed=self.observed,
                    expected=self.expected, note=self.note)


class Report:
    """Collects checks; the test verdict is derived from them."""

    def __init__(self) -> None:
        self.checks: list[Check] = []

    def check(self, name: str, ok: bool, observed: str, expected: str,
              note: str = "") -> bool:
        self.checks.append(Check(name, "PASS" if ok else "FAIL",
                                 observed, expected, note))
        return ok

    def observe(self, name: str, observed: str, note: str = "") -> None:
        self.checks.append(Check(name, "OBSERVE", observed, "", note))

    def verdict(self) -> str:
        if any(c.verdict == "FAIL" for c in self.checks):
            return "FAIL"
        if any(c.verdict == "PASS" for c in self.checks):
            return "PASS"
        return "OBSERVE"


LAST_SIL: "Sil | None" = None   # the worker reads log/accommodations from here


class Sil:
    """One loaded DLL instance + wired models. One per worker process."""

    def __init__(self, accept_raw_devaddr: set[int] | None = None,
                 exti_force_fire: bool = False,
                 boot_forever: bool = False,
                 sensor_present: bool = True) -> None:
        global LAST_SIL
        LAST_SIL = self
        self.dll = self._load()
        self.log = TransactionLog()
        self.clock = SimClock(self.log)
        self.sensor = VL53Model(self.clock, self.log,
                                accept_raw_devaddr=accept_raw_devaddr,
                                boot_forever=boot_forever,
                                sensor_present=sensor_present)
        self.bus = CanBusModel(self.log)
        self.gpio = GpioExtiModel(self.log, force_fire=exti_force_fire)
        self.accommodations = list(self.sensor.accommodations) + \
            list(self.gpio.accommodations)

        # wire the sensor's GPIO1 line to the EXTI decision, and EXTI to the DLL
        self.sensor.gpio1_listeners.append(self.gpio.on_gpio1_transition)
        # the force-fire accommodation follows the sensor's programmed
        # interrupt polarity: only the transition INTO the asserted level fires
        self.gpio.asserted_level = \
            lambda: 0 if self.sensor._active_low() else 1
        self.gpio.fire_exti = lambda pin: self.dll.HAL_GPIO_EXTI_Callback(
            ct.c_uint16(pin))
        # CAN model pushes TXBRP/TXBTO into the DLL-side registers
        self.bus.push_regs = lambda brp, bto: self.dll.sil_fdcan_set_regs(
            ct.c_uint32(brp), ct.c_uint32(bto))
        self.bus.push_regs(0, 0)

        self._wire_callbacks()
        self._declare_prototypes()

    # ------------------------------------------------------------------
    def _load(self) -> ct.CDLL:
        return _load_dll()

    def _wire_callbacks(self) -> None:
        # keep refs on self: ctypes callbacks are garbage-collected otherwise
        def i2c_cb(dev, reg, memsize, buf, length, is_write):
            try:
                if is_write:
                    data = [buf[i] for i in range(length)]
                    st, _ = self.sensor.i2c(dev, reg, length, True, data)
                else:
                    st, out = self.sensor.i2c(dev, reg, length, False, None)
                    if st == HAL_OK and out is not None:
                        for i, b in enumerate(out):
                            buf[i] = b
                return st
            except Exception as exc:                     # loud, not silent
                self.log.add("err", f"i2c model exception: {exc!r}")
                return 1

        def i2c_ready_cb(dev, trials, timeout_ms):
            try:
                return self.sensor.is_device_ready(dev, trials, timeout_ms)
            except Exception as exc:                     # loud, not silent
                self.log.add("err", f"i2c ready model exception: {exc!r}")
                return 1

        def delay_cb(ms):
            try:
                self.clock.advance(ms)
            except Exception as exc:
                self.log.add("err", f"clock exception: {exc!r}")

        def gpio_init_cb(port, pin, mode, pull):
            try:
                self.gpio.on_gpio_init(port, pin, mode, pull)
            except Exception as exc:
                self.log.add("err", f"gpio model exception: {exc!r}")

        def fdcan_add_cb(identifier, id_type, dlc_code, data):
            try:
                from models import DLC_CODE_TO_BYTES
                nbytes = DLC_CODE_TO_BYTES.get(dlc_code, 0)
                payload = [data[i] for i in range(nbytes)] if data else []
                return self.bus.add_to_tx_fifo(identifier, dlc_code, payload,
                                               id_type)
            except Exception as exc:
                self.log.add("err", f"can model exception: {exc!r}")
                return 1

        def fdcan_filter_cb(id_type, index, ftype, fconfig, id1, id2):
            try:
                return self.bus.config_filter(id_type, index, ftype, fconfig, id1, id2)
            except Exception as exc:
                self.log.add("err", f"can filter exception: {exc!r}")
                return 1

        def fdcan_op_cb(op):
            try:
                return self.bus.op(op)
            except Exception as exc:
                self.log.add("err", f"can op exception: {exc!r}")
                return 1

        def fdcan_protocol_cb(lec, bus_off, err_passive):
            try:
                l, b, e = self.bus.protocol_status()
                lec[0], bus_off[0], err_passive[0] = l, b, e
            except Exception as exc:
                self.log.add("err", f"can protocol exception: {exc!r}")

        def fdcan_counters_cb(tx_err):
            try:
                tx_err[0] = self.bus.counters()
            except Exception as exc:
                self.log.add("err", f"can counters exception: {exc!r}")

        self._cbs = [
            I2C_CB(i2c_cb), DELAY_CB(delay_cb), GPIO_INIT_CB(gpio_init_cb),
            FDCAN_ADD_CB(fdcan_add_cb), FDCAN_FILTER_CB(fdcan_filter_cb),
            FDCAN_OP_CB(fdcan_op_cb), FDCAN_PROTOCOL_CB(fdcan_protocol_cb),
            FDCAN_COUNTERS_CB(fdcan_counters_cb),
            I2C_READY_CB(i2c_ready_cb),
        ]
        d = self.dll
        d.sil_set_i2c_cb(self._cbs[0])
        d.sil_set_delay_cb(self._cbs[1])
        d.sil_set_gpio_init_cb(self._cbs[2])
        d.sil_set_fdcan_add_cb(self._cbs[3])
        d.sil_set_fdcan_filter_cb(self._cbs[4])
        d.sil_set_fdcan_op_cb(self._cbs[5])
        d.sil_set_fdcan_protocol_cb(self._cbs[6])
        d.sil_set_fdcan_counters_cb(self._cbs[7])
        d.sil_set_i2c_ready_cb(self._cbs[8])

    def _declare_prototypes(self) -> None:
        d = self.dll
        u8, u16, u32 = ct.c_uint8, ct.c_uint16, ct.c_uint32

        # ULD API (Dev_t is uint16_t; VL53L4CD_Error is uint8_t)
        for fn, args in {
            "VL53L4CD_SensorInit": [u16],
            "VL53L4CD_StartRanging": [u16],
            "VL53L4CD_StopRanging": [u16],
            "VL53L4CD_ClearInterrupt": [u16],
        }.items():
            getattr(d, fn).argtypes = args
            getattr(d, fn).restype = u8
        d.VL53L4CD_GetSensorId.argtypes = [u16, ct.POINTER(u16)]
        d.VL53L4CD_GetSensorId.restype = u8
        d.VL53L4CD_GetResult.argtypes = [u16, ct.POINTER(VL53ResultsData)]
        d.VL53L4CD_GetResult.restype = u8
        d.VL53L4CD_CheckForDataReady.argtypes = [u16, ct.POINTER(u8)]
        d.VL53L4CD_CheckForDataReady.restype = u8

        # platform shims (the student's platform.c, compiled as-is)
        d.VL53L4CD_RdByte.argtypes = [u16, u16, ct.POINTER(u8)]
        d.VL53L4CD_RdByte.restype = u8
        d.VL53L4CD_RdWord.argtypes = [u16, u16, ct.POINTER(u16)]
        d.VL53L4CD_RdWord.restype = u8
        d.VL53L4CD_RdDWord.argtypes = [u16, u16, ct.POINTER(u32)]
        d.VL53L4CD_RdDWord.restype = u8
        d.VL53L4CD_WrByte.argtypes = [u16, u16, u8]
        d.VL53L4CD_WrByte.restype = u8
        d.VL53L4CD_WrWord.argtypes = [u16, u16, u16]
        d.VL53L4CD_WrWord.restype = u8
        d.VL53L4CD_WrDWord.argtypes = [u16, u16, u32]
        d.VL53L4CD_WrDWord.restype = u8

        # BoardManager (C) + shim + probe
        d.initializeCAN.restype = ct.c_bool
        d.push_on_bus.argtypes = [u16]
        d.push_on_bus.restype = ct.c_bool
        d.sensor_start.restype = ct.c_bool
        d.get_data_it.restype = None
        d.sil_main_step.restype = None
        d.sil_main_start.argtypes = [u32]
        d.sil_main_start.restype = ct.c_int32
        d.sil_start_can_attempts.argtypes = []
        d.sil_start_can_attempts.restype = u32
        d.sil_start_sensor_attempts.argtypes = []
        d.sil_start_sensor_attempts.restype = u32
        d.MX_GPIO_Init.restype = None
        d.sil_can_queue_size.argtypes = [ct.c_void_p]
        d.sil_can_queue_size.restype = ct.c_int
        for name in ("sil_fw_device_instance", "sil_fw_front_can_id",
                     "sil_fw_gpio_pin_4", "sil_fw_payload_length"):
            getattr(d, name).argtypes = []
            getattr(d, name).restype = u32
        d.sil_get_hfdcan1.restype = ct.c_void_p
        d.HAL_GPIO_EXTI_Callback.argtypes = [u16]
        d.HAL_GPIO_EXTI_Callback.restype = None
        d.HAL_I2C_IsDeviceReady.argtypes = [ct.c_void_p, u16, u32, u32]
        d.HAL_I2C_IsDeviceReady.restype = ct.c_int32
        d.sil_flush.restype = None
        d.sil_fdcan_set_regs.argtypes = [u32, u32]
        d.sil_fdcan_set_regs.restype = None

        # CAN wrapper (extern "C")
        d.CAN_create.argtypes = [ct.c_void_p, u32, u32]
        d.CAN_create.restype = ct.c_void_p
        d.CAN_initialize.argtypes = [ct.c_void_p]
        d.CAN_initialize.restype = ct.c_bool
        d.CAN_configureTransmission.argtypes = [ct.c_void_p]
        d.CAN_configureTransmission.restype = ct.c_bool
        d.CAN_addMessageToQueue.argtypes = [ct.c_void_p, u32,
                                            ct.POINTER(u8), u8]
        d.CAN_addMessageToQueue.restype = ct.c_bool
        d.CAN_transmitMessage.argtypes = [ct.c_void_p]
        d.CAN_transmitMessage.restype = ct.c_bool
        d.CAN_checkACK.argtypes = [ct.c_void_p]
        d.CAN_checkACK.restype = ct.c_bool

    # ---------------- convenience accessors ----------------

    @property
    def data_ready(self) -> bool:
        return ct.c_bool.in_dll(self.dll, "data_ready").value

    @data_ready.setter
    def data_ready(self, v: bool) -> None:
        ct.c_bool.in_dll(self.dll, "data_ready").value = v

    @property
    def data(self) -> VL53ResultsData:
        return VL53ResultsData.in_dll(self.dll, "data")

    def i2c_writes_to(self, reg: int) -> list[str]:
        from models import reg_name
        return self.log.find(f"WR {reg_name(reg)}")

    def i2c_reads_of(self, reg: int) -> list[str]:
        from models import reg_name
        return self.log.find(f"RD {reg_name(reg)}")
