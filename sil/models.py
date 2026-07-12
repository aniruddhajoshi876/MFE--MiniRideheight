"""
models.py -- Python-side peripheral models for the SIL harness.

- SimClock:   simulated milliseconds, advanced by the mocked HAL_Delay.
- VL53Model:  I2C register model of the VL53L4CD (registers stored MSB-first
              per datasheet DS13812 Table 8; interrupt polarity derived from
              GPIO_HV_MUX__CTRL exactly as the ULD reads it).
- CanBusModel: FDCAN Tx model as an ordered per-buffer state machine
              (FIFO-accepted -> TXBRP pending -> attempted -> ACK/error ->
              TXBTO), so "queued" can never be conflated with "delivered".
- GpioExtiModel: records the REAL MX_GPIO_Init() configuration and decides
              whether a GPIO1 level transition fires EXTI4.

All activity lands in a shared TransactionLog used as failure evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---- HAL status codes (mirrors sil_hal.h) ----
HAL_OK, HAL_ERROR, HAL_BUSY, HAL_TIMEOUT = 0, 1, 2, 3

# ---- VL53L4CD registers (mirrors VL53L4CD_api.h) ----
REG_OSC_FREQ            = 0x0006
REG_GPIO_HV_MUX_CTRL    = 0x0030
REG_GPIO_TIO_HV_STATUS  = 0x0031
REG_SYSTEM_INTERRUPT_CLEAR = 0x0086
REG_SYSTEM_START        = 0x0087
REG_RESULT_RANGE_STATUS = 0x0089
REG_RESULT_SPAD_NB      = 0x008C
REG_RESULT_SIGNAL_RATE  = 0x008E
REG_RESULT_AMBIENT_RATE = 0x0090
REG_RESULT_SIGMA        = 0x0092
REG_RESULT_DISTANCE     = 0x0096
REG_INTERMEASUREMENT_MS = 0x006C
REG_OSC_CALIBRATE_VAL   = 0x00DE
REG_FIRMWARE_SYSTEM_STATUS = 0x00E5
REG_IDENTIFICATION_MODEL_ID = 0x010F

# ULD GetResult maps raw device status -> user status via this table
# (VL53L4CD_api.c, status_rtn). Inverse map for programming ground truth.
_STATUS_RTN = [255, 255, 255, 5, 2, 4, 1, 7, 3, 0, 255, 255, 9, 13,
               255, 255, 255, 255, 10, 6, 255, 255, 11, 12]
USER_TO_RAW_STATUS = {}
for raw, user in enumerate(_STATUS_RTN):
    USER_TO_RAW_STATUS.setdefault(user, raw)

REG_NAMES = {
    REG_OSC_FREQ: "OSC_FREQ",
    REG_GPIO_HV_MUX_CTRL: "GPIO_HV_MUX__CTRL",
    REG_GPIO_TIO_HV_STATUS: "GPIO__TIO_HV_STATUS",
    REG_SYSTEM_INTERRUPT_CLEAR: "SYSTEM__INTERRUPT_CLEAR",
    REG_SYSTEM_START: "SYSTEM_START",
    REG_RESULT_RANGE_STATUS: "RESULT__RANGE_STATUS",
    REG_RESULT_SPAD_NB: "RESULT__SPAD_NB",
    REG_RESULT_SIGNAL_RATE: "RESULT__SIGNAL_RATE",
    REG_RESULT_AMBIENT_RATE: "RESULT__AMBIENT_RATE",
    REG_RESULT_SIGMA: "RESULT__SIGMA",
    REG_RESULT_DISTANCE: "RESULT__DISTANCE",
    REG_INTERMEASUREMENT_MS: "INTERMEASUREMENT_MS",
    REG_OSC_CALIBRATE_VAL: "RESULT__OSC_CALIBRATE_VAL",
    REG_FIRMWARE_SYSTEM_STATUS: "FIRMWARE__SYSTEM_STATUS",
    REG_IDENTIFICATION_MODEL_ID: "IDENTIFICATION__MODEL_ID",
}


def reg_name(reg: int) -> str:
    return REG_NAMES.get(reg, f"0x{reg:04X}")


class TransactionLog:
    """Ordered record of every simulated bus/pin/time event."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def add(self, kind: str, text: str) -> None:
        self.entries.append(f"[{len(self.entries):04d}] {kind:5s} {text}")

    def tail(self, n: int = 25) -> list[str]:
        return self.entries[-n:]

    def find(self, needle: str) -> list[str]:
        return [e for e in self.entries if needle in e]


class SimClock:
    """Simulated milliseconds. HAL_Delay advances it; nothing else does."""

    def __init__(self, log: TransactionLog) -> None:
        self.now_ms = 0
        self.log = log
        self.listeners = []          # called with (now_ms) after each advance

    def advance(self, ms: int) -> None:
        self.now_ms += int(ms)
        for cb in list(self.listeners):
            cb(self.now_ms)


@dataclass
class Fault:
    """Injected I2C fault: match by op/reg, respond with a HAL status."""
    reg: int
    op: str                  # "read" | "write" | "any"
    status: int = HAL_ERROR  # HAL status to return
    remaining: int = -1      # -1 = persistent, N = fire N times

    def matches(self, reg: int, is_write: bool) -> bool:
        if self.remaining == 0:
            return False
        if self.reg is not None and self.reg != reg:
            return False
        if self.op == "read" and is_write:
            return False
        if self.op == "write" and not is_write:
            return False
        return True

    def consume(self) -> int:
        if self.remaining > 0:
            self.remaining -= 1
        return self.status


class VL53Model:
    """
    I2C register model of the VL53L4CD.

    Addressing: the device answers at 7-bit address DEVICE_7BIT (0x29, i.e.
    8-bit/DevAddress form 0x52). The HAL DevAddress argument is interpreted
    as the real HAL does: the wire 7-bit address is DevAddress >> 1. If
    `accept_raw_devaddr` contains a value, transactions whose *raw argument*
    equals it are also acknowledged -- an explicitly-logged SIL accommodation
    so downstream logic can be exercised in isolation.

    Multi-byte registers are stored MSB-first (datasheet Table 8).

    Timing: a measurement completes only when the simulated clock passes its
    deadline while the device is ranging and the interrupt is re-armed.
    Register writes never advance time.
    """

    DEVICE_7BIT = 0x29           # datasheet default: DevAddress form 0x52
    BOOT_MS = 2                  # sim-ms until FIRMWARE__SYSTEM_STATUS = 0x3
    RANGING_PERIOD_MS = 10       # sim-ms from start/re-arm to data-ready

    def __init__(self, clock: SimClock, log: TransactionLog,
                 accept_raw_devaddr: set[int] | None = None,
                 boot_forever: bool = False) -> None:
        self.clock = clock
        self.log = log
        self.boot_forever = boot_forever
        if boot_forever:
            log.add("note", "scenario: sensor never leaves boot state "
                            "(FIRMWARE__SYSTEM_STATUS stays 0x02)")
        self.accept_raw = accept_raw_devaddr or set()
        self.accommodations: list[str] = []
        if self.accept_raw:
            note = ("SIL accommodation: sensor model additionally answers raw "
                    f"DevAddress argument(s) {sorted(hex(a) for a in self.accept_raw)} "
                    "so downstream logic can be exercised; the address-convention "
                    "test runs WITHOUT this accommodation")
            self.accommodations.append(note)
            log.add("note", note)

        self.regs: dict[int, int] = {}
        self.faults: list[Fault] = []
        self.devaddr_args_seen: set[int] = set()
        self.nacked_args: set[int] = set()

        # --- power-on defaults ---
        self._store(REG_IDENTIFICATION_MODEL_ID, 0xEBAA, 2)  # model id 0xEBAA
        self.regs[REG_FIRMWARE_SYSTEM_STATUS] = 0x02          # not booted yet
        # OSC_FREQ: chosen so the ULD timing math stays well-defined (no
        # divide-by-zero / overflow-to-zero) regardless of the byte order the
        # platform layer produces -- the byte-order contradiction must surface
        # as a VALUE mismatch in the tests, not as a model-dependent crash.
        self._store(REG_OSC_FREQ, 0x0AC8, 2)
        self._store(REG_OSC_CALIBRATE_VAL, 0x0A40, 2)
        self.boot_deadline = self.BOOT_MS

        # --- measurement/interrupt state ---
        self.ranging = False
        self.armed = True
        self.interrupt_asserted = False
        self.inflight_deadline: int | None = None
        self.completions = 0
        self.pending = dict(distance_mm=0, user_status=0, signal_kcps=1024,
                            ambient_kcps=8, sigma_mm=4, spads=4)

        # physical GPIO1 line (open drain, idle high)
        self.gpio1_level = 1
        self.gpio1_listeners = []    # called with (old_level, new_level)

        clock.listeners.append(self._on_time)

    # ---------------- register byte store (MSB-first) ----------------

    def _store(self, reg: int, value: int, nbytes: int) -> None:
        for i in range(nbytes):
            shift = 8 * (nbytes - 1 - i)
            self.regs[reg + i] = (value >> shift) & 0xFF

    def reg_bytes(self, reg: int, nbytes: int) -> list[int]:
        return [self.regs.get(reg + i, 0) for i in range(nbytes)]

    def reg_value(self, reg: int, nbytes: int) -> int:
        v = 0
        for b in self.reg_bytes(reg, nbytes):
            v = (v << 8) | b
        return v

    # ---------------- interrupt polarity, exactly as the ULD reads it ----------------

    def _int_pol(self) -> int:
        # VL53L4CD_CheckForDataReady: bit4 of GPIO_HV_MUX__CTRL set -> pol 0
        return 0 if (self.regs.get(REG_GPIO_HV_MUX_CTRL, 0) & 0x10) else 1

    def _active_low(self) -> bool:
        # pol 0 == active low: interrupt asserted -> TIO bit0 = 0, GPIO1 = 0
        return self._int_pol() == 0

    def _tio_status_byte(self) -> int:
        base = self.regs.get(REG_GPIO_TIO_HV_STATUS, 0) & ~1
        pol = self._int_pol()
        bit0 = pol if self.interrupt_asserted else (1 - pol)
        return base | bit0

    def _set_gpio1(self, level: int, why: str) -> None:
        old = self.gpio1_level
        if level == old:
            return
        self.gpio1_level = level
        self.log.add("gpio", f"GPIO1 {old}->{level} ({why})")
        for cb in list(self.gpio1_listeners):
            cb(old, level)

    # ---------------- time-driven completion ----------------

    def _on_time(self, now: int) -> None:
        if self.boot_forever:
            pass
        elif self.boot_deadline is not None and now >= self.boot_deadline:
            self.regs[REG_FIRMWARE_SYSTEM_STATUS] = 0x03
            self.boot_deadline = None
            self.log.add("time", f"t={now}ms sensor booted (FIRMWARE__SYSTEM_STATUS=0x03)")
        if (self.ranging and self.armed and self.inflight_deadline is not None
                and now >= self.inflight_deadline):
            self._complete_measurement(now)

    def _complete_measurement(self, now: int) -> None:
        p = self.pending
        raw_status = USER_TO_RAW_STATUS.get(p["user_status"], p["user_status"])
        self._store(REG_RESULT_RANGE_STATUS, raw_status, 1)
        self._store(REG_RESULT_SPAD_NB, (p["spads"] * 256) & 0xFFFF, 2)
        self._store(REG_RESULT_SIGNAL_RATE, (p["signal_kcps"] // 8) & 0xFFFF, 2)
        self._store(REG_RESULT_AMBIENT_RATE, (p["ambient_kcps"] // 8) & 0xFFFF, 2)
        self._store(REG_RESULT_SIGMA, (p["sigma_mm"] * 4) & 0xFFFF, 2)
        self._store(REG_RESULT_DISTANCE, p["distance_mm"] & 0xFFFF, 2)
        self.completions += 1
        self.armed = False
        self.interrupt_asserted = True
        self.inflight_deadline = None
        self.log.add("time",
                     f"t={now}ms measurement #{self.completions} complete "
                     f"(distance={p['distance_mm']}mm user_status={p['user_status']}) "
                     "-> interrupt asserted")
        self._set_gpio1(0 if self._active_low() else 1, "measurement complete")

    # ---------------- test-facing API ----------------

    def program_measurement(self, distance_mm: int, user_status: int = 0,
                            signal_kcps: int = 2048, ambient_kcps: int = 8,
                            sigma_mm: int = 4, spads: int = 5) -> None:
        """Set the *pending* result. Does NOT raise any interrupt by itself."""
        self.pending = dict(distance_mm=distance_mm, user_status=user_status,
                            signal_kcps=signal_kcps, ambient_kcps=ambient_kcps,
                            sigma_mm=sigma_mm, spads=spads)
        self.log.add("note", f"pending measurement programmed: {self.pending}")

    def complete_measurement(self) -> None:
        """Advance the sim clock to the in-flight deadline (time-driven path)."""
        if self.inflight_deadline is None:
            self.log.add("note", "complete_measurement(): no measurement in flight "
                                 "(device not ranging or interrupt not cleared)")
            return
        delta = max(0, self.inflight_deadline - self.clock.now_ms)
        self.clock.advance(delta if delta > 0 else 1)

    def add_fault(self, reg: int, op: str, status: int = HAL_ERROR,
                  times: int = -1) -> None:
        self.faults.append(Fault(reg=reg, op=op, status=status, remaining=times))
        self.log.add("note", f"fault armed: {op} {reg_name(reg)} -> HAL status {status} "
                             f"({'persistent' if times < 0 else f'{times}x'})")

    # ---------------- the I2C transaction entry point ----------------

    def i2c(self, dev_addr: int, reg: int, length: int, is_write: bool,
            data: list[int] | None) -> tuple[int, list[int] | None]:
        """Returns (hal_status, read_bytes_or_None)."""
        self.devaddr_args_seen.add(dev_addr)

        wire7 = (dev_addr >> 1) & 0x7F
        acknowledged = (wire7 == self.DEVICE_7BIT) or (dev_addr in self.accept_raw)
        rw = "WR" if is_write else "RD"
        if not acknowledged:
            self.nacked_args.add(dev_addr)
            self.log.add("i2c", f"{rw} {reg_name(reg)} len={length} "
                                f"DevAddress=0x{dev_addr:02X} (wire 7-bit 0x{wire7:02X}) "
                                f"-> NACK (device 7-bit is 0x{self.DEVICE_7BIT:02X})")
            return HAL_ERROR, None

        for f in self.faults:
            if f.matches(reg, is_write):
                st = f.consume()
                self.log.add("i2c", f"{rw} {reg_name(reg)} len={length} "
                                    f"-> injected fault, HAL status {st}")
                return st, None

        if is_write:
            for i, b in enumerate(data or []):
                self.regs[reg + i] = b & 0xFF
            self.log.add("i2c", f"WR {reg_name(reg)} len={length} "
                                f"bytes=[{' '.join(f'{b:02X}' for b in (data or []))}]")
            self._write_side_effects(reg, data or [])
            return HAL_OK, None

        # read: TIO_HV_STATUS is live status, everything else is the byte store
        out = []
        for i in range(length):
            a = reg + i
            if a == REG_GPIO_TIO_HV_STATUS:
                out.append(self._tio_status_byte())
            else:
                out.append(self.regs.get(a, 0))
        self.log.add("i2c", f"RD {reg_name(reg)} len={length} "
                            f"bytes=[{' '.join(f'{b:02X}' for b in out)}]")
        return HAL_OK, out

    def _write_side_effects(self, reg: int, data: list[int]) -> None:
        span = range(reg, reg + len(data))

        if REG_SYSTEM_START in span:
            val = data[REG_SYSTEM_START - reg]
            if val in (0x21, 0x40):
                self.ranging = True
                self.armed = True
                self.inflight_deadline = self.clock.now_ms + self.RANGING_PERIOD_MS
                mode = "continuous" if val == 0x21 else "autonomous/VHV"
                self.log.add("note", f"SYSTEM_START=0x{val:02X} -> ranging started ({mode}), "
                                     f"ready at t={self.inflight_deadline}ms")
            elif val == 0x80:
                self.ranging = False
                self.inflight_deadline = None
                self.log.add("note", "SYSTEM_START=0x80 -> ranging stopped")

        if REG_SYSTEM_INTERRUPT_CLEAR in span:
            val = data[REG_SYSTEM_INTERRUPT_CLEAR - reg]
            if val & 0x01:
                was = self.interrupt_asserted
                self.interrupt_asserted = False
                self.armed = True
                self._set_gpio1(1 if self._active_low() else 0, "interrupt cleared")
                if self.ranging:
                    self.inflight_deadline = self.clock.now_ms + self.RANGING_PERIOD_MS
                self.log.add("note", f"interrupt clear (was {'asserted' if was else 'idle'}); "
                                     "re-armed" + (f", next ready t={self.inflight_deadline}ms"
                                                   if self.ranging else ""))


# ====================================================================
# FDCAN model
# ====================================================================

DLC_CODE_TO_BYTES = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
                     9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}

# CAN LastErrorCode values (matches the comment table in CANDriver.cpp)
LEC_NO_ERROR, LEC_ACK_ERROR, LEC_NO_CHANGE = 0, 3, 7


@dataclass
class TxBufferState:
    frame: dict | None = None
    pending: bool = False        # TXBRP bit
    transmitted: bool = False    # TXBTO bit


class CanBusModel:
    """
    Ordered per-buffer Tx state machine:
      FIFO-accepted -> request pending (TXBRP) -> transmission attempted
      -> ACK (TXBTO set, frame delivered) or ACK-error (TXBRP stays pending).

    TXBRP/TXBTO invariants hold PER BUFFER BIT; different buffers may hold
    different states simultaneously. Register state is pushed into the DLL
    after every event via `push_regs` (wired by the harness).
    """

    NUM_TX_BUFFERS = 3   # FDCAN Tx FIFO depth on STM32G4

    def __init__(self, log: TransactionLog) -> None:
        self.log = log
        self.buffers = [TxBufferState() for _ in range(self.NUM_TX_BUFFERS)]
        self.put_index = 0
        self.delivered: list[dict] = []          # frames that were ACKed
        self.accepted: list[dict] = []           # frames accepted into FIFO
        self.filter_configs: list[dict] = []
        self.ops: list[str] = []
        self.node_acks = True                    # False = nobody ACKs frames
        self.inject_add_status: int | None = None
        self.inject_op_status: dict[int, int] = {}
        self.last_error_code = LEC_NO_CHANGE
        self.tx_error_cnt = 0
        self.push_regs = lambda brp, bto: None   # set by harness

    # ---- register words ----
    def txbrp(self) -> int:
        return sum(1 << i for i, b in enumerate(self.buffers) if b.pending)

    def txbto(self) -> int:
        return sum(1 << i for i, b in enumerate(self.buffers) if b.transmitted)

    def _sync_regs(self) -> None:
        self.push_regs(self.txbrp(), self.txbto())

    # ---- HAL entry points ----
    def config_filter(self, id_type, index, ftype, fconfig, id1, id2) -> int:
        cfg = dict(id_type=id_type, index=index, type=ftype,
                   config=fconfig, id1=id1, id2=id2)
        self.filter_configs.append(cfg)
        self.log.add("can", f"filter configured: range 0x{id1:03X}..0x{id2:03X} "
                            f"type={ftype} -> config={fconfig}")
        return HAL_OK

    def op(self, code: int) -> int:
        name = {1: "ConfigGlobalFilter", 2: "Start", 3: "ActivateNotification"}.get(code, str(code))
        self.ops.append(name)
        st = self.inject_op_status.get(code, HAL_OK)
        self.log.add("can", f"{name} -> HAL status {st}")
        return st

    def add_to_tx_fifo(self, identifier: int, dlc_code: int, payload: list[int]) -> int:
        if self.inject_add_status is not None:
            st = self.inject_add_status
            self.inject_add_status = None
            self.log.add("can", f"AddMessageToTxFifoQ ID=0x{identifier:03X} "
                                f"-> injected HAL status {st} (frame NOT accepted)")
            return st

        idx = self.put_index
        self.put_index = (self.put_index + 1) % self.NUM_TX_BUFFERS
        nbytes = DLC_CODE_TO_BYTES.get(dlc_code, 0)
        frame = dict(identifier=identifier, dlc_code=dlc_code,
                     nbytes=nbytes, payload=payload[:nbytes], buffer=idx)
        buf = self.buffers[idx]
        buf.frame = frame
        buf.pending = True
        buf.transmitted = False    # hardware clears TXBTO bit on a new request
        self.accepted.append(frame)
        self.log.add("can", f"FIFO-accepted into buffer {idx}: ID=0x{identifier:03X} "
                            f"dlc={nbytes} payload=[{' '.join(f'{b:02X}' for b in frame['payload'])}] "
                            f"(TXBRP bit {idx} set)")
        self._sync_regs()
        self._attempt_transmissions()
        return HAL_OK

    def _attempt_transmissions(self) -> None:
        for i, buf in enumerate(self.buffers):
            if not buf.pending:
                continue
            if self.node_acks:
                buf.pending = False
                buf.transmitted = True
                self.delivered.append(buf.frame)
                self.last_error_code = LEC_NO_ERROR
                self.log.add("can", f"buffer {i} transmitted + ACKed "
                                    f"(TXBRP bit {i} cleared, TXBTO bit {i} set)")
            else:
                self.tx_error_cnt = min(128, self.tx_error_cnt + 8)
                self.last_error_code = LEC_ACK_ERROR
                self.log.add("can", f"buffer {i} transmission attempted, NO ACK "
                                    f"(TXBRP bit {i} still pending, TxErrorCnt={self.tx_error_cnt})")
        self._sync_regs()

    def protocol_status(self) -> tuple[int, int, int]:
        return self.last_error_code, 0, 1 if self.tx_error_cnt >= 128 else 0

    def counters(self) -> int:
        return self.tx_error_cnt


class GpioExtiModel:
    """
    Records the REAL MX_GPIO_Init() pin configuration (via the mock
    HAL_GPIO_Init) and decides whether a GPIO1 level transition fires EXTI4.
    `fire_exti` is wired by the harness to the DLL's HAL_GPIO_EXTI_Callback.
    """

    EXTI_IT_BIT = 0x00010000
    TRIGGER_RISING_BIT = 0x00100000
    TRIGGER_FALLING_BIT = 0x00200000
    PA = 0
    PIN4 = 0x0010

    def __init__(self, log: TransactionLog, force_fire: bool = False) -> None:
        self.log = log
        self.configs: list[dict] = []
        self.force_fire = force_fire
        self.fire_exti = lambda pin: None       # set by harness
        self.exti_fired = 0
        self.accommodations: list[str] = []
        if force_fire:
            note = ("SIL accommodation: EXTI4 fires on ANY PA4 level transition, "
                    "bypassing the configured-edge check (which has its own test)")
            self.accommodations.append(note)
            log.add("note", note)

    def on_gpio_init(self, port_id: int, pin: int, mode: int, pull: int) -> None:
        cfg = dict(port=port_id, pin=pin, mode=mode, pull=pull)
        self.configs.append(cfg)
        self.log.add("gpio", f"HAL_GPIO_Init: port={'AB?'[port_id] if port_id < 2 else port_id} "
                             f"pin=0x{pin:04X} mode=0x{mode:08X} ({self.describe_mode(mode)}) "
                             f"pull={pull}")

    def pa4_config(self) -> dict | None:
        for cfg in self.configs:
            if cfg["port"] == self.PA and (cfg["pin"] & self.PIN4):
                return cfg
        return None

    @classmethod
    def describe_mode(cls, mode: int) -> str:
        if not (mode & cls.EXTI_IT_BIT):
            return "no EXTI interrupt"
        edges = []
        if mode & cls.TRIGGER_RISING_BIT:
            edges.append("RISING")
        if mode & cls.TRIGGER_FALLING_BIT:
            edges.append("FALLING")
        return "EXTI on " + "+".join(edges) if edges else "EXTI, no edge"

    def on_gpio1_transition(self, old: int, new: int) -> None:
        cfg = self.pa4_config()
        rising = old == 0 and new == 1
        falling = old == 1 and new == 0
        if cfg is None:
            self.log.add("gpio", f"PA4 transition {old}->{new}: no PA4 configuration "
                                 "captured (MX_GPIO_Init not run?) -> EXTI4 not fired")
            return
        mode = cfg["mode"]
        matches = bool(mode & self.EXTI_IT_BIT) and (
            (rising and (mode & self.TRIGGER_RISING_BIT)) or
            (falling and (mode & self.TRIGGER_FALLING_BIT)))
        if matches or (self.force_fire and (rising or falling)):
            forced = "" if matches else " [forced by SIL accommodation]"
            self.exti_fired += 1
            self.log.add("gpio", f"PA4 transition {old}->{new} -> EXTI4 fired{forced}")
            self.fire_exti(self.PIN4)
        else:
            self.log.add("gpio", f"PA4 transition {old}->{new} did NOT match configured "
                                 f"trigger ({self.describe_mode(mode)}) -> EXTI4 not fired")
