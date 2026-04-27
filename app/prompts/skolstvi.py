"""Modul kategorie `skolstvi` (školy, univerzity, stipendia)."""

SKOLSTVI_MODULE = """\
SPECIFIKA: ŠKOLSTVÍ (MŠ, ZŠ, SŠ, VŠ, stipendia)

LHŮTY:
- Zápis do mateřské školy: typicky 2.-16. května (záleží na zřizovateli)
- Zápis do 1. třídy ZŠ: 1.-30. dubna
- Přihláška na střední školu: do 1. března (i jarní termíny SCIO/CERMAT)
- Přihláška na vysokou školu: typicky do 28. února
  (některé fakulty do 31. března — nutno zkontrolovat)
- Zápis ke studiu VŠ: termín v rozhodnutí o přijetí (typicky srpen-září)
- Žádost o stipendium (sociální, prospěchové, ubytovací):
  termíny stanoví fakulta / zákon
- Odvolání proti nepřijetí ke studiu: 15 dnů od doručení rozhodnutí
- Studijní průměr a podmínky postupu: kontroluje se vždy ke konci
  akademického roku
- Žádost o uznání předchozího vzdělání (nostrifikace): kdykoli

POVINNÁ POLE Z OCR:
- Název školy / fakulty / katedry
- IZO / RED-IZO školy (pro středoškolské)
- Datum a místo přijímacích zkoušek
- Studijní obor / program
- Termíny zápisu / nástupu
- Bankovní spojení pro platbu školného (u soukromých / platby studia
  v cizím jazyce)

SANKCE A NÁSLEDKY:
- Nedodržení termínu přihlášky → ztráta termínu / nástupu
- Nezápis ke studiu → ztráta nároku na přijetí
- Neuhrazení školného (soukromé / studium v cizím jazyce) →
  vyloučení ze studia
- Nesplnění studijních povinností → ukončení studia, povinnost
  zaplatit školné při překročení standardní doby studia + 1 rok

TIPS:
- Termíny zápisů jsou pro zřizovatele, mohou se mírně lišit
  (kontrola na webu konkrétní školy / fakulty)
- Při neúspěchu u přijímaček lze podat odvolání i bez nového výsledku
  (děkan má diskreci přijmout v rámci kapacity)
"""
