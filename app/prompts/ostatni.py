"""Modul kategorie `ostatni` (fallback / neznámá kategorie).

Lhůty se nevytváří automaticky — kategorie je příliš neurčitá.
Skill: „category === 'ostatni' → auto NE, jen návrh na ruční přidání".
"""

OSTATNI_MODULE = """\
SPECIFIKA: OSTATNÍ (neurčitý / neznámý dokument)

PRAVIDLA:
- Dej obecný přehled obsahu — co dokument je a od koho
- Zkus uhodnout, čeho se týká (i když to není v 9 hlavních kategoriích)
- NEVYTVÁŘEJ deadliny automaticky — uživatel je přidá ručně, pokud potřebuje
  → deadlines = []
- V poli `tips` napiš návrh: „Pokud v dokumentu vidíš důležité datum,
  přidej připomenutí ručně přes ikonu kalendáře."
- NEVYMÝŠLEJ pravidla pro sankce typická pro známé kategorie

CO DĚLAT:
- Pokud je dokument zjevně z některé z 9 hlavních kategorií, ale klasifikátor
  ho označil jako ostatni kvůli low confidence — řekni uživateli, že
  doporučujeme přefoceni v lepším světle
- Pokud je dokument cizojazyčný (ne česky), přelož klíčové body do {language}
- Pokud je obsah krátký (úryvek, fragment) — řekni, že chybí kontext
"""
