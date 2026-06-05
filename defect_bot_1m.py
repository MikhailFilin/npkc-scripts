import os
import sys
import logging
import threading
from io import BytesIO

import pandas as pd
import clickhouse_connect
import telebot
import telebot.apihelper
from telebot import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg

# === Настройки Telegram-бота ===
TELEGRAM_TOKEN = cfg.BOT_TOKEN_DEFECT

# === Прокси ===
TELEGRAM_PROXY = cfg.TELEGRAM_PROXY
if TELEGRAM_PROXY:
    telebot.apihelper.proxy = {'https': TELEGRAM_PROXY, 'http': TELEGRAM_PROXY}
    logging.info(f"Прокси настроен: {TELEGRAM_PROXY}")

# === Настройки ClickHouse ===
CH_HOST_TARGET    = cfg.CH_HOST_TARGET
CH_PORT_TARGET    = cfg.CH_PORT_TARGET
CH_USER_TARGET    = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET
CH_TABLE_NAME = 'defect'

# Инициализация логгера
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создание таблицы при старте (если не существует)
def ensure_table_exists(client):
    create_query = f"""
    CREATE TABLE IF NOT EXISTS {CH_DATABASE_TARGET}.{CH_TABLE_NAME} (
        accession_number String
    ) ENGINE = MergeTree()
    ORDER BY accession_number
    """
    client.command(create_query)
    logger.info(f"Таблица {CH_TABLE_NAME} проверена/создана.")

# Обработка текста
def parse_text_input(text: str):
    raw_items = text.replace(',', ' ').replace('\n', ' ').split()
    return list(set(item.strip() for item in raw_items if item.strip()))

# Обработка Excel-файла
def parse_excel_file(file_bytes: bytes):
    try:
        df = pd.read_excel(BytesIO(file_bytes), header=None)
        if df.empty or df.shape[0] == 0:
            return []
        first_col = df.iloc[:, 0].dropna().astype(str).str.strip()
        return first_col[first_col != ''].tolist()
    except Exception as e:
        logger.error(f"Ошибка при чтении Excel: {e}")
        return []

# Запись в ClickHouse
def insert_into_clickhouse(client, accession_numbers: list):
    if not accession_numbers:
        return 0
    unique_numbers = list(set(accession_numbers))
    data = [[num] for num in unique_numbers]
    client.insert(
        table=f"{CH_DATABASE_TARGET}.{CH_TABLE_NAME}",
        data=data,
        column_names=['accession_number']
    )
    return len(unique_numbers)

# Инициализация клиента ClickHouse при запуске
def init_clickhouse_client():
    client = clickhouse_connect.get_client(
        host=CH_HOST_TARGET,
        port=CH_PORT_TARGET,
        username=CH_USER_TARGET,
        password=CH_PASSWORD_TARGET,
        database=CH_DATABASE_TARGET,
        secure=True,
        verify=False
    )
    ensure_table_exists(client)
    logger.info("ClickHouse клиент инициализирован.")
    return client

# Создание бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ch_client = init_clickhouse_client()

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "Привет! Отправьте:\n"
        "• Текст с номерами (через запятую, пробел или перенос строки), или\n"
        "• Excel-файл (.xlsx/.xls) с номерами в первом столбце.\n"
        "Я добавлю их в таблицу defect в ClickHouse."
    )

# Обработчик текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text
    numbers = parse_text_input(text)
    if not numbers:
        bot.reply_to(message, "Не удалось извлечь номера. Попробуйте снова.")
        return

    try:
        inserted = insert_into_clickhouse(ch_client, numbers)
        bot.reply_to(message, f"✅ Добавлено {inserted} уникальных номеров в таблицу defect.")
    except Exception as e:
        logger.error(f"Ошибка вставки: {e}")
        bot.reply_to(message, "❌ Ошибка при записи в базу. Проверьте логи.")

# Обработчик документов
@bot.message_handler(content_types=['document'])
def handle_document(message):
    document = message.document
    if not document.file_name.lower().endswith(('.xlsx', '.xls')):
        bot.reply_to(message, "Пожалуйста, отправьте Excel-файл (.xlsx или .xls).")
        return

    try:
        file_info = bot.get_file(document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        numbers = parse_excel_file(downloaded_file)

        if not numbers:
            bot.reply_to(message, "Файл пуст или не содержит данных в первом столбце.")
            return

        inserted = insert_into_clickhouse(ch_client, numbers)
        bot.reply_to(message, f"✅ Из Excel добавлено {inserted} уникальных номеров в таблицу defect.")
    except Exception as e:
        logger.error(f"Ошибка обработки файла: {e}")
        bot.reply_to(message, "❌ Ошибка при обработке файла.")

# Запуск бота
if __name__ == '__main__':
    logger.info("Бот запущен...")
    
    # Функция для безопасной остановки бота
    def stop_bot_safely():
        logger.info("Инициировано завершение работы после 1 минуты...")
        bot.stop_polling()
    
    # Запускаем таймер на 180 секунд (3 минуты)
    threading.Timer(180.0, stop_bot_safely).start()
    
    # Основной цикл обработки
    bot.infinity_polling()
    
    # Завершение работы
    ch_client.close()
    logger.info("Соединение с ClickHouse закрыто.")
    logger.info("Бот успешно завершил работу.")