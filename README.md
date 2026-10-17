# Supermarktaanbiedingen

Eén overzicht van alle supermarktaanbiedingen van deze week, met zoeken, filteren per winkel en categorie, sorteren op korting en een boodschappenlijstje. Een scraper haalt elke maandag en woensdag automatisch de nieuwe aanbiedingen op.

## Hoe het werkt

`scraper/scrape.py` opent de aanbiedingenpagina van elke supermarkt in een onzichtbare Chrome-browser, vangt de gegevens op die de pagina zelf inlaadt en herkent daarin producten met een prijs of actie (1+1 gratis, 2e halve prijs, 35% korting…). Lukt dat niet, dan leest hij de zichtbare productkaartjes van de pagina. Het resultaat komt in `site/data/offers.json`, en `site/index.html` toont dat.

Mislukt een supermarkt een keer, dan blijven de gegevens van de vorige keer staan (gemarkeerd als mogelijk verouderd) en zie je onderaan de pagina bij ‘Status per supermarkt’ wat er misging.

## Optie A: gratis en automatisch via GitHub (aanbevolen)

1. Maak een gratis account op github.com en maak een nieuwe repository, bijvoorbeeld `aanbiedingen`. Kies **Public** (GitHub Pages is gratis voor publieke repositories; bij een betaald account kan het ook privé).
2. Upload alle bestanden uit deze map naar de repository (knop *Add file → Upload files*). Let op dat de verborgen map `.github` meekomt; sleep anders `.github/workflows/update.yml` los erin.
3. Ga naar **Settings → Pages** en kies bij *Source* voor **GitHub Actions**.
4. Ga naar **Settings → Actions → General** en zet onder *Workflow permissions* **Read and write permissions** aan.
5. Ga naar het tabblad **Actions**, kies **Aanbiedingen bijwerken** en klik **Run workflow**. Na een paar minuten staat je site op `https://<jouw-gebruikersnaam>.github.io/aanbiedingen/`.

Daarna draait hij vanzelf op maandag- en woensdagochtend. De tijden pas je aan in `.github/workflows/update.yml`.

**Let op:** sommige supermarkten blokkeren bezoekers vanaf servers in datacenters (zoals die van GitHub). Zie je bij een winkel steeds ‘Ophalen mislukt’ terwijl hij lokaal wel werkt, gebruik dan optie B voor die winkel, of voor alles.

## Optie B: op je eigen computer of een Raspberry Pi

Je hebt Python 3.10 of nieuwer nodig.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r scraper/requirements.txt
python -m playwright install chromium

python scraper/scrape.py           # aanbiedingen ophalen
python -m http.server -d site 8000 # open daarna http://localhost:8000
```

Automatisch elke maandag en woensdag om 7:00 (Linux/Mac, via `crontab -e`):

```
0 7 * * 1,3 cd /pad/naar/supermarkt-aanbiedingen && .venv/bin/python scraper/scrape.py
```

Op Windows doe je hetzelfde met Taakplanner. Wil je de site toch online, laat de cronjob na het scrapen `git commit` en `git push` doen naar je GitHub-repository en zet in de workflow de stap ‘Haal aanbiedingen op’ uit.

## Supermarkten aanpassen

In `scraper/stores.json` staat per supermarkt de pagina-URL, kleur en of hij aan staat (`"enabled": true/false`). Vomar en Poiesz staan standaard uit; zet ze aan als ze bij jou in de buurt zitten. Extra opties per winkel:

- `"scrolls": 20` laat hij verder naar beneden scrollen als een site aanbiedingen pas bij scrollen inlaadt (standaard 12).
- `"cents": true` als een site prijzen in centen opslaat (je ziet dan prijzen als € 199,00).
- `"min_offers": 20` bepaalt vanaf hoeveel gevonden aanbiedingen hij de terugvaloptie overslaat (standaard 8).

## Als een supermarkt niet (goed) werkt

Supermarkten veranderen hun websites regelmatig. Ik heb deze scraper niet tegen de echte sites kunnen testen, dus reken erop dat je bij de eerste keer voor een paar winkels iets moet bijstellen. Zo pak je dat aan:

1. Controleer of de URL in `stores.json` nog naar de aanbiedingenpagina wijst. Open hem in je browser; verwijst hij door, neem dan de nieuwe URL over.
2. Draai die ene winkel in debugmodus en kijk mee:
   ```bash
   python scraper/scrape.py --only jumbo --debug --headful
   ```
3. In `debug/jumbo/` vind je een screenshot, de HTML van de pagina, alle opgevangen data (`response_*.json`) en wat hij herkende (`offers.json`). Op het screenshot zie je meteen of er bijvoorbeeld een cookiemelding of een blokkade in de weg zat.
4. Zit er rommel tussen (bijvoorbeeld menu-items) of mist er iets, stuur dan het `response_*.json`-bestand met de aanbiedingen erin naar Claude, met de vraag een specifieke uitleesfunctie voor die winkel te schrijven.

## Netjes blijven

Gebruik dit voor jezelf. De scraper bezoekt elke site maar twee keer per week en één winkel tegelijk, en dat moet je zo houden. Lees de gebruiksvoorwaarden van de supermarkten; sommige verbieden automatisch uitlezen. Publiceer de verzamelde gegevens niet als dienst voor anderen. Prijzen en acties kunnen per filiaal verschillen; de winkel zelf is leidend.
