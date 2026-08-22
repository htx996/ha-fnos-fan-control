import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import (
    DOMAIN,
    DATA_UPDATE_COORDINATOR,
    DEVICE_ID_NAS,
    CONF_ENABLE_DOCKER,
    CONF_MAC,
)
from .dashboard import dashboard_metadata

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]
    enable_docker = domain_data.get(CONF_ENABLE_DOCKER, False)
    
    entities = []
    
    # Keep wake-up independent from SSH so it remains usable while the NAS is off.
    entities.extend(
        [
            PowerOnButton(config_entry),
            PowerOffButton(coordinator, config_entry.entry_id),
            RebootButton(coordinator, config_entry.entry_id),
        ]
    )
    
    # 2. Add independent VM actions while retaining legacy switches elsewhere.
    if "vms" in coordinator.data:
        for vm in coordinator.data["vms"]:
            entities.extend(
                [
                    VMStartButton(
                        coordinator,
                        vm["name"],
                        vm.get("title", vm["name"]),
                        config_entry.entry_id,
                    ),
                    VMShutdownButton(
                        coordinator,
                        vm["name"],
                        vm.get("title", vm["name"]),
                        config_entry.entry_id,
                    ),
                    VMRebootButton(
                        coordinator,
                        vm["name"],
                        vm.get("title", vm["name"]),
                        config_entry.entry_id,
                    ),
                ]
            )
    
    # 3. Add independent Docker actions when Docker support is enabled.
    if enable_docker and "docker_containers" in coordinator.data:
        for container in coordinator.data["docker_containers"]:
            safe_name = (
                container["name"]
                .replace(" ", "_")
                .replace("/", "_")
                .replace(".", "_")
            )
            entities.extend(
                [
                    DockerContainerStartButton(
                        coordinator,
                        container["name"],
                        safe_name,
                        config_entry.entry_id,
                    ),
                    DockerContainerStopButton(
                        coordinator,
                        container["name"],
                        safe_name,
                        config_entry.entry_id,
                    ),
                    DockerContainerRestartButton(
                        coordinator,
                        container["name"],
                        safe_name,
                        config_entry.entry_id,
                    ),
                ]
            )
    
    async_add_entities(entities)


class PowerOnButton(ButtonEntity):
    """Wake the NAS without depending on an active SSH coordinator."""

    def __init__(self, config_entry):
        self.config_entry = config_entry
        self._attr_name = "开机"
        self._attr_unique_id = f"{config_entry.entry_id}_flynas_power_on"
        self._dashboard_attributes = dashboard_metadata("control", "power_on", 10)
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS",
        }

    @property
    def available(self):
        return bool(self.config_entry.data.get(CONF_MAC))

    async def async_press(self):
        mac = self.config_entry.data.get(CONF_MAC)
        if not mac:
            _LOGGER.warning("无法唤醒系统，未配置MAC地址")
            return
        await self.hass.services.async_call(
            "wake_on_lan",
            "send_magic_packet",
            {"mac": mac},
        )

    @property
    def extra_state_attributes(self):
        return {
            **self._dashboard_attributes,
            "控制方式": "网络唤醒",
            "MAC地址": self.config_entry.data.get(CONF_MAC, "未配置"),
        }


class PowerOffButton(CoordinatorEntity, ButtonEntity):
    """Shut down the NAS through the existing coordinator command layer."""

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_name = "关机"
        self._attr_unique_id = f"{entry_id}_flynas_power_off"
        self._dashboard_attributes = dashboard_metadata("control", "power_off", 20)
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:power-off"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS",
        }

    async def async_press(self):
        await self.coordinator.shutdown_system()

    @property
    def extra_state_attributes(self):
        return {
            **self._dashboard_attributes,
            "提示": "按下此按钮将关闭飞牛NAS系统",
        }


class RebootButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_name = "重启"
        self._attr_unique_id = f"{entry_id}_flynas_reboot"
        self._dashboard_attributes = dashboard_metadata("control", "reboot", 30)
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:restart"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统",
            "manufacturer": "飞牛",
            "model": "飞牛NAS"
        }
    
    async def async_press(self):
        await self.coordinator.reboot_system()
        self.async_write_ha_state()
        
    @property
    def extra_state_attributes(self):
        return {
            **self._dashboard_attributes,
            "提示": "按下此按钮将重启飞牛NAS系统"
        }


class _VMActionButton(CoordinatorEntity, ButtonEntity):
    action = ""
    resulting_state = ""
    role = ""
    label = ""
    icon = "mdi:power"
    order = 20

    def __init__(self, coordinator, vm_name, vm_title, entry_id):
        super().__init__(coordinator)
        self.vm_name = vm_name
        self.vm_title = vm_title
        self.vm_manager = getattr(coordinator, "vm_manager", None)
        self._attr_name = f"{vm_title} {self.label}"
        self._attr_unique_id = f"{entry_id}_flynas_vm_{vm_name}_{self.action}"
        self._attr_extra_state_attributes = dashboard_metadata(
            "vm", self.role, self.order
        )
        self._attr_icon = self.icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vm_{vm_name}")},
            "name": vm_title,
            "via_device": (DOMAIN, DEVICE_ID_NAS),
        }

    async def async_press(self):
        if not self.vm_manager:
            _LOGGER.error("vm_manager不可用，无法%s虚拟机 %s", self.label, self.vm_name)
            return
        try:
            success = await self.vm_manager.control_vm(self.vm_name, self.action)
            if not success:
                return
            for vm in self.coordinator.data.get("vms", []):
                if vm["name"] == self.vm_name:
                    vm["state"] = self.resulting_state
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "%s虚拟机时出错: %s", self.label, str(err), exc_info=True
            )


class VMStartButton(_VMActionButton):
    action = "start"
    resulting_state = "running"
    role = "power_on"
    label = "开机"
    icon = "mdi:play"
    order = 20


class VMShutdownButton(_VMActionButton):
    action = "shutdown"
    resulting_state = "shut off"
    role = "power_off"
    label = "关机"
    icon = "mdi:power-off"
    order = 25


class VMRebootButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, vm_name, vm_title, entry_id):
        super().__init__(coordinator)
        self.vm_name = vm_name
        self.vm_title = vm_title
        self._attr_name = f"{vm_title} 重启"
        self._attr_unique_id = f"{entry_id}_flynas_vm_{vm_name}_reboot"
        self._attr_extra_state_attributes = dashboard_metadata("vm", "reboot", 30)
        self._attr_icon = "mdi:restart"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vm_{vm_name}")},
            "name": vm_title,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }

        self.vm_manager = coordinator.vm_manager if hasattr(coordinator, 'vm_manager') else None

    async def async_press(self):
        """重启虚拟机"""
        if not self.vm_manager:
            _LOGGER.error("vm_manager不可用，无法重启虚拟机 %s", self.vm_name)
            return
            
        try:
            success = await self.vm_manager.control_vm(self.vm_name, "reboot")
            if success:
                # 更新状态为"重启中"
                for vm in self.coordinator.data["vms"]:
                    if vm["name"] == self.vm_name:
                        vm["state"] = "rebooting"
                self.async_write_ha_state()
                
                # 在下次更新时恢复实际状态
                self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception as e:
            _LOGGER.error("重启虚拟机时出错: %s", str(e), exc_info=True)


class _DockerContainerActionButton(CoordinatorEntity, ButtonEntity):
    action = ""
    resulting_state = ""
    role = ""
    label = ""
    icon = "mdi:docker"
    order = 20

    def __init__(self, coordinator, container_name, safe_name, entry_id):
        super().__init__(coordinator)
        self.container_name = container_name
        self._attr_name = f"{container_name} {self.label}"
        self._attr_unique_id = f"{entry_id}_docker_{safe_name}_{self.action}"
        self._attr_extra_state_attributes = dashboard_metadata(
            "docker", self.role, self.order
        )
        self._attr_icon = self.icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"docker_{safe_name}")},
            "name": container_name,
            "via_device": (DOMAIN, DEVICE_ID_NAS),
        }

    async def async_press(self):
        manager = getattr(self.coordinator, "docker_manager", None)
        if manager is None:
            _LOGGER.error(
                "Docker管理功能未启用，无法%s容器 %s",
                self.label,
                self.container_name,
            )
            return
        try:
            success = await manager.control_container(
                self.container_name, self.action
            )
            if not success:
                return
            for container in self.coordinator.data.get("docker_containers", []):
                if container["name"] == self.container_name:
                    container["status"] = self.resulting_state
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "%sDocker容器 %s 时出错: %s",
                self.label,
                self.container_name,
                str(err),
                exc_info=True,
            )


class DockerContainerStartButton(_DockerContainerActionButton):
    action = "start"
    resulting_state = "running"
    role = "power_on"
    label = "启动"
    icon = "mdi:play"
    order = 20


class DockerContainerStopButton(_DockerContainerActionButton):
    action = "stop"
    resulting_state = "exited"
    role = "power_off"
    label = "停止"
    icon = "mdi:stop"
    order = 25


class DockerContainerRestartButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, container_name, safe_name, entry_id):
        super().__init__(coordinator)
        self.container_name = container_name
        self.safe_name = safe_name
        self._attr_name = f"{container_name} 重启"
        self._attr_unique_id = f"{entry_id}_docker_{safe_name}_restart"
        self._dashboard_attributes = dashboard_metadata("docker", "restart", 30)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"docker_{safe_name}")},
            "name": container_name,
            "via_device": (DOMAIN, DEVICE_ID_NAS)
        }
        self._attr_icon = "mdi:restart"

    async def async_press(self):
        """重启Docker容器"""
        # 检查是否启用了Docker功能
        if not hasattr(self.coordinator, 'docker_manager') or self.coordinator.docker_manager is None:
            _LOGGER.error("Docker管理功能未启用，无法重启容器 %s", self.container_name)
            return
            
        try:
            # 更新状态为"重启中"
            for container in self.coordinator.data.get("docker_containers", []):
                if container["name"] == self.container_name:
                    container["status"] = "restarting"
            self.async_write_ha_state()
            
            # 执行重启命令
            success = await self.coordinator.docker_manager.control_container(self.container_name, "restart")
            
            if success:
                _LOGGER.info("Docker容器 %s 重启命令已发送", self.container_name)
                
                # 强制刷新状态（因为容器重启可能需要时间）
                self.coordinator.async_request_refresh()
            else:
                _LOGGER.error("Docker容器 %s 重启失败", self.container_name)
                # 恢复原始状态
                for container in self.coordinator.data.get("docker_containers", []):
                    if container["name"] == self.container_name:
                        container["status"] = "running"  # 假设重启失败后状态不变
                self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error("重启Docker容器 %s 时出错: %s", self.container_name, str(e), exc_info=True)
            # 恢复原始状态
            for container in self.coordinator.data.get("docker_containers", []):
                if container["name"] == self.container_name:
                    container["status"] = "running"
            self.async_write_ha_state()
    
    @property
    def extra_state_attributes(self):
        return {
            **self._dashboard_attributes,
            "容器名称": self.container_name,
            "操作类型": "重启容器",
            "提示": "重启操作可能需要一些时间完成"
        }
