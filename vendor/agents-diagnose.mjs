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
  return value;
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
        else state=sha(fs.readFileSync(file))===expected?'ok':'changed';
      }
    }catch(error){state=error.code==='ENOENT'?'missing':'unreadable';}
    return {path:relative,state};
  });
}
function run(program,args,options={}){
  return spawnSync(program,args,{encoding:'utf8',timeout:20000,maxBuffer:512*1024,windowsHide:true,...options});
}
function runtime(owner){
  if(process.platform==='win32'){
    const local=process.env.LOCALAPPDATA;
    if(!local)throw new Error('Official Hermes not found');
    const candidates=[path.join(local,'hermes/hermes-agent/.hermes/bin/hermes.exe'),path.join(local,'hermes/bin/hermes.exe')];
    const launcher=candidates.find(p=>plain(p)&&fs.existsSync(p));
    if(!launcher)throw new Error('Official Hermes not found');
    const result=run(launcher,['--print-runtime-command','--module','__serditov_addon__']);
    const command=JSON.parse(result.stdout);
    const suffix="runpy.run_module('__serditov_addon__', run_name='__main__', alter_sys=True)";
    if(result.status!==0||command.length!==4||command[1]!=='-I'||command[2]!=='-c'||!command[3].endsWith(suffix)||!path.isAbsolute(command[0])||!plain(command[0]))throw new Error('Unsupported runtime');
    // This is the official launcher's bootstrap, not user config or a PATH Python.
    const bootstrap=command[3].slice(0,-suffix.length);
    const info=run(command[0],['-I','-B','-c',bootstrap+"from hermes_cli.profiles import get_profile_dir; import hermes_cli,json; from pathlib import Path; print(json.dumps({'home':str(get_profile_dir('default')),'source':str(Path(hermes_cli.__file__).resolve().parents[1])}))"]);
    if(info.status!==0)throw new Error('Official runtime is not ready');
    const location=JSON.parse(info.stdout.trim().split('\n').at(-1));
    return {home:location.home,source:location.source,python:command[0],prefix:['-I','-B','-c'],bootstrap};
  }
  const home=path.join(owner,'.hermes');
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
export async function main({owner=os.homedir()}={}){
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
    const result=run(name==='node'?process.execPath:name,[name==='ffmpeg'||name==='ffprobe'?'-version':'--version'],{timeout:3000});
    rtTools[name]=result.status===0?'available':result.error?.code==='ENOENT'?'missing':'unknown';
  }
  const report={schemaVersion:1,reportId:crypto.randomUUID(),createdAt:new Date().toISOString(),target:process.platform==='darwin'?'mac':process.platform==='win32'?'windows':'vps',manifestSha256:sha(JSON.stringify(manifest)),files,profiles:observed,tools:rtTools};
  // Save before upload, including when offline. Only finite observations, no raw
  // output, full local paths, file contents, prompts, config or conversations.
  const saved=saveReport(owner,{basis,manifest,report});
  const problems=files.filter(f=>f.state!=='ok');
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
  const loaderReady=observed.every(p=>p.probe==='ok'&&p.instructions==='loaded'&&p.skills.length===Object.keys(manifest.files).filter(f=>f.startsWith(p.name+'/skills/')&&f.endsWith('/SKILL.md')).length);
  const blocked=observed.some(p=>['cli','telegram'].some(platform=>p.toolAccess?.[platform]==='blocked'));
  return problems.length||!loaderReady||blocked?3:0;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(fs.realpathSync(process.argv[1])).href){
  main().then(code=>{process.exitCode=code;}).catch(()=>{console.log('Проверка не завершена. Настройки и данные не менялись.');process.exitCode=2;});
}
