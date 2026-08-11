#!/usr/bin/env python3
# Hysteria2 manager with Alpine Linux / OpenRC support.
# Based on the workflow of seagullz4/hysteria2, reworked to avoid systemd-only assumptions.

import glob
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    print("缺少 requests。Alpine 请先执行: apk add --no-cache py3-requests")
    sys.exit(1)

CONFIG_DIR = Path("/etc/hysteria")
CONFIG_FILE = CONFIG_DIR / "config.yaml"
HY2_DIR = Path("/etc/hy2config")
URL_FILE = HY2_DIR / "hy2_url_scheme.txt"
BINARY = Path("/usr/local/bin/hysteria")
OPENRC_SERVICE = Path("/etc/init.d/hysteria-server")
OPENRC_IPTABLES_SERVICE = Path("/etc/init.d/hysteria-iptables")
SYSTEMD_SERVICE = "hysteria-server.service"
API_URL = "https://api.hy2.io/v1/update"
REPO_RELEASE = "https://github.com/apernet/hysteria/releases/download/app"
LATEST_DOWNLOAD = "https://github.com/apernet/hysteria/releases/latest/download"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def run(cmd, check=False, capture_output=False, text=True, **kwargs):
    return subprocess.run(cmd, check=check, capture_output=capture_output, text=text, **kwargs)


def is_root():
    return os.geteuid() == 0


def os_id():
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                return line.split("=", 1)[1].strip().strip('"').lower()
    except OSError:
        pass
    return "unknown"


def is_alpine():
    return os_id() == "alpine"


def service_backend():
    if is_alpine() and shutil.which("rc-service"):
        return "openrc"
    if shutil.which("systemctl"):
        return "systemd"
    if shutil.which("rc-service"):
        return "openrc"
    return "unknown"


def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    HY2_DIR.mkdir(parents=True, exist_ok=True)
    URL_FILE.touch(exist_ok=True)


def yaml_q(value):
    """JSON strings are valid YAML scalars and safely quote user input."""
    return json.dumps(str(value), ensure_ascii=False)


def detect_arch():
    machine = os.uname().machine.lower()
    mapping = {
        "i386": "386", "i686": "386",
        "amd64": "amd64", "x86_64": "amd64",
        "armv5tel": "arm", "armv6l": "arm", "armv7": "arm", "armv7l": "arm",
        "armv8": "arm64", "aarch64": "arm64",
        "mips": "mipsle", "mipsle": "mipsle", "mips64": "mipsle", "mips64le": "mipsle",
        "s390x": "s390x", "loongarch64": "loong64", "riscv64": "riscv64",
    }
    if machine not in mapping:
        raise RuntimeError(f"暂不支持 CPU 架构: {machine}")
    return mapping[machine]


def get_latest_version(arch):
    params = {
        "cver": "installscript",
        "plat": "linux",
        "arch": arch,
        "chan": "release",
        "side": "server",
    }
    r = requests.get(API_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    def find_lver(obj):
        if isinstance(obj, dict):
            value = obj.get("lver")
            if isinstance(value, str):
                return value
            for item in obj.values():
                found = find_lver(item)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = find_lver(item)
                if found:
                    return found
        return None

    version = find_lver(data)
    if not version or not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Hysteria API 返回了无法识别的版本: {version!r}")
    return version


def download_binary(version=None):
    arch = detect_arch()
    ensure_dirs()
    if version:
        version = version if version.startswith("v") else f"v{version}"
        if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
            raise ValueError("版本号格式无效，应类似 2.6.0 或 v2.6.0")
    else:
        try:
            version = get_latest_version(arch)
        except Exception as exc:
            print(f"{YELLOW}获取最新版本号失败，将尝试 latest 下载地址: {exc}{RESET}")
            version = None

    if version:
        url = f"{REPO_RELEASE}/{version}/hysteria-linux-{arch}"
        version_label = version
    else:
        url = f"{LATEST_DOWNLOAD}/hysteria-linux-{arch}"
        version_label = "latest"

    tmp = Path("/tmp/hysteria-download")
    print(f"正在下载 Hysteria2 ({version_label}, {arch}) ...")
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    tmp.chmod(0o755)

    # Basic sanity check before replacing a working binary.
    check = run([str(tmp), "version"], capture_output=True)
    if check.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("下载的 Hysteria 可执行文件校验失败")

    BINARY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp, BINARY)
    BINARY.chmod(0o755)
    tmp.unlink(missing_ok=True)
    print(f"{GREEN}Hysteria2 已安装到 {BINARY}{RESET}")
    print(check.stdout.strip())


def install_openrc_service():
    if not is_alpine():
        return
    if not shutil.which("rc-service"):
        raise RuntimeError("未找到 OpenRC，请先执行 apk add --no-cache openrc")

    content = r'''#!/sbin/openrc-run
name="hysteria-server"
description="Hysteria 2 server"
command="/usr/local/bin/hysteria"
command_args="server -c /etc/hysteria/config.yaml"
command_background="yes"
pidfile="/run/${RC_SVCNAME}.pid"
output_log="/var/log/hysteria-server.log"
error_log="/var/log/hysteria-server.err"
start_stop_daemon_args="--stdout ${output_log} --stderr ${error_log}"

depend() {
    need net
    after firewall
}

start_pre() {
    checkpath --file --mode 0644 "${output_log}"
    checkpath --file --mode 0644 "${error_log}"
    if [ ! -r /etc/hysteria/config.yaml ]; then
        eerror "Missing /etc/hysteria/config.yaml"
        return 1
    fi
}
'''
    OPENRC_SERVICE.write_text(content)
    OPENRC_SERVICE.chmod(0o755)
    run(["rc-update", "add", "hysteria-server", "default"], check=False)


def install_systemd_via_official(version=None):
    """Keep compatibility for non-Alpine systems using the official installer."""
    cmd = "bash <(curl -fsSL https://get.hy2.sh/)"
    if version:
        version = version if version.startswith("v") else f"v{version}"
        cmd += f" --version {shlex.quote(version)}"
    cp = run(cmd, shell=True, executable="/bin/bash")
    if cp.returncode != 0:
        raise RuntimeError("官方 Hysteria 安装脚本执行失败")


def install_hysteria(version=None):
    if is_alpine():
        download_binary(version)
        install_openrc_service()
    else:
        install_systemd_via_official(version)


def service_enable_start():
    backend = service_backend()
    if backend == "openrc":
        install_openrc_service()
        run(["rc-update", "add", "hysteria-server", "default"], check=False)
        return run(["rc-service", "hysteria-server", "start"]).returncode == 0
    if backend == "systemd":
        return run(["systemctl", "enable", "--now", SYSTEMD_SERVICE]).returncode == 0
    print(f"{RED}未检测到 systemd 或 OpenRC{RESET}")
    return False


def service_action(action):
    backend = service_backend()
    if backend == "openrc":
        if action == "enable-start":
            return service_enable_start()
        mapped = {"start": "start", "stop": "stop", "restart": "restart", "status": "status"}
        if action in mapped:
            return run(["rc-service", "hysteria-server", mapped[action]]).returncode == 0
    elif backend == "systemd":
        mapped = {"start": "start", "stop": "stop", "restart": "restart", "status": "status"}
        if action == "enable-start":
            return service_enable_start()
        if action in mapped:
            return run(["systemctl", mapped[action], SYSTEMD_SERVICE]).returncode == 0
    print(f"{RED}当前系统没有可用的服务管理器{RESET}")
    return False


def show_logs():
    backend = service_backend()
    if backend == "openrc":
        out = Path("/var/log/hysteria-server.log")
        err = Path("/var/log/hysteria-server.err")
        print("===== stdout =====")
        if out.exists():
            run(["tail", "-n", "100", str(out)])
        else:
            print("暂无日志")
        print("===== stderr =====")
        if err.exists():
            run(["tail", "-n", "100", str(err)])
        else:
            print("暂无错误日志")
    elif backend == "systemd":
        run(["journalctl", "--no-pager", "-e", "-u", SYSTEMD_SERVICE])


def install_shortcut():
    target_dir = Path("/usr/local/lib/hysteria2")
    target = target_dir / "hysteria2.py"
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        current = Path(__file__).resolve()
        if current != target:
            shutil.copy2(current, target)
            target.chmod(0o755)
        elif not target.exists():
            shutil.copy2(current, target)
    except Exception as exc:
        print(f"{YELLOW}保存 hy2 管理脚本失败: {exc}{RESET}")
        return

    wrapper = Path("/usr/local/bin/hy2")
    wrapper.write_text("#!/bin/sh\nexec python3 /usr/local/lib/hysteria2/hysteria2.py \"$@\"\n")
    wrapper.chmod(0o755)


def agree_treaty():
    ensure_dirs()
    agree_file = HY2_DIR / "agree.txt"
    if agree_file.exists():
        install_shortcut()
        return
    while True:
        print("我同意使用本程序时遵守部署服务器所在地、所在国家和用户所在国家的法律法规；本程序仅供学习交流使用。")
        choice = input("是否同意并继续安装 Hysteria2 [y/n]：").strip().lower()
        if choice == "y":
            agree_file.touch()
            install_shortcut()
            return
        if choice == "n":
            sys.exit(0)
        print(f"{RED}请输入 y 或 n{RESET}")


def create_iptables_persistence_service():
    restore_script = HY2_DIR / "restore-iptables.sh"
    restore_script.write_text(r'''#!/bin/sh
set -e
if [ -s /etc/hy2config/iptables-rules.v4 ]; then
    iptables-restore < /etc/hy2config/iptables-rules.v4
fi
if command -v ip6tables-restore >/dev/null 2>&1 && [ -s /etc/hy2config/iptables-rules.v6 ]; then
    ip6tables-restore < /etc/hy2config/iptables-rules.v6
fi
''')
    restore_script.chmod(0o755)

    if service_backend() == "openrc":
        content = r'''#!/sbin/openrc-run
description="Restore Hysteria2 iptables rules"

depend() {
    need localmount
    before hysteria-server
}

start() {
    ebegin "Restoring Hysteria2 iptables rules"
    /etc/hy2config/restore-iptables.sh
    eend $?
}
'''
        OPENRC_IPTABLES_SERVICE.write_text(content)
        OPENRC_IPTABLES_SERVICE.chmod(0o755)
        run(["rc-update", "add", "hysteria-iptables", "default"], check=False)
        print("已创建 OpenRC iptables 持久化服务")
    elif service_backend() == "systemd":
        service_path = Path("/etc/systemd/system/hysteria-iptables.service")
        service_path.write_text("""[Unit]\nDescription=Restore Hysteria2 iptables rules\nBefore=hysteria-server.service\nAfter=network.target\n\n[Service]\nType=oneshot\nExecStart=/etc/hy2config/restore-iptables.sh\nRemainAfterExit=true\n\n[Install]\nWantedBy=multi-user.target\n""")
        run(["systemctl", "daemon-reload"], check=False)
        run(["systemctl", "enable", "hysteria-iptables.service"], check=False)


def save_iptables_rules():
    ensure_dirs()
    try:
        with (HY2_DIR / "iptables-rules.v4").open("w") as f:
            run(["iptables-save"], check=True, stdout=f)
        if shutil.which("ip6tables-save"):
            try:
                with (HY2_DIR / "iptables-rules.v6").open("w") as f:
                    run(["ip6tables-save"], check=True, stdout=f)
            except Exception as exc:
                print(f"{YELLOW}IPv6 规则保存失败，继续保留 IPv4 持久化: {exc}{RESET}")
                (HY2_DIR / "iptables-rules.v6").unlink(missing_ok=True)
        create_iptables_persistence_service()
        return True
    except Exception as exc:
        print(f"{RED}保存 iptables 规则失败: {exc}{RESET}")
        return False


def cleanup_port_hopping():
    script = HY2_DIR / "jump_port_back.sh"
    if script.exists():
        run(["/bin/sh", str(script)], check=False, stderr=subprocess.DEVNULL)
        script.unlink(missing_ok=True)


def validate_port(prompt):
    while True:
        try:
            port = int(input(prompt))
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        print("端口号范围必须是 1~65535")


def configure_port_hopping(target_port):
    choice = input("是否开启端口跳跃 [y/n]：").strip().lower()
    if choice != "y":
        return ""

    result = run(["ip", "-o", "link", "show"], capture_output=True)
    if result.returncode == 0:
        print("可用网络接口：")
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 2:
                print(" -", parts[1].strip())
    iface = input("请输入 IPv4 网络接口名称（常见 eth0）：").strip()

    while True:
        first = validate_port("请输入起始端口：")
        last = validate_port("请输入结束端口：")
        if first <= last:
            break
        print("起始端口不能大于结束端口")

    cleanup_port_hopping()
    run(["iptables", "-t", "nat", "-A", "PREROUTING", "-i", iface, "-p", "udp",
         "--dport", f"{first}:{last}", "-j", "REDIRECT", "--to-ports", str(target_port)], check=True)

    cleanup_lines = [
        "#!/bin/sh",
        f"iptables -t nat -D PREROUTING -i {shlex.quote(iface)} -p udp --dport {first}:{last} -j REDIRECT --to-ports {target_port} 2>/dev/null || true",
    ]

    if input("是否开启 IPv6 端口跳跃 [y/n]：").strip().lower() == "y" and shutil.which("ip6tables"):
        iface6 = input("请输入 IPv6 网络接口名称：").strip()
        run(["ip6tables", "-t", "nat", "-A", "PREROUTING", "-i", iface6, "-p", "udp",
             "--dport", f"{first}:{last}", "-j", "REDIRECT", "--to-ports", str(target_port)], check=True)
        cleanup_lines.append(
            f"ip6tables -t nat -D PREROUTING -i {shlex.quote(iface6)} -p udp --dport {first}:{last} -j REDIRECT --to-ports {target_port} 2>/dev/null || true"
        )

    script = HY2_DIR / "jump_port_back.sh"
    script.write_text("\n".join(cleanup_lines) + "\n")
    script.chmod(0o755)
    save_iptables_rules()
    return f"&mport={first}-{last}"


def get_ipv4():
    try:
        r = requests.get("https://api.ipify.org", timeout=5)
        r.raise_for_status()
        ipaddress.IPv4Address(r.text.strip())
        return r.text.strip()
    except Exception:
        while True:
            ip = input("无法自动获取 IPv4，请手动输入：").strip()
            try:
                ipaddress.IPv4Address(ip)
                return ip
            except ipaddress.AddressValueError:
                print("IPv4 地址无效")


def get_ipv6():
    try:
        r = requests.get("https://api64.ipify.org", timeout=5)
        r.raise_for_status()
        ipaddress.IPv6Address(r.text.strip())
        return f"[{r.text.strip()}]"
    except Exception:
        while True:
            ip = input("无法自动获取 IPv6，请手动输入：").strip()
            try:
                ipaddress.IPv6Address(ip)
                return f"[{ip}]"
            except ipaddress.AddressValueError:
                print("IPv6 地址无效")


def generate_self_signed_cert():
    domain = input("请输入自签证书域名（默认 bing.com）：").strip() or "bing.com"
    if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        raise ValueError("域名格式无效")
    target = Path("/etc/ssl/private")
    target.mkdir(parents=True, exist_ok=True)
    key = target / f"{domain}.key"
    cert = target / f"{domain}.crt"
    cmd = [
        "openssl", "req", "-x509", "-nodes", "-newkey", "ec",
        "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-keyout", str(key), "-out", str(cert),
        "-subj", f"/CN={domain}", "-days", "36500",
    ]
    run(cmd, check=True)
    key.chmod(0o600)
    cert.chmod(0o644)
    return domain, str(cert), str(key)


def build_config(port, password, masquerade, brutal, obfs_block, sniff_block, cert_mode):
    lines = [f"listen: :{port}", ""]
    if cert_mode[0] == "acme":
        _, domain, email, acme_dns = cert_mode
        lines += ["acme:", "  domains:", f"    - {yaml_q(domain)}", f"  email: {yaml_q(email)}"]
        if acme_dns:
            lines += acme_dns.splitlines()
        lines.append("")
    else:
        _, cert, key = cert_mode
        lines += ["tls:", f"  cert: {yaml_q(cert)}", f"  key: {yaml_q(key)}", ""]

    lines += [
        "auth:", "  type: password", f"  password: {yaml_q(password)}", "",
        "masquerade:", "  type: proxy", "  proxy:", f"    url: {yaml_q(masquerade)}", "    rewriteHost: true", "",
        f"ignoreClientBandwidth: {'true' if brutal else 'false'}", "",
    ]
    if obfs_block:
        lines += obfs_block.splitlines() + [""]
    if sniff_block:
        lines += sniff_block.splitlines() + [""]
    CONFIG_FILE.write_text("\n".join(lines).rstrip() + "\n")


def choose_certificate():
    while True:
        print("1. 自动申请域名证书 (ACME)\n2. 使用自签证书\n3. 手动选择证书路径")
        choice = input("请输入选项：").strip()
        if choice == "1":
            domain = input("请输入域名：").strip()
            if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
                print("域名格式无效")
                continue
            email = input("请输入邮箱：").strip()
            acme_dns = ""
            if input("是否配置 ACME DNS [y/n]：").strip().lower() == "y":
                print("当前 Alpine 版保留 Hysteria 内置 ACME；DNS 高级配置请安装后手动编辑 config.yaml。")
            return ("acme", domain, email, acme_dns), domain, "&insecure=0"
        if choice == "2":
            domain, cert, key = generate_self_signed_cert()
            mode = input("1. IPv4 模式\n2. IPv6 模式\n请输入选项：").strip()
            host = get_ipv6() if mode == "2" else get_ipv4()
            return ("tls", cert, key), host, f"&sni={urllib.parse.quote(domain)}&insecure=1"
        if choice == "3":
            cert = input("请输入证书路径：").strip()
            key = input("请输入私钥路径：").strip()
            domain = input("请输入域名：").strip()
            return ("tls", cert, key), domain, f"&sni={urllib.parse.quote(domain)}&insecure=0"
        print("输入错误")


def configure_hysteria():
    ensure_dirs()
    port = validate_port("请输入端口号：")
    username = input("请输入节点名称：").strip()
    password = input("请输入强密码：").strip()
    masquerade = input("请输入伪装网址（例如 https://www.bing.com/）：").strip()

    brutal = input("是否开启 Brutal 模式 [y/n]：").strip().lower() == "y"

    obfs_block = ""
    obfs_query = ""
    if input("是否开启 Salamander 混淆 [y/n]：").strip().lower() == "y":
        obfs_password = input("请输入混淆密码：").strip()
        obfs_block = f"obfs:\n  type: salamander\n  salamander:\n    password: {yaml_q(obfs_password)}"
        obfs_query = "&obfs=salamander&obfs-password=" + urllib.parse.quote(obfs_password, safe="")

    sniff_block = ""
    if input("是否开启协议嗅探 Sniff [y/n]：").strip().lower() == "y":
        sniff_block = "sniff:\n  enable: true\n  timeout: 2s\n  rewriteDomain: false\n  tcpPorts: 80,443,8000-9000\n  udpPorts: all"

    jump_query = configure_port_hopping(port)
    cert_mode, host, tls_query = choose_certificate()

    # ACME URL still needs SNI.
    if cert_mode[0] == "acme":
        domain = cert_mode[1]
        tls_query = f"&sni={urllib.parse.quote(domain)}&insecure=0"

    build_config(port, password, masquerade, brutal, obfs_block, sniff_block, cert_mode)

    link = (
        f"hysteria2://{urllib.parse.quote(password, safe='')}@{host}:{port}?"
        f"{tls_query.lstrip('&')}{obfs_query}{jump_query}#{urllib.parse.quote(username, safe='')}"
    )
    URL_FILE.write_text(f"您的 Hysteria2 链接：{link}\n")
    print("\n您的 Hysteria2 链接：")
    print(link)

    if shutil.which("qrencode"):
        run(["qrencode", "-s", "1", "-m", "1", "-t", "ANSI256", "-o", "-", link])

    if input("是否下载 clash/sing-box/surge 转换模板 [y/n]：").strip().lower() == "y":
        encoded = urllib.parse.quote(link, safe="")
        rule = "&ua=&selectedRules=%5B%22Location%3ACN%22%2C%22Private%22%2C%22Non-China%22%2C%22Github%22%2C%22Google%22%2C%22Youtube%22%2C%22AI+Services%22%2C%22Telegram%22%2C%22Ad+Block%22%5D&customRules=%5B%5D&include_auto_select=false"
        for name, endpoint in [("clash.yaml", "clash"), ("sing-box.yaml", "singbox"), ("surge.yaml", "surge")]:
            try:
                r = requests.get(f"https://sub.baibaicat.site/{endpoint}?config={encoded}{rule}", timeout=20)
                r.raise_for_status()
                (HY2_DIR / name).write_bytes(r.content)
            except Exception as exc:
                print(f"{YELLOW}{name} 下载失败: {exc}{RESET}")

    if service_enable_start():
        service_action("restart")
        print(f"{GREEN}配置完成，Hysteria2 服务已启动{RESET}")
    else:
        print(f"{RED}配置已写入，但服务启动失败，请查看状态/日志{RESET}")


def view_config():
    if not CONFIG_FILE.exists():
        print("未找到 /etc/hysteria/config.yaml")
        return
    print("===== /etc/hysteria/config.yaml =====")
    print(CONFIG_FILE.read_text())
    if URL_FILE.exists():
        print("===== 节点链接 =====")
        print(URL_FILE.read_text())


def manual_edit():
    editor = shutil.which("nano") or shutil.which("vi")
    if not editor:
        print("未找到 nano/vi。Alpine 可执行 apk add nano")
        return
    run([editor, str(CONFIG_FILE)])
    service_action("restart")


def enable_bbr():
    print("Alpine 不安装 XanMod；这里改为尝试启用当前内核自带的 BBR。")
    if shutil.which("modprobe"):
        run(["modprobe", "tcp_bbr"], check=False)
    available = Path("/proc/sys/net/ipv4/tcp_available_congestion_control")
    if available.exists() and "bbr" not in available.read_text().split():
        print(f"{YELLOW}当前内核未提供 BBR，请更换/升级 Alpine 内核后再试。{RESET}")
        return
    conf = Path("/etc/sysctl.d/99-hysteria2.conf")
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n")
    run(["sysctl", "-p", str(conf)], check=False)
    print(f"{GREEN}已写入 BBR sysctl 配置{RESET}")


def uninstall_hysteria():
    if input("是否卸载 Hysteria2 [y/n]：").strip().lower() != "y":
        return
    backend = service_backend()
    cleanup_port_hopping()
    if backend == "openrc":
        run(["rc-service", "hysteria-server", "stop"], check=False, stderr=subprocess.DEVNULL)
        run(["rc-update", "del", "hysteria-server", "default"], check=False, stderr=subprocess.DEVNULL)
        run(["rc-update", "del", "hysteria-iptables", "default"], check=False, stderr=subprocess.DEVNULL)
        OPENRC_SERVICE.unlink(missing_ok=True)
        OPENRC_IPTABLES_SERVICE.unlink(missing_ok=True)
    elif backend == "systemd":
        # On non-Alpine, delegate binary/service removal to official installer.
        run("bash <(curl -fsSL https://get.hy2.sh/) --remove", shell=True, executable="/bin/bash")
        run(["systemctl", "disable", "--now", "hysteria-iptables.service"], check=False, stderr=subprocess.DEVNULL)
        Path("/etc/systemd/system/hysteria-iptables.service").unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"], check=False)

    BINARY.unlink(missing_ok=True)
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)
    shutil.rmtree(HY2_DIR, ignore_errors=True)
    Path("/usr/local/bin/hy2").unlink(missing_ok=True)
    shutil.rmtree(Path("/usr/local/lib/hysteria2"), ignore_errors=True)
    print("Hysteria2 已卸载")
    sys.exit(0)


def check_version():
    if not BINARY.exists():
        print("Hysteria2 尚未安装")
        return
    run([str(BINARY), "version"], check=False)


def install_menu():
    while True:
        print("1. 安装/更新最新版本\n2. 安装指定版本\n0. 返回")
        choice = input("请输入选项：").strip()
        try:
            if choice == "1":
                install_hysteria()
                if is_alpine():
                    print("Alpine/OpenRC 服务文件已创建；请继续配置。")
                return
            if choice == "2":
                version = input("版本号（例如 2.6.0）：").strip()
                install_hysteria(version)
                return
            if choice == "0":
                return
        except Exception as exc:
            print(f"{RED}安装失败: {exc}{RESET}")


def config_menu():
    while True:
        print("1. 查看配置\n2. 一键配置\n3. 手动编辑配置\n4. 性能优化/BBR\n0. 返回")
        choice = input("请输入选项：").strip()
        try:
            if choice == "1":
                view_config()
            elif choice == "2":
                configure_hysteria()
            elif choice == "3":
                manual_edit()
            elif choice == "4":
                enable_bbr()
            elif choice == "0":
                return
            else:
                print("输入错误")
        except Exception as exc:
            print(f"{RED}操作失败: {exc}{RESET}")
        input("按回车继续...")


def server_manage():
    while True:
        print("1. 启动并设为开机自启\n2. 停止\n3. 重启\n4. 查看状态\n5. 查看日志\n6. 查看版本\n0. 返回")
        choice = input("请输入选项：").strip()
        if choice == "1":
            service_action("enable-start")
        elif choice == "2":
            service_action("stop")
        elif choice == "3":
            service_action("restart")
        elif choice == "4":
            service_action("status")
        elif choice == "5":
            show_logs()
        elif choice == "6":
            check_version()
        elif choice == "0":
            return
        else:
            print("输入错误")
        input("按回车继续...")


def main():
    if not is_root():
        print(f"{RED}请使用 root 运行此脚本{RESET}")
        sys.exit(1)

    if is_alpine():
        required = ["rc-service", "rc-update", "ip", "iptables", "openssl"]
        missing = [cmd for cmd in required if not shutil.which(cmd)]
        if missing:
            print(f"{RED}缺少 Alpine 依赖: {', '.join(missing)}{RESET}")
            print("请先执行同目录下: sh phy2.sh")
            sys.exit(1)

    ensure_dirs()
    agree_treaty()

    while True:
        os.system("clear")
        backend = service_backend()
        print(f"{RED}HELLO HYSTERIA2 !{RESET}  系统: {os_id()}  服务管理: {backend}")
        print("1. 安装/更新 Hysteria2\n2. 卸载 Hysteria2\n3. Hysteria2 配置\n4. Hysteria2 服务管理\n0. 退出")
        choice = input("请输入选项：").strip()
        if choice == "1":
            install_menu()
        elif choice == "2":
            uninstall_hysteria()
        elif choice == "3":
            config_menu()
        elif choice == "4":
            check_version()
            server_manage()
        elif choice == "0":
            print("已退出")
            return
        else:
            print("输入错误")
            time.sleep(1)


if __name__ == "__main__":
    main()
