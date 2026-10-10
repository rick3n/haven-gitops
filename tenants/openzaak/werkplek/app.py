"""
ACM-werkplek — een kleine rollen-UI op de ZGW-API's van Open Zaak.

Inloggen via Keycloak (realm havenplus). De groep van de gebruiker bepaalt met
welke API-client (Applicatie) de werkplek praat:

    acm-loket        -> client 'loket'
    acm-behandelaar  -> client 'behandeling'
    acm-toezicht     -> client 'behandeling' (zelfde autorisaties, andere rol)
    acm-forensisch   -> client 'forensisch'
    k8s-admins       -> client 'openzaak' (alles; beheer)

De werkplek voegt géén eigen autorisatie toe: alles wat je ziet of niet ziet
komt uit de Autorisaties API van Open Zaak. Een 403 wordt dus gewoon getoond.
De JWT draagt user_id = Keycloak-gebruikersnaam, zodat de audittrail klopt.
"""
import base64
import datetime as dt
import html
import os
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

import jwt
import requests
from flask import Flask, abort, redirect, render_template_string, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET"]
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://werkplek.haven.3n.nl").rstrip("/")
OIDC_PUBLIC = os.environ.get("OIDC_ISSUER_PUBLIC", "https://keycloak.haven.3n.nl/realms/havenplus").rstrip("/")
OIDC_INTERNAL = os.environ.get("OIDC_ISSUER_INTERNAL", "http://keycloak-service.keycloak-instances:8080/realms/havenplus").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "werkplek")
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]

ZGW_BASE = os.environ.get("OPENZAAK_URL", "http://openzaak-nginx.openzaak.svc").rstrip("/")
ZGW_HOST = os.environ.get("OPENZAAK_HOST", "openzaak.haven.3n.nl")
ZGW_PUBLIC = f"https://{ZGW_HOST}"
RSIN = os.environ.get("RSIN", "000000000")

ROLES = {  # groep -> (client_id, env met secret, label)
    "acm-loket": ("loket", "LOKET_SECRET", "Loket (ConsuWijzer)"),
    "acm-behandelaar": ("behandeling", "BEHANDELING_SECRET", "Behandelaar onderzoek"),
    "acm-toezicht": ("behandeling", "BEHANDELING_SECRET", "Toezichthouder"),
    "acm-forensisch": ("forensisch", "FORENSISCH_SECRET", "Forensisch onderzoeker"),
    "k8s-admins": ("openzaak", "OPENZAAK_SECRET", "Beheer (alle autorisaties)"),
}
VERTR = ["openbaar", "beperkt_openbaar", "intern", "zaakvertrouwelijk", "vertrouwelijk", "confidentieel", "geheim", "zeer_geheim"]


# ----------------------------------------------------------------- ZGW-client
class Forbidden(Exception):
    def __init__(self, url, body):
        self.url, self.body = url, body


class ZGW:
    """Praat met Open Zaak als één Applicatie, namens één gebruiker."""

    def __init__(self, client_id, secret, user_id, user_repr):
        self.client_id, self.secret, self.user_id, self.user_repr = client_id, secret, user_id, user_repr

    def _token(self):
        return jwt.encode({"iss": self.client_id, "iat": int(time.time()), "client_id": self.client_id,
                           "user_id": self.user_id, "user_representation": self.user_repr}, self.secret, algorithm="HS256")

    def req(self, method, url, **kw):
        if url.startswith(ZGW_PUBLIC):
            url = ZGW_BASE + url[len(ZGW_PUBLIC):]
        h = {"Authorization": f"Bearer {self._token()}", "Accept-Crs": "EPSG:4326", "Content-Crs": "EPSG:4326",
             "Host": ZGW_HOST, "X-Forwarded-Proto": "https"}
        r = requests.request(method, url, headers=h, timeout=30, **kw)
        if r.status_code == 403:
            raise Forbidden(url, r.json() if r.content else {})
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content and r.status_code != 204 else None

    def get(self, url, **params):
        return self.req("GET", url, params=params)

    def list(self, url, **params):
        page = self.get(url, **params)
        if isinstance(page, list):
            return page
        out = list(page["results"])
        while page.get("next"):
            page = self.req("GET", page["next"])
            out += page["results"]
        return out

    def post(self, url, body):
        return self.req("POST", url, json=body)

    def patch(self, url, body):
        return self.req("PATCH", url, json=body)


def admin():
    """Alleen voor catalogusmetadata (namen van status-/resultaattypen enz.)."""
    return ZGW("openzaak", os.environ["OPENZAAK_SECRET"], "werkplek", "ACM-werkplek (catalogus)")


def me():
    r = session["role"]
    return ZGW(r["client"], os.environ[r["secret_env"]], session["user"], session["name"])


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


# ------------------------------------------------------------------------ OIDC
_disc = {}


def discovery():
    if not _disc:
        _disc.update(requests.get(OIDC_INTERNAL + "/.well-known/openid-configuration", timeout=20).json())
    return _disc


@app.route("/login")
def login():
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    session.clear()
    session.update(oidc_state=state, oidc_nonce=nonce, next=request.args.get("next", "/"))
    q = {"client_id": OIDC_CLIENT_ID, "response_type": "code", "scope": "openid profile email",
         "redirect_uri": PUBLIC_URL + "/callback", "state": state, "nonce": nonce}
    return redirect(f"{OIDC_PUBLIC}/protocol/openid-connect/auth?{urlencode(q)}")


@app.route("/callback")
def callback():
    if request.args.get("state") != session.get("oidc_state"):
        abort(400, "state klopt niet")
    d = discovery()
    tok = requests.post(d["token_endpoint"], data={
        "grant_type": "authorization_code", "code": request.args["code"], "redirect_uri": PUBLIC_URL + "/callback",
        "client_id": OIDC_CLIENT_ID, "client_secret": OIDC_CLIENT_SECRET}, timeout=20).json()
    if "id_token" not in tok:
        abort(401, f"geen id_token: {tok}")
    jwks = jwt.PyJWKClient(d["jwks_uri"])
    key = jwks.get_signing_key_from_jwt(tok["id_token"]).key
    claims = jwt.decode(tok["id_token"], key, algorithms=["RS256"], audience=OIDC_CLIENT_ID,
                        options={"verify_iss": False})  # issuer verschilt per host (backchannelDynamic)
    if claims.get("nonce") != session.get("oidc_nonce"):
        abort(400, "nonce klopt niet")
    groups = claims.get("groups", [])
    role = next((g for g in ("acm-forensisch", "acm-toezicht", "acm-behandelaar", "acm-loket", "k8s-admins") if g in groups), None)
    if not role:
        return render("<h2>Geen ACM-rol</h2><p>Gebruiker <b>{{u}}</b> zit in geen van de groepen acm-loket, acm-behandelaar, "
                      "acm-toezicht, acm-forensisch of k8s-admins.</p>", u=claims.get("preferred_username")), 403
    nxt = session.get("next", "/")
    session.clear()
    session.update(user=claims.get("preferred_username"), name=claims.get("name") or claims.get("preferred_username"),
                   groups=groups, role={"group": role, "client": ROLES[role][0], "secret_env": ROLES[role][1], "label": ROLES[role][2]})
    return redirect(nxt)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(f"{OIDC_PUBLIC}/protocol/openid-connect/logout?{urlencode({'client_id': OIDC_CLIENT_ID, 'post_logout_redirect_uri': PUBLIC_URL})}")


# -------------------------------------------------------------------- helpers
_cat = {"t": 0}


def catalogus():
    """Namen van zaaktypen en hun onderdelen; 10 minuten gecachet."""
    if time.time() - _cat["t"] > 600:
        a = admin()
        zts = {z["url"]: z for z in a.list(f"{ZGW_BASE}/catalogi/api/v1/zaaktypen") if not z["concept"]}
        for z in zts.values():
            z["_statustypen"] = sorted(a.list(f"{ZGW_BASE}/catalogi/api/v1/statustypen", zaaktype=z["url"]), key=lambda s: s["volgnummer"])
            z["_resultaattypen"] = a.list(f"{ZGW_BASE}/catalogi/api/v1/resultaattypen", zaaktype=z["url"])
            z["_roltypen"] = a.list(f"{ZGW_BASE}/catalogi/api/v1/roltypen", zaaktype=z["url"])
            z["_eigenschappen"] = a.list(f"{ZGW_BASE}/catalogi/api/v1/eigenschappen", zaaktype=z["url"])
            ziot = a.list(f"{ZGW_BASE}/catalogi/api/v1/zaaktype-informatieobjecttypen", zaaktype=z["url"])
            z["_iots"] = [a.get(x["informatieobjecttype"]) for x in ziot]
        names = {}
        for z in zts.values():
            for k in ("_statustypen", "_resultaattypen", "_roltypen", "_eigenschappen", "_iots"):
                for x in z[k]:
                    names[x["url"]] = x.get("omschrijving") or x.get("naam")
        _cat.update(t=time.time(), zts=zts, names=names)
    return _cat


def pub(url):
    return url.replace(ZGW_BASE, ZGW_PUBLIC) if url else url


def uuid_of(url):
    return url.rstrip("/").split("/")[-1]


def render(body, **ctx):
    ctx.setdefault("session", session)
    return render_template_string(LAYOUT.replace("%%BODY%%", body), **ctx)


@app.errorhandler(Forbidden)
def on_forbidden(e):
    return render(TPL_403, url=pub(e.url), body=e.body), 403


@app.errorhandler(RuntimeError)
def on_error(e):
    return render("<h2>Fout van de API</h2><pre>{{e}}</pre>", e=str(e)), 500


# ---------------------------------------------------------------------- views
@app.route("/")
@login_required
def home():
    c, cat = me(), catalogus()
    zaken = c.list(f"{ZGW_BASE}/zaken/api/v1/zaken", ordering="-identificatie", pageSize=100)
    per_type = {}
    for z in zaken:
        zt = cat["zts"].get(pub(z["zaaktype"]), {})
        z["_zaaktype"] = zt.get("identificatie", "?")
        z["_status"] = cat["names"].get(pub(z["status"] and c.get(z["status"])["statustype"]), "—") if z.get("status") else "—"
        per_type.setdefault(z["_zaaktype"], []).append(z)
    zichtbaar = [zt["identificatie"] for zt in cat["zts"].values() if zt["identificatie"] in per_type]
    onzichtbaar = [zt["identificatie"] for zt in cat["zts"].values() if zt["identificatie"] not in per_type]
    return render(TPL_HOME, per_type=per_type, onzichtbaar=onzichtbaar, uuid_of=uuid_of)


@app.route("/zaak/<uuid>")
@login_required
def zaak(uuid):
    c, cat = me(), catalogus()
    z = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}")
    zt = cat["zts"].get(pub(z["zaaktype"]), {"identificatie": "?", "_statustypen": [], "_resultaattypen": [], "_iots": []})
    statussen = sorted(c.list(f"{ZGW_BASE}/zaken/api/v1/statussen", zaak=z["url"]), key=lambda s: s["datumStatusGezet"])
    for s in statussen:
        s["_naam"] = cat["names"].get(pub(s["statustype"]), "?")
    rollen = c.list(f"{ZGW_BASE}/zaken/api/v1/rollen", zaak=z["url"])
    for r in rollen:
        r["_naam"] = cat["names"].get(pub(r["roltype"]), "?")
        bi = r.get("betrokkeneIdentificatie") or {}
        r["_wie"] = bi.get("statutaireNaam") or bi.get("geslachtsnaam") or bi.get("achternaam") or bi.get("identificatie") or bi.get("inpBsn") or "?"
    eig = [(e["naam"], e["waarde"]) for e in c.list(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}/zaakeigenschappen")]
    resultaat = None
    if z.get("resultaat"):
        resultaat = cat["names"].get(pub(c.get(z["resultaat"])["resultaattype"]), "?")
    docs = []
    for zio in c.list(f"{ZGW_BASE}/zaken/api/v1/zaakinformatieobjecten", zaak=z["url"]):
        try:
            d = c.get(zio["informatieobject"])
            docs.append({"ok": True, "titel": d["titel"], "type": cat["names"].get(pub(d["informatieobjecttype"]), "?"),
                         "vertr": d["vertrouwelijkheidaanduiding"], "auteur": d["auteur"], "bron": d["bronorganisatie"],
                         "datum": d["creatiedatum"], "url": url_for("document", uuid=uuid_of(d["url"]))})
        except Forbidden as e:
            docs.append({"ok": False, "titel": zio.get("titel") or "(document)", "reden": e.body.get("detail", "geen toegang")})
    gerelateerd = []
    for r in z.get("relevanteAndereZaken", []):
        try:
            o = c.get(r["url"])
            gerelateerd.append({"ok": True, "id": o["identificatie"], "oms": o["omschrijving"], "url": url_for("zaak", uuid=uuid_of(o["url"])),
                                "aard": r["aardRelatie"], "vertr": o["vertrouwelijkheidaanduiding"], "raw": o["url"]})
        except Forbidden as e:
            gerelateerd.append({"ok": False, "id": uuid_of(r["url"])[:8], "aard": r["aardRelatie"], "reden": e.body.get("detail", "geen toegang"),
                                "url": url_for("zaak", uuid=uuid_of(r["url"]))})
    gezet = {s["statustype"] for s in statussen}
    volgende = [s for s in zt["_statustypen"] if s["url"] not in gezet]
    return render(TPL_ZAAK, z=z, zt=zt, statussen=statussen, rollen=rollen, eig=eig, resultaat=resultaat, docs=docs,
                  gerelateerd=gerelateerd, volgende=volgende, VERTR=VERTR, uuid=uuid, gesloten=bool(z.get("einddatum")))


@app.route("/document/<uuid>")
@login_required
def document(uuid):
    c, cat = me(), catalogus()
    d = c.get(f"{ZGW_BASE}/documenten/api/v1/enkelvoudiginformatieobjecten/{uuid}")
    inhoud = requests.get(d["inhoud"].replace(ZGW_PUBLIC, ZGW_BASE), headers={
        "Authorization": f"Bearer {c._token()}", "Host": ZGW_HOST, "X-Forwarded-Proto": "https"}, timeout=30)
    tekst = inhoud.text if inhoud.ok and (d.get("formaat") or "").startswith("text/") else f"({inhoud.status_code}, {d.get('formaat')})"
    return render(TPL_DOC, d=d, tekst=tekst, type=cat["names"].get(pub(d["informatieobjecttype"]), "?"))


@app.route("/zaak/<uuid>/audit")
@login_required
def audit(uuid):
    c = me()
    regels = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}/audittrail")
    regels = sorted(regels, key=lambda r: r["aanmaakdatum"], reverse=True)
    return render(TPL_AUDIT, regels=regels, uuid=uuid)


@app.route("/zaak/<uuid>/status", methods=["POST"])
@login_required
def zet_status(uuid):
    c = me()
    z = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}")
    c.post(f"{ZGW_BASE}/zaken/api/v1/statussen", {"zaak": z["url"], "statustype": request.form["statustype"],
                                                    "datumStatusGezet": dt.datetime.now(dt.timezone.utc).isoformat(),
                                                    "statustoelichting": request.form.get("toelichting", "")})
    return redirect(url_for("zaak", uuid=uuid))


@app.route("/zaak/<uuid>/resultaat", methods=["POST"])
@login_required
def zet_resultaat(uuid):
    c = me()
    z = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}")
    c.post(f"{ZGW_BASE}/zaken/api/v1/resultaten", {"zaak": z["url"], "resultaattype": request.form["resultaattype"],
                                                     "toelichting": request.form.get("toelichting", "")})
    return redirect(url_for("zaak", uuid=uuid))


def maak_document(c, zaak_url, iot_url, titel, tekst, vertr, auteur, bron=RSIN, ontvangstdatum=None):
    body = {"bronorganisatie": bron, "creatiedatum": dt.date.today().isoformat(), "titel": titel, "auteur": auteur,
            "taal": "nld", "formaat": "text/plain", "inhoud": base64.b64encode(tekst.encode()).decode(),
            "bestandsnaam": "".join(ch if ch.isalnum() else "-" for ch in titel.lower())[:40] + ".txt",
            "bestandsomvang": len(tekst.encode()), "informatieobjecttype": iot_url, "vertrouwelijkheidaanduiding": vertr,
            "indicatieGebruiksrecht": False}
    if ontvangstdatum:
        body["ontvangstdatum"] = ontvangstdatum
    d = c.post(f"{ZGW_BASE}/documenten/api/v1/enkelvoudiginformatieobjecten", body)
    c.post(f"{ZGW_BASE}/zaken/api/v1/zaakinformatieobjecten", {"zaak": zaak_url, "informatieobject": d["url"], "titel": titel})
    return d


@app.route("/zaak/<uuid>/document", methods=["POST"])
@login_required
def voeg_document_toe(uuid):
    c = me()
    z = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}")
    maak_document(c, z["url"], request.form["informatieobjecttype"], request.form["titel"], request.form["tekst"],
                  request.form["vertrouwelijkheidaanduiding"], session["name"])
    return redirect(url_for("zaak", uuid=uuid))


@app.route("/zaak/<uuid>/vrijgeven", methods=["POST"])
@login_required
def vrijgeven(uuid):
    """Forensisch: filterverantwoording vastleggen, gefilterde set als 'Vrijgegeven
    bewijsstuk' aan de onderzoekszaak hangen, vordering afsluiten."""
    c, cat = me(), catalogus()
    v = c.get(f"{ZGW_BASE}/zaken/api/v1/zaken/{uuid}")
    vt = cat["zts"][pub(v["zaaktype"])]
    so_url = request.form["onderzoek"]
    so = c.get(so_url)
    st = cat["zts"][pub(so["zaaktype"])]
    iot_v = {i["omschrijving"]: i["url"] for i in vt["_iots"]}
    iot_so = {i["omschrijving"]: i["url"] for i in st["_iots"]}
    stat = {s["omschrijving"]: s["url"] for s in vt["_statustypen"]}
    res = {r["omschrijving"]: r["url"] for r in vt["_resultaattypen"]}
    now = lambda: dt.datetime.now(dt.timezone.utc).isoformat()
    maak_document(c, v["url"], iot_v["Filterverantwoording"], f"Filterverantwoording {v['identificatie']}",
                  request.form["verantwoording"], "geheim", session["name"])
    c.post(f"{ZGW_BASE}/zaken/api/v1/statussen", {"zaak": v["url"], "statustype": stat["Gefilterd"], "datumStatusGezet": now()})
    maak_document(c, so["url"], iot_so["Vrijgegeven bewijsstuk"], request.form["titel"], request.form["bewijsstuk"],
                  "zaakvertrouwelijk", session["name"], ontvangstdatum=dt.date.today().isoformat())
    maak_document(c, v["url"], iot_v["Vrijgaveverklaring"], f"Vrijgaveverklaring {v['identificatie']}",
                  f"Vrijgegeven aan {so['identificatie']}: '{request.form['titel']}'. Grondslag en selectie: zie filterverantwoording. "
                  f"Vrijgegeven door {session['name']} op {dt.date.today().isoformat()}.", "vertrouwelijk", session["name"])
    c.post(f"{ZGW_BASE}/zaken/api/v1/resultaten", {"zaak": v["url"], "resultaattype": res["Vrijgegeven"], "toelichting": "Gefilterde set overgedragen"})
    time.sleep(1)
    c.post(f"{ZGW_BASE}/zaken/api/v1/statussen", {"zaak": v["url"], "statustype": stat["Vrijgegeven"], "datumStatusGezet": now()})
    return redirect(url_for("zaak", uuid=uuid_of(so["url"])))


@app.route("/healthz")
def healthz():
    return "ok"


# ------------------------------------------------------------------ templates
LAYOUT = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>ACM-werkplek</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7f9;--fg:#1b1f24;--muted:#5b6470;--line:#d9dee5;--card:#fff;--acc:#005ea5;--warn:#b42318;--ok:#067647;
--loket:#1d6fb8;--beh:#1f8a4c;--for:#c2410c;--adm:#6b21a8}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:10px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header a{color:var(--acc);text-decoration:none}header .brand{font-weight:700;font-size:17px}
.rol{padding:3px 10px;border-radius:999px;color:#fff;font-size:13px;font-weight:600}
.rol.acm-loket{background:var(--loket)}.rol.acm-behandelaar,.rol.acm-toezicht{background:var(--beh)}.rol.acm-forensisch{background:var(--for)}.rol.k8s-admins{background:var(--adm)}
main{max-width:1100px;margin:0 auto;padding:20px 24px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:24px 0 8px}
.sub{color:var(--muted);margin:0 0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;font-size:13px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line);background:#fff}
.pill.geheim,.pill.zeer_geheim,.pill.confidentieel{background:#fee4e2;border-color:#f9b3ac;color:var(--warn)}
.pill.vertrouwelijk{background:#fff0d6;border-color:#f6c37a}.pill.zaakvertrouwelijk{background:#e8f1fb;border-color:#b6d0ee}
.lock{background:#fff4f2;border:1px dashed #f0a39a;color:var(--warn);padding:8px 12px;border-radius:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
form.inline{display:grid;gap:8px}input,select,textarea{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;width:100%}
button{font:inherit;padding:7px 14px;border:0;border-radius:6px;background:var(--acc);color:#fff;cursor:pointer}button.warn{background:var(--for)}
.muted{color:var(--muted)}.ok{color:var(--ok)}code{font-size:13px;background:#eef1f5;padding:1px 5px;border-radius:4px}
.big403{font-size:64px;font-weight:800;color:var(--warn);line-height:1}
</style></head><body>
<header><a class="brand" href="/">ACM-werkplek</a>
{% if session.get('user') %}<span class="rol {{session.role.group}}">{{session.role.label}}</span>
<span class="muted">{{session.name}} · API-client <code>{{session.role.client}}</code></span>
<span style="margin-left:auto"><a href="/logout">Uitloggen</a></span>{% endif %}</header>
<main>%%BODY%%</main></body></html>"""

TPL_HOME = """<h1>Mijn zaken</h1>
<p class="sub">Alles op deze pagina komt rechtstreeks uit de Zaken API, opgevraagd als <code>{{session.role.client}}</code>. Wat je niet ziet, bestaat voor deze rol niet.</p>
{% for zt, zaken in per_type.items() %}<h2>{{zt}} <span class="muted">({{zaken|length}})</span></h2>
<div class="card"><table><tr><th>Identificatie</th><th>Omschrijving</th><th>Status</th><th>Vertrouwelijkheid</th><th>Start</th></tr>
{% for z in zaken %}<tr><td><a href="/zaak/{{uuid_of(z.url)}}">{{z.identificatie}}</a></td><td>{{z.omschrijving}}</td><td>{{z._status}}{% if z.einddatum %} <span class="muted">(gesloten)</span>{% endif %}</td>
<td><span class="pill {{z.vertrouwelijkheidaanduiding}}">{{z.vertrouwelijkheidaanduiding}}</span></td><td>{{z.startdatum}}</td></tr>{% endfor %}</table></div>{% endfor %}
{% if onzichtbaar %}<h2 class="muted">Niet zichtbaar voor deze rol</h2><div class="lock">Zaaktypen {{onzichtbaar|join(', ')}}: de Zaken API geeft voor <code>{{session.role.client}}</code> geen enkele zaak terug — er is geen autorisatie op dit zaaktype.</div>{% endif %}"""

TPL_ZAAK = """<p class="sub"><a href="/">← Mijn zaken</a></p>
<h1>{{z.identificatie}} <span class="pill {{z.vertrouwelijkheidaanduiding}}">{{z.vertrouwelijkheidaanduiding}}</span></h1>
<p class="sub">{{zt.identificatie}} · {{z.omschrijving}}{% if gesloten %} · <b>gesloten {{z.einddatum}}</b>{% endif %}
{% if resultaat %} · resultaat: <b>{{resultaat}}</b>{% endif %} · <a href="/zaak/{{uuid}}/audit">audittrail</a></p>
<div class="grid">
<div class="card"><h2 style="margin-top:0">Statusverloop</h2><table>{% for s in statussen %}<tr><td>{{s.datumStatusGezet[:16]|replace('T',' ')}}</td><td><b>{{s._naam}}</b> <span class="muted">{{s.statustoelichting}}</span></td></tr>{% endfor %}</table>
{% if volgende and not gesloten %}<form class="inline" method="post" action="/zaak/{{uuid}}/status" style="margin-top:10px">
<select name="statustype">{% for s in volgende %}<option value="{{s.url}}">{{s.volgnummer}}. {{s.omschrijving}}</option>{% endfor %}</select>
<input name="toelichting" placeholder="toelichting (optioneel)"><button>Status zetten</button></form>{% endif %}</div>
<div class="card"><h2 style="margin-top:0">Betrokkenen</h2><table>{% for r in rollen %}<tr><td>{{r._naam}}</td><td>{{r._wie}} <span class="muted">({{r.betrokkeneType}})</span></td></tr>{% endfor %}</table>
<h2>Kenmerken</h2><table>{% for n,w in eig %}<tr><td>{{n}}</td><td>{{w}}</td></tr>{% endfor %}</table>
{% if not resultaat and not gesloten and zt._resultaattypen %}<form class="inline" method="post" action="/zaak/{{uuid}}/resultaat" style="margin-top:10px">
<select name="resultaattype">{% for r in zt._resultaattypen %}<option value="{{r.url}}">{{r.omschrijving}}</option>{% endfor %}</select>
<input name="toelichting" placeholder="toelichting"><button>Resultaat vastleggen</button></form>{% endif %}</div>
</div>
<h2>Documenten</h2><div class="card"><table><tr><th>Titel</th><th>Type</th><th>Vertrouwelijkheid</th><th>Auteur</th><th>Bron</th><th>Datum</th></tr>
{% for d in docs %}{% if d.ok %}<tr><td><a href="{{d.url}}">{{d.titel}}</a></td><td>{{d.type}}</td><td><span class="pill {{d.vertr}}">{{d.vertr}}</span></td><td>{{d.auteur}}</td><td>{{d.bron}}</td><td>{{d.datum}}</td></tr>
{% else %}<tr><td colspan="6"><div class="lock">🔒 <b>{{d.titel}}</b> — Documenten API: {{d.reden}}</div></td></tr>{% endif %}{% endfor %}</table>
{% if not gesloten and zt._iots %}<details style="margin-top:10px"><summary>Document toevoegen</summary><form class="inline" method="post" action="/zaak/{{uuid}}/document" style="margin-top:8px">
<select name="informatieobjecttype">{% for i in zt._iots %}<option value="{{i.url}}">{{i.omschrijving}} (standaard {{i.vertrouwelijkheidaanduiding}})</option>{% endfor %}</select>
<input name="titel" placeholder="titel" required><textarea name="tekst" rows="4" placeholder="inhoud (tekst)"></textarea>
<select name="vertrouwelijkheidaanduiding">{% for v in VERTR %}<option {% if v=='zaakvertrouwelijk' %}selected{% endif %}>{{v}}</option>{% endfor %}</select>
<button>Toevoegen</button></form></details>{% endif %}</div>
{% if gerelateerd %}<h2>Gerelateerde zaken</h2><div class="card">{% for g in gerelateerd %}{% if g.ok %}<p><a href="{{g.url}}">{{g.id}}</a> — {{g.oms}} <span class="pill {{g.vertr}}">{{g.vertr}}</span> <span class="muted">({{g.aard}})</span></p>
{% else %}<div class="lock" style="margin:6px 0">🔒 Gerelateerde zaak <a href="{{g.url}}">{{g.id}}…</a> ({{g.aard}}) — Zaken API: {{g.reden}}</div>{% endif %}{% endfor %}</div>{% endif %}
{% if zt.identificatie == 'VORDERING' and not gesloten and session.role.client == 'forensisch' %}
<h2>Filteren en vrijgeven</h2><div class="card"><p class="muted">Legt de filterverantwoording (geheim) en een vrijgaveverklaring vast in deze vorderingszaak, maakt het <b>vrijgegeven bewijsstuk</b> (zaakvertrouwelijk) aan in de onderzoekszaak en sluit de vordering af met resultaat "Vrijgegeven".</p>
<form class="inline" method="post" action="/zaak/{{uuid}}/vrijgeven">
<select name="onderzoek">{% for g in gerelateerd if g.ok %}<option value="{{g.raw}}">{{g.id}} — {{g.oms}}</option>{% endfor %}</select>
<textarea name="verantwoording" rows="3" required placeholder="Filterverantwoording: welke selectie, op welke grond, door wie"></textarea>
<input name="titel" required placeholder="Titel vrijgegeven bewijsstuk, bv. 'Tijdlijn 9 oktober, Oldenzaal–Bad Bentheim'">
<textarea name="bewijsstuk" rows="5" required placeholder="De gefilterde inhoud die de behandelaar mag zien"></textarea>
<button class="warn">Vrijgeven aan onderzoek</button></form></div>{% endif %}"""

TPL_DOC = """<p class="sub"><a href="javascript:history.back()">← terug</a></p><h1>{{d.titel}} <span class="pill {{d.vertrouwelijkheidaanduiding}}">{{d.vertrouwelijkheidaanduiding}}</span></h1>
<p class="sub">{{type}} · auteur {{d.auteur}} · bron {{d.bronorganisatie}} · {{d.creatiedatum}}{% if d.ontvangstdatum %} · ontvangen {{d.ontvangstdatum}}{% endif %}</p>
<div class="card"><pre style="white-space:pre-wrap;margin:0">{{tekst}}</pre></div>"""

TPL_AUDIT = """<p class="sub"><a href="/zaak/{{uuid}}">← zaak</a></p><h1>Audittrail</h1><p class="sub">Uit de Zaken API: wie deed wat, via welke applicatie.</p>
<div class="card"><table><tr><th>Wanneer</th><th>Actie</th><th>Resource</th><th>Gebruiker</th><th>Applicatie</th><th>Toelichting</th></tr>
{% for r in regels %}<tr><td>{{r.aanmaakdatum[:19]|replace('T',' ')}}</td><td>{{r.actie}}</td><td>{{r.resource}} <span class="muted">{{r.resourceWeergave}}</span></td><td>{{r.gebruikersId}}</td><td>{{r.applicatieWeergave}}</td><td>{{r.toelichting}}</td></tr>{% endfor %}</table></div>"""

TPL_403 = """<div class="big403">403</div><h1>Je hebt geen toestemming om deze actie uit te voeren</h1>
<p class="sub">Dit is geen scherm van de werkplek maar het antwoord van Open Zaak zelf, voor API-client <code>{{session.role.client}}</code>.</p>
<div class="card"><table><tr><th>URL</th><td><code>{{url}}</code></td></tr><tr><th>code</th><td>{{body.code}}</td></tr><tr><th>detail</th><td>{{body.detail}}</td></tr><tr><th>instance</th><td class="muted">{{body.instance}}</td></tr></table></div>
<p><a href="javascript:history.back()">← terug</a> · <a href="/">Mijn zaken</a></p>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
