import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime, timedelta # Импортирован timedelta
import requests
# === Импорты ===
import clickhouse_connect
import psycopg2
from sqlalchemy import create_engine, text
import pandas as pd
import personal_config as cfg

# === Настройки ClickHouse ===
CH_HOST = cfg.CH_HOST
CH_PORT = cfg.CH_PORT
CH_USER = cfg.CH_USER
CH_PASSWORD = cfg.CH_PASSWORD
CH_DATABASE = cfg.CH_DATABASE
# === Настройки PostgreSQL ===
PG_HOST = cfg.PG_HOST
PG_PORT = cfg.PG_PORT
PG_USER = cfg.PG_USER
PG_PASSWORD = cfg.PG_PASSWORD
PG_DATABASE = cfg.PG_DATABASE
PG_SCHEMA = cfg.PG_SCHEMA
# Таблица остаётся с тем же названием
PG_TABLE_WORKLOAD = f'{PG_SCHEMA}.workload_komet_week'
# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD
# Координаты
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y

# === Импорт модуля уведомлений ===
from ntfy_notifier import send_ntfy_alert

# === Настройки ntfy ===
NTFY_LOG_TOPIC = "my_caop_bot_alerts_2026"  # Топик для логов
NTFY_DASHBOARD_TOPIC = "push_mrc_dashboards_7895"  # Топик для финальных уведомлений о дашборде

# === Импорт для записи статуса ===
sys.path.append(r'L:\bot') # Путь к папке с status_tracker.py
# Проверим, существует ли модуль, прежде чем импортировать
try:
    from status_tracker import record_script_completion, record_script_error
    STATUS_TRACKER_AVAILABLE = True
except ImportError:
    print("⚠️ Модуль status_tracker не найден. Запись статуса отключена.")
    STATUS_TRACKER_AVAILABLE = False
    def record_script_completion(*args, **kwargs): pass
    def record_script_error(*args, **kwargs): pass

# === Функция: Отправка сообщения в ntfy для логов ===
def send_log_ntfy_message(message: str):
    send_ntfy_alert(message=message, title="Workload Week Log", priority="default", tags="log")

# === Функция: Отправка сообщения о дашборде в ntfy ===
def send_dashboard_ntfy_message(message: str):
    """Отправляет сообщение в ntfy для дашборда (в финальный топик)."""
    send_ntfy_alert(message=message, title="Dashboard Update", priority="high", tags="chart_with_upwards_trend", topic_override=NTFY_DASHBOARD_TOPIC)

# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_log_ntfy_message("🔄 Запуск VPN клиента...")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f" PID: {process.pid}")
    except Exception as e:
        error_msg = f"❌ Ошибка запуска VPN: {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg)
        raise
    time.sleep(15)
    # Активация окна (логика может потребовать адаптации)
    windows = pyautogui.getWindowsWithTitle('')
    activated = False
    for window in windows:
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"✅ Окно '{window.title}' активировано.")
                activated = True
                break
            except:
                pass # Игнорируем ошибки активации для отдельного окна
    if not activated:
        print("⚠️ Не удалось активировать окно.")
    # Ввод пароля и подключение
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y)
    time.sleep(0.5)
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y) # Повторный клик на всякий случай
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.press('delete')
    time.sleep(0.5)
    for char in VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1) # Пауза между вводом символов
    time.sleep(1)
    pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
    time.sleep(15) # Ждем подключения
    print("✅ Подключение к VPN инициировано.")
    send_log_ntfy_message("✅ Подключение к VPN инициировано.")

# === Функция: Отключение от VPN ===
def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_log_ntfy_message("🛑 Начинаю отключение от VPN...")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 Отключение завершено.")
    send_log_ntfy_message("🛑 Отключение от VPN выполнено.")

# === Функция: Создание таблицы для данных загрузки ===
def create_workload_table():
    """Создание схемы и таблицы workload_komet_week в PostgreSQL с новой структурой"""
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            # Проверяем, существует ли схема
            schema_check = text(f"SELECT schema_name FROM information_schema.schemata WHERE schema_name = '{PG_SCHEMA}';")
            result = conn.execute(schema_check)
            if not result.fetchone():
                print(f"Schema '{PG_SCHEMA}' does not exist. Creating it...")
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA};"))
                conn.commit()
                print(f"Schema '{PG_SCHEMA}' created successfully.")
            else:
                print(f"Schema '{PG_SCHEMA}' already exists.")
            # Проверяем, существует ли таблица
            table_check = text(f"""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = '{PG_SCHEMA}' AND table_name = 'workload_komet_week';
            """)
            result = conn.execute(table_check)
            if result.fetchone():
                print(f"Table '{PG_TABLE_WORKLOAD}' already exists.")
            else:
                print(f"Table '{PG_TABLE_WORKLOAD}' does not exist. Creating it...")
                create_table_query = f"""
                CREATE TABLE {PG_TABLE_WORKLOAD} (
                    god INTEGER,
                    nedelya INTEGER,
                    ae_title TEXT,
                    corrected_device_type TEXT,
                    conduct_mo_name TEXT,
                    conduct_mu_name TEXT,
                    tip_uchrezhdeniya TEXT,
                    vid_uchrezhdeniya TEXT,
                    dnei_otrabotano INTEGER,
                    chasov_za_nedelyu INTEGER,
                    nativ INTEGER,
                    s_ku INTEGER,
                    issledovanii_za_nedelyu INTEGER,
                    dni_po_normativu INTEGER,
                    "Fakt" NUMERIC, -- Используем двойные кавычки, так как Fakt - ключевое слово в SQL
                    "Plan" NUMERIC, -- Используем двойные кавычки, так как Plan - ключевое слово в SQL
                    zagruzka NUMERIC -- Загрузка в процентах
                )
                """
                conn.execute(text(create_table_query))
                conn.commit()
                print(f"Table '{PG_TABLE_WORKLOAD}' created successfully.")
        print(f"✅ Схема {PG_SCHEMA} и таблица {PG_TABLE_WORKLOAD} проверены/созданы.")
        return True
    except Exception as e:
        error_msg = f"❌ Ошибка создания схемы/таблицы workload: {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg)
        return False

# === Функция: Экспорт данных о загрузке ===
def export_workload_komet_week():
    """Загрузка новых данных о загрузке из v_instrumental_examinations в PostgreSQL"""
    print("📊 Загрузка новых данных о загрузке (workload_komet_week)...")
    send_log_ntfy_message("📊 Загрузка новых данных о загрузке (workload_komet_week)...")

    # Создаем таблицу перед началом работы (пропускаем в режиме extract_only)
    if not _extract_only_week:
        if not create_workload_table():
            return False

    # Подключение к VPN
    try:
        connect_vpn()
    except Exception as e:
        send_log_ntfy_message(f"❌ Ошибка VPN: {e}")
        return False

    client = None
    try:
        # Подключение к ClickHouse через VPN
        client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
                                              database=CH_DATABASE, secure=True, verify=False)
        print("✅ ClickHouse (Workload) подключён.")

        # === ПОЛНОСТЬЮ НОВЫЙ ЗАПРОС ===
        query = """WITH
spravochnik_protsedur AS (
    SELECT 'КТ брюшной полости и малого таза с контрастом' AS research_name, 'Компьютерная томография' AS research_subtype_name, 'КТ' AS device_type, 'С КУ' AS nativ_s_ku
    UNION ALL SELECT 'КТ голеностопного сустава', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ органов грудной клетки с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ поясн.-крестц. и копчикового отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ почек и мочевыводящих путей', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ головы', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ мягких тканей шеи', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ головы и шеи с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ плечевого сустава', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ надпочечников с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ органов брюшной полости с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ коленных суставов с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ височно-нижнечелюстных суставов', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ челюстно-лицевой области', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ голени', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ грудной клетки, брюшной полости, малого таза', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография бр.аорты и арт. нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ почек и мочевыводящих путей с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ околоносовых пазух с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ плеча', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ бедра с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'НДКТ органов грудной клетки скрининг рака легкого', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ костей таза с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ глазниц', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ челюсти', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'НДКТ околоносовых пазух', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ височной кости', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ органов брюшной полости и малого таза', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография интракраниальных сосудов', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ груд. отдела позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'НДКТ грудной клетки медицинских работников', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография сосудов шеи', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ орг. груд. кл., брюш. пол., мал. таза контр.', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ шейного отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография артерий нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ головы и шеи', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ органов малого таза с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ пояснично-крестцового отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ головного мозга с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ грудного отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ мягких тканей шеи с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ головы с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ органов малого таза', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография брюшной аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ органов грудной клетки', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ височно-нижнечелюстного сустава', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ локтевого сустава', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ шейн. отдела позвоночника контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ лицевого отдела черепа', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ коленных суставов', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ лучезапястного сустава', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ гортани с фонацией с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ грудного, пояснично-крестц. отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ плеча с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ поясн.-крестц. и копчикового отд. позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ головного мозга', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ гортани с фонацией', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ надпочечников', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ бедра', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография артерий шеи', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ кисти', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ предплечья', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ стопы', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ лицевого отдела черепа с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ тазобедренных суставов', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ околоносовых пазух', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ шейного и грудного отделов позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ поясн.-крестц. отдела позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ-ангиография сосудов нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ-ангиография грудного отдела аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ головы, шеи, грудной клетки с контр.', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ крестцового и копчикового отделов позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ голени с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ костей таза', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ-ангиография нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ предплечья с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ грудного, пояснично-крестц. отд. позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ-ангиография артерий верхних конечностей', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ глазниц с контрастом', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'КТ шейн., грудн., поясн.-крестц. отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив'
    UNION ALL SELECT 'КТ челюстно-лицевой области с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'Ангиопульмонография', 'Компьютерная томография', 'КТ', 'С КУ'
    UNION ALL SELECT 'МРТ предстательной железы', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МР-артериография экстракраниальных артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ коленного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ мягких тканей лиц. отдела черепа с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ ротоглотки и полости рта с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга, шейн., груд. отд. позв. с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ шейного отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ грудного отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ пояснично-крестцового сплетения', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ мосто-мозжечкового угла', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ органов малого таза', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ плечевого сплетения', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ мосто-мозжечкового угла с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МР- венография интракраниальных вен и синусов', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ грудного отдела позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ коленного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга, шейного отд. позв. с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ мочевого пузыря с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МР-холангиопанкреатография', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ спинного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ прямой кишки', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ височно-нижнечелюстных суставов', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МР-артериография интракр. артерий с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ лучезапястного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МР- артериогр. и веногр. интракр. сосудов', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ крестц.-подвзд. сочленений с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ лучезапястного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга, вен и синусов', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ органов грудной клетки', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ пояснично-крестцового отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ почек и надпочечников', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ кисти', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ органов мошонки', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ почек и надпочечников с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ околоносовых пазух', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного и спинного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга, шейного отд. позв.', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ предстательной железы с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ органов брюшной полости', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ мягких тканей', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ крестцово-подвздошных сочленений', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ шеи', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ глазниц с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга при эпилепсии', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ стопы', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ прямой кишки с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ органов мошонки с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ плечевого сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ органов малого таза с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ спинного мозга', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ носоглотки, ротоглотки и полости рта', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ мочевого пузыря', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ околоносовых пазух с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга при эпилепсии с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ копчика', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ поясн.-крестц. отд. позвоночника с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МР- артериогр. интракр. и экстракр. артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга, артерий и вен', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ шеи с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'Бипараметрическая МРТ простаты', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ голеностопного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ стопы с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ мягких тканей лицевого отдела черепа', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга, шейного и грудного отд. позв.', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга и артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ гипоталамо-гипофизарной области', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ локтевого сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ шейного отдела позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ тазобедренного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ глазниц', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ средостения', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ мягких тканей с в/в контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ молочных желез с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ плечевого сплетения с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МР- артериография интракраниальных артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ органов брюшной полости с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ костей таза', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ головного мозга и артерий с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ плечевого сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ пояснично-крестцового сплетения с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ гипоталамо-гипофизарной обл. с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ при рассеянном склерозе с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ головного мозга, артерий и вен с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ основания черепа', 'Магнитно-резонансная томография', 'МРТ', 'Натив'
    UNION ALL SELECT 'МРТ кисти с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ тазобедренного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ костей таза с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
    UNION ALL SELECT 'МРТ копчика с контрастным усилением', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
),
normativy_dney_nedelya AS (
    SELECT 2025 AS god, 1 AS nedelya, 0 AS dni_po_normativu
    UNION ALL SELECT 2025, 2, 3
    UNION ALL SELECT 2025, 3, 6
    UNION ALL SELECT 2025, 4, 6
    UNION ALL SELECT 2025, 5, 6
    UNION ALL SELECT 2025, 6, 6
    UNION ALL SELECT 2025, 7, 6
    UNION ALL SELECT 2025, 8, 6
    UNION ALL SELECT 2025, 9, 6
    UNION ALL SELECT 2025, 10, 5
    UNION ALL SELECT 2025, 11, 6
    UNION ALL SELECT 2025, 12, 6
    UNION ALL SELECT 2025, 13, 6
    UNION ALL SELECT 2025, 14, 6
    UNION ALL SELECT 2025, 15, 6
    UNION ALL SELECT 2025, 16, 6
    UNION ALL SELECT 2025, 17, 6
    UNION ALL SELECT 2025, 18, 5
    UNION ALL SELECT 2025, 19, 4
    UNION ALL SELECT 2025, 20, 6
    UNION ALL SELECT 2025, 21, 6
    UNION ALL SELECT 2025, 22, 6
    UNION ALL SELECT 2025, 23, 6
    UNION ALL SELECT 2025, 24, 5
    UNION ALL SELECT 2025, 25, 6
    UNION ALL SELECT 2025, 26, 6
    UNION ALL SELECT 2025, 27, 6
    UNION ALL SELECT 2025, 28, 6
    UNION ALL SELECT 2025, 29, 6
    UNION ALL SELECT 2025, 30, 6
    UNION ALL SELECT 2025, 31, 6
    UNION ALL SELECT 2025, 32, 6
    UNION ALL SELECT 2025, 33, 6
    UNION ALL SELECT 2025, 34, 6
    UNION ALL SELECT 2025, 35, 6
    UNION ALL SELECT 2025, 36, 6
    UNION ALL SELECT 2025, 37, 6
    UNION ALL SELECT 2025, 38, 6
    UNION ALL SELECT 2025, 39, 6
    UNION ALL SELECT 2025, 40, 6
    UNION ALL SELECT 2025, 41, 6
    UNION ALL SELECT 2025, 42, 6
    UNION ALL SELECT 2025, 43, 6
    UNION ALL SELECT 2025, 44, 6
    UNION ALL SELECT 2025, 45, 5
    UNION ALL SELECT 2025, 46, 6
    UNION ALL SELECT 2025, 47, 6
    UNION ALL SELECT 2025, 48, 6
    UNION ALL SELECT 2025, 49, 6
    UNION ALL SELECT 2025, 50, 6
    UNION ALL SELECT 2025, 51, 6
    UNION ALL SELECT 2025, 52, 6
    UNION ALL SELECT 2026, 2, 2
    UNION ALL SELECT 2026, 3, 6
    UNION ALL SELECT 2026, 4, 6
    UNION ALL SELECT 2026, 5, 6
    UNION ALL SELECT 2026, 6, 6
    UNION ALL SELECT 2026, 7, 6
    UNION ALL SELECT 2026, 8, 6
    UNION ALL SELECT 2026, 9, 5
    UNION ALL SELECT 2026, 10, 6
    UNION ALL SELECT 2026, 11, 5
    UNION ALL SELECT 2026, 12, 6
    UNION ALL SELECT 2026, 13, 6
    UNION ALL SELECT 2026, 14, 6
    UNION ALL SELECT 2026, 15, 6
    UNION ALL SELECT 2026, 16, 6
    UNION ALL SELECT 2026, 17, 6
    UNION ALL SELECT 2026, 18, 5
    UNION ALL SELECT 2026, 19, 5
    UNION ALL SELECT 2026, 20, 6
    UNION ALL SELECT 2026, 21, 6
    UNION ALL SELECT 2026, 22, 6
    UNION ALL SELECT 2026, 23, 6
    UNION ALL SELECT 2026, 24, 5
    UNION ALL SELECT 2026, 25, 6
    UNION ALL SELECT 2026, 26, 6
    UNION ALL SELECT 2026, 27, 6
    UNION ALL SELECT 2026, 28, 6
    UNION ALL SELECT 2026, 29, 6
    UNION ALL SELECT 2026, 30, 6
    UNION ALL SELECT 2026, 31, 6
    UNION ALL SELECT 2026, 32, 6
    UNION ALL SELECT 2026, 33, 6
    UNION ALL SELECT 2026, 34, 6
    UNION ALL SELECT 2026, 35, 6
    UNION ALL SELECT 2026, 36, 6
    UNION ALL SELECT 2026, 37, 6
    UNION ALL SELECT 2026, 38, 6
    UNION ALL SELECT 2026, 39, 6
    UNION ALL SELECT 2026, 40, 6
    UNION ALL SELECT 2026, 41, 6
    UNION ALL SELECT 2026, 42, 6
    UNION ALL SELECT 2026, 43, 6
    UNION ALL SELECT 2026, 44, 6
    UNION ALL SELECT 2026, 45, 5
    UNION ALL SELECT 2026, 46, 6
    UNION ALL SELECT 2026, 47, 6
    UNION ALL SELECT 2026, 48, 6
    UNION ALL SELECT 2026, 49, 6
    UNION ALL SELECT 2026, 50, 6
    UNION ALL SELECT 2026, 51, 6
    UNION ALL SELECT 2026, 52, 6
)
SELECT
    toYear(vie.conduct_date) AS god,
    toWeek(vie.conduct_date, 3) AS nedelya,
    vie.ae_title,
    multiIf(
        vie.ae_title IN ('ING_GP45', 'AWP190202', 'AWP190378', 'AWP190384', 'AWP190464', 'AWP190802', 'AWP190826', 'EXCELMRI_GP219'), 'МРТ',
        vie.ae_title IN ('3994BY5T', 'A5EKD37M', 'AMULET_KUZ', 'DMX_TGBF2', 'MAMMO51_GP66', 'MAMMO51_GP68', 'MAMMO5_GP175', 'MAMMO5_GP2', 'MAMMO5_GP219F4', 'MAMMO5_GP23', 'MAMMO5_GP45', 'MAMMO5_GP68', 'R4A2E7R4', 'SENO1_GP195F3', 'SENO1_GP36F3', 'SENO_DKC1', 'SENO_GP107', 'SENO_GP109', 'SENO_GP109F1', 'SENO_GP109F4', 'SENO_GP115F1', 'SENO_GP12', 'SENO_GP12F5', 'SENO_GP134F2', 'SENO_GP166', 'SENO_GP170F1', 'SENO_GP170F4', 'SENO_GP175F3', 'SENO_GP180', 'SENO_GP180F3', 'SENO_GP191', 'SENO_GP191F2', 'SENO_GP195', 'SENO_GP195F1', 'SENO_GP19F3', 'SENO_GP212F197', 'SENO_GP212F217', 'SENO_GP214F1', 'SENO_GP214F2', 'SENO_GP219F1', 'SENO_GP22F2', 'SENO_GP2F3', 'SENO_GP2F5', 'SENO_GP36F3', 'SENO_GP3F1', 'SENO_GP45F2', 'SENO_GP46F1', 'SENO_GP52F2', 'SENO_GP52F3', 'SENO_GP62F3', 'SENO_GP62F5', 'SENO_GP64F2', 'SENO_GP66F4', 'SENO_GP69', 'SENO_GP6F4', 'SENO_GP8', 'SENO_GP8F2', 'SENO_K2042', 'SENO_KDC4F2', 'SENO_KDC4F4', 'SENO_KDC6', 'SENO_KDC6F4', 'SENO_KDP121F5', 'SENO_MOS'), 'ММГ',
        vie.ae_title IN ('AQSP_GP68', 'CT172868', 'REVOL_GP210', 'REVOL_GP214', 'RVMAX-GP23'), 'КТ',
        vie.ae_title IN ('ARP_TGBF6', 'IMP_KDC2', 'RADREX_MOSF3', 'RENEX1_MOS', 'RENEX5_GP52F1', 'RENEXRC1_GP191F1', 'RENEXRC_DC3', 'RENEXRC_DC3F5', 'RENEXRC_GP109', 'RENEXRC_GP134F1', 'RENEXRC_GP166', 'RENEXRC_GP166F3', 'RENEXRC_GP170', 'RENEXRC_GP170F4', 'RENEXRC_GP180F4', 'RENEXRC_GP195', 'RENEXRC_GP195F3', 'RENEXRC_GP209', 'RENEXRC_GP209F2', 'RENEXRC_GP2F3', 'RENEXRC_GP46F3', 'RENEXRC_GP5', 'RENEXRC_GP52F2', 'RENEXRC_GP62', 'RENEXRC_GP64F1', 'RENEXRC_GP66', 'RENEXRC_GP66F4', 'RENEXRC_GP67F1', 'RENEXRC_GP68', 'RENEXRC_GP68F2', 'RENEXRC_GP69', 'RENEXRC_GP6F3', 'RENEXRC_GP8F2', 'RENEXRC_K2042', 'RENEXRC_KDC4F2', 'RENEXRC_KDC4F5', 'RENEXRC_KDC6', 'RENEXRC_KDC6F3', 'RENEX_DGP148', 'RENEX_GP11', 'RENEX_GP180F2', 'RENEX_GP180F4', 'RENEX_GP23F3', 'RENEX_GP45F5', 'RENEX_GP62F1', 'RENEX_K2042', 'RENEX_KDC4F2', 'RENEX_KDC4F4', 'RENRC_GP212F197', 'TERRA1_GP67F3'), 'РГ',
        vie.ae_title IN ('GAMRF_GP68F1', 'PROSKAN_GP212F6','RENEXRC_GP12F3','RENEXRC_GP45'), 'РГ',
        vie.ae_title IN ('GELUNAR1_GP180', 'GELUNAR2_GP68', 'GELUNAR_GP212'), 'Денситометрия',
        vie.device_type
    ) AS corrected_device_type,
    vie.conduct_mo_name,
    vie.conduct_mu_name,
    multiIf(
        (vie.conduct_mo_name ILIKE '%Кончаловского%') OR (vie.conduct_mo_name ILIKE '%Филатова%'),
        'Стационарное',
        'Амбулаторное'
    ) AS tip_uchrezhdeniya,
    multiIf(
        (vie.conduct_mo_name ILIKE '%ДГП%') OR (vie.conduct_mo_name ILIKE '%Дгп%'),
        'Детское',
        'Взрослое'
    ) AS vid_uchrezhdeniya,
    count(DISTINCT toDate(vie.conduct_date)) AS dnei_otrabotano,
    count(*) AS chasov_za_nedelyu,
    countDistinctIf(vie.accession_number, sp.nativ_s_ku = 'Натив' OR sp.nativ_s_ku IS NULL) AS nativ,
    countDistinctIf(vie.accession_number, sp.nativ_s_ku = 'С КУ') AS s_ku,
    count(DISTINCT vie.accession_number) AS issledovanii_za_nedelyu,
    ndn.dni_po_normativu,
    multiIf(
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'КТ'),
            (nativ * 15 + s_ku * 25),
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'КТ'),
            (nativ * 20 + s_ku * 35),
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'МРТ'),
            (nativ * 30 + s_ku * 45),
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'МРТ'),
            (nativ * 40 + s_ku * 60),
        issledovanii_za_nedelyu
    ) AS Fakt,
    multiIf(
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'КТ'),
            (28 * 15 + 11 * 25) * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'КТ'),
            (19 * 20 + 8 * 35) * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'МРТ'),
            (17 * 30 + 4 * 45) * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'МРТ'),
            (13 * 40 + 2 * 60) * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'РГ'),
            72 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'ММГ'),
            58 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Ангиография'),
            8 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Денситометрия'),
            35 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Флюорография'),
            115 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'ФЛГ'),
            72 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'РГ'),
            50 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'Денситометрия'),
            24 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'Флюорография'),
            80 * ndn.dni_po_normativu,
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'ФЛГ'),
            50 * ndn.dni_po_normativu,
        0
    ) AS Plan,
    if((Plan = 0) OR (Plan IS NULL), NULL, (Fakt / Plan) * 100) AS Zagruzka
FROM data_views.v_instrumental_examinations AS vie
LEFT JOIN spravochnik_protsedur AS sp ON vie.research_name = sp.research_name
LEFT JOIN normativy_dney_nedelya AS ndn
    ON toYear(vie.conduct_date) = ndn.god
    AND toWeek(vie.conduct_date, 3) = ndn.nedelya
WHERE vie.conduct_date IS NOT NULL
  AND vie.conduct_mo_name IS NOT NULL
  AND vie.ae_title IS NOT NULL
  AND NOT (
        vie.conduct_mo_name ILIKE '%Кончаловского%'
     OR vie.conduct_mo_name ILIKE '%Филатова%'
     OR vie.conduct_mo_name ILIKE '%Юдина%'
     OR vie.conduct_mo_name ILIKE '%Ерамиша%'
  )
GROUP BY
    god,
    nedelya,
    vie.ae_title,
    corrected_device_type,
    vie.conduct_mo_name,
    vie.conduct_mu_name,
    tip_uchrezhdeniya,
    vid_uchrezhdeniya,
    ndn.dni_po_normativu
ORDER BY god, nedelya, vie.ae_title"""
        # === КОНЕЦ ПОЛНОСТЬЮ НОВОГО ЗАПРОСА ===
        print("📥 Выполняем запрос к ClickHouse...")
        result = client.query(query)
        # Преобразуем результат в DataFrame
        # Структура ИСПРАВЛЕНА: удалены district_short_name и conduct_mu_name
        columns = ['god', 'nedelya', 'ae_title', 'corrected_device_type',
                   'conduct_mo_name', 'conduct_mu_name',
                   'tip_uchrezhdeniya', 'vid_uchrezhdeniya',
                   'dnei_otrabotano', 'chasov_za_nedelyu',
                   'nativ', 's_ku', 'issledovanii_za_nedelyu', 'dni_po_normativu',
                   'Fakt', 'Plan', 'zagruzka']
        df = pd.DataFrame(result.result_rows, columns=columns)
        print(f"📥 Получено {len(df)} строк данных о загрузке.")
        global _buffer_df_week
        if _extract_only_week:
            _buffer_df_week = df
            return True
    except Exception as e:
        error_msg = f"❌ Ошибка при работе с ClickHouse (workload_komet_week): {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg)
        if client:
            client.close()
        disconnect_vpn()
        return False
    finally:
        if client:
            client.close()

    # Отключение от VPN
    try:
        disconnect_vpn()
    except Exception as e:
        send_log_ntfy_message(f"⚠️ Ошибка отключения VPN: {e}")

    if df.empty:
        print("ostringstream Нет данных о загрузке для выгрузки.")
        send_log_ntfy_message("ostringstream Нет данных workload_komet_week для выгрузки.")
        return True

    # Выгрузка в PostgreSQL
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)

        # Очистка таблицы
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_WORKLOAD};"))
            conn.commit()

        # Выгрузка данных
        df.to_sql(
            PG_TABLE_WORKLOAD.split('.')[-1], # Только имя таблицы без схемы
            engine,
            if_exists='append',
            index=False,
            schema=PG_SCHEMA
        )
        success_msg = f"✅ Успешно выгружено {len(df)} строк в {PG_TABLE_WORKLOAD}."
        print(success_msg)
        send_log_ntfy_message(success_msg)
        return len(df)
    except Exception as e:
        error_msg = f"❌ Ошибка выгрузки в PostgreSQL (workload_komet_week): {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg)
        return False

# ===========================================================================
# Фазовый интерфейс для all_dashboards_up.py
# ===========================================================================

_buffer_df_week = None
_extract_only_week = False


def extract_phase() -> bool:
    """VPN включён (или nooped) — устанавливает флаг и вызывает основную функцию."""
    global _extract_only_week
    _extract_only_week = True
    try:
        return bool(export_workload_komet_week())
    finally:
        _extract_only_week = False


def load_phase() -> bool:
    """VPN выключен — создаём таблицу и льём буфер в PostgreSQL."""
    global _buffer_df_week
    if _buffer_df_week is None:
        print("❌ load_phase (week): буфер пуст")
        return False
    if not create_workload_table():
        return False
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_WORKLOAD};"))
            conn.commit()
        _buffer_df_week.to_sql(
            PG_TABLE_WORKLOAD.split('.')[-1], engine,
            if_exists='append', index=False, schema=PG_SCHEMA
        )
        print(f"✅ Успешно выгружено {len(_buffer_df_week)} строк в {PG_TABLE_WORKLOAD}.")
        return len(_buffer_df_week)
    except Exception as e:
        print(f"❌ Ошибка load_phase (week): {e}")
        return False


# === Основная функция ===
def main():
    """Основная функция"""
    script_name = "workload_export_new.py" # Новое имя скрипта для логов и статуса
    print("🚀 Запуск экспорта новых данных workload_komet_week")
    send_log_ntfy_message("🚀 Запуск экспорта новых данных workload_komet_week")

    # Выполняем экспорт данных о загрузке
    success_workload = export_workload_komet_week()

    # === ОБРАБОТКА РЕЗУЛЬТАТОВ ===
    if success_workload:
        print("🏁 Экспорт новых данных workload_komet_week завершен успешно")
        send_log_ntfy_message("🏁 Экспорт новых данных workload_komet_week завершен успешно")

        # Финальное уведомление в финальный топик
        send_ntfy_alert(
            f"✅ Обновление Загрузка аппаратов по неделям завершено! Выгружено {success_workload} строк.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override=NTFY_DASHBOARD_TOPIC  # ← указываем топик напрямую
        )

        # Сообщение для дашборда
        dashboard_msg_workload = "Новый дашборд по загрузке аппаратов обновлен"
        send_dashboard_ntfy_message(dashboard_msg_workload)

        # Записываем успех в базу
        if STATUS_TRACKER_AVAILABLE:
            record_script_completion(script_name, dashboard_msg_workload)
    else:
        print("❌ Экспорт новых данных workload_komet_week завершен с ошибками")
        send_log_ntfy_message("❌ Экспорт новых данных workload_komet_week завершен с ошибками")

        # Финальное уведомление в финальный топик
        send_ntfy_alert(
            "❌ Загрузка аппаратов по неделям завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override=NTFY_DASHBOARD_TOPIC  # ← указываем топик напрямую
        )

        # Сообщение об ошибке для дашборда
        error_msg_workload = "Не удалось обновить новый дашборд по загрузке аппаратов"
        send_dashboard_ntfy_message(f"❌ {error_msg_workload}")

        # Записываем ошибку в базу
        if STATUS_TRACKER_AVAILABLE:
            record_script_error(script_name, error_msg_workload)

    return success_workload

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)