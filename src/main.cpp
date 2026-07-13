#include <Arduino.h>

#define ADC_PIN        34
#define BAUD_RATE      115200
#define SAMPLE_RATE    10000       // 10 kHz
#define BUFFER_SIZE    256

#define SYNC_A  0xAA
#define SYNC_B  0x55

volatile bool shouldSample = false;
uint16_t buffer[BUFFER_SIZE];
int bufferIndex = 0;

hw_timer_t* timer = NULL;

void IRAM_ATTR onTimer() {
    shouldSample = true;
}

void sendPacket(uint16_t* samples, int count) {
    uint8_t checksum = 0;

    Serial.write(SYNC_A);
    Serial.write(SYNC_B);

    Serial.write((count >> 8) & 0xFF);
    Serial.write(count & 0xFF);

    for (int i = 0; i < count; i++) {
        uint8_t hi = (samples[i] >> 8) & 0xFF;
        uint8_t lo = samples[i] & 0xFF;
        Serial.write(hi);
        Serial.write(lo);
        checksum ^= hi;
        checksum ^= lo;
    }

    Serial.write(checksum);
}

void setup() {
    Serial.begin(BAUD_RATE);
    delay(500);                      // let serial line settle before timer starts
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    timer = timerBegin(SAMPLE_RATE);
    timerAttachInterrupt(timer, &onTimer);
    timerAlarm(timer, 1, true, 0);
}

void loop() {
    if (shouldSample) {
        shouldSample = false;
        buffer[bufferIndex++] = (uint16_t)analogRead(ADC_PIN);

        if (bufferIndex >= BUFFER_SIZE) {
            sendPacket(buffer, BUFFER_SIZE);
            bufferIndex = 0;
        }
    }
}
