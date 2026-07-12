/*
 * SIL shadow of the STM32G4 HAL umbrella header.
 *
 * The real Core/Inc/{main,i2c,fdcan,gpio}.h compile UNMODIFIED against this
 * mock: they only include "stm32g4xx_hal.h", which resolves here because the
 * real HAL include directory is not on the SIL include path.
 */
#ifndef SIL_SHADOW_STM32G4XX_HAL_H
#define SIL_SHADOW_STM32G4XX_HAL_H
#include "sil_hal.h"
#endif
