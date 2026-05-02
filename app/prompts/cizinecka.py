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

POTVRZENÍ O PODÁNÍ ŽÁDOSTI:
- Pokud `TYP DOKUMENTU` nebo OCR říká "potvrzení o podání žádosti",
  jde o potvrzení, že žádost už byla podána. Nejde o výzvu k doložení.
- V takovém případě:
  - `summary`: vysvětli, jaká žádost byla podána podle OCR
    (např. prodloužení doby platnosti zaměstnanecké karty).
  - `actions_required`: typicky jen sledovat stav řízení / uschovat potvrzení.
  - `documents_needed`: [] — žádné obecné přílohy původní žádosti neuváděj.
  - `penalty`: null, pokud OCR neobsahuje konkrétní výzvu, lhůtu nebo sankci.
  - `tips`: můžeš zmínit, že potvrzení samo o sobě není povolením k pobytu
    a je vhodné mít platný cestovní doklad, pokud to OCR obsahuje.
- Nikdy z RAG nedoplňuj u potvrzení obecné přílohy jako ubytování, pracovní
  smlouvu nebo pojištění. Ty patří do původní žádosti, ne do tohoto potvrzení.

TIPS:
- Lhůta pro odvolání 15 dní je krátká — okamžitě jednat
- Při zastavení řízení lze podat novou žádost (nový poplatek 2 500 Kč),
  ale mezi tím nesmí být přepobyt
- Datum doručení = datum převzetí (osobně), nebo 10. den fikce
  (datová schránka, pošta uložená)
- Při výzvě k doložení vždy vyžadovat potvrzení o doručení odpovědi
"""
