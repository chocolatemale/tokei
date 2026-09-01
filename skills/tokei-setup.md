# Tokei 多设备数据同步 — 配置指南

交互式引导用户完成 Tokei 多设备同步的全部配置。

## 触发

用户说 "setup tokei"、"配置 tokei 同步"、"tokei sync setup"、"设置用量同步" 时触发。

## 架构说明

- 每台设备独立运行 `usage.30s.py` 采集本机 AI 用量，生成 `<device_id>.json`
- 所有设备通过一个 **私有 Git 仓库** (`~/.tokei/sync/`) 同步数据
- Mac 端 Tokei.app 聚合所有设备数据展示；Linux 端通过 crontab 自动采集+推送
- 采集脚本从本仓库检出复制，或 `git clone https://github.com/chocolatemale/tokei.git` 后复制 `usage.30s.py`。不要从同步仓库或 CDN 下载。

## 执行流程

按以下步骤逐一检查和执行，已完成的步骤跳过：

### 步骤 1: 检查环境

```bash
which git >/dev/null 2>&1 && echo "✅ git" || echo "❌ git"
which python3 >/dev/null 2>&1 && echo "✅ python3" || echo "❌ python3"
which gh >/dev/null 2>&1 && echo "✅ gh CLI" || echo "⚠️ gh CLI 未安装(可选，手动配置也行)"
[ -d ~/.tokei/sync/.git ] && echo "✅ 同步仓库已存在" || echo "⏳ 同步仓库未配置"
[ -f ~/.tokei/config.json ] && echo "✅ 本机配置存在" || echo "⏳ 本机未配置"
crontab -l 2>/dev/null | grep -q tokei && echo "✅ crontab 已配置" || echo "⏳ crontab 未配置"
```

如果缺少 git 或 python3，提示安装后继续。

### 步骤 2: 安装采集脚本

```bash
mkdir -p ~/.tokei
# 采集脚本来自 chocolatemale/tokei 检出，不要从同步仓库复制
if [ -f ./usage.30s.py ]; then
  cp ./usage.30s.py ~/.tokei/usage.30s.py
else
  git clone --depth 1 -- https://github.com/chocolatemale/tokei.git ~/.tokei/src
  cp ~/.tokei/src/usage.30s.py ~/.tokei/usage.30s.py
fi
chmod +x ~/.tokei/usage.30s.py
echo "✅ 采集脚本已安装"
```

### 步骤 3: 配置同步仓库

判断当前场景：

**场景 A — 首台设备（需要创建仓库）：**

```bash
# 有 gh CLI
gh repo create tokei-sync --private
SYNC_REPO=$(gh repo view tokei-sync --json sshUrl -q .sshUrl)
case "$SYNC_REPO" in -*) echo "仓库地址不能以 - 开头" >&2; exit 1 ;; esac
git clone -- "$SYNC_REPO" ~/.tokei/sync

# 没有 gh CLI — 提示用户手动在 GitHub 创建私有仓库 tokei-sync，然后：
SYNC_REPO="git@github.com:<用户名>/tokei-sync.git"
case "$SYNC_REPO" in -*) echo "仓库地址不能以 - 开头" >&2; exit 1 ;; esac
git clone -- "$SYNC_REPO" ~/.tokei/sync
```

初始化并推送：

```bash
cd ~/.tokei/sync
git commit --allow-empty -m "init" && git push -u origin main
```

**场景 B — 加入已有仓库（其他设备已配好）：**

询问用户仓库地址，然后：

```bash
case "$SYNC_REPO" in -*) echo "仓库地址不能以 - 开头" >&2; exit 1 ;; esac
git clone -- "$SYNC_REPO" ~/.tokei/sync
```

### 步骤 4: 写入本机配置

```bash
DEVICE_NAME=$(hostname -s)
python3 -c '
import sys
value = sys.argv[1].strip()
if (not value or value in (".", "..") or len(value) > 128
        or any(ord(ch) < 32 or ch in "/\\" for ch in value)):
    raise SystemExit("无效的设备名")
' "$DEVICE_NAME"
cat > ~/.tokei/config.json <<EOF
{
  "device_id": "$DEVICE_NAME",
  "sync_dir": "~/.tokei/sync",
  "auto_sync": false,
  "sync_interval": 30
}
EOF
echo "✅ 本机配置完成: $DEVICE_NAME"
```

### 步骤 5: 配置定时采集（Linux/远程服务器）

Mac 端由 Tokei.app 负责采集，跳过此步。仅 Linux/远程服务器需要：

```bash
(crontab -l 2>/dev/null; echo '*/30 * * * * cd ~/.tokei/sync && python3 ~/.tokei/usage.30s.py --json >/dev/null && device_file="./$(python3 -c "import json;print(json.load(open(\"$HOME/.tokei/config.json\"))[\"device_id\"])").json" && git pull -q && git add -- "$device_file" && git diff --cached --quiet || git commit -qm sync && git push -q') | crontab -
echo "✅ crontab 已配置，每 30 分钟自动采集并同步"
```

### 步骤 6: 验证

```bash
# 立即采集一次
cd ~/.tokei/sync && python3 ~/.tokei/usage.30s.py --json >/dev/null 2>&1

# 检查生成的数据文件
DEVICE_NAME=$(cat ~/.tokei/config.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["device_id"])')
[ -f ~/.tokei/sync/${DEVICE_NAME}.json ] && echo "✅ 数据文件已生成" || echo "❌ 数据文件未找到"

# 推送
cd ~/.tokei/sync && git add -- "./${DEVICE_NAME}.json" && git diff --cached --quiet || git commit -m "sync $DEVICE_NAME" && git push
echo ""
echo "═══ 完成 ═══"
echo "  本机: $DEVICE_NAME"
ls ~/.tokei/sync/*.json 2>/dev/null | while read f; do
    name=$(basename "$f" .json)
    echo "  📱 $name"
done
```

### 步骤 7: 提示后续

- **Mac 用户**：Tokei 菜单栏 → 设置 → 多设备同步 → 开启即可
- **其他设备**：在新设备上重复步骤 1-6，clone 同一个仓库即可加入

## 交互策略

- 每一步执行前先告诉用户要做什么，得到确认后再执行
- 已完成的步骤直接跳过并显示 ✅
- 首先判断场景：首台设备 vs 加入已有仓库
- 判断平台：Mac(跳过 crontab) vs Linux(需要 crontab)
- 出错时给出具体的修复建议

## 回答风格

简洁直接，每步一行结果。不要长段解释。像 CLI 安装向导一样。
