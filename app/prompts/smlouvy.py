"""Modul kategorie `smlouvy` (nájemní, pracovní, úvěrové, pojistné, kupní).

Smlouvy jsou specifické — POUZE KRITICKÉ lhůty (konec smlouvy, výpověď,
zvýšení platby), NE měsíční platby. Jinak uživatel utopí v push notifikacích.
"""

SMLOUVY_MODULE = """\
SPECIFIKA: SMLOUVY (nájemní, pracovní, úvěrové, pojistné, kupní)

DŮLEŽITÝ ROZDÍL:
- Extrahuj POUZE kritické lhůty:
  * Datum konce smlouvy (datum ukončení / platnost do)
  * Výpovědní lhůta (kdy nejpozději vypovědět)
  * Konec fixní úrokové sazby (refixace u úvěrů)
  * Změna výše nájemného / sazby / pojistného
  * Možnost odstoupení (typicky 14 dní u spotřebitelských smluv)
- NEVYTVÁŘEJ deadliny pro pravidelné platby (měsíční nájem, splátky)
  — uživatel je zná

POVINNÁ POLE Z OCR:
- Strany smlouvy (pronajímatel/nájemce, zaměstnavatel/zaměstnanec, atd.)
- Datum platnosti od / do
- Výpovědní lhůta a její běh (typicky od 1. dne následujícího měsíce)
- Sankce za porušení (smluvní pokuta, úrok z prodlení)
- Číslo smlouvy (důležité pro reklamace)

LHŮTY PODLE TYPU:
- Nájem bytu: výpověď nájemce kdykoli s 3měsíční lhůtou
  (od 1. dne následujícího měsíce). Pronajímatel jen ze zákonných důvodů.
- Pracovní smlouva: výpověď s 2měsíční lhůtou (od 1. dne následujícího
  měsíce). Dohoda o ukončení kdykoli. Zkušební doba max 3 měsíce.
- Úvěr / hypotéka: refixace (výpověď úvěru bez sankce 3 měsíce před
  koncem fixace), předčasné splacení (do 25 % ročně bez sankce)
- Pojištění: výpověď k výročí s 6týdenní lhůtou. Změna pojistitele
  u povinného ručení do 6 týdnů po obnově.
- Spotřebitelská smlouva (na dálku, mimo provozovnu): odstoupení do 14 dnů

SANKCE:
- Smluvní pokuta — uvedena ve smlouvě (max 0,5 % denně z dlužné částky
  podle judikatury)
- Úrok z prodlení — smluvní nebo zákonný (repo + 8 % p.a.)

TIPS:
- Vždy zaslat výpověď doporučeně nebo přes datovou schránku — bez důkazu
  o doručení neplatí
- U nájmu bytu zákon chrání nájemce — nelze ze dne na den vypovědět
"""
