# ACM-demo op Open Zaak — desired state

Demo voor de ACM: van consumentenmelding (ConsuWijzer) via signaalonderzoek naar
een vordering bij een leverancier, met **functionele scheiding tussen forensisch
en zaakbehandeling**. De scheiding wordt niet met een werkafspraak maar met de
ZGW-API's afgedwongen: vertrouwelijkheid op zaken en documenten, en per
API-client (Applicatie) autorisaties per zaaktype en documenttype.

Ontwerp: `haven/docs/acm-demo-zaaktypencatalogus.xlsx` in `nixos_eigen_hardware1`
(11 tabbladen: zaaktypen, status-/resultaat-/roltypen, documenttypen,
eigenschappen, applicaties, Keycloak, demoscenario, bewust-niet).

## Wat `acm.py` doet

| Stap | Inhoud | Idempotent |
|---|---|---|
| `catalogus` | Catalogus `ACM` (RSIN 000000000) met 10 informatieobjecttypen en 3 zaaktypen, gepubliceerd | bestaat → overslaan (gepubliceerde zaaktypen zijn immutable) |
| `applicaties` | `loket`, `behandeling`, `forensisch` met autorisaties (zrc per zaaktype, drc per documenttype, max. vertrouwelijkheid) | bestaat → `PUT` (altijd in sync met git) |
| `demo [snelkoop\|spoor\|all]` | Per casus: 5 meldingen, onderzoek `SO-2026-000n` met de meldingen eraan, triage, verzoek tot vordering; vorderingszaak `V-2026-000n` (geheim) met vordering en ruwe dataset | per identificatie: bestaat → overslaan |
| `check` | Stap 6 van het scenario, voor beide casussen: `behandeling` leest het onderzoek (200), krijgt 403 op de vorderingszaak en de ruwe dataset, ziet 0 vorderingszaken | exit 1 als de scheiding niet klopt |

Twee casussen (`CASUSSEN` in het script):

| | `snelkoop` (nr 1) | `spoor` (nr 2) |
|---|---|---|
| Meldingen | CM-2026-0001..05, webwinkel levert niet | CM-2026-0101..05, reizigers GoVolta Amsterdam–Berlijn, 9 oktober: zeven uur vast bij Bad Bentheim |
| Loket | drie keer "Signaal geregistreerd" | drie keer **"Doorverwezen"** (reizigersrechten: ILT / Geschillencommissie OV — geen ACM-bevoegdheid), twee keer signaal over NS |
| Onderzoek | SO-2026-0001: structurele niet-nakoming | SO-2026-0002: benadeling nieuwkomer door NS/DB (verzoek GoVolta aan de ACM; AJC, defecte ICE niet teruggezet) |
| Vordering | V-2026-0001 bij het betaalplatform | V-2026-0002 bij ProRail: verkeersleidingslogs en communicatie 9 okt 17:00–01:00; DB InfraGO via de Bundesnetzagentur |
| Ruwe data | transacties van niet-betrokken klanten | álle treinen van die avond, met namen en roosters van verkeersleiders |

De spoorcasus is een **reconstructie uit persberichten** (Treinenweb, Oost, Treinreiziger,
10 oktober 2026); de bevindingen, vorderingen en datasets zijn verzonnen en stellen niets
vast over NS, DB, ProRail of GoVolta. KvK/RSIN-nummers zijn demowaarden.

De Job `acm-desired-state` draait `all` in het Open Zaak-image (heeft `requests`
en `PyJWT`). Het script zit als ConfigMap met hash-suffix in de tenant:
wijzigt `acm.py`, dan maakt Flux de Job opnieuw aan (`force`-annotatie).
Faalt de 403-test, dan faalt de Job — zichtbaar in `flux get kustomizations`.

| Zaaktype | Selectielijst (2020) | Resultaattypen |
|---|---|---|
| CONSUMENTENMELDING | 21 Adviseren | 21.1 Advies gegeven / Doorverwezen / Signaal geregistreerd (P5Y); 21.3 Niet bevoegd (P1Y) |
| SIGNAALONDERZOEK | 12 Toezien en handhaven | 12.1 Geen vervolg (P5Y); 12.1.10 Waarschuwing, 12.2.2 Formeel onderzoek (blijvend bewaren) |
| VORDERING | 12 Toezien en handhaven | 12.1.8 Vrijgegeven (P6M, ruwe data vernietigen); 12.3 Ingetrokken (P1Y) |

Selectielijstklassen worden op nummer opgezocht bij `selectielijst.openzaak.nl`
(egress uit het cluster is nodig; Open Zaak valideert ze zelf ook).

## De scheiding in één tabel

| Client | CONSUMENTENMELDING | SIGNAALONDERZOEK | VORDERING |
|---|---|---|---|
| `loket` | alles, t/m zaakvertrouwelijk | alleen lezen, t/m intern | — |
| `behandeling` | lezen | alles, t/m zaakvertrouwelijk | **geen autorisatie → 403** |
| `forensisch` | — | lezen/bijwerken + documenten aanmaken (vrijgeven) | alles, t/m geheim |

Het "vrijgegeven bewijsstuk" is een documenttype van SIGNAALONDERZOEK
(zaakvertrouwelijk) dat forensisch aanmaakt; de ruwe dataset blijft een
documenttype van VORDERING (geheim) waar `behandeling` geen autorisatie op heeft.

## Secrets

JWT-secrets van de drie clients staan in `../koppeling.sops.yaml`
(`secret_loket`, `secret_behandeling`, `secret_forensisch`) en worden door de
Open Zaak-configuratiestap `vng_api_common_credentials` geregistreerd. Ophalen
op havenpc: `pw openzaak openzaak-koppeling secret_behandeling` (zie
`haven/SLEUTELS.md`).

## Handmatig draaien en testen

- `run-local.sh <catalogus|applicaties|demo|check|all> [snelkoop|spoor|all]` — draait
  `acm.py` in een eigen tijdelijke pod met de secrets uit het cluster (ontwikkelen
  zonder Flux). `ACM_NODE=worker2` kiest de node. Niet in de Open Zaak-webpod: die
  zit met uwsgi al tegen zijn geheugenlimiet en de kernel killt dan het script.
- `test-public.sh` — stap 6 via `https://openzaak.haven.3n.nl` met curl, zoals
  in de demo: 200 op CM/SO, `count: 0` op VORDERING, 403 op de vorderingszaak.

Let op de URL's: Open Zaak bouwt ze uit de request-host en autorisaties matchen
op zaaktype-URL. Het script praat in-cluster maar stuurt `Host:
openzaak.haven.3n.nl` + `X-Forwarded-Proto: https`, zodat alles canoniek is en
ook de publieke host werkt. Een API-client die via `openzaak-nginx.openzaak.svc`
praat zónder die headers krijgt daarom 403.

## Valkuilen

- **Scopenamen.** Open Zaak 1.30 kent `zaken.statussen.toevoegen`; het oudere
  `zaken.statussen.zetten` wordt door het Autorisaties-API geaccepteerd maar matcht
  nergens op ("Met de 'zaken.aanmaken' scope mag je slechts 1 status zetten"). De
  geldige namen staan in `components/*/api/scopes.py` van het image.
- **Halve zaken.** Breekt een run af, dan blijven zaken zonder vervolgstatus achter
  en slaat het script ze de volgende keer over (bestaat → overslaan). Verwijder ze
  dan als `openzaak`-client (`DELETE` op de zaak) en draai opnieuw.

## Bewust niet (nu)

- **Open Zaak-admin als behandelaar-UI.** De admin filtert niet op
  Applicatie-autorisaties; wie daar inlogt (ook `behandelaar.demo`) ziet alle
  zaken. De demo-rollen leven in de API-clients; de admin is beheer (`rick`).
  Een echte behandelaar-UI (bv. Open Archiefbeheer/Open Inwoner-achtige apps)
  komt na de geheugenuitbreiding.
- Open Formulieren als loket (stap 1), Objecten API voor "Onderneming",
  besluit via Besluiten API bij de vordering — zie tabblad "Bewust niet".
- Nieuwe versies van gepubliceerde zaaktypen: wijzig je het ontwerp, verwijder
  dan de catalogus in de admin (of maak een nieuwe `identificatie`) en laat de
  Job opnieuw draaien.
