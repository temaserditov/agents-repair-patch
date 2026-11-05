#!/usr/bin/env bash
# Bash 3.2; never sources shell startup files or installation .env.
set -u
bundle_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 2
owner_path="$HOME"
no_report=0
previous=''
for argument in "$@"; do
  if [[ "$previous" == --home ]]; then owner_path="$argument"; fi
  case "$argument" in
    --home=*) owner_path="${argument#--home=}" ;;
    --no-report) no_report=1 ;;
  esac
  previous="$argument"
done
case "$(uname -s)" in Darwin|Linux) ;; *) printf '%s\n' 'Поддерживаются Mac и Linux VPS.' >&2; exit 2 ;; esac
hermes_python="$owner_path/.hermes/hermes-agent/venv/bin/python"
if [[ ! -x "$hermes_python" ]]; then
  printf '%s\n' 'Python установленного Hermes не найден. Переустановка не запускалась.' >&2
  exit 2
fi
# The interpreter itself can be the normal venv symlink. Guard its ancestors,
# bundle, owner and installation in Python before importing release helpers.
"$hermes_python" -I -B -c 'import pathlib,runpy,sys; p=pathlib.Path(sys.argv[1]).absolute(); roots=[p.parent,pathlib.Path(sys.executable).absolute().parent];
for root in roots:
 for item in [root,*root.parents]:
  if item.is_symlink(): print("Небезопасный путь; проверка остановлена."); sys.exit(2)
sys.argv.pop(0); runpy.run_path(str(p),run_name="__main__")' "$bundle_dir/agents_repair.py" "$@"
repair_code=$?
if [[ "$no_report" == 0 && ( "$repair_code" == 0 || "$repair_code" == 3 ) ]]; then
  node_program="$owner_path/.hermes/node/bin/node"
  if [[ ! -x "$node_program" ]]; then node_program="$(command -v node || true)"; fi
  if [[ -n "$node_program" ]]; then
    "$node_program" --input-type=module -e 'import {pathToFileURL} from "node:url"; const m=await import(pathToFileURL(process.argv[1])); process.exitCode=await m.main({owner:process.argv[2]});' "$bundle_dir/vendor/agents-diagnose.mjs" "$owner_path" || printf '%s\n' 'Отправка или полная проверка inventory не завершена; локальный отчёт ремонта сохранён.'
  else
    printf '%s\n' 'Node.js не найден; локальный отчёт ремонта сохранён, inventory не отправлен.'
  fi
fi
exit "$repair_code"
