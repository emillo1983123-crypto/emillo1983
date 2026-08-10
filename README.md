# Kodi Lektor PL

Publiczne repozytorium dodatku **Kodi Lektor PL** autorstwa **emillo1983**.
Dodatek czyta tekstowe napisy filmu głosem ElevenLabs bez Termuksa i ma
domyślnie włączony filtr rodzinny łagodzący wulgaryzmy.
Projekt jest rozwijany pod marką **KWPJ LABS**; provider dodatku pozostaje
oznaczony jako **emillo1983**.

## Instalacja w Kodi 20 lub 21

1. Pobierz [pakiet repozytorium Kodi](https://emillo1983123-crypto.github.io/emillo1983/repository.subtitle.tts.pl-1.0.7.zip).
2. W Kodi włącz `Ustawienia → System → Dodatki → Nieznane źródła`.
3. Wybierz `Dodatki → Zainstaluj z pliku ZIP` i wskaż pobrany plik.
4. Wybierz `Zainstaluj z repozytorium → Lektor PL — repozytorium emillo1983`.
5. Zainstaluj **Kodi Lektor PL**.
6. Otwórz ustawienia dodatku i wpisz własny klucz API ElevenLabs.

Po jednorazowej instalacji repozytorium Kodi sprawdza `addons.xml` na GitHub
i może automatycznie instalować kolejne wersje dodatku.

## Jak zdobyć klucz API ElevenLabs

Każdy użytkownik dodatku powinien korzystać z własnego klucza:

1. Załóż darmowe konto na [elevenlabs.io](https://elevenlabs.io).
2. Otwórz `Developers → API Keys` albo przejdź bezpośrednio do
   [strony kluczy API](https://elevenlabs.io/app/settings/api-keys).
3. Wybierz `Create API Key` i nazwij klucz `Kodi Lektor PL`.
4. Nadaj kluczowi uprawnienie `Text to Speech`.
5. Opcjonalnie ustaw niski limit kredytów.
6. Skopiuj klucz od razu — pełny klucz jest wyświetlany tylko raz — i wklej
   go w ustawieniach dodatku.

Nie udostępniaj klucza nikomu. API jest dostępne w planie darmowym, ale każde
generowanie mowy zużywa kredyty. Repozytorium ani dodatek nie zawierają
wspólnego klucza API.

## Oszczędzanie kredytów ElevenLabs

Domyślny model Flash zużywa mniej kredytów na znak niż Multilingual v2.
Włączony domyślnie tryb oszczędny lokalnie usuwa zbędne etykiety mówców,
wypełniacze, jąkanie i bezpośrednie powtórzenia. Nie usuwa negacji, liczb ani
nazw. Gotowa kwestia jest zapisywana w cache i przy powtórzeniu nie obciąża
ponownie API. Tryb można wyłączyć, aby używać szerszego kontekstu sąsiednich
kwestii kosztem mniejszej liczby trafień cache.

## Automatyczne polskie napisy

1. Z oficjalnego repozytorium Kodi zainstaluj dostawcę napisów, np.
   OpenSubtitles.com, Napiprojekt.pl albo Napisy24.pl.
2. Otwórz menu **Kodi Lektor PL** i wybierz
   `Skonfiguruj automatyczne polskie napisy`.
3. Wskaż dostawcę. Dodatek ustawi język polski oraz pobieranie jego najwyżej
   ocenionego wyniku dla filmu lub odcinka.

Dostawca może wymagać osobnego konta. Dodatek nie wyszukuje automatycznie
napisów dla telewizji na żywo ani PVR.

## Ustawienia dźwięku

W `Ustawienia → System → Dźwięk`:

- wyłącz przekazywanie dźwięku (passthrough),
- ustaw `Odtwarzaj dźwięki GUI` na `Zawsze`.

Kodi nie może mieszać głosu lektora z surowym strumieniem passthrough.

## Obsługiwane napisy

Wersja 0.8.0 obsługuje tekstowe napisy SRT, VTT, ASS/SSA i MicroDVD.
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
