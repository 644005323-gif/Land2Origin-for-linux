#!/usr/bin/env bash
set -euo pipefail

script=${1:?Windows Python script path is required}
WINE_ROOT=/home/origin/wine-11.16-combase-test
PREFIX=/home/origin/.wine-origin2024-combase-clean
PYTHON='C:\ndax-external-test\cpython311\python.exe'
ORIGIN='C:\Program Files\OriginLab\Origin2024b\Origin64.exe'
IID='{91186B48-39F5-11D3-9367-00C04F79EAFE}'
PSOA='{00020424-0000-0000-C000-000000000046}'

unset WAYLAND_DISPLAY XDG_SESSION_TYPE
export WINEDLLOVERRIDES="winewayland.drv=d"
export DISPLAY=:100 WINEPREFIX="$PREFIX" WINEARCH=win64
export BOX64_DYNAREC=1 WINEDEBUG=-all

if [[ ! -S /tmp/.X11-unix/X100 ]]; then
  nohup Xvfb :100 -screen 0 1024x768x24 -ac >/tmp/origin-external-xvfb.log 2>&1 &
  for _ in $(seq 1 10); do
    [[ -S /tmp/.X11-unix/X100 ]] && break
    sleep 1
  done
fi
[[ -S /tmp/.X11-unix/X100 ]] || { echo "Xvfb :100 did not start" >&2; exit 1; }

started=0
if ! pgrep -af 'Origin64\.exe' >/dev/null; then
  nohup "$WINE_ROOT/bin/wine" "$ORIGIN" >/home/origin/origin-external.log 2>&1 < /dev/null &
  started=1
fi
for _ in $(seq 1 90); do
  if pgrep -af 'Origin64\.exe' >/dev/null; then break; fi
  sleep 1
done
test -n "$(pgrep -af 'Origin64\.exe' || true)"
if [[ "$started" == 1 ]]; then
  sleep 60
fi

# Starting Wine's reg.exe can exceed the VM's remaining memory while Origin is
# resident. The values are persistent, so inspect system.reg without spawning
# another Windows process and repair them only when they are absent.
registry_has_origin_proxy() {
  python3 - "$PREFIX/system.reg" "$IID" "$PSOA" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").casefold()
iid = sys.argv[2].casefold()
psoa = sys.argv[3].casefold()
for leaf in ("proxystubclsid", "proxystubclsid32"):
    section = f"[software\\\\classes\\\\interface\\\\{iid}\\\\{leaf}]"
    start = text.find(section)
    if start < 0 or psoa not in text[start:text.find("\n\n", start)]:
        raise SystemExit(1)
PY
}

if ! registry_has_origin_proxy; then
  values=""
  for _ in $(seq 1 6); do
    "$WINE_ROOT/bin/wine" reg add "HKCR\\Interface\\${IID}\\ProxyStubClsid" /ve /d "$PSOA" /f >/dev/null
    "$WINE_ROOT/bin/wine" reg add "HKCR\\Interface\\${IID}\\ProxyStubClsid32" /ve /d "$PSOA" /f >/dev/null
    if registry_has_origin_proxy; then
      values=ok
      break
    fi
    sleep 3
  done
  [[ "$values" == ok ]] || { echo "Origin IOApplication proxy registration was not applied" >&2; exit 1; }
fi

exec "$WINE_ROOT/bin/wine" "$PYTHON" "$script"
