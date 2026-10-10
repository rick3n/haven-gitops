# Open Zaak + Open Notificaties (Haven-lab)

| | |
|---|---|
| Open Zaak 1.30 | https://openzaak.haven.3n.nl — admin: `/admin/`, API-dashboard: `/` |
| Open Notificaties 1.16 | https://opennotificaties.haven.3n.nl |
| Databases | CloudNative-PG, `openzaak-pg` (PostGIS-image, pg_trgm) en `opennotificaties-pg` |
| Media | PVC op StorageClass `nfs` (RWX) |
| Secrets | `secrets.sops.yaml` (DB, SECRET_KEY, superuser `admin`), `koppeling.sops.yaml` (ZGW-clients) |

Koppeling (django-setup-configuration, draait als Helm-hook na elke upgrade):
Open Zaak publiceert via client `openzaak` naar ON (in-cluster `http://opennotificaties.openzaak.svc`),
ON valideert JWT's via de Autorisaties API van Open Zaak (client `opennotificaties`,
`http://openzaak-nginx.openzaak.svc`). In-cluster http omdat de gateway-cert van `haven-ca`
niet in de container-truststore zit; publieke URL's blijven https.

Getest 2026-10-09: catalogus `MELD` en zaaktype `MELDING` ("Melding behandelen") aangemaakt via
de Catalogi API; de notificatie op kanaal `zaaktypen` is door de worker afgeleverd.

Admin-wachtwoorden: `sops -d secrets.sops.yaml` (age-sleutel op havenpc) of
`kubectl -n openzaak get secret openzaak-values -o jsonpath='{.data.values\.yaml}' | base64 -d`.

Een JWT voor de API maak je met client_id/secret uit `koppeling.sops.yaml` (HS256, claims
`iss`, `iat`, `client_id`, `user_id`, `user_representation`).

Volgende stap: de ZTC "melding → advies" (statustypen, resultaattypen, roltypen,
informatieobjecttypen) en Open Formulieren als meldformulier.

## ACM-demo

`acm/` bevat de ACM-zaaktypencatalogus, de drie API-clients met autorisaties en de
demodata als desired state (Job `acm-desired-state`, draait bij elke wijziging van
`acm/acm.py`). Zie `acm/README.md`.
