#pragma once
#include <stdint.h>

// Only the current, still-held request may acknowledge readiness. Releasing,
// reconnecting, a timeout or replay must never play a late success prompt.
struct RecordingStartGate {
  bool waiting=false;
  uint32_t nonce=0,started=0;
  constexpr void begin(uint32_t now,uint32_t id) {waiting=true;nonce=id;started=now;}
  constexpr void cancel() {waiting=false;}
  constexpr bool expired(uint32_t now) const {return waiting&&uint32_t(now-started)>=2000;}
  constexpr bool accept(uint32_t now,uint32_t id,bool held) {
    if(!waiting||nonce!=id||!held||expired(now)) return false;
    waiting=false;return true;
  }
};
constexpr bool recordingStartChecks() {
  RecordingStartGate g;g.begin(100,17);
  if(g.accept(120,18,true)||g.accept(130,17,false)) return false;
  if(!g.accept(140,17,true)||g.accept(150,17,true)) return false;
  g.begin(200,19);g.cancel();if(g.accept(220,19,true)) return false;
  g.begin(300,20);if(g.accept(2300,20,true)) return false;
  g.begin(0xfffffff0u,21);if(!g.accept(20,21,true)) return false;
  g.begin(400,22);g.cancel();g.begin(450,23);
  return !g.accept(460,22,true)&&g.accept(470,23,true);
}
static_assert(recordingStartChecks(),"recording ready gate regression");
