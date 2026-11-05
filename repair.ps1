# Windows PowerShell 5.1 / PowerShell 7. No elevation or policy changes.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$forward=@($args)
function Assert-Plain([string]$Path) {
  $full=[IO.Path]::GetFullPath($Path)
  $roots=@((Join-Path $env:LOCALAPPDATA 'hermes'),(Join-Path $env:USERPROFILE 'InfobizAgents'),$PSScriptRoot)
  if($env:HERMES_HOME){$roots+=$env:HERMES_HOME}
  $item=if([IO.Directory]::Exists($full)){[IO.DirectoryInfo]$full}else{[IO.FileInfo]$full}
  while($item){
    $scoped=$item.FullName -eq $full
    foreach($root in $roots){
      $base=[IO.Path]::GetFullPath($root).TrimEnd('\')
      if($item.FullName.Equals($base,[StringComparison]::OrdinalIgnoreCase) -or $item.FullName.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){$scoped=$true}
    }
    # System parents may have reparse attributes; reject entries we manage.
    if($scoped -and $item.Exists -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Unsafe path'}
    if($item -is [IO.FileInfo]){$item=$item.Directory}else{$item=$item.Parent}
  }
}
function Quote-Native([string]$Value) {
  '"'+[regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1')+'"'
}
function Invoke-RepairProcess([string]$Program,[string[]]$Arguments) {
  $start=New-Object Diagnostics.ProcessStartInfo
  $start.FileName=$Program
  $start.Arguments=(@($Arguments|ForEach-Object{Quote-Native $_}) -join ' ')
  $start.UseShellExecute=$false;$start.CreateNoWindow=$true
  $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true;$start.RedirectStandardInput=$true
  $start.StandardOutputEncoding=[Text.Encoding]::UTF8;$start.StandardErrorEncoding=[Text.Encoding]::UTF8
  $start.EnvironmentVariables['PYTHONUTF8']='1'
  # ProcessStartInfo inherits the parent's environment, including CI diagnostics.
  if($env:AGENTS_REPAIR_DEBUG_FILE){$start.EnvironmentVariables['AGENTS_REPAIR_DEBUG_FILE']=$env:AGENTS_REPAIR_DEBUG_FILE}
  $child=[Diagnostics.Process]::Start($start)
  try {
    $child.StandardInput.Close()
    $output=$child.StandardOutput.ReadToEndAsync();$errors=$child.StandardError.ReadToEndAsync()
    if(-not $child.WaitForExit(7200000)){$child.Kill();throw 'Runtime timeout'}
    if(-not $output.Wait(5000) -or -not $errors.Wait(5000)){throw 'Runtime output unavailable'}
    # Raw stderr is deliberately consumed and discarded, never sent to the chat.
    return [pscustomobject]@{Code=$child.ExitCode;Output=$output.Result}
  } finally {$child.Dispose()}
}
try {
  if($env:OS -ne 'Windows_NT'){throw 'Windows required'}
  $candidates=@()
  if($env:HERMES_HOME){$candidates+=Join-Path $env:HERMES_HOME 'hermes-agent\.hermes\bin\hermes.exe';$candidates+=Join-Path $env:HERMES_HOME 'bin\hermes.exe'}
  $candidates+=Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\.hermes\bin\hermes.exe'
  $candidates+=Join-Path $env:LOCALAPPDATA 'hermes\bin\hermes.exe'
  $found=Get-Command hermes.exe -ErrorAction SilentlyContinue
  if($found){$candidates+=$found.Source}
  $launcher=$candidates|Where-Object{Test-Path -LiteralPath $_ -PathType Leaf}|Select-Object -First 1
  if(-not $launcher -or $launcher -match '\\WindowsApps\\'){throw 'Official runtime missing'}
  Assert-Plain $launcher
  $resolved=Invoke-RepairProcess $launcher @('--print-runtime-command','--module','__serditov_addon__')
  if($resolved.Code){throw 'Runtime probe failed'}
  $runtime=ConvertFrom-Json -InputObject $resolved.Output
  $entry="runpy.run_module('__serditov_addon__', run_name='__main__', alter_sys=True)"
  if($runtime.Count -ne 4 -or $runtime[1] -ne '-I' -or $runtime[2] -ne '-c' -or -not [IO.Path]::IsPathRooted($runtime[0]) -or $runtime[0] -notmatch 'python\.exe$' -or -not ([string]$runtime[3]).EndsWith($entry)){throw 'Unsupported bootstrap'}
  Assert-Plain $runtime[0]
  Assert-Plain $PSScriptRoot
  $bootstrap=([string]$runtime[3]).Substring(0,([string]$runtime[3]).Length-$entry.Length)
  $code=$bootstrap+"import os,sys; from pathlib import Path; from hermes_cli.profiles import get_profile_dir; os.environ['AGENTS_REPAIR_HERMES_HOME']=str(Path(get_profile_dir('marketer')).parent.parent); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
  $run=Invoke-RepairProcess $runtime[0] (@('-I','-B','-c',$code,(Join-Path $PSScriptRoot 'agents_repair.py'))+$forward)
  [Console]::Out.Write($run.Output)
  $repairCode=$run.Code
  if(($repairCode -eq 0 -or $repairCode -eq 3) -and -not ($forward -contains '--no-report')){
    try {
    # Same Node selection as the official overlay. No arbitrary system Python.
    $probe=$bootstrap+"import shutil`nn=None`ntry:`n from pm.install import installed_package`n n=installed_package('node')`nexcept (ImportError,KeyError,FileNotFoundError): pass`nprint(str(n.binary) if n and n.binary else (shutil.which('node') or ''))"
    $nodeRun=Invoke-RepairProcess $runtime[0] @('-I','-B','-c',$probe)
    $nodeProgram=($nodeRun.Output.Trim() -split '\r?\n')[-1]
    if($nodeRun.Code -eq 0 -and $nodeProgram -and (Test-Path -LiteralPath $nodeProgram)){
      Assert-Plain $nodeProgram
      $ownerPath=[Environment]::GetFolderPath('UserProfile')
      for($i=0;$i -lt $forward.Count;$i++){
        if($forward[$i] -eq '--home' -and $i+1 -lt $forward.Count){$ownerPath=$forward[$i+1]}
        if($forward[$i] -like '--home=*'){$ownerPath=$forward[$i].Substring(7)}
      }
      $inventory=Invoke-RepairProcess $nodeProgram @('--input-type=module','-e','import {pathToFileURL} from "node:url"; const m=await import(pathToFileURL(process.argv[1])); process.exitCode=await m.main({owner:process.argv[2]});',(Join-Path $PSScriptRoot 'vendor/agents-diagnose.mjs'),$ownerPath)
      [Console]::Out.Write($inventory.Output)
      if($inventory.Code){Write-Host 'Inventory не подтверждён; локальный JSON ремонта сохранён.'}
    }else{Write-Host 'Node.js недоступен; локальный JSON ремонта сохранён.'}
    }catch{Write-Host 'Inventory не отправлен; локальный JSON ремонта сохранён.'}
  }
  exit $repairCode
}catch{
  # Never echo exception text / child output: bootstrap may include private paths.
  Write-Host 'Проверка не завершена: официальный Hermes или безопасный путь недоступен. Данные не удалялись.'
  exit 2
}
