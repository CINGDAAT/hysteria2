#!/bin/sh
set -eu

RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
CYAN='\033[36m'
RESET='\033[0m'

if [ "$(id -u)" -ne 0 ]; then
    printf "%b请使用 root 运行此脚本%b\n" "$RED" "$RESET"
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    printf "%b无法识别系统%b\n" "$RED" "$RESET"
    exit 1
fi

. /etc/os-release
OS_ID=${ID:-unknown}

apk_pkg_exists() {
    # 只查询当前机器 /etc/apk/repositories 中实际可用的包。
    apk search -x "$1" 2>/dev/null | grep -q .
}

apk_pkg_installed() {
    apk info -e "$1" >/dev/null 2>&1
}

apk_install_required() {
    label=$1
    shift

    for pkg in "$@"; do
        if apk_pkg_installed "$pkg"; then
            printf "%b[已安装]%b %s -> %s\n" "$GREEN" "$RESET" "$label" "$pkg"
            return 0
        fi
        if apk_pkg_exists "$pkg"; then
            printf "%b[安装]%b %s -> %s\n" "$CYAN" "$RESET" "$label" "$pkg"
            apk add --no-cache "$pkg"
            return 0
        fi
    done

    printf "%b错误：当前 Alpine 仓库找不到 %s（候选: %s）%b\n" "$RED" "$label" "$*" "$RESET"
    return 1
}

apk_install_optional() {
    label=$1
    shift

    for pkg in "$@"; do
        if apk_pkg_installed "$pkg"; then
            printf "%b[已安装]%b %s -> %s\n" "$GREEN" "$RESET" "$label" "$pkg"
            return 0
        fi
        if apk_pkg_exists "$pkg"; then
            printf "%b[可选]%b %s -> %s\n" "$CYAN" "$RESET" "$label" "$pkg"
            if apk add --no-cache "$pkg"; then
                return 0
            fi
        fi
    done

    printf "%b[跳过]%b 当前仓库没有可用的 %s，不影响 Hysteria2 核心功能\n" "$YELLOW" "$RESET" "$label"
    return 0
}

show_alpine_version() {
    ALPINE_VERSION=${VERSION_ID:-}
    if [ -r /etc/alpine-release ]; then
        ALPINE_VERSION=$(cat /etc/alpine-release 2>/dev/null || true)
    fi
    [ -n "$ALPINE_VERSION" ] || ALPINE_VERSION=unknown

    printf "检测到 Alpine Linux %s\n" "$ALPINE_VERSION"

    # 3.18+ 是本项目的目标支持范围；不设置最高版本限制。
    # 对更老版本也不强制退出，仍按本机仓库做 best-effort 依赖解析。
    major=${ALPINE_VERSION%%.*}
    rest=${ALPINE_VERSION#*.}
    minor=${rest%%.*}
    case "$major:$minor" in
        *[!0-9:]*|:*)
            printf "%b无法解析版本号，将直接按当前仓库可用包自动选择依赖。%b\n" "$YELLOW" "$RESET"
            ;;
        *)
            if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 18 ]; }; then
                printf "%b提示：目标支持范围为 Alpine 3.18+；当前版本较旧，将继续尝试兼容安装。%b\n" "$YELLOW" "$RESET"
            fi
            ;;
    esac
}

install_alpine() {
    show_alpine_version
    echo "正在刷新 APK 索引并按当前机器仓库选择依赖..."
    apk update

    # Hysteria2 Alpine/OpenRC 核心运行依赖。
    # 候选包按优先级排列；实际选择由当前机器仓库决定，而不是写死发行版小版本。
    apk_install_required "CA 证书" ca-certificates
    apk_install_required "OpenSSL" openssl
    apk_install_required "Python 3" python3
    apk_install_required "Python Requests" py3-requests
    apk_install_required "OpenRC" openrc
    apk_install_required "ip 命令" iproute2
    apk_install_required "iptables 命令" iptables iptables-nft

    # 管理/体验相关依赖。缺失时不阻断主程序安装。
    apk_install_optional "Bash" bash
    apk_install_optional "curl" curl
    apk_install_optional "wget" wget
    apk_install_optional "nano 编辑器" nano
    apk_install_optional "coreutils" coreutils
    apk_install_optional "grep" grep
    apk_install_optional "procps/sysctl" procps procps-ng
    apk_install_optional "util-linux" util-linux

    # Alpine 不同分支可能对 qrencode 工具采用不同拆包方式。
    # 依次探测当前仓库，避免对 3.18、3.24 或未来版本写死包名。
    apk_install_optional "终端二维码工具" libqrencode-tools qrencode libqrencode

    update-ca-certificates >/dev/null 2>&1 || true
    printf "%bAlpine 依赖安装完成（按当前仓库自动解析）%b\n" "$GREEN" "$RESET"
}

install_debian() {
    echo "检测到 Debian/Ubuntu，正在安装依赖..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        bash curl wget sudo ca-certificates openssl qrencode nano \
        python3 python3-requests iproute2 iptables procps
    printf "%b依赖安装完成%b\n" "$GREEN" "$RESET"
}

install_redhat() {
    echo "检测到 RHEL 系发行版，正在安装依赖..."
    if command -v dnf >/dev/null 2>&1; then
        PM=dnf
    else
        PM=yum
    fi
    "$PM" install -y \
        bash curl wget sudo ca-certificates openssl qrencode nano \
        python3 python3-requests iproute iptables procps-ng
    printf "%b依赖安装完成%b\n" "$GREEN" "$RESET"
}

case "$OS_ID" in
    alpine)
        install_alpine
        ;;
    debian|ubuntu)
        install_debian
        ;;
    rocky|centos|fedora|rhel|almalinux)
        install_redhat
        ;;
    *)
        printf "%b暂不支持发行版: %s%b\n" "$RED" "$OS_ID" "$RESET"
        exit 1
        ;;
esac
