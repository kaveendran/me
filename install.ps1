<#
me_harness installer for Windows (PowerShell 5.1 or 7). macOS and Linux: install.sh.

  irm https://raw.githubusercontent.com/kaveendran/me/main/install.ps1 | iex

With options:
  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/kaveendran/me/main/install.ps1))) -NoUi
From a clone (installs that clone):
  powershell -ExecutionPolicy Bypass -File install.ps1 [options]

Options:
  -Dir PATH      where me_harness lives (default ~\Documents\me\me_harness, or $env:ME_DIR)
  -Yes           answer yes to every question (installs whatever is missing)
  -NoUi          skip building the web UI and the Obsidian plugin (no Node.js needed)
  -NoObsidian    don't offer to install Obsidian
  -NoSetup       don't start `me setup` at the end
  -Uninstall     remove the background jobs, the /me:* plugin and the `me` command
                 (your settings, history and vaults are kept)

Safe to run again: it updates the clone and reinstalls `me`. uv, Claude Code, gh and
Node.js install for your user only, with no admin rights, and downloads are checked
against their published SHA-256 sums. Only Git for Windows comes from winget.
#>
param(
    [string]$Dir = "",
    [switch]$Yes,
    [switch]$NoUi,
    [switch]$Ui,
    [switch]$NoObsidian,
    [switch]$NoSetup,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$RepoUrl = if ($env:ME_REPO) { $env:ME_REPO } else { 'https://github.com/kaveendran/me.git' }
$Branch = if ($env:ME_BRANCH) { $env:ME_BRANCH } else { '' }  # empty: the repository's default branch
$Bin = Join-Path $HOME '.local\bin'
$NodeHome = Join-Path $env:LOCALAPPDATA 'me-harness\node'
$NodeLine = 'latest-v22.x'
$Local = $false
if (-not $Dir) { $Dir = $env:ME_DIR }
if (-not $Dir) {
    $Dir = Join-Path $HOME 'Documents\me\me_harness'
    # Run from a clone (not piped from irm), and no -Dir: install that clone as it is.
    if ($PSCommandPath) {
        $here = Split-Path -Parent $PSCommandPath
        $toml = Join-Path $here 'pyproject.toml'
        if ((Test-Path $toml) -and (Select-String -Path $toml -Pattern '^name = "me-harness"' -Quiet)) {
            $Dir = $here; $Local = $true
        }
    }
}

function Say($m) { Write-Host $m }
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "! $m" -ForegroundColor Yellow }
function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Ask($question, $default = $true) {
    # -Yes says yes; without someone at the keyboard, the default.
    if ($Yes) { return $true }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) { return $default }
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Host "$question $hint"
    if (-not $reply) { return $default }
    return $reply -match '^(y|yes)$'
}

function Refresh-Path {
    # Pick up what installers just added, without opening a new window.
    $all = @($Bin) + ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') +
           ([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') + ($env:Path -split ';')
    $env:Path = (@($all | Where-Object { $_ } | Select-Object -Unique)) -join ';'
}

function Add-UserPath($dir) {
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($user -split ';' | Where-Object { $_ })
    if ($parts -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', (@($dir) + $parts) -join ';', 'User')
    }
    if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$dir;$env:Path" }
}

function Fetch($url, $out) {
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
}

function Verify($file, $sums) {
    # FILE's name must be listed in SUMS with a matching SHA-256.
    $name = Split-Path -Leaf $file
    $want = $null
    foreach ($line in Get-Content $sums) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -ge 2 -and $parts[1].TrimStart('*') -eq $name) { $want = $parts[0]; break }
    }
    if (-not $want) { throw "no published checksum for $name" }
    $got = (Get-FileHash -Algorithm SHA256 $file).Hash
    if ($got -ne $want.ToUpper()) { throw "checksum mismatch for $name; not installing it" }
}

# Plain functions with $args, so arguments such as --quiet reach the program untouched.
function Run-Native {
    # Programs don't throw when they fail; check the exit code.
    $exe, $rest = $args
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}

function Quiet {
    # Run a program, ignore its output and its exit code. Windows PowerShell turns
    # redirected stderr into errors, which 'Stop' would make fatal.
    $exe, $rest = $args
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @rest *> $null } catch { $null = $_ } finally { $ErrorActionPreference = $saved }
}

function Expand-Zip($zip, $dest) {
    # Windows' own tar.exe unpacks zips far faster than Expand-Archive.
    if (Have tar.exe) { Run-Native tar.exe -xf $zip -C $dest } else { Expand-Archive $zip -DestinationPath $dest }
}

function Latest-Release($repo) {
    # The tag GitHub's /releases/latest redirects to; no API call, so no rate limit.
    $req = [Net.HttpWebRequest]::Create("https://github.com/$repo/releases/latest")
    $req.AllowAutoRedirect = $false
    $resp = $req.GetResponse()
    try { return ($resp.Headers['Location'] -split '/')[-1] } finally { $resp.Close() }
}

$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$nodeArch = if ($arch -eq 'arm64') { 'arm64' } else { 'x64' }

# ---------------------------------------------------------------- uninstall

function Uninstall-Me {
    Step 'Removing me_harness'
    Refresh-Path
    if (Have me) { & me schedule uninstall; if ($LASTEXITCODE -ne 0) { Warn "couldn't remove every background job" } }
    if (Have claude) {
        Quiet claude plugin uninstall me@me-local
        Quiet claude plugin marketplace remove me-local
    }
    if (Have uv) { Quiet uv tool uninstall me-harness }
    Say 'Removed the background jobs, the /me:* commands in Claude Code, and the me command.'
    Say "Kept your data: $Dir (me.toml, state\) and your vaults. Delete them yourself if you want them gone."
}

# ---------------------------------------------------------------- prerequisites

function Install-Git {
    if (Have git) { return }
    if (-not (Have winget)) {
        throw 'Git for Windows is needed (Claude Code uses its Git Bash). Install it from https://git-scm.com/download/win, then run this again.'
    }
    if (-not (Ask 'Git for Windows is needed (Claude Code uses its Git Bash). Install it with winget?')) {
        throw 'Install Git for Windows from https://git-scm.com/download/win, then run this again.'
    }
    Step 'Installing Git for Windows'
    & winget install --id Git.Git --exact --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Have git)) { throw "Git didn't land on PATH; open a new PowerShell window and run this again." }
}

function Install-Uv {
    if (Have uv) { return }
    Step "Installing uv (runs me's Python)"
    $env:UV_NO_MODIFY_PATH = '1'
    # In a child PowerShell, so its installer can't end this script.
    & powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex' | Out-Null
    Remove-Item Env:UV_NO_MODIFY_PATH
    Refresh-Path
    if (-not (Have uv)) { throw "uv didn't install; see https://docs.astral.sh/uv/getting-started/installation/" }
}

function Install-Claude {
    if (Have claude) { Say "Claude Code $(& claude --version | Select-Object -First 1)"; return }
    if (Ask "Claude Code isn't installed, and me runs on it. Install it now?") {
        Step 'Installing Claude Code'
        & powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://claude.ai/install.ps1 | iex'
        Refresh-Path
        if (-not (Have claude)) { Warn "Claude Code didn't land on PATH; see https://code.claude.com/docs/en/setup" }
    } else {
        Warn 'Install Claude Code before using me: https://code.claude.com/docs/en/setup'
    }
}

function Install-Gh {
    if (Have gh) { return }
    if (-not (Ask "The GitHub CLI (gh) isn't installed. Install it for your user?")) { Warn 'GitHub triage needs gh: https://cli.github.com'; return }
    Step 'Installing the GitHub CLI'
    $v = (Latest-Release 'cli/cli').TrimStart('v')
    $name = "gh_${v}_windows_$arch"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid())
    New-Item -ItemType Directory $tmp | Out-Null
    try {
        Fetch "https://github.com/cli/cli/releases/download/v$v/$name.zip" "$tmp\$name.zip"
        Fetch "https://github.com/cli/cli/releases/download/v$v/gh_${v}_checksums.txt" "$tmp\sums.txt"
        Verify "$tmp\$name.zip" "$tmp\sums.txt"
        New-Item -ItemType Directory "$tmp\gh" | Out-Null
        Expand-Zip "$tmp\$name.zip" "$tmp\gh"
        New-Item -ItemType Directory -Force $Bin | Out-Null
        Copy-Item "$tmp\gh\bin\gh.exe" "$Bin\gh.exe" -Force
    } finally {
        Remove-Item -Recurse -Force $tmp
    }
    Add-UserPath $Bin
    Say "gh $v"
}

function Test-Node {
    if (-not ((Have node) -and (Have npm.cmd))) { return $false }
    # From `node --version` (v22.23.4): Windows PowerShell drops the quotes inside a -p script.
    $major = ((& node --version) -replace '^v', '' -split '\.')[0]
    return ([int]$major -ge 20)
}

function Install-Node {
    if (Test-Node) { return $true }
    if (-not (Ask "Node.js 20 or newer isn't installed (it builds the web UI and the Obsidian plugin). Install Node 22 for your user?")) { return $false }
    Step 'Installing Node.js'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid())
    New-Item -ItemType Directory $tmp | Out-Null
    try {
        Fetch "https://nodejs.org/dist/$NodeLine/SHASUMS256.txt" "$tmp\sums.txt"
        $file = (Get-Content "$tmp\sums.txt" | ForEach-Object { ($_ -split '\s+')[1] } |
                 Where-Object { $_ -like "*-win-$nodeArch.zip" } | Select-Object -First 1)
        if (-not $file) { throw "no Node.js build for win-$nodeArch" }
        Fetch "https://nodejs.org/dist/$NodeLine/$file" "$tmp\$file"
        Verify "$tmp\$file" "$tmp\sums.txt"
        Expand-Zip "$tmp\$file" $tmp
        if (Test-Path $NodeHome) { Remove-Item -Recurse -Force $NodeHome }
        New-Item -ItemType Directory -Force (Split-Path -Parent $NodeHome) | Out-Null
        Move-Item (Join-Path $tmp ($file -replace '\.zip$', '')) $NodeHome
    } finally {
        Remove-Item -Recurse -Force $tmp
    }
    Add-UserPath $NodeHome
    if (-not (Test-Node)) { Warn "another, older Node.js comes first on your PATH"; return $false }
    Say "Node.js $(& node --version)"
    return $true
}

function Check-Obsidian {
    if ($NoObsidian) { return }
    $installed = (Test-Path "$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe") -or (Test-Path "$env:ProgramFiles\Obsidian\Obsidian.exe")
    if ($installed) { return }
    if ((Have winget) -and (Ask "Obsidian isn't installed. Install it with winget?")) {
        & winget install --id Obsidian.Obsidian --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Warn "winget couldn't install Obsidian" }
        return
    }
    Say 'Obsidian (optional, for your notes): https://obsidian.md/download'
}

# ---------------------------------------------------------------- me_harness

function Get-Repo {
    if ($Local) { Say "Installing from this copy: $Dir"; return }
    if (Test-Path (Join-Path $Dir '.git')) {
        Step "Updating $Dir"
        $changes = & git -C $Dir status --porcelain --untracked-files=no
        if ($changes) {
            Warn "$Dir has local changes; leaving it as it is"
        } else {
            & git -C $Dir pull --ff-only --quiet
            if ($LASTEXITCODE -ne 0) { Warn "couldn't fast-forward $Dir; keeping your copy" }
        }
        return
    }
    if ((Test-Path $Dir) -and (Get-ChildItem -Force $Dir | Select-Object -First 1)) {
        throw "$Dir already exists and isn't a copy of me_harness; choose another folder with -Dir"
    }
    Step "Downloading me_harness to $Dir"
    New-Item -ItemType Directory -Force (Split-Path -Parent $Dir) | Out-Null
    $clone = @('clone', '--quiet')
    if ($Branch) { $clone += @('--branch', $Branch) }
    Run-Native git @clone $RepoUrl $Dir
}

function Install-Me {
    Step 'Installing the me command'
    # 3.12: the terminal UI's windows-curses ships wheels for it, so nothing needs compiling.
    Run-Native uv tool install --quiet --force --editable --python 3.12 $Dir
    Quiet uv tool update-shell
    Add-UserPath $Bin
    if (-not (Have me)) { throw "me didn't land on PATH ($Bin)" }
    Say "me is at $((Get-Command me).Source)"
}

function Build-Ui {
    if ($NoUi) { return }
    if (-not $Ui -and -not (Ask 'Build the Mission Control web UI and the Obsidian plugin? (needs Node.js; about a minute)')) { return }
    if (-not (Install-Node)) { Warn 'Skipped the web UI and Obsidian plugin (no Node.js). Run this installer again to add them.'; return }
    foreach ($d in 'ui', 'obsidian-plugin') {
        Step "Building $d"
        $env:NPM_CONFIG_UPDATE_NOTIFIER = 'false'
        Push-Location (Join-Path $Dir $d)
        try {
            # npm.cmd, not npm: PowerShell's default execution policy blocks npm.ps1.
            if (Test-Path package-lock.json) { & npm.cmd ci --no-audit --no-fund --loglevel=error }
            else { & npm.cmd install --no-audit --no-fund --loglevel=error }
            if ($LASTEXITCODE -eq 0) { & npm.cmd run build --silent }
            if ($LASTEXITCODE -ne 0) { Warn "$d didn't build; try later: cd `"$Dir\$d`"; npm.cmd install; npm.cmd run build" }
        } finally {
            Pop-Location
        }
    }
}

function Main {
    if ($Uninstall) { Uninstall-Me; return }
    Step "me_harness installer (Windows, $arch)"
    Refresh-Path
    Install-Git
    Install-Uv
    Install-Claude
    Install-Gh
    Check-Obsidian
    Get-Repo
    Install-Me
    Build-Ui

    Step 'Installed'
    Say "  me_harness:  $Dir   (settings go in me.toml here; your data stays on this machine)"
    Say "  command:     $((Get-Command me).Source)"
    if (-not $NoSetup -and -not [Console]::IsInputRedirected -and [Environment]::UserInteractive) {
        Say ''
        Say 'Starting guided setup: sign in to Claude and GitHub, pick your vaults, connect your phone.'
        & me setup
    } else {
        Say 'Next: run  me setup'
    }
    $global:LASTEXITCODE = 0  # the install worked; don't pass on a helper's exit code
}

# Dot-sourced (by the CI tests of single steps): define everything, run nothing.
if ($MyInvocation.InvocationName -ne '.') { Main }
