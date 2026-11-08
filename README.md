# Stazioni MeteoNetwork – area Curtarolo

Lettura automatica (GitHub Actions, ogni 15 minuti) delle stazioni meteo MeteoNetwork
entro 15 km da Curtarolo, per l'app emergenze della protezione civile.

## Cosa produce

| File | Contenuto |
|---|---|
| `data/ultimo.json` | Ultimo dato di ogni stazione, ordinate per distanza da Curtarolo |
| `data/storico/AAAA-MM.csv` | Una riga per ogni nuovo rilevamento di ogni stazione |

L'app può leggere l'ultimo dato da:

```
https://raw.githubusercontent.com/simone-9/meteonetwork/main/data/ultimo.json
```

Funziona così solo se il repository è pubblico. Se è privato, la lettura va fatta con un token.

### Campi principali di ogni stazione in `ultimo.json`

- `distanza_km` – distanza da Curtarolo
- `online` – stato indicato da MeteoNetwork
- `dato_recente` – `true` se l'ultimo rilevamento ha meno di 60 minuti (`minuti_dato_vecchio` in `stazioni.json`)
- `ultimo_rilevamento_utc` / `ultimo_rilevamento_locale`
- `temperatura`, `umidita`, `pressione` – `attuale`, `max`, `min` del giorno
- `pioggia.oggi_mm` – pioggia caduta da mezzanotte
- `pioggia.intensita_mm_h` – intensità attuale
- `pioggia.intensita_max_oggi_mm_h` – intensità massima del giorno
- `vento` – `velocita_kmh`, `direzione`, `raffica_kmh`, `media_kmh`
- `sensori_non_a_norma` – sensori che MeteoNetwork segnala come non affidabili
- `errore_lettura` – presente solo se la scheda non è stata letta: in quel caso restano i valori della lettura precedente

Se un sensore manca o non è valido, il valore è `null`.

## Stazioni

L'elenco è in `stazioni.json`. Per aggiungere o togliere una stazione basta modificare
quel file indicando il codice MeteoNetwork (es. `vnt329`) e le coordinate.
La distanza viene calcolata automaticamente dal punto `centro`.

## Avvio manuale

Scheda **Actions** → *Lettura stazioni MeteoNetwork* → **Run workflow**.

## Fonte dei dati

Dati © MeteoNetwork – www.meteonetwork.eu – licenza CC-BY 4.0.
Lo script legge le schede pubbliche delle stazioni, con una pausa tra una richiesta e l'altra.
