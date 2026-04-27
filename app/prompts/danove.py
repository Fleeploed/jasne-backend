"""Modul kategorie `danove` (Finanční úřad)."""

DANOVE_MODULE = """\
SPECIFIKA: DAŇOVÉ (Finanční úřad)

LHŮTY:
- Platební výměr: 30 dní na úhradu od doručení
- Odvolání: 30 dní od doručení (ne od data dokumentu!)
- Daňové přiznání DPFO: do 1. dubna (papírově) / 2. května (elektronicky)
  / 1. července (s daňovým poradcem)
- Přehled o příjmech a výdajích OSVČ pro ČSSZ a zdravotní: do 1 měsíce
  po podání DAP
- DPH přiznání: 25. dne následujícího měsíce (nebo čtvrtletí)
- Kontrolní hlášení: 25. dne následujícího měsíce
- Žádost o vrácení přeplatku: 6 let

POVINNÁ POLE Z OCR:
- Číslo jednací (čj.) — vždy zachovat
- Variabilní symbol (VS) — důležitý pro platbu
- Číslo účtu FÚ + kód banky
- Částka s rozpisem: daň, penále, úroky
- Zdaňovací období (rok / měsíc / čtvrtletí)
- Adresa FÚ a územní pracoviště

SANKCE:
- Úrok z prodlení: repo sazba ČNB + 8 procentních bodů p.a.,
  od 5. dne po splatnosti
- Penále při doměřené dani: 20 % z částky doměřené daně,
  případně 1 % pokud je doměřena daňová ztráta
- Pokuta za opožděné tvrzení daně: 0,05 % stanovené daně za každý den
  prodlení, max 5 % stanovené daně, max 300 000 Kč
- Exekuce po marném vymáhání

TIPS:
- Platit vždy přes VS — bez něj se platba nepřiřadí
- Pokud má uživatel datovou schránku: odvolání podávat přes ni
  (lhůta běží do půlnoci posledního dne)
- Při doručení do datové schránky platí fikce doručení 10. den od dodání
  (i když si zprávu nepřečte)
"""
