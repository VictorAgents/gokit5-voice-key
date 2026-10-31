#pragma once
#include <esp_system.h>

// Verified reference gizwits-4key config: ADC1 channel 0 = ESP32-S3 GPIO1.
// Reference PowerManager uses 12-bit/12dB, raw endpoints 1785..2370 and
// measured divider ratio 0.467. These are estimates, not battery health.
constexpr uint8_t BATTERY_PIN=1;
// Factory image 77a356... config at 0x3c13ab30 is mask 0x808,
// INPUT + pull-up. Function 0x4200f064: CHRG(11) LOW -> charging;
// otherwise STDBY(3) LOW -> charge complete; otherwise neither.
uint8_t chargeState=0;
uint16_t batteryRaw=0,batteryPinMv=0,batteryMv=0;
uint8_t batteryPercent=255;
bool batteryValid=false;
uint32_t batterySampledAt=0;
uint16_t batteryRawSamples[16]={},batteryMvSamples[16]={};
uint8_t batterySampleIndex=0,batterySampleCount=0;

constexpr uint8_t percentFromRaw(uint16_t raw) {
  return raw<=1785?0:raw>=2370?100:uint8_t((uint32_t(raw)-1785)*100/(2370-1785));
}
static_assert(percentFromRaw(1785)==0&&percentFromRaw(2370)==100&&
              percentFromRaw(2077)==49,"battery estimate boundaries");

void sampleBattery() {
  chargeState=digitalRead(11)==LOW?1:digitalRead(3)==LOW?2:0;
  uint16_t raw=analogRead(BATTERY_PIN);
  uint32_t mv=analogReadMilliVolts(BATTERY_PIN);
  // Do not retain a previous good value if the ADC starts failing.
  if(raw==0||raw>=4095||mv<100||mv>3000) {
    batteryValid=false;batteryPercent=255;batterySampleCount=0;batterySampleIndex=0;return;
  }
  batteryRawSamples[batterySampleIndex]=raw;
  batteryMvSamples[batterySampleIndex]=uint16_t(mv);
  batterySampleIndex=(batterySampleIndex+1)%16;
  if(batterySampleCount<16) batterySampleCount++;
  uint32_t rawTotal=0,mvTotal=0;
  for(int i=0;i<batterySampleCount;i++) {rawTotal+=batteryRawSamples[i];mvTotal+=batteryMvSamples[i];}
  batteryRaw=rawTotal/batterySampleCount;batteryPinMv=mvTotal/batterySampleCount;
  batteryMv=(uint32_t(batteryPinMv)*1000+233)/467;
  batteryValid=batteryMv>=2500&&batteryMv<=4500;
  batteryPercent=batteryValid?percentFromRaw(batteryRaw):255;
}
void beginBattery() {
  pinMode(11,INPUT_PULLUP);pinMode(3,INPUT_PULLUP);
  analogReadResolution(12);analogSetPinAttenuation(BATTERY_PIN,ADC_11db);
  for(int i=0;i<4;i++) sampleBattery();
  batterySampledAt=millis();
  Serial.printf("BATTERY ready=%d raw=%u pin_mv=%u battery_mv=%u percent_estimate=%u charge=%u\n",
                batteryValid,batteryRaw,batteryPinMv,batteryMv,batteryPercent,chargeState);
}
void batteryTick() {
  uint32_t now=millis();
  if(now-batterySampledAt>=500) {batterySampledAt=now;sampleBattery();}
}
