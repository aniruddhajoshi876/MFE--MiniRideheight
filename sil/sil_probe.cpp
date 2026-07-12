/*
 * sil_probe.cpp — SIL-only observation probe.
 *
 * Calls ONLY the public CANDriver API so tests can observe internal queue
 * state (e.g. size underflow after a transmit on an empty queue) without
 * modifying any file under Core/.
 */

#include "CANDriver.h"

extern "C" int sil_can_queue_size(void *handle)
{
    if (handle == nullptr) {
        return -999;
    }
    return static_cast<CANDriver *>(handle)->getQueueSize();
}
