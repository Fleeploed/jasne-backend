# Jasně — Help Admin

Одностраничный веб-админ для управления статьями раздела «Помощь» в мобильном приложении Jasně. Один HTML-файл, без билда, без сервера. Деплой — drag-and-drop на Vercel / Netlify / любой статический хостинг.

## Что менять в коде

Откройте [`index.html`](./index.html) и замените два значения в самом верху `<script>`-блока:

```js
const SUPABASE_URL = "https://YOUR-PROJECT.supabase.co";
const SUPABASE_ANON_KEY = "PASTE_ANON_KEY_HERE";
```

Это **anon key** (он же `SUPABASE_ANON_KEY` / `EXPO_PUBLIC_SUPABASE_ANON_KEY` из остальных частей проекта). Anon key публичный по дизайну — безопасность держится на RLS. **Никогда не вставляйте сюда `service_role` ключ.**

## Подготовка БД

1. Применить миграцию `supabase/migrations/0011_help_admin.sql` (через Supabase CLI или вручную в SQL Editor):
   - создаёт `help_categories` и `help_guides`,
   - добавляет колонку `profiles.role`,
   - включает RLS и политики (читать опубликованное — всем; писать — только админам),
   - сеет 6 категорий MVP.

2. Повысить себя до админа. В Dashboard → Authentication → Users найти свой `User UID` и выполнить в SQL Editor:

   ```sql
   update profiles set role = 'admin' where id = '<ваш-uid>';
   ```

3. (По желанию, но рекомендуется) Перенести 8 существующих YAML-гайдов в БД, чтобы мобильное приложение не осталось без контента после переключения `help.py`:

   ```bash
   python -m scripts.seed_help_guides_from_yaml
   ```

   Скрипт использует `SUPABASE_SERVICE_KEY` из `.env` и upsert-ит на `(slug, language)` — повторный запуск безопасен.

## Локальный запуск

Открыть файл двойным кликом — `file://…/index.html` работает напрямую, без локального сервера. Логин через Supabase Auth.

## Деплой

- **Vercel**: `vercel --prod` из папки `help-admin/`, либо drag-and-drop в Vercel Dashboard.
- **Netlify**: drag-and-drop папки `help-admin/` в Deploys.
- **Cloudflare Pages**: подключить ветку, root = `help-admin/`.

Никаких build-команд, переменных окружения и фреймворков не нужно.

## Безопасность в двух предложениях

Anon-ключ публичный — он же лежит в мобильном приложении. Любой пишущий запрос упирается в RLS-политику `is_help_admin()`, которая проверяет `profiles.role = 'admin'` для текущего JWT, поэтому без админ-аккаунта ни вставить, ни обновить, ни удалить ничего нельзя, даже если кто-то откроет devtools.

## Жизненный цикл контента

«Опубликовать → мгновенно в мобильном приложении». Бэкенд (`app/routers/help.py`) при каждом запросе читает опубликованные статьи прямо из Postgres, поэтому redeploy не нужен. Черновики (`status = draft`) видны только админу через эту же админку и в API не попадают.

## Что админ НЕ редактирует

Метаданные категорий — `hero`, `fallback`, `useful_actions`, `guides_order` — продолжают жить в [`help_content/categories.yaml`](../help_content/categories.yaml). Они меняются раз в полгода и не входят в ежедневный поток редактирования. Если нужно поправить — это правка через git, как раньше.
