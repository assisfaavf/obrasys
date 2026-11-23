param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$url = 'http://127.0.0.1:8000'
$logDirectory = Join-Path $env:LOCALAPPDATA 'Obrasys'
$log = Join-Path $logDirectory 'inicio.log'
$mutex = New-Object System.Threading.Mutex($false, 'Local\ObrasysLauncher')
$ownsMutex = $false
$locationPushed = $false
$composeArgs = @('--env-file', '.env')

function Invoke-Docker {
    param([string[]]$Arguments)
    # Docker writes progress and some diagnostics to stderr even on success.
    $ErrorActionPreference = 'Continue'
    & $docker @Arguments 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
    return $LASTEXITCODE
}

try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { return }

    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Get-Date -Format o | Set-Content -LiteralPath $log -Encoding utf8
    Push-Location -LiteralPath $project
    $locationPushed = $true
    if (Test-Path -LiteralPath '.env.desktop') {
        $composeArgs += @('--env-file', '.env.desktop')
    }
    $composeArgs += @('-f', 'docker-compose.yml', '-f', 'docker-compose.desktop.yml')

    $docker = (Get-Command docker.exe).Source
    if ((Invoke-Docker @('info', '--format', '{{.ServerVersion}}')) -ne 0) {
        $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $dockerDesktop)) { throw 'Docker Desktop nao encontrado.' }
        if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
            Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
        }
        $deadline = (Get-Date).AddMinutes(2)
        while ((Invoke-Docker @('info', '--format', '{{.ServerVersion}}')) -ne 0) {
            if ((Get-Date) -ge $deadline) { throw 'O Docker Desktop nao ficou pronto.' }
            Start-Sleep -Seconds 3
        }
    }

    if ((Invoke-Docker (@('compose') + $composeArgs + @('up', '-d'))) -ne 0) {
        throw 'O Docker Compose nao conseguiu iniciar o Obrasys.'
    }
    $deadline = (Get-Date).AddMinutes(2)
    while ($true) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/admin/login/" -TimeoutSec 5
            if ($response.StatusCode -eq 200 -and $response.Content -match 'csrfmiddlewaretoken') { break }
        } catch {
            Add-Content -LiteralPath $log -Value $_.Exception.Message
        }
        if ((Get-Date) -ge $deadline) {
            Invoke-Docker (@('compose') + $composeArgs + @('logs', '--tail', '60', 'web', 'db')) | Out-Null
            throw 'O Obrasys nao respondeu a tempo.'
        }
        Start-Sleep -Seconds 2
    }

    if ($CheckOnly) {
        Write-Output "Obrasys disponivel em $url"
        return
    }
    $edge = @(${env:ProgramFiles(x86)}, $env:ProgramFiles) |
        Where-Object { $_ } |
        ForEach-Object { Join-Path $_ 'Microsoft\Edge\Application\msedge.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if (-not $edge) { throw 'Microsoft Edge nao encontrado.' }
    Start-Process -FilePath $edge -ArgumentList "--app=$url/"
} catch {
    if ($CheckOnly) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "$($_.Exception.Message)`r`n`r`nLog: $log", 'Obrasys',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
} finally {
    if ($locationPushed) { Pop-Location }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
