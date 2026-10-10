#!/usr/bin/env python3
"""
ACM-demo op Open Zaak: catalogus, zaaktypen, applicaties en demodata als
desired state. Idempotent: alles wat al bestaat wordt overgeslagen of
bijgewerkt, nooit dubbel aangemaakt.

Ontwerp: haven/docs/acm-demo-zaaktypencatalogus.xlsx (nixos_eigen_hardware1).

    acm.py catalogus     catalogus ACM + 3 zaaktypen (gepubliceerd)
    acm.py applicaties   loket / behandeling / forensisch met autorisaties
    acm.py demo [snelkoop|spoor|all]   demodata t/m de vorderingszaak (stap 1-5), per casus
    acm.py check         de 403-test (stap 6) + overzicht
    acm.py all           alles hierboven, in die volgorde

Omgeving:
    OPENZAAK_URL      basis-URL (default http://openzaak-nginx.openzaak.svc)
    OPENZAAK_SECRET   JWT-secret van client 'openzaak' (heeft_alle_autorisaties)
    LOKET_SECRET, BEHANDELING_SECRET, FORENSISCH_SECRET   voor demo en check
"""
import json
import os
import sys
import time
import datetime as dt

import jwt
import requests

BASE = os.environ.get("OPENZAAK_URL", "http://openzaak-nginx.openzaak.svc").rstrip("/")
# Open Zaak bouwt URL's uit de request-host. Autorisaties matchen op zaaktype-URL,
# dus alles moet de canonieke publieke host dragen, ook als we in-cluster praten.
HOST_HEADERS = {"Host": os.environ.get("OPENZAAK_HOST", "openzaak.haven.3n.nl"), "X-Forwarded-Proto": "https"} \
    if os.environ.get("OPENZAAK_HOST", "openzaak.haven.3n.nl") else {}
SELECTIELIJST = "https://selectielijst.openzaak.nl/api/v1/"
ZTC = f"{BASE}/catalogi/api/v1"
ZRC = f"{BASE}/zaken/api/v1"
DRC = f"{BASE}/documenten/api/v1"
AC = f"{BASE}/autorisaties/api/v1"
BRC = f"{BASE}/besluiten/api/v1"
RSIN = "000000000"                      # demo-RSIN (voldoet aan de 11-proef)
VANDAAG = dt.date.today().isoformat()
GELDIG = "2026-10-01"


# --------------------------------------------------------------------- client
class Client:
    def __init__(self, client_id, secret, user="acm-script", repr_="ACM desired-state script"):
        self.client_id, self.secret, self.user, self.repr = client_id, secret, user, repr_
        self.s = requests.Session()

    def token(self):
        claims = {"iss": self.client_id, "iat": int(time.time()), "client_id": self.client_id,
                  "user_id": self.user, "user_representation": self.repr}
        return jwt.encode(claims, self.secret, algorithm="HS256")

    def req(self, method, url, ok=(200, 201, 204), **kw):
        h = {"Authorization": f"Bearer {self.token()}", "Accept-Crs": "EPSG:4326",
             "Content-Crs": "EPSG:4326", **HOST_HEADERS}
        r = self.s.request(method, self._local(url), headers=h, timeout=60, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:800]}")
        return r.json() if r.content and r.status_code != 204 else None

    def get(self, url, **params):
        return self.req("GET", url, params=params)

    def list(self, url, **params):
        out, page = [], self.get(url, **params)
        if isinstance(page, list):      # niet elke lijst is gepagineerd (bv. zaakinformatieobjecten)
            return page
        out += page["results"]
        while page.get("next"):
            page = self.req("GET", page["next"])
            out += page["results"]
        return out

    def post(self, url, body):
        return self.req("POST", url, json=body)

    def patch(self, url, body):
        return self.req("PATCH", url, json=body)

    def put(self, url, body):
        return self.req("PUT", url, json=body)

    def status(self, method, url):
        """Alleen de statuscode (voor de 403-test)."""
        h = {"Authorization": f"Bearer {self.token()}", "Accept-Crs": "EPSG:4326", **HOST_HEADERS}
        return self.s.request(method, self._local(url), headers=h, timeout=60).status_code

    @staticmethod
    def _local(url):
        """Canonieke URL's (https://<host>/...) terugvertalen naar de basis-URL waarop we praten."""
        pub = f"https://{HOST_HEADERS['Host']}" if HOST_HEADERS else None
        return BASE + url[len(pub):] if pub and url.startswith(pub) else url


def admin():
    return Client("openzaak", os.environ["OPENZAAK_SECRET"])


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- selectielijst
_sl = {}


def sl_procestype(nummer):
    key = ("pt", nummer)
    if key not in _sl:
        for p in requests.get(SELECTIELIJST + "procestypen", params={"jaar": 2020}, timeout=30).json():
            _sl[("pt", p["nummer"])] = p["url"]
    return _sl[key]


def sl_resultaat(procestype_nummer, volledig_nummer):
    key = ("res", volledig_nummer)
    if key not in _sl:
        r = requests.get(SELECTIELIJST + "resultaten",
                         params={"procesType": sl_procestype(procestype_nummer), "pageSize": 100},
                         timeout=30).json()
        for x in r["results"]:
            _sl[("res", x["volledigNummer"])] = x
    return _sl[key]


def sl_rto(omschrijving):
    key = ("rto", omschrijving)
    if key not in _sl:
        for x in requests.get(SELECTIELIJST + "resultaattypeomschrijvingen", timeout=30).json():
            _sl[("rto", x["omschrijving"])] = x["url"]
    return _sl[key]


# --------------------------------------------------------------------- ontwerp
ZAAKTYPEN = {
    "CONSUMENTENMELDING": dict(
        omschrijving="Melding consument behandelen", procestype=21,
        doel="Consument individueel adviseren en de melding als signaal vastleggen",
        aanleiding="Melding van een consument over een bedrijf (webwinkel, energie, telecom)",
        intern_extern="extern", vertrouwelijkheid="zaakvertrouwelijk", doorlooptijd="P10D",
        verantwoordelijke="ACM / ConsuWijzer", onderwerp="Consumentenmelding",
        handeling_initiator="Melden", handeling_behandelaar="Behandelen",
        statustypen=[("Ontvangen", "Melding binnengekomen via loket; zaak aangemaakt", True),
                     ("In behandeling", "Behandelaar ConsuWijzer heeft de melding opgepakt", False),
                     ("Geadviseerd", "Consument heeft advies of doorverwijzing gekregen", True),
                     ("Afgehandeld", "Melding gesloten; signaal geregistreerd op de onderneming", True)],
        resultaattypen=[("Advies gegeven", "Afgehandeld", "21.1", "Consument individueel geholpen"),
                        ("Doorverwezen", "Afgehandeld", "21.1", "Naar geschillencommissie, rechter of andere toezichthouder"),
                        ("Signaal geregistreerd", "Afgehandeld", "21.1", "Geen individueel advies, wel vastgelegd als signaal"),
                        ("Niet bevoegd", "Afgebroken", "21.3", "Valt buiten het toezichtdomein van de ACM")],
        roltypen=[("Melder", "initiator"), ("Behandelaar loket", "behandelaar"),
                  ("Betrokken onderneming", "belanghebbende")],
        iot=[("Melding", "zaakvertrouwelijk", "inkomend"),
             ("Bewijsstuk consument", "zaakvertrouwelijk", "inkomend"),
             ("Adviesbrief", "zaakvertrouwelijk", "uitgaand")],
        eigenschappen=[("Sector", "tekst", ["energie", "telecom", "online kopen", "reizen", "financieel", "overig"]),
                       ("Onderwerp", "tekst", ["misleiding", "niet geleverd", "opzegging", "kosten", "overig"]),
                       ("Onderneming (KvK)", "getal", []),
                       ("Kanaal", "tekst", ["web", "telefoon", "brief"])],
    ),
    "SIGNAALONDERZOEK": dict(
        omschrijving="Signaal beoordelen", procestype=12,
        doel="Beoordelen of signalen over een onderneming aanleiding geven tot onderzoek of maatregel",
        aanleiding="Eén of meer consumentenmeldingen, eigen waarneming of tip",
        intern_extern="intern", vertrouwelijkheid="zaakvertrouwelijk", doorlooptijd="P90D",
        verantwoordelijke="ACM / directie Consumenten", onderwerp="Signaalonderzoek",
        handeling_initiator="Signaleren", handeling_behandelaar="Onderzoeken",
        statustypen=[("Signaal ontvangen", "Onderzoekszaak aangemaakt (bv. bij derde melding over dezelfde onderneming)", False),
                     ("Triage", "Beoordeling ernst, bevoegdheid en prioriteit", False),
                     ("In onderzoek", "Onderzoek loopt; eventueel vordering uitgezet bij forensisch", False),
                     ("Afgerond", "Besluit over vervolg genomen", False)],
        resultaattypen=[("Geen vervolg", "Afgehandeld", "12.1", "Signalen onvoldoende voor maatregel"),
                        ("Waarschuwing", "Afgehandeld", "12.1.10", "Informele interventie richting onderneming"),
                        ("Formeel onderzoek", "Afgehandeld", "12.2.2", "Overdracht naar formeel handhavingstraject (buiten demo)")],
        roltypen=[("Toezichthouder", "beslisser"), ("Behandelaar onderzoek", "behandelaar"),
                  ("Betrokken onderneming", "belanghebbende")],
        iot=[("Beoordelingsnotitie", "zaakvertrouwelijk", "intern"),
             ("Vrijgegeven bewijsstuk", "zaakvertrouwelijk", "intern"),
             ("Verzoek tot vordering", "zaakvertrouwelijk", "intern")],
        eigenschappen=[("Onderneming (KvK)", "getal", []),
                       ("Aantal meldingen", "getal", []),
                       ("Prioriteit", "tekst", ["laag", "midden", "hoog"])],
    ),
    "VORDERING": dict(
        omschrijving="Gegevens vorderen bij leverancier", procestype=12,
        doel="Rechtmatig verkrijgen, veiligstellen en filteren van gegevens van een leverancier ten behoeve van een onderzoek",
        aanleiding="Verzoek vanuit een signaalonderzoek; wettelijke bevoegdheid forensisch team",
        intern_extern="intern", vertrouwelijkheid="geheim", doorlooptijd="P30D",
        verantwoordelijke="ACM / forensisch team", onderwerp="Vordering",
        handeling_initiator="Verzoeken", handeling_behandelaar="Vorderen",
        statustypen=[("Vordering verzonden", "Formele vordering aan de leverancier", False),
                     ("Data ontvangen", "Ruwe dataset ontvangen en veiliggesteld (geheim)", False),
                     ("Gefilterd", "Selectie op relevantie en rechtmatigheid; filterverantwoording vastgelegd", False),
                     ("Vrijgegeven", "Gefilterde set als 'Vrijgegeven bewijsstuk' gekoppeld aan de onderzoekszaak", False)],
        resultaattypen=[("Vrijgegeven", "Afgehandeld", "12.1.8", "Gefilterde set overgedragen; ruwe data na termijn vernietigen"),
                        ("Ingetrokken", "Afgebroken", "12.3", "Vordering niet doorgezet of niet rechtmatig gebleken; ruwe data vernietigen")],
        roltypen=[("Forensisch onderzoeker", "behandelaar"), ("Leverancier", "belanghebbende"),
                  ("Aanvrager", "adviseur")],
        iot=[("Vordering", "geheim", "uitgaand"),
             ("Ruwe dataset", "geheim", "inkomend"),
             ("Filterverantwoording", "geheim", "intern"),
             ("Vrijgaveverklaring", "vertrouwelijk", "intern")],
        eigenschappen=[("Leverancier", "tekst", []),
                       ("Grondslag", "tekst", []),
                       ("Periode", "tekst", []),
                       ("Filterstatus", "tekst", ["ongefilterd", "gefilterd", "vrijgegeven"])],
    ),
}

RELATIES = [  # (van, naar, aard, toelichting)
    ("CONSUMENTENMELDING", "SIGNAALONDERZOEK", "bijdrage", "Meldingen voeden een signaalonderzoek"),
    ("SIGNAALONDERZOEK", "VORDERING", "bijdrage", "Onderzoek vraagt forensisch om een vordering"),
    ("VORDERING", "SIGNAALONDERZOEK", "bijdrage", "Vrijgegeven bewijsstuk gaat naar het onderzoek"),
]

ZRC_ALL = ["zaken.lezen", "zaken.aanmaken", "zaken.bijwerken", "zaken.statussen.zetten"]
DRC_ALL = ["documenten.lezen", "documenten.aanmaken", "documenten.bijwerken"]
APPLICATIES = {  # client_id: (label, [(zaaktype, zrc-scopes, drc-scopes, max vertrouwelijkheid)])
    "loket": ("ACM loket (ConsuWijzer, Open Formulieren)", [
        ("CONSUMENTENMELDING", ZRC_ALL, ["documenten.lezen", "documenten.aanmaken"], "zaakvertrouwelijk"),
        ("SIGNAALONDERZOEK", ["zaken.lezen"], [], "intern"),
    ]),
    "behandeling": ("ACM zaakbehandeling en toezicht", [
        ("SIGNAALONDERZOEK", ZRC_ALL, ["documenten.lezen", "documenten.aanmaken"], "zaakvertrouwelijk"),
        ("CONSUMENTENMELDING", ["zaken.lezen"], ["documenten.lezen"], "zaakvertrouwelijk"),
        # VORDERING: bewust géén autorisatie -> 403. Dit is de demo.
    ]),
    "forensisch": ("ACM forensisch team", [
        ("VORDERING", ZRC_ALL, DRC_ALL, "geheim"),
        ("SIGNAALONDERZOEK", ["zaken.lezen", "zaken.bijwerken"], ["documenten.lezen", "documenten.aanmaken"], "zaakvertrouwelijk"),
    ]),
}


# -------------------------------------------------------------------- catalogus
def catalogus(c):
    cats = c.list(f"{ZTC}/catalogussen", domein="ACM", rsin=RSIN)
    if cats:
        cat = cats[0]
        log(f"catalogus ACM bestaat: {cat['url']}")
    else:
        cat = c.post(f"{ZTC}/catalogussen", {
            "domein": "ACM", "rsin": RSIN, "naam": "ACM (demo Haven-lab)",
            "contactpersoonBeheerNaam": "Rick (3n)", "begindatumVersie": GELDIG, "versie": "1"})
        log(f"catalogus ACM aangemaakt: {cat['url']}")
    return cat


def informatieobjecttypen(c, cat):
    """Alle IOT's uit het ontwerp; één per omschrijving in de catalogus."""
    bestaand = {i["omschrijving"]: i for i in c.list(f"{ZTC}/informatieobjecttypen", catalogus=cat["url"])}
    out = {}
    for zt in ZAAKTYPEN.values():
        for oms, vertr, _ in zt["iot"]:
            if oms in bestaand:
                out[oms] = bestaand[oms]
                continue
            out[oms] = bestaand[oms] = c.post(f"{ZTC}/informatieobjecttypen", {
                "catalogus": cat["url"], "omschrijving": oms, "vertrouwelijkheidaanduiding": vertr,
                "beginGeldigheid": GELDIG, "informatieobjectcategorie": "ACM-demo"})
            log(f"  informatieobjecttype {oms} ({vertr}) aangemaakt")
    return out


def zaaktype(c, cat, ident, spec, iots):
    zts = c.list(f"{ZTC}/zaaktypen", catalogus=cat["url"], identificatie=ident)
    if zts:
        log(f"zaaktype {ident} bestaat ({'concept' if zts[0]['concept'] else 'gepubliceerd'})")
        return zts[0], False
    zt = c.post(f"{ZTC}/zaaktypen", {
        "identificatie": ident, "omschrijving": spec["omschrijving"],
        "vertrouwelijkheidaanduiding": spec["vertrouwelijkheid"], "doel": spec["doel"],
        "aanleiding": spec["aanleiding"], "indicatieInternOfExtern": spec["intern_extern"],
        "handelingInitiator": spec["handeling_initiator"], "onderwerp": spec["onderwerp"],
        "handelingBehandelaar": spec["handeling_behandelaar"], "doorlooptijd": spec["doorlooptijd"],
        "opschortingEnAanhoudingMogelijk": False, "verlengingMogelijk": False,
        "publicatieIndicatie": False, "productenOfDiensten": [],
        "selectielijstProcestype": sl_procestype(spec["procestype"]),
        "referentieproces": {"naam": spec["omschrijving"]},
        "verantwoordelijke": spec["verantwoordelijke"],
        "beginGeldigheid": GELDIG, "versiedatum": GELDIG,
        "catalogus": cat["url"], "besluittypen": [], "gerelateerdeZaaktypen": [],
    })
    log(f"zaaktype {ident} aangemaakt")
    for i, (oms, tekst, informeren) in enumerate(spec["statustypen"], 1):
        c.post(f"{ZTC}/statustypen", {"zaaktype": zt["url"], "omschrijving": oms, "volgnummer": i,
                                      "informeren": informeren, "statustekst": tekst})
    for oms, generiek, klasse, toelichting in spec["resultaattypen"]:
        res = sl_resultaat(spec["procestype"], klasse)
        body = {"zaaktype": zt["url"], "omschrijving": oms, "resultaattypeomschrijving": sl_rto(generiek),
                "selectielijstklasse": res["url"], "toelichting": toelichting,
                "archiefnominatie": res["waardering"]}
        if res["waardering"] == "vernietigen":
            body["archiefactietermijn"] = res["bewaartermijn"]
            body["brondatumArchiefprocedure"] = {"afleidingswijze": "afgehandeld", "datumkenmerk": "",
                                                "einddatumBekend": False, "objecttype": "",
                                                "registratie": "", "procestermijn": None}
        c.post(f"{ZTC}/resultaattypen", body)
    for oms, generiek in spec["roltypen"]:
        c.post(f"{ZTC}/roltypen", {"zaaktype": zt["url"], "omschrijving": oms, "omschrijvingGeneriek": generiek})
    for i, (oms, _, richting) in enumerate(spec["iot"], 1):
        c.post(f"{ZTC}/zaaktype-informatieobjecttypen", {
            "zaaktype": zt["url"], "informatieobjecttype": iots[oms]["url"], "volgnummer": i, "richting": richting})
    for naam, formaat, waarden in spec["eigenschappen"]:
        c.post(f"{ZTC}/eigenschappen", {
            "zaaktype": zt["url"], "naam": naam, "definitie": naam,
            "specificatie": {"groep": "ACM", "formaat": formaat, "lengte": "20" if formaat == "getal" else "40",
                             "kardinaliteit": "1", "waardenverzameling": waarden}})
    log(f"  {len(spec['statustypen'])} statustypen, {len(spec['resultaattypen'])} resultaattypen, "
        f"{len(spec['roltypen'])} roltypen, {len(spec['iot'])} documenttypen, {len(spec['eigenschappen'])} eigenschappen")
    return zt, True


def cmd_catalogus():
    c = admin()
    cat = catalogus(c)
    iots = informatieobjecttypen(c, cat)
    zts, nieuw = {}, set()
    for ident, spec in ZAAKTYPEN.items():
        zts[ident], is_nieuw = zaaktype(c, cat, ident, spec, iots)
        if is_nieuw:
            nieuw.add(ident)
    # Relaties tussen zaaktypen (alleen op concepten te zetten)
    for van, naar, aard, toelichting in RELATIES:
        if van in nieuw:
            rel = [r for r in zts[van].get("gerelateerdeZaaktypen", [])] + [
                {"zaaktype": zts[naar]["url"], "aardRelatie": aard, "toelichting": toelichting}]
            zts[van] = c.patch(zts[van]["url"], {"gerelateerdeZaaktypen": rel})
    # Publiceren: eerst documenttypen, dan zaaktypen
    for oms, iot in iots.items():
        if iot["concept"]:
            c.post(iot["url"] + "/publish", {})
    for ident, zt in zts.items():
        if zt["concept"]:
            c.post(zt["url"] + "/publish", {})
            log(f"zaaktype {ident} gepubliceerd")
    return cat, zts, iots


# ------------------------------------------------------------------ applicaties
def cmd_applicaties():
    c = admin()
    cat = catalogus(c)
    zts = {z["identificatie"]: z for z in c.list(f"{ZTC}/zaaktypen", catalogus=cat["url"])}
    iots = {i["omschrijving"]: i for i in c.list(f"{ZTC}/informatieobjecttypen", catalogus=cat["url"])}
    bestaand = {a["clientIds"][0]: a for a in c.list(f"{AC}/applicaties") if a["clientIds"]}
    for client_id, (label, regels) in APPLICATIES.items():
        auts = []
        for ident, zrc, drc, maxv in regels:
            if zrc:
                auts.append({"component": "zrc", "scopes": zrc, "zaaktype": zts[ident]["url"],
                             "maxVertrouwelijkheidaanduiding": maxv})
            if drc:
                for oms, _, _ in ZAAKTYPEN[ident]["iot"]:
                    auts.append({"component": "drc", "scopes": drc, "informatieobjecttype": iots[oms]["url"],
                                 "maxVertrouwelijkheidaanduiding": maxv})
        body = {"clientIds": [client_id], "label": label, "heeftAlleAutorisaties": False, "autorisaties": auts}
        if client_id in bestaand:
            c.put(bestaand[client_id]["url"], body)
            log(f"applicatie {client_id}: bijgewerkt ({len(auts)} autorisaties)")
        else:
            c.post(f"{AC}/applicaties", body)
            log(f"applicatie {client_id}: aangemaakt ({len(auts)} autorisaties)")


# ------------------------------------------------------------------------ demo
# Twee casussen. Identificaties: snelkoop = CM-2026-00xx / SO-2026-0001 / V-2026-0001,
# spoor = CM-2026-01xx / SO-2026-0002 / V-2026-0002.
#
# Alle inhoud is demomateriaal. De spoorcasus is een reconstructie uit
# persberichten van 10 oktober 2026 (Treinenweb, Oost, Treinreiziger) over de
# gestrande GoVolta-trein bij Bad Bentheim en het verzoek van GoVolta aan de ACM;
# de "bevindingen", vorderingen en datasets zijn verzonnen en stellen niets vast.
#
# Onderneming: (naam, KvK-nummer, RSIN) — RSIN moet aan de 11-proef voldoen.
CASUSSEN = {
    "snelkoop": dict(
        nr=1, prefix="CM-2026-00",
        onderneming=("SnelKoop B.V.", "12345678", "823456705"),
        ruis=("BelVast Telecom", "87654321", "857654329"),
        leverancier=("PayFlow Payments B.V.", "855555555"),
        # (omschrijving, sector, onderwerp, kanaal, toelichting, over_ruis, resultaat)
        meldingen=[
            ("Bestelling niet geleverd, geen reactie op mails", "online kopen", "niet geleverd", "web",
             "Consument bestelde een laptop op 12 september; niets ontvangen, klantenservice onbereikbaar.", False, "Signaal geregistreerd"),
            ("Geld terug beloofd, nooit ontvangen", "online kopen", "niet geleverd", "telefoon",
             "Retour geaccepteerd, terugbetaling na 6 weken nog niet gedaan.", False, "Signaal geregistreerd"),
            ("Reviews lijken nep, levertijd klopt niet", "online kopen", "misleiding", "web",
             "'Op voorraad, morgen in huis' maar levering na 5 weken; alleen 5-sterrenreviews zonder tekst.", False, "Signaal geregistreerd"),
            ("Abonnement kon niet opgezegd worden", "telecom", "opzegging", "web",
             "Ander bedrijf; dient als 'ruis' in de demo.", True, None),
            ("Verborgen kosten bij het afrekenen", "online kopen", "kosten", "web",
             "Servicekosten verschenen pas in de laatste stap van de bestelling.", False, None),
        ],
        bewijsstuk=("Screenshot bestelling", "Screenshot orderbevestiging (demo-placeholder)."),
        advies="Geachte heer/mevrouw, wij adviseren u ... (demo).",
        so_oms="Signaal SnelKoop B.V.: leveringen en terugbetalingen",
        so_toel="Aangemaakt na de derde consumentenmelding over dezelfde onderneming.",
        beoordeling="Vier meldingen in drie weken over niet-levering en uitblijvende terugbetaling. Patroon wijst op structurele niet-nakoming. Prioriteit hoog.",
        verzoek=("Verzoek tot vordering transactiegegevens betaalplatform",
                 "Verzoek aan forensisch: transactie- en uitbetalingsgegevens van SnelKoop B.V. bij het betaalplatform over 1 augustus - 30 september 2026, om omvang en patroon vast te stellen."),
        v_oms="Vordering transactiegegevens betaalplatform (SnelKoop B.V.)",
        periode="2026-08-01 t/m 2026-09-30",
        vordering=("Vordering PayFlow 2026-0001",
                   "Hierbij vorderen wij op grond van ... alle transactiegegevens van SnelKoop B.V. over de periode ... (demo)."),
        ruwe=("Ruwe dataset PayFlow export",
              "order_id;datum;bedrag;klant_email;iban\n1001;2026-08-02;499,00;consument1@example.org;NL00DEMO0000000001\n(... 2.000 regels, ongefilterd, bevat gegevens van niet-betrokken klanten ...)"),
    ),
    "spoor": dict(
        nr=2, prefix="CM-2026-01",
        onderneming=("NS Reizigers B.V.", "32116727", "801059264"),       # KvK/RSIN: demo-waarden (11-proef), niet geverifieerd
        ruis=("GoVolta B.V.", "91234567", "863456789"),                   # de vervoerder van de reizigers zelf
        leverancier=("ProRail B.V. (verkeersleiding)", "801478637"),
        meldingen=[
            ("Zeven uur vast in de trein bij Bad Bentheim, geen informatie", "reizen", "overig", "web",
             "Reiziger GoVolta Amsterdam-Berlijn, 9 oktober. Anderhalf uur bij Oldenzaal, daarna zeven uur bij Bad Bentheim. Geen water, geen omroepberichten.", True, "Doorverwezen"),
            ("ICE reed door, wij mochten niet mee", "reizen", "overig", "telefoon",
             "De ICE van NS/DB die later passeerde nam gestrande reizigers niet mee. Melder wil weten of dat mag.", False, "Signaal geregistreerd"),
            ("Compensatie na 7 uur vertraging: wie betaalt?", "reizen", "kosten", "web",
             "Gemiste overnachting in Berlijn (€140). GoVolta verwijst naar NS, NS naar GoVolta.", True, "Doorverwezen"),
            ("NS-loket Hengelo: 'niet onze reizigers'", "reizen", "overig", "brief",
             "Melder stond met kinderen op het perron; NS-personeel weigerde hulp omdat het ticket van GoVolta was.", False, "Signaal geregistreerd"),
            ("Bussen pas na middernacht", "reizen", "overig", "web",
             "GoVolta zette zelf bussen in, maar pas na zeven uur. Melder vraagt of de ACM hier iets mee doet.", True, "Doorverwezen"),
        ],
        bewijsstuk=("Foto vertrekbord Bad Bentheim", "Foto van het vertrekbord met 'Verspätung unbestimmt' (demo-placeholder)."),
        advies="Geachte heer/mevrouw, voor compensatie bij vertraging op het spoor (Verordening (EU) 2021/782) verwijzen wij u naar de vervoerder en, bij een geschil, naar de Geschillencommissie Openbaar Vervoer; toezicht daarop ligt bij de ILT. Uw melding is als signaal geregistreerd (demo).",
        so_oms="Signaal spoor: benadeling nieuwkomer GoVolta door NS/DB (Bad Bentheim, 9 oktober)",
        so_toel="Aangemaakt na vijf reizigersmeldingen plus het verzoek van GoVolta aan de ACM om te onderzoeken of gevestigde vervoerders nieuwkomers bewust benadelen (Treinenweb, 10 oktober 2026). Reconstructie, demo.",
        beoordeling="Twee bronnen: vijf reizigersmeldingen (deels doorverwezen naar ILT/Geschillencommissie — reizigersrechten zijn geen ACM-bevoegdheid) en het verzoek van GoVolta. Kernvraag: is het niet-terugzetten van de defecte ICE en het niet-meenemen van reizigers (AJC) gedrag dat een nieuwkomer op het spoor benadeelt? Betrokken: NS, DB, ProRail, DB InfraGO. Prioriteit hoog; grensoverschrijdend (Bundesnetzagentur).",
        verzoek=("Verzoek tot vordering verkeersleidingslogs 9 oktober",
                 "Verzoek aan forensisch: logboeken van de verkeersleiding (ProRail, Oldenzaal-grens) en de communicatie NS-DB-ProRail van 9 oktober 17:00-01:00 over de defecte ICE, het verzoek om 50 meter terug te zetten en de AJC-afstemming. Alleen de tijdlijn van dat traject is relevant voor het onderzoek."),
        v_oms="Vordering verkeersleidingslogs ProRail (Oldenzaal-Bad Bentheim, 9 oktober)",
        periode="2026-10-09 17:00 t/m 2026-10-10 01:00",
        vordering=("Vordering ProRail 2026-0002",
                   "Hierbij vorderen wij op grond van ... de logboeken van de verkeersleiding en de bijbehorende communicatie over het baanvak Oldenzaal-grens op 9 oktober 2026 (demo). Gegevens van DB InfraGO lopen via de Bundesnetzagentur."),
        ruwe=("Ruwe dataset verkeersleiding 9 oktober",
              "tijd;treinnr;vervoerder;baanvak;melding;dienstdoende\n17:42;ICE 141;DB;Oldenzaal-grens;stroomafnemer defect, stilstand;VL-7 (naam);\n17:55;GV 1203;GoVolta;Oldenzaal;wacht op vrijgave;VL-7 (naam)\n(... 1.400 regels over ALLE treinen van die avond, incl. namen en dienstroosters van verkeersleiders en machinisten ...)"),
    ),
}


def zt_onderdelen(c, zt_url):
    st = {s["omschrijving"]: s for s in c.list(f"{ZTC}/statustypen", zaaktype=zt_url)}
    rt = {r["omschrijving"]: r for r in c.list(f"{ZTC}/roltypen", zaaktype=zt_url)}
    res = {r["omschrijving"]: r for r in c.list(f"{ZTC}/resultaattypen", zaaktype=zt_url)}
    eig = {e["naam"]: e for e in c.list(f"{ZTC}/eigenschappen", zaaktype=zt_url)}
    return st, rt, res, eig


def zaak(c, zt, ident, omschrijving, toelichting, vertrouwelijkheid=None, startdatum=None):
    bestaand = c.list(f"{ZRC}/zaken", identificatie=ident, bronorganisatie=RSIN)
    if bestaand:
        return bestaand[0], False
    body = {"identificatie": ident, "bronorganisatie": RSIN, "omschrijving": omschrijving[:80],
            "toelichting": toelichting, "zaaktype": zt["url"], "verantwoordelijkeOrganisatie": RSIN,
            "startdatum": startdatum or VANDAAG, "registratiedatum": VANDAAG}
    if vertrouwelijkheid:
        body["vertrouwelijkheidaanduiding"] = vertrouwelijkheid
    return c.post(f"{ZRC}/zaken", body), True


_klok = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)


def status(c, z, st, oms, tijd=None):
    """Statussen moeten in de tijd oplopen; zonder expliciete tijd tikt een klok mee."""
    global _klok
    if tijd is None:
        _klok += dt.timedelta(minutes=1)
        tijd = _klok.isoformat()
    c.post(f"{ZRC}/statussen", {"zaak": z["url"], "statustype": st[oms]["url"], "datumStatusGezet": tijd})


def elfproef(prefix, lengte=9):
    """Vul een getal aan tot een geldig BSN/RSIN (11-proef) — demo-identificaties."""
    for rest in range(10 ** (lengte - len(prefix))):
        n = prefix + str(rest).zfill(lengte - len(prefix))
        if sum(int(d) * (lengte - i) for i, d in enumerate(n[:-1])) % 11 == int(n[-1]):
            return n
    raise ValueError(prefix)


def rol(c, z, rt, oms, betrokkene_type, identificatie, naam=None):
    body = {"zaak": z["url"], "roltype": rt[oms]["url"], "betrokkeneType": betrokkene_type,
            "roltoelichting": oms}
    if betrokkene_type == "natuurlijk_persoon":
        body["betrokkeneIdentificatie"] = {"inpBsn": identificatie, "geslachtsnaam": naam or ""}
    elif betrokkene_type == "niet_natuurlijk_persoon":
        body["betrokkeneIdentificatie"] = {"innNnpId": identificatie, "statutaireNaam": naam or ""}
    else:  # medewerker
        body["betrokkeneIdentificatie"] = {"identificatie": identificatie, "achternaam": naam or identificatie}
    c.post(f"{ZRC}/rollen", body)


def eigenschap(c, z, eig, naam, waarde):
    c.post(f"{z['url']}/zaakeigenschappen", {"zaak": z["url"], "eigenschap": eig[naam]["url"], "waarde": str(waarde)})


def document(c, z, iots, oms, titel, inhoud, vertrouwelijkheid, auteur, bron=RSIN, ontvangstdatum=None):
    import base64
    doc = c.post(f"{DRC}/enkelvoudiginformatieobjecten", {
        "bronorganisatie": bron, "creatiedatum": VANDAAG, "titel": titel, "auteur": auteur,
        "taal": "nld", "formaat": "text/plain", "inhoud": base64.b64encode(inhoud.encode()).decode(),
        "bestandsnaam": titel.lower().replace(" ", "-")[:40] + ".txt", "bestandsomvang": len(inhoud.encode()),
        "informatieobjecttype": iots[oms]["url"], "vertrouwelijkheidaanduiding": vertrouwelijkheid,
        "indicatieGebruiksrecht": False, **({"ontvangstdatum": ontvangstdatum} if ontvangstdatum else {})})
    c.post(f"{ZRC}/zaakinformatieobjecten", {"zaak": z["url"], "informatieobject": doc["url"], "titel": titel})
    return doc


def demo_casus(naam, c, zts, iots, loket, beh, forensisch):
    k = CASUSSEN[naam]
    nr, ond, ruis, lev = k["nr"], k["onderneming"], k["ruis"], k["leverancier"]

    # Stap 1-2: consumentenmeldingen
    st, rt, res, eig = zt_onderdelen(c, zts["CONSUMENTENMELDING"]["url"])
    meldingen = []
    for i, (oms, sector, onderwerp, kanaal, toelichting, over_ruis, resultaat) in enumerate(k["meldingen"], 1):
        ident = f"{k['prefix']}{i:02d}"
        bedrijf = ruis if over_ruis else ond
        dagen = 20 - 3 * i if naam == "snelkoop" else 1
        z, nieuw = zaak(loket, zts["CONSUMENTENMELDING"], ident, oms, toelichting,
                        startdatum=(dt.date.today() - dt.timedelta(days=dagen)).isoformat())
        if nieuw:
            status(loket, z, st, "Ontvangen", (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dagen, hours=3)).isoformat())
            rol(loket, z, rt, "Melder", "natuurlijk_persoon", elfproef(f"9999{nr}{i:02d}"), f"Consument {nr}.{i}")
            rol(loket, z, rt, "Betrokken onderneming", "niet_natuurlijk_persoon", bedrijf[2], bedrijf[0])
            rol(loket, z, rt, "Behandelaar loket", "medewerker", "loket.demo", "Loket")
            for n_, w_ in (("Sector", sector), ("Onderwerp", onderwerp), ("Kanaal", kanaal), ("Onderneming (KvK)", bedrijf[1])):
                eigenschap(loket, z, eig, n_, w_)
            document(loket, z, iots, "Melding", f"Melding {ident}", f"{oms}\n\n{toelichting}\n\nOnderneming: {bedrijf[0]} (KvK {bedrijf[1]})",
                     "zaakvertrouwelijk", f"Consument {nr}.{i}")
            if resultaat:
                document(loket, z, iots, "Bewijsstuk consument", f"{k['bewijsstuk'][0]} {ident}", k["bewijsstuk"][1],
                         "zaakvertrouwelijk", f"Consument {nr}.{i}")
                status(loket, z, st, "In behandeling")
                status(loket, z, st, "Geadviseerd")
                document(loket, z, iots, "Adviesbrief", f"Adviesbrief {ident}", k["advies"], "zaakvertrouwelijk", "ConsuWijzer")
                loket.post(f"{ZRC}/resultaten", {"zaak": z["url"], "resultaattype": res[resultaat]["url"],
                                                 "toelichting": resultaat})
                status(loket, z, st, "Afgehandeld")
            log(f"melding {ident} aangemaakt ({bedrijf[0]}{', ' + resultaat if resultaat else ''})")
        meldingen.append(z)

    # Stap 3-4: signaalonderzoek met de meldingen eraan, triage, verzoek tot vordering
    so_id, v_id = f"SO-2026-{nr:04d}", f"V-2026-{nr:04d}"
    st, rt, res, eig = zt_onderdelen(c, zts["SIGNAALONDERZOEK"]["url"])
    so, nieuw = zaak(beh, zts["SIGNAALONDERZOEK"], so_id, k["so_oms"], k["so_toel"])
    if nieuw:
        status(beh, so, st, "Signaal ontvangen")
        rol(beh, so, rt, "Behandelaar onderzoek", "medewerker", "behandelaar.demo", "Behandelaar")
        rol(beh, so, rt, "Toezichthouder", "medewerker", "toezicht.demo", "Toezicht")
        rol(beh, so, rt, "Betrokken onderneming", "niet_natuurlijk_persoon", ond[2], ond[0])
        beh.patch(so["url"], {"relevanteAndereZaken": [{"url": z["url"], "aardRelatie": "bijdrage"} for z in meldingen]})
        for n_, w_ in (("Onderneming (KvK)", ond[1]), ("Aantal meldingen", len(meldingen)), ("Prioriteit", "hoog")):
            eigenschap(beh, so, eig, n_, w_)
        status(beh, so, st, "Triage")
        document(beh, so, iots, "Beoordelingsnotitie", f"Beoordelingsnotitie {so_id}", k["beoordeling"], "zaakvertrouwelijk", "behandelaar.demo")
        document(beh, so, iots, "Verzoek tot vordering", k["verzoek"][0], k["verzoek"][1], "zaakvertrouwelijk", "behandelaar.demo")
        status(beh, so, st, "In onderzoek")
        log(f"signaalonderzoek {so_id} aangemaakt met {len(meldingen)} gekoppelde meldingen, triage en verzoek tot vordering")

    # Stap 5: forensisch maakt de vorderingszaak (geheim) met vordering en ruwe dataset
    st, rt, res, eig = zt_onderdelen(c, zts["VORDERING"]["url"])
    v, nieuw = zaak(forensisch, zts["VORDERING"], v_id, k["v_oms"],
                    f"Op verzoek van {so_id}. Ruwe data blijft in deze zaak.", vertrouwelijkheid="geheim")
    if nieuw:
        rol(forensisch, v, rt, "Forensisch onderzoeker", "medewerker", "forensisch.demo", "Forensisch")
        rol(forensisch, v, rt, "Aanvrager", "medewerker", "behandelaar.demo", "Behandelaar")
        rol(forensisch, v, rt, "Leverancier", "niet_natuurlijk_persoon", lev[1], lev[0])
        forensisch.patch(v["url"], {"relevanteAndereZaken": [{"url": so["url"], "aardRelatie": "bijdrage"}]})
        for n_, w_ in (("Leverancier", lev[0]), ("Grondslag", "artikel 6b Instellingswet ACM (demo)"),
                       ("Periode", k["periode"]), ("Filterstatus", "ongefilterd")):
            eigenschap(forensisch, v, eig, n_, w_)
        status(forensisch, v, st, "Vordering verzonden")
        document(forensisch, v, iots, "Vordering", k["vordering"][0], k["vordering"][1], "geheim", "forensisch.demo")
        document(forensisch, v, iots, "Ruwe dataset", k["ruwe"][0], k["ruwe"][1], "geheim", lev[0], bron=RSIN, ontvangstdatum=VANDAAG)
        status(forensisch, v, st, "Data ontvangen")
        log(f"vorderingszaak {v_id} aangemaakt (geheim) met vordering en ruwe dataset")


def cmd_demo(welke="all"):
    # De demodata wordt aangemaakt mét de bijbehorende demo-client, zodat de
    # audittrail laat zien wie wat deed. Ontbreekt een secret, dan via 'openzaak'.
    def cl(name, user, repr_):
        sec = os.environ.get(f"{name.upper()}_SECRET")
        return Client(name, sec, user, repr_) if sec else Client("openzaak", os.environ["OPENZAAK_SECRET"], user, repr_)

    c = admin()
    cat = catalogus(c)
    zts = {z["identificatie"]: z for z in c.list(f"{ZTC}/zaaktypen", catalogus=cat["url"])}
    iots = {i["omschrijving"]: i for i in c.list(f"{ZTC}/informatieobjecttypen", catalogus=cat["url"])}
    loket = cl("loket", "loket.demo", "Behandelaar loket (ConsuWijzer)")
    beh = cl("behandeling", "behandelaar.demo", "Behandelaar onderzoek")
    forensisch = cl("forensisch", "forensisch.demo", "Forensisch onderzoeker")
    for naam in (CASUSSEN if welke == "all" else [welke]):
        log(f"== casus {naam}")
        demo_casus(naam, c, zts, iots, loket, beh, forensisch)
    log("demo staat klaar t/m stap 5; stap 6 = 'acm.py check', stap 7-9 live in de demo")


# ----------------------------------------------------------------------- check
def cmd_check():
    c = admin()
    cat = catalogus(c)
    zts = {z["identificatie"]: z for z in c.list(f"{ZTC}/zaaktypen", catalogus=cat["url"])}
    log(f"zaaktypen: {', '.join(f'{k} ({'gepubliceerd' if not v['concept'] else 'concept'})' for k, v in zts.items())}")
    for client_id in ("loket", "behandeling", "forensisch"):
        apps = c.list(f"{AC}/applicaties", clientIds=client_id)
        log(f"applicatie {client_id}: {len(apps[0]['autorisaties']) if apps else 'ONTBREEKT'} autorisaties")
    sec = os.environ.get("BEHANDELING_SECRET")
    if not sec:
        log("BEHANDELING_SECRET ontbreekt; 403-test overgeslagen")
        return
    beh = Client("behandeling", sec, "behandelaar.demo", "Behandelaar onderzoek")
    tests = [("behandeling ziet vorderingszaken in de lijst", beh.get(f"{ZRC}/zaken", zaaktype=zts["VORDERING"]["url"])["count"], 0)]
    for k in CASUSSEN.values():
        so_id, v_id = f"SO-2026-{k['nr']:04d}", f"V-2026-{k['nr']:04d}"
        v = c.list(f"{ZRC}/zaken", identificatie=v_id, bronorganisatie=RSIN)
        so = c.list(f"{ZRC}/zaken", identificatie=so_id, bronorganisatie=RSIN)
        if not v or not so:
            log(f"demodata {so_id}/{v_id} ontbreekt; draai eerst 'acm.py demo'")
            continue
        ruwe = [z for z in c.list(f"{ZRC}/zaakinformatieobjecten", zaak=v[0]["url"]) if "Ruwe" in z["titel"]]
        tests += [(f"behandeling leest {so_id}", beh.status("GET", so[0]["url"]), 200),
                  (f"behandeling leest {v_id}", beh.status("GET", v[0]["url"]), 403),
                  (f"behandeling leest ruwe dataset {v_id}", beh.status("GET", ruwe[0]["informatieobject"]) if ruwe else None, 403)]
    ok = True
    for naam, code, verwacht in tests:
        goed = code == verwacht
        ok &= goed
        log(f"  {'OK ' if goed else 'FOUT'} {naam}: HTTP {code} (verwacht {verwacht})")
    log("scheiding forensisch/behandeling: " + ("technisch afgedwongen" if ok else "NIET in orde"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    arg = sys.argv[2] if len(sys.argv) > 2 else "all"       # demo [snelkoop|spoor|all]
    {"catalogus": lambda: cmd_catalogus(), "applicaties": cmd_applicaties, "demo": lambda: cmd_demo(arg), "check": cmd_check,
     "all": lambda: (cmd_catalogus(), cmd_applicaties(), cmd_demo(), cmd_check())}[cmd]()
