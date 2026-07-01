# INPUT: чат Битрикс24 chat147573 (номера исследований текстом)
# OUTPUT: ClickHouse, таблица defect (accession_number, reason, entered_at)
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

import requests
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg

# === Настройки ===
BITRIX_WEBHOOK = cfg.BITRIX_WEBHOOK
DIALOG_ID = "chat147573"
POLL_INTERVAL = 5    # секунд — опрос чата
FLUSH_INTERVAL = 180  # секунд — пачечная запись буфера в ClickHouse

CH_HOST_TARGET     = cfg.CH_HOST_TARGET
CH_PORT_TARGET     = cfg.CH_PORT_TARGET
CH_USER_TARGET     = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET
CH_TABLE_NAME = 'defect'

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defect_bot_state.json")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Группы причин снятия, внутри каждой — категории
GROUPS = {
    "1": ("Зависло НПКЦ", {
        "1": "распределено на врача",
        "2": "описано",
        "3": "Другое",
    }),
    "2": ("Зависло ИАЦ", {
        "1": "заявка отсутствует в списках для описания",
        "2": "невозможно взять в работу (отсутствует кнопка)",
        "3": "Другое",
    }),
    "3": ("Зависло МО", {
        "1": "переснято под другим назначением",
        "2": "отсутствуют снимки",
        "3": "ПИН",
        "4": "Другое",
    }),
    "4": ("Прочее", {
        "1": "Травма",
        "2": "Другое",
    }),
}

GROUPS_MENU = "\n".join(f"{k} — {v[0]}" for k, v in GROUPS.items())


def category_menu(group_key: str) -> str:
    _, categories = GROUPS[group_key]
    return "\n".join(f"{k} — {v}" for k, v in categories.items())

# Маркер, по которому отличаем собственные сообщения бота (чтобы не реагировать на них)
BOT_TAG = " [бот]"


# ---------------------------------------------------------------------------
# Bitrix24 helpers
# ---------------------------------------------------------------------------

def bx(method: str, params: dict | None = None) -> dict:
    url = f"{BITRIX_WEBHOOK.rstrip('/')}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Bitrix API [{method}]: {e}")
        return {}


def bx_send(text: str) -> int:
    result = bx("im.message.add", {"DIALOG_ID": DIALOG_ID, "MESSAGE": text + BOT_TAG})
    return int(result.get("result") or 0)


def bx_get_new_messages(last_msg_id: int) -> list[dict]:
    """Возвращает новые сообщения (id > last_msg_id), от старых к новым."""
    data = bx("im.dialog.messages.get", {"DIALOG_ID": DIALOG_ID, "LIMIT": 20})
    result = data.get("result", {})
    messages = result.get("messages", []) if isinstance(result, dict) else []
    new_messages = [m for m in messages if int(m.get("id", 0)) > last_msg_id]
    new_messages.sort(key=lambda m: int(m.get("id", 0)))
    return new_messages


def is_bot_message(text: str) -> bool:
    return text.strip().endswith(BOT_TAG.strip())


# ---------------------------------------------------------------------------
# ClickHouse
# ---------------------------------------------------------------------------

def ensure_table_exists() -> None:
    client = clickhouse_connect.get_client(
        host=CH_HOST_TARGET, port=CH_PORT_TARGET,
        username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
        database=CH_DATABASE_TARGET, secure=True, verify=False,
    )
    try:
        client.command(f"""
            CREATE TABLE IF NOT EXISTS {CH_DATABASE_TARGET}.{CH_TABLE_NAME} (
                accession_number String,
                reason String DEFAULT '',
                category String DEFAULT '',
                entered_at DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY accession_number
        """)
        client.command(f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_NAME} ADD COLUMN IF NOT EXISTS reason String DEFAULT ''")
        client.command(f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_NAME} ADD COLUMN IF NOT EXISTS category String DEFAULT ''")
        client.command(f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_NAME} ADD COLUMN IF NOT EXISTS entered_at DateTime DEFAULT now()")
        logger.info(f"Таблица {CH_TABLE_NAME} проверена/создана.")
    finally:
        client.close()


def flush_buffer(state: dict) -> None:
    """Пачечно записывает накопленный буфер в ClickHouse. Соединение открывается только здесь."""
    buffer = state.get("buffer", [])
    if not buffer:
        return
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )

        # Если номер уже есть в базе — затираем старую запись, чтобы вставить актуальную
        accession_numbers = list({row["accession_number"] for row in buffer})
        numbers_list = ", ".join(f"'{n}'" for n in accession_numbers)
        client.command(
            f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_NAME} "
            f"DELETE WHERE accession_number IN ({numbers_list})"
        )

        client.insert(
            table=f"{CH_DATABASE_TARGET}.{CH_TABLE_NAME}",
            data=[
                [
                    row["accession_number"],
                    row["reason"],
                    row["category"],
                    datetime.strptime(row["entered_at"], "%Y-%m-%d %H:%M:%S"),
                ]
                for row in buffer
            ],
            column_names=["accession_number", "reason", "category", "entered_at"],
        )
        client.close()
        logger.info(f"Записано в ClickHouse пачкой: {len(buffer)} строк.")
        state["buffer"] = []
    except Exception as e:
        logger.error(f"Ошибка пачечной записи в ClickHouse (буфер сохранён, попробуем позже): {e}")


# ---------------------------------------------------------------------------
# Парсинг номеров исследований
# ---------------------------------------------------------------------------

ACCESSION_RE = re.compile(r'^[AaАа]?\d{4,20}$')


def parse_text_input(text: str) -> list[str]:
    """Извлекает только чисто цифровые токены длиной от 4 знаков (номера исследований).
    Отбрасывает служебный текст, BBCode ([USER=...]) и короткие цифры (это ответы 1-6)."""
    raw_items = text.replace(',', ' ').replace('\n', ' ').split()
    candidates = (item.strip() for item in raw_items if item.strip())
    return list(dict.fromkeys(item for item in candidates if ACCESSION_RE.match(item)))


def is_system_message(msg: dict) -> bool:
    """Системные сообщения (приглашение в чат и т.п.) содержат BBCode [USER=...]."""
    text = msg.get("text") or msg.get("message") or ""
    return bool(re.search(r'\[USER=\d+\]|\[/USER\]', text))


# ---------------------------------------------------------------------------
# Состояние (очередь номеров, ожидающих причину)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {
        "last_msg_id": 0,
        "queue": [],
        "current": None,
        "current_stage": None,
        "current_group": None,
        "bootstrapped": False,
        "buffer": [],
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ask_current(state: dict) -> None:
    """Если есть номер в очереди и не задан текущий вопрос — спрашивает группу."""
    if state["current"] is None and state["queue"]:
        state["current"] = state["queue"].pop(0)
        state["current_stage"] = "group"
        state["current_group"] = None
        bx_send(f"Исследование {state['current']}: выберите группу —\n{GROUPS_MENU}")
        logger.info(f"Спросил группу для {state['current']}")


# ---------------------------------------------------------------------------
# Главный цикл
# ---------------------------------------------------------------------------

def process_message(text: str, state: dict) -> None:
    text = text.strip()
    if not text or is_bot_message(text):
        return

    # Ожидаем ответ-цифру на текущий вопрос (сначала группа, потом категория)
    if state["current"] is not None:
        stage = state.get("current_stage")

        if stage == "group":
            if text in GROUPS:
                state["current_group"] = text
                state["current_stage"] = "category"
                group_name = GROUPS[text][0]
                bx_send(f"Группа «{group_name}» выбрана. Теперь укажите категорию —\n{category_menu(text)}")
                logger.info(f"Группа для {state['current']}: {group_name}")
                return
            numbers = parse_text_input(text)
            if numbers:
                state["queue"].extend(n for n in numbers if n not in state["queue"] and n != state["current"])
                bx_send(f"Добавил {len(numbers)} номеров в очередь. Сначала выберите группу по текущему: {state['current']}")
            else:
                bx_send(f"Пожалуйста, выберите группу цифрой:\n{GROUPS_MENU}")
            return

        if stage == "category":
            group_key = state["current_group"]
            group_name, categories = GROUPS[group_key]
            if text in categories:
                accession_number = state["current"]
                category = categories[text]
                state.setdefault("buffer", []).append({
                    "accession_number": accession_number,
                    "reason": group_name,
                    "category": category,
                    "entered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                bx_send(
                    f"✅ {accession_number} — {group_name} / {category} "
                    f"(запишется в базу в течение {FLUSH_INTERVAL // 60} мин.)"
                )
                logger.info(f"В буфер: {accession_number} -> {group_name} / {category}")
                state["current"] = None
                state["current_stage"] = None
                state["current_group"] = None
                ask_current(state)
                return
            numbers = parse_text_input(text)
            if numbers:
                state["queue"].extend(n for n in numbers if n not in state["queue"] and n != state["current"])
                bx_send(f"Добавил {len(numbers)} номеров в очередь. Сначала выберите категорию по текущему: {state['current']}")
            else:
                bx_send(f"Пожалуйста, выберите категорию цифрой:\n{category_menu(group_key)}")
            return

    # Нет текущего вопроса — пытаемся распарсить новые номера
    numbers = parse_text_input(text)
    if not numbers:
        return
    state["queue"].extend(n for n in numbers if n not in state["queue"])
    logger.info(f"В очередь добавлено {len(numbers)} номеров")
    ask_current(state)


def main() -> None:
    logger.info("Бот запущен (поллинг Битрикс24, чат %s)...", DIALOG_ID)
    ensure_table_exists()
    state = load_state()
    state.setdefault("buffer", [])

    elapsed_since_flush = 0

    while True:
        try:
            new_messages = bx_get_new_messages(state["last_msg_id"])
            for msg in new_messages:
                state["last_msg_id"] = max(state["last_msg_id"], int(msg.get("id", 0)))

                if not state["bootstrapped"]:
                    # При первом запуске не обрабатываем историю, только запоминаем последний id
                    continue

                if is_system_message(msg):
                    continue

                text = msg.get("text") or msg.get("message") or ""
                process_message(text, state)

            if not state["bootstrapped"]:
                state["bootstrapped"] = True
                logger.info("Бутстрап завершён, last_msg_id=%s", state["last_msg_id"])

            elapsed_since_flush += POLL_INTERVAL
            if elapsed_since_flush >= FLUSH_INTERVAL:
                flush_buffer(state)
                elapsed_since_flush = 0

            save_state(state)
        except Exception as e:
            logger.error(f"Ошибка цикла поллинга: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
