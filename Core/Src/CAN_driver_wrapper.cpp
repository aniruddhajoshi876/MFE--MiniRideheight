/*
 * CAN_driver_wrapper.cpp
 *
 *  Implementation of the C-callable wrapper. Bridges the extern "C" API
 *  declared in CAN_driver_wrapper.h onto the C++ CANDriver / CANMessage
 *  classes. The void* handle is really a CANDriver*.
 */

#include "CAN_driver_wrapper.h"
#include "CANDriver.h"
#include "CANMessage.hpp"

extern "C" {

void* CAN_create(FDCAN_HandleTypeDef* canInstance,
                 uint32_t filterLowID,
                 uint32_t filterHighID) {
    /*
     * Function-local static: the CANDriver is constructed once, on the first
     * call, using that call's arguments, and lives for the whole program.
     * No heap, nothing to free.
     */
    static CANDriver driver(canInstance, filterLowID, filterHighID);
    return &driver;
}

bool CAN_initialize(void* handle) {
    if (handle == nullptr) return false;
    return static_cast<CANDriver*>(handle)->initialize();
}

bool CAN_configureTransmission(void* handle) {
    if (handle == nullptr) return false;
    return static_cast<CANDriver*>(handle)->configureTransmission();
}

bool CAN_addMessageToQueue(void* handle,
                           uint32_t id,
                           const uint8_t* data,
                           uint8_t length) {
    if (handle == nullptr || data == nullptr) return false;
    if (length > 8) length = 8;
    CANMessage message(id, data, length);
    return static_cast<CANDriver*>(handle)->addMessageToQueue(message);
}

bool CAN_transmitMessage(void* handle) {
    if (handle == nullptr) return false;
    return static_cast<CANDriver*>(handle)->transmitMessage();
}

bool CAN_checkACK(void* handle) {
    if (handle == nullptr) return false;
    return static_cast<CANDriver*>(handle)->checkACK();
}

} /* extern "C" */
