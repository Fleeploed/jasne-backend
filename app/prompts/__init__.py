"""Systémové prompty pro Jasně LLM pipeline.

Layout podle skill spec — jeden modul na jeden účel:

  classifier.py — call 1 (GPT-4o Vision): OCR + klasifikace
  base.py       — call 2 (analyzer): společná šablona, proměnné se vyplňují
  <kategorie>.py — call 2 add-on: pravidla pro lhůty/sankce dané kategorie
  chat.py       — Q&A nad existujícím skenem (Pro)

Všechny prompty jsou v češtině — báze znalostí (RAG chunky) i originální
dokumenty jsou v češtině, takže LLM lépe drží konzistentní terminologii.
Výstup `summary` a `translation` se generuje v jazyce uživatele
(cs/en/uk/ru) podle pole `language` v base prompt.

Prompty držíme v kódu (ne v DB), aby změny prošly code review zároveň
s úpravami JSON schémat, která musí produkovat.
"""

from app.prompts.base import BASE_SYSTEM_PROMPT
from app.prompts.chat import CHAT_SYSTEM_PROMPT
from app.prompts.cizinecka import CIZINECKA_MODULE
from app.prompts.classifier import CLASSIFIER_SYSTEM_PROMPT
from app.prompts.danove import DANOVE_MODULE
from app.prompts.ostatni import OSTATNI_MODULE
from app.prompts.skolstvi import SKOLSTVI_MODULE
from app.prompts.smlouvy import SMLOUVY_MODULE
from app.prompts.socialni import SOCIALNI_MODULE
from app.prompts.soudni import SOUDNI_MODULE
from app.prompts.ucty import UCTY_MODULE
from app.prompts.urad_prace import URAD_PRACE_MODULE
from app.prompts.zdravotni import ZDRAVOTNI_MODULE

# Lookup table — analyzer vybere modul podle category. Neznámá kategorie
# spadne na OSTATNI_MODULE (žádné automatické lhůty).
CATEGORY_MODULES: dict[str, str] = {
    "danove": DANOVE_MODULE,
    "socialni": SOCIALNI_MODULE,
    "zdravotni": ZDRAVOTNI_MODULE,
    "cizinecka": CIZINECKA_MODULE,
    "smlouvy": SMLOUVY_MODULE,
    "ucty": UCTY_MODULE,
    "soudni": SOUDNI_MODULE,
    "urad_prace": URAD_PRACE_MODULE,
    "skolstvi": SKOLSTVI_MODULE,
    "ostatni": OSTATNI_MODULE,
}

__all__ = [
    "BASE_SYSTEM_PROMPT",
    "CATEGORY_MODULES",
    "CHAT_SYSTEM_PROMPT",
    "CLASSIFIER_SYSTEM_PROMPT",
]
