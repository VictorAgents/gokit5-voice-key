#pragma once
#include <esp_sleep.h>
#include <esp32-hal-rmt.h>

volatile bool shutdownRequested=false;
bool statusPwm=false;
bool ringReady=false;
uint32_t lastActivity=0;

void setRingColors(const uint8_t colors[4][3]) {
  // Four WS2812 pixels; writing one pixel leaves the remaining three latched.
  if(!ringReady) return;
  static uint8_t previous[4][3];static bool first=true;
  if(!first&&!memcmp(previous,colors,sizeof(previous))) return;
  rmt_data_t symbols[4*24]={};
  int index=0;
  for(int pixel=0;pixel<4;pixel++) {
    uint8_t grb[]={colors[pixel][1],colors[pixel][0],colors[pixel][2]};
    for(uint8_t c:grb) for(int bit=7;bit>=0;bit--) {
    auto &s=symbols[index++];bool one=c&(1<<bit);
    s.level0=1;s.duration0=one?8:4;s.level1=0;s.duration1=one?4:8;
  }
  }
  if(rmtWrite(2,symbols,4*24,20)) {memcpy(previous,colors,sizeof(previous));first=false;}
  delayMicroseconds(80);
}
void clearRing() {const uint8_t colors[4][3]={};setRingColors(colors);}
void colorWheel(uint8_t pos,uint8_t *out) {
  if(pos<85) {out[0]=255-pos*3;out[1]=pos*3;out[2]=0;}
  else if(pos<170) {pos-=85;out[0]=0;out[1]=255-pos*3;out[2]=pos*3;}
  else {pos-=170;out[0]=pos*3;out[1]=0;out[2]=255-pos*3;}
}
void setStatusBrightness(uint8_t value) {
  // GPIO6 is active LOW on the verified board.
  if(statusPwm) ledcWrite(6,255-value);
  else digitalWrite(6,value?LOW:HIGH);
}
void hardwareTask(void*) {
  PowerButton power;
  uint8_t previous=0;
  bool powerRaw=digitalRead(12)==LOW,powerStable=powerRaw;
  uint32_t changedAt=millis();
  for(;;) {
    uint32_t now=millis();uint8_t mask=0;
    const int pins[]={12,13,14,38};
    for(int i=0;i<4;i++) if(digitalRead(pins[i])==LOW) mask|=1<<i;
    if(mask||mask!=previous) lastActivity=now;
    previous=mask;
    // Power input is sampled independently of Wi-Fi authentication and audio.
    bool pressed=mask&1;
    if(pressed!=powerRaw) {powerRaw=pressed;changedAt=now;}
    if(now-changedAt>=30) powerStable=pressed;
    if(power.step(now,powerStable)) shutdownRequested=true;
    bool pairing=(int32_t)(pairingUntil-now)>0;
    uint32_t idle=now-lastActivity;
    uint8_t options[OPTIONS_SIZE];optionsSnapshot(options);
    int mode=options[4];float level=options[5]/100.0f;
    uint32_t timeout=options[6]*1000;
    // All templates observe the same idle timeout; zero explicitly means always on.
    if(timeout) {
      uint32_t fade=min(uint32_t(2000),timeout);
      if(idle>=timeout) level=0;
      else if(idle>timeout-fade) level*=float(timeout-idle)/fade;
    }
    if(mode==2) level*=0.2f+0.8f*(0.5f+0.5f*sinf(now*2.0f*PI/2400.0f));
    if(pairing&&mode) level=(now/250)%2?options[5]/100.0f:0;
    if(!mode) level=0;
    uint8_t colors[4][3]={};
    for(int pixel=0;pixel<4;pixel++) {
      uint8_t color[]={options[7],options[8],options[9]};
      if(mode==3&&!pairing) colorWheel(uint8_t(now/20+pixel*64),color);
      if(mode==4&&(mask&2)) {color[0]=255;color[1]=40;color[2]=40;}
      if(mode==4&&(mask&12)) {color[0]=32;color[1]=255;color[2]=100;}
      for(int i=0;i<3;i++) colors[pixel][i]=uint8_t(color[i]*level);
    }
    uint8_t brightness=uint8_t(255*level);
    if(shutdownRequested) brightness=0;
    if(shutdownRequested) memset(colors,0,sizeof(colors));
    setStatusBrightness(brightness);
    setRingColors(colors);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}
void beginHardwareControls() {
  ringReady=rmtInit(2,RMT_TX_MODE,RMT_MEM_NUM_BLOCKS_1,10000000);
  clearRing();
  statusPwm=ledcAttach(6,5000,8);
  lastActivity=millis();
  Serial.printf("HARDWARE led_active_low=1 ring_pixels=4 pwm=%d ring=%d\n",statusPwm,ringReady);
  xTaskCreatePinnedToCore(hardwareTask,"hardware",3072,nullptr,1,nullptr,1);
}
void powerOff() {
  Serial.println("POWER_OFF long_press_released");
  keysSuspended=true;
  if(authenticated) sendFrame(10);
  disconnectPeer();
  if(bleLink) keysServer->disconnect(keysServer->getConnId());
  BLEDevice::getAdvertising()->stop();
  WiFi.mode(WIFI_OFF);
  setStatusBrightness(0);clearRing();
  playPowerSound(false);
  digitalWrite(7,LOW);
  // External hold circuit cuts battery power. USB may continue supplying power;
  // deep sleep then keeps lights and radios off until POWER is pressed again.
  esp_sleep_enable_ext0_wakeup(GPIO_NUM_12,0);
  digitalWrite(15,LOW);
  delay(100);
  esp_deep_sleep_start();
}
