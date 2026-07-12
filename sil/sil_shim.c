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

#include "BoardManager.h"

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
