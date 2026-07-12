/*
 * BoardManager.h
 *
 *  Created on: Jul 11, 2026
 *      Author: aniru
 */

#ifndef INC_BOARDMANAGER_H_
#define INC_BOARDMANAGER_H_

#include "../../Drivers/VL53L4CD_ULD_Driver/VL53L4CD_api.h"
#include <stdbool.h>
#include <stdio.h>
#include "CAN_driver_wrapper.h"


#define DEVICE_INSTANCE		0x52
#define FRONT_CAN_ID		0x262
#define RL_CAN_ID			0x263
#define RR_CAN_ID			0x264
#define PAYLOAD_LENGTH		2U

extern VL53L4CD_ResultsData_t data;
extern void* can_handle;

bool initializeCAN();
bool push_on_bus(uint16_t payload);

bool sensor_start();
void get_data_it();


#endif /* INC_BOARDMANAGER_H_ */
