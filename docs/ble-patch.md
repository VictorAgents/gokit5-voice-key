# 随项目提供的 BLE 注册修复

基线：Espressif Arduino-ESP32 3.3.12 的 `libraries/BLE`。文件原有版权和逐文件许可全部保留；BLEService.cpp 原文为 Apache-2.0，其他文件以自身头部为准。

修改日期：2026-10-31（公开项目同步）。修改位置：`firmware/libraries/BLE/src/BLEService.cpp` 的 `BLEService::addCharacteristic`。

原检查在发现相同UUID时直接返回，会忽略另一个使用相同HID Report UUID `0x2A4D` 的特征对象。键盘和消费者报告需要各自注册的句柄；未注册对象后续notify曾导致崩溃。

修复只在既有指针与传入对象相同（`pExisting == pCharacteristic`）时跳过注册；UUID相同、对象不同则允许注册。设备初始化额外检查两个报告句柄有效且不同，检查失败停止BLE任务。

构建必须传 `--library firmware/libraries/BLE`，避免意外采用全局未修复库。修改仅作用于本项目，不改Arduino安装目录。

上游来源：https://github.com/espressif/arduino-esp32/tree/3.3.12/libraries/BLE 。重建方法见根README；框架、SDK及其他文件的许可见THIRD_PARTY.md、licenses和源码头部。
