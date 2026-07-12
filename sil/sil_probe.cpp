/*
 * sil_probe.cpp — SIL-only observation probe.
 *
 * Calls ONLY the public CANDriver API so tests can observe internal queue
 * state (e.g. size underflow after a transmit on an empty queue) without
 * modifying any file under Core/.
 */

#include <stdint.h>

#include "stm32g4xx_hal.h"
#include "BoardManager.h"
#include "CANDriver.h"

extern "C" int sil_can_queue_size(void *handle)
{
    if (handle == nullptr) {
        return -999;
    }
    return static_cast<CANDriver *>(handle)->getQueueSize();
}

/*
 * Compile-time firmware constants exposed through the DLL.  Python reads
 * these values instead of carrying a second, manually-synchronised copy.
 * Keep the exported ABI fixed-width even when the underlying macro is an
 * unsuffixed integer literal.
 */
extern "C" uint32_t sil_fw_device_instance(void)
{
    return (uint32_t)DEVICE_INSTANCE;
}

extern "C" uint32_t sil_fw_front_can_id(void)
{
    return (uint32_t)FRONT_CAN_ID;
}

extern "C" uint32_t sil_fw_gpio_pin_4(void)
{
    return (uint32_t)GPIO_PIN_4;
}

extern "C" uint32_t sil_fw_payload_length(void)
{
    return (uint32_t)PAYLOAD_LENGTH;
}
