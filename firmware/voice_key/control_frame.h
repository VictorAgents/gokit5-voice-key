#pragma once
#include <stdint.h>
#include <stddef.h>

struct ControlFrame {
  uint8_t buffer[512]={};
  size_t used=0,expected=8;
  uint32_t started=0;
  constexpr void reset() {used=0;expected=8;}
  constexpr bool expired(uint32_t now) const {return used&&uint32_t(now-started)>6000;}
  constexpr int feed(uint8_t c,uint32_t now) {
    if(used>=sizeof(buffer)) return -1;
    if(!used) started=now;
    buffer[used++]=c;
    if(used==8) {
      if(buffer[0]!='G'||buffer[1]!='K'||buffer[2]!='5'||buffer[3]!='P'||buffer[4]!=1) return -1;
      size_t size=(buffer[6]<<8)|buffer[7];
      if(size>504) return -1;
      expected=8+size;
    }
    return used==expected?1:0;
  }
};
constexpr bool controlFrameChecks() {
  ControlFrame p;
  uint8_t header[]={'G','K','5','P',1,12,0,41};
  for(auto c:header) if(p.feed(c,100)!=0) return false;
  if(p.expired(3100)||!p.expired(6101)) return false;
  for(int i=0;i<40;i++) if(p.feed(0,3200)) return false;
  if(p.feed(0,3200)!=1||p.expected!=49||p.buffer[5]!=12) return false;
  p.reset();header[5]=9;header[7]=0;
  for(int i=0;i<8;i++) if(p.feed(header[i],4000)!=(i==7?1:0)) return false;
  p.reset();header[6]=1;header[7]=248; // exact 504-byte boundary
  for(int i=0;i<8;i++) if(p.feed(header[i],4000)) return false;
  for(int i=0;i<504;i++) if(p.feed(0,4000)!=(i==503?1:0)) return false;
  p.reset();header[6]=1;header[7]=249; // 505 exceeds maximum payload
  for(int i=0;i<8;i++) if(p.feed(header[i],4000)!=(i==7?-1:0)) return false;
  p.reset();p.feed('G',0xfffffff0u);
  if(p.expired(200)||!p.expired(6100)) return false;
  p.reset();return p.used==0&&p.expected==8;
}
static_assert(controlFrameChecks(),"control TCP fragmentation regression");
