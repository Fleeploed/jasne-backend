"""Modul kategorie `socialni` (ČSSZ — Česká správa sociálního zabezpečení)."""

SOCIALNI_MODULE = """\
SPECIFIKA: SOCIÁLNÍ (ČSSZ)

LHŮTY:
- Přehled o příjmech a výdajích OSVČ: do 1 měsíce po podání DAP
  (typicky do 1. května, s poradcem do 1. srpna)
- Doplatek pojistného OSVČ: do 8 dní po podání přehledu
- Měsíční zálohy OSVČ: do 8. dne následujícího měsíce
- Žádost o důchod: kdykoli po dosažení důchodového věku, doporučeno
  3-4 měsíce předem
- Hlášení nemoci: do 8 dnů od počátku PN (zaměstnanec přes zaměstnavatele,
  OSVČ přímo na ČSSZ pokud platí nemocenské pojištění)
- Mateřská (PPM): žádost lze podat 8-6 týdnů před plánovaným nástupem
- Otcovská: žádost do 6 týdnů od narození / převzetí dítěte
- Odvolání proti rozhodnutí ČSSZ: 30 dní od doručení

POVINNÁ POLE Z OCR:
- Variabilní symbol (typicky rodné číslo nebo IČO)
- Číslo účtu ČSSZ + kód banky
- Období (rok / měsíc), za které se platí
- Vyměřovací základ
- Sazby: 29,2 % důchodové, 2,1 % nemocenské (OSVČ dobrovolné),
  1,2 % státní politika zaměstnanosti
- Adresa OSSZ / PSSZ podle místa bydliště

SANKCE:
- Penále z dlužného pojistného: 0,05 % za každý kalendářní den prodlení
- Pokuta za nepodání přehledu: až 50 000 Kč
- Pokuta za neoznámení změn: až 20 000 Kč
- Při dlouhodobém neplacení: exekuce, ztráta nároku na nemocenskou

TIPS:
- Datum splatnosti pojistného OSVČ je vždy 8. den (i víkend / svátek)
- Změna výše záloh se podává přes nový přehled za předchozí rok
- Při souběhu zaměstnání + OSVČ se platí ze zaměstnání primárně
"""
