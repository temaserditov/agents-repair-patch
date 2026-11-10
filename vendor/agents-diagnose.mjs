#!/usr/bin/env node
// One read-only check for Mac, Ubuntu and official Windows Hermes.
// Imports only Node built-ins; no package installation and no model requests.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';

const profiles=new Set(['marketer','copywriter','designer','editor','tech','assistant','sales']);
const tools=['python','node','git','ffmpeg','ffprobe'];
const titles={marketer:'Маркетолог',copywriter:'Копирайтер',designer:'Дизайнер',editor:'Монтажёр',tech:'Технарь',assistant:'Ассистент',sales:'Продажник'};
const sha=data=>crypto.createHash('sha256').update(data).digest('hex');
export function validPath(value){
  if(typeof value!=='string')return false;
  const parts=value.split('/');
  return value.length<400&&parts.length>=2&&parts.length<20&&profiles.has(parts[0])&&parts.every(p=>/^[a-zA-Z0-9_.-]+$/.test(p)&&!['.','..'].includes(p));
}
export function plain(file){
  for(let current=path.resolve(file);;current=path.dirname(current)){
    try{if(fs.lstatSync(current).isSymbolicLink())return false;}catch(error){if(error.code!=='ENOENT')throw error;}
    if(path.dirname(current)===current)break;
  }
  return true;
}
function json(file,limit=1024*1024){
  if(!plain(file)||fs.statSync(file).size>limit)throw new Error('Unsafe local inventory');
  return JSON.parse(fs.readFileSync(file,'utf8'));
}
export function validateManifest(value){
  if(value?.schemaVersion!==1||!Array.isArray(value.profiles)||!value.profiles.length||value.profiles.length>7||
    !value.profiles.every(p=>profiles.has(p))||new Set(value.profiles).size!==value.profiles.length||
    typeof value.revision!=='string'||!/^[a-zA-Z0-9.+-]{1,120}$/.test(value.revision)||![1,2,3].includes(value.tier)||!value.files||Array.isArray(value.files)||typeof value.files!=='object')throw new Error('Invalid manifest');
  const entries=Object.entries(value.files);
  if(!entries.length||entries.length>5000||entries.some(([name,digest])=>!validPath(name)||!value.profiles.includes(name.split('/')[0])||typeof digest!=='string'||! /^[a-f0-9]{64}$/.test(digest)))throw new Error('Invalid manifest');
  if(value.textHashes!==undefined&&(!value.textHashes||Array.isArray(value.textHashes)||typeof value.textHashes!=='object'||
    Object.entries(value.textHashes).some(([name,digest])=>!Object.hasOwn(value.files,name)||typeof digest!=='string'||!/^[a-f0-9]{64}$/.test(digest))))throw new Error('Invalid normalized text inventory');
  return value;
}
export function normalizedTextHash(data){
  try{return sha(new TextDecoder('utf-8',{fatal:true}).decode(data).replace(/\r\n/g,'\n'));}catch{return null;}
}
// agents-tooling-bootstrap (public student add-on) appends one marked block to a
// profile's AGENTS.md/SOUL.md; a platform update may move it under the personal
// additions heading it creates. FIX_0572: such a file is the package text plus the
// add-on, not a damaged file. Any other difference still reads as changed.
const toolingBegin='<!-- agents-student-tooling:start -->',toolingEnd='<!-- agents-student-tooling:end -->';
// A server before FIX_0572 rejects an unknown file state for the whole report:
// the add-on goes as 'changed' plus a hint that the newer server maps back.
export function wireFiles(files){return files.map(f=>f.state==='tooling-addon'?{path:f.path,state:'changed',addon:'tooling'}:f);}
export function withoutToolingBlock(text){
  const start=text.indexOf(toolingBegin),end=text.indexOf(toolingEnd);
  if(start<0||end<start||text.indexOf(toolingBegin,start+1)>=0||text.indexOf(toolingEnd,end+1)>=0)return null;
  const rest=(text.slice(0,start)+text.slice(end+toolingEnd.length)).trimEnd()
    .replace(/\n*## Личные дополнения \(перенесены при обновлении [^)\n]{1,120}\)$/,'').trimEnd();
  return [rest+'\n',rest];
}
function toolingAddonOnly(bytes,expected,textExpected){
  let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes).replace(/\r\n/g,'\n');}catch{return false;}
  const variants=withoutToolingBlock(text);
  return !!variants&&variants.some(v=>sha(v)===expected||sha(v)===textExpected);
}
export function observeFiles(home,manifest){
  return Object.entries(manifest.files).map(([relative,expected])=>{
    const file=path.join(home,'profiles',...relative.split('/'));let state='unknown';
    try{
      if(!plain(file))state='linked';
      else{
        const stat=fs.statSync(file);
        if(!stat.isFile())state='unreadable';
        else if(stat.size>8*1024*1024)state='oversized';
        else {
          const bytes=fs.readFileSync(file);
          state=sha(bytes)===expected?'ok':'changed';
          if(state==='changed'&&/\.(md|txt|yaml|yml|json|mjs|py)$/i.test(relative)&&normalizedTextHash(bytes)===(manifest.textHashes?.[relative]||expected))state='format-only';
          if(state==='changed'&&/(^|\/)(AGENTS|SOUL)\.md$/.test(relative)&&toolingAddonOnly(bytes,expected,manifest.textHashes?.[relative]||expected))state='tooling-addon';
        }
      }
    }catch(error){state=error.code==='ENOENT'?'missing':'unreadable';}
    return {path:relative,state};
  });
}
function run(program,args,options={}){
  return spawnSync(program,args,{encoding:'utf8',timeout:20000,maxBuffer:512*1024,windowsHide:true,...options});
}
export function runtime(owner,{platform=process.platform,env=process.env,execute=run}={}){
  if(platform==='win32'){
    const home=env.AG_DIAG_HERMES_HOME||env.HERMES_HOME;
    const roots=[home,env.LOCALAPPDATA&&path.join(env.LOCALAPPDATA,'hermes')].filter(Boolean);
    const candidates=roots.flatMap(root=>[path.join(root,'hermes-agent/.hermes/bin/hermes.exe'),path.join(root,'bin/hermes.exe')]);
    const launcher=candidates.find(p=>plain(p)&&fs.existsSync(p));
    if(!launcher)throw new Error('Official Hermes not found');
    const runtimeEnv={...env,...(home?{HERMES_HOME:home}:{})};
    const result=execute(launcher,['--print-runtime-command','--module','__serditov_addon__'],{env:runtimeEnv});
    const command=JSON.parse(result.stdout);
    const suffix="runpy.run_module('__serditov_addon__', run_name='__main__', alter_sys=True)";
    if(result.status!==0||command.length!==4||command[1]!=='-I'||command[2]!=='-c'||!command[3].endsWith(suffix)||!path.isAbsolute(command[0])||!plain(command[0]))throw new Error('Unsupported runtime');
    // This is the official launcher's bootstrap, not user config or a PATH Python.
    if(env.AG_DIAG_PYTHON&&path.resolve(env.AG_DIAG_PYTHON)!==path.resolve(command[0]))throw new Error('Runtime Python mismatch');
    const bootstrap=command[3].slice(0,-suffix.length);
    const info=execute(command[0],['-I','-B','-c',bootstrap+"from hermes_cli.profiles import get_profile_dir; import hermes_cli,json; from pathlib import Path; print(json.dumps({'home':str(get_profile_dir('marketer').parent.parent),'source':str(Path(hermes_cli.__file__).resolve().parents[1])}))"],{env:runtimeEnv});
    if(info.status!==0)throw new Error('Official runtime is not ready');
    const location=JSON.parse(info.stdout.trim().split('\n').at(-1));
    return {home:location.home,source:location.source,python:command[0],prefix:['-I','-B','-c'],bootstrap};
  }
  const home=env.AG_DIAG_HERMES_HOME||env.HERMES_HOME||path.join(owner,'.hermes');
  const python=path.join(home,'hermes-agent/venv/bin/python');
  // A venv interpreter is normally a symlink; the owned venv parent must be plain.
  if(!plain(path.dirname(python))||!fs.existsSync(python))throw new Error('Hermes runtime is missing');
  return {home,source:path.join(home,'hermes-agent'),python,prefix:['-B','-c'],bootstrap:''};
}
export function inspectProfiles(owner,manifest,probeSource,rt=runtime(owner)){
  return manifest.profiles.map(name=>{
    const request={home:rt.home,profile:name,skills:Object.keys(manifest.files).filter(p=>p.startsWith(name+'/skills/')&&p.endsWith('/SKILL.md'))};
    const command=rt.bootstrap+probeSource;
    const result=run(rt.python,[...rt.prefix,command],{cwd:rt.source,input:JSON.stringify(request),
      env:{...process.env,HERMES_HOME:path.join(rt.home,'profiles',name),PYTHONDONTWRITEBYTECODE:'1'}});
    let observed={instructions:'unknown',skills:[],gateway:'unknown',runtimeContext:'unverified',probe:result.error?.code==='ETIMEDOUT'?'timeout':'failed',reason:result.error?.code==='ETIMEDOUT'?'timeout':'loader-failed'};
    if(result.status===0)try{observed=JSON.parse(result.stdout.trim().split('\n').at(-1));}catch{}
    return {...observed,name};
  });
}
function allowedOrigin(value){
  const url=new URL(value);
  if(url.username||url.password||url.hash||url.search||!(url.origin==='https://school.serditov.ru'||(url.protocol==='http:'&&['127.0.0.1','localhost'].includes(url.hostname))))throw new Error('Untrusted diagnostic destination');
  return url.origin;
}
export function connection(owner){
  if(process.env.AG_DIAG_SESSION&&process.env.AG_DIAG_SECRET){
    const base=allowedOrigin(process.env.AG_DIAG_BASE||'https://school.serditov.ru');
    if(!/^[a-zA-Z0-9_-]{1,80}$/.test(process.env.AG_DIAG_SESSION))throw new Error('Invalid session');
    return {url:base+'/api/install/inventory/'+process.env.AG_DIAG_SESSION,credential:process.env.AG_DIAG_SECRET};
  }
  const root=path.join(owner,'InfobizAgents/control');
  const config=json(path.join(root,'config.json'),16384);
  if(!/^[a-zA-Z0-9_-]{1,80}$/.test(config.installationId))throw new Error('Invalid installation');
  const base=allowedOrigin(config.checkUrl);
  if(new URL(config.checkUrl).pathname!=='/api/install/control/check/'+config.installationId)throw new Error('Invalid control URL');
  let result;
  if(process.platform==='darwin'){
    const helper=path.join(root,'managed-control-keychain');
    if(!plain(helper))throw new Error('Linked credential helper');
    result=run(helper,['get','com.infobiz.agents.managed-control',config.installationId]);
  }else if(process.platform==='win32'){
    const file=path.join(root,'credential.dpapi');if(!plain(file))throw new Error('Linked credential store');
    result=run('powershell.exe',['-NoProfile','-NonInteractive','-Command',"Add-Type -AssemblyName System.Security; $b=[IO.File]::ReadAllBytes($env:AG_INVENTORY_DPAPI); $p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); [Console]::Out.Write([Text.Encoding]::UTF8.GetString($p))"],{env:{...process.env,AG_INVENTORY_DPAPI:file}});
  }else{
    // The same installation-bound OS credential used by managed control. Never
    // search .env/auth files or export it; stdout is consumed only in memory.
    result=run('systemd-creds',['decrypt','--name=control_credential','/etc/credstore.encrypted/infobiz-managed-control.credential','-']);
  }
  const credential=result.stdout?.trim();
  if(result.status!==0||!credential||credential.length<32||credential.length>256)throw new Error('Diagnostic credential unavailable');
  return {url:base+'/api/install/control/inventory/'+config.installationId,credential};
}
async function requestJson(url,credential,body){
  const response=await fetch(url,{method:body?'POST':'GET',redirect:'error',signal:AbortSignal.timeout(15000),
    headers:{authorization:'Bearer '+credential,...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  if(!response.ok)throw new Error('Report transport failed');
  const chunks=[];let size=0;
  for await(const chunk of response.body){size+=chunk.byteLength;if(size>1024*1024)throw new Error('Response too large');chunks.push(chunk);}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
function localManifest(owner){
  const root=path.join(owner,'InfobizAgents/control/tariffs');
  const receipt=json(path.join(root,'last-apply.json'));
  const state=json(path.join(root,'tariff-files.json'));
  return validateManifest({schemaVersion:1,revision:'local-record',tier:receipt.tier,profiles:receipt.profiles,files:state.files});
}
export function saveReport(owner,value){
  const name='Проверка-агентов-'+value.report.reportId+'.json';
  for(const directory of [path.join(owner,'Downloads'),owner])try{
    if(!plain(directory))continue;
    fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const destination=path.join(directory,name);
    fs.writeFileSync(destination,JSON.stringify(value,null,2)+'\n',{mode:0o600,flag:'wx'});
    return destination;
  }catch{}
  return null;
}
// Installation may finish with explicitly preserved personal files. Standalone
// diagnostics must still report their real condition and return exit 3.
export function preservedApplyWarnings(owner,manifest,files,home,{allowClean=false}={}) {
  try {
    const root=path.join(owner,'InfobizAgents/control/tariffs');
    const apply=json(path.join(root,'last-apply.json')),state=json(path.join(root,'tariff-files.json'));
    if(!Array.isArray(apply.conflicts)||new Set(apply.conflicts).size!==apply.conflicts.length||
       !apply.conflicts.every(p=>validPath(p)&&Object.hasOwn(manifest.files,p))||
       !Array.isArray(apply.merged)||!Array.isArray(apply.mergedTrailing)||
       !Array.isArray(apply.missingInstructions)||apply.missingInstructions.length||
       !Array.isArray(apply.invalidSouls)||apply.invalidSouls.length||
       !Array.isArray(apply.contentConflicts)||apply.contentConflicts.length||
       JSON.stringify(apply.profiles)!==JSON.stringify(manifest.profiles)||apply.tier!==manifest.tier||
       !state.packageFiles||typeof state.revision!=='string'||
       (manifest.revision!=='local-record'&&state.revision!==manifest.revision)||
       !Object.entries(manifest.files).every(([n,h])=>h===(manifest.revision==='local-record'?state.files[n]:state.packageFiles[n])))return false;
    const problems=files.filter(f=>!['ok','format-only','tooling-addon'].includes(f.state));
    return (allowClean||problems.length>0)&&problems.every(f=>{
      if(apply.conflicts.includes(f.path))return ['changed','unreadable','linked','oversized'].includes(f.state);
      // An online manifest names package hashes; a completed merge has its own
      // recorded result hash. Only those exact installed bytes qualify.
      if(f.state!=='changed'||!home||!validPath(f.path)||!state.files[f.path]||state.files[f.path]===state.packageFiles[f.path])return false;
      const file=path.join(home,'profiles',...f.path.split('/'));
      return plain(file)&&sha(fs.readFileSync(file))===state.files[f.path];
    });
  } catch {return false;}
}
// Only known diagnostic API gaps can be downgraded after a completed apply.
// Working loaders with missing instructions/skills and blocked tools still fail.
export function profileCheckState(manifest,observed) {
  const expected=p=>Object.keys(manifest.files).filter(f=>f.startsWith(p.name+'/skills/')&&f.endsWith('/SKILL.md'));
  const ready=p=>p.probe==='ok'&&p.reason!=='api-partial'&&p.instructions==='loaded'&&expected(p).every(f=>p.skills.includes(f));
  const unsupported=p=>['api-unsupported','module-missing'].includes(p.reason)&&['failed','unavailable'].includes(p.probe)&&p.instructions!=='not-loaded';
  const partial=p=>p.probe==='ok'&&p.reason==='api-partial'&&p.instructions==='loaded';
  return {
    loaderReady:observed.every(ready),
    compatibilityGap:observed.some(p=>unsupported(p)||partial(p))&&observed.every(p=>ready(p)||unsupported(p)||partial(p)),
    blocked:observed.some(p=>['cli','telegram'].some(platform=>p.toolAccess?.[platform]==='blocked')),
  };
}
export async function main({owner=os.homedir(),afterInstall=false}={}){
  let transport=null,manifest,basis='server';
  try{transport=connection(owner);manifest=validateManifest(await requestJson(transport.url,transport.credential));}
  catch{basis='local';try{manifest=localManifest(owner);}catch{
    const saved=saveReport(owner,{basis:'unavailable',report:{schemaVersion:1,reportId:crypto.randomUUID(),createdAt:new Date().toISOString(),problem:'expected-composition-unavailable'}});
    console.log('Не удалось получить список вашей установки. Нужна помощь поддержки; новый код не запрашивался.');
    if(saved)console.log('Файл проверки: '+saved);
    return 2;
  }}
  let rt;try{rt=runtime(owner);}catch{}
  const files=rt?observeFiles(rt.home,manifest):Object.keys(manifest.files).map(path=>({path,state:'unknown'}));
  let observed;
  try{if(!rt)throw new Error('Runtime missing');observed=inspectProfiles(owner,manifest,fs.readFileSync(new URL('./profile-probe.py',import.meta.url),'utf8'),rt);}
  catch{observed=manifest.profiles.map(name=>({name,instructions:'unknown',skills:[],gateway:'unknown',runtimeContext:'unverified',probe:'unavailable',reason:'runtime-unavailable'}));}
  const rtTools={};
  for(const name of tools){
    if(name==='python'){rtTools[name]=observed.some(p=>p.probe==='ok')?'available':'unknown';continue;}
    const result=run(name==='node'?(process.env.AG_DIAG_NODE||process.execPath):name,[name==='ffmpeg'||name==='ffprobe'?'-version':'--version'],{timeout:3000});
    rtTools[name]=result.status===0?'available':result.error?.code==='ENOENT'?'missing':'unknown';
  }
  const report={schemaVersion:1,reportId:crypto.randomUUID(),createdAt:new Date().toISOString(),target:process.platform==='darwin'?'mac':process.platform==='win32'?'windows':'vps',manifestSha256:sha(JSON.stringify(manifest)),
    files:wireFiles(files),profiles:observed,tools:rtTools};
  // Save before upload, including when offline. Only finite observations, no raw
  // output, full local paths, file contents, prompts, config or conversations.
  const checks=profileCheckState(manifest,observed);
  const compatibilityWarning=afterInstall&&checks.compatibilityGap&&!checks.blocked&&preservedApplyWarnings(owner,manifest,files,rt?.home,{allowClean:true});
  const warning='Проверка загрузчика не поддерживает эту версию Hermes, инструкции не подтверждены.';
  const saved=saveReport(owner,{basis,manifest,report,...(compatibilityWarning?{warnings:[warning]}:{})});
  const problems=files.filter(f=>!['ok','format-only','tooling-addon'].includes(f.state));
  const formatted=files.filter(f=>f.state==='format-only');
  if(formatted.length)console.log('Изменён только формат UTF-8 BOM/CRLF, текст совпадает: '+formatted.length+'.');
  const addon=files.filter(f=>f.state==='tooling-addon');
  if(addon.length)console.log('Инструкции дополнены только блоком дополнительных инструментов, остальной текст совпадает: '+addon.length+'.');
  console.log(problems.length?'Обнаружены отсутствующие, изменённые или недоступные файлы: '+problems.length+'.':'Все файлы записанного набора на месте.');
  for(const p of observed)console.log(titles[p.name]+': '+(p.probe==='ok'?'инструкции '+(p.instructions==='loaded'?'подключаются':'не подтверждены')+', видно скиллов '+p.skills.length:'подключение проверить не удалось')+'.');
  for(const p of observed)if(Object.values(p.toolAccess||{}).includes('blocked'))console.log(titles[p.name]+': в настройках запрещены необходимые инструменты. Наличие скиллов на диске этого не исправляет.');
  console.log('Содержимое уже запущенных диалогов не проверялось. Ответы ботов и подключение внешних сервисов этим тестом не подтверждаются.');
  let uploaded=false;
  if(transport&&basis==='server')try{const result=await requestJson(transport.url,transport.credential,report);uploaded=result.ok===true&&result.reportId===report.reportId;}catch{}
  if(uploaded)console.log('Отчёт отправлен и привязан к вашей установке.');
  else console.log('Отправить отчёт не удалось. '+(basis==='local'?'Сравнение выполнено с сохранённым списком установки, не с актуальными данными платформы. ':''));
  if(saved)console.log('Файл проверки: '+saved);
  else console.log('Не удалось сохранить файл проверки.');
  const {loaderReady,blocked}=checks;
  if(compatibilityWarning) {
    console.log(warning);
    console.log('Установка продолжается. Подробности сохранены в отчёте проверки.');
    return 0;
  }
  if(afterInstall&&loaderReady&&!blocked&&preservedApplyWarnings(owner,manifest,files,rt?.home)) {
    console.log('Установка завершена. Нестандартные личные файлы сохранены; их состояние указано в отчёте проверки.');
    return 0;
  }
  return problems.length||!loaderReady||blocked?3:0;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(fs.realpathSync(process.argv[1])).href){
  main({afterInstall:process.argv.includes('--after-install')}).then(code=>{process.exitCode=code;}).catch(()=>{console.log('Проверка не завершена. Настройки и данные не менялись.');process.exitCode=2;});
}
