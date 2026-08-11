# Kodi Lektor PL

Publiczne repozytorium dodatku **Kodi Lektor PL** autorstwa **emillo1983**.
Dodatek czyta tekstowe napisy filmu głosem ElevenLabs bez Termuksa i ma
domyślnie włączony filtr rodzinny łagodzący wulgaryzmy.
Projekt jest rozwijany pod marką **KWPJ LABS**; provider dodatku pozostaje
oznaczony jako **emillo1983**.

## Instalacja w Kodi 20 lub 21

1. Pobierz [pakiet repozytorium Kodi](https://emillo1983123-crypto.github.io/emillo1983/repository.subtitle.tts.pl-1.0.10.zip).
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
4. Nadaj kluczowi uprawnienia `Text to Speech`, `Voices Read` i `User Read`.
5. Opcjonalnie ustaw niski limit kredytów.
6. Skopiuj klucz od razu — pełny klucz jest wyświetlany tylko raz — i wklej
   go w ustawieniach dodatku.

Nie udostępniaj klucza nikomu. API jest dostępne w planie darmowym, ale każde
generowanie mowy zużywa kredyty. Repozytorium ani dodatek nie zawierają
wspólnego klucza API.

Uprawnienia są rozdzielone. `Text to Speech` wystarcza do samego czytania,
`Voices Read` jest potrzebne do przycisku wyboru głosu, a `User Read` do
licznika kredytów. Dlatego działający lektor i błąd po wejściu do listy głosów
nie oznaczają sprzeczności ani złego klucza — zwykle trzeba włączyć brakujące
uprawnienie w ustawieniach tego klucza. Warto również sprawdzić limit kredytów
i listę dozwolonych adresów IP klucza.

## Profile i tempo lektora

W ustawieniach dodatku można jednym wyborem ustawić profil narracji:
**Klasyczny — niski i spokojny**, **Ciepły dokumentalny**, **Naturalny** albo
**Dynamiczny**. Są to autorskie archetypy sposobu czytania — nie imitują głosu
ani wizerunku żadnej prawdziwej osoby. Domyślny profil klasyczny oraz tempo 95%
dają spokojniejszy, tradycyjny charakter narracji. Suwak pozwala wybrać tempo
od 70% do 120%.

API ElevenLabs nie udostępnia dodatkom osobnej, technicznej regulacji wysokości
głosu. Rzeczywiście niski ton zależy więc przede wszystkim od legalnego głosu
wybranego na koncie ElevenLabs i wpisanego w dodatku jako `Voice ID`. Profile
zmieniają sposób prowadzenia narracji, ale nie służą do kopiowania konkretnych
lektorów.

## Oszczędzanie kredytów ElevenLabs

Domyślny model Flash zużywa mniej kredytów na znak niż Multilingual v2.
Włączony domyślnie tryb oszczędny lokalnie usuwa zbędne etykiety mówców,
wypełniacze, jąkanie i bezpośrednie powtórzenia. Nie usuwa negacji, liczb ani
nazw. Gotowa kwestia jest zapisywana w cache i przy powtórzeniu nie obciąża
ponownie API. Tryb można wyłączyć, aby używać szerszego kontekstu sąsiednich
kwestii kosztem mniejszej liczby trafień cache.

## Licznik kredytów i koszt filmu

Po znalezieniu tekstowych napisów dodatek w tle pobiera oficjalny stan konta
ElevenLabs i jeden raz dla danego filmu pokazuje:

- szacowaną liczbę kredytów potrzebnych na cały plik napisów,
- stan bieżącego limitu zwrócony przez API ElevenLabs,
- szacowany limit po obejrzeniu filmu.

Ten sam raport można otworzyć ręcznie z menu dodatku. Wymaga on uprawnienia
`User Read`; brak tego uprawnienia nie zatrzymuje lektora. W modelach Flash i
Turbo planów samoobsługowych znak wysyłany przez API kosztuje bazowo około
0,5 kredytu, a w Multilingual v2 około 1 kredytu. Wynik jest oznaczony jako
szacunek: cache, pominięte kwestie, indywidualna stawka głosu i umowy
Enterprise mogą zmienić rzeczywiste zużycie.

Obecny endpoint konta ElevenLabs nadal opisuje podstawowy limit polami
`character_count` i `character_limit`. Dlatego dodatek pokazuje te wartości
jako znaki limitu, a szacowane kredyty filmu osobno. Nie miesza obu jednostek
i nie przedstawia przybliżenia jako rachunku.

Przycisk doładowania otwiera wyłącznie oficjalny panel ElevenLabs
`Developers → Top Up`. Dodatek nie pobiera kodów BLIK, danych karty, loginu ani
hasła i nie pośredniczy w płatności. Dostępne waluty, metody płatności i
ostateczna cena są zawsze ustalane i wyświetlane przez ElevenLabs.

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

W `Ustawienia → System → Dźwięk`:

- wyłącz przekazywanie dźwięku (passthrough),
- ustaw `Odtwarzaj dźwięki GUI` na `Zawsze`.

Kodi nie może mieszać głosu lektora z surowym strumieniem passthrough.

## Obsługiwane napisy

Wersja 0.9.0 obsługuje tekstowe napisy SRT, VTT, ASS/SSA i MicroDVD.
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
