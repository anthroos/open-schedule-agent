# Social Posts — open-schedule-agent launch

## Facebook (UA)

Зробив опенсорс-альтернативу Calendly — тільки замість форми, люди пишуть боту в Телеграм як живій людині.

Як це працює для гостя: пишеш боту, він питає ім'я, email, тему зустрічі, показує вільні слоти з Google Calendar. Обираєш час — бот створює подію в календарі з Google Meet лінком. Можна додати ще 1-2 учасників, їм теж прийде інвайт.

Як це працює для власника: свій розклад теж налаштовуєш через чат з ботом. Пишеш йому "Додай понеділок 10-18", "Заблокуй суботу", "Додай вівторок і четвер 11:00, 14:00, 16:00" — він розуміє природну мову і зберігає правила. Не треба лізти ні в який дашборд. Коли хтось букає зустріч — тобі прилітає нотифікація в Телеграм з деталями.

Тобто обидві сторони спілкуються з AI — і гість, і власник календаря.

Під капотом: Python, Google Calendar API, Anthropic/OpenAI tool use, SQLite, FastAPI. Є Web API, MCP сервер для AI-агентів, Docker, деплой на Railway. Працює з Claude, GPT або локальним Ollama.

MIT ліцензія. 70 тестів. Документація англійською. Можна розгорнути за 15 хвилин якщо вмієш в Python.

Буду радий фідбеку — особливо якщо спробуєте розгорнути у себе.

https://github.com/anthroos/open-schedule-agent


## Telegram (UA)

Виклав в опенсорс свого AI scheduling бота — альтернатива Calendly, але через живий діалог в Телеграмі.

Для гостя: пише боту, AI збирає ім'я, email, тему, показує вільні слоти, букає зустріч в Google Calendar з Meet лінком. Можна додати інших учасників.

Для власника: розклад налаштовується теж через чат з ботом. Пишеш "Додай понеділок 10-18", "Заблокуй п'ятницю після 15:00" — бот розуміє і зберігає. Ніяких дашбордів. Коли хтось букає — приходить нотифікація з деталями.

Обидві сторони працюють через розмову з AI.

LLM: Claude / GPT / Ollama. Є Web API, MCP сервер, Docker, Railway. MIT, 70 тестів, документація англійською.

Хто хоче потестити — розгортайте, README детальний. Цікавить фідбек: чи вдалось накатити, що не вистачає.

github.com/anthroos/open-schedule-agent


## Twitter / X (EN)

I open-sourced my AI scheduling agent — a conversational alternative to Calendly.

Instead of a booking form, guests chat with a Telegram bot. AI collects name, email, topic, shows available slots from Google Calendar, and books a meeting with a Meet link.

The owner side is conversational too — you set your schedule by chatting: "Add Monday 10-18", "Block Saturday". No dashboard needed.

Both sides talk to AI. That's the whole point.

Python, Google Calendar API, Anthropic/OpenAI tool use, SQLite. Supports Claude, GPT, or local Ollama. MIT license, 70 tests.

github.com/anthroos/open-schedule-agent
