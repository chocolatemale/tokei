#!/usr/bin/env bash
# Tokei Collector — 远程设备一键部署
# 用法: bash install.sh --repo <git-repo-url> --name <device-name>
# 从 chocolatemale/tokei 检出运行。采集脚本不得来自同步仓库或 CDN。
set -e

REPO=""
NAME=""
INTERVAL=30
SOFTWARE_REPO="https://github.com/chocolatemale/tokei.git"

while [[ $# -gt 0 ]]; do
    case $1 in
        --repo) REPO="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [ "$INTERVAL" -lt 1 ] || [ "$INTERVAL" -gt 59 ]; then
    echo "同步间隔必须是 1 到 59 分钟" >&2
    exit 1
fi

if [ -z "$REPO" ]; then
    echo "用法: install.sh --repo <git-repo-url> --name <device-name>"
    echo "  --repo      同步 Git 仓库地址(必填)"
    echo "  --name      设备名(默认: hostname)"
    echo "  --interval  同步间隔分钟(默认: 30)"
    exit 1
fi

case "$REPO" in
    -*) echo "仓库地址不能以 - 开头" >&2; exit 1 ;;
esac

[ -z "$NAME" ] && NAME=$(hostname -s)

python3 -c '
import sys
value = sys.argv[1].strip()
if (not value or value in (".", "..") or len(value) > 128
        or any(ord(ch) < 32 or ch in "/\\" for ch in value)):
    raise SystemExit(1)
' "$NAME" || {
    echo "无效的设备名" >&2
    exit 1
}

echo "=== Tokei Collector 安装 ==="
echo "  仓库: $REPO"
echo "  设备: $NAME"
echo "  间隔: ${INTERVAL}m"
echo ""

TOKEI_DIR="$HOME/.tokei"
SYNC_DIR="$TOKEI_DIR/sync"
mkdir -p "$TOKEI_DIR"

if [ -d "$SYNC_DIR/.git" ]; then
    echo "[✓] 同步仓库已存在"
    git -C "$SYNC_DIR" pull -q
else
    echo "[·] 克隆同步仓库..."
    git clone -- "$REPO" "$SYNC_DIR"
fi

INSTALL_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
copy_collector_files() {
    local src_dir="$1"
    local fname dst
    for fname in usage.30s.py pricing.json pricing_overrides.json; do
        dst="$TOKEI_DIR/$fname"
        if [ -f "$src_dir/$fname" ]; then
            cp "$src_dir/$fname" "$dst"
            echo "[✓] $fname 已安装"
        fi
    done
}

if [ -f "$INSTALL_DIR/usage.30s.py" ]; then
    copy_collector_files "$INSTALL_DIR"
else
    SRC_DIR="$TOKEI_DIR/src"
    echo "[·] 克隆 $SOFTWARE_REPO 以获取采集脚本..."
    rm -rf "$SRC_DIR"
    git clone --depth 1 -- "$SOFTWARE_REPO" "$SRC_DIR"
    copy_collector_files "$SRC_DIR"
fi

if [ ! -f "$TOKEI_DIR/usage.30s.py" ]; then
    echo "缺少 usage.30s.py，请从 chocolatemale/tokei 检出后重试" >&2
    exit 1
fi

python3 - "$SYNC_DIR" "$NAME" "$INTERVAL" > "$TOKEI_DIR/config.json" <<'PY'
import json
import sys

json.dump({
    "sync_dir": sys.argv[1],
    "device_id": sys.argv[2],
    "auto_sync": True,
    "sync_interval": int(sys.argv[3]),
}, sys.stdout, ensure_ascii=False, indent=2)
print()
PY
echo "[✓] 配置已写入 $TOKEI_DIR/config.json"

SYNC_SCRIPT="$TOKEI_DIR/sync.sh"
cat > "$SYNC_SCRIPT" <<'SYNCEOF'
#!/usr/bin/env bash
set -euo pipefail
TOKEI="$HOME/.tokei"
cd "$TOKEI/sync" || exit 1

device_id="$(python3 -c '
import json, sys
with open("'"$HOME"'/.tokei/config.json", encoding="utf-8") as handle:
    value = json.load(handle).get("device_id", "")
if (not isinstance(value, str) or not value.strip() or value.strip() in (".", "..")
        or len(value.strip()) > 128
        or any(ord(ch) < 32 or ch in "/\\" for ch in value.strip())):
    raise SystemExit("invalid device_id")
print(value.strip())
')"
device_file="./${device_id}.json"

PYTHONPATH="$TOKEI" python3 "$TOKEI/usage.30s.py" --json >/dev/null 2>&1
git pull -q --rebase --autostash 2>/dev/null || true
git add -- "$device_file"
git diff --cached --quiet || git commit -qm "tokei sync ${device_id}"
git push -q 2>/dev/null || true
SYNCEOF
chmod +x "$SYNC_SCRIPT"
echo "[✓] 同步脚本: $SYNC_SCRIPT"

CRON_LINE="*/$INTERVAL * * * * $SYNC_SCRIPT >/dev/null 2>&1"
(crontab -l 2>/dev/null | grep -v "tokei/sync.sh"; echo "$CRON_LINE") | crontab -
echo "[✓] Cron 已配置: 每 ${INTERVAL} 分钟同步"

echo ""
echo "=== 安装完成 ==="
echo "  首次同步: bash $SYNC_SCRIPT"
echo "  查看状态: cat $SYNC_DIR/$NAME.json | python3 -m json.tool | head -5"
echo ""

bash "$SYNC_SCRIPT" && echo "[✓] 首次同步成功" || echo "[!] 首次同步失败,请检查 Git 权限"
