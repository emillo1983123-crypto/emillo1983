# Kodi Lektor PL

Publiczne repozytorium dodatku **Kodi Lektor PL** autorstwa **emillo1983**.
Dodatek czyta tekstowe napisy filmu głosem ElevenLabs bez Termuksa i ma
domyślnie włączony filtr rodzinny łagodzący wulgaryzmy.

## Instalacja w Kodi 20 lub 21

1. Pobierz [pakiet repozytorium Kodi](https://emillo1983123-crypto.github.io/emillo1983/repository.subtitle.tts.pl-1.0.6.zip).
2. W Kodi włącz `Ustawienia → System → Dodatki → Nieznane źródła`.
3. Wybierz `Dodatki → Zainstaluj z pliku ZIP` i wskaż pobrany plik.
4. Wybierz `Zainstaluj z repozytorium → Lektor PL — repozytorium emillo1983`.
5. Zainstaluj **Kodi Lektor PL**.
6. Otwórz ustawienia dodatku i wpisz własny klucz API ElevenLabs.

Po jednorazowej instalacji repozytorium Kodi sprawdza `addons.xml` na GitHub
i może automatycznie instalować kolejne wersje dodatku.

## Ustawienia dźwięku

W `Ustawienia → System → Dźwięk`:

- wyłącz przekazywanie dźwięku (passthrough),
- ustaw `Odtwarzaj dźwięki GUI` na `Zawsze`.

Kodi nie może mieszać głosu lektora z surowym strumieniem passthrough.

## Obsługiwane napisy

Wersja 0.7.1 obsługuje tekstowe napisy SRT, VTT, ASS/SSA i MicroDVD.
Napisy obrazkowe PGS, VobSub i DVD nie zawierają tekstu i nie mogą zostać
odczytane bez OCR.

## Prywatność

Repozytorium nie zawiera klucza API. Klucz podany w Kodi jest maskowany w
interfejsie, ale Kodi zapisuje ustawienia profilu lokalnie bez szyfrowania.

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
