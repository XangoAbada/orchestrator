# Context use of every running Claude Code and Codex process, as JSON:
# [{ app, ancestors: [pids up the tree], tokens, window }]. The mod matches ancestors to terminal pids.
$ErrorActionPreference = 'SilentlyContinue'
$procs = @{}
Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId] = $_ }

function Ancestors($p) {
  $out = @(); $cur = $p
  for ($i = 0; $i -lt 6 -and $cur; $i++) { $out += [int]$cur.ParentProcessId; $cur = $procs[[int]$cur.ParentProcessId] }
  $out
}

# Last lines of a file, without reading the whole transcript.
function Tail($path, $bytes = 524288) {
  $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite,Delete')
  try {
    $fs.Seek([Math]::Max(0, $fs.Length - $bytes), 'Begin') | Out-Null
    $text = (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd()
  } finally { $fs.Close() }
  $lines = $text -split "`n"
  [array]::Reverse($lines)
  $lines
}

function FirstLine($path) {
  $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite,Delete')
  try { (New-Object IO.StreamReader($fs)).ReadLine() } finally { $fs.Close() }
}

$home_ = $env:USERPROFILE
$result = @()

foreach ($p in $procs.Values | Where-Object Name -eq 'claude.exe') {
  $sess = Get-Content "$home_\.claude\sessions\$($p.ProcessId).json" -Raw | ConvertFrom-Json
  if (-not $sess.sessionId) { continue }
  $file = Get-ChildItem "$home_\.claude\projects\*\$($sess.sessionId).jsonl" | Select-Object -First 1
  if (-not $file) { continue }
  foreach ($l in Tail $file.FullName) {
    if ($l -notmatch '"usage"') { continue }
    $u = ($l | ConvertFrom-Json).message.usage
    if (-not $u) { continue }
    $result += @{ app = 'claude'; ancestors = @(Ancestors $p); tokens = [int]($u.input_tokens + $u.cache_read_input_tokens + $u.cache_creation_input_tokens); window = $null }
    break
  }
}

# ponytail: a Codex process gets the newest interactive rollout written since it started; two Codex sessions at once can swap. Upgrade: match session_meta cwd to the pane's folder.
$codex = $procs.Values | Where-Object { $_.Name -eq 'codex.exe' -and -not ($procs[[int]$_.ParentProcessId].Name -eq 'codex.exe') }
if ($codex) {
  $rollouts = Get-ChildItem "$home_\.codex\sessions" -Recurse -Filter 'rollout-*.jsonl' |
    Where-Object LastWriteTime -gt (Get-Date).AddDays(-2) | Sort-Object LastWriteTime -Descending
  foreach ($p in $codex) {
    foreach ($f in $rollouts) {
      if ($f.LastWriteTime -lt $p.CreationDate) { break }
      if ((FirstLine $f.FullName) -match '"originator":"codex_exec"') { continue }
      foreach ($l in Tail $f.FullName) {
        if ($l -notmatch '"token_count"') { continue }
        $info = ($l | ConvertFrom-Json).payload.info
        if (-not $info) { continue }
        $result += @{ app = 'codex'; ancestors = @(Ancestors $p); tokens = [int]$info.last_token_usage.total_tokens; window = [int]$info.model_context_window }
        break
      }
      break
    }
  }
}

ConvertTo-Json @($result) -Depth 4 -Compress
