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
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	ok = HAL_I2C_Mem_Read(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, (uint8_t*) value, 2, 100); //read 32-bit registr and store in value

	return (ok == HAL_OK) ? 0 : status; //if ok ==hal_ok, return 0; else return 255
}

uint8_t VL53L4CD_RdWord(Dev_t dev, uint16_t RegisterAdress, uint16_t *value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	ok = HAL_I2C_Mem_Read(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, (uint8_t*) value, 2, 100); //read 16-bit registr and store in value

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

	ok = HAL_I2C_Mem_Write(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, (uint8_t*) &value, 2, 100);


	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WrDWord(Dev_t dev, uint16_t RegisterAdress, uint32_t value)
{
	uint8_t status = 255;
	HAL_StatusTypeDef ok;
	/* To be filled by customer. Return 0 if OK */
	/* Warning : For big endian platforms, fields 'RegisterAdress' and 'value' need to be swapped. */
	//#error "This code is empty, please populate the function with valid code for your processor."

	ok = HAL_I2C_Mem_Write(&hi2c2, dev, RegisterAdress, I2C_MEMADD_SIZE_16BIT, (uint8_t*) &value, 4, 100);

	return (ok == HAL_OK) ? 0 : status;
}

uint8_t VL53L4CD_WaitMs(Dev_t dev, uint32_t TimeMs)
{
	/* To be filled by customer */
	//#error "This code is empty, please populate the function with valid code for your processor."

	HAL_Delay(TimeMs);

	return 0;
}


