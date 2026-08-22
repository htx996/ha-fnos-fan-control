# 飞牛NAS集成

> 此集成支持在Home Assistant中监控和控制飞牛NAS设备

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=htx996&repository=ha-fnos-fan-control&category=integration)

## 📊 功能列表

*   ​**硬件监控**​
    *   硬盘温度
    *   硬盘健康状态
    *   硬盘通电时间
    *   风扇转速 RPM
    *   风扇 PWM 百分比和当前控制状态
    *   风扇发现状态诊断
*   ​**系统监控**​
    *   系统运行状态
    *   CPU温度监控
*   ​**设备控制**​
    *   设备重启按钮
    *   设备关机按钮
    *   电源开关（支持网络唤醒开机）
    *   飞牛虚拟机开关机控制
    *   飞牛docker开关控制
    *   支持 PWM 的机型可在 Home Assistant 中通过 fan 实体 0-100% 调速
    *   支持 `pwm*_enable` 的机型可切换自动、手动、全速模式
*   ​**UPS信息**​
    *   UPS电量显示
    *   UPS负载
    *   UPS状态

* * *

## 🔧 飞牛NAS端配置

### 现已支持非root用户访问，无需配置ssh，需要开启飞牛SSH服务

## 💻 Home Assistant安装

1.  进入**HACS商店**​
2.  添加自定义存储库：
```shell
https://github.com/htx996/ha-fnos-fan-control
```
3.  搜索"飞牛NAS"，点击下载
4.  ​**重启Home Assistant服务**

## ⚙️ 集成配置

1.  添加新集成 → 搜索"飞牛NAS"
2.  配置参数：
    *   NAS IP地址（必填）
    *   SSH端口（默认：22）
    *   SSH用户名和密码
    *   MAC地址（用于网络唤醒）
    *   扫描间隔（推荐≥300秒）

## ⚠️ 注意事项

*   确保NAS与Home Assistant在同一局域网
*   首次配置后请等待5分钟完成初始数据采集
*   频繁扫描可能导致NAS负载升高
*   网络唤醒功能需在BIOS中启用Wake-on-LAN
*   风扇功能会自动发现 `/sys/class/hwmon`，不需要手动填写 `hwmonX`
*   如果标准 hwmon 没有风扇，会继续只读扫描 `/sys` 中的非标准 `fan*_input` / `pwm*` 路径
*   不支持 PWM 控制的机型只显示风扇监控实体，不会创建调速实体
*   如果没有看到风扇实体，请打开“飞牛NAS系统监控”设备里的“风扇发现状态”实体，查看 `hwmon候选`、`sysfs候选`、`散热设备`、`sensors摘要`、`sensors_u摘要` 属性

### 🔄 问题排查

# 测试SSH连接
```shell
ssh root@<NAS_IP> -p <端口>
```
若连接失败，请检查：

*   防火墙设置
*   SSH服务状态
*   路由器端口转发配置

* * *

> 📌 建议使用固定IP分配给NAS设备以确保连接稳定
# 免责声明

1. **非官方性质**  
   本插件为**非官方第三方开发**，与飞牛NAS官方无任何关联。

2. **使用风险自担**  
   使用本插件可能导致不可预见的系统问题或数据风险，用户需自行承担所有使用风险。

3. **无质量保证**  
   插件开发者在法律允许的最大范围内：
   - 不保证功能的完整性或准确性
   - 不承担因使用插件导致的任何直接/间接损失
   - 不提供任何明示或暗示的保证

4. **数据安全责任**  
   用户需自行确保插件操作不会危及：
   - 系统稳定性
   - 数据完整性
   - 网络安全性

5. **反馈即授权**  
   用户反馈将被视为授权开发者用于插件改进（不含敏感个人信息）。

**继续使用本插件即表示您已阅读、理解并接受上述免责条款。**
