#pragma once
#include <stdint.h>

// Input is debounced. Reserve a brief window before forwarding individual keys.
// A short chord fires on release; a 3 s chord enters pairing instead.
struct PairButtons {
  enum State { Blocked, Idle, Pending, Individual, Chord } state=Blocked;
  uint32_t since=0;
  uint8_t sent=0;
  struct Events { uint8_t down=0, up=0; bool chord=false, pair=false; };
  constexpr Events step(uint32_t now, uint8_t mask) {
    Events e;
    if(state==Blocked) { if(!mask) state=Idle; return e; }
    if(state==Idle) {
      if(!mask) return e;
      state=mask==3?Chord:Pending;since=now;sent=mask;
      return e;
    }
    if(state==Pending) {
      if(mask==3) {state=Chord;since=now;return e;}
      if(!mask) {e.down=sent;e.up=sent;state=Idle;return e;}
      if(mask!=sent) {e.down=sent;e.up=sent;sent=mask;since=now;return e;}
      if(uint32_t(now-since)>=120) {state=Individual;e.down=mask;sent=mask;}
      return e;
    }
    if(state==Individual) {
      e.down=mask&~sent;e.up=sent&~mask;sent=mask;
      if(!mask) state=Idle;
      return e;
    }
    if(mask!=3) {e.chord=true;state=mask?Blocked:Idle;}
    else if(uint32_t(now-since)>=3000) {e.pair=true;state=Blocked;}
    return e;
  }
};

struct PowerButton {
  bool armed=false, down=false;
  uint32_t since=0;
  constexpr bool step(uint32_t now, bool pressed) {
    if(!pressed&&!down) armed=true;
    if(pressed&&!down) {down=true;since=now;}
    if(!pressed&&down) {down=false;bool off=armed&&uint32_t(now-since)>=2000;armed=true;return off;}
    return false;
  }
};

// Compile-time regressions exercise the same state machine used by the device.
constexpr bool buttonLogicChecks() {
  PairButtons b;
  b.step(0,0);b.step(10,1);
  if(b.step(50,0).down!=1) return false; // quick single tap is retained
  b.step(100,1);
  if(b.step(150,3).down) return false; // no component key in chord
  auto shortChord=b.step(300,2);
  if(!shortChord.chord||shortChord.down||b.step(350,2).down) return false;
  b.step(400,0);b.step(500,3);
  if(!b.step(3500,3).pair||b.step(3510,3).pair||b.step(3600,0).chord) return false;
  PowerButton p;
  p.step(0,true);
  if(p.step(3000,false)) return false; // boot hold cannot switch off
  p.step(4000,true);
  if(!p.step(6100,false)) return false;
  PairButtons wrap;wrap.step(0,0);wrap.step(0xfffffff0u,1);
  if(wrap.step(150,1).down!=1||wrap.step(160,0).up!=1) return false;
  return true;
}
static_assert(buttonLogicChecks(), "button gesture regression");
