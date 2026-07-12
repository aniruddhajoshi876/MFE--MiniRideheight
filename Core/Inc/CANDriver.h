/*
 * CANDriver.h
 *
 *  Created on: Feb 12, 2025
 *      Author: mariobouzakhm
 */

#ifndef INC_CANDRIVER_H_
#define INC_CANDRIVER_H_

#include <stdint.h>
#include "fdcan.h"
#include "CANMessage.hpp"

class CANDriver {
	public:
		static constexpr int MAX_QUEUE_CAPACITY = 50;

	private:
		FDCAN_HandleTypeDef *canInstance;
		FDCAN_TxHeaderTypeDef TxHeader;

		CANMessage canQueue[MAX_QUEUE_CAPACITY];
		int queueSize;
		int queueHeadIndex;
		int queueTailIndex;

		uint32_t filterLowID;
		uint32_t filterHighID;

		static uint32_t dlcFromLen(uint8_t len);

	public:
		CANDriver(FDCAN_HandleTypeDef *canInstance, uint32_t filterLowID, uint32_t filterHighID);

		bool initialize();
		bool configureTransmission();

		bool addMessageToQueue(const CANMessage& message);
		bool transmitMessage();
		bool transmitPriorityMessage();

		bool isQueueFull() const;
		bool isQueueEmpty() const;
		int getQueueSize() const;
		bool checkACK();

};

#endif /* INC_CANDRIVER_H_ */
