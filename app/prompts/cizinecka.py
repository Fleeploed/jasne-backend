"""Modul kategorie `cizinecka` (OAMP / MV ČR)."""

CIZINECKA_MODULE = """\
SPECIFIKA: CIZINECKÁ AGENDA (OAMP / MV ČR)

LHŮTY:
- Žádost o prodloužení pobytu: nejpozději 14 dní před skončením
  (lze podat až 90 dní před skončením)
- Odpověď OAMP na běžné řízení: 60 dní od podání žádosti
- Odpověď OAMP na složité řízení: až 180 dní (nutné usnesení o prodloužení)
- Výzva k doložení: termín uvedený v dokumentu (typicky 8-30 dní)
  — nesplnění = zastavení řízení
- Odvolání proti rozhodnutí: 15 dní od doručení (Komise pro rozhodování
  ve věcech pobytu cizinců)
- Nahlášení změny adresy: do 30 dnů od změny
- Žádost o občanství: po splnění zákonných podmínek (typicky 5-10 let
  trvalého pobytu)

POVINNÁ POLE Z OCR:
- Číslo jednací (čj.)
- Typ pobytu (přechodný, trvalý, dlouhodobý, krátkodobé vízum)
- Účel pobytu (zaměstnání, podnikání, studium, sloučení rodiny)
- Datum platnosti průkazu / víza
- Číslo cestovního dokladu (pasu)
- Územní pracoviště OAMP
- Termín splnění výzvy (pokud jde o výzvu k doložení)

SANKCE:
- Pobyt po lhůtě (přepobyt): správní vyhoštění, zákaz vstupu až 3 roky
- Neoprávněný pobyt: pokuta až 100 000 Kč + správní vyhoštění
- Práce bez povolení: pokuta až 100 000 Kč zaměstnanci,
  až 10 mil. Kč zaměstnavateli
- Nesplnění výzvy: zastavení řízení, ztráta zaplaceného poplatku

TIPS:
- Lhůta pro odvolání 15 dní je krátká — okamžitě jednat
- Při zastavení řízení lze podat novou žádost (nový poplatek 2 500 Kč),
  ale mezi tím nesmí být přepobyt
- Datum doručení = datum převzetí (osobně), nebo 10. den fikce
  (datová schránka, pošta uložená)
- Při výzvě k doložení vždy vyžadovat potvrzení o doručení odpovědi
"""
