# Kodi Lektor PL

Publiczne repozytorium dodatku **Kodi Lektor PL** od **KWPJ LABS**.
Wersja 1.0.0 przygotowuje tekstowe polskie napisy i przekazuje je do darmowej,
zgodnej usługi TTS zainstalowanej w Kodi lub na urządzeniu. Nie wymaga klucza
ElevenLabs ani nie wysyła tekstu do płatnego API.

## Instalacja w Kodi 20 lub 21

1. Pobierz [pakiet repozytorium Kodi](https://emillo1983123-crypto.github.io/emillo1983/repository.subtitle.tts.pl-1.0.10.zip).
2. W Kodi włącz `Ustawienia → System → Dodatki → Nieznane źródła`.
3. Wybierz `Dodatki → Zainstaluj z pliku ZIP` i wskaż pobrany plik.
4. Wybierz `Zainstaluj z repozytorium → Lektor PL — repozytorium emillo1983`.
5. Zainstaluj **Kodi Lektor PL**.
6. Zainstaluj zgodną usługę TTS Kodi, wybierz w niej polski głos i w menu
   dodatku uruchom **Test darmowego głosu urządzenia**.

Po jednorazowej instalacji repozytorium Kodi sprawdza `addons.xml` na GitHub
i może automatycznie instalować kolejne wersje dodatku.

## Darmowy głos

Dodatek nie zawiera własnego silnika syntezy mowy. Korzysta z usługi TTS
zgodnej z Kodi, skonfigurowanej lokalnie na urządzeniu. Jakość i dostępność
polskiego głosu zależą od tej usługi. Gdy test dodatku zgłasza brak zgodnej
usługi, należy ją najpierw zainstalować i skonfigurować — dodatek nie udaje,
że lektor działa, gdy system nie potrafi wypowiedzieć tekstu.

## Automatyczne polskie napisy

1. Z oficjalnego repozytorium Kodi zainstaluj **OpenSubtitles.com**
   (`service.subtitles.opensubtitles-com`).
2. Otwórz menu **Kodi Lektor PL** i wybierz
   `Ustaw OpenSubtitles.com dla polskich napisów`.
3. Dodatek ustawi OpenSubtitles.com jako domyślne źródło dla filmów i seriali,
   język polski oraz automatyczne pobieranie pierwszego wyniku.
4. W ustawieniach OpenSubtitles.com zarejestruj albo zaimportuj własne konto,
   jeśli dostawca tego wymaga.

Jeżeli pierwsze wyszukiwanie nie przyniesie tekstowego pliku, dodatek wykona
jedną ponowną próbę po 45 sekundach. Po dwóch próbach zatrzymuje się, aby nie
tworzyć pętli okien. Dodatek nie odczytuje loginu ani hasła OpenSubtitles.com
i nie wyszukuje automatycznie napisów dla telewizji na żywo ani PVR.

## Ustawienia dźwięku

Głos jest odtwarzany przez wybraną usługę TTS Kodi. Jej ustawienia, w tym głos,
głośność i tempo, są nadrzędne wobec suwaka dodatku. Dla testu wyłącz
przekazywanie dźwięku (passthrough), gdy usługa TTS tego wymaga.

## Obsługiwane napisy

Wersja 1.0.0 obsługuje tekstowe napisy SRT, VTT, ASS/SSA i MicroDVD.
Napisy obrazkowe PGS, VobSub i DVD nie zawierają tekstu i nie mogą zostać
odczytane bez OCR.

## Prywatność

Dodatek 1.0.0 nie pobiera ani nie przechowuje klucza API usługi głosowej.
Konto OpenSubtitles.com, jeżeli dostawca go wymaga, jest konfigurowane wyłącznie
w jego własnym dodatku Kodi.

## Rozwój i publikacja aktualizacji

Kod dodatku znajduje się w `service.subtitle.tts.pl`, a generator repozytorium
w `kodi_repository/build_repo.ps1`. Każda aktualizacja dodatku musi mieć
wyższy numer `version`, ponieważ Kodi może zachować starszą paczkę w pamięci
podręcznej.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\kodi_repository\build_repo.ps1 `
  -LanBaseUrl "https://emillo1983123-crypto.github.io/emillo1983"
```

Projekt jest udostępniony na licencji MIT.
