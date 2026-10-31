#pragma once
#include <stdint.h>

// Decide once at each debounced press. Recovery never moves an in-flight
// utterance between PC-injected Alt and BLE Alt (separate key owners).
struct MicRouteLatch {
  enum Route:uint8_t {None,Audio,Ble};
  Route route=None;
  bool armed=false;
  uint32_t since=0;
  constexpr void block() {route=None;armed=false;}
  constexpr Route step(uint32_t now,bool held,bool voiceMode,bool receiver,bool ble) {
    if(!held) {route=None;armed=true;return route;}
    if(!voiceMode) {block();return route;}
    if(armed) {armed=false;since=now;route=receiver?Audio:(ble?Ble:None);}
    if((route==Ble&&!ble)||uint32_t(now-since)>=120000) route=None;
    return route;
  }
};
constexpr bool micRouteChecks() {
  MicRouteLatch r;
  if(r.step(0,true,true,false,true)!=r.None) return false; // boot-held
  r.step(1,false,true,false,true);
  if(r.step(2,true,true,false,true)!=r.Ble) return false;
  if(r.step(3,true,true,true,true)!=r.Ble) return false; // receiver recovers
  if(r.step(4,true,true,true,false)!=r.None) return false; // BLE lost
  if(r.step(5,true,true,true,true)!=r.None) return false; // no replay on reconnect
  r.step(6,false,true,true,true);
  if(r.step(7,true,true,true,true)!=r.Audio) return false;
  if(r.step(8,true,true,false,true)!=r.Audio) return false; // no mid-hold fallback
  r.step(9,false,true,false,false);
  if(r.step(10,true,true,false,false)!=r.None||r.step(11,true,true,false,true)!=r.None) return false;
  r.step(12,false,true,false,true);
  if(r.step(13,true,false,false,true)!=r.None) return false; // shortcut modes own their keys
  r.step(14,false,true,false,true);r.step(15,true,true,false,true);r.block();
  if(r.step(16,true,true,false,true)!=r.None) return false;
  r.step(0xfffffff0u,false,true,false,true);r.step(0xfffffff1u,true,true,false,true);
  if(r.step(119983,true,true,false,true)!=r.Ble||r.step(119985,true,true,false,true)!=r.None) return false;
  return true;
}
static_assert(micRouteChecks(),"microphone route latch regression");
