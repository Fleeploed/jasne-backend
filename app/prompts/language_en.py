"""Analyzer language contract for English output."""

EN_LANGUAGE_MODULE = """\
LANGUAGE PROMPT: ENGLISH

Write the user-facing output in English.

JSON fields:
- `document_type`: technical ASCII slug, for example `potvrzeni_o_podani_zadosti`.
- `document_type_cz`: Czech document type name. Keep this field in Czech.
- `document_type_localized`: English document type name.
- `summary`: English.
- `translation`: English plain-language translation/explanation.
- `deadlines[].description`: English.
- `actions_required`: English.
- `penalty`: English.
- `where_to_go`: English, keeping Czech institution names and addresses unchanged.
- `documents_needed`: English, keeping official Czech document names in parentheses when useful.
- `tips`: English.

Do not answer in Czech just because the OCR document or category rules are Czech.
Keep Czech bureaucracy terms such as `datová schránka`, `OAMP`, `číslo jednací`,
and `platební výměr` in Czech when they are official terms, but explain them in English.
"""
