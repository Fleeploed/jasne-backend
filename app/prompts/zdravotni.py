"""Modul kategorie `zdravotni` (VZP, OZP, ČPZP, RBP, ZPMV, VoZP)."""

ZDRAVOTNI_MODULE = """\
SPECIFIKA: ZDRAVOTNÍ POJIŠTĚNÍ

LHŮTY:
- Pojistné OSVČ: do 8. dne následujícího měsíce
- Přehled OSVČ pro zdravotní pojišťovnu: do 1 měsíce po podání DAP
- Doplatek pojistného OSVČ: do 8 dní po podání přehledu
- Změna zdravotní pojišťovny:
  * lze měnit jen 1× za 12 měsíců
  * efektivní vždy 1.1. nebo 1.7.
  * žádost podat do 31.3. (pro 1.7.) nebo do 30.9. (pro 1.1.)
- Odvolání proti výměru pojistného: 30 dní od doručení
- Hlášení nového narozeného dítěte: do 8 dnů (zákonný zástupce)

POVINNÁ POLE Z OCR:
- Číslo pojištěnce (rodné číslo bez lomítka)
- Kód zdravotní pojišťovny (111 VZP, 201 VoZP, 205 ČPZP,
  207 OZP, 209 ZPMV, 211 RBP, 213 ZPMV)
- Variabilní symbol pro platbu
- Číslo účtu pojišťovny
- Vyměřovací základ a sazba 13,5 %
- Minimální záloha OSVČ pro daný rok

SANKCE:
- Penále z dlužného pojistného: 0,05 % za každý den prodlení
- Pokuta za neoznámení změn (zaměstnání, OSVČ, EU): až 10 000 Kč
- Exekuce dlužného pojistného přes celní úřad
- Při dlouhodobém dluhu: regulační poplatky / omezení péče

TIPS:
- Při souběhu zaměstnání + OSVČ stačí minimální záloha jako OSVČ
- Při změně pojišťovny: nemůže odmítnout (povinnost přijetí všech)
- Cizinci s pobytem v ČR mají povinnost se pojistit do 8 dnů od příjezdu
"""
