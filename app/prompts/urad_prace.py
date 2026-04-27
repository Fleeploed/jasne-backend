"""Modul kategorie `urad_prace` (ÚP ČR — Úřad práce)."""

URAD_PRACE_MODULE = """\
SPECIFIKA: ÚŘAD PRÁCE (ÚP ČR)

LHŮTY:
- Žádost o podporu v nezaměstnanosti: do 3 dnů po skončení zaměstnání
  (registrace jako uchazeč o zaměstnání)
- Doložení rozhodných skutečností: termín uvedený ve výzvě (typicky 15 dnů)
- Oznámení změn (zaměstnání, brigáda, pobyt v zahraničí, nemoc):
  do 8 dnů od změny
- Žádost o příspěvek na bydlení: kdykoli, vyplácí se za měsíce zpětně max 3
- Žádost o rodičovský příspěvek: do 4 let věku dítěte
- Žádost o porodné: do 1 roku od narození dítěte
- Žádost o pohřebné: do 1 roku od pohřbu
- Odvolání proti rozhodnutí ÚP: 15 dnů od doručení (k Ministerstvu práce
  a sociálních věcí)

POVINNÁ POLE Z OCR:
- Spisová značka / číslo jednací
- Pracoviště ÚP (krajská pobočka, kontaktní pracoviště)
- Druh dávky (podpora, příspěvek na bydlení, hmotná nouze, atd.)
- Období, za které se vyplácí
- Výše dávky
- Bankovní spojení pro výplatu

SANKCE:
- Vyřazení z evidence uchazečů o zaměstnání:
  * 3 měsíce při neoznámení změn
  * 6 měsíců při odmítnutí vhodného zaměstnání
- Vrácení neoprávněně vyplacené dávky + sankce
- Pokuta za zatajení rozhodných skutečností: až 200 000 Kč
- Trestní oznámení při úmyslném zneužití dávek

TIPS:
- Po skončení pracovního poměru vždy hned jít na ÚP, jinak bez podpory
- Doba trvání podpory: 5 měsíců (do 50 let), 8 měsíců (50-55 let),
  11 měsíců (nad 55 let)
- Výše podpory: prvních 2 měsíce 65 %, další 2 měsíce 50 %, zbytek 45 %
  z průměrného čistého výdělku (max 0,58násobek průměrné mzdy)
"""
