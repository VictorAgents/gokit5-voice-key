#pragma once
#include <Preferences.h>

// schema, boot tone, shutdown tone, volume, light template, brightness,
// idle seconds (0=always on), red, green, blue.
constexpr size_t OPTIONS_SIZE=10;
uint8_t deviceOptions[OPTIONS_SIZE]={1,1,1,25,1,32,5,32,85,255};
Preferences optionsPreferences;
SemaphoreHandle_t optionsMutex;
constexpr bool validOptions(const uint8_t *p) {
  return p[0]==1&&p[1]<=3&&p[2]<=3&&p[3]<=100&&p[4]<=4&&p[5]<=100&&p[6]<=60;
}
void optionsSnapshot(uint8_t *out) {
  xSemaphoreTake(optionsMutex,portMAX_DELAY);memcpy(out,deviceOptions,OPTIONS_SIZE);xSemaphoreGive(optionsMutex);
}
void beginDeviceOptions() {
  optionsMutex=xSemaphoreCreateMutex();optionsPreferences.begin("gokit-options",false);
  uint8_t saved[OPTIONS_SIZE];
  if(optionsPreferences.getBytesLength("options")==OPTIONS_SIZE&&
     optionsPreferences.getBytes("options",saved,OPTIONS_SIZE)==OPTIONS_SIZE&&validOptions(saved)) memcpy(deviceOptions,saved,OPTIONS_SIZE);
}
bool applyOptions(const uint8_t *data) {
  if(!validOptions(data)) return false;
  uint8_t old[OPTIONS_SIZE];optionsSnapshot(old);
  if(!memcmp(old,data,OPTIONS_SIZE)) return true;
  if(optionsPreferences.putBytes("options",data,OPTIONS_SIZE)!=OPTIONS_SIZE) return false;
  xSemaphoreTake(optionsMutex,portMAX_DELAY);memcpy(deviceOptions,data,OPTIONS_SIZE);xSemaphoreGive(optionsMutex);
  return true;
}
