/*
 * sil_hal.h — mock STM32 HAL for the software-in-the-loop (SIL) harness.
 *
 * Defines ONLY the HAL symbols the compiled firmware units reference, and
 * forwards every peripheral operation to callbacks registered from Python.
 * Constant values are copied verbatim from the real STM32G4 HAL headers so
 * the driver sees identical encodings (DLC codes, GPIO mode bits, ...).
 *
 * This file shadows the real i2c.h / fdcan.h / gpio.h via include-path order.
 * Nothing under Core/ or Drivers/ is modified.
 */

#ifndef SIL_HAL_H
#define SIL_HAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* Common                                                              */
/* ------------------------------------------------------------------ */

typedef enum {
    HAL_OK      = 0x00,
    HAL_ERROR   = 0x01,
    HAL_BUSY    = 0x02,
    HAL_TIMEOUT = 0x03
} HAL_StatusTypeDef;

void     HAL_Delay(uint32_t Delay);
uint32_t HAL_GetTick(void);

/* ------------------------------------------------------------------ */
/* I2C                                                                 */
/* ------------------------------------------------------------------ */

typedef struct { int sil_dummy; } I2C_HandleTypeDef;

extern I2C_HandleTypeDef hi2c2;

/* value copied from stm32g4xx_hal_i2c.h */
#define I2C_MEMADD_SIZE_8BIT   (0x00000001U)
#define I2C_MEMADD_SIZE_16BIT  (0x00000010U)

HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *hi2c, uint16_t DevAddress,
                                   uint16_t MemAddress, uint16_t MemAddSize,
                                   uint8_t *pData, uint16_t Size, uint32_t Timeout);
HAL_StatusTypeDef HAL_I2C_Mem_Write(I2C_HandleTypeDef *hi2c, uint16_t DevAddress,
                                    uint16_t MemAddress, uint16_t MemAddSize,
                                    uint8_t *pData, uint16_t Size, uint32_t Timeout);

void MX_I2C2_Init(void);

/* ------------------------------------------------------------------ */
/* GPIO / EXTI / NVIC                                                  */
/* ------------------------------------------------------------------ */

typedef struct { int sil_dummy; } GPIO_TypeDef;

extern GPIO_TypeDef sil_gpioa;
extern GPIO_TypeDef sil_gpiob;
#define GPIOA (&sil_gpioa)
#define GPIOB (&sil_gpiob)

typedef struct {
    uint32_t Pin;
    uint32_t Mode;
    uint32_t Pull;
    uint32_t Speed;
    uint32_t Alternate;
} GPIO_InitTypeDef;

/* values copied from stm32g4xx_hal_gpio.h */
#define GPIO_PIN_4                   ((uint16_t)0x0010)
#define GPIO_MODE_INPUT              (0x00000000U)
#define GPIO_MODE_IT_RISING          (0x00110000U)  /* MODE_INPUT | EXTI_IT | TRIGGER_RISING  */
#define GPIO_MODE_IT_FALLING         (0x00210000U)  /* MODE_INPUT | EXTI_IT | TRIGGER_FALLING */
#define GPIO_MODE_IT_RISING_FALLING  (0x00310000U)
#define GPIO_NOPULL                  (0x00000000U)
#define GPIO_PULLUP                  (0x00000001U)
#define GPIO_PULLDOWN                (0x00000002U)

typedef enum {
    EXTI4_IRQn      = 10,
    I2C2_EV_IRQn    = 33,
    FDCAN1_IT0_IRQn = 21
} IRQn_Type;

void HAL_GPIO_Init(GPIO_TypeDef *GPIOx, GPIO_InitTypeDef *GPIO_Init);
void HAL_NVIC_SetPriority(IRQn_Type IRQn, uint32_t PreemptPriority, uint32_t SubPriority);
void HAL_NVIC_EnableIRQ(IRQn_Type IRQn);

/* Defined in the firmware's own gpio.c (compiled into the SIL DLL). */
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin);

#define __HAL_RCC_GPIOA_CLK_ENABLE()  ((void)0)
#define __HAL_RCC_GPIOB_CLK_ENABLE()  ((void)0)
#define __HAL_RCC_GPIOC_CLK_ENABLE()  ((void)0)
#define __HAL_RCC_GPIOF_CLK_ENABLE()  ((void)0)

/* ------------------------------------------------------------------ */
/* FDCAN                                                               */
/* ------------------------------------------------------------------ */

/* Only the registers the compiled code actually touches. */
typedef struct {
    volatile uint32_t TXBRP;   /* Tx buffer request pending  */
    volatile uint32_t TXBTO;   /* Tx buffer transmission occurred */
} FDCAN_GlobalTypeDef;

typedef struct {
    FDCAN_GlobalTypeDef *Instance;
    uint32_t ErrorCode;
    uint32_t State;
} FDCAN_HandleTypeDef;

extern FDCAN_HandleTypeDef hfdcan1;

typedef struct {
    uint32_t Identifier;
    uint32_t IdType;
    uint32_t TxFrameType;
    uint32_t DataLength;
    uint32_t ErrorStateIndicator;
    uint32_t BitRateSwitch;
    uint32_t FDFormat;
    uint32_t TxEventFifoControl;
    uint32_t MessageMarker;
} FDCAN_TxHeaderTypeDef;

typedef struct {
    uint32_t IdType;
    uint32_t FilterIndex;
    uint32_t FilterType;
    uint32_t FilterConfig;
    uint32_t FilterID1;
    uint32_t FilterID2;
} FDCAN_FilterTypeDef;

typedef struct {
    uint32_t LastErrorCode;
    uint32_t DataLastErrorCode;
    uint32_t Activity;
    uint32_t ErrorPassive;
    uint32_t Warning;
    uint32_t BusOff;
    uint32_t RxESIflag;
    uint32_t RxBRSflag;
    uint32_t RxFDFflag;
    uint32_t ProtocolException;
    uint32_t TDCvalue;
} FDCAN_ProtocolStatusTypeDef;

typedef struct {
    uint32_t TxErrorCnt;
    uint32_t RxErrorCnt;
    uint32_t RxErrorPassive;
    uint32_t ErrorLogging;
} FDCAN_ErrorCountersTypeDef;

/* values copied from stm32g4xx_hal_fdcan.h */
#define FDCAN_STANDARD_ID              ((uint32_t)0x00000000U)
#define FDCAN_DATA_FRAME               ((uint32_t)0x00000000U)
#define FDCAN_DLC_BYTES_0              ((uint32_t)0x00000000U)
#define FDCAN_DLC_BYTES_1              ((uint32_t)0x00000001U)
#define FDCAN_DLC_BYTES_2              ((uint32_t)0x00000002U)
#define FDCAN_DLC_BYTES_3              ((uint32_t)0x00000003U)
#define FDCAN_DLC_BYTES_4              ((uint32_t)0x00000004U)
#define FDCAN_DLC_BYTES_5              ((uint32_t)0x00000005U)
#define FDCAN_DLC_BYTES_6              ((uint32_t)0x00000006U)
#define FDCAN_DLC_BYTES_7              ((uint32_t)0x00000007U)
#define FDCAN_DLC_BYTES_8              ((uint32_t)0x00000008U)
#define FDCAN_ESI_ACTIVE               ((uint32_t)0x00000000U)
#define FDCAN_BRS_OFF                  ((uint32_t)0x00000000U)
#define FDCAN_CLASSIC_CAN              ((uint32_t)0x00000000U)
#define FDCAN_NO_TX_EVENTS             ((uint32_t)0x00000000U)
#define FDCAN_FILTER_RANGE             ((uint32_t)0x00000000U)
#define FDCAN_FILTER_TO_RXFIFO0        ((uint32_t)0x00000001U)
#define FDCAN_REJECT                   ((uint32_t)0x00000002U)
#define FDCAN_FILTER_REMOTE            ((uint32_t)0x00000000U)
#define FDCAN_IT_RX_FIFO0_NEW_MESSAGE  ((uint32_t)0x00000001U)

HAL_StatusTypeDef HAL_FDCAN_ConfigFilter(FDCAN_HandleTypeDef *hfdcan,
                                         FDCAN_FilterTypeDef *sFilterConfig);
HAL_StatusTypeDef HAL_FDCAN_ConfigGlobalFilter(FDCAN_HandleTypeDef *hfdcan,
                                               uint32_t NonMatchingStd,
                                               uint32_t NonMatchingExt,
                                               uint32_t RejectRemoteStd,
                                               uint32_t RejectRemoteExt);
HAL_StatusTypeDef HAL_FDCAN_Start(FDCAN_HandleTypeDef *hfdcan);
HAL_StatusTypeDef HAL_FDCAN_ActivateNotification(FDCAN_HandleTypeDef *hfdcan,
                                                 uint32_t ActiveITs,
                                                 uint32_t BufferIndexes);
HAL_StatusTypeDef HAL_FDCAN_AddMessageToTxFifoQ(FDCAN_HandleTypeDef *hfdcan,
                                                FDCAN_TxHeaderTypeDef *pTxHeader,
                                                uint8_t *pTxData);
HAL_StatusTypeDef HAL_FDCAN_GetProtocolStatus(FDCAN_HandleTypeDef *hfdcan,
                                              FDCAN_ProtocolStatusTypeDef *ProtocolStatus);
HAL_StatusTypeDef HAL_FDCAN_GetErrorCounters(FDCAN_HandleTypeDef *hfdcan,
                                             FDCAN_ErrorCountersTypeDef *ErrorCounters);

void MX_FDCAN1_Init(void);

/* ------------------------------------------------------------------ */
/* SIL callback registration (called from Python via ctypes)           */
/* ------------------------------------------------------------------ */

/* is_write: 1 = write, 0 = read. Return value is a HAL_StatusTypeDef. */
typedef int32_t (*sil_i2c_cb_t)(uint16_t dev_address, uint16_t mem_address,
                                uint16_t mem_add_size, uint8_t *buf,
                                uint16_t len, int32_t is_write);
typedef void    (*sil_delay_cb_t)(uint32_t ms);
typedef void    (*sil_gpio_init_cb_t)(uint32_t port_id, uint32_t pin,
                                      uint32_t mode, uint32_t pull);
typedef int32_t (*sil_fdcan_add_cb_t)(uint32_t identifier, uint32_t dlc_code,
                                      uint8_t *data);
typedef int32_t (*sil_fdcan_filter_cb_t)(uint32_t id_type, uint32_t filter_index,
                                         uint32_t filter_type, uint32_t filter_config,
                                         uint32_t filter_id1, uint32_t filter_id2);
/* op: 1 = ConfigGlobalFilter, 2 = Start, 3 = ActivateNotification */
typedef int32_t (*sil_fdcan_op_cb_t)(int32_t op);
typedef void    (*sil_fdcan_protocol_cb_t)(uint32_t *last_error_code,
                                           uint32_t *bus_off,
                                           uint32_t *error_passive);
typedef void    (*sil_fdcan_counters_cb_t)(uint32_t *tx_error_cnt);

void sil_set_i2c_cb(sil_i2c_cb_t cb);
void sil_set_delay_cb(sil_delay_cb_t cb);
void sil_set_gpio_init_cb(sil_gpio_init_cb_t cb);
void sil_set_fdcan_add_cb(sil_fdcan_add_cb_t cb);
void sil_set_fdcan_filter_cb(sil_fdcan_filter_cb_t cb);
void sil_set_fdcan_op_cb(sil_fdcan_op_cb_t cb);
void sil_set_fdcan_protocol_cb(sil_fdcan_protocol_cb_t cb);
void sil_set_fdcan_counters_cb(sil_fdcan_counters_cb_t cb);

/* Python-side CAN model pushes register state here after each bus event. */
void sil_fdcan_set_regs(uint32_t txbrp, uint32_t txbto);

void    *sil_get_hfdcan1(void);
uint32_t sil_get_tick(void);
void     sil_flush(void);   /* fflush the C runtime (printf capture) */

#ifdef __cplusplus
}
#endif

#endif /* SIL_HAL_H */
