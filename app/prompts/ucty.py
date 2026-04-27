"""Modul kategorie `ucty` (faktury, utility, telco, SVJ vyúčtování)."""

UCTY_MODULE = """\
SPECIFIKA: ÚČTY / FAKTURY / UTILITY

LHŮTY:
- Splatnost faktury: uvedena explicitně (typicky 14 dní)
- Upomínka první: zákonná lhůta neexistuje, smluvní 7-14 dní
- Upomínka druhá / předžalobní výzva: 7 dní od první
- Reklamace vyúčtování energií: do 30 dnů od obdržení
- Reklamace bankovních služeb: do 13 měsíců od provedení transakce
- Vyúčtování SVJ (společenství vlastníků): do 30. 4. následujícího roku
- Reklamace SVJ vyúčtování: do 5 měsíců od obdržení
- Změna dodavatele energií: bez poplatku po 1. roce smlouvy
  s výpovědní lhůtou 30 dní

POVINNÁ POLE Z OCR:
- Číslo faktury / variabilní symbol (VS)
- Částka celkem (s DPH a bez DPH zvlášť)
- IBAN nebo číslo účtu + kód banky
- Datum vystavení a datum splatnosti
- Dodavatel: název, IČO, DIČ, adresa
- Odběratel: název, IČO/RČ, adresa
- Předmět fakturace (rozpis položek)
- U energií: období spotřeby, EAN/EIC kód odběrného místa

SANKCE:
- Úrok z prodlení smluvní: uveden ve smlouvě (často 0,05-0,1 % denně)
- Úrok z prodlení zákonný: repo sazba ČNB + 8 procentních bodů p.a.
- Upomínkový poplatek: uveden ve smlouvě (typicky 100-500 Kč)
- Náklady na vymáhání pohledávky: dle vyhl. 484/2000 Sb.
- Odpojení energií / služeb: po druhé bezúspěšné upomínce

TIPS:
- Vždy platit přes VS — bez něj může být platba ztracena
- Při sporu o vyúčtování: zaplatit nespornou část, písemně reklamovat zbytek
- U energií jde reklamace přes dodavatele (ne distributora)
"""
