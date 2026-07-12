/*
 * BoardManager.c
 *
 *  Created on: Jul 11, 2026
 *      Author: aniru
 */

#include "BoardManager.h"

bool initializeCAN(){
	can_handle = CAN_create(&hfdcan1, 0x0, 0x7FF); //create pointer to can_driver class

	if (!CAN_initialize(can_handle)){return false;} //enable fdcan peripheral

	if (!CAN_configureTransmission(can_handle)) {return false;} //configure settings

	return true;
}

bool push_on_bus(uint16_t payload){
	if (!CAN_addMessageToQueue(can_handle, FRONT_CAN_ID, (uint8_t*) &payload, PAYLOAD_LENGTH)) {return false;}

	if (!CAN_transmitMessage(can_handle)) {return false;}

	return CAN_checkACK(can_handle);
}

bool sensor_start(){
	  if (VL53L4CD_SensorInit(DEVICE_INSTANCE)) {return false;}; //initialize sensor
	  if (VL53L4CD_StartRanging(DEVICE_INSTANCE)) {return true;} //begin reading distance
	  return true;
}

void get_data_it(){
	  VL53L4CD_GetResult(DEVICE_INSTANCE, &data); //get data
	  VL53L4CD_ClearInterrupt(DEVICE_INSTANCE); //clear interrupt
	  if (data.range_status == 0){
		  printf("Distance: %d (mm)\r\n", data.distance_mm); //print data to terminal
		  //send data along CAN
	  } else{
		  printf("Measurement Error\r\n");
	  }
}


