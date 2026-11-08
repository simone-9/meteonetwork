#!/usr/bin/env python3
"""Legge i dati in tempo reale delle stazioni MeteoNetwork elencate in stazioni.json.

Per ogni stazione scarica la scheda pubblica
https://www.meteonetwork.eu/it/weather-station/<id> e ne estrae gli ultimi valori.

Produce:
  data/ultimo.json            ultimo dato di ogni stazione (letto dall'app emergenze)
  data/storico/AAAA-MM.csv    una riga per ogni nuovo rilevamento di ogni stazione

Usa solo la libreria standard di Python (nessuna dipendenza da installare).
Dati MeteoNetwork, licenza CC-BY 4.0.
"""

import csv
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RADICE = Path(__file__).resolve().parent.parent
FILE_CONFIG = RADICE / "stazioni.json"
FILE_ULTIMO = RADICE / "data" / "ultimo.json"
CARTELLA_STORICO = RADICE / "data" / "storico"

URL_STAZIONE = "https://www.meteonetwork.eu/it/weather-station/{id}"
USER_AGENT = "Mozilla/5.0 (compatible; lettore-stazioni-meteonetwork/1.0)"
FUSO = ZoneInfo("Europe/Rome")
PAUSA_TRA_RICHIESTE = 1.5  # secondi, per non sovraccaricare il sito

# Sensori di cui segnalare il "Non a norma" (radiazione solare/UV esclusi: quasi nessuna stazione li ha)
SENSORI_CONTROLLATI = {"Temperatura", "Umidità", "Pressione", "Pioggia", "Vento", "Temp. di rugiada"}

NUM = r"(-?\d+(?:\.\d+)?|-{2,3})"  # un numero oppure "--" / "---" (sensore assente)

COLONNE_STORICO = [
    "rilevamento_utc", "id", "nome", "temperatura", "umidita", "pressione",
    "rugiada", "pioggia_oggi_mm", "intensita_mm_h", "vento_kmh",
    "direzione", "raffica_kmh",
]


# ---------------------------------------------------------------- utilità

def numero(valore):
    """Converte il testo in float; '--', '---' o vuoto diventano None."""
    if valore is None or valore.startswith("--") or valore == "":
        return None
    try:
        return float(valore)
    except ValueError:
        return None


def distanza_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def scarica(url, tentativi=3):
    errore = None
    for n in range(tentativi):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "it-IT,it;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace"), r.geturl()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            errore = e
            time.sleep(3 * (n + 1))
    raise RuntimeError(f"download fallito: {errore}")


def solo_testo(pagina):
    pagina = re.sub(r"<script[\s\S]*?</script>", " ", pagina, flags=re.I)
    pagina = re.sub(r"<style[\s\S]*?</style>", " ", pagina, flags=re.I)
    pagina = re.sub(r"<!--[\s\S]*?-->", " ", pagina)
    pagina = re.sub(r"<[^>]+>", " ", pagina)
    return re.sub(r"\s+", " ", html.unescape(pagina))


def cerca(testo, schema):
    m = re.search(schema, testo)
    return m.groups() if m else None


# ---------------------------------------------------------------- lettura di una stazione

def leggi_stazione(id_stazione):
    pagina, url = scarica(URL_STAZIONE.format(id=id_stazione))
    testo = solo_testo(pagina)

    # Ora dell'ultimo rilevamento: la pagina la scrive in UTC dentro uno script
    ora = re.search(r"maxTHour\s*=\s*(\d+)\s*-\s*hours", pagina)
    minuti = re.search(r"maxTMin\s*=\s*(\d+)\s*-\s*parseInt", pagina)
    giorno = re.search(r"' del ' \+ '(\d{4}-\d{2}-\d{2})'", pagina)
    rilevamento = None
    if ora and minuti and giorno:
        rilevamento = datetime.strptime(giorno.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc) \
            + timedelta(hours=int(ora.group(1)), minutes=int(minuti.group(1)))

    # Stato online/offline: il bottone nel percorso in alto (la pagina contiene anche
    # un bottone "Offline" commentato, quindi tolgo prima i commenti HTML)
    senza_commenti = re.sub(r"<!--[\s\S]*?-->", " ", pagina)
    stato = re.search(r'class="btn btn-(?:success|danger)"[^>]*>\s*(online|offline)\s*<', senza_commenti, re.I)
    stato = (stato.group(1).lower(),) if stato else None
    t = cerca(testo, rf"Temperatura {NUM} ?°C {NUM} ?°C {NUM} ?°C")
    u = cerca(testo, rf"Umidità {NUM} % {NUM} % {NUM} %")
    p = cerca(testo, rf"Pressione {NUM} hPa {NUM} hPa {NUM} hPa")
    r = cerca(testo, rf"Pioggia {NUM} mm \({NUM} mm/h\) Rate: {NUM} mm/h")
    v = cerca(testo, rf"Vento {NUM} km/h \(([A-Z]{{1,3}}|-+)\) Raffica {NUM} km/h Vel\. media {NUM} km/h")
    d = cerca(testo, rf"Temp\. di rugiada {NUM} ?°C")

    if not (t or u or r):
        raise RuntimeError("formato della pagina non riconosciuto")

    # Sensori che MeteoNetwork ha segnato "Non a norma" (dato non affidabile o disabilitato)
    non_a_norma = []
    for riquadro in senza_commenti.split('class="col-lg shadow-sm')[1:]:
        if "Non a norma" in riquadro:
            titolo = re.search(r'class="c_head[^"]*"[^>]*>\s*(?:<span[\s\S]*?</span>\s*)?([^<]+?)\s*(?:<br|</span)', riquadro)
            nome = html.unescape(titolo.group(1)).strip() if titolo else ""
            if nome in SENSORI_CONTROLLATI:
                non_a_norma.append(nome)

    pressione = [numero(x) for x in p] if p else [None, None, None]
    if pressione[0] is not None and pressione[0] < 800:  # 0 hPa = barometro assente
        pressione = [None, None, None]

    return {
        "url": url,
        "online": (stato[0] == "online") if stato else None,
        "ultimo_rilevamento_utc": rilevamento.strftime("%Y-%m-%dT%H:%M:%SZ") if rilevamento else None,
        "ultimo_rilevamento_locale": rilevamento.astimezone(FUSO).strftime("%d/%m/%Y %H:%M") if rilevamento else None,
        "temperatura": {"attuale": numero(t[0]), "max": numero(t[1]), "min": numero(t[2])} if t else None,
        "umidita": {"attuale": numero(u[0]), "max": numero(u[1]), "min": numero(u[2])} if u else None,
        "pressione": {"attuale": pressione[0], "max": pressione[1], "min": pressione[2]},
        "rugiada": numero(d[0]) if d else None,
        "pioggia": {
            "oggi_mm": numero(r[0]),
            "intensita_mm_h": numero(r[1]),
            "intensita_max_oggi_mm_h": numero(r[2]),
        } if r else None,
        "vento": {
            "velocita_kmh": numero(v[0]),
            "direzione": None if v[1].startswith("-") else v[1],
            "raffica_kmh": numero(v[2]),
            "media_kmh": numero(v[3]),
        } if v else None,
        "sensori_non_a_norma": non_a_norma,
    }


# ---------------------------------------------------------------- principale

def main():
    config = json.loads(FILE_CONFIG.read_text(encoding="utf-8"))
    centro = config["centro"]
    limite_vecchio = timedelta(minutes=config.get("minuti_dato_vecchio", 60))
    adesso = datetime.now(timezone.utc)

    precedenti = {}
    if FILE_ULTIMO.exists():
        try:
            for s in json.loads(FILE_ULTIMO.read_text(encoding="utf-8")).get("stazioni", []):
                precedenti[s["id"]] = s
        except (json.JSONDecodeError, KeyError):
            pass

    risultati, nuove_righe, errori = [], [], 0
    for i, st in enumerate(config["stazioni"]):
        if i:
            time.sleep(PAUSA_TRA_RICHIESTE)
        base = {
            "id": st["id"],
            "nome": st["nome"],
            "comune": st["comune"],
            "lat": st["lat"],
            "lon": st["lon"],
            "distanza_km": round(distanza_km(centro["lat"], centro["lon"], st["lat"], st["lon"]), 1),
        }
        try:
            dati = leggi_stazione(st["id"])
        except Exception as e:  # una stazione che non risponde non blocca le altre
            errori += 1
            print(f"[ERRORE] {st['id']}: {e}", file=sys.stderr)
            vecchio = precedenti.get(st["id"], {})
            risultati.append({**vecchio, **base, "dato_recente": False, "errore_lettura": str(e)})
            continue

        rilev = dati["ultimo_rilevamento_utc"]
        aggiornato = False
        if rilev:
            eta = adesso - datetime.strptime(rilev, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            aggiornato = eta <= limite_vecchio
        risultati.append({**base, **dati, "dato_recente": aggiornato})
        print(f"[OK] {st['id']} {st['nome']}: {dati['ultimo_rilevamento_locale']}")

        if rilev and precedenti.get(st["id"], {}).get("ultimo_rilevamento_utc") != rilev:
            T, U, P = dati["temperatura"] or {}, dati["umidita"] or {}, dati["pressione"] or {}
            R, V = dati["pioggia"] or {}, dati["vento"] or {}
            nuove_righe.append([
                rilev, st["id"], st["nome"], T.get("attuale"), U.get("attuale"), P.get("attuale"),
                dati["rugiada"], R.get("oggi_mm"), R.get("intensita_mm_h"),
                V.get("velocita_kmh"), V.get("direzione"), V.get("raffica_kmh"),
            ])

    if errori == len(config["stazioni"]):
        print("Nessuna stazione letta: non aggiorno i file.", file=sys.stderr)
        sys.exit(1)

    risultati.sort(key=lambda s: s["distanza_km"])
    uscita = {
        "aggiornato_utc": adesso.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggiornato_locale": adesso.astimezone(FUSO).strftime("%d/%m/%Y %H:%M"),
        "centro": centro,
        "fonte": "MeteoNetwork (www.meteonetwork.eu) - licenza CC-BY 4.0",
        "stazioni_lette": len(risultati) - errori,
        "stazioni_in_errore": errori,
        "stazioni": risultati,
    }
    FILE_ULTIMO.parent.mkdir(parents=True, exist_ok=True)
    FILE_ULTIMO.write_text(json.dumps(uscita, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if nuove_righe:
        CARTELLA_STORICO.mkdir(parents=True, exist_ok=True)
        for riga in nuove_righe:
            mese = riga[0][:7]
            f = CARTELLA_STORICO / f"{mese}.csv"
            nuovo = not f.exists()
            with f.open("a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if nuovo:
                    w.writerow(COLONNE_STORICO)
                w.writerow(["" if x is None else x for x in riga])

    print(f"Lette {len(risultati) - errori}/{len(risultati)} stazioni, {len(nuove_righe)} nuovi rilevamenti nello storico.")


if __name__ == "__main__":
    main()
