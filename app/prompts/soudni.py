"""Modul kategorie `soudni` (soudy, exekuce, insolvence, platební rozkaz).

Eskalovaná kategorie — krátké lhůty, vážné následky, doporučujeme advokáta.
"""

SOUDNI_MODULE = """\
SPECIFIKA: SOUDNÍ ŘÍZENÍ (soudy, exekutor, insolvence)

KRITICKÉ LHŮTY (krátké, nevynechat!):
- Odpor proti platebnímu rozkazu: 15 dnů od doručení
  (jediná možnost, jak zrušit rozkaz — bez odporu nabývá právní moci)
- Odvolání proti rozsudku: 15 dnů od doručení písemného vyhotovení
- Námitka proti směnečnému / šekovému platebnímu rozkazu: 8 dnů!
- Návrh na zastavení exekuce: kdykoli, ale rozhodnutí trvá
- Návrh na povolení splátek v exekuci: kdykoli, ale exekutor nemusí přijmout
- Insolvenční návrh + návrh na povolení oddlužení: kdykoli
- Žaloba pro zmatečnost: 3 měsíce od doručení rozhodnutí
- Dovolání: 2 měsíce od doručení rozhodnutí odvolacího soudu

POVINNÁ POLE Z OCR:
- Spisová značka (sp. zn.) — KRITICKÁ pro každou další komunikaci
- Soud / exekutorský úřad (název, adresa)
- Identifikace stran (žalobce, žalovaný, dlužník, věřitel)
- Předmět řízení (žalobní nárok, dlužná částka)
- Datum doručení (počátek běhu lhůty)
- Pokyn k plnění (kam, kolik, do kdy, VS)
- U exekuce: pověření soudního exekutora, zástavní právo

SANKCE A NÁSLEDKY:
- Bez odporu / odvolání → rozhodnutí v právní moci, exekuce
- Náklady řízení: soudní poplatek + náklady advokáta protistrany
  (typicky desítky tisíc Kč)
- Náklady exekuce: minimálně 7 700 Kč + 15 % z vymáhané částky
- Odměna exekutora: dle exekutorského tarifu
- Zákaz dispozice s majetkem (mobiliární, nemovitostní exekuce)
- Srážky ze mzdy / dávek nad nezabavitelnou částku
- Zápis do insolvenčního rejstříku, registr dlužníků

TIPS:
- KAŽDÝ soudní dokument otevřít okamžitě — lhůty běží od doručení
- Datum doručení = převzetí osobně, nebo 10. den fikce
  (datová schránka, pošta uložená 10 dní)
- Při doručení do datové schránky platí fikce 10. den od dodání,
  i když si zprávu uživatel nepřečte
- Vždy doporučit konzultaci s advokátem — chyba ve lhůtě je nevratná
- Bezplatnou pomoc: Česká advokátní komora, Probační a mediační služba
"""
