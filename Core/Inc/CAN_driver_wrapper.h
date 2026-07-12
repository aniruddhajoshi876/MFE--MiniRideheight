/*
 * CAN_driver_wrapper.h
 *
 *  C-callable wrapper around the C++ CANDriver / CANMessage classes.
 *  Include this from C sources (e.g. main.c) to drive CAN without C++.
 */

#ifndef INC_CAN_DRIVER_WRAPPER_H_
#define INC_CAN_DRIVER_WRAPPER_H_

#include <stdint.h>
#include <stdbool.h>
#include "fdcan.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * All functions take a void* handle that points at the underlying C++
 * CANDriver. C code just passes it around; the .cpp casts it back.
 */

/*
 * Get the driver instance. Uses static storage and is constructed on the
 * first call; later calls return the same handle. There is nothing to free.
 */
void* CAN_create(FDCAN_HandleTypeDef* canInstance,
                 uint32_t filterLowID,
                 uint32_t filterHighID);

/* Configure the Rx filter, start FDCAN and enable Rx notifications. */
bool CAN_initialize(void* handle);

/* Set up the shared Tx header used for outgoing frames. */
bool CAN_configureTransmission(void* handle);

/*
 * Enqueue a frame. data points to up to 8 bytes; length is clamped to 8.
 * Returns false if the queue is full.
 */
bool CAN_addMessageToQueue(void* handle,
                           uint32_t id,
                           const uint8_t* data,
                           uint8_t length);

/* Pop the head of the queue and push it to the Tx FIFO. */
bool CAN_transmitMessage(void* handle);

/* Report protocol/error status of the last transmission. */
bool CAN_checkACK(void* handle);

#ifdef __cplusplus
}
#endif

#endif /* INC_CAN_DRIVER_WRAPPER_H_ */
