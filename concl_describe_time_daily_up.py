"""
concl_describe_time_daily_up.py
Синхронизация данных describe_time_daily из исходной ClickHouse в целевую.
Выборка: все процедуры (без ограничения по кодам КОДАП) за последние
DAYS_TO_SYNC дней, время описания ≤ MAX_DURATION_HOURS ч.
Агрегация на стороне source CH: дата × код × процедура × модальность → среднее время.
Полная перезапись таблицы при каждом запуске (TRUNCATE + INSERT).
Все уведомления отправляются через ntfy.

# INPUT:  data_views.v_eris_assignment_results (source CH datalake.emias.ru, через VPN)
# OUTPUT: describe_time_daily (target CH, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import pickle as _pickle
import clickhouse_connect
from datetime import datetime

from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки целевой ClickHouse ===
CH_HOST_TARGET     = cfg.CH_HOST_TARGET
CH_PORT_TARGET     = cfg.CH_PORT_TARGET
CH_USER_TARGET     = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

# === Настройки исходной ClickHouse ===
CH_HOST_SOURCE     = cfg.CH_HOST
CH_PORT_SOURCE     = cfg.CH_PORT
CH_USER_SOURCE     = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE

# === Настройки VPN ===
VPN_APP_PATH            = cfg.VPN_APP_PATH
VPN_PASSWORD            = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

# === Параметры выборки ===
MU_ID              = '100001912827'
MAX_DURATION_HOURS = 6
DAYS_TO_SYNC       = 30

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PKL  = os.path.join(_SCRIPTS_DIR, 'temp_buffer_describe_time_daily.pkl')
_TABLE_NAME  = 'describe_time_daily'

_COLUMNS = [
    'report_date', 'diagnostic_code', 'diagnostic_name', 'modality',
    'n_studies', 'avg_seconds', 'avg_time', 'load_datetime',
]

_SOURCE_QUERY = f"""
WITH studies AS (
    SELECT
        accession_number,
        diagnostic_code,
        any(diagnostic_name)                    AS diagnostic_name,
        any(device_type)                        AS modality,
        min(assignment_describe_start_date)     AS describe_start,
        min(assignment_result_doc_created_date) AS describe_end
    FROM data_views.v_eris_assignment_results
    WHERE assignment_describe_mu_id = %(mu_id)s
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      AND assignment_result_doc_created_date >= now() - INTERVAL {DAYS_TO_SYNC} DAY
      AND assignment_result_emp_fio != 'Кивасёв С. А.'
      AND assignment_result_emp_fio != 'Каспарьян Л. Г.'
    GROUP BY accession_number, diagnostic_code
    HAVING describe_end >= describe_start
       AND dateDiff('second', describe_start, describe_end) <= %(max_duration_seconds)s
)
SELECT
    toDate(describe_end)                                  AS report_date,
    diagnostic_code,
    diagnostic_name,
    modality,
    count()                                                AS n_studies,
    avg(dateDiff('second', describe_start, describe_end))  AS avg_seconds
FROM studies
GROUP BY report_date, diagnostic_code, diagnostic_name, modality
ORDER BY report_date, diagnostic_code
"""


def _format_hms(seconds: float) -> str:
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}'


# === VPN ===

def connect_vpn():
    print("Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для describe_time_daily...", title="VPN Connect", priority="default", tags="lock")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        msg = f"Ошибка запуска VPN: {e}"
        print(msg)
        send_ntfy_alert(msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    time.sleep(15)

    for window in pyautogui.getWindowsWithTitle(''):
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"   Окно '{window.title}' активировано.")
                break
            except Exception:
                pass

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
    print("VPN подключён.")
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


def disconnect_vpn():
    print("Отключаю VPN...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("VPN отключён.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


def _ensure_table_exists(client) -> None:
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            report_date      Date,
            diagnostic_code  String,
            diagnostic_name  String,
            modality         String,
            n_studies        UInt32,
            avg_seconds      Float64,
            avg_time         String,
            load_datetime    DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (report_date, diagnostic_code, modality)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


# === Фазовый интерфейс для all_dashboards_up.py ===

def extract_phase() -> bool:
    """VPN включён — source CH → pickle-буфер."""
    print(f"[describe_time_daily] extract_phase: запрос к source CH "
          f"(все процедуры, последние {DAYS_TO_SYNC} дн., ≤ {MAX_DURATION_HOURS} ч)...")

    client_source = None
    try:
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        result = client_source.query(
            _SOURCE_QUERY,
            parameters={
                'mu_id': MU_ID,
                'max_duration_seconds': MAX_DURATION_HOURS * 3600,
            },
        )
        rows = result.result_rows
        client_source.close()
        client_source = None

        with open(_BUFFER_PKL, 'wb') as f:
            _pickle.dump(rows, f)
        print(f"[describe_time_daily] extract_phase: сохранено {len(rows)} строк в буфер.")
        return True
    except Exception as e:
        print(f"[describe_time_daily] extract_phase ошибка: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase() -> bool:
    """VPN выключен — pickle-буфер → TRUNCATE + INSERT в target CH."""
    if not os.path.exists(_BUFFER_PKL):
        print("[describe_time_daily] load_phase: буфер не найден")
        return False

    try:
        with open(_BUFFER_PKL, 'rb') as f:
            raw_rows = _pickle.load(f)
        os.remove(_BUFFER_PKL)
    except Exception as e:
        print(f"[describe_time_daily] load_phase: ошибка чтения буфера: {e}")
        return False

    if not raw_rows:
        print("[describe_time_daily] load_phase: буфер пуст, пропускаю.")
        return True

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"[describe_time_daily] Подключаюсь к целевой ClickHouse (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                return False

    try:
        _ensure_table_exists(client_target)
        print(f"Очищаю таблицу {_TABLE_NAME} (TRUNCATE)...")
        client_target.command(f"TRUNCATE TABLE {_TABLE_NAME}")

        load_dt = datetime.now().replace(microsecond=0)
        processed_rows = []
        for row in raw_rows:
            report_date, diagnostic_code, diagnostic_name, modality, n_studies, avg_seconds = row

            processed_rows.append([
                report_date,
                str(diagnostic_code)  if diagnostic_code  is not None else '',
                str(diagnostic_name)  if diagnostic_name  is not None else '',
                str(modality)         if modality         is not None else '',
                int(n_studies)        if n_studies         is not None else 0,
                float(avg_seconds)    if avg_seconds       is not None else 0.0,
                _format_hms(avg_seconds) if avg_seconds is not None else '0:00:00',
                load_dt,
            ])

        print(f"[describe_time_daily] load_phase: вставляю {len(processed_rows)} строк в {_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)
        print(f"[describe_time_daily] load_phase: вставлено {len(processed_rows)} строк.")
        client_target.close()
        client_target = None
        return True
    except Exception as e:
        print(f"[describe_time_daily] load_phase ошибка: {e}")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PKL):
            try:
                os.remove(_BUFFER_PKL)
            except Exception:
                pass
        return False


# === Standalone-запуск (с собственным VPN-циклом) ===

def export_describe_time_daily() -> bool:
    """Полный цикл: VPN → source CH → pickle → VPN off → target CH."""
    print(f"[describe_time_daily] Начинаю синхронизацию (все процедуры, последние {DAYS_TO_SYNC} дн., ≤ {MAX_DURATION_HOURS} ч)...")
    send_ntfy_alert(
        "Начинаю синхронизацию describe_time_daily...",
        title="describe_time_daily Start",
        priority="default",
        tags="inbox",
    )

    extract_ok = False
    try:
        connect_vpn()
        extract_ok = extract_phase()
    except Exception as e:
        print(f"Ошибка VPN/extract: {e}")
        send_ntfy_alert(f"Сбой describe_time_daily extract: {str(e)[:80]}",
                        title="describe_time_daily Error", priority="urgent", tags="fire")
        try:
            disconnect_vpn()
        except Exception:
            pass
        if os.path.exists(_BUFFER_PKL):
            try:
                os.remove(_BUFFER_PKL)
            except Exception:
                pass
        return False

    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning",
                        priority="default", tags="warning")

    if not extract_ok:
        send_ntfy_alert("Ошибка extract describe_time_daily", title="describe_time_daily Error",
                        priority="urgent", tags="fire")
        return False

    result = load_phase()

    if result:
        send_ntfy_alert(
            "✅ describe_time_daily обновлён.",
            title="describe_time_daily Success",
            priority="high",
            tags="white_check_mark",
        )
    else:
        send_ntfy_alert(
            "❌ describe_time_daily: ошибка загрузки в target CH",
            title="describe_time_daily Error",
            priority="urgent",
            tags="warning",
        )

    return result


def main():
    print("Запуск concl_describe_time_daily_up")
    send_ntfy_alert(
        "Запускаю concl_describe_time_daily_up...",
        title="describe_time_daily Start",
        priority="default",
        tags="robot",
    )

    success = export_describe_time_daily()

    if success:
        send_ntfy_alert(
            "✅ Обновление describe_time_daily завершено!",
            title="Dashboard Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "❌ Обновление describe_time_daily завершилось с ошибкой!",
            title="Dashboard Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
