/*
 * mock_hal.c — implementation of the SIL mock HAL.
 *
 * Every peripheral call is forwarded to a Python callback registered via the
 * sil_set_* functions. If a required callback is missing, the call returns
 * HAL_ERROR so an unwired harness fails loudly instead of silently passing.
 */

#include "sil_hal.h"
#include <stdio.h>

/* ---- peripheral handle instances (normally defined by CubeMX files) ---- */
I2C_HandleTypeDef   hi2c2;
GPIO_TypeDef        sil_gpioa;
GPIO_TypeDef        sil_gpiob;

static FDCAN_GlobalTypeDef sil_fdcan_regs; /* TXBRP / TXBTO backing store */
FDCAN_HandleTypeDef hfdcan1 = { &sil_fdcan_regs, 0u, 0u };

/* ---- registered callbacks ---- */
static sil_i2c_cb_t             s_i2c_cb;
static sil_delay_cb_t           s_delay_cb;
static sil_gpio_init_cb_t       s_gpio_init_cb;
static sil_fdcan_add_cb_t       s_fdcan_add_cb;
static sil_fdcan_filter_cb_t    s_fdcan_filter_cb;
static sil_fdcan_op_cb_t        s_fdcan_op_cb;
static sil_fdcan_protocol_cb_t  s_fdcan_protocol_cb;
static sil_fdcan_counters_cb_t  s_fdcan_counters_cb;

static uint32_t s_tick_ms;

void sil_set_i2c_cb(sil_i2c_cb_t cb)                       { s_i2c_cb = cb; }
void sil_set_delay_cb(sil_delay_cb_t cb)                   { s_delay_cb = cb; }
void sil_set_gpio_init_cb(sil_gpio_init_cb_t cb)           { s_gpio_init_cb = cb; }
void sil_set_fdcan_add_cb(sil_fdcan_add_cb_t cb)           { s_fdcan_add_cb = cb; }
void sil_set_fdcan_filter_cb(sil_fdcan_filter_cb_t cb)     { s_fdcan_filter_cb = cb; }
void sil_set_fdcan_op_cb(sil_fdcan_op_cb_t cb)             { s_fdcan_op_cb = cb; }
void sil_set_fdcan_protocol_cb(sil_fdcan_protocol_cb_t cb) { s_fdcan_protocol_cb = cb; }
void sil_set_fdcan_counters_cb(sil_fdcan_counters_cb_t cb) { s_fdcan_counters_cb = cb; }

void sil_fdcan_set_regs(uint32_t txbrp, uint32_t txbto)
{
    sil_fdcan_regs.TXBRP = txbrp;
    sil_fdcan_regs.TXBTO = txbto;
}

void *sil_get_hfdcan1(void)  { return &hfdcan1; }
uint32_t sil_get_tick(void)  { return s_tick_ms; }
void sil_flush(void)         { fflush(NULL); }

/* ---- time ---- */

void HAL_Delay(uint32_t Delay)
{
    s_tick_ms += Delay;
    if (s_delay_cb != 0) {
        s_delay_cb(Delay);   /* advances the Python-side simulated clock */
    }
}

uint32_t HAL_GetTick(void) { return s_tick_ms; }

/* ---- I2C ---- */

HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *hi2c, uint16_t DevAddress,
                                   uint16_t MemAddress, uint16_t MemAddSize,
                                   uint8_t *pData, uint16_t Size, uint32_t Timeout)
{
    (void)hi2c; (void)Timeout;
    if (s_i2c_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_i2c_cb(DevAddress, MemAddress, MemAddSize,
                                       pData, Size, 0);
}

HAL_StatusTypeDef HAL_I2C_Mem_Write(I2C_HandleTypeDef *hi2c, uint16_t DevAddress,
                                    uint16_t MemAddress, uint16_t MemAddSize,
                                    uint8_t *pData, uint16_t Size, uint32_t Timeout)
{
    (void)hi2c; (void)Timeout;
    if (s_i2c_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_i2c_cb(DevAddress, MemAddress, MemAddSize,
                                       pData, Size, 1);
}

void MX_I2C2_Init(void) { /* peripheral clock/pin setup has no SIL meaning */ }

/* ---- GPIO / NVIC ---- */

void HAL_GPIO_Init(GPIO_TypeDef *GPIOx, GPIO_InitTypeDef *GPIO_Init)
{
    uint32_t port_id = (GPIOx == GPIOA) ? 0u : (GPIOx == GPIOB) ? 1u : 0xFFu;
    if (s_gpio_init_cb != 0) {
        s_gpio_init_cb(port_id, GPIO_Init->Pin, GPIO_Init->Mode, GPIO_Init->Pull);
    }
}

void HAL_NVIC_SetPriority(IRQn_Type IRQn, uint32_t PreemptPriority, uint32_t SubPriority)
{
    (void)IRQn; (void)PreemptPriority; (void)SubPriority;
}

void HAL_NVIC_EnableIRQ(IRQn_Type IRQn) { (void)IRQn; }

/* ---- FDCAN ---- */

HAL_StatusTypeDef HAL_FDCAN_ConfigFilter(FDCAN_HandleTypeDef *hfdcan,
                                         FDCAN_FilterTypeDef *sFilterConfig)
{
    (void)hfdcan;
    if (s_fdcan_filter_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_fdcan_filter_cb(sFilterConfig->IdType,
                                                sFilterConfig->FilterIndex,
                                                sFilterConfig->FilterType,
                                                sFilterConfig->FilterConfig,
                                                sFilterConfig->FilterID1,
                                                sFilterConfig->FilterID2);
}

HAL_StatusTypeDef HAL_FDCAN_ConfigGlobalFilter(FDCAN_HandleTypeDef *hfdcan,
                                               uint32_t NonMatchingStd,
                                               uint32_t NonMatchingExt,
                                               uint32_t RejectRemoteStd,
                                               uint32_t RejectRemoteExt)
{
    (void)hfdcan; (void)NonMatchingStd; (void)NonMatchingExt;
    (void)RejectRemoteStd; (void)RejectRemoteExt;
    if (s_fdcan_op_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_fdcan_op_cb(1);
}

HAL_StatusTypeDef HAL_FDCAN_Start(FDCAN_HandleTypeDef *hfdcan)
{
    (void)hfdcan;
    if (s_fdcan_op_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_fdcan_op_cb(2);
}

HAL_StatusTypeDef HAL_FDCAN_ActivateNotification(FDCAN_HandleTypeDef *hfdcan,
                                                 uint32_t ActiveITs,
                                                 uint32_t BufferIndexes)
{
    (void)hfdcan; (void)ActiveITs; (void)BufferIndexes;
    if (s_fdcan_op_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_fdcan_op_cb(3);
}

HAL_StatusTypeDef HAL_FDCAN_AddMessageToTxFifoQ(FDCAN_HandleTypeDef *hfdcan,
                                                FDCAN_TxHeaderTypeDef *pTxHeader,
                                                uint8_t *pTxData)
{
    (void)hfdcan;
    if (s_fdcan_add_cb == 0) return HAL_ERROR;
    return (HAL_StatusTypeDef)s_fdcan_add_cb(pTxHeader->Identifier,
                                             pTxHeader->DataLength,
                                             pTxData);
}

HAL_StatusTypeDef HAL_FDCAN_GetProtocolStatus(FDCAN_HandleTypeDef *hfdcan,
                                              FDCAN_ProtocolStatusTypeDef *ProtocolStatus)
{
    (void)hfdcan;
    ProtocolStatus->LastErrorCode = 0;
    ProtocolStatus->DataLastErrorCode = 0;
    ProtocolStatus->Activity = 0;
    ProtocolStatus->ErrorPassive = 0;
    ProtocolStatus->Warning = 0;
    ProtocolStatus->BusOff = 0;
    ProtocolStatus->RxESIflag = 0;
    ProtocolStatus->RxBRSflag = 0;
    ProtocolStatus->RxFDFflag = 0;
    ProtocolStatus->ProtocolException = 0;
    ProtocolStatus->TDCvalue = 0;
    if (s_fdcan_protocol_cb == 0) return HAL_ERROR;
    {
        uint32_t lec = 0, bus_off = 0, err_passive = 0;
        s_fdcan_protocol_cb(&lec, &bus_off, &err_passive);
        ProtocolStatus->LastErrorCode = lec;
        ProtocolStatus->BusOff = bus_off;
        ProtocolStatus->ErrorPassive = err_passive;
    }
    return HAL_OK;
}

HAL_StatusTypeDef HAL_FDCAN_GetErrorCounters(FDCAN_HandleTypeDef *hfdcan,
                                             FDCAN_ErrorCountersTypeDef *ErrorCounters)
{
    (void)hfdcan;
    ErrorCounters->TxErrorCnt = 0;
    ErrorCounters->RxErrorCnt = 0;
    ErrorCounters->RxErrorPassive = 0;
    ErrorCounters->ErrorLogging = 0;
    if (s_fdcan_counters_cb == 0) return HAL_ERROR;
    {
        uint32_t tx_err = 0;
        s_fdcan_counters_cb(&tx_err);
        ErrorCounters->TxErrorCnt = tx_err;
    }
    return HAL_OK;
}

void MX_FDCAN1_Init(void) { /* real init lives in fdcan.c, which is not linked */ }

/* Declared in Core/Inc/main.h; defined in main.c which is not linked. */
void Error_Handler(void)
{
    fprintf(stderr, "[SIL] Error_Handler() reached\n");
}
