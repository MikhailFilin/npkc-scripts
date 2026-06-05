import subprocess
import pyautogui
import time
import sys
import os
import traceback
from datetime import datetime
import requests
import logging
import pickle as _pickle

# === Импорты ===
import clickhouse_connect
import pandas as pd

# === Импорт модуля уведомлений ===
from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройка логгера ===
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'export_task_lists.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === Настройки исходной ClickHouse ===
CH_HOST = cfg.CH_HOST
CH_PORT = cfg.CH_PORT
CH_USER = cfg.CH_USER
CH_PASSWORD = cfg.CH_PASSWORD
CH_DATABASE = cfg.CH_DATABASE

# === Настройки целевой ClickHouse ===
CH_HOST_TARGET = cfg.CH_HOST_TARGET
CH_PORT_TARGET = cfg.CH_PORT_TARGET
CH_USER_TARGET = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

CH_TABLE = 'v_instrumental_task_lists'

# Буфер для двухфазного выполнения
_BUFFER_TASK_LISTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'task_lists_phase_buffer.pkl')

# Глубина выборки (дней)
DAYS_TO_SYNC = 5

# === Настройки для VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y

# === Настройки ntfy ===
NTFY_LOG_TOPIC = "my_caop_bot_alerts_2026"
NTFY_DASHBOARD_TOPIC = "push_mrc_dashboards_7895"

# Колонки результата
_COLUMNS = [
    'personal_task_list_id', 'user_shift_id', 'task_id', 'task_status_id',
    'assignment_doc_id', 'assignment_id', 'job_execution_id', 'cito',
    'additional_request_id', 'is_refusing', 'refusal_reason_id', 'exception_reason',
    'is_transfer', 'comment', 'personal_task_list_created', 'personal_task_list_updated',
    'user_shift_start_date', 'user_shift_end_date', 'user_shift_created', 'user_shift_updated',
    'reception_type', 'mo_id', 'mu_id', 'modality_id', 'diagnostic_code', 'diagnostic_title',
    'birthday', 'is_norma', 'conduct_date', 'patient_id', 'is_equipment_task_completed',
    'has_active_pin', 'task_created', 'task_updated', 'additional_request_code',
    'additional_request_title', 'task_status_code', 'task_status_title', 'refusal_title',
    'reception_type_name', 'version_data', 'medical_employee_id', 'doctor_id', 'main_lpu_id',
    'department_id', 'speciality_id', 'min_doc_sdate', 'max_doc_edate', 'cur_doc_id',
    'fio', 'snils_cache', 'mei_sdate', 'mei_edate', 'cur_mei', 'study_uid', 'accession_number'
]


def send_log_ntfy_message(message: str):
    send_ntfy_alert(message=message, title="Task Lists Log", priority="default", tags="log")


def send_dashboard_ntfy_message(message: str, title: str = "Dashboard Update"):
    send_ntfy_alert(message=message, title=title, priority="high", tags="chart_with_upwards_trend", topic_override=NTFY_DASHBOARD_TOPIC)


def connect_vpn():
    logger.info("🔄 Запуск VPN клиента...")
    send_log_ntfy_message("🔄 Запуск VPN клиента...")
    try:
        if not os.path.exists(VPN_APP_PATH):
            raise FileNotFoundError(f"VPN-приложение не найдено: {VPN_APP_PATH}")
        process = subprocess.Popen(VPN_APP_PATH)
        logger.info(f"   Запущен процесс с PID: {process.pid}")
        time.sleep(15)

        windows = pyautogui.getWindowsWithTitle('')
        activated = False
        for window in windows:
            title = window.title.lower()
            if any(kw in title for kw in ['check point', 'trgui', 'endpoint']):
                try:
                    window.activate()
                    time.sleep(2)
                    logger.info(f"✅ Активировано окно: '{window.title}'")
                    activated = True
                    break
                except Exception as e:
                    logger.warning(f"Не удалось активировать окно '{window.title}': {e}")
        if not activated:
            logger.warning("⚠️ Не удалось активировать окно VPN-клиента.")

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
            time.sleep(0.05)

        time.sleep(1)
        pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
        time.sleep(15)
        logger.info("✅ Подключение к VPN инициировано.")
        send_log_ntfy_message("✅ Подключение к VPN инициировано.")
    except Exception as e:
        error_msg = f"❌ Ошибка при подключении к VPN: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        send_log_ntfy_message(error_msg)
        raise


def disconnect_vpn():
    logger.info("🛑 Отключение от VPN...")
    send_log_ntfy_message("🛑 Начинаю отключение от VPN...")
    try:
        pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
        time.sleep(2)
        pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
        time.sleep(3)
        pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
        time.sleep(5)
        logger.info("✅ Отключение от VPN завершено.")
        send_log_ntfy_message("✅ Отключение от VPN выполнено.")
    except Exception as e:
        error_msg = f"⚠️ Ошибка при отключении от VPN: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        send_log_ntfy_message(error_msg)


def ensure_ch_table():
    """Создаёт/пересоздаёт таблицу в целевой ClickHouse.
    Использует CREATE OR REPLACE чтобы исправить схему при изменениях (напр. Date → Date32).
    Данные всё равно перезаписываются через TRUNCATE перед каждой загрузкой.
    """
    logger.info("🗃️ Проверка/создание таблицы в целевой ClickHouse...")
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        # Date32 вместо Date: birthday пациентов до 1970 года выходит за uint16 диапазон.
        # Если таблица уже создана со старой схемой — удалите её вручную в CH,
        # при следующем запуске она пересоздастся правильно.
        client.command(f'''
        CREATE TABLE IF NOT EXISTS {CH_TABLE} (
            personal_task_list_id      Nullable(Int64),
            user_shift_id              Nullable(Int64),
            task_id                    Nullable(Int64),
            task_status_id             Nullable(Int64),
            assignment_doc_id          Nullable(String),
            assignment_id              Nullable(Int64),
            job_execution_id           Nullable(Int64),
            cito                       Nullable(String),
            additional_request_id      Nullable(Int64),
            is_refusing                Nullable(String),
            refusal_reason_id          Nullable(String),
            exception_reason           Nullable(String),
            is_transfer                Nullable(String),
            comment                    Nullable(String),
            personal_task_list_created Nullable(DateTime),
            personal_task_list_updated Nullable(DateTime),
            user_shift_start_date      Nullable(DateTime),
            user_shift_end_date        Nullable(DateTime),
            user_shift_created         Nullable(DateTime),
            user_shift_updated         Nullable(DateTime),
            reception_type             Nullable(String),
            mo_id                      Nullable(Int64),
            mu_id                      Nullable(Int64),
            modality_id                Nullable(String),
            diagnostic_code            Nullable(String),
            diagnostic_title           Nullable(String),
            birthday                   Nullable(Date32),
            is_norma                   Nullable(String),
            conduct_date               Nullable(DateTime),
            patient_id                 Nullable(Int64),
            is_equipment_task_completed Nullable(UInt8),
            has_active_pin             Nullable(UInt8),
            task_created               Nullable(DateTime),
            task_updated               Nullable(DateTime),
            additional_request_code    Nullable(String),
            additional_request_title   Nullable(String),
            task_status_code           Nullable(String),
            task_status_title          Nullable(String),
            refusal_title              Nullable(String),
            reception_type_name        Nullable(String),
            version_data               Nullable(DateTime),
            medical_employee_id        Nullable(Int64),
            doctor_id                  Nullable(Int64),
            main_lpu_id                Nullable(Int64),
            department_id              Nullable(Int64),
            speciality_id              Nullable(Int64),
            min_doc_sdate              Nullable(Date32),
            max_doc_edate              Nullable(Date32),
            cur_doc_id                 Nullable(Int64),
            fio                        Nullable(String),
            snils_cache                Nullable(String),
            mei_sdate                  Nullable(Date32),
            mei_edate                  Nullable(Date32),
            cur_mei                    Nullable(UInt8),
            study_uid                  Nullable(String),
            accession_number           Nullable(String)
        ) ENGINE = MergeTree()
        ORDER BY tuple()
        ''')
        client.close()
        logger.info(f"✅ Таблица {CH_TABLE} готова.")
        return True
    except Exception as e:
        error_msg = f"❌ Ошибка создания таблицы в целевой ClickHouse: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        send_log_ntfy_message(error_msg)
        return False


def extract_and_buffer_data():
    """VPN включён: запрос source ClickHouse → pickle-буфер."""
    logger.info("📥 Начало извлечения данных из ClickHouse...")
    send_log_ntfy_message("📥 Начало извлечения данных из ClickHouse...")

    client = None
    success = False

    try:
        connect_vpn()

        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False
        )
        logger.info("✅ Подключение к ClickHouse установлено.")

        query = f"""
        WITH
        mei_1 AS (
              SELECT
                medical_employee_id,
                medical_employee_fio_obf,
                snils_formatted_obf,
                sdate,
                edate,
                cur
              FROM dm_ap2.dct_medical_employees
              WHERE edate >= '2023-01-01'
              GROUP BY ALL),
        mei AS (
              SELECT
                medical_employee_id,
                medical_employee_fio_obf,
                snils_formatted_obf,
                MIN(sdate) AS sdate,
                MAX(edate) AS edate,
                cur
              FROM mei_1
              GROUP BY ALL),
        dd AS (
              SELECT
                doctor_id,
                medical_employee_id,
                main_lpu_id,
                department_id,
                speciality_id,
                sdate,
                toDate(REPLACE(toString(edate), '2299-12-31', '2099-12-31')) AS edate,
                cur
              FROM dm_ap2.dct_doctors
              WHERE edate >= '2023-01-01'),
        res_med_rab AS (
              SELECT
                dd.doctor_id,
                dd.medical_employee_id,
                dd.main_lpu_id,
                dd.department_id,
                dd.speciality_id,
                dd.sdate AS doc_sdate,
                dd.edate AS doc_edate,
                dd.cur AS cur_doc_id,
                mei.medical_employee_fio_obf AS fio,
                mei.snils_formatted_obf AS snils_cache,
                mei.sdate AS mei_sdate,
                mei.edate AS mei_edate,
                mei.cur AS cur_mei
              FROM dd
              INNER JOIN mei
                ON dd.medical_employee_id = mei.medical_employee_id
              WHERE
                dd.edate >= mei.sdate
                AND dd.edate <= mei.edate
              ORDER BY mei.medical_employee_id, mei.sdate, doctor_id),
        grouped_res_med_rab AS (
              SELECT
                DISTINCT
                medical_employee_id,
                doctor_id,
                main_lpu_id,
                department_id,
                speciality_id,
                MIN(doc_sdate) AS min_doc_sdate,
                MAX(doc_edate) AS max_doc_edate,
                cur_doc_id,
                fio,
                snils_cache,
                mei_sdate,
                mei_edate,
                cur_mei
              FROM res_med_rab
              GROUP BY ALL
              ORDER BY medical_employee_id, mei_sdate, doctor_id),
        dop_info AS (
              SELECT
                DISTINCT assignment_id,
                study_uid,
                accession_number
              FROM data_views.v_instrumental_examinations
              GROUP BY ALL
        )
        SELECT
          tl.personal_task_list_id,
          tl.user_shift_id,
          tl.task_id,
          tl.task_status_id,
          tl.assignment_doc_id,
          tl.assignment_id,
          tl.job_execution_id,
          tl.cito,
          tl.additional_request_id,
          tl.is_refusing,
          tl.refusal_reason_id,
          tl.exception_reason,
          tl.is_transfer,
          tl.comment,
          tl.personal_task_list_created,
          tl.personal_task_list_updated,
          tl.user_shift_start_date,
          tl.user_shift_end_date,
          tl.user_shift_created,
          tl.user_shift_updated,
          tl.reception_type,
          tl.mo_id,
          tl.mu_id,
          tl.modality_id,
          tl.diagnostic_code,
          tl.diagnostic_title,
          tl.birthday,
          tl.is_norma,
          tl.conduct_date,
          tl.patient_id,
          tl.is_equipment_task_completed,
          tl.has_active_pin,
          tl.task_created,
          tl.task_updated,
          tl.additional_request_code,
          tl.additional_request_title,
          tl.task_status_code,
          tl.task_status_title,
          tl.refusal_title,
          tl.reception_type_name,
          tl.version_data,
          mr.medical_employee_id,
          mr.doctor_id,
          mr.main_lpu_id,
          mr.department_id,
          mr.speciality_id,
          mr.min_doc_sdate,
          mr.max_doc_edate,
          mr.cur_doc_id,
          mr.fio,
          mr.snils_cache,
          mr.mei_sdate,
          mr.mei_edate,
          CASE WHEN mr.cur_mei = 1 THEN toUInt8(1) ELSE toUInt8(0) END AS cur_mei,
          vie.study_uid,
          vie.accession_number
        FROM data_views.v_instrumental_task_lists tl
        LEFT JOIN grouped_res_med_rab mr
          ON tl.job_execution_id = mr.doctor_id
        LEFT JOIN dop_info vie
          ON tl.assignment_id = vie.assignment_id
        WHERE tl.personal_task_list_created >= now() - INTERVAL {DAYS_TO_SYNC} DAY
        """

        logger.info("📤 Выполнение запроса к ClickHouse...")
        result = client.query(query)
        rows = result.result_rows
        logger.info(f"📋 Получено {len(rows)} строк.")

        df = pd.DataFrame(rows, columns=_COLUMNS)
        with open(_BUFFER_TASK_LISTS, 'wb') as f:
            _pickle.dump(df, f)
        logger.info(f"💾 Сохранено {len(df)} строк в pickle-буфер.")
        success = True

    except Exception as e:
        error_msg = f"❌ Ошибка при извлечении данных: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        send_log_ntfy_message(error_msg)
        success = False
    finally:
        if client:
            client.close()
        try:
            disconnect_vpn()
        except Exception as e:
            logger.error(f"⚠️ Ошибка при отключении от VPN: {e}")

    return success


def _bool_to_uint8(x):
    """Конвертирует bool/str/int/None → Nullable UInt8.
    CH-view может вернуть булевые значения как str 'True'/'False'."""
    if x is None:
        return None
    try:
        if isinstance(x, float) and pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, bool):
        return 1 if x else 0
    if isinstance(x, str):
        return 1 if x.lower() in ('true', '1', 'yes') else 0
    try:
        return int(bool(x))
    except (TypeError, ValueError):
        return None


def load_buffer_to_ch():
    """pickle-буфер → target ClickHouse (TRUNCATE + INSERT)."""
    logger.info("📤 Загрузка данных из буфера в целевую ClickHouse...")
    send_log_ntfy_message("📤 Загрузка данных в целевую ClickHouse...")

    try:
        if not os.path.exists(_BUFFER_TASK_LISTS):
            logger.warning("⚠️ Буфер не найден, загрузка пропущена.")
            return True

        with open(_BUFFER_TASK_LISTS, 'rb') as f:
            df = _pickle.load(f)

        if df is None or df.empty:
            logger.info("Буфер пуст — загрузка не требуется.")
            send_log_ntfy_message("Буфер пуст — загрузка в целевую ClickHouse не выполнена.")
            return True

        if not ensure_ch_table():
            return False

        # Приведение булевых колонок к UInt8 (CH-view возвращает их как str 'True'/'False')
        for col in ['is_equipment_task_completed', 'has_active_pin', 'cur_mei']:
            if col in df.columns:
                df[col] = df[col].apply(_bool_to_uint8)

        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )

        logger.info(f"🧹 Очистка таблицы {CH_TABLE}...")
        client.command(f'TRUNCATE TABLE IF EXISTS {CH_TABLE}')

        logger.info(f"📤 Загрузка {len(df)} строк в {CH_DATABASE_TARGET}.{CH_TABLE}...")
        client.insert_df(CH_TABLE, df)
        client.close()

        logger.info(f"✅ Данные успешно загружены в {CH_TABLE}.")
        send_log_ntfy_message(f"✅ Успешно выгружено {len(df)} строк в {CH_TABLE}.")
        return True

    except Exception as e:
        error_msg = f"❌ Ошибка загрузки в целевую ClickHouse: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        send_log_ntfy_message(error_msg)
        return False


def export_task_lists_data():
    logger.info("🚀 Запуск полного цикла экспорта данных...")
    send_log_ntfy_message("🚀 Запуск экспорта данных v_instrumental_task_lists")

    extract_success = extract_and_buffer_data()
    if not extract_success:
        logger.error("❌ Этап извлечения данных завершился неудачей.")
        send_dashboard_ntfy_message("❌ Не удалось обновить дашборд: ошибка извлечения данных из ClickHouse.", title="Task Lists Error")
        return False

    load_success = load_buffer_to_ch()
    if not load_success:
        logger.error("❌ Этап загрузки в целевую ClickHouse завершился неудачей.")
        send_dashboard_ntfy_message("❌ Не удалось обновить дашборд: ошибка загрузки в целевую ClickHouse.", title="Task Lists Error")
        return False

    return True


def main():
    start_time = datetime.now()
    logger.info("="*60)
    logger.info("▶️ Скрипт экспорта данных v_instrumental_task_lists запущен")
    send_log_ntfy_message("▶️ Скрипт экспорта данных v_instrumental_task_lists запущен")

    success = False
    try:
        success = export_task_lists_data()
    except Exception as e:
        error_msg = f"🔥 КРИТИЧЕСКАЯ ОШИБКА: {e}\n{traceback.format_exc()}"
        logger.critical(error_msg)
        send_log_ntfy_message(error_msg)
        send_dashboard_ntfy_message("❌ КРИТИЧЕСКАЯ ОШИБКА: скрипт аварийно завершил работу.", title="Task Lists Critical Error")
        success = False

    # Удаление буфера
    try:
        if os.path.exists(_BUFFER_TASK_LISTS):
            os.remove(_BUFFER_TASK_LISTS)
            logger.info("🗑️ Временный буфер удалён.")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить буфер: {e}")

    duration = datetime.now() - start_time
    if success:
        logger.info("✅ Экспорт завершён успешно!")
        send_log_ntfy_message(f"✅ Экспорт завершён успешно! (Время выполнения: {duration})")
        send_ntfy_alert(
            "✅ Дашборд рабочие списки завершен успешно.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"
        )
    else:
        send_ntfy_alert(
            "❌ Дашборд рабочие списки завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"
        )

    return success


# ===========================================================================
# Двухфазное выполнение для all_dashboards_up.py
# ===========================================================================

def extract_phase():
    """Фаза 1 (VPN включён): source ClickHouse → pickle-буфер. Без управления VPN."""
    logger.info(f"📥 [task_lists] extract_phase: запрос к source ClickHouse за {DAYS_TO_SYNC} дней...")
    client = None
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False
        )
        query = f"""
        WITH
        mei_1 AS (
              SELECT medical_employee_id, medical_employee_fio_obf, snils_formatted_obf, sdate, edate, cur
              FROM dm_ap2.dct_medical_employees WHERE edate >= '2023-01-01' GROUP BY ALL),
        mei AS (
              SELECT medical_employee_id, medical_employee_fio_obf, snils_formatted_obf,
                MIN(sdate) AS sdate, MAX(edate) AS edate, cur
              FROM mei_1 GROUP BY ALL),
        dd AS (
              SELECT doctor_id, medical_employee_id, main_lpu_id, department_id, speciality_id,
                sdate, toDate(REPLACE(toString(edate), '2299-12-31', '2099-12-31')) AS edate, cur
              FROM dm_ap2.dct_doctors WHERE edate >= '2023-01-01'),
        res_med_rab AS (
              SELECT dd.doctor_id, dd.medical_employee_id, dd.main_lpu_id, dd.department_id, dd.speciality_id,
                dd.sdate AS doc_sdate, dd.edate AS doc_edate, dd.cur AS cur_doc_id,
                mei.medical_employee_fio_obf AS fio, mei.snils_formatted_obf AS snils_cache,
                mei.sdate AS mei_sdate, mei.edate AS mei_edate, mei.cur AS cur_mei
              FROM dd INNER JOIN mei ON dd.medical_employee_id = mei.medical_employee_id
              WHERE dd.edate >= mei.sdate AND dd.edate <= mei.edate
              ORDER BY mei.medical_employee_id, mei.sdate, doctor_id),
        grouped_res_med_rab AS (
              SELECT DISTINCT medical_employee_id, doctor_id, main_lpu_id, department_id, speciality_id,
                MIN(doc_sdate) AS min_doc_sdate, MAX(doc_edate) AS max_doc_edate, cur_doc_id,
                fio, snils_cache, mei_sdate, mei_edate, cur_mei
              FROM res_med_rab GROUP BY ALL ORDER BY medical_employee_id, mei_sdate, doctor_id),
        dop_info AS (
              SELECT DISTINCT assignment_id, study_uid, accession_number
              FROM data_views.v_instrumental_examinations GROUP BY ALL)
        SELECT
          tl.personal_task_list_id, tl.user_shift_id, tl.task_id, tl.task_status_id,
          tl.assignment_doc_id, tl.assignment_id, tl.job_execution_id, tl.cito,
          tl.additional_request_id, tl.is_refusing, tl.refusal_reason_id, tl.exception_reason,
          tl.is_transfer, tl.comment, tl.personal_task_list_created, tl.personal_task_list_updated,
          tl.user_shift_start_date, tl.user_shift_end_date, tl.user_shift_created, tl.user_shift_updated,
          tl.reception_type, tl.mo_id, tl.mu_id, tl.modality_id, tl.diagnostic_code, tl.diagnostic_title,
          tl.birthday, tl.is_norma, tl.conduct_date, tl.patient_id,
          tl.is_equipment_task_completed, tl.has_active_pin, tl.task_created, tl.task_updated,
          tl.additional_request_code, tl.additional_request_title, tl.task_status_code,
          tl.task_status_title, tl.refusal_title, tl.reception_type_name, tl.version_data,
          mr.medical_employee_id, mr.doctor_id, mr.main_lpu_id, mr.department_id, mr.speciality_id,
          mr.min_doc_sdate, mr.max_doc_edate, mr.cur_doc_id, mr.fio, mr.snils_cache,
          mr.mei_sdate, mr.mei_edate,
          CASE WHEN mr.cur_mei = 1 THEN toUInt8(1) ELSE toUInt8(0) END AS cur_mei,
          vie.study_uid, vie.accession_number
        FROM data_views.v_instrumental_task_lists tl
        LEFT JOIN grouped_res_med_rab mr ON tl.job_execution_id = mr.doctor_id
        LEFT JOIN dop_info vie ON tl.assignment_id = vie.assignment_id
        WHERE tl.personal_task_list_created >= now() - INTERVAL {DAYS_TO_SYNC} DAY
        """
        result = client.query(query)
        client.close()
        client = None
        df = pd.DataFrame(result.result_rows, columns=_COLUMNS)
        with open(_BUFFER_TASK_LISTS, 'wb') as f:
            _pickle.dump(df, f)
        logger.info(f"  ✅ [task_lists] extract_phase: сохранено {len(df)} строк в буфер.")
        return True
    except Exception as e:
        logger.error(f"  ❌ [task_lists] extract_phase: {e}")
        if client:
            client.close()
        return False


def load_phase():
    """Фаза 2 (VPN выключен): pickle-буфер → target ClickHouse (TRUNCATE + INSERT)."""
    logger.info("📤 [task_lists] load_phase: загрузка из буфера в target ClickHouse...")
    if not os.path.exists(_BUFFER_TASK_LISTS):
        logger.warning("  📭 [task_lists] load_phase: буфер не найден.")
        return True
    try:
        with open(_BUFFER_TASK_LISTS, 'rb') as f:
            df = _pickle.load(f)
        os.remove(_BUFFER_TASK_LISTS)
    except Exception as e:
        logger.error(f"  ❌ [task_lists] load_phase: не удалось прочитать буфер: {e}")
        return False

    if df is None or df.empty:
        logger.warning("  ⚠️ [task_lists] load_phase: буфер пуст, вставка не требуется.")
        return True

    try:
        if not ensure_ch_table():
            return False

        for col in ['is_equipment_task_completed', 'has_active_pin', 'cur_mei']:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else int(x))

        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        client.command(f'TRUNCATE TABLE IF EXISTS {CH_TABLE}')
        client.insert_df(CH_TABLE, df)
        client.close()
        logger.info(f"  ✅ [task_lists] load_phase: вставлено {len(df)} строк в {CH_TABLE}.")
        return True
    except Exception as e:
        logger.error(f"  ❌ [task_lists] load_phase: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
