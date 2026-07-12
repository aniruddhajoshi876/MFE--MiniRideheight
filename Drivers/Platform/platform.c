/**
  *
  * Copyright (c) 2023 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */


#include "platform.h"


uint8_t VL53L4CD_RdDWord(Dev_t dev, uint16_t RegisterAdress, uint32_t *value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	uint8_t buffer[4];

	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	ok = HAL_I2C_Mem_Read(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, buffer, 4, 100); //read 32-bit registr and store in value
	*value = ((uint32_t)buffer[0] << 24) | (uint32_t)buffer[1] << 16 | (uint32_t)buffer[2] << 8 | (uint32_t)buffer[3];
	return (ok == HAL_OK) ? 0 : status; //if ok ==hal_ok, return 0; else return 255
}

uint8_t VL53L4CD_RdWord(Dev_t dev, uint16_t RegisterAdress, uint16_t *value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	uint8_t buffer[2];

	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	ok = HAL_I2C_Mem_Read(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, buffer, 2, 100); //read 16-bit registr and store in value
	*value = ((uint16_t)buffer[0] << 8) | (uint16_t)buffer[1];
	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_RdByte(Dev_t dev, uint16_t RegisterAdress, uint8_t *value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */


	ok = HAL_I2C_Mem_Read(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, value, 1, 100);


	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WrByte(Dev_t dev, uint16_t RegisterAdress, uint8_t value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;

	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */

	ok = HAL_I2C_Mem_Write(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, &value, 1, 100);


	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WrWord(Dev_t dev, uint16_t RegisterAdress, uint16_t value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	uint8_t big_endian_value[2] = {(uint8_t)(value >> 8), (uint8_t)(value)};

	ok = HAL_I2C_Mem_Write(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, big_endian_value, 2, 100);


	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WrDWord(Dev_t dev, uint16_t RegisterAdress, uint32_t value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	uint8_t big_endian_value[4] = {(uint8_t)(value >> 24), (uint8_t)(value >> 16), (uint8_t)(value >> 8), (uint8_t)(value)};

	ok = HAL_I2C_Mem_Write(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, big_endian_value, 4, 100);

	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WaitMs(Dev_t dev, uint32_t TimeMs)
{
	/* To be filled by customer */
	//#error "This code is empty, please populate the function with valid code for your processor."

	HAL_Delay(TimeMs);

	return 0;
}


