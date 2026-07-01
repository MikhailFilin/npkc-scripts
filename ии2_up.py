"""
validation_eris_report.py
Экспорт данных валидации Eris Report из ClickHouse в PostgreSQL.
Все уведомления отправляются через ntfy.
"""

import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime
import requests

# === Импорты для работы с БД ===
import clickhouse_connect
from sqlalchemy import create_engine, text
import pandas as pd

# === Импорт модуля уведомлений ===
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

# Имя таблицы
PG_TABLE_INSTRUMENTAL = f'{PG_SCHEMA}.validation_eris_report'

# Глубина выборки (дней)
DAYS_TO_SYNC = 4

# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты для pyautogui
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y

# === Импорт для записи статуса ===
sys.path.append(r'L:\bot')
try:
    from status_tracker import record_script_completion, record_script_error
    STATUS_TRACKER_AVAILABLE = True
except ImportError:
    STATUS_TRACKER_AVAILABLE = False
    def record_script_completion(*args, **kwargs): pass
    def record_script_error(*args, **kwargs): pass


# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN-клиент для валидации Eris...", title="VPN Connect", priority="default", tags="lock")
    
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


# === Функция: Создание схемы и таблицы в PostgreSQL ===
def create_validation_eris_report():
    """Создание схемы и таблицы validation_eris_report в PostgreSQL"""
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url)

        with engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA};"))

            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS {PG_TABLE_INSTRUMENTAL} (
                conduct_date DATE,
                conduct_mu_name VARCHAR,
                device_type VARCHAR,
                ae_title VARCHAR,
                research_id VARCHAR,
                research_name VARCHAR,
                payment_source VARCHAR,
                is_norm INTEGER,
                modelid VARCHAR,
                accession_number VARCHAR,
                study_uid VARCHAR,
                studyuid_ai VARCHAR
            )
            """
            conn.execute(text(create_table_query))
            conn.commit()

        print(f"✅ Схема {PG_SCHEMA} и таблица {PG_TABLE_INSTRUMENTAL} проверены/созданы.")
        return True

    except Exception as e:
        error_msg = f"❌ Ошибка создания схемы/таблицы ({PG_TABLE_INSTRUMENTAL}): {type(e).__name__}: {str(e)[:1000]}..."
        print(error_msg)
        send_ntfy_alert(error_msg, title="DB Schema Error", priority="urgent", tags="database")
        return False


# === Основная функция: Экспорт данных ===
def export_validation_eris_report():
    """Загрузка данных из сложного запроса в PostgreSQL"""
    print(f"📊 Загрузка данных в {PG_TABLE_INSTRUMENTAL}...")
    send_ntfy_alert(f"Начинаю загрузку данных в {PG_TABLE_INSTRUMENTAL}...", title="Validation Eris Start", priority="default", tags="inbox")

    # Создаем таблицу перед началом работы
    if not create_validation_eris_report():
        return False

    # Подключение к VPN
    try:
        connect_vpn()
    except Exception as e:
        send_ntfy_alert(f"Ошибка VPN: {str(e)[:80]}", title="VPN Fail", priority="urgent", tags="warning")
        return False

    client = None
    try:
        # Подключение к ClickHouse через VPN
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False,
            send_receive_timeout=2200,
            connect_timeout=60
        )
        print("✅ ClickHouse подключён.")

        # Сложный запрос
        query = f"""
        SELECT
            toDate(vie.conduct_date) AS conduct_date,
            vie.conduct_mu_name AS conduct_mu_name,
            vie.device_type AS device_type,
            vie.ae_title AS ae_title,
            vie.research_id AS research_id,
            vie.research_name AS research_name,
            vie.payment_source AS payment_source,
            toInt32OrZero(eris_rep.norma_value) AS is_norm,
            eris_rep.modelId AS modelId,
            vie.accession_number AS accession_number,
            vie.study_uid AS study_uid,
            eris_rep.studyIUID AS studyUID_ai
        FROM data_views.v_instrumental_examinations vie
        LEFT JOIN (
            SELECT
                JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
                any(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value,
                any(JSONExtractInt(JSONExtractString(raw_data, 'aiResult'), 'modelId')) AS modelId
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
                AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
                GROUP BY studyIUID
            ) as eris_rep
            ON vie.study_uid = eris_rep.studyIUID
            WHERE vie.ae_title IS NOT NULL 
                AND vie.patient_age > '17' 
                AND vie.conduct_mo_id != '10449178' 
                AND toDate(vie.conduct_date) >= now() - INTERVAL {DAYS_TO_SYNC} DAY
        """

        print("📥 Выполняем сложный запрос к ClickHouse...")
        result = client.query(query)

        columns = [
            'conduct_date', 'conduct_mu_name', 'device_type',
            'ae_title', 'research_id', 'research_name',
            'payment_source', 'is_norm', 'modelid',
            'accession_number', 'study_uid', 'studyuid_ai'
        ]

        df = pd.DataFrame(result.result_rows, columns=columns)
        print(f"📥 Получено {len(df)} строк данных.")

    except Exception as e:
        error_msg = f"❌ Ошибка при работе с ClickHouse: {type(e).__name__}: {str(e)[:2000]}..."
        print(error_msg)
        send_ntfy_alert(f"Сбой ClickHouse: {str(e)[:80]}", title="CH Error", priority="urgent", tags="fire")
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
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning", priority="default", tags="warning")

    if df.empty:
        print("📭 Нет данных для выгрузки.")
        send_ntfy_alert("Нет данных для выгрузки в validation_eris_report", title="Validation Eris Empty", priority="default", tags="inbox")
        return True

    # Выгрузка в PostgreSQL
    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url, echo=False)

        # Очистка таблицы
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_INSTRUMENTAL};"))
            conn.commit()

        # Выгрузка данных
        chunk_size = 1000
        total_rows = len(df)
        print(f"📤 Начинаем выгрузку {total_rows} строк в PostgreSQL...")

        df.to_sql(
            PG_TABLE_INSTRUMENTAL.split('.')[-1],
            engine,
            if_exists='append',
            index=False,
            schema=PG_SCHEMA,
            method='multi',
            chunksize=chunk_size
        )

        success_msg = f"✅ Успешно выгружено {total_rows} строк в {PG_TABLE_INSTRUMENTAL}."
        print(success_msg)
        send_ntfy_alert(success_msg, title="Validation Eris Success", priority="high", tags="white_check_mark")
        return True

    except Exception as e:
        error_detail = str(e)[:2000] + "..." if len(str(e)) > 2000 else str(e)
        error_msg = f"❌ Ошибка выгрузки в PostgreSQL: {type(e).__name__}: {error_detail}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка PostgreSQL: {str(e)[:80]}", title="PG Load Error", priority="urgent", tags="database")
        return False


# === Основная функция скрипта ===
def main():
    """Основная функция"""
    script_name = "validation_eris_report.py"

    print(f"🚀 Запуск экспорта данных в {PG_TABLE_INSTRUMENTAL}")
    send_ntfy_alert(f"Запускаю экспорт валидации Eris...", title="Validation Eris Start", priority="default", tags="robot")

    # Выполняем экспорт
    success = export_validation_eris_report()

    # === ОБРАБОТКА РЕЗУЛЬТАТОВ И ОТПРАВКА УВЕДОМЛЕНИЙ ===
    if success:
        send_ntfy_alert(
            "✅ Дашборд Отработка ИИ, Часть 2 из 2 завершён успешно! Дашборд обновлён.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("🏁 Экспорт завершен успешно")

        # Записываем успех в базу
        try:
            record_script_completion(script_name, "Дашборд по отработке ИИ обновлен")
            print("✅ Успех записан в систему отслеживания.")
        except Exception as e:
            err_msg = f"⚠️ Ошибка записи успеха: {e}"
            print(err_msg)
            send_ntfy_alert(err_msg, title="Tracker Warning", priority="default", tags="warning")

    else:
        send_ntfy_alert(
            "❌ Дашборд Отработка ИИ, Часть 2 из 2 завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("❌ Экспорт завершен с ошибками")

        # Записываем ошибку в базу
        try:
            record_script_error(script_name, f"Не удалось обновить дашборд {PG_TABLE_INSTRUMENTAL}")
            print("❌ Ошибка записана в систему отслеживания.")
        except Exception as e:
            err_msg = f"⚠️ Ошибка записи ошибки: {e}"
            print(err_msg)
            send_ntfy_alert(err_msg, title="Tracker Warning", priority="default", tags="warning")

    return success


# === Точка входа ===
if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)


# ===========================================================================
# Двухфазное выполнение для all_dashboards_up.py
# ===========================================================================
import pickle as _pickle

_BUFFER_ИИ2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ии2_phase_buffer.pkl')


def extract_phase():
    """Фаза 1 (VPN уже включён): запрос source CH → pickle буфер. Без управления VPN."""
    client = None
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False,
            send_receive_timeout=2200, connect_timeout=60
        )
        query = f"""
        SELECT
            toDate(vie.conduct_date) AS conduct_date,
            vie.conduct_mu_name AS conduct_mu_name,
            vie.device_type AS device_type,
            vie.ae_title AS ae_title,
            vie.research_id AS research_id,
            vie.research_name AS research_name,
            vie.payment_source AS payment_source,
            toInt32OrZero(eris_rep.norma_value) AS is_norm,
            eris_rep.modelId AS modelId,
            vie.accession_number AS accession_number,
            vie.study_uid AS study_uid,
            eris_rep.studyIUID AS studyUID_ai
        FROM data_views.v_instrumental_examinations vie
        LEFT JOIN (
            SELECT
                JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
                any(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value,
                any(JSONExtractInt(JSONExtractString(raw_data, 'aiResult'), 'modelId')) AS modelId
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
                AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
                GROUP BY studyIUID
            ) as eris_rep
            ON vie.study_uid = eris_rep.studyIUID
            WHERE vie.ae_title IS NOT NULL
                AND vie.patient_age > '17'
                AND vie.conduct_mo_id != '10449178'
                AND toDate(vie.conduct_date) >= now() - INTERVAL {DAYS_TO_SYNC} DAY
        """
        result = client.query(query)
        columns = [
            'conduct_date', 'conduct_mu_name', 'device_type',
            'ae_title', 'research_id', 'research_name',
            'payment_source', 'is_norm', 'modelid',
            'accession_number', 'study_uid', 'studyuid_ai'
        ]
        df = pd.DataFrame(result.result_rows, columns=columns)
        with open(_BUFFER_ИИ2, 'wb') as _f:
            _pickle.dump(df, _f)
        print(f"  ✅ [ии2] extract: {len(df)} строк → буфер")
        return True
    except Exception as e:
        print(f"  ❌ [ии2] extract: {e}")
        send_ntfy_alert(f"ии2 CH: {str(e)[:80]}", title="CH Error", priority="urgent", tags="fire")
        return False
    finally:
        if client:
            client.close()


def load_phase():
    """Фаза 2 (VPN выключен): pickle буфер → PostgreSQL. Без управления VPN."""
    if not os.path.exists(_BUFFER_ИИ2):
        print("  📭 [ии2] load: буфер не найден")
        return True
    try:
        with open(_BUFFER_ИИ2, 'rb') as _f:
            df = _pickle.load(_f)
        os.remove(_BUFFER_ИИ2)
    except Exception as e:
        print(f"  ❌ [ии2] load буфер: {e}")
        return False

    if not create_validation_eris_report():
        return False

    if df.empty:
        return True

    try:
        pg_url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
        engine = create_engine(pg_url, echo=False)
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {PG_TABLE_INSTRUMENTAL};"))
            conn.commit()
        df.to_sql(
            PG_TABLE_INSTRUMENTAL.split('.')[-1], engine,
            if_exists='append', index=False, schema=PG_SCHEMA,
            method='multi', chunksize=1000
        )
        print(f"  ✅ [ии2] load: {len(df)} строк → PG")
        send_ntfy_alert(f"✅ ии2: {len(df)} строк.", title="Validation Eris Success", priority="high", tags="white_check_mark")
        return True
    except Exception as e:
        print(f"  ❌ [ии2] load PG: {e}")
        send_ntfy_alert(f"ии2 PG: {str(e)[:80]}", title="PG Load Error", priority="urgent", tags="database")
        return False