/*
 * CANDriver.cpp
 *
 *  Created on: Feb 12, 2025
 *      Author: mariobouzakhm
 */

#include "CANDriver.h"
#include "CANMessage.hpp"
#include <cstdio>

CANDriver::CANDriver(FDCAN_HandleTypeDef* canInstance, uint32_t filterLowID, uint32_t filterHighID)
    : canInstance(canInstance),
      queueSize(0),
      queueHeadIndex(0),
      queueTailIndex(0),
      filterLowID(filterLowID),
      filterHighID(filterHighID) {}

uint32_t CANDriver::dlcFromLen(uint8_t len) {
    if (len > 8) len = 8;

    switch (len) {
        case 0: return FDCAN_DLC_BYTES_0;
        case 1: return FDCAN_DLC_BYTES_1;
        case 2: return FDCAN_DLC_BYTES_2;
        case 3: return FDCAN_DLC_BYTES_3;
        case 4: return FDCAN_DLC_BYTES_4;
        case 5: return FDCAN_DLC_BYTES_5;
        case 6: return FDCAN_DLC_BYTES_6;
        case 7: return FDCAN_DLC_BYTES_7;
        default:return FDCAN_DLC_BYTES_8;
    }
}

bool CANDriver::initialize() {
	 FDCAN_FilterTypeDef sFilterConfig;

	  /* Configure Rx filter */
	  sFilterConfig.IdType = FDCAN_STANDARD_ID;
	  sFilterConfig.FilterIndex = 0;
	  sFilterConfig.FilterType = FDCAN_FILTER_RANGE;
	  sFilterConfig.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
	  sFilterConfig.FilterID1 = this->filterLowID;
	  sFilterConfig.FilterID2 = this->filterHighID;

	  if(HAL_FDCAN_ConfigFilter(this->canInstance, &sFilterConfig) != HAL_OK) {
		  return 0;
	  }

	  if (HAL_FDCAN_ConfigGlobalFilter(this->canInstance, FDCAN_REJECT, FDCAN_REJECT, FDCAN_FILTER_REMOTE, FDCAN_FILTER_REMOTE) != HAL_OK)
	  {
		return 0;
	  }

	  /* Start the FDCAN module */
	  if (HAL_FDCAN_Start(this->canInstance) != HAL_OK)
	  {
		printf("FDCAN initialization error\r\n");
		return 0;
	  }

	  if (HAL_FDCAN_ActivateNotification(this->canInstance, FDCAN_IT_RX_FIFO0_NEW_MESSAGE, 0) != HAL_OK)
	  {
		return 0;
	  }
	  printf("fdcan initialized\r\n");
	  return 1;
}

bool CANDriver::configureTransmission() {
	  FDCAN_TxHeaderTypeDef TxHeader;

	  TxHeader.IdType = FDCAN_STANDARD_ID;
	  TxHeader.TxFrameType = FDCAN_DATA_FRAME;
	  TxHeader.DataLength = FDCAN_DLC_BYTES_8;
	  TxHeader.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
	  TxHeader.BitRateSwitch = FDCAN_BRS_OFF;
	  TxHeader.FDFormat = FDCAN_CLASSIC_CAN;
	  TxHeader.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
	  TxHeader.MessageMarker = 0;

	  this->TxHeader = TxHeader;

	  return 1;
}

bool CANDriver::addMessageToQueue(const CANMessage& message) {
    if (isQueueFull()){
    	printf("Queue is full\r\n");
    	return false;
    }

    canQueue[queueTailIndex] = message;
    queueTailIndex = (queueTailIndex + 1) % MAX_QUEUE_CAPACITY;
    queueSize++;
    return true;
}

bool CANDriver::transmitMessage() {
    if (isQueueEmpty()) {
    	printf("Queue is empty\r\n");
    	return false;
    }

    CANMessage msg = canQueue[queueHeadIndex];
    queueHeadIndex = (queueHeadIndex + 1) % MAX_QUEUE_CAPACITY;
    queueSize--;

    TxHeader.Identifier = msg.getID();
    TxHeader.DataLength = CANDriver::dlcFromLen(msg.getLength());

    printf("[CAN] Calling HAL_FDCAN_AddMessageToTxFifoQ: ID=0x%03lX len=%lu\r\n",
           TxHeader.Identifier, TxHeader.DataLength);

    HAL_StatusTypeDef halStatus = HAL_FDCAN_AddMessageToTxFifoQ(canInstance, &TxHeader, msg.getData());
    printf("[CAN] HAL status: %d (0=OK,1=ERR,2=BUSY,3=TIMEOUT)\r\n", (int)halStatus);

    if (halStatus != HAL_OK){
    	printf("Transmission Error\r\n");
        return false;
    }

    printf("[CAN] Message queued to TX FIFO OK\r\n");
    return true;
}

bool CANDriver::transmitPriorityMessage() {
	//to be implemented
	return 1;
}

bool CANDriver::isQueueFull() const { return queueSize == MAX_QUEUE_CAPACITY; }

bool CANDriver::isQueueEmpty() const { return this->queueSize == 0;}

int CANDriver::getQueueSize() const { return this->queueSize; }

bool CANDriver::checkACK(){
	FDCAN_ProtocolStatusTypeDef status;
	HAL_FDCAN_GetProtocolStatus(canInstance, &status);

	FDCAN_ErrorCountersTypeDef counter;
	HAL_FDCAN_GetErrorCounters(canInstance, &counter);
//	printf("[CAN] checkACK: LastErrorCode=%lu BusOff=%lu ErrorPassive=%lu\r\n",
//	       (uint32_t)status.LastErrorCode, (uint32_t)status.BusOff, (uint32_t)status.ErrorPassive);
//	/*
//	 * 		Value	Meaning		What it tells you
//				0	No error	Everything OK
//				1	Stuff error	Bit stuffing issue
//				2	Form error	Frame format issue
//				3	ACK error	No node acknowledged
//				4	Bit1 error	Bit mismatch
//				5	Bit0 error	Bit mismatch
//				6	CRC error	Corrupted frame
//				7	No change	No new error
//	 */

	switch (status.LastErrorCode) {
	    case 0: printf("No error\r\n"); break;
	    case 1: printf("Stuff error\r\n"); break;
	    case 2: printf("Form error\r\n"); break;
	    case 3: printf("ACK error, no node detected\r\n"); break;
	    case 4: printf("Bit1 error\r\n"); break;
	    case 5: printf("Bit0 error\r\n"); break;
	    case 6: printf("CRC error\r\n"); break;
	    case 7: printf("No change\r\n"); break;
	    default: printf("Unknown CAN error\r\n"); break;
	}

	//number of errors
	uint32_t error_counter;
	error_counter = counter.TxErrorCnt;
	printf("Number of error: %ld\r\n", error_counter); //maxes out at 128


	uint8_t buffer_pending_check;
	buffer_pending_check = *(&hfdcan1.Instance->TXBRP); //check for pending messages

	uint8_t buffer_transmit_check;
	buffer_transmit_check = *(&hfdcan1.Instance->TXBTO); //check bits 0,1, and 2 since since of queue only permits 3 messages to be held before overflow

	// & 0b001 isolates bit 0 (buffer 1) from each register, leaving 0 (clear) or 1 (set).
	// !(...) on the TXBTO side is true when bit 0 is clear, i.e. buffer 1 has NOT finished
	// transmitting; combined with the TXBRP side being true (bit 0 set, buffer 1 still has
	// a request pending), the whole condition means "buffer 1 is pending but not transmitted".
	if (!(buffer_transmit_check & 0b001) && (buffer_pending_check & 0b001)){
		printf("buffer 1 pending but not transmitted\r\n");
		return false;
	}

	// & 0b010 isolates bit 1 (buffer 2), leaving 0 (clear) or 2 (set). !(...) is true when
	// bit 1 is clear (buffer 2 not yet transmitted); AND'd with the TXBRP side (bit 1 set,
	// buffer 2 still pending) gives "buffer 2 pending but not transmitted".
	if (!(buffer_transmit_check & 0b010) && (buffer_pending_check & 0b010)){
		printf("buffer 2 pending but not transmitted\r\n");
		printf("Message not transmitted\r\n");
		return false;
	}

	// & 0b100 isolates bit 2 (buffer 3), leaving 0 (clear) or 4 (set) — same reasoning as
	// buffer 2 above: !(...) true means bit 2 clear (not transmitted), AND'd with TXBRP bit 2
	// set (still pending) gives "buffer 3 pending but not transmitted".
	if (!(buffer_transmit_check & 0b100) && (buffer_pending_check & 0b100)){
		printf("buffer 3 pending but not transmitted\r\n");
		printf("Message not transmitted\r\n");
		return false;
	}

	return true;
}

