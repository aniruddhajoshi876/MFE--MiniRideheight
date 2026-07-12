/*
 * CANMessage.hpp
 *
 *  Created on: Feb 12, 2025
 *      Author: mariobouzakhm
 */

#ifndef INC_CANMESSAGE_HPP_
#define INC_CANMESSAGE_HPP_

#include <stdint.h>

class CANMessage {
	private:
		uint32_t id;
		uint8_t  data[8];
		uint8_t length;

	public:
		CANMessage();
	    CANMessage(uint32_t id,const uint8_t* data, uint8_t length);
	    void setMessage(const uint8_t* data, uint8_t length);

	    uint8_t *getData();
	    uint32_t getID();
	    uint8_t getLength();
};

#endif /* INC_CANMESSAGE_HPP_ */
