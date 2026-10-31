#include <Arduino.h>
#include <driver/i2c.h>
#include <driver/i2s_std.h>
#include <math.h>
#include "es8311.h"
#include "ble_keys.h"
#include "device_sounds.h"
#include "battery.h"
#include "wireless.h"
#include "hardware_controls.h"

// Verified gizwits-4key wiring; wireless microphone and configurable desktop keys.
constexpr int HOLD = 15, PA = 7, STATUS_LED = 6;
constexpr int BUTTONS[] = {12, 13, 14, 38};
constexpr const char *NAMES[] = {"POWER", "MIC", "PLUS", "MINUS"};
constexpr int RATE = 16000;
i2s_chan_handle_t mic = nullptr;
bool audioReady = false;
bool stable[4], raw[4];
uint32_t changed[4] = {}, lastReport = 0, inspectUntil = 0;
uint64_t squares = 0, sampleCount = 0;
int peak = 0;

bool checked(esp_err_t err, const char *step) {
  Serial.printf("INIT %s: %s\n", step, esp_err_to_name(err));
  return err == ESP_OK;
}

bool initAudio() {
  i2c_config_t bus = {};
  bus.mode = I2C_MODE_MASTER;
  bus.sda_io_num = 18;
  bus.scl_io_num = 17;
  bus.sda_pullup_en = GPIO_PULLUP_ENABLE;
  bus.scl_pullup_en = GPIO_PULLUP_ENABLE;
  bus.master.clk_speed = 100000;
  if (!checked(i2c_param_config(I2C_NUM_0, &bus), "i2c_config") ||
      !checked(i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0), "i2c_install")) return false;
  i2c_cmd_handle_t probe = i2c_cmd_link_create();
  i2c_master_start(probe);
  i2c_master_write_byte(probe, 0x18 << 1, true);
  i2c_master_stop(probe);
  esp_err_t found = i2c_master_cmd_begin(I2C_NUM_0, probe, pdMS_TO_TICKS(100));
  i2c_cmd_link_delete(probe);
  if (!checked(found, "es8311_at_0x18")) return false;

  i2s_chan_config_t channel = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  channel.dma_desc_num = 6;
  channel.dma_frame_num = 240;
  channel.auto_clear_after_cb = true;
  if (!checked(i2s_new_channel(&channel, &speaker, &mic), "i2s_duplex_channel")) return false;
  i2s_std_config_t config = {};
  config.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(RATE);
  config.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT; // S3 default macro otherwise selects BOTH slots.
  config.gpio_cfg.mclk = GPIO_NUM_16;
  config.gpio_cfg.bclk = GPIO_NUM_9;
  config.gpio_cfg.ws = GPIO_NUM_45;
  config.gpio_cfg.dout = GPIO_NUM_8;
  config.gpio_cfg.din = GPIO_NUM_10;
  if (!checked(i2s_channel_init_std_mode(speaker, &config), "i2s_output") ||
      !checked(i2s_channel_init_std_mode(mic, &config), "i2s_mode") ||
      !checked(i2s_channel_enable(speaker), "i2s_output_enable") ||
      !checked(i2s_channel_enable(mic), "i2s_enable")) return false;
  es8311_handle_t codec = es8311_create(I2C_NUM_0, ES8311_ADDRESS_0);
  audioCodec = codec;
  if (!codec) return false;
  es8311_clock_config_t clock = {};
  clock.mclk_from_mclk_pin = true;
  clock.mclk_frequency = RATE * 256;
  clock.sample_frequency = RATE;
  if (!checked(es8311_init(codec, &clock, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16), "es8311_init") ||
      !checked(es8311_microphone_config(codec, false), "analog_mic") ||
      !checked(es8311_microphone_gain_set(codec, ES8311_MIC_GAIN_24DB), "mic_gain")) return false;
  es8311_voice_mute(codec, true);
  soundReady = true;
  return true;
}

void setup() {
  digitalWrite(HOLD, HIGH);
  pinMode(HOLD, OUTPUT);
  digitalWrite(HOLD, HIGH);
  pinMode(PA, OUTPUT);
  digitalWrite(PA, LOW); // Speaker amplifier stays off.
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, HIGH);
  for (int i = 0; i < 4; ++i) {
    pinMode(BUTTONS[i], INPUT_PULLUP);
    stable[i] = raw[i] = digitalRead(BUTTONS[i]) == LOW;
  }
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0); // Debug output must not stall Wi-Fi when USB is attached but unread.
  delay(1500); // Bounded wait, including when powered by battery without USB.
  Serial.println("GOKIT5 BLE + WIFI PTT v0.13.0 profile=gizwits-4key");
  Serial.printf("RESET_REASON=%d\n",int(esp_reset_reason()));
  Serial.printf("CHIP=%s FLASH=%u PSRAM=%u\n", ESP.getChipModel(), ESP.getFlashChipSize(), ESP.getPsramSize());
  beginDeviceOptions();
  beginCustomAudio();
  beginBattery();
  audioReady = initAudio();
  playPowerSound(true);
  beginBleKeys();
  beginHardwareControls();
  wirelessBegin();
  Serial.printf("AUDIO_READY=%d RATE=%d; hold MIC for levels; serial m=10s levels, s=status\n", audioReady, RATE);
}

void loop() {
  if(shutdownRequested) powerOff();
  batteryTick();
  uint32_t now = millis();
  for (int i = 0; i < 4; ++i) {
    bool value = digitalRead(BUTTONS[i]) == LOW;
    if (value != raw[i]) { raw[i] = value; changed[i] = now; }
    if (value != stable[i] && now - changed[i] >= 25) {
      stable[i] = value;
      Serial.printf("KEY %s GPIO%d %s\n", NAMES[i], BUTTONS[i], value ? "DOWN" : "UP");
    }
  }
  static String command;
  while (Serial.available()) {
    char c=Serial.read();
    if(c=='\n') {
      command.trim();
      if(command=="m") inspectUntil=now+10000;
      if(command=="s") {
        Serial.printf("STATUS uptime=%lu audio=%d keys=%d%d%d%d hold=%d heap=%u minheap=%u\n",now,audioReady,stable[0],stable[1],stable[2],stable[3],digitalRead(HOLD),ESP.getFreeHeap(),ESP.getMinFreeHeap());
        wirelessStatus();
        Serial.printf("BATTERY ready=%d raw=%u pin_mv=%u battery_mv=%u percent_estimate=%u charge=%u reset=%d\n",batteryValid,batteryRaw,batteryPinMv,batteryMv,batteryPercent,chargeState,int(esp_reset_reason()));
        uint8_t o[OPTIONS_SIZE];optionsSnapshot(o);
        Serial.printf("OPTIONS boot=%u shutdown=%u volume=%u light=%u brightness=%u idle=%u rgb=%02x%02x%02x\n",o[1],o[2],o[3],o[4],o[5],o[6],o[7],o[8],o[9]);
        Serial.printf("BLE link=%d secure=%d auth_reason=%d pairing=%d LED_idle_ms=%lu\n",bleLink,bleConnected,bleAuthReason,(int32_t)(pairingUntil-now)>0,now-lastActivity);
      }
      if(command=="pair") beginPairing();
      if(command=="led") lastActivity=now;
      if(command=="sound") playPowerSound(true);
      if(command=="audio-clear") {
        customPreferences.remove("startup6");customPreferences.remove("shutdown6");customPreferences.remove("startup");customPreferences.remove("shutdown");loadCustom(0);loadCustom(1);
        Serial.println("CUSTOM_AUDIO_CLEARED");
      }
      wirelessCommand(command);command="";
    } else if(command.length()<320) command+=c;
    else command="";
  }
  wirelessTick(stable[1],audioReady,stable[2],stable[3]);
  bool inspect = stable[1] || (int32_t)(inspectUntil - now) > 0;
  if (audioReady) {
    int16_t samples[240];
    size_t bytes = 0;
    if (i2s_channel_read(mic, samples, sizeof(samples), &bytes, 20) == ESP_OK) {
      wirelessAudio(samples,bytes);
      if(inspect) {
      for (size_t i = 0; i < bytes / sizeof(int16_t); ++i) {
        int value = samples[i];
        peak = max(peak, abs(value));
        squares += (int64_t)value * value;
        ++sampleCount;
      }
      }
    }
  } else delay(5);
  if (now - lastReport >= 1000) {
    if (inspect && audioReady) Serial.printf("MIC samples=%llu peak=%d rms=%.1f\n", sampleCount, peak, sampleCount ? sqrt((double)squares / sampleCount) : 0.0);
    peak = 0; squares = 0; sampleCount = 0; lastReport = now;
  }
}
