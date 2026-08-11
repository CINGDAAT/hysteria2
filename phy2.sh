#!/bin/sh
set -eu

RED='\033[31m'
GREEN='\033[32m'
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

install_alpine() {
    echo "检测到 Alpine Linux，正在安装依赖..."
    apk update
    apk add --no-cache \
        bash curl wget ca-certificates openssl qrencode nano \
        python3 py3-requests openrc iproute2 iptables \
        coreutils grep procps util-linux
    update-ca-certificates >/dev/null 2>&1 || true
    printf "%bAlpine 依赖安装完成%b\n" "$GREEN" "$RESET"
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
