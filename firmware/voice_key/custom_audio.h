#pragma once
#include <esp_partition.h>
#include <mbedtls/md.h>
#include <Preferences.h>

constexpr uint32_t CUSTOM_MAX_BYTES=192000,CUSTOM_SLOT_SIZE=0x30000,CUSTOM_HEADER=64;
// Leave all four original 3-second slots untouched, including on rollback.
constexpr uint32_t CUSTOM_BASE=0x80000,LEGACY_SLOT_SIZE=0x20000;
static_assert(CUSTOM_HEADER+CUSTOM_MAX_BYTES<=CUSTOM_SLOT_SIZE,"Audio exceeds slot");
static_assert(CUSTOM_BASE+4*CUSTOM_SLOT_SIZE<=0x160000,"Audio exceeds existing partition");
const esp_partition_t *customPartition=nullptr;
Preferences customPreferences;
bool customValid[2]={};uint32_t customSize[2]={};uint8_t customSlot[2]={},customHash[2][32]={};
bool customLegacy[2]={};
struct UploadState {
  bool active=false;uint8_t event=0,slot=0;
  uint32_t nonce=0,size=0,received=0,last=0;uint8_t hash[32];
} uploadState;
uint32_t readBE32(const uint8_t *p) {return(uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
void writeBE32(uint8_t *p,uint32_t n) {p[0]=n>>24;p[1]=n>>16;p[2]=n>>8;p[3]=n;}
uint32_t customOffset(uint8_t event,uint8_t slot,bool legacy=false) {
  return legacy?(uint32_t(event)*2+slot)*LEGACY_SLOT_SIZE:
    CUSTOM_BASE+(uint32_t(event)*2+slot)*CUSTOM_SLOT_SIZE;
}
bool fileHash(uint32_t offset,uint32_t size,uint8_t *hash) {
  mbedtls_md_context_t ctx;mbedtls_md_init(&ctx);
  bool ok=mbedtls_md_setup(&ctx,mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),0)==0;
  if(ok) ok=mbedtls_md_starts(&ctx)==0;
  uint8_t block[1024];
  for(uint32_t done=0;ok&&done<size;) {
    size_t count=min(uint32_t(sizeof(block)),size-done);
    ok=esp_partition_read(customPartition,offset+done,block,count)==ESP_OK&&mbedtls_md_update(&ctx,block,count)==0;
    done+=count;
  }
  if(ok) ok=mbedtls_md_finish(&ctx,hash)==0;
  mbedtls_md_free(&ctx);return ok;
}
bool loadCustomSlot(uint8_t event,bool legacy) {
  uint32_t pointer=customPreferences.getUInt(legacy?(event?"shutdown":"startup"):(event?"shutdown6":"startup6"),0);
  if(!customPartition||pointer<1||pointer>2) return false;
  uint8_t slot=pointer-1;
  uint8_t header[CUSTOM_HEADER],hash[32];uint32_t offset=customOffset(event,slot,legacy);
  if(esp_partition_read(customPartition,offset,header,sizeof(header))!=ESP_OK||memcmp(header,"GK5A",4)||header[4]!=1||header[5]!=event) return false;
  uint32_t size=readBE32(header+8);
  if(!size||size>(legacy?96000:CUSTOM_MAX_BYTES)||size%2||!fileHash(offset+CUSTOM_HEADER,size,hash)||memcmp(hash,header+12,32)) return false;
  customSlot[event]=slot;customLegacy[event]=legacy;
  customValid[event]=true;customSize[event]=size;memcpy(customHash[event],hash,32);
  return true;
}
void loadCustom(uint8_t event) {
  customValid[event]=false;customSize[event]=0;memset(customHash[event],0,32);
  if(!loadCustomSlot(event,false)) loadCustomSlot(event,true);
}
void beginCustomAudio() {
  customPreferences.begin("gokit-audio",false);
  customPartition=esp_partition_find_first(ESP_PARTITION_TYPE_DATA,ESP_PARTITION_SUBTYPE_DATA_SPIFFS,"spiffs");
  if(customPartition&&customPartition->size<CUSTOM_BASE+4*CUSTOM_SLOT_SIZE) customPartition=nullptr;
  loadCustom(0);loadCustom(1);
  Serial.printf("CUSTOM_AUDIO storage=%d boot=%u shutdown=%u\n",customPartition!=nullptr,customSize[0],customSize[1]);
}
uint8_t beginUpload(const uint8_t *p) {
  if(!customPartition) return 5;
  uint32_t size=readBE32(p+6);
  if(p[0]!=1||p[1]>1||!size||size>CUSTOM_MAX_BYTES||size%2) return 3;
  uploadState.active=false;uploadState.event=p[1];uploadState.nonce=readBE32(p+2);
  uploadState.slot=customValid[p[1]]&&!customLegacy[p[1]]?(customSlot[p[1]]^1):0;
  uploadState.size=size;uploadState.received=0;
  memcpy(uploadState.hash,p+10,32);
  // Only erase the inactive slot. Active audio survives interruption/power loss.
  if(esp_partition_erase_range(customPartition,customOffset(uploadState.event,uploadState.slot),CUSTOM_SLOT_SIZE)!=ESP_OK) return 5;
  uploadState.active=true;uploadState.last=millis();return 0;
}
uint8_t uploadChunk(const uint8_t *p,size_t size) {
  if(!uploadState.active||readBE32(p)!=uploadState.nonce) return 6;
  uint32_t offset=readBE32(p+4);size_t count=size-8;
  if(offset!=uploadState.received||!count||count%2||offset+count>uploadState.size) {uploadState.active=false;return 3;}
  if(esp_partition_write(customPartition,customOffset(uploadState.event,uploadState.slot)+CUSTOM_HEADER+offset,p+8,count)!=ESP_OK) {uploadState.active=false;return 5;}
  uploadState.received+=count;uploadState.last=millis();return 0;
}
uint8_t finishUpload(uint32_t nonce) {
  if(!uploadState.active||nonce!=uploadState.nonce) return 6;
  uploadState.active=false;
  if(uploadState.received!=uploadState.size) return 3;
  uint8_t hash[32],header[CUSTOM_HEADER]={};uint32_t offset=customOffset(uploadState.event,uploadState.slot);
  if(!fileHash(offset+CUSTOM_HEADER,uploadState.size,hash)) return 5;
  if(memcmp(hash,uploadState.hash,32)) return 4;
  memcpy(header,"GK5A",4);header[4]=1;header[5]=uploadState.event;writeBE32(header+8,uploadState.size);memcpy(header+12,hash,32);
  if(esp_partition_write(customPartition,offset,header,sizeof(header))!=ESP_OK) return 5;
  if(customPreferences.putUInt(uploadState.event?"shutdown6":"startup6",uploadState.slot+1)!=4) return 5;
  loadCustom(uploadState.event);return customValid[uploadState.event]?1:5;
}
