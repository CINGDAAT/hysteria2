# Hysteria2 Alpine / OpenRC 兼容版

这是基于 `seagullz4/hysteria2` 使用流程整理的 Alpine Linux 兼容版本，重点解决原脚本对 `apt/dnf + systemd/systemctl/journalctl` 的依赖。

## Alpine 上的主要改动

- `phy2.sh` 增加 Alpine 检测，并使用 `apk add --no-cache` 安装依赖。
- 不再在 Alpine 上调用官方 systemd 安装流程，而是直接下载 Hysteria 官方 Linux 二进制。
- 新增 OpenRC 服务 `/etc/init.d/hysteria-server`。
- 服务管理改用 `rc-service` / `rc-update`。
- 日志改为 `/var/log/hysteria-server.log` 和 `/var/log/hysteria-server.err`。
- 端口跳跃的 iptables 规则持久化改为 OpenRC 服务 `/etc/init.d/hysteria-iptables`。
- Alpine 下不再调用 XanMod 安装脚本，性能优化菜单改为尝试启用当前内核提供的 BBR。
- `hy2` 快捷命令使用本地安装的管理脚本，避免再次从上游下载后覆盖 Alpine 修改。
- 保留原脚本的主要使用方式：安装/更新、卸载、一键配置、自签证书、ACME、端口跳跃、二维码、订阅模板、服务管理。

## 安装

将本目录上传到 Alpine VPS，然后以 root 执行：

```sh
chmod +x install.sh phy2.sh hysteria2.py
sh install.sh
```

安装完成后以后直接输入：

```sh
hy2
```

也可以分两步执行：

```sh
sh phy2.sh
python3 hysteria2.py
```

## OpenRC 常用命令

```sh
rc-service hysteria-server status
rc-service hysteria-server restart
rc-service hysteria-server stop
rc-service hysteria-server start
rc-update add hysteria-server default
```

查看日志：

```sh
tail -n 100 /var/log/hysteria-server.log
tail -n 100 /var/log/hysteria-server.err
```

## 说明

Hysteria 本身可以使用官方 Linux 可执行文件运行在 Alpine 上；官方 `get.hy2.sh` 的限制主要来自它按 systemd 环境设计。因此本版本在 Alpine 上直接安装官方二进制，再由 OpenRC 接管服务。

建议在全新的 Alpine 3.x VPS 上使用 root 运行。若 VPS 的内核没有 BBR，本脚本不会强行更换内核，只会提示当前内核不支持。

## 上游

- 原一键脚本：`seagullz4/hysteria2`
- Hysteria 官方项目：`apernet/hysteria`

本修改版应继续遵守上游项目对应的开源许可证。仓库原项目标注为 GPL-2.0。
