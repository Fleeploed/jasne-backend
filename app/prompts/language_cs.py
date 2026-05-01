"""Analyzer language contract for Czech output."""

CS_LANGUAGE_MODULE = """\
JAZYKOVÝ PROMPT: ČEŠTINA

Piš výstup pro uživatele česky.

Pole JSON:
- `document_type`: technický slug bez diakritiky, např. `potvrzeni_o_podani_zadosti`.
- `document_type_cz`: český název typu dokumentu.
- `document_type_localized`: null.
- `summary`: česky.
- `translation`: null.
- `deadlines[].description`: česky.
- `actions_required`: česky.
- `penalty`: česky.
- `where_to_go`: česky.
- `documents_needed`: česky.
- `tips`: česky.

Pokud je OCR dokument v jiném jazyce, přesto odpovídej uživateli česky.
"""
