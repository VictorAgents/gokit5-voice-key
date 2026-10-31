#pragma once
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <mbedtls/base64.h>
#include <mbedtls/md.h>
#include <esp_random.h>
#include <sys/socket.h>
#include "control_frame.h"
#include "recording_start.h"

// GK5P v1 framing: magic[4], version, type, network-order uint16 length.
// PC initiates the connection; neither endpoint sends the pairing key.
Preferences preferences;
WiFiServer audioServer(8765);
WiFiClient peer;
uint8_t pairKey[32], challenge[16];
bool configured=false, authenticated=false, armed=false, streaming=false, listening=false;
uint32_t lastPing=0, lastHeartbeat=0, pressStarted=0;
uint32_t lastBatteryReport=0;
bool extraArmed[3]={false,false,false}, extraSent[3]={false,false,false};
uint32_t extraStarted[3]={0,0,0};
uint8_t pendingKeys[KEY_CONFIG_SIZE];bool keysPending=false;
int lastBleState=-1;
uint8_t pendingOptions[OPTIONS_SIZE];bool optionsPending=false;
ControlFrame controlFrame;
RecordingStartGate recordingStart;
std::atomic<uint16_t> lastWifiReason{0};

bool decodeKey(const String &hex) {
  if(hex.length()!=64) return false;
  for(int i=0;i<32;i++) {
    char a=hex[i*2], b=hex[i*2+1];
    if(!isxdigit(a)||!isxdigit(b)) return false;
    pairKey[i]=(uint8_t)strtoul(hex.substring(i*2,i*2+2).c_str(),nullptr,16);
  }
  return true;
}
void disconnectPeer() {
  voiceReceiverConnected.store(false);
  peer.stop(); authenticated=false; streaming=false; armed=false;
  recordingStart.cancel();
  controlFrame.reset();
  uploadState.active=false;
  lastBleState=-1;
  for(int i=0;i<3;i++) extraArmed[i]=extraSent[i]=false;
}
bool sendFrame(uint8_t type,const void *data=nullptr,size_t size=0) {
  if(size>504) return false;
  uint8_t packet[512]={ 'G','K','5','P',1,type,(uint8_t)(size>>8),(uint8_t)size };
  if(size) memcpy(packet+8,data,size);
  uint32_t started=millis();
  // One TCP write per PCM frame avoids a second tiny packet at 67 frames/s.
  if(!peer.connected() || peer.write(packet,size+8)!=size+8) {
    Serial.printf("NET_SEND_FAILED type=%u ms=%lu errno=%d rssi=%d\n",type,millis()-started,errno,WiFi.RSSI());
    disconnectPeer(); return false;
  }
  return true;
}
bool sendUploadAck(uint32_t nonce,uint8_t status,uint32_t received) {
  uint8_t packet[9];writeBE32(packet,nonce);packet[4]=status;writeBE32(packet+5,received);
  return sendFrame(23,packet,sizeof(packet));
}
bool sendCustomAssets() {
  uint8_t packet[75]={1};
  for(int i=0;i<2;i++) {int off=1+i*37;packet[off]=customValid[i];writeBE32(packet+off+1,customSize[i]);memcpy(packet+off+5,customHash[i],32);}
  return sendFrame(26,packet,sizeof(packet));
}
bool sendBatteryStatus() {
  uint8_t packet[14]={1,uint8_t(2|8|(batteryValid?1:0)|(batteryValid&&batteryPercent<=15?4:0)|
                                  (chargeState==1?16:chargeState==2?32:0)),batteryPercent};
  packet[3]=batteryRaw>>8;packet[4]=batteryRaw;
  packet[5]=batteryPinMv>>8;packet[6]=batteryPinMv;
  packet[7]=batteryMv>>8;packet[8]=batteryMv;
  writeBE32(packet+9,millis());packet[13]=uint8_t(esp_reset_reason());
  return sendFrame(27,packet,sizeof(packet));
}
bool readExact(uint8_t *out,size_t size,uint32_t deadline) {
  size_t done=0;
  while(done<size && peer.connected() && (int32_t)(deadline-millis())>0) {
    int count=peer.read(out+done,size-done);
    if(count>0) done+=count;
    else {uint8_t b;if(::recv(peer.fd(),&b,1,MSG_PEEK|MSG_DONTWAIT)==0) return false;delay(1);}
  }
  return done==size;
}
bool readFrame(uint8_t &type,uint8_t *data,size_t &size,uint32_t timeout=300) {
  uint8_t header[8]; uint32_t end=millis()+timeout;
  if(!readExact(header,8,end)) {Serial.println("NET_HEADER_TIMEOUT");return false;}
  if(memcmp(header,"GK5P",4) || header[4]!=1) {Serial.println("NET_INVALID_HEADER");return false;}
  type=header[5];size=(header[6]<<8)|header[7];
  return size<=64 && readExact(data,size,end);
}
// Control TCP packets can be fragmented or retransmitted. Never wait for the
// rest of a packet in the microphone loop; keep polling keys and draining I2S.
int pollControlFrame(uint8_t &type,uint8_t *data,size_t &size) {
  if(controlFrame.expired(millis())) {Serial.println("NET_CONTROL_TIMEOUT");return -1;}
  while(peer.available()) {
    int c=peer.read();if(c<0) break;
    int result=controlFrame.feed(uint8_t(c),millis());
    if(result<0) return -1;
    if(result>0) {
      type=controlFrame.buffer[5];size=controlFrame.expected-8;
      if(size) memcpy(data,controlFrame.buffer+8,size);
      controlFrame.reset();return 1;
    }
  }
  return 0;
}
void proof(const char *role,uint8_t out[32]) {
  uint8_t message[22]; memcpy(message,challenge,16); memcpy(message+16,role,6);
  mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),pairKey,32,message,22,out);
}
bool equalProof(const uint8_t *a,const uint8_t *b) {
  uint8_t difference=0; for(int i=0;i<32;i++) difference|=a[i]^b[i]; return difference==0;
}
void joinWifi(bool reconfigure=false) {
  disconnectPeer();
  WiFi.persistent(false);
  if(reconfigure) {
    // Stop an initial/failed SDK attempt before applying new STA credentials.
    // eraseap=false preserves flash settings; app preferences are already saved.
    WiFi.setAutoReconnect(false);WiFi.mode(WIFI_STA);
    WiFi.disconnect(true,false,1000);WiFi.mode(WIFI_OFF);
  }
  WiFi.mode(WIFI_STA); WiFi.setSleep(false);WiFi.setAutoReconnect(true);
  WiFi.begin(preferences.getString("ssid").c_str(),preferences.getString("pass").c_str());
}
void wirelessBegin() {
  WiFi.onEvent([](arduino_event_id_t event,arduino_event_info_t info) {
    uint16_t reason=info.wifi_sta_disconnected.reason;lastWifiReason.store(reason);
    // No SSID, BSSID, password or pairing key in this diagnostic.
    Serial.printf("WIFI_DISCONNECTED reason=%u\n",reason);
  },ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.onEvent([](arduino_event_id_t,arduino_event_info_t) {lastWifiReason.store(0);},ARDUINO_EVENT_WIFI_STA_GOT_IP);
  preferences.begin("gokit-ptt",false);
  configured=preferences.getString("ssid").length() && decodeKey(preferences.getString("key"));
  if(configured) joinWifi();
}
String decodeConfigField(const String &encoded) {
  uint8_t out[128];size_t used=0;
  if(mbedtls_base64_decode(out,127,&used,(const uint8_t*)encoded.c_str(),encoded.length())!=0) return "";
  out[used]=0;return String((const char*)out);
}
void wirelessCommand(const String &line) {
  if(line.startsWith("CFG ")) {
    int first=line.indexOf(' ',4), second=line.indexOf(' ',first+1);
    if(first<0||second<0) {Serial.println("CONFIG_ERROR format");return;}
    String ssid=decodeConfigField(line.substring(4,first));
    String password=decodeConfigField(line.substring(first+1,second));
    String key=line.substring(second+1);
    if(!ssid.length()||ssid.length()>32||password.length()<8||password.length()>63||!decodeKey(key)) {
      Serial.println("CONFIG_ERROR fields");return;
    }
    preferences.putString("ssid",ssid);preferences.putString("pass",password);preferences.putString("key",key);
    configured=true;joinWifi(true);Serial.println("CONFIG_OK");
  }
}
void wirelessStatus() {
  Serial.printf("WIRELESS v0.13 build=0.13.0 configured=%d wifi=%d ip=%s auth=%d stream=%d rssi=%d BLE=%d sound=%d pending=%d\n",configured,(int)WiFi.status(),WiFi.localIP().toString().c_str(),authenticated,streaming,(int)WiFi.RSSI(),bleConnected,soundReady,recordingStart.waiting);
  Serial.printf("WIFI_DIAGNOSTIC reason=%u\n",lastWifiReason.load());
}
void wirelessTick(bool pressed,bool ready,bool plus,bool minus) {
  bool audioMode=micUsesAudio();
  const bool extraPressed[3]={pressed&&!audioMode,plus,minus};
  pressed=pressed&&audioMode;
  uint32_t now=millis();
  if(WiFi.status()!=WL_CONNECTED) {
    if(listening) {audioServer.end();MDNS.end();listening=false;Serial.println("WIFI_SERVICE_STOPPED");}
    if(authenticated) disconnectPeer();return;
  }
  if(!listening) {
    audioServer.begin();audioServer.setNoDelay(true);listening=true;
    MDNS.begin((String("gokit5-")+deviceSuffix()).c_str());MDNS.addService("gokitptt","tcp",8765);
    Serial.println("WIFI_CONNECTED");
  }
  if(peer.connected() && peer.available()==0) {
    uint8_t byte;
    if(::recv(peer.fd(),&byte,1,MSG_PEEK|MSG_DONTWAIT)==0) {
      Serial.println("PEER_EOF");disconnectPeer();
    }
  }
  if(!peer.connected()) {
    disconnectPeer();peer=audioServer.accept();
    if(!peer) return;
    Serial.println("PEER_ACCEPTED");
    peer.setNoDelay(true); peer.setConnectionTimeout(1000);
    esp_fill_random(challenge,16);
    if(!sendFrame(1,challenge,16)) return;
    uint8_t type=0,received[64],expected[32];size_t size=0;
    proof("client",expected);
    if(!readFrame(type,received,size,5000)||type!=2||size!=32||!equalProof(received,expected)) {
      Serial.printf("AUTH_FAILED type=%u size=%u\n",type,(unsigned)size);
      disconnectPeer();audioServer.end();MDNS.end();listening=false;return;
    }
    proof("device",expected);
    if(!sendFrame(3,expected,32)) return;
    authenticated=true;voiceReceiverConnected.store(true);armed=!pressed;lastPing=lastHeartbeat=millis();
    for(int i=0;i<3;i++) extraArmed[i]=!extraPressed[i];
    const char version[]="0.13.0";
    if(!sendFrame(11,version,sizeof(version)-1)) return;
    if(!sendCustomAssets()) return;
    if(!sendBatteryStatus()) return;
    lastBatteryReport=millis();
    Serial.println("PEER_AUTHENTICATED");
  }
  if(!authenticated) return;
  if(lastBleState!=(int)bleConnected) {
    uint8_t connected=bleConnected?1:0;
    if(!sendFrame(14,&connected,1)) return;
    lastBleState=connected;
  }
  now=millis();
  if(uploadState.active&&(!keysAreReleased()||digitalRead(12)==LOW||now-uploadState.last>30000)) {
    uploadState.active=false;
    if(!sendUploadAck(uploadState.nonce,2,uploadState.received)) return;
  }
  uint8_t type,data[504];size_t size=0;
  int frame=pollControlFrame(type,data,size);
  if(frame<0) {Serial.println("NET_INVALID_CONTROL");disconnectPeer();return;}
  if(frame>0) {
    if(type==10&&!size) {Serial.println("PEER_GOODBYE");disconnectPeer();return;}
    if(type==12&&size==KEY_CONFIG_SIZE&&validKeys(data)) {memcpy(pendingKeys,data,KEY_CONFIG_SIZE);keysPending=true;}
    else if(type==15&&size==OPTIONS_SIZE&&validOptions(data)) {memcpy(pendingOptions,data,OPTIONS_SIZE);optionsPending=true;}
    else if(type==17&&size==8&&data[0]==1&&data[1]<=1&&data[2]<=3&&data[3]<=100) {
      // Preview is temporary and never changes saved settings.
      uint8_t result[5];memcpy(result,data+4,4);
      bool busy=streaming||uploadState.active||!keysAreReleased()||digitalRead(12)==LOW;
      result[4]=busy?2:playTone(data[2],data[3],data[1]==1);
      if(!sendFrame(18,result,sizeof(result))) return;
      // Discard idle microphone samples captured during the prompt.
      if(!busy&&soundReady) {int16_t discard[240];size_t used;for(int i=0;i<8;i++) i2s_channel_read(mic,discard,sizeof(discard),&used,0);}
    }
    else if(type==20&&size==42) {
      bool busy=streaming||!keysAreReleased()||digitalRead(12)==LOW;
      uint8_t result=busy?2:beginUpload(data);
      if(!sendUploadAck(readBE32(data+2),result,0)) return;
    }
    else if(type==21&&size>=10) {
      uint8_t result=uploadChunk(data,size);
      if(!sendUploadAck(readBE32(data),result,uploadState.received)) return;
    }
    else if(type==22&&size==4) {
      uint8_t result=finishUpload(readBE32(data));
      if(!sendUploadAck(readBE32(data),result,uploadState.received)) return;
      if(result==1&&!sendCustomAssets()) return;
    }
    else if(type==24&&size==4) {if(uploadState.nonce==readBE32(data)) uploadState.active=false;}
    else if(type==25&&size==0) {if(!sendCustomAssets()) return;}
    else if(type==29&&size==5&&data[4]<=1) {
      uint32_t nonce=readBE32(data);
      if(recordingStart.waiting&&recordingStart.nonce==nonce) {
        bool held=digitalRead(13)==LOW&&digitalRead(12)==HIGH&&micUsesAudio();
        if(data[4]||!recordingStart.accept(millis(),nonce,held)) {
          recordingStart.cancel();if(!sendFrame(30,data,4)) return;
          Serial.println("RECORDING_START_CANCELLED not_ready_or_released");
        } else {
          uint8_t result=playRecordingReadySound();
          if(result==0||result==1) discardRecordingPrompt();
          held=digitalRead(13)==LOW&&digitalRead(12)==HIGH&&micUsesAudio();
          if((result==0||result==1)&&held) {
            if(!sendFrame(4,data,4)) return;
            streaming=true;pressStarted=millis();
            Serial.printf("RECORDING_READY cue=%u nonce=%lu\n",result,nonce);
          } else {
            if(!sendFrame(30,data,4)) return;
            Serial.printf("RECORDING_START_CANCELLED cue=%u held=%d\n",result,held);
          }
        }
      }
    }
    else if(type!=9||size) {Serial.println("NET_INVALID_PING");disconnectPeer();return;}
    lastPing=millis();
  }
  now=millis(); // Refresh after reads: stale unsigned time can appear to wrap.
  if(keysPending&&keysAreReleased()) {
    bool saved=applyKeys(pendingKeys);keysPending=false;
    if(saved&&!sendFrame(13,pendingKeys,KEY_CONFIG_SIZE)) return;
  }
  if(optionsPending&&keysAreReleased()&&!streaming) {
    bool saved=applyOptions(pendingOptions);optionsPending=false;
    if(saved&&!sendFrame(16,pendingOptions,OPTIONS_SIZE)) return;
  }
  if(now-lastPing>8000) {Serial.printf("NET_HEARTBEAT_TIMEOUT gap=%lu rssi=%d\n",now-lastPing,WiFi.RSSI());disconnectPeer();return;}
  if(now-lastHeartbeat>=500) {if(!sendFrame(7))return;lastHeartbeat=now;}
  if(now-lastBatteryReport>=5000) {if(!sendBatteryStatus())return;lastBatteryReport=now;}
  // Frame 8 is status only for BLE shortcuts; the PC must not duplicate them.
  for(int i=0;i<3;i++) {
    if(extraSent[i]&&(!extraPressed[i]||now-extraStarted[i]>=120000)) {
      const uint8_t event[]={(uint8_t)(i+1),0};
      if(!sendFrame(8,event,2)) return;
      extraSent[i]=false;
    }
    if(!extraPressed[i]) extraArmed[i]=true;
    if(extraPressed[i]&&extraArmed[i]&&!extraSent[i]) {
      const uint8_t event[]={(uint8_t)(i+1),1};
      if(!sendFrame(8,event,2)) return;
      extraSent[i]=true;extraArmed[i]=false;extraStarted[i]=now;
    }
  }
  if(!pressed) armed=true;
  if(recordingStart.waiting&&(!pressed||recordingStart.expired(now))) {
    uint8_t nonce[4];writeBE32(nonce,recordingStart.nonce);recordingStart.cancel();
    if(!sendFrame(30,nonce,4)) return;
    Serial.println("RECORDING_START_CANCELLED released_or_timeout");
  }
  if(pressed&&armed&&!streaming&&!recordingStart.waiting&&ready&&!uploadState.active) {
    uint32_t nonce=esp_random();uint8_t data[4];writeBE32(data,nonce);
    recordingStart.begin(now,nonce);armed=false;
    if(!sendFrame(28,data,4)) return;
    Serial.printf("RECORDING_START_REQUEST nonce=%lu\n",nonce);
  }
  if(streaming&&(!pressed||now-pressStarted>=120000)) {
    sendFrame(6);streaming=false;
  }
}
void wirelessAudio(const int16_t *data,size_t size) {
  if(authenticated&&streaming) sendFrame(5,data,size);
}
