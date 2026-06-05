import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime
import requests

# === Импорты для работы с БД ===
import clickhouse_connect
import psycopg2
from sqlalchemy import create_engine, text
import pandas as pd
import pickle as _pickle


from ntfy_notifier import send_ntfy_alert
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
PG_TABLE_CAOP = f'{PG_SCHEMA}.count_caop'

_BUFFER_CAOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'caop_phase_buffer.pkl')

# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты для pyautogui
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y


# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN-клиент...", title="VPN Connect", priority="default", tags="lock")
    
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        error_msg = f"❌ Ошибка запуска VPN: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    
    time.sleep(15)
    
    # Активация окна
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
    
    # Ввод пароля
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y)
    time.sleep(0.5)
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.press('delete')
    time.sleep(0.5)
    
    for char in VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1)
    
    time.sleep(1)
    pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
    time.sleep(15)
    
    print("✅ Подключение к VPN инициировано.")
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


# === Функция: Отключение от VPN ===
def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    
    print("🛑 Отключение завершено.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


# === Функция: Создание таблицы в PostgreSQL ===
def create_count_caop_table():
    """Создание схемы и таблицы count_caop в PostgreSQL"""
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)

        with engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA};"))
            
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS {PG_TABLE_CAOP} (
                conduct_date DATE,
                assignment_mu_name VARCHAR,
                conduct_mo_name VARCHAR,
                conduct_mu_name VARCHAR,
                research_subtype_name VARCHAR,
                device_type VARCHAR,
                ae_title VARCHAR,
                research_id VARCHAR,
                research_name VARCHAR,
                payment_source VARCHAR,
                patient_age_group VARCHAR,
                "Описано" VARCHAR,
                count_accession INTEGER
            )
            """
            conn.execute(text(create_table_query))
            conn.commit()

        print(f"✅ Схема {PG_SCHEMA} и таблица {PG_TABLE_CAOP} проверены/созданы.")
        return True

    except Exception as e:
        error_msg = f"❌ Ошибка создания схемы/таблицы: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, title="DB Schema Error", priority="urgent", tags="database")
        return False


# === Основная функция экспорта ===
def export_count_caop():
    """
    Загрузка данных из v_instrumental_examinations в PostgreSQL.
    Returns:
        tuple: (success: bool, rows_count: int)
    """
    print("📊 Загрузка данных для ЦАОП в count_caop...")
    send_ntfy_alert("Начинаю выгрузку данных ЦАОП...", title="CAOP Export Start", priority="default", tags="inbox")

    if not create_count_caop_table():
        return False, 0

    # Подключение к VPN
    try:
        connect_vpn()
    except Exception as e:
        send_ntfy_alert(f"Ошибка VPN: {str(e)[:80]}", title="VPN Fail", priority="urgent", tags="warning")
        return False, 0

    client = None
    df = None
    
    try:
        # Подключение к ClickHouse
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False
        )
        print("✅ ClickHouse подключён.")

        # Запрос к ClickHouse
        query = """
        SELECT toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')) AS conduct_date,
               vie.assignment_mu_name AS assignment_mu_name,
               vie.conduct_mo_name AS conduct_mo_name,
               vie.conduct_mu_name AS conduct_mu_name,
               vie.research_subtype_name AS research_subtype_name,
               vie.device_type AS device_type,
               vie.ae_title AS ae_title,
               vie.research_id AS research_id,
               vie.research_name AS research_name,
               if(vie.payment_source IS NULL, 'ОМС', vie.payment_source) AS payment_source,
               if(vie.patient_age >= 18, 'Взрослые', 'Дети') AS patient_age_group,
               if(descr.study_uid IS NOT NULL, 'Описано', NULL) AS "Описано",
               count(DISTINCT vie.accession_number) AS count_accession
        FROM data_views.v_instrumental_examinations vie
        LEFT JOIN (
            SELECT DISTINCT study_uid,
                   toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow') AS assignment_result_doc_created_date_moscow,
                   toTimeZone(toDateTime(conduct_date), 'Europe/Moscow') AS conduct_datetime_moscow
            FROM data_views.v_eris_assignment_results
        ) descr ON vie.study_uid = descr.study_uid
        WHERE 
            vie.ae_title IS NOT NULL
            AND (
                vie.assignment_mu_name LIKE '%ЦАОП%'
                OR vie.assignment_mu_name IN (
                    'ГБУЗ «ММНКЦ им. С.П. Боткина ДЗМ» МГГЦ',
                    'МГЦ ГБУЗ МКНЦ им.А.С.Логинова ДЗМ'
                )
            )
            AND toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')) >= '2024-01-01'
        GROUP BY toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')),
                 vie.assignment_mu_name,
                 vie.conduct_mo_name,
                 vie.conduct_mu_name,
                 vie.research_subtype_name,
                 vie.device_type,
                 vie.ae_title,
                 vie.research_id,
                 vie.research_name,
                 if(vie.payment_source IS NULL, 'ОМС', vie.payment_source),
                 if(vie.patient_age >= 18, 'Взрослые', 'Дети'),
                 descr.study_uid
        ORDER BY toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')),
                 vie.assignment_mu_name
        """

        print("📥 Выполняем запрос к ClickHouse...")
        result = client.query(query)

        columns = [
            'conduct_date', 'assignment_mu_name', 'conduct_mo_name', 'conduct_mu_name',
            'research_subtype_name', 'device_type', 'ae_title',
            'research_id', 'research_name', 'payment_source', 'patient_age_group', 'Описано', 'count_accession'
        ]

        df = pd.DataFrame(result.result_rows, columns=columns)
        print(f"📥 Получено {len(df)} строк данных для ЦАОП.")

    except Exception as e:
        error_msg = f"❌ Ошибка ClickHouse: {e}"
        print(error_msg)
        send_ntfy_alert(f"Сбой ClickHouse: {str(e)[:80]}", title="CH Error", priority="urgent", tags="fire")
        if client:
            client.close()
        disconnect_vpn()
        return False, 0
    finally:
        if client:
            client.close()

    # Отключение VPN после получения данных
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning", priority="default", tags="warning")

    # Проверка на пустой DataFrame
    if df is None or df.empty:
        print("📭 Нет данных для выгрузки.")
        send_ntfy_alert("Нет данных для выгрузки в count_caop", title="CAOP Empty", priority="default", tags="inbox")
        return True, 0

    # Выгрузка в PostgreSQL
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)

        # Очистка таблицы
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_CAOP};"))
            conn.commit()

        # Выгрузка данных
        df.to_sql(
            PG_TABLE_CAOP.split('.')[-1],
            engine,
            if_exists='append',
            index=False,
            schema=PG_SCHEMA
        )

        rows_count = len(df)
        success_msg = f"✅ Успешно выгружено {rows_count} строк в {PG_TABLE_CAOP}."
        print(success_msg)
        send_ntfy_alert(success_msg, title="CAOP Success", priority="high", tags="white_check_mark")

        return True, rows_count

    except Exception as e:
        error_msg = f"❌ Ошибка выгрузки в PostgreSQL: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка PostgreSQL: {str(e)[:80]}", title="PG Error", priority="urgent", tags="database")
        return False, 0


# === Основная точка входа ===
def main():
    """Основная функция"""
    print("🚀 Запуск экспорта данных для ЦАОП")
    send_ntfy_alert("Запускаю экспорт ЦАОП...", title="CAOP Start", priority="default", tags="robot")


    # Выполняем экспорт
    success, rows_count = export_count_caop()

    # Финальные уведомления
    if success:
        send_ntfy_alert(
            f"✅ Обновление Цаоп завершено! Выгружено {rows_count} строк.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
    else:
        send_ntfy_alert(
            "❌ Обновление Цаоп завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )

    print("🏁 Скрипт завершён.")
    return success


def extract_phase():
    """Фаза 1 (VPN включён): запрос к source ClickHouse → pickle-буфер."""
    print("📥 [caop] extract_phase: запрос к ClickHouse...")
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False
        )
        query = """
        SELECT toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')) AS conduct_date,
               vie.assignment_mu_name AS assignment_mu_name,
               vie.conduct_mo_name AS conduct_mo_name,
               vie.conduct_mu_name AS conduct_mu_name,
               vie.research_subtype_name AS research_subtype_name,
               vie.device_type AS device_type,
               vie.ae_title AS ae_title,
               vie.research_id AS research_id,
               vie.research_name AS research_name,
               if(vie.payment_source IS NULL, 'ОМС', vie.payment_source) AS payment_source,
               if(vie.patient_age >= 18, 'Взрослые', 'Дети') AS patient_age_group,
               if(descr.study_uid IS NOT NULL, 'Описано', NULL) AS "Описано",
               count(DISTINCT vie.accession_number) AS count_accession
        FROM data_views.v_instrumental_examinations vie
        LEFT JOIN (
            SELECT DISTINCT study_uid,
                   toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow') AS assignment_result_doc_created_date_moscow,
                   toTimeZone(toDateTime(conduct_date), 'Europe/Moscow') AS conduct_datetime_moscow
            FROM data_views.v_eris_assignment_results
        ) descr ON vie.study_uid = descr.study_uid
        WHERE
            vie.ae_title IS NOT NULL
            AND (
                vie.assignment_mu_name LIKE '%ЦАОП%'
                OR vie.assignment_mu_name IN (
                    'ГБУЗ «ММНКЦ им. С.П. Боткина ДЗМ» МГГЦ',
                    'МГЦ ГБУЗ МКНЦ им.А.С.Логинова ДЗМ'
                )
            )
            AND toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')) >= '2024-01-01'
        GROUP BY toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')),
                 vie.assignment_mu_name,
                 vie.conduct_mo_name,
                 vie.conduct_mu_name,
                 vie.research_subtype_name,
                 vie.device_type,
                 vie.ae_title,
                 vie.research_id,
                 vie.research_name,
                 if(vie.payment_source IS NULL, 'ОМС', vie.payment_source),
                 if(vie.patient_age >= 18, 'Взрослые', 'Дети'),
                 descr.study_uid
        ORDER BY toDate(toTimeZone(toDateTime(vie.conduct_date), 'Europe/Moscow')),
                 vie.assignment_mu_name
        """
        result = client.query(query)
        client.close()
        columns = [
            'conduct_date', 'assignment_mu_name', 'conduct_mo_name', 'conduct_mu_name',
            'research_subtype_name', 'device_type', 'ae_title',
            'research_id', 'research_name', 'payment_source', 'patient_age_group', 'Описано', 'count_accession'
        ]
        df = pd.DataFrame(result.result_rows, columns=columns)
        with open(_BUFFER_CAOP, 'wb') as f:
            _pickle.dump(df, f)
        print(f"  ✅ [caop] extract_phase: сохранено {len(df)} строк в буфер.")
        return True
    except Exception as e:
        print(f"  ❌ [caop] extract_phase: {e}")
        return False


def load_phase():
    """Фаза 2 (VPN выключен): pickle-буфер → PostgreSQL (TRUNCATE + INSERT)."""
    print("📤 [caop] load_phase: загрузка из буфера в PostgreSQL...")
    try:
        with open(_BUFFER_CAOP, 'rb') as f:
            df = _pickle.load(f)
    except Exception as e:
        print(f"  ❌ [caop] load_phase: не удалось прочитать буфер: {e}")
        return False

    if df is None or df.empty:
        print("  ⚠️ [caop] load_phase: буфер пуст, пропускаю.")
        return True

    if not create_count_caop_table():
        return False

    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_CAOP};"))
            conn.commit()
        df.to_sql(
            PG_TABLE_CAOP.split('.')[-1], engine,
            if_exists='append', index=False, schema=PG_SCHEMA
        )
        print(f"  ✅ [caop] load_phase: выгружено {len(df)} строк в {PG_TABLE_CAOP}.")
        return True
    except Exception as e:
        print(f"  ❌ [caop] load_phase: ошибка PostgreSQL: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)