#!/usr/bin/env bash
# ============================================================================
#  芮拓MC面板 · Linux 一键安装脚本
#
#  与同仓 RT面板（rt-panel）**完全独立、零耦合**：
#    · 不读写它的任何目录/文件/服务/数据库；不检测它是否存在；不向它注册任何东西
#    · 服务名 mc-panel（与 rt-panel 互不干扰）、目录 /opt/mc-panel、数据目录自带
#    · 卸载本面板不会碰到 rt-panel；卸载 rt-panel 也不会影响本面板
#
#  用法：
#    sudo bash install.sh                     # 安装（默认端口 8100）
#    sudo bash install.sh --port 8200         # 指定端口
#    sudo bash install.sh --dir /opt/mc-panel # 指定安装目录
#    sudo bash install.sh --no-systemd        # 只装程序、不注册 systemd 服务
#    sudo bash install.sh --uninstall         # 卸载（会二次确认，默认保留数据）
#
#  ⚠️ 什么时候该用 --no-systemd：
#    有些加固过的服务器把 /etc/systemd/system **整个目录**加了不可变属性
#    （`chattr +i`，`lsattr` 显示 ----i---------），此时**连 root 都写不进去**，
#    注册服务会报 `Operation not permitted`。宝塔的「系统加固」就会这么干。
#    本脚本**不会**自动替你解除那个锁 —— 那会连带解开同机上其它服务的保护。
#    → 要么加 --no-systemd 跳过服务注册（脚本会打印前台启动命令），
#      要么你自己确认后手动：chattr -i /etc/systemd/system
#
#  脚本做的事：
#    1. 安装系统依赖（python3 / python3-venv / curl / tar）
#    2. 复制程序到 $INSTALL_DIR
#    3. 建虚拟环境并安装 requirements.txt
#    4. 写 systemd 服务 mc-panel 并开机自启（--no-systemd 可跳过）
#    5. 初始化数据库；**超级管理员初始口令只在本地终端打印一次**
# ============================================================================
set -euo pipefail

SERVICE_NAME="mc-panel"
INSTALL_DIR="/opt/mc-panel"
DATA_DIR=""
PORT="8100"
BIND_HOST="0.0.0.0"
RUN_USER="mcpanel"
WITH_SYSTEMD=1          # --no-systemd 置 0：只装程序，不注册服务
UNINSTALL=0

GOLD=$'\033[38;2;212;175;55m'
GOLDL=$'\033[38;2;245;208;97m'
GREY=$'\033[38;2;150;150;150m'
GREEN=$'\033[38;2;103;194;58m'
RED=$'\033[38;2;245;108;108m'
# ⚠️ BOLD 必须在这里定义：脚本是 `set -u`，用了没定义的变量会直接让脚本中止
#    （实测踩过：完成横幅里用了 $BOLD，结果前边全装成功、却在最后一行崩掉）
BOLD=$'\033[1m'
RST=$'\033[0m'

step() { printf "\n${GOLDL}==> %s${RST}\n" "$1"; }
ok()   { printf "  ${GREEN}OK${RST}  %s\n" "$1"; }
info() { printf "      ${GREY}%s${RST}\n" "$1"; }
warn() { printf "  ${RED}!!${RST}  %s\n" "$1"; }
die()  { printf "  ${RED}XX${RST}  %s\n" "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 参数
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --bind) BIND_HOST="${2:-}"; shift 2 ;;
    --user) RUN_USER="${2:-}"; shift 2 ;;
    --no-systemd) WITH_SYSTEMD=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    "")
      # 空参数几乎总是**上游传参写法错**造成的（典型：
      # 引导脚本用 `"${PASS_ARGS[@]:-}"`，空数组会退化成一个空串参数）。
      # 这里明确点出来，而不是含糊地报「未知参数：」。
      die "收到了一个空参数 —— 通常是调用方把空数组写成了 \"\${ARGS[@]:-}\"；正确写法是 \"\${ARGS[@]}\"（空数组天然展开为零个参数）" ;;
    *) die "未知参数：$1（用 --help 看用法）" ;;
  esac
done

# --- 参数校验（原来完全不校验：--port abc / --dir 空值 / 尾斜杠都会一路带到底）
case "$PORT" in ''|*[!0-9]*) die "--port 必须是纯数字，收到：'$PORT'" ;; esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  die "--port 要在 1-65535 之间，收到：$PORT"
fi
[ -n "$INSTALL_DIR" ] || die "--dir 不能为空"
case "$INSTALL_DIR" in
  /*) : ;;                                  # 要求绝对路径，否则后面 unit 里的路径不可靠
  *) die "--dir 必须是绝对路径，收到：$INSTALL_DIR" ;;
esac
[ -n "$SERVICE_NAME" ] || die "服务名不能为空"
[ -n "$RUN_USER" ] || die "--user 不能为空"
# 去掉尾斜杠：否则会拼出 /opt/mc-panel//backend 这种路径
INSTALL_DIR="${INSTALL_DIR%/}"
[ "$INSTALL_DIR" = "" ] && die "--dir 不能是 /"
# 端口不能已被占用（原来不查，装完才发现起不来）
if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}\$"; then
  die "端口 $PORT 已被占用（ss -lntp 可看是谁占用）；换一个：--port 8200"
fi

DATA_DIR="$INSTALL_DIR/backend/data"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 校验源码目录确实是"一个面板源码包"，而不是随便哪个目录。
# ⚠️ 实测踩过：如果把 install.sh 放到 /tmp 单独执行，SRC_DIR 会变成 /tmp，
#    于是下面 `tar -C $SRC_DIR` 会把整个 /tmp 打进安装目录（还会打到
#    site_total.sock / mysql.sock 这些套接字），而且**漏掉 backend/**，
#    最后卡在一句莫名其妙的 `cd: /opt/mc-panel/backend: No such file or directory`。
if [ "$SRC_DIR" != "$INSTALL_DIR" ] && [ "$UNINSTALL" != "1" ]; then
  MISSING=""
  [ -f "$SRC_DIR/backend/run.py" ] || MISSING="backend/run.py"
  [ -f "$SRC_DIR/backend/requirements.txt" ] || MISSING="${MISSING:-}${MISSING:+, }backend/requirements.txt"
  [ -d "$SRC_DIR/frontend/dist" ] || MISSING="${MISSING:+$MISSING, }frontend/dist"
  if [ -n "$MISSING" ]; then
    printf "\n${RED}XX${RST}  %s\n" "源码目录不像一个完整的安装包：$SRC_DIR"
    printf "      ${GREY}%s${RST}\n" "缺少：$MISSING"
    printf "      ${GREY}%s${RST}\n" "请用官方一键安装（会先解包再执行包内的 install.sh）："
    printf "      ${GREY}%s${RST}\n" "  curl -sSO https://www.rt888.icu/install_mc.sh && bash install_mc.sh"
    printf "      ${GREY}%s${RST}\n" "或先解压源码包，再从包目录内执行："
    printf "      ${GREY}%s${RST}\n" "  tar -xzf mc-panel-*.tar.gz && cd mc-panel-* && bash install.sh"
    die "已中止，未做任何改动"
  fi
fi

if [ "$(id -u)" != "0" ]; then die "请用 root 运行：sudo bash install.sh"; fi

# --- systemd 可用性探测（原来只查 `command -v systemctl` —— "有命令"不等于"能用"）
#     典型反例：加固过的机器把 /etc/systemd/system 加了 chattr +i，连 root 都写不进；
#     容器里 systemctl 存在但 systemd 并不是 PID 1。这两种情况都要在**动手之前**拦住，
#     否则会在第 187 行甩出一句没头没脑的 "Operation not permitted"。
SYSTEMD_PROBLEM=""
if [ "$WITH_SYSTEMD" = "1" ]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    SYSTEMD_PROBLEM="没有 systemctl 命令（本机不是 systemd 系统）"
  elif [ ! -d /run/systemd/system ]; then
    SYSTEMD_PROBLEM="systemd 没有在运行（/run/systemd/system 不存在）；容器里常见"
  elif ! touch "/etc/systemd/system/.mc-panel-wtest.$$" 2>/dev/null; then
    if command -v lsattr >/dev/null 2>&1 && lsattr -d /etc/systemd/system 2>/dev/null | grep -q -- '----i'; then
      SYSTEMD_PROBLEM="/etc/systemd/system 被加了**不可变属性**（chattr +i，宝塔「系统加固」会这么做）—— 连 root 都写不进去"
    else
      SYSTEMD_PROBLEM="/etc/systemd/system 不可写（可能是只读挂载 / LSM 拦截）"
    fi
  else
    rm -f "/etc/systemd/system/.mc-panel-wtest.$$"
  fi
fi

if [ -n "$SYSTEMD_PROBLEM" ]; then
  printf "\n${RED}XX${RST}  %s\n" "无法注册 systemd 服务：$SYSTEMD_PROBLEM"
  printf "      ${GREY}%s${RST}\n" "这不会影响程序文件与数据，本脚本不会自动替你解除该限制"
  printf "      ${GREY}%s${RST}\n" "（自动解除会连带解开同机其它服务的保护）。两条正路："
  printf "      ${GREY}%s${RST}\n" "  1) 跳过服务注册，只装程序：  bash install.sh --no-systemd"
  printf "      ${GREY}%s${RST}\n" "  2) 你确认后手动解锁再重跑：  chattr -i /etc/systemd/system && bash install.sh"
  if command -v lsattr >/dev/null 2>&1; then
    printf "      ${GREY}%s${RST}\n" "查看当前锁： lsattr -d /etc/systemd/system"
  fi
  die "已中止，未做任何改动"
fi

# ---------------------------------------------------------------- 卸载
if [ "$UNINSTALL" = "1" ]; then
  step "卸载 $SERVICE_NAME"
  info "本操作只影响本面板，不会触碰 rt-panel 或任何其它服务。"
  printf "  确认卸载？数据目录 %s 将保留（如需彻底删除请手动 rm -rf）。[y/N] " "$DATA_DIR"
  read -r ans
  case "$ans" in
    y|Y) ;;
    *) die "已取消" ;;
  esac
  if [ "$WITH_SYSTEMD" = "1" ]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
  else
    # --no-systemd 表示本机注册不了服务；那些 systemctl 调用没有意义，
    # 而且服务不存在时它们会失败（原来无守卫，只是靠 `|| true` 掩盖）
    info "以 --no-systemd 卸载：跳过 systemctl 调用"
  fi
  # ⚠️ 加固过的机器上 unit 可能带不可变属性（chattr +i），此时 rm 会失败。
  #    这里如实报告并给出解法，而不是 `|| true` 吞掉后照样说"已移除"。
  if rm -f "/etc/systemd/system/$SERVICE_NAME.service" 2>/dev/null; then
    ok "服务已停止并移除"
  elif [ -e "/etc/systemd/system/$SERVICE_NAME.service" ]; then
    warn "删除 /etc/systemd/system/$SERVICE_NAME.service 失败（文件可能带不可变属性）"
    if command -v lsattr >/dev/null 2>&1; then
      info "当前属性：$(lsattr /etc/systemd/system/$SERVICE_NAME.service 2>/dev/null | head -n1)"
      info "手动解除后删除： chattr -i /etc/systemd/system/$SERVICE_NAME.service && rm -f /etc/systemd/system/$SERVICE_NAME.service"
    fi
    info "服务已停掉，但 unit 文件还在 —— 开机可能仍会尝试拉起"
    systemctl daemon-reload 2>/dev/null || true
    info "程序目录：$INSTALL_DIR（未删除，如需删除：rm -rf $INSTALL_DIR）"
    info "数据目录：$DATA_DIR（未删除）"
    exit 1
  else
    ok "服务已停止（unit 文件本就不存在）"
  fi
  systemctl daemon-reload 2>/dev/null || true
  info "程序目录：$INSTALL_DIR（未删除，如需删除：rm -rf $INSTALL_DIR）"
  info "数据目录：$DATA_DIR（未删除）"
  exit 0
fi

printf "\n${GOLD}  芮拓MC面板 · Linux 安装${RST}\n"
printf "  ${GREY}安装目录 %s · 端口 %s · 服务名 %s${RST}\n" "$INSTALL_DIR" "$PORT" "$SERVICE_NAME"
printf "  ${GREY}独立部署：不依赖、不检测、不注册到同机其它面板${RST}\n"

# ---------------------------------------------------------------- 依赖
step "检查系统与依赖"
# 只有需要注册服务时才强制 systemd；--no-systemd 时不该拿它拦人
if [ "$WITH_SYSTEMD" = "1" ]; then
  command -v systemctl >/dev/null 2>&1 || die "没有 systemctl（要么用 systemd 发行版，要么加 --no-systemd）"
fi
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || warn "apt-get update 失败，继续尝试安装"
  apt-get install -y -qq python3 python3-venv python3-pip curl tar ca-certificates >/dev/null 2>&1 \
    || warn "部分依赖安装失败，请自行确认 python3 / python3-venv 存在"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 python3-pip curl tar ca-certificates >/dev/null 2>&1 || warn "dnf 安装依赖失败"
elif command -v yum >/dev/null 2>&1; then
  yum install -y -q python3 python3-pip curl tar ca-certificates >/dev/null 2>&1 || warn "yum 安装依赖失败"
else
  warn "未识别的包管理器，请自行确保 python3 / python3-venv / curl / tar 可用"
fi
command -v python3 >/dev/null 2>&1 || die "找不到 python3"
ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"

# Java 不强制安装：面板能检测并用实例自带/系统 Java，也可在界面里一键下载 Adoptium
if command -v java >/dev/null 2>&1; then
  ok "检测到 Java：$(java -version 2>&1 | head -n1)"
else
  warn "未检测到 java —— 面板可以运行，但启动 MC 实例前需要装 Java，或在面板「设置 → Java 运行时」里下载"
fi

# ---------------------------------------------------------------- 用户
step "准备运行身份"
if [ "$RUN_USER" = "root" ]; then
  # 显式选 root 是可以的（容器里常这样），但要说清楚，不能悄悄降级
  warn "按你的指定，面板将以 root 身份运行（容器或特殊环境才建议这么做）"
elif id "$RUN_USER" >/dev/null 2>&1; then
  ok "复用已存在的用户 $RUN_USER"
else
  # ⚠️ 先查 useradd **能不能执行**，而不是等到它失败再猜原因。
  #    有些加固环境（宝塔的 tamper_core / 防篡改内核保护）会把
  #    /usr/sbin/useradd 的**可执行位去掉**（改成 644），此时它报的是
  #    "Permission denied"（退出码 126）—— 光看返回码根本猜不到是这个原因。
  if ! command -v useradd >/dev/null 2>&1; then
    printf "\n${RED}XX${RST}  %s\n" "本机没有 useradd，无法创建专用运行用户 $RUN_USER"
    printf "      ${GREY}%s${RST}\n" "  1) 复用已有低权用户：bash install.sh --user www"
    printf "      ${GREY}%s${RST}\n" "  2) 或明确接受用 root 运行：bash install.sh --user root"
    die "已中止，未做任何改动"
  fi
  if [ ! -x "$(command -v useradd)" ]; then
    printf "\n${RED}XX${RST}  %s\n" "$(command -v useradd) 没有可执行权限（$(stat -c '%a' "$(command -v useradd)" 2>/dev/null)），无法创建用户"
    printf "      ${GREY}%s${RST}\n" "这是**加固插件**改的（宝塔 tamper_core / 防篡改内核保护会这么做，"
    printf "      ${GREY}%s${RST}\n" "本意是防木马建后门用户，副作用是合法管理员也用不了）。"
    printf "      ${GREY}%s${RST}\n" "  1) 复用已有低权用户（推荐，零改动）：bash install.sh --user www"
    printf "      ${GREY}%s${RST}\n" "  2) 恢复可执行位后重跑：chmod +x $(command -v useradd)"
    printf "      ${GREY}%s${RST}\n" "  3) 或明确接受用 root 运行：bash install.sh --user root"
    die "已中止，未做任何改动"
  fi
  if useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$RUN_USER" 2>/dev/null \
     || useradd --system --home-dir "$INSTALL_DIR" --shell /sbin/nologin "$RUN_USER" 2>/dev/null; then
    ok "已创建运行用户 $RUN_USER"
  else
    # ⚠️ 原来这里只 warn 就沿用 root —— 那是个**静默的提权**：用户以为面板跑在
    # 专用低权用户下，实际跑在 root。这里改成必须显式确认，不默认降级。
    printf "\n${RED}XX${RST}  %s\n" "创建运行用户 $RUN_USER 失败"
    printf "      ${GREY}%s${RST}\n" "本脚本不会在未告知的情况下改用 root 运行面板。"
    printf "      ${GREY}%s${RST}\n" "  1) 手动建用户后重跑：useradd --system --shell /sbin/nologin $RUN_USER"
    printf "      ${GREY}%s${RST}\n" "  2) 复用已有低权用户：bash install.sh --user www"
    printf "      ${GREY}%s${RST}\n" "  3) 或明确接受用 root 运行：bash install.sh --user root"
    die "已中止，未做任何改动"
  fi
fi
ok "运行用户：$(id -un "$RUN_USER" 2>/dev/null || echo "$RUN_USER")"

# ⚠️ 加固机器上**不要用 www 这类被安全模块盯上的用户**：
#    实测（宝塔 tamper_core / BT security）`www` 执行外部程序会被**间歇性拦截**，
#    表现极隐蔽 —— 子进程创建抛畸形异常 `SubprocessError('')`（消息与 errno 全空），
#    连 `www` 跑 /usr/bin/timeout 都报假的 "Success"。后果是**面板能起、服开不了**：
#      以 www 跑面板 → 启动实例必失败；换成 root 启动同一个面板 → 实例立刻起来。
#    这类机器上请显式 `--user root`（面板要管理 MC 进程与端口，本就需要较高权限）。
if [ "$RUN_USER" != "root" ]; then
  warn "若本机有宝塔安全模块（或类似 execve 拦截），以 $RUN_USER 运行会**开不了服**"
  warn "  ↳ 症状：面板正常，启动实例却报「启动失败：SubprocessError: 」（错误信息是空的）"
  warn "  ↳ 处理：改用 --user root 重装，或把已装好的服务改成以 root 启动"
fi

# ---------------------------------------------------------------- 复制程序
step "复制程序到 $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
  # 只复制面板自己的东西；显式排除本机开发产物，避免把 .deps / 测试数据带上线
  tar -C "$SRC_DIR" \
      --exclude='./backend/.deps' --exclude='./backend/data' --exclude='./backend/tmp' \
      --exclude='./backend/__pycache__' --exclude='*/__pycache__' --exclude='./docs/ui-review/_probe' \
      -cf - . | tar -C "$INSTALL_DIR" -xf -
fi
# ⚠️ 原来这里是 `chmod -R a+rX "$INSTALL_DIR"` —— 把**整个安装目录**（含 backend/
#    与其中的配置模板）变成世界可读，是个不必要的信息暴露面。真正需要被运行用户
#    读到的只是程序文件；数据目录另有更严的权限（见下）。
chmod -R go-w "$INSTALL_DIR"
chmod -R a+rX "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend" 2>/dev/null || chmod -R a+rX "$INSTALL_DIR"
ok "程序文件已就位"

# ---------------------------------------------------------------- 虚拟环境
step "创建虚拟环境并安装依赖"
cd "$INSTALL_DIR/backend"
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  python3 -m venv "$INSTALL_DIR/venv" || die "创建 venv 失败（请确认已装 python3-venv）"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
"$INSTALL_DIR/venv/bin/pip" install --quiet -r requirements.txt || die "依赖安装失败（可试 --index-url https://pypi.org/simple）"
ok "Python 依赖已安装"

# ---------------------------------------------------------------- 配置
step "写入面板配置"
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/config.json" ]; then
  cat > "$DATA_DIR/config.json" <<JSON
{
  "port": ${PORT},
  "bind_host": "${BIND_HOST}",
  "site_name": "芮拓MC面板",
  "session_hours": 24,
  "max_login_fails": 5,
  "lock_minutes": 10,
  "sample_interval": 5,
  "log_ring_lines": 2000,
  "download_workers": 2,
  "default_java": "",
  "default_jvm_args": "-XX:+UseG1GC -XX:MaxGCPauseMillis=200",
  "default_memory_mb": 2048,
  "backup_keep": 5,
  "mirror_prefix": "",
  "rcon_enabled": false,
  "rcon_host": "127.0.0.1",
  "rcon_port": 25575,
  "rcon_password": "",
  "curseforge_api_key": "",
  "auto_restart_limit": 3,
  "theme": "darkgold"
}
JSON
  ok "已写入 $DATA_DIR/config.json（端口 $PORT）"
else
  ok "已存在 $DATA_DIR/config.json，保留不动"
fi
chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR" 2>/dev/null || warn "chown 失败，权限可能需手动调整"
chmod 700 "$DATA_DIR" 2>/dev/null || true

# ---------------------------------------------------------------- systemd
UNIT_WRITTEN=0
if [ "$WITH_SYSTEMD" = "1" ]; then
  step "注册 systemd 服务 $SERVICE_NAME"
  # ⚠️ 写到临时文件再 mv 进 /etc/systemd/system：
  #    原来直接 `cat > /etc/systemd/system/...`，目录不可变时 `set -e` 会把整个
  #    安装中止在一句没头没脑的 "Operation not permitted" 上，而且**前面已经装好的
  #    东西全部白装**。这里改用可检测的写法：失败时给明确原因并**继续完成安装**，
  #    而不是把程序留在半成品状态。
  UNIT_TMP="$(mktemp)"
  cat > "$UNIT_TMP" <<UNIT
[Unit]
Description=MC Server Panel (Minecraft server control panel)
Documentation=file://$INSTALL_DIR/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR/backend
Environment=PYTHONUTF8=1
Environment=PYTHONIOENCODING=utf-8
Environment=MC_DATA_DIR=$DATA_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/backend/run.py
Restart=on-failure
RestartSec=5
# 面板需要管理 MC 进程与端口，放宽一点限制但仍保留基本隔离
NoNewPrivileges=true
PrivateTmp=true
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT
  if cp "$UNIT_TMP" "/etc/systemd/system/$SERVICE_NAME.service" 2>/dev/null; then
    UNIT_WRITTEN=1
    # daemon-reload 失败也要说清（原来它在 set -e 下、只取末行返回值，
    # 会出现「服务报 active 但 reload 失败」的矛盾状态）
    if systemctl daemon-reload 2>/dev/null; then
      ok "服务已注册（未启动，先初始化账号）"
    else
      warn "daemon-reload 失败 —— unit 已落盘但 systemd 可能还没看到它"
      warn "手动执行： systemctl daemon-reload"
    fi
  else
    warn "写入 /etc/systemd/system/$SERVICE_NAME.service 失败（权限被环境限制）"
    warn "程序与数据已经装好，只是**没有注册成服务**。"
    warn "可手动： chattr -i /etc/systemd/system && cp $UNIT_TMP /etc/systemd/system/$SERVICE_NAME.service && systemctl daemon-reload"
    warn "或直接用前台方式启动（见下方完成摘要）"
  fi
  rm -f "$UNIT_TMP"
else
  step "跳过 systemd 服务注册（--no-systemd）"
  info "程序与数据照常安装；启动方式见下方完成摘要。"
fi

# ---------------------------------------------------------------- 初始化 + 初始口令
step "初始化数据库并创建超级管理员"
info "初始口令只在本终端打印一次；不写入任何日志文件、不显示在网页、不落盘明文。"
# ⚠️ 初始化**必须用运行身份**执行，不能以 root 跑：
#    否则 mc.db 属 root，而服务以 $RUN_USER 运行 → 登录时写 sessions 表报
#    `sqlite3.OperationalError: attempt to write a readonly database`，
#    前端只看到一句「服务器内部错误」（实测踩过，排查了很久）。
_init_as_user() {
  # 用 runuser 而不是 sudo -u：有些加固环境（宝塔安全模块）会拦 sudo -u，
  # 且是**静默拦截**（无输出、退出码 0），很难查。
  if [ "$RUN_USER" = "root" ]; then
    cd "$INSTALL_DIR/backend" && PYTHONUTF8=1 MC_DATA_DIR="$DATA_DIR" \
      "$INSTALL_DIR/venv/bin/python" -
  elif command -v runuser >/dev/null 2>&1; then
    cd "$INSTALL_DIR/backend" && runuser -u "$RUN_USER" -- env PYTHONUTF8=1 \
      MC_DATA_DIR="$DATA_DIR" "$INSTALL_DIR/venv/bin/python" -
  else
    cd "$INSTALL_DIR/backend" && su -s /bin/sh "$RUN_USER" -c \
      "PYTHONUTF8=1 MC_DATA_DIR='$DATA_DIR' '$INSTALL_DIR/venv/bin/python' -"
  fi
}
INIT_OUT="$(_init_as_user <<'PY' 2>&1
import sys, os
sys.path.insert(0, os.getcwd())
from app.database import init_db
from app.auth import ensure_admin_user
init_db()
pwd = ensure_admin_user()
if pwd:
    sys.stderr.write('INITIAL_PASSWORD=' + pwd + '\n')
else:
    sys.stderr.write('INITIAL_PASSWORD=\n')
PY
)" || true
INIT_PW="$(printf '%s' "$INIT_OUT" | sed -n 's/^INITIAL_PASSWORD=//p' | head -n1)"

# 双保险：无论初始化是否成功，都把数据目录交还给运行用户。
# （上面已用运行身份执行，这一步是兜底 —— 万一是旧库/旧目录带过来的 root 属主。）
if [ "$RUN_USER" != "root" ]; then
  chown -R "$RUN_USER:$RUN_USER" "$DATA_DIR" 2>/dev/null \
    || warn "把数据目录交给 $RUN_USER 失败，请手动：chown -R $RUN_USER:$RUN_USER $DATA_DIR"
fi
if [ -z "$INIT_PW" ] && [ -n "$(printf '%s' "$INIT_OUT" | grep -iE 'Traceback|Error' || true)" ]; then
  warn "初始化过程有报错，开头几行："
  printf '%s\n' "$INIT_OUT" | head -5 | sed 's/^/      /'
fi

SERVICE_OK=0
if [ "$UNIT_WRITTEN" = "1" ]; then
  step "启动服务"
  # `systemctl enable --now` 需要 systemd ≥220（--now 是 220 才加的）。
  # 老发行版上它会直接失败，所以拆成 enable + start 两步，兼容性最好。
  systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 \
    || warn "设置开机自启失败（systemctl enable），继续尝试启动"
  systemctl start "$SERVICE_NAME" >/dev/null 2>&1 \
    || warn "启动失败，请查看：journalctl -u $SERVICE_NAME -n 50"
  # sleep 2 太乐观：慢机器上 JVM/依赖初始化可能超过 2 秒，会误判失败。
  # 改成最多等 10 秒的轮询。
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet "$SERVICE_NAME"; then SERVICE_OK=1; break; fi
    sleep 1
  done
  if [ "$SERVICE_OK" = "1" ]; then
    ok "服务已启动并设为开机自启"
  else
    warn "服务未处于 active 状态，请查看：journalctl -u $SERVICE_NAME -n 80"
  fi
fi

# 防火墙（可选，失败不影响安装）
FIREWALL_NOTE=0
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  if ufw allow "$PORT/tcp" >/dev/null 2>&1; then ok "已放行 ufw 端口 $PORT/tcp"; else FIREWALL_NOTE=1; fi
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  if firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1; then
    ok "已放行 firewalld 端口 $PORT/tcp"
  else
    FIREWALL_NOTE=1
  fi
fi
if [ "$FIREWALL_NOTE" = "1" ]; then
  warn "防火墙放行失败 —— 面板可能**外网连不上**（本机可访问）"
  warn "请手动放行：$PORT/tcp"
elif ! command -v ufw >/dev/null 2>&1 && ! command -v firewall-cmd >/dev/null 2>&1; then
  info "未检测到 ufw/firewalld；若云厂商有安全组，记得放行 $PORT/tcp"
fi

# ---------------------------------------------------------------- 无 systemd 时的服务控制 + 开机自启
# --no-systemd（或 unit 写不进去）时，装一个自带的服务控制脚本，
# 并用 crontab @reboot 做开机自启 —— 这**不碰 systemd**，所以在加固机器上也能用。
BOOT_OK=0
if [ "$SERVICE_OK" != "1" ]; then
  step "安装服务控制脚本 panelctl"
  if [ -f "$INSTALL_DIR/panelctl" ]; then
    # 把安装时的实际值写进去（模板里是 __INSTALL_DIR__ 这类占位符）
    sed -i \
      -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      -e "s|__RUN_USER__|$RUN_USER|g" \
      -e "s|__PORT__|$PORT|g" \
      "$INSTALL_DIR/panelctl" 2>/dev/null || true
    chmod +x "$INSTALL_DIR/panelctl"
    [ "$RUN_USER" = "root" ] || chown "$RUN_USER:$RUN_USER" "$INSTALL_DIR/panelctl" 2>/dev/null || true
    ok "panelctl 已就位：bash $INSTALL_DIR/panelctl {start|stop|restart|status|log}"
  else
    warn "包内缺少 panelctl，跳过（不影响启动，只是少了个控制脚本）"
  fi

  step "注册开机自启（crontab @reboot，不碰 systemd）"
  CRON_LINE="@reboot bash $INSTALL_DIR/panelctl start >/dev/null 2>&1"
  if command -v crontab >/dev/null 2>&1; then
    # 先备份现有 crontab（这台机器上其它服务也在用它）
    _cron_bak="/root/crontab.bak.$SERVICE_NAME.$(date +%Y%m%d-%H%M%S)"
    crontab -l >"$_cron_bak" 2>/dev/null || true
    if crontab -l 2>/dev/null | grep -qF "panelctl start"; then
      ok "开机自启已存在（跳过重复注册）"
      BOOT_OK=1
    elif { crontab -l 2>/dev/null; echo "$CRON_LINE"; } | crontab - 2>/dev/null; then
      BOOT_OK=1
      ok "已加入 crontab：$CRON_LINE"
      info "原 crontab 已备份到 $_cron_bak"
      info "查看： crontab -l | grep panelctl"
    else
      warn "写入 crontab 失败 —— 开机不会自动启动"
      warn "可手动添加这一行（crontab -e）："
      warn "  $CRON_LINE"
    fi
  else
    warn "本机没有 crontab —— 开机不会自动启动"
    warn "可改用你自己的守护方式，命令是："
    warn "  bash $INSTALL_DIR/panelctl start"
  fi
fi

# ⚠️ 完成横幅要如实反映结果：原来无论是否启动成功都打印「安装完成」，
#    用户会以为已经跑起来了（这正是"以为成功、其实没起"的来源）。
if [ "$SERVICE_OK" = "1" ]; then
  printf "\n${GREEN}${BOLD}================= 安装完成 =================${RST}\n"
elif [ "$UNIT_WRITTEN" = "1" ]; then
  printf "\n${RED}${BOLD}===== 已安装，但服务没起来（需人工介入） =====${RST}\n"
elif [ "$WITH_SYSTEMD" = "0" ]; then
  printf "\n${GREEN}${BOLD}===== 安装完成（未注册服务，按下面命令前台启动） =====${RST}\n"
else
  printf "\n${RED}${BOLD}===== 已安装，但服务未注册成功（需人工介入） =====${RST}\n"
fi
printf "  面板地址   : ${GOLDL}http://<服务器IP>:%s/${RST}\n" "$PORT"
if [ "$UNIT_WRITTEN" = "1" ]; then
  printf "  服务管理   : systemctl {status|restart|stop} %s\n" "$SERVICE_NAME"
  printf "  查看日志   : journalctl -u %s -f\n" "$SERVICE_NAME"
else
  # 决定运行身份：优先用当前登录用户（-u），失败再退回"不加 -u"
  # ⚠️ 有些加固环境会拦 `sudo -u`（宝塔安全模块会弹 "Tips from BT security"），
  #    此时要用 runuser —— 所以这里两条都给出来，别让用户卡在启动方式上。
  printf "  ${GREY}注意：若用 sudo -u 被安全模块拦截，改用 runuser：${RST}\n"
  printf "    runuser -u %s -- env PYTHONUTF8=1 MC_DATA_DIR=%s %s/venv/bin/python %s/backend/run.py\n" \
    "$RUN_USER" "$DATA_DIR" "$INSTALL_DIR" "$INSTALL_DIR"
  printf "  ${GREY}前台启动：${RST}\n"
  if [ "$RUN_USER" = "root" ]; then
    printf "    cd %s/backend && PYTHONUTF8=1 MC_DATA_DIR=%s %s/venv/bin/python run.py\n" \
      "$INSTALL_DIR" "$DATA_DIR" "$INSTALL_DIR"
  else
    printf "    cd %s/backend && runuser -u %s -- env PYTHONUTF8=1 MC_DATA_DIR=%s %s/venv/bin/python run.py\n" \
      "$INSTALL_DIR" "$RUN_USER" "$DATA_DIR" "$INSTALL_DIR"
  fi
  printf "  ${GREY}后台常驻（无 systemd）：${RST}\n"
  if [ "$RUN_USER" = "root" ]; then
    printf "    cd %s/backend && nohup %s/venv/bin/python run.py > %s/run.log 2>&1 &\n" \
      "$INSTALL_DIR" "$INSTALL_DIR" "$DATA_DIR"
  else
    printf "    runuser -u %s -- env MC_DATA_DIR=%s nohup %s/venv/bin/python %s/backend/run.py > %s/run.log 2>&1 &\n" \
      "$RUN_USER" "$DATA_DIR" "$INSTALL_DIR" "$INSTALL_DIR" "$DATA_DIR"
  fi
fi
printf "  数据目录   : %s\n" "$DATA_DIR"
printf "  程序目录   : %s\n" "$INSTALL_DIR"
if [ -x "$INSTALL_DIR/panelctl" ]; then
  printf "  服务控制   : bash %s/panelctl {start|stop|restart|status|log}\n" "$INSTALL_DIR"
  if [ "$BOOT_OK" = "1" ]; then
    printf "  开机自启   : 已注册（crontab @reboot）· 查看 crontab -l | grep panelctl\n"
  else
    printf "  开机自启   : ${RED}未注册${RST} —— 需手动加 crontab（见上方提示）\n"
  fi
fi
printf "  配置文件   : %s/config.json（改端口后 restart 生效）\n" "$DATA_DIR"
printf "  重置口令   : cd %s/backend && %s/venv/bin/python tools/reset_admin.py admin\n" \
  "$INSTALL_DIR" "$INSTALL_DIR"
printf "  卸载       : sudo bash %s/install.sh --uninstall\n" "$INSTALL_DIR"
printf "\n"
printf "  默认账号是 ${GOLDL}超级管理员${RST}，用户名 ${GOLDL}admin${RST}\n"
if [ -n "$INIT_PW" ]; then
  printf "  ${GOLD}初始口令（只显示这一次，请立刻保存）：${RST} ${GOLDL}%s${RST}\n" "$INIT_PW"
  printf "  ${GREY}提示：登录后请在「设置 → 修改口令」里更换。${RST}\n"
else
  printf "  ${GREY}数据库已存在管理员账号，未生成新口令（用原口令登录）。${RST}\n"
fi
printf "  ${GREY}与 RT面板（rt-panel）零耦合：本脚本未读取、未修改、未注册到它的任何文件/服务/数据库。${RST}\n"
printf "${GOLD}============================================${RST}\n\n"

# 收尾自检：真的能连上才算成功（避免"横幅说完成、实际连不上"）
if [ "$SERVICE_OK" = "1" ] || [ "$WITH_SYSTEMD" = "0" ]; then
  if command -v curl >/dev/null 2>&1; then
    for _ in 1 2 3 4 5; do
      if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        ok "本机自检通过：http://127.0.0.1:$PORT/api/health 有响应"
        break
      fi
      sleep 1
    done
  fi
fi
