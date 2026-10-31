#pragma once
#include <driver/i2s_std.h>
#include "es8311.h"
#include "device_options.h"
#include "custom_audio.h"

i2s_chan_handle_t speaker=nullptr;
es8311_handle_t audioCodec=nullptr;
bool soundReady=false;
extern i2s_chan_handle_t mic;

// Full-duplex TX shares the microphone's 16 kHz clock; PA is enabled only for
// a bounded prompt, and disabled on every success/failure exit.
uint8_t playTone(uint8_t tone,uint8_t volume,bool shutdown=false) {
  if(!tone||!volume) return 1;
  if(!soundReady) return 3;
  if(tone>3||volume>100) return 4;
  uint8_t event=shutdown?1:0;
  if(tone==3&&!customValid[event]) return 5;
  digitalWrite(7,LOW);
  // ES8311 register 0x32 is a dB scale, not a perceptual percent scale.
  // Driver value 75 maps to 0xBF (0 dB); adjust prompt PCM amplitude instead.
  // Reference: https://files.waveshare.com/wiki/common/ES8311.DS.pdf
  bool ok=es8311_voice_volume_set(audioCodec,75,nullptr)==ESP_OK;
  ok=ok&&es8311_voice_mute(audioCodec,false)==ESP_OK;
  size_t written=0;int16_t block[128]={};
  // Clear TX before enabling the amplifier to avoid a start-up click.
  ok=ok&&i2s_channel_write(speaker,block,sizeof(block),&written,50)==ESP_OK;
  if(ok) {delay(12);digitalWrite(7,HIGH);delay(8);}
  int notes=tone==2?2:1;
  if(tone==3) {
    uint32_t base=customOffset(event,customSlot[event],customLegacy[event])+CUSTOM_HEADER;
    for(uint32_t offset=0;ok&&offset<customSize[event];) {
      size_t count=min(uint32_t(sizeof(block)),customSize[event]-offset);
      ok=esp_partition_read(customPartition,base+offset,block,count)==ESP_OK;
      for(size_t i=0;i<count/2;i++) block[i]=int16_t(constrain(int(block[i]),-7000,7000)*int(volume)/100);
      written=0;
      if(ok) ok=i2s_channel_write(speaker,block,count,&written,50)==ESP_OK&&written==count;
      offset+=count;
    }
    memset(block,0,sizeof(block));
    if(ok) i2s_channel_write(speaker,block,sizeof(block),&written,50);
  }
  for(int note=0;tone!=3&&ok&&note<notes;note++) {
    const int samples=16000*(tone==2?120:180)/1000;
    float frequency=shutdown?(note==0?660.0f:440.0f):(note==0?660.0f:880.0f);
    if(tone==1) frequency=shutdown?440.0f:880.0f;
    for(int offset=0;ok&&offset<samples;offset+=128) {
      int count=min(128,samples-offset);
      for(int i=0;i<count;i++) {
        int n=offset+i;
        float envelope=min(1.0f,min(float(n)/240.0f,float(samples-1-n)/240.0f));
        block[i]=int16_t(7000.0f*(volume/100.0f)*envelope*sinf(2.0f*PI*frequency*n/16000.0f));
      }
      size_t bytes=count*sizeof(int16_t);written=0;
      ok=i2s_channel_write(speaker,block,bytes,&written,50)==ESP_OK&&written==bytes;
    }
    memset(block,0,sizeof(block));
    if(ok) {written=0;ok=i2s_channel_write(speaker,block,sizeof(block),&written,50)==ESP_OK;delay(12);}
  }
  // Allow the last queued samples to drain (6 x 240 samples = 90 ms max).
  delay(ok?100:0);
  digitalWrite(7,LOW);
  es8311_voice_mute(audioCodec,true);
  Serial.printf("TONE tone=%u volume=%u result=%u\n",tone,volume,ok?0:4);
  return ok?0:4;
}
void playPowerSound(bool startup) {
  uint8_t options[OPTIONS_SIZE];optionsSnapshot(options);
  playTone(options[startup?1:2],options[3],!startup);
}
uint8_t playRecordingReadySound() {
  uint8_t options[OPTIONS_SIZE];optionsSnapshot(options);
  // Reuse the physically accepted 180ms soft prompt. A shortened 90ms cue
  // returned driver success but was not audible in the first user test.
  return playTone(1,options[3],false);
}
void discardRecordingPrompt() {
  // Empty the 90ms RX DMA backlog and a short acoustic tail before sending PCM.
  int16_t discard[240];size_t used=0;uint32_t began=millis();
  do {i2s_channel_read(mic,discard,sizeof(discard),&used,10);} while(millis()-began<80);
  for(int i=0;i<8;i++) i2s_channel_read(mic,discard,sizeof(discard),&used,0);
}
