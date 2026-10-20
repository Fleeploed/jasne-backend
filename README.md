# Jasně — Document intelligence backend

Jasně helps residents of Czechia understand official letters, bills, contracts, and other administrative documents. It converts a photograph or PDF into a plain-language explanation, practical next steps, and deadlines, while keeping follow-up questions in the context of the original document.

This repository contains the **Python/FastAPI backend** for the [Jasně mobile application](https://github.com/Fleeploed/jasne-frontend). It brings together OCR, AI analysis, retrieval-augmented generation (RAG), authenticated document storage, subscriptions, and scheduled reminders. The project is an MVP under active development.

## What the backend does

- Processes images and PDFs and streams scan progress through Server-Sent Events.
- Extracts document text with Azure Document Intelligence, then classifies and analyzes it with OpenAI models.
- Retrieves category-specific reference material from PostgreSQL with pgvector.
- Produces structured summaries, actions, and validated deadlines.
- Supports streamed follow-up chat for each document, with subscription and usage checks.
- Manages scans, deadlines, user profiles, push tokens, and account deletion with a recovery period.
- Serves multilingual help content and supports OAMP immigration application status lookup and tracking.
- Synchronizes subscription status through RevenueCat webhooks and runs scheduled notification and tracking jobs.

## Processing pipeline

1. Authenticate the request and validate ownership of uploaded storage paths.
2. Check input size, document pages, request limits, and usage allowances.
3. Extract text using Azure Document Intelligence.
4. Classify the document and retrieve relevant reference material with pgvector.
5. Route the analysis to the configured model and validate the structured result, including deadlines.
6. Charge usage atomically, persist the result, and initialize the document conversation and reminders.

Concrete dates, amounts, and obligations must come from the uploaded document. Retrieved reference material provides background context. Deadline validation helps prevent invalid dates from being saved or displayed.

## Technology

| Area | Stack |
| --- | --- |
| API | Python, FastAPI, Pydantic, Uvicorn |
| OCR | Azure Document Intelligence |
| AI | OpenAI API, category-specific prompts and model routing |
| Database and retrieval | Supabase PostgreSQL, pgvector |
| Identity and storage | Supabase Auth, private Storage, row-level security |
| Streaming | Server-Sent Events |
| External integrations | RevenueCat, Expo Push, DeepL, Playwright for OAMP lookup |
| Deployment | Docker, Railway, GitHub Actions |
| Quality checks | pytest, pytest-asyncio, Ruff |

## My contribution

Jasně is my personal project. I designed and developed the mobile application for explaining Czech official documents, and built its Python backend with FastAPI.

My work includes:

- Designing the REST API and integrating the mobile application with the backend.
- Integrating OCR and language models for document extraction, classification, and analysis.
- Implementing a retrieval-augmented generation (RAG) pipeline using PostgreSQL and pgvector to retrieve relevant reference information.

## About the developer

I am [Alisher Ibrayev](https://www.linkedin.com/in/alisher-ibrayev), a software engineer with experience in process automation, AI integrations, and Python backend development. My professional work includes UiPath automation, API integrations, Azure AI services, and automated evaluation of AI-agent responses. Jasně is my independent project applying these interests to document processing and a mobile user experience.

## Repository structure

```text
app/                   API routers, models, services, prompts, and workers
supabase/migrations/   Database schema and access policies
help_content/          Help categories and bundled content
help-admin/            Static help-content administration interface
scripts/               Knowledge loading, content seeding, and smoke checks
tests/                 Unit and API tests
```

## Local development

Use Python 3.12 and a dedicated virtual environment.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Fill `.env` with credentials for your own Supabase, OpenAI, and Azure resources. Apply the migrations in `supabase/migrations/` to your development database and configure the private `documents` storage bucket. Optional integrations have additional settings in [`.env.example`](.env.example).

```sh
uvicorn app.main:app --reload
```

Use `/health` for liveness and `/docs` for the API reference when enabled outside production. OAMP lookup additionally requires the Playwright Chromium browser:

```sh
python -m playwright install chromium
```

The help administration interface has its own [configuration instructions](help-admin/README.md).

## Validation

```sh
ruff check app tests
ruff format --check app tests
pytest
```

## Credentials and access control

Real credentials belong in local `.env` files, deployment environment variables, or GitHub Actions secrets. Never commit service keys, webhook credentials, database passwords, private signing keys, or access tokens. Templates contain placeholders only.

User requests are authenticated with Supabase JWTs. Access is checked in API handlers and database policies. Internal jobs use a separate cron secret; webhook authentication and subscription checks are enforced on the server. The static help admin uses only a public Supabase client key and requires an authenticated administrator account.
