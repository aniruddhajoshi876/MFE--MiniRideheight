/*
 * sil_shim.c — SIL-only glue.
 *
 * main.c is not linked into the SIL DLL (it is a non-returning superloop),
 * so this file supplies the globals main.c normally defines, and a
 * sil_main_step() that reproduces the main-loop body VERBATIM so the tested
 * sequence (clear data_ready BEFORE consuming) is the real one.
 *
 * No file under Core/ or Drivers/ is modified.
 */

#include <stdint.h>

#include "BoardManager.h"
#include "stm32g4xx_hal.h"   /* resolves to the SIL shadow header */

/* Globals normally defined in Core/Src/main.c (see its USER CODE BEGIN PD). */
bool data_ready;
VL53L4CD_ResultsData_t data;
void *can_handle;

/* Verbatim mirror of the while(1) body in Core/Src/main.c:116-119. */
void sil_main_step(void)
{
    if (data_ready) {
        data_ready = 0;
        get_data_it();
    }
}

/*
 * Bounded MIRROR of the application-level startup in Core/Src/main.c
 * (the initializeCAN() retry loop followed by the sensor_start() retry
 * loop, each with HAL_Delay(20) between attempts). main() itself retries
 * forever and is not linked, so this is not the literal production entry
 * point: max_retries bounds each loop so an absent or faulted device
 * cannot hang a SIL worker. sys.startup_mirror_drift checks that main.c
 * still matches the calls mirrored here.
 */
typedef enum {
    SIL_START_OK            = 0,
    SIL_START_CAN_FAILED    = 1,
    SIL_START_SENSOR_FAILED = 2
} SilStartStatus;

static uint32_t s_can_attempts;
static uint32_t s_sensor_attempts;

uint32_t sil_start_can_attempts(void)    { return s_can_attempts; }
uint32_t sil_start_sensor_attempts(void) { return s_sensor_attempts; }

int32_t sil_main_start(uint32_t max_retries)
{
    bool ok = false;
    s_can_attempts = 0;
    s_sensor_attempts = 0;
    while (!ok && s_can_attempts < max_retries) {   /* main.c: while (!initializeCAN()) */
        s_can_attempts++;
        ok = initializeCAN();
        if (!ok) {
            HAL_Delay(20);
        }
    }
    if (!ok) {
        return SIL_START_CAN_FAILED;
    }
    ok = false;
    while (!ok && s_sensor_attempts < max_retries) { /* main.c: while (!sensor_start()) */
        s_sensor_attempts++;
        ok = sensor_start();
        if (!ok) {
            HAL_Delay(20);
        }
    }
    if (!ok) {
        return SIL_START_SENSOR_FAILED;
    }
    return SIL_START_OK;
}
