# sync_deploy.ps1 —— demo-mcp 源码一键上传到云服务器（Rclone + SSH 密钥，无 rsync）
#
# 用法（在项目根，PowerShell）：
#   .\scripts\sync_deploy.ps1 -Setup         # 一次性引导：装 rclone / 生成密钥 / 推送公钥 / 联通自检
#   .\scripts\sync_deploy.ps1                # 默认：增量上传（copy，只增不删）
#   .\scripts\sync_deploy.ps1 -Mirror        # 镜像同步（sync --delete，会删服务器上本地没有的文件，慎用）
#   .\scripts\sync_deploy.ps1 -DryRun        # 演练：只列出将要传输的文件，不实际传（copy 模式）
#   .\scripts\sync_deploy.ps1 -Setup -Check  # 只做联通自检（不重装/不生成密钥）
#
# 也可双击项目根的 sync.cmd（等同上面不加参数）。
#
# 说明：本地无 rsync，改用 Rclone 走 SFTP/SSH；root + SSH 密钥免密、支持非标准端口。

param(
    [switch]$Setup,     # 一次性引导
    [switch]$Mirror,    # rclone sync --delete（镜像，会删服务器多余文件）
    [switch]$DryRun,    # 演练（copy 模式下不真传）
    [switch]$Check      # 仅做 ssh 免密自检 + 列出远端目标目录（验证用）
)

# ---------- 可配置常量 ----------
$HOST    = '129.204.156.192'
$PORT    = '39588'                                  # 非标准 SSH 端口
$USER    = 'root'                                   # SSH 登录用户（宝塔默认 root 可登录）
$KEY     = Join-Path $env:USERPROFILE '.ssh\id_ed25519'   # C:\Users\..\.ssh\id_ed25519
$KEY_FS  = $KEY.Replace('\', '/')                   # rclone 用正斜杠路径
$LOCAL   = 'D:\TushareAgent'
$REMOTE  = '/www/wwwroot/TushareAgent'              # 前导 / 表示绝对路径

# 可选排除集合（默认为空 = 字面全量）。嫌 .venv/node_modules 太大时，把 $UseExcludes 改成 $true。
$UseExcludes = $false
$Excludes = @(
    '.venv/**', 'scripts/web/node_modules/**', 'scripts/web/dist/**', '.git/**',
    '__pycache__/**', '.pytest_cache/**', '.ruff_cache/**',
    '*.egg-info/**', 'demo.db', '*.log'
)

# ---------- 定位 rclone ----------
function Find-Rclone {
    $c = Get-Command rclone -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\rclone.exe'),
        'C:\Program Files\rclone\rclone.exe',
        'C:\ProgramData\chocolatey\bin\rclone.exe',
        'C:\Program Files\Rclone\rclone.exe'
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

$rclone = Find-Rclone
if (-not $rclone) {
    Write-Host '未找到 rclone，尝试用 winget 安装 Rclone.Rclone ...' -ForegroundColor Yellow
    if (-not $Setup) {
        Write-Host '默认上传前需要先装 rclone；请先运行： .\scripts\sync_deploy.ps1 -Setup' -ForegroundColor Yellow
        exit 1
    }
    winget install --id Rclone.Rclone --accept-source-agreements --silent --disable-interactivity
    # winget 新装通常不会刷新当前进程 PATH，重新探测一次
    $links = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $env:Path = $env:Path + ';' + $links
    $rclone = Find-Rclone
    if (-not $rclone) {
        Write-Host 'winget 装完仍未定位到 rclone.exe，请重开终端后再跑一次 -Setup。' -ForegroundColor Red
        exit 1
    }
}

# ---------- 内联 SFTP 连接串（不写全局 rclone.conf，自包含、无确认弹窗）----------
# 用字符串拼接，避开 PS 5.1 对 "$VAR:" 与 "$VAR:xxx" 的变量作用域解析问题。
$sftp = ':sftp,host=' + $HOST + ',user=' + $USER + ',port=' + $PORT + ',key_file=' + $KEY_FS
$remote = $sftp + ':' + $REMOTE
$parent = $sftp + ':/www/wwwroot'

function Test-SshKeyAuth {
    # BatchMode=yes 强制非交互：只在密钥可用时成功，避免无限输密码。
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p $PORT ($USER + '@' + $HOST) 'echo key-ok' 2>$null
    return ($LASTEXITCODE -eq 0)
}

# ---------- 一次性引导 ----------
if ($Setup) {
    Write-Host '== 一次性引导：初始化 SSH 密钥 + Rclone ==' -ForegroundColor Cyan

    # 1) 生成 SSH 密钥（空密码，便于自动化）
    if (-not (Test-Path $KEY)) {
        Write-Host ('生成 SSH 密钥：' + $KEY) -ForegroundColor Yellow
        ssh-keygen -t ed25519 -N '' -f $KEY | Out-Null
    } else {
        Write-Host ('SSH 密钥已存在：' + $KEY) -ForegroundColor Green
    }

    # 2) 推送公钥到服务器（仅一次；会提示输一次服务器密码）
    if (-not (Test-SshKeyAuth)) {
        Write-Host '把公钥写入服务器 authorized_keys（需要输一次服务器密码）...' -ForegroundColor Yellow
        $raw = Get-Content -Raw ($KEY + '.pub')
        $pub = $raw.Trim().Replace([char]13, '')
        $pub | ssh -p $PORT ($USER + '@' + $HOST) 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
        if ($LASTEXITCODE -ne 0) {
            Write-Host ('公钥推送失败（exit=' + $LASTEXITCODE + '），请检查服务器是否允许 ' + $USER + ' SSH 登录。') -ForegroundColor Red
            exit 1
        }
    }

    # 3) 联通自检
    if (Test-SshKeyAuth) {
        Write-Host ('SSH 免密登录成功：' + $USER + '@' + $HOST + ' 已可用。') -ForegroundColor Green
    } else {
        Write-Host ('SSH 免密自检失败：密钥可能未被服务器接受，或 ' + $USER + ' 无法登录。') -ForegroundColor Red
        exit 1
    }

    if (-not $Check) {
        # 目标目录不存在则创建（root 可写 /www/wwwroot）
        ssh -o BatchMode=yes -p $PORT ($USER + '@' + $HOST) ('mkdir -p ' + $REMOTE)
    }

    Write-Host '列出远程 /www/wwwroot 下的目录（应能见到 TushareAgent 或其父级）：' -ForegroundColor Cyan
    & $rclone lsd $parent
    if (-not $Check) {
        Write-Host '引导完成。以后直接运行： .\scripts\sync_deploy.ps1' -ForegroundColor Green
    }
    exit 0
}

# ---------- 日常上传 ----------
if ($Check) {
    if (-not (Test-SshKeyAuth)) { Write-Host 'SSH 免密不可用，请先运行 -Setup。' -ForegroundColor Red; exit 1 }
    Write-Host ('远程目录：' + $REMOTE) -ForegroundColor Cyan
    & $rclone lsd $parent
    exit 0
}

# 密钥检查（默认上传前也要能免密）
if (-not (Test-Path $KEY)) {
    Write-Host ('未找到 SSH 密钥 ' + $KEY + ' ，请先运行： .\scripts\sync_deploy.ps1 -Setup') -ForegroundColor Red
    exit 1
}
if (-not (Test-SshKeyAuth)) {
    Write-Host 'SSH 免密不可用，请先运行 -Setup 完成公钥推送。' -ForegroundColor Red
    exit 1
}

# 组装参数：copy 或 sync / --delete
$ra = @()
if ($Mirror) { $ra += 'sync', '--delete' } else { $ra += 'copy' }
if ($UseExcludes) { foreach ($e in $Excludes) { $ra += '--exclude', $e } }
if ($DryRun) { $ra += '--dry-run' }
$ra += '--transfers', '8', '--verbose', $LOCAL, $remote

Write-Host ('== 开始同步（' + ($ra -join ' ') + '）==') -ForegroundColor Cyan
if ($Mirror) { Write-Host '注意：sync --delete 会删除服务器上本地没有的文件！' -ForegroundColor Yellow }

& $rclone @ra
$code = $LASTEXITCODE
$modeLabel = '增量'
if ($Mirror) { $modeLabel = '镜像' }
if ($code -eq 0) {
    Write-Host ('== 同步结束（' + $modeLabel + '）==') -ForegroundColor Green
} else {
    Write-Host ('== 同步结束，rclone 退出码 ' + $code + ' ==') -ForegroundColor Red
}
exit $code
