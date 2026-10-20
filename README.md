# Wetterbericht Riederalp

Erzeugt einen Wetterbericht im Stil von MeteoSchweiz für Riederalp, Wallis, Schweiz und Europa, für heute, morgen und die Folgetage. Grundlage sind Open-Meteo-Daten: MeteoSchweiz ICON-CH1/CH2 als Primärmodell, ein Multimodellvergleich, fünf Ensembles, ECMWF-Druckflächen über Europa, ERA5-Land 1991–2020 als Klimatologie und CAMS-Aerosol.

Neben der klassischen Prognose rechnet das Skript Diagnosen, die in Standardberichten fehlen: Inversion Mörel–Riederalp, Föhnindex Lugano–Zürich, Feuchtefluss gegen den Alpenhauptkamm in 700 hPa, Hochnebelobergrenze, Bise, Trog- und Rückenachsen, geostrophische Höhenströmung, Strahlstromlage, Blockierung nach Tibaldi-Molteni, Saharastaub, Anomalie und Perzentil gegenüber 1991–2020 sowie KI-Ensemble (AIFS) gegen IFS-Ensemble.

Unsicherheit ist überall sichtbar: Ensemble-Spannen (10–90 %), Niederschlagswahrscheinlichkeiten, Modellstreuung, Ausreisser, eine Prognosesicherheit pro Tag und die Vorhersagbarkeitsgrenze des aktuellen Laufs. Modelle, die Open-Meteo gerade nicht liefert, werden im Bericht unter «Nicht verfügbar» aufgeführt statt still weggelassen.

## Täglich als Webseite (GitHub Actions + Pages)

1. Neues **öffentliches** Repository anlegen und `wetterbericht.py` sowie `.github/workflows/wetterbericht.yml` hochladen (Ordnerstruktur beibehalten).
2. *Settings → Pages → Source*: «GitHub Actions» wählen.
3. *Actions → Wetterbericht Riederalp → Run workflow*. Der Bericht erscheint unter `https://<benutzer>.github.io/<repo>/`.

Danach läuft er automatisch um 06:40 und 17:40 Uhr (Sommerzeit; im Winter eine Stunde früher). «Run workflow» erzeugt ihn jederzeit neu, auch in der GitHub-App.

Hinweise: Pages für private Repositories braucht ein kostenpflichtiges GitHub-Konto. GitHub pausiert geplante Workflows in öffentlichen Repositories nach 60 Tagen ohne Aktivität; ein Commit reaktiviert sie.

Optional: Ein Repository-Secret `ANTHROPIC_API_KEY` fügt eine von Claude formulierte synoptische Einordnung hinzu (Modell über `WB_CLAUDE_MODEL` änderbar, Standard `claude-sonnet-5-5`).

## Lokal, per Cron oder auf Databricks

```
python3 wetterbericht.py --out site
```

Python 3.9 oder neuer, nur Standardbibliothek (unter Windows zusätzlich `pip install tzdata`). Optionen: `--days 3..10`, `--cache ORDNER`, `--claude`.

Ausgabe: `index.html` (Bericht mit Grafiken, hell/dunkel), `bericht.md` (gleicher Text) und `diagnostik.json` (alle Kennzahlen).

Der erste Lauf eines Kalendermonats lädt die ERA5-Land-Klimatologie (30 Abfragen, dauert etwa eine Minute) und speichert sie im Cache. Ein normaler Lauf braucht rund zwei Minuten und einige hundert gewichtete Open-Meteo-Aufrufe (Gratislimit: 10 000 pro Tag, nur nicht-kommerziell).

## Anpassen

Orte stehen in `LOCAL`, Vergleichsmodelle in `COMPARE`, Ensembles in `ENSEMBLES`. Pro Modell sind mehrere Open-Meteo-IDs hinterlegt, weil Open-Meteo Modelle gelegentlich umbenennt; die erste funktionierende wird verwendet. Schwellen der Diagnosen (Föhn ±4/±8 hPa, Bise +3/+5 hPa, Feuchtefluss 40/80 g kg⁻¹ m s⁻¹) sind Faustregeln und im Code leicht zu ändern.

## Grenzen

Das Skript wurde gegen nachgebildete Open-Meteo-Antworten getestet, nicht gegen die Live-API. Beim ersten echten Lauf lohnt sich ein Blick in den Abschnitt «Nicht verfügbar» und in `diagnostik.json`. Die Diagnosen sind bewusst einfach (10°-Raster für Z500, Punktwerte statt Felder) und ersetzen keine Analyse von Karten. Kein offizieller Bericht von MeteoSchweiz; Warnungen: meteoschweiz.admin.ch.

Daten: Open-Meteo.com (CC BY 4.0) mit Modellen von MeteoSchweiz, ECMWF, DWD, NOAA, Météo-France und ItaliaMeteo; ERA5-Land und CAMS (Copernicus).
