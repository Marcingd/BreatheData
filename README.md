# BreatheData

Zaplecze danych dla tarczy Garmin **Breathe**. Pobiera prognozy jakości powietrza
i pyłków z CAMS (Copernicus), przelicza je na indeksy, pakuje w siatkę po jednym
bajcie na wartość i serwuje przez Cloudflare Worker w formacie identycznym
z Open-Meteo.

```
CAMS / Atmosphere Data Store  ->  GitHub Actions (co 6 h)  ->  Cloudflare KV  ->  Worker  ->  zegarek
```

**Po co to istnieje:** dane CAMS są na licencji CC-BY i wolno ich używać
komercyjnie, ale darmowy endpoint Open-Meteo jest tylko do użytku
niekomercyjnego. Ten pipeline zdejmuje tamto ograniczenie bez abonamentu.

## Koszt

Zero, w darmowych limitach:

| Element | Limit darmowy | Zużycie |
|---|---|---|
| GitHub Actions (repo publiczne) | bez limitu minut | 4 przebiegi dziennie |
| Cloudflare KV zapisy | 1000 / dobę | 52 / dobę |
| Cloudflare KV odczyty | 100 000 / dobę | 1 na zegarek na godzinę |
| Cloudflare Workers | 100 000 zapytań / dobę | ~4000 zegarków |
| Atmosphere Data Store | darmowy | 4 zapytania dziennie |

## Format siatki

Domena CAMS dla Europy (30..72 N, -25..45 E) przerzedzona do 0,25 stopnia,
czyli 169 x 281 komórek. Jeden plik na godzinę, 464 kB.

Nagłówek 28 B, potem 10 bajtów na komórkę: europejski AQI, US AQI, PM2.5, PM10
i sześć pyłków. Stężenia są kwantowane logarytmicznie, więc jeden bajt pokrywa
zakres od 0,05 do 39 000 z błędem poniżej 1 procenta.

Stałe formatu żyją w dwóch miejscach naraz, w `src/grid.py` i `worker/src/decode.js`.
Pilnuje tego `worker/test/decode.test.mjs`, który dekoduje plik zapisany przez
Pythona i porównuje wartość w wartość.

## Uruchomienie od zera

### 1. Atmosphere Data Store

1. Załóż konto na <https://ads.atmosphere.copernicus.eu>.
2. Wejdź na stronę zbioru `cams-europe-air-quality-forecasts` i **zaakceptuj
   licencję** (bez tego API odmawia pobrania).
3. Skopiuj Personal Access Token z profilu.

### 2. Cloudflare

```bash
npm install -g wrangler
wrangler login
wrangler kv namespace create GRID
```

Wklej zwrócone `id` do `worker/wrangler.toml`, a potem:

```bash
cd worker && wrangler deploy
```

Zapamiętaj adres, który wypisze wrangler, na przykład
`https://breathe-data.twoj-login.workers.dev`.

Do wgrywania danych z Actions potrzebny jest jeszcze token API z uprawnieniem
**Workers KV Storage: Edit** (My Profile → API Tokens).

### 3. GitHub

Repozytorium **publiczne**, żeby minuty Actions były darmowe. W Settings →
Secrets and variables → Actions dodaj:

| Sekret | Skąd |
|---|---|
| `ADS_KEY` | token z ADS |
| `CF_ACCOUNT_ID` | Cloudflare, prawa kolumna panelu |
| `CF_KV_NAMESPACE_ID` | `id` z `wrangler kv namespace create` |
| `CF_API_TOKEN` | token z uprawnieniem Workers KV Storage: Edit |

Odpal workflow `publish CAMS grids` ręcznie (Actions → Run workflow) i sprawdź
log. Pierwszy przebieg pokazuje nazwy zmiennych znalezione w pliku CAMS, więc
od razu widać, czy odczyt trafił we właściwe pola.

Kontrola: `curl https://twoj-worker.workers.dev/health`

### 4. Tarcza

W `Breathe/source/AirModel.mc` podmień jedną stałą:

```monkeyc
const URL = "https://breathe-data.twoj-login.workers.dev/v1/air";
```

Nic więcej się nie zmienia, bo Worker oddaje ten sam kształt odpowiedzi
co Open-Meteo.

## Testy

```bash
python tests/test_pipeline.py                                  # indeksy i format
node worker/test/decode.test.mjs                               # zgodność koder/dekoder
python build.py --synthetic --out out --now 2026-08-01T14      # przebieg bez ADS
node worker/test/worker.test.mjs                               # odpowiedź dla zegarka
```

Tryb `--synthetic` pomija ADS i generuje powtarzalne dane zastępcze, więc cała
droga od kodera po odpowiedź HTTP daje się sprawdzić bez kluczy.

## Zasięg

Na razie tylko Europa, bo pyłki CAMS liczy wyłącznie dla domeny europejskiej.
Poza nią Worker oddaje puste pola, a tarcza pokazuje `--` i `POLLEN N/A`.
Objęcie świata samym AQI wymaga drugiego zbioru
(`cams-global-atmospheric-composition-forecasts`) i drugiej siatki.

## Licencja danych

Dane: **CAMS European air quality forecasts**, Copernicus Atmosphere Monitoring
Service, licencja CC-BY 4.0. Przy publikacji tarczy w Connect IQ Store atrybucja
musi znaleźć się w opisie aplikacji.
