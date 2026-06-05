$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Push-Location $ProjectRoot

try {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        throw 'wsl is not available on this machine.'
    }

    if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
        throw 'ssh is not available on this machine.'
    }

    $VpsHost = 'ssh.archun.net'
    $VpsPort = 2005
    $VpsUser = 'chun'
    $VpsPath = '/opt/ptcg'

    $RsyncExcludes = @(
        '--exclude=.env'
        '--exclude=.env.local'
        '--exclude=.env.production'
        '--exclude=.git'
        '--exclude=.reasonix'
        '--exclude=.codex'
        '--exclude=data'
        '--exclude=__pycache__'
        '--exclude=.pytest_cache'
        '--exclude=.mypy_cache'
        '--exclude=.ruff_cache'
        '--exclude=node_modules'
        '--exclude=dist'
        '--exclude=build'
        '--exclude=*.pyc'
        '--exclude=*.dump'
        '--exclude=*.sql'
        '--exclude=*.backup'
        '--exclude=*.zip'
        '--exclude=*.7z'
        '--exclude=*.tar'
        '--exclude=*.tar.gz'
    )

    $VpsTarget = "${VpsUser}@${VpsHost}:${VpsPath}/"

    Write-Host "[1/3] Syncing code to $VpsTarget"
    $RsyncArgs = @(
        'rsync'
        '-avz'
        '--delete'
        '--partial'
        '--append-verify'
        '--info=progress2'
    ) + $RsyncExcludes + @(
        './'
        $VpsTarget
        '-e'
        "ssh -p $VpsPort"
    )
    & wsl @RsyncArgs
    if ($LASTEXITCODE -ne 0) {
        throw "rsync failed with exit code $LASTEXITCODE."
    }

    Write-Host "[2/3] Rebuilding web container on VPS"
    $RemoteRestart = "cd $VpsPath && docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.prod.local.yml up -d --build db ptcg-web"
    & ssh -p $VpsPort "$VpsUser@$VpsHost" $RemoteRestart
    if ($LASTEXITCODE -ne 0) {
        throw "remote restart failed with exit code $LASTEXITCODE."
    }

    Write-Host "[3/3] Verifying API"
    $RemoteCheck = 'curl -fsS http://127.0.0.1:5577/api/decks/japanese/list?page=1 > /dev/null'
    & ssh -p $VpsPort "$VpsUser@$VpsHost" $RemoteCheck
    if ($LASTEXITCODE -ne 0) {
        throw "API verification failed with exit code $LASTEXITCODE."
    }

    Write-Host 'Deployment complete.'
}
finally {
    Pop-Location
}
