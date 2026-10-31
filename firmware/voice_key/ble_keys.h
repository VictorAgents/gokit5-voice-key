#pragma once
#include <BLEDevice.h>
#include <BLEHIDDevice.h>
#include <BLESecurity.h>
#include <Preferences.h>
#include "button_logic.h"
#include "mic_route.h"
#include <atomic>
#include <esp_mac.h>

String deviceSuffix() {
  uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);
  char suffix[7];snprintf(suffix,sizeof(suffix),"%02x%02x%02x",mac[3],mac[4],mac[5]);return String(suffix);
}

// Config: schema byte, then 4 x [mode, modifiers, keys[6], consumerLE16].
constexpr size_t KEY_CONFIG_SIZE=41;
uint8_t keyConfig[KEY_CONFIG_SIZE]={2,3,64};
SemaphoreHandle_t keysMutex;
volatile bool bleConnected=false;
volatile bool bleReady=false;
volatile bool bleLink=false, keysSuspended=false;
volatile uint32_t pairingUntil=0;
volatile int bleAuthReason=-1;
BLEServer *keysServer=nullptr;
BLECharacteristic *keyboardInput=nullptr, *consumerInput=nullptr;
Preferences keysPreferences;
std::atomic<bool> voiceReceiverConnected{false};
std::atomic<uint8_t> currentMicRoute{MicRouteLatch::None};
constexpr int keyPins[3]={13,14,38};

bool validKeys(const uint8_t *p) {
  if(p[0]!=2) return false;
  for(int i=0;i<4;i++) {
    const uint8_t *b=p+1+i*10;
    if(b[0]<1||b[0]>(i==0?3:2)) return false;
    if(i==3&&b[0]!=1) return false;
    for(int k=2;k<8;k++) if(b[k]&&(b[k]<4||b[k]>115)) return false;
    uint16_t c=b[8]|(b[9]<<8);
    if(c&&c!=0xe9&&c!=0xea&&c!=0xe2&&c!=0xcd&&c!=0xb5&&c!=0xb6) return false;
  }
  return true;
}
void keysSnapshot(uint8_t *out) {
  xSemaphoreTake(keysMutex,portMAX_DELAY);memcpy(out,keyConfig,KEY_CONFIG_SIZE);xSemaphoreGive(keysMutex);
}
bool micUsesAudio() {
  uint8_t config[KEY_CONFIG_SIZE];keysSnapshot(config);
  return config[1]==3&&currentMicRoute.load()==MicRouteLatch::Audio;
}
bool keysAreReleased() {
  return digitalRead(13)==HIGH&&digitalRead(14)==HIGH&&digitalRead(38)==HIGH;
}
bool applyKeys(const uint8_t *p) {
  uint8_t old[KEY_CONFIG_SIZE];keysSnapshot(old);
  if(!memcmp(old,p,KEY_CONFIG_SIZE)) return true;
  if(keysPreferences.putBytes("mapping",p,KEY_CONFIG_SIZE)!=KEY_CONFIG_SIZE) return false;
  xSemaphoreTake(keysMutex,portMAX_DELAY);memcpy(keyConfig,p,KEY_CONFIG_SIZE);xSemaphoreGive(keysMutex);
  return true;
}
class KeysServerCallbacks:public BLEServerCallbacks {
  void onConnect(BLEServer*) override {bleLink=true;Serial.println("BLE_LINK_CONNECTED waiting_auth");}
  void onDisconnect(BLEServer*) override {bleLink=bleConnected=false;Serial.println("BLE_LINK_DISCONNECTED");if(!keysSuspended) BLEDevice::startAdvertising();}
};
class KeysSecurity:public BLESecurityCallbacks {
  uint32_t onPassKeyRequest() override {return 0;}
  void onPassKeyNotify(uint32_t) override {}
  bool onConfirmPIN(uint32_t) override {return true;}
  bool onSecurityRequest() override {return true;}
#if defined(CONFIG_BLUEDROID_ENABLED)
  void onAuthenticationComplete(esp_ble_auth_cmpl_t p) override {
    bleConnected=p.success;bleAuthReason=p.success?0:p.fail_reason;
    Serial.printf("BLE_AUTH success=%d reason=%d\n",p.success,bleAuthReason);
  }
#elif defined(CONFIG_NIMBLE_ENABLED)
  void onAuthenticationComplete(ble_gap_conn_desc *p,int status) override {
    bleConnected=!status&&p->sec_state.encrypted;bleAuthReason=status;
    Serial.printf("BLE_AUTH success=%d reason=%d\n",bleConnected,status);
  }
#endif
};
void beginPairing() {
  if(keysSuspended) return;
  pairingUntil=millis()+60000;
  // Keep saved bonds: entering pairing is not a factory reset.
  if(bleLink) keysServer->disconnect(keysServer->getConnId());
  BLEDevice::getAdvertising()->stop();
  BLEDevice::startAdvertising();
  Serial.println("BLE_PAIRING 60s bonds_preserved");
}
void keyboardTask(void*) {
  bool stable[3]={},raw[3]={},armed[3]={},active[3]={};
  uint32_t changed[3]={},started[4]={};
  bool chordActive=false;
  PairButtons pairButtons;
  uint8_t previous[8]={},lastConfig[KEY_CONFIG_SIZE]={};uint16_t previousConsumer=0;
  bool wasConnected=false;
  MicRouteLatch micRoute;
  for(;;) {
    uint32_t now=millis();uint8_t config[KEY_CONFIG_SIZE];keysSnapshot(config);
    bool connected=bleConnected&&!keysSuspended;
    if(memcmp(config,lastConfig,KEY_CONFIG_SIZE)||keysSuspended) micRoute.block();
    if(connected!=wasConnected||memcmp(config,lastConfig,KEY_CONFIG_SIZE)) {
      for(int i=0;i<3;i++) {stable[i]=raw[i]=digitalRead(keyPins[i])==LOW;armed[i]=!stable[i];active[i]=false;}
      memset(previous,0xff,8);previousConsumer=0xffff;
      pairButtons=PairButtons{};chordActive=false;
      memcpy(lastConfig,config,KEY_CONFIG_SIZE);wasConnected=connected;
    }
    uint8_t report[8]={};uint16_t consumer=0;int count=0;
    for(int i=0;i<3;i++) {
      const uint8_t *b=config+1+i*10;
      bool value=digitalRead(keyPins[i])==LOW;
      if(value!=raw[i]) {raw[i]=value;changed[i]=now;}
      if(value!=stable[i]&&now-changed[i]>=25) {
        stable[i]=value;
        if(!value) {armed[i]=true;if(b[0]==2) active[i]=false;}
        else if(i==0&&connected&&armed[i]&&b[0]!=3) {active[i]=true;armed[i]=false;started[i]=now;}
      }
    }
    uint8_t route=micRoute.step(now,stable[0],config[1]==3&&!keysSuspended,
                               voiceReceiverConnected.load(),connected);
    if(currentMicRoute.exchange(route)!=route)
      Serial.printf("MIC_ROUTE %s\n",route==MicRouteLatch::Audio?"device_audio":route==MicRouteLatch::Ble?"ble_right_alt":"idle");
    // Fallback is always RightAlt, independent of the configured PTT shortcut.
    if(route==MicRouteLatch::Ble&&connected) report[0]|=64;
    auto events=pairButtons.step(now,(stable[1]?1:0)|(stable[2]?2:0));
    if(events.pair) beginPairing();
    if(events.chord&&connected) {chordActive=true;started[3]=now;Serial.println("KEY PLUS_MINUS TAP");}
    for(int i=1;i<3;i++) {
      const uint8_t *b=config+1+i*10;uint8_t bit=1<<(i-1);
      if((events.down&bit)&&connected) {active[i]=true;started[i]=now;}
      if((events.up&bit)&&b[0]==2) {
        // Even a fast hold-mode tap gets one complete report.
        if(!(events.down&bit)) active[i]=false;
      }
      if(b[0]==2&&!stable[i]&&now-started[i]>=35) active[i]=false;
    }
    if(chordActive&&now-started[3]>=35) chordActive=false;
    for(int i=0;i<4;i++) {
      const uint8_t *b=config+1+i*10;
      bool &isActive=i==3?chordActive:active[i];
      if(isActive&&((b[0]==1&&now-started[i]>=35)||now-started[i]>=120000)) isActive=false;
      if(!connected||!isActive) continue;
      report[0]|=b[1];
      for(int k=2;k<8;k++) if(b[k]) {
        bool seen=false;for(int n=2;n<8;n++) if(report[n]==b[k]) seen=true;
        if(!seen&&count<6) report[2+count++]=b[k];
      }
      uint16_t usage=b[8]|(b[9]<<8);if(usage) consumer=usage;
    }
    if(connected) {
      if(memcmp(report,previous,8)) {keyboardInput->setValue(report,8);keyboardInput->notify();memcpy(previous,report,8);}
      if(consumer!=previousConsumer) {uint8_t c[]={uint8_t(consumer),uint8_t(consumer>>8)};consumerInput->setValue(c,2);consumerInput->notify();previousConsumer=consumer;}
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
void beginBleKeys() {
  keysMutex=xSemaphoreCreateMutex();
  keyConfig[11]=1;keyConfig[21]=1;keyConfig[31]=1;
  keysPreferences.begin("gokit-keys",false);
  uint8_t saved[KEY_CONFIG_SIZE]={};size_t length=keysPreferences.getBytesLength("mapping");
  if((length==31||length==KEY_CONFIG_SIZE)&&keysPreferences.getBytes("mapping",saved,length)==length) {
    if(length==31&&saved[0]==1) {saved[0]=2;saved[31]=1;}
    if(validKeys(saved)) memcpy(keyConfig,saved,KEY_CONFIG_SIZE);
  }
  BLEDevice::init((String("Gokit5 Keys-")+deviceSuffix()).c_str());
  auto security=new BLESecurity();security->setCapability(ESP_IO_CAP_NONE);security->setAuthenticationMode(true,false,true);
  // Let the Windows host initiate pairing after service discovery.
  security->setForceAuthentication(false);
  BLEDevice::setSecurityCallbacks(new KeysSecurity());
  auto server=BLEDevice::createServer();keysServer=server;server->setCallbacks(new KeysServerCallbacks());
  auto hid=new BLEHIDDevice(server);hid->manufacturer()->setValue("Gokit5");hid->pnp(2,0x303a,0x4005,0x0050);hid->hidInfo(0,1);
  static uint8_t descriptor[]={
    0x05,1,0x09,6,0xa1,1,0x85,1,0x05,7,0x19,0xe0,0x29,0xe7,0x15,0,0x25,1,0x75,1,0x95,8,0x81,2,
    0x95,1,0x75,8,0x81,1,0x95,5,0x75,1,0x05,8,0x19,1,0x29,5,0x91,2,0x95,1,0x75,3,0x91,1,
    0x95,6,0x75,8,0x15,0,0x25,0x73,0x05,7,0x19,0,0x29,0x73,0x81,0,0xc0,
    0x05,0x0c,0x09,1,0xa1,1,0x85,2,0x15,0,0x26,0x9c,2,0x19,0,0x2a,0x9c,2,0x75,16,0x95,1,0x81,0,0xc0};
  hid->reportMap(descriptor,sizeof(descriptor));keyboardInput=hid->inputReport(1);hid->outputReport(1);consumerInput=hid->inputReport(2);
  uint8_t emptyKeys[8]={},emptyConsumer[2]={};
  keyboardInput->setValue(emptyKeys,8);consumerInput->setValue(emptyConsumer,2);
  hid->startServices();
  // Fail closed if an incompatible BLE library silently ignores duplicate
  // report UUIDs. Never notify a characteristic that was not registered.
  if(keyboardInput->getHandle()==0xffff||consumerInput->getHandle()==0xffff||keyboardInput->getHandle()==consumerInput->getHandle()) {
    Serial.println("BLE_INIT_FAILED report_registration; use project BLE library");return;
  }
  Serial.printf("BLE_REPORTS keyboard=%u consumer=%u\n",keyboardInput->getHandle(),consumerInput->getHandle());
  auto adv=BLEDevice::getAdvertising();adv->setAppearance(HID_KEYBOARD);adv->addServiceUUID(hid->hidService()->getUUID());adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  bleReady=xTaskCreatePinnedToCore(keyboardTask,"ble-keys",6144,nullptr,1,nullptr,1)==pdPASS;
  Serial.printf("BLE_READY=%d name=Gokit5 Keys-%s\n",bleReady,deviceSuffix().c_str());
}
