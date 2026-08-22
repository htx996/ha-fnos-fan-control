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
    *   支持 PWM 的机型可在 Home Assistant 中通过 fan 实体调速
    *   仅在底层硬件模式经过验证时提供自动、手动、全速模式
    *   自动发现已安装的 LLLED 2.0.1 风扇后端，支持 LLLED 原厂兼容温控曲线
*   ​**UPS信息**​
    *   UPS电量显示
    *   UPS负载
    *   UPS状态

* * *

## 🔧 飞牛NAS端配置

### 现已支持非root用户访问，无需配置ssh，需要开启飞牛SSH服务

## 🌀 风扇接入方式

本集成支持以下两种飞牛 NAS 端方案。两者都由本集成通过现有 SSH 层读取，不会由 Home Assistant 自动安装驱动、应用或修改系统启动配置。

### 方式一：`ite-it87` 驱动

适合只需要标准 Linux hwmon 监控和直接 PWM 控制的用户。

1. 在飞牛应用中心安装与当前 fnOS 内核版本匹配的 `ite-it87` 驱动并启用。
2. 确认 `lsmod | grep '^it87'` 能看到模块。
3. 重启或重新加载“飞牛NAS”集成。

驱动会把 ITE Super I/O 芯片的温度、风扇转速和 PWM 通道暴露到 `/sys/class/hwmon`。本集成会自动发现实际 `hwmonX`，无需手工配置路径。上游 [frankcrawford/it87](https://github.com/frankcrawford/it87) 明确支持 IT8613E 的监控与控制。

在 DXP4800 Pro 上，本集成只使用已接线的 CPU 风扇和系统风扇通道。直接 hwmon 后端提供手动调速与全速，不把未经确认的 `pwm*_enable=2` 当作可靠自动温控。

### 方式二：[`LLLED_FPK`](https://github.com/BearHero520/LLLED_FPK)

适合需要自动温控、手动调速和全速模式的用户。

1. 从 LLLED_FPK Releases 下载 FPK，在飞牛应用中心手动安装。
2. 打开“绿联 LED 灯控”的 BIOS 与风扇页面，确认当前机型已识别风扇。
3. 完成应用要求的风扇写入风险确认。
4. 重启或重新加载“飞牛NAS”集成。

本集成检测到可用 LLLED 后会优先使用其本地接口，并创建全局“风扇控制模式”实体：自动模式由 LLLED 温控守护进程同时参考 CPU、硬盘和 NVMe 温度，手动模式允许通过 fan 实体设置百分比，全速模式写入最大 PWM。

LLLED_FPK 官方说明要求 fnOS 0.9.27 或更高版本、x86 平台和 root 权限；不同机型提供的风扇能力不同。其当前列表把 DXP4800 Pro 标为“待验证”，因此必须以应用 BIOS 与风扇页面的实际识别结果为准。

### 两种方式同时安装

`ite-it87` 只负责暴露硬件通道，LLLED 负责温控策略，两者可以同时安装。本集成检测到 LLLED 可用时优先由 LLLED 执行控制，同时利用 hwmon 数据并保持实体身份稳定。不要再并行运行其他风扇守护程序，也不要同时在 LLLED 页面和 Home Assistant 中反复写入固定转速。

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

## 仪表盘

仓库提供一套可选的中文 Lovelace 仪表盘，使用实体分类属性自动显示系统、存储卷、硬盘、风扇、UPS、虚拟机和 Docker。硬件数量变化后页面会自动适配，不需要填写 `hwmonX` 或逐个绑定硬盘实体。

### 准备前端卡片

先在 HACS 的“前端”分类安装并启用：

* [`button-card`](https://github.com/custom-cards/button-card)
* [`auto-entities`](https://github.com/thomasloven/lovelace-auto-entities)
* [`card-mod`](https://github.com/thomasloven/lovelace-card-mod)
* [`mini-graph-card`](https://github.com/kalkih/mini-graph-card)

安装或升级本集成并重启 Home Assistant 后，NAS 图片会自动安装到：

```text
/config/www/community/fn_nas/fn_nas.png
```

集成只维护这个固定文件，不会覆盖同目录中的其他用户文件。

### 导入卡片模板

1. 打开目标仪表盘，进入编辑模式。
2. 选择右上角菜单中的“原始配置编辑器”。
3. 将 [`dashboard/button_card_templates.yaml`](dashboard/button_card_templates.yaml) 的内容合并到仪表盘顶层。
4. 如果已有 `button_card_templates:`，只合并其中的 `fn_nas_metric` 和 `fn_nas_control`，不要重复创建顶层键，也不要覆盖现有 `views:`。

### 导入页面

1. 在同一仪表盘中新建一个空白视图。
2. 打开该视图的“以 YAML 编辑”。
3. 删除自动生成的视图内容，粘贴 [`dashboard/fn_nas_view.yaml`](dashboard/fn_nas_view.yaml) 的全部内容并保存。

主页面上的电源、重启、风扇和容器控制默认只打开实体详情，不会单击后立即执行高风险操作。当前页面会汇总所有已配置的 `fn_nas` 集成条目。

HACS 不会自动修改用户的 Lovelace 仪表盘。后续版本如果更新了页面结构，需要重新合并模板并替换视图 YAML；建议先备份自己对页面做过的调整。

## ⚠️ 注意事项

*   确保NAS与Home Assistant在同一局域网
*   首次配置后请等待5分钟完成初始数据采集
*   频繁扫描可能导致NAS负载升高
*   网络唤醒功能需在BIOS中启用Wake-on-LAN
*   风扇功能会自动发现 `/sys/class/hwmon`，不需要手动填写 `hwmonX`
*   如果标准 hwmon 没有风扇，会继续只读扫描 `/sys` 中的非标准 `fan*_input` / `pwm*` 路径
*   不支持 PWM 控制的机型只显示风扇监控实体，不会创建调速实体
*   对精确匹配 `UGREEN DXP4800 Pro` 的 IT8613 控制器，只暴露已接线的通道 2（CPU 风扇）和通道 3（系统风扇）；升级后会清理旧版误建的通道 1、4、5 实体
*   未使用 LLLED 时，DXP4800 Pro 的直接 hwmon 后端只提供“手动、全速”，不把未经验证的 `pwm*_enable=2` 当作自动温控；最低值限制为 PWM 80（约 31%），关闭 fan 实体也不会让散热风扇停转
*   如果安装了 LLLED 2.0.1 且 LLLED 的 BIOS 风扇页面显示可用，集成会优先使用 LLLED 后端，并新增一个全局“风扇控制模式”实体：
    *   “自动”启动 LLLED `ugreenctl-fand`，使用 LLLED 返回的精确机型原厂兼容曲线
    *   “手动”停止 LLLED 温控守护进程，并保留当前安全 PWM；从全速切回手动时恢复为 50%
    *   “全速”停止 LLLED 温控守护进程，再将 CPU 和系统风扇写为 PWM 255
*   LLLED 的自动模式是一个同时管理 CPU、硬盘和 NVMe 温度的全局软件温控器，不是 Linux `pwm*_enable=2`；因此不会给 CPU 和系统风扇分别创建相互冲突的模式下拉框
*   使用 LLLED 控制前，必须先在 LLLED 界面完成风扇写入风险确认，然后重新加载“飞牛NAS”集成。集成只在 LLLED 报告“已确认”后创建控制实体，并在每次写入时携带 LLLED 要求的确认令牌，不会替用户自动确认风险
*   已安装 `ite-it87` 时可同时使用 hwmon 监控数据；LLLED 可用时实际控制优先交给 LLLED。未安装该驱动时，如果 LLLED 的受保护直控后端可用，仍可创建风扇实体
*   `1.3.22` 修复 LLLED 与 hwmon 读取状态短暂切换时，风扇 ID 变化导致控制实体变灰、RPM/PWM/控制状态显示“未知”的问题；现有实体唯一 ID 保持不变
*   `1.3.23` 将 CPU、系统风扇迁移到稳定的物理通道标识；升级重启时优先保留最早创建实体的 `entity_id`，并自动移除同一风扇由旧 hwmon/LLLED 标识产生的“不可用”重复实体
*   `1.3.24` 使用 `verify` 统一仓库验证命名，移除文件型调试输出和强制 DEBUG，并补充 `ite-it87`、LLLED_FPK 两种风扇接入方案
*   `1.3.25` 增加可选中文 Lovelace 仪表盘、自动分类实体和原创 NAS 图片资源；不会自动修改用户已有的仪表盘配置
*   `1.3.26` 修复温度趋势卡片的实体列表配置，并提高浅色、深色主题下的文字对比度
*   `1.3.27` 将可选仪表盘改为统一无边框背景，并新增操作系统版本与内核信息实体
*   `1.3.28` 新增设备名称实体，系统版本改为显示 fnOS 产品版本，并保留基础系统与内核信息
*   `1.3.29` 将仪表盘改为图形化容量、硬盘和风扇面板，并使用原生 Tile 提供模式与风扇速度控制
*   `1.3.30` 压缩仪表盘各版块留白，移除重复的大型调速滑块；风扇动画卡直接显示 PWM 百分比，点击卡片仍可打开对应风扇实体调速
*   不要同时在 LLLED 页面和 Home Assistant 中反复设置固定转速。两者使用同一 LLLED 控制器，最后一次操作会决定当前模式和 PWM
*   本仓库不包含或复制 LLLED 的程序、二进制文件或源代码，只通过现有 SSH 层调用用户已经安装的 LLLED 接口
*   从旧版本升级后如果风扇仍处于全速，请在模式下拉框选择“手动”，集成会自动恢复到 50%；也可以打开对应 fan 实体直接设置 40%-50%
*   直接 hwmon 后端的手动调速使用单个 SSH 事务依次写入手动模式、目标 PWM、再次确认手动模式，等待 1 秒后同时读取两个 sysfs 文件；如果仍被系统改回全速，请复制实体属性中的“最近控制结果”用于定位外部控制器回写
*   如果没有看到风扇实体，请打开“飞牛NAS系统监控”设备里的“风扇发现状态”实体，查看 `主机硬件`、`已加载风扇模块`、`可用风扇模块`、`相关服务`、`风扇服务详情`、`厂商风扇接口`、`风扇服务日志`、`风扇启动脚本`、`fnOS板级风扇配置`、`fancontrol运行状态`、`it87模块信息`、`风扇内核日志`、`风扇控制应用`、`hwmon候选`、`sysfs候选` 和 `sensors摘要` 属性
*   诊断过程只读取 DMI、内核模块、systemd 服务和本机 `9511` 端口状态；集成不会自动加载内核模块或修改 fnOS 系统配置
*   对检测到 `pwm-fancontrol.service` 的系统，集成只读取有限的 systemd 状态、执行程序路径、当前启动日志和 `/proc/it86` 接口是否存在；不会启动、停止或重启该服务
*   对固定路径 `/usr/trim/bin/pwm-fancontrol.sh`，只提取风扇相关且不含敏感关键词的命令行，并执行无副作用的语法检查；`it87` 仅执行模块信息查询和 `modprobe -n` 预演
*   集成只读解析 `/boot/board.json` 中的 `fan[]` 配置并检查其 `fsysfs` / `tsysfs` 路径，同时读取 `/usr/sbin/fancontrol` 文件元数据和现有进程状态；不会启动该程序或写入配置路径

### 🔄 问题排查

# 验证SSH连接
```shell
ssh root@<NAS_IP> -p <端口>
```
若连接失败，请检查：

*   防火墙设置
*   SSH服务状态
*   路由器端口转发配置

* * *

> 📌 建议使用固定IP分配给NAS设备以确保连接稳定

## 仓库验证

```shell
python3 -m verification.run
```

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
