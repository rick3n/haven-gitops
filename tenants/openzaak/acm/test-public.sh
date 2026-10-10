#!/usr/bin/env bash
# Demo-stap 6 via de publieke host: client 'behandeling' leest CM/SO, ziet 0 vorderingszaken, 403 op de vorderingszaak.
set -uo pipefail
export KUBECONFIG=${KUBECONFIG:-$HOME/nixos_eigen_hardware1/haven/talos/kubeconfig}
P=$(kubectl -n openzaak get pod -o name | grep -E 'pod/openzaak-[a-z0-9]+-[a-z0-9]+$' | head -1)
tok() { local sec; sec=$(kubectl -n openzaak get secret openzaak-koppeling -o jsonpath="{.data.secret_$1}" | base64 -d)
  kubectl -n openzaak exec -i "$P" -- python -c "import jwt,time,sys;print(jwt.encode({'iss':'$1','iat':int(time.time()),'client_id':'$1','user_id':'$2','user_representation':'$2'},sys.stdin.read().strip(),algorithm='HS256'))" <<<"$sec"; }
B=https://openzaak.haven.3n.nl/zaken/api/v1
TB=$(tok behandeling behandelaar.demo); TA=$(tok openzaak admin)
# -k: haven-ca zit niet in de truststore van havenpc; op de hx90 (root-CA geïmporteerd) kan het zonder.
get() { curl -sk -H "Authorization: Bearer $1" -H "Accept-Crs: EPSG:4326" "$2"; }
code() { curl -sk -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" -H "Accept-Crs: EPSG:4326" "$2"; }
for id in CM-2026-0001 SO-2026-0001 V-2026-0001; do
  q="$B/zaken?identificatie=$id&bronorganisatie=000000000"
  echo "$id als behandeling: HTTP $(code "$TB" "$q") $(get "$TB" "$q" | grep -o '"count":[0-9]*')"
done
VURL=$(get "$TA" "$B/zaken?identificatie=V-2026-0001&bronorganisatie=000000000" | grep -o '"url":"[^"]*/zaken/api/v1/zaken/[a-f0-9-]*"' | head -1 | cut -d'"' -f4)
echo "vorderingszaak: $VURL"
echo "GET als behandeling: HTTP $(code "$TB" "$VURL")"; get "$TB" "$VURL" | cut -c1-160; echo
echo "Token voor Postman/curl (behandeling, 1 uur geldig): $TB"
