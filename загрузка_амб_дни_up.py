import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime
import requests

# === Импорты ===
import clickhouse_connect
import psycopg2
from sqlalchemy import create_engine, text
import pandas as pd
import personal_config as cfg

# === Импорт модуля уведомлений ===
from ntfy_notifier import send_ntfy_alert

# Новая таблица для данных по дням
PG_TABLE_WORKLOAD_DAY = f'{cfg.PG_SCHEMA}.workload_komet_day'

# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH

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
    send_ntfy_alert(message=message, title="Workload Day Log", priority="default", tags="log")

# === Функция: Отправка сообщения о дашборде в ntfy ===
def send_dashboard_ntfy_message(message: str):
    """Отправляет сообщение в ntfy для дашборда (в финальный топик)."""
    send_ntfy_alert(message=message, title="Dashboard Update", priority="high", tags="chart_with_upwards_trend", topic_override="push_mrc_dashboards_7895")

# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_log_ntfy_message("🔄 Запуск VPN клиента...")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
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
                pass
    if not activated:
        print("⚠️ Не удалось активировать окно.")
    pyautogui.click(cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y)
    time.sleep(0.5)
    pyautogui.click(cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.press('delete')
    time.sleep(0.5)
    for char in cfg.VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1)
    time.sleep(1)
    pyautogui.click(cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y)
    time.sleep(15)
    print("✅ Подключение к VPN инициировано.")
    send_log_ntfy_message("✅ Подключение к VPN инициировано.")

# === Функция: Отключение от VPN ===
def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_log_ntfy_message("🛑 Начинаю отключение от VPN...")
    pyautogui.rightClick(cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 Отключение завершено.")
    send_log_ntfy_message("🛑 Отключение от VPN выполнено.")

# === Функция: Создание таблицы для данных загрузки по дням ===
def create_workload_day_table():
            """Создание схемы и таблицы workload_komet_day в PostgreSQL с новой структурой"""
            try:
                pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
                engine = create_engine(pg_url)
                with engine.connect() as conn:
                    # Создаем схему, если не существует
                    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {cfg.PG_SCHEMA};"))
                    # Создаем таблицу, если не существует
                    # Структура обновлена в соответствии с новым запросом по дням
                    # === ИЗМЕНЕНИЕ 1: Добавлено поле conduct_mu_name ===
                    create_table_query = f"""
                    CREATE TABLE IF NOT EXISTS {PG_TABLE_WORKLOAD_DAY} (
                        data_issledovaniya DATE,
                        ae_title VARCHAR,
                        corrected_device_type VARCHAR,
                        conduct_mo_name VARCHAR,
                        conduct_mu_name VARCHAR,
                        tip_uchrezhdeniya VARCHAR,
                        vid_uchrezhdeniya VARCHAR,
                        chasov_za_den INTEGER,
                        nativ INTEGER,
                        s_ku INTEGER,
                        issledovanii_za_den INTEGER,
                        "Fakt" NUMERIC, -- Используем двойные кавычки, так как Fakt - ключевое слово в SQL
                        "Plan" NUMERIC, -- Используем двойные кавычки, так как Plan - ключевое слово в SQL
                        zagruzka NUMERIC -- Загрузка в процентах
                    )
                    """
                    # === КОНЕЦ ИЗМЕНЕНИЯ 1 ===
                    conn.execute(text(create_table_query))
                    conn.commit()
                print(f"✅ Схема {cfg.PG_SCHEMA} и таблица {PG_TABLE_WORKLOAD_DAY} проверены/созданы.")
                return True
            except Exception as e:
                error_msg = f"❌ Ошибка создания схемы/таблицы workload_day: {e}"
                print(error_msg)
                send_log_ntfy_message(error_msg)
                return False

# === Функция: Экспорт данных о загрузке по дням ===
def export_workload_komet_day():
    """Загрузка новых данных о загрузке по дням из v_instrumental_examinations в PostgreSQL"""
    print("📊 Загрузка новых данных о загрузке по дням (workload_komet_day)...")
    send_log_ntfy_message("📊 Загрузка новых данных о загрузке по дням (workload_komet_day)...")

    # Создаем таблицу перед началом работы (пропускаем в режиме extract_only)
    if not _extract_only_day:
        if not create_workload_day_table():
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
        client = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT, username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            database=cfg.CH_DATABASE, secure=True, verify=False
        )
        print("✅ ClickHouse (Workload Day) подключён.")

        # === ПОЛНОСТЬЮ НОВЫЙ ЗАПРОС ПО ДНЯМ ===
        query = """
WITH
-- Справочник Натив/С КУ
spravochnik_protsedur AS (
    SELECT *
    FROM (
        -- Начало данных справочника
        SELECT 'КТ брюшной полости и малого таза с контрастом' AS research_name, 'Компьютерная томография' AS research_subtype_name, 'КТ' AS device_type, 'С КУ' AS nativ_s_ku UNION ALL
        SELECT 'КТ голеностопного сустава', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ органов грудной клетки с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ поясн.-крестц. и копчикового отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ почек и мочевыводящих путей', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ головы', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ мягких тканей шеи', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ головы и шеи с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ плечевого сустава', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ надпочечников с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ органов брюшной полости с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ коленных суставов с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ височно-нижнечелюстных суставов', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ челюстно-лицевой области', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ голени', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ грудной клетки, брюшной полости, малого таза', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография бр.аорты и арт. нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ почек и мочевыводящих путей с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ околоносовых пазух с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ плеча', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ бедра с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'НДКТ органов грудной клетки скрининг рака легкого', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ костей таза с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ глазниц', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ челюсти', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'НДКТ околоносовых пазух', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ височной кости', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ органов брюшной полости и малого таза', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография интракраниальных сосудов', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ груд. отдела позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'НДКТ грудной клетки медицинских работников', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография сосудов шеи', 'пьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ орг. груд. кл., брюш. пол., мал. таза контр.', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ шейного отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография артерий нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ головы и шеи', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ органов малого таза с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ пояснично-крестцового отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ головного мозга с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ грудного отдела позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ мягких тканей шеи с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ головы с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ органов малого таза', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография брюшной аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ органов грудной клетки', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ височно-нижнечелюстного сустава', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ локтевого сустава', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ шейн. отдела позвоночника контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ лицевого отдела черепа', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ коленных суставов', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ лучезапястного сустава', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ гортани с фонацией с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ грудного, пояснично-крестц. отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ плеча с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ поясн.-крестц. и копчикового отд. позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ головного мозга', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ гортани с фонацией', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ надпочечников', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ бедра', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография артерий шеи', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ кисти', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ предплечья', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ стопы', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ лицевого отдела черепа с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ тазобедренных суставов', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ околоносовых пазух', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ шейного и грудного отделов позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ поясн.-крестц. отдела позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ-ангиография сосудов нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ-ангиография грудного отдела аорты и ее ветвей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ головы, шеи, грудной клетки с контр.', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ крестцового и копчикового отделов позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ голени с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ костей таза', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ-ангиография нижних конечностей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ предплечья с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ грудного, пояснично-крестц. отд. позвоночника с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ-ангиография артерий верхних конечностей', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ глазниц с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'КТ шейн., грудн., поясн.-крестц. отд. позвоночника', 'Компьютерная томография', 'КТ', 'Натив' UNION ALL
        SELECT 'КТ челюстно-лицевой области с контрастом', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'Ангиопульмонография', 'Компьютерная томография', 'КТ', 'С КУ' UNION ALL
        SELECT 'МРТ предстательной железы', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МР-артериография экстракраниальных артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ коленного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ мягких тканей лиц. отдела черепа с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ ротоглотки и полости рта с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга, шейн., груд. отд. позв. с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ шейного отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ грудного отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ пояснично-крестцового сплетения', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ мосто-мозжечкового угла', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ органов малого таза', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ плечевого сплетения', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ мосто-мозжечкового угла с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МР- венография интракраниальных вен и синусов', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ грудного отдела позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ коленного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга, шейного отд. позв. с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ мочевого пузыря с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МР-холангиопанкреатография', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ спинного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ прямой кишки', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ височно-нижнечелюстных суставов', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МР-артериография интракр. артерий с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ лучезапястного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МР- артериогр. и веногр. интракр. сосудов', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ крестц.-подвзд. сочленений с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ лучезапястного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга, вен и синусов', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ органов грудной клетки', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ пояснично-крестцового отдела позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ почек и надпочечников', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ кисти', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ органов мошонки', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ почек и надпочечников с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'Мовых пазух', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного и спинного мозга с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга, шейного отд. позв.', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ предстательной железы с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ органов брюшной полости', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ мягких тканей', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ крестцово-подвздошных сочленений', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ шеи', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ глазниц с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга при эпилепсии', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ стопы', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ прямой кишки с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ органов мошонки с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ плечевого сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ органов малого таза с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ спинного мозга', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ носоглотки, ротоглотки и полости рта', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ мочевого пузыря', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ околоносовых пазух с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга при эпилепсии с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ копчика', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ поясн.-крестц. отд. позвоночника с контр.', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МР- артериогр. интракр. и экстракр. артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга, артерий и вен', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ шеи с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'Бипараметрическая МРТ простаты', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ голеностопного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ стопы с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ мягких тканей лицевого отдела черепа', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга, шейного и грудного отд. позв.', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга и артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ гипоталамо-гипофизарной области', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ локтевого сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'Много отдела позвоночника с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ тазобедренного сустава', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ глазниц', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ средостения', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ мягких тканей с в/в контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ молочных желез с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ плечевого сплетения с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МР- артериография интракраниальных артерий', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ органов брюшной полости с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ костей таза', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ головного мозга и артерий с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ плечевого сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ пояснично-крестцового сплетения с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ гипоталамо-гипофизарной обл. с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ позвоночника', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ при рассеянном склерозе с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ головного мозга, артерий и вен с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ основания черепа', 'Магнитно-резонансная томография', 'МРТ', 'Натив' UNION ALL
        SELECT 'МРТ кисти с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ тазобедренного сустава с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ костей таза с контрастом', 'Магнитно-резонансная томография', 'МРТ', 'С КУ' UNION ALL
        SELECT 'МРТ копчика с контрастным усилением', 'Магнитно-резонансная томография', 'МРТ', 'С КУ'
        -- Конец данных справочника
    )
)
-- Основной запрос по дням
SELECT
    toDate(vie.conduct_date) AS data_issledovaniya,
    vie.ae_title,
    -- Коррекция device_type для конкретных ae_title
    multiIf(
        vie.ae_title IN ('ING_GP45', 'AWP190202', 'AWP190378', 'AWP190384', 'AWP190464', 'AWP190802', 'AWP190826', 'EXCELMRI_GP219'), 'МРТ',
        vie.ae_title IN ('3994BY5T', 'A5EKD37M', 'AMULET_KUZ', 'DMX_TGBF2', 'MAMMO51_GP66', 'MAMMO51_GP68', 'MAMMO5_GP175', 'MAMMO5_GP2', 'MAMMO5_GP219F4', 'MAMMO5_GP23', 'MAMMO5_GP45', 'MAMMO5_GP68', 'R4A2E7R4', 'SENO1_GP195F3', 'SENO1_GP36F3', 'SENO_DKC1', 'SENO_GP107', 'SENO_GP109', 'SENO_GP109F1', 'SENO_GP109F4', 'SENO_GP115F1', 'SENO_GP12', 'SENO_GP12F5', 'SENO_GP134F2', 'SENO_GP166', 'SENO_GP170F1', 'SENO_GP170F4', 'SENO_GP175F3', 'SENO_GP180', 'SENO_GP180F3', 'SENO_GP191', 'SENO_GP191F2', 'SENO_GP195', 'SENO_GP195F1', 'SENO_GP19F3', 'SENO_GP212F197', 'SENO_GP212F217', 'SENO_GP214F1', 'SENO_GP214F2', 'SENO_GP219F1', 'SENO_GP22F2', 'SENO_GP2F3', 'SENO_GP2F5', 'SENO_GP36F3', 'SENO_GP3F1', 'SENO_GP45F2', 'SENO_GP46F1', 'SENO_GP52F2', 'SENO_GP52F3', 'SENO_GP62F3', 'SENO_GP62F5', 'SENO_GP64F2', 'SENO_GP66F4', 'SENO_GP69', 'SENO_GP6F4', 'SENO_GP8', 'SENO_GP8F2', 'SENO_K2042', 'SENO_KDC4F2', 'SENO_KDC4F4', 'SENO_KDC6', 'SENO_KDC6F4', 'SENO_KDP121F5', 'SENO_MOS'), 'ММГ',
        vie.ae_title IN ('AQSP_GP68', 'CT172868', 'REVOL_GP210', 'REVOL_GP214', 'RVMAX-GP23'), 'КТ',
        vie.ae_title IN ('ARP_TGBF6', 'IMP_KDC2', 'RADREX_MOSF3', 'RENEX1_MOS', 'RENEX5_GP52F1', 'RENEXRC1_GP191F1', 'RENEXRC_DC3', 'RENEXRC_DC3F5', 'RENEXRC_GP109', 'RENEXRC_GP134F1', 'RENEXRC_GP166', 'RENEXRC_GP166F3', 'RENEXRC_GP170', 'RENEXRC_GP170F4', 'RENEXRC_GP180F4', 'RENEXRC_GP195', 'RENEXRC_GP195F3', 'RENEXRC_GP209', 'RENEXRC_GP209F2', 'RENEXRC_GP2F3', 'RENEXRC_GP46F3', 'RENEXRC_GP5', 'RENEXRC_GP52F2', 'RENEXRC_GP62', 'RENEXRC_GP62F1', 'RENEXRC_GP64F1', 'RENEXRC_GP66', 'RENEXRC_GP66F4', 'RENEXRC_GP67F1', 'RENEXRC_GP68', 'RENEXRC_GP68F2', 'RENEXRC_GP69', 'RENEXRC_GP6F3', 'RENEXRC_GP8F2', 'RENEXRC_K2042', 'RENEXRC_KDC4F2', 'RENEXRC_KDC4F5', 'RENEXRC_KDC6', 'RENEXRC_KDC6F3', 'RENEXRC_KDC6F4', 'RENEXRC_KDC6', 'RENEXRC_KDC6F3', 'RENEX_DGP148', 'RENEX_GP11', 'RENEX_GP180F2', 'RENEX_GP180F4', 'RENEX_GP23F3', 'RENEX_GP45F5', 'RENEX_GP62F1', 'RENEX_K2042', 'RENEX_KDC4F2', 'RENEX_KDC4F4', 'RENEXRC_GP12F3','RENEXRC_GP45'), 'РГ',
        vie.ae_title IN ('GAMRF_GP68F1', 'PROSKAN_GP212F6'), 'РГ',
        vie.ae_title IN ('GELUNAR1_GP180', 'GELUNAR2_GP68', 'GELUNAR_GP212'), 'Денситометрия',
        -- Если ae_title не в списке, используем значение из базы
        vie.device_type
    ) AS corrected_device_type,
    vie.conduct_mo_name,
    vie.conduct_mu_name,
    -- Обновленная логика определения типа и вида учреждения
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
    count(*) AS chasov_za_den,
    -- Классификация и подсчет исследований за день с использованием справочника
    -- Изменено: исследования без классификации (sp.nativ_s_ku IS NULL) считаются Натив
    countDistinctIf(vie.accession_number, sp.nativ_s_ku = 'Натив' OR sp.nativ_s_ku IS NULL) AS nativ,
    countDistinctIf(vie.accession_number, sp.nativ_s_ku = 'С КУ') AS s_ku,
    count(DISTINCT vie.accession_number) AS issledovanii_za_den,
    -- Расчет ФАКТ
    multiIf(
        -- КТ
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'КТ'),
            (nativ * 15 + s_ku * 25), -- Минуты
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'КТ'),
            (nativ * 20 + s_ku * 35), -- Минуты (пример, замените на реальные нормы)
        -- МРТ
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'МРТ'),
            (nativ * 30 + s_ku * 45), -- Минуты
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'МРТ'),
            (nativ * 40 + s_ku * 60), -- Минуты (пример, замените на реальные нормы)
        -- Для остальных устройств Факт = Количество исследований за день
        issledovanii_za_den
    ) AS Fakt, -- В минутах для КТ/МРТ, в исследованиях для других
    -- Расчет ПЛАН
    multiIf(
        -- КТ
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'КТ'),
            (28 * 15 + 11 * 25), -- Минуты (695)
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'КТ'),
            (19 * 20 + 8 * 35), -- Минуты (660) (пример, замените на реальные нормы)
        -- МРТ
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'МРТ'),
            (17 * 30 + 4 * 45), -- Минуты (690)
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'МРТ'),
            (13 * 40 + 2 * 60), -- Минуты (640) (пример, замените на реальные нормы)
        -- Остальные модальности (планы указаны в Python-скрипте)
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'РГ'),
            72, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'ММГ'),
            58, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Ангиография'),
            8, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Денситометрия'),
            35, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'Флюорография'),
            115, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Взрослое') AND (corrected_device_type = 'ФЛГ'),
            72, -- Исследования в день (предположим, что ФЛГ считается как РГ)
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'РГ'),
            50, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'Денситометрия'),
            24, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'Флюорография'),
            80, -- Исследования в день
        (tip_uchrezhdeniya = 'Амбулаторное') AND (vid_uchrezhdeniya = 'Детское') AND (corrected_device_type = 'ФЛГ'),
            50, -- Исследования в день (предположим, что ФЛГ считается как РГ)
        0 -- По умолчанию
    ) AS Plan, -- В минутах для КТ/МРТ, в исследованиях для других
    -- Расчет Загрузки
    if((Plan = 0) OR (Plan IS NULL), NULL, (Fakt / Plan) * 100) AS Zagruzka -- Процент
FROM data_views.v_instrumental_examinations AS vie
-- JOIN со справочником процедур
LEFT JOIN spravochnik_protsedur AS sp
    ON vie.research_name = sp.research_name
WHERE vie.conduct_date IS NOT NULL
  AND vie.conduct_mo_name IS NOT NULL
  AND vie.ae_title IS NOT NULL
  -- Добавленная фильтрация: исключаем стационары
  AND NOT ((vie.conduct_mo_name ILIKE '%Кончаловского%') OR (vie.conduct_mo_name ILIKE '%Филатова%') OR (vie.conduct_mo_name ILIKE '%Юдина%') OR (vie.conduct_mo_name ILIKE '%Ерамиша%'))
  -- Фильтр за последние 30 дней
  AND vie.conduct_date >= today() - INTERVAL 30 DAY
GROUP BY
    data_issledovaniya,
    vie.ae_title,
    corrected_device_type, -- Используем скорректированное значение
    vie.conduct_mo_name,
    vie.conduct_mu_name,
    tip_uchrezhdeniya,
    vid_uchrezhdeniya
ORDER BY data_issledovaniya, vie.ae_title
"""
        # === КОНЕЦ ПОЛНОСТЬЮ НОВОГО ЗАПРОСА ПО ДНЯМ ===
        print("📥 Выполняем запрос к ClickHouse...")
        result = client.query(query)
        # Преобразуем результат в DataFrame
        # Структура обновлена в соответствии с новым запросом по дням
        columns = [
            'data_issledovaniya', 'ae_title', 'corrected_device_type',
            'conduct_mo_name', 'conduct_mu_name','tip_uchrezhdeniya', 'vid_uchrezhdeniya',
            'chasov_za_den', 'nativ', 's_ku', 'issledovanii_za_den',
            'Fakt', 'Plan', 'zagruzka'
        ]
        df = pd.DataFrame(result.result_rows, columns=columns)
        print(f"📥 Получено {len(df)} строк данных о загрузке по дням.")
        global _buffer_df_day
        if _extract_only_day:
            _buffer_df_day = df
            return True
    except Exception as e:
        error_msg = f"❌ Ошибка при работе с ClickHouse (workload_komet_day): {e}"
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
        print("ostringstream Нет данных о загрузке по дням для выгрузки.")
        send_log_ntfy_message("ostringstream Нет данных workload_komet_day для выгрузки.")
        return True

    # Выгрузка в PostgreSQL
    try:
        pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
        engine = create_engine(pg_url)

        # Очистка таблицы
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_WORKLOAD_DAY};"))
            conn.commit()

        # Выгрузка данных
        df.to_sql(
            PG_TABLE_WORKLOAD_DAY.split('.')[-1], # Только имя таблицы без схемы
            engine,
            if_exists='append',
            index=False,
            schema=cfg.PG_SCHEMA
        )
        success_msg = f"✅ Успешно выгружено {len(df)} строк в {PG_TABLE_WORKLOAD_DAY}."
        print(success_msg)
        send_log_ntfy_message(success_msg)
        return len(df)
    except Exception as e:
        error_msg = f"❌ Ошибка выгрузки в PostgreSQL (workload_komet_day): {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg)
        return False

# ===========================================================================
# Фазовый интерфейс для all_dashboards_up.py
# ===========================================================================

_buffer_df_day = None
_extract_only_day = False


def extract_phase() -> bool:
    """VPN включён (или nooped) — устанавливает флаг и вызывает основную функцию."""
    global _extract_only_day
    _extract_only_day = True
    try:
        return bool(export_workload_komet_day())
    finally:
        _extract_only_day = False


def load_phase() -> bool:
    """VPN выключен — создаём таблицу и льём буфер в PostgreSQL."""
    global _buffer_df_day
    if _buffer_df_day is None:
        print("❌ load_phase (day): буфер пуст")
        return False
    if not create_workload_day_table():
        return False
    try:
        pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_WORKLOAD_DAY};"))
            conn.commit()
        _buffer_df_day.to_sql(
            PG_TABLE_WORKLOAD_DAY.split('.')[-1], engine,
            if_exists='append', index=False, schema=cfg.PG_SCHEMA
        )
        print(f"✅ Успешно выгружено {len(_buffer_df_day)} строк в {PG_TABLE_WORKLOAD_DAY}.")
        return len(_buffer_df_day)
    except Exception as e:
        print(f"❌ Ошибка load_phase (day): {e}")
        return False


# === Основная функция ===
def main():
    """Основная функция"""
    script_name = "workload_export_day.py" # Новое имя скрипта для логов и статуса
    print("🚀 Запуск экспорта новых данных workload_komet_day")
    send_log_ntfy_message("🚀 Запуск экспорта новых данных workload_komet_day")

    # Выполняем экспорт данных о загрузке по дням
    success_workload_day = export_workload_komet_day()

    # === ОБРАБОТКА РЕЗУЛЬТАТОВ ===
    if success_workload_day:
        print("🏁 Экспорт новых данных workload_komet_day завершен успешно")
        send_log_ntfy_message("🏁 Экспорт новых данных workload_komet_day завершен успешно")

        # Финальное уведомление в финальный топик
        send_ntfy_alert(
            f"✅ Обновление Загрузка аппаратов по дням завершено! Выгружено {success_workload_day} строк.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )

        # Сообщение для дашборда
        dashboard_msg_workload_day = "Новый дашборд по загрузке аппаратов по дням обновлен"
        send_dashboard_ntfy_message(dashboard_msg_workload_day)

        # Записываем успех в базу
        if STATUS_TRACKER_AVAILABLE:
            record_script_completion(script_name, dashboard_msg_workload_day)
    else:
        print("❌ Экспорт новых данных workload_komet_day завершен с ошибками")
        send_log_ntfy_message("❌ Экспорт новых данных workload_komet_day завершен с ошибками")

        # Финальное уведомление в финальный топик
        send_ntfy_alert(
            "❌ Загрузка аппаратов по дням завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )

        # Сообщение об ошибке для дашборда
        error_msg_workload_day = "Не удалось обновить новый дашборд по загрузке аппаратов по дням"
        send_dashboard_ntfy_message(f"❌ {error_msg_workload_day}")

        # Записываем ошибку в базу
        if STATUS_TRACKER_AVAILABLE:
            record_script_error(script_name, error_msg_workload_day)

    return success_workload_day

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)