"""
ари_кис_up.py
Синхронизация данных АРИ КИС из исходной ClickHouse (via VPN) в целевую ClickHouse (dit_dm).
Таблица назначения: dit_dm.ari_kis (создаётся автоматически)

# INPUT:  kis.eris, kis.instrumental, kis.emp, kis.v_radiology_result_doc,
#          dm_ap2.dct_medical_employees, dm_ap2.dct_doctors  (source CH, через VPN)
# OUTPUT: ari_kis (target CH, dit_dm, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import sqlite3
import clickhouse_connect
from datetime import datetime, timedelta
import datetime as datetime_module

from ntfy_notifier import send_ntfy_alert
import personal_config2 as cfg

# === Настройки синхронизации (плавающие даты) ===
DAYS_TO_SYNC = 6

# === Настройки исходной ClickHouse ===
CH_HOST_SOURCE     = cfg.CH_HOST
CH_PORT_SOURCE     = cfg.CH_PORT
CH_USER_SOURCE     = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE_KIS  # 'kis'

# === Настройки целевой ClickHouse (Yandex Cloud) ===
CH_HOST_TARGET     = cfg.CH_HOST_TARGET
CH_PORT_TARGET     = cfg.CH_PORT_TARGET
CH_USER_TARGET     = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_DIT  # 'dit_dm'

_TABLE_NAME = 'ari_kis'

# === VPN ===
VPN_APP_PATH           = cfg.VPN_APP_PATH
VPN_PASSWORD           = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PATH = os.path.join(_SCRIPTS_DIR, 'temp_buffer_ari_kis.db')

_COLUMNS = [
    'accession_number', 'study_uid', 'naz_code', 'naz_name', 'device_type', 'facility_id',
    'time_created', 'emias_id', 'sign_date', 'sign_datetime',
    'patient_id', 'pay_type_name', 'protocol_id',
    'sign_emp_id', 'sign_emp_fio', 'sign_emp_fio_short',
    'sign_emp_medical_employee_id', 'sign_emp_doctor_id',
    'creator_emp_id', 'creator_emp_fio', 'creator_emp_fio_short',
    'creator_emp_medical_employee_id', 'creator_emp_doctor_id',
    'description', 'conclusion', 'birth_date', 'load_datetime',
]


# === SQLite адаптеры ===
def setup_sqlite_adapters():
    def adapt_date(val):     return val.isoformat()
    def adapt_datetime(val): return val.isoformat()
    def convert_date(val):   return datetime_module.date.fromisoformat(val.decode())
    def convert_ts(val):     return datetime_module.datetime.fromisoformat(val.decode())

    sqlite3.register_adapter(datetime_module.date, adapt_date)
    sqlite3.register_adapter(datetime_module.datetime, adapt_datetime)
    sqlite3.register_converter("date", convert_date)
    sqlite3.register_converter("timestamp", convert_ts)


# === VPN ===
def connect_vpn():
    print("Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для ari_kis...", title="VPN Connect", priority="default", tags="lock")
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
                print(f"Окно '{window.title}' активировано.")
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
            accession_number                String,
            study_uid                       String,
            naz_code                        String,
            naz_name                        String,
            device_type                     String,
            facility_id                     String,
            time_created                    DateTime,
            emias_id                        String,
            sign_date                       Date,
            sign_datetime                   DateTime64(3, 'UTC'),
            patient_id                      String,
            pay_type_name                   String,
            protocol_id                     String,
            sign_emp_id                     String,
            sign_emp_fio                    String,
            sign_emp_fio_short              String,
            sign_emp_medical_employee_id    Nullable(Int64),
            sign_emp_doctor_id              Nullable(Int64),
            creator_emp_id                  String,
            creator_emp_fio                 String,
            creator_emp_fio_short           String,
            creator_emp_medical_employee_id Nullable(Int64),
            creator_emp_doctor_id           Nullable(Int64),
            description                     String,
            conclusion                      String,
            birth_date                      Nullable(String),
            load_datetime                   DateTime DEFAULT now()
        )
        ENGINE = MergeTree()
        ORDER BY (sign_date, accession_number)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


def _build_query(n_days_ago: str) -> str:
    return f"""
WITH

snils_to_doctor AS (
    SELECT
        mei.snils_formatted_obf                                           AS snils_cache,
        argMax(mei.medical_employee_fio_obf, tuple(mei.cur, mei.edate))   AS fio_short,
        argMax(mei.medical_employee_id,      tuple(mei.cur, mei.edate))   AS medical_employee_id,
        argMax(dd.doctor_id,                 tuple(dd.cur,  dd.edate))    AS doctor_id
    FROM dm_ap2.dct_medical_employees mei
    INNER JOIN dm_ap2.dct_doctors dd
           ON mei.medical_employee_id = dd.medical_employee_id
    WHERE mei.edate >= '2023-01-01'
      AND dd.edate  >= '2023-01-01'
    GROUP BY mei.snils_formatted_obf
),

instrumental_agg AS (
    SELECT naz_id, time_created, emias_id, sign_dt, patient_id,
           pay_type_name, sign_emp_id, creator_emp_id, protocol_id
    FROM (
        SELECT *,
               row_number() OVER (PARTITION BY naz_id ORDER BY sign_dt ASC) AS rn
        FROM kis.instrumental
    ) sub
    WHERE rn = 1
),

current_emp AS (
    SELECT
        id,
        any(surname)  AS surname,
        any(name)     AS name,
        any(patron)   AS patron,
        anyIf(snils_cache, snils_cache IS NOT NULL AND snils_cache != '') AS snils_cache
    FROM kis.emp
    GROUP BY id
),

descriptions AS (
    SELECT
        protocol_id,
        any(results_description) AS description,
        any(conclusion)          AS conclusion
    FROM kis.v_radiology_result_doc
    WHERE reading_type = 'Первое чтение'
    GROUP BY protocol_id
),

patients_uniq AS (
    SELECT
        emias_id,
        any(birth_date) AS birth_date
    FROM kis.patients
    GROUP BY emias_id
)

SELECT
    e.accession_number                                    AS accession_number,
    e.studyuid                                            AS study_uid,
    e.naz_code                                            AS naz_code,
    e.naz_name                                            AS naz_name,
    e.device_type                                         AS device_type,
    e.facility_id                                         AS facility_id,
    i.time_created                                        AS time_created,
    i.emias_id                                            AS emias_id,
    toDate(i.sign_dt)                                     AS sign_date,
    toDateTime64(i.sign_dt, 3, 'UTC')                     AS sign_datetime,
    i.patient_id                                          AS patient_id,
    i.pay_type_name                                       AS pay_type_name,
    i.protocol_id                                         AS protocol_id,
    i.sign_emp_id                                         AS sign_emp_id,
    trimBoth(concat(
        coalesce(se.surname, ''), ' ',
        coalesce(se.name, ''),
        if(coalesce(se.patron, '') = '', '', concat(' ', se.patron))
    ))                                                    AS sign_emp_fio,
    sd.fio_short                                          AS sign_emp_fio_short,
    sd.medical_employee_id                                AS sign_emp_medical_employee_id,
    sd.doctor_id                                          AS sign_emp_doctor_id,
    i.creator_emp_id                                      AS creator_emp_id,
    trimBoth(concat(
        coalesce(ce.surname, ''), ' ',
        coalesce(ce.name, ''),
        if(coalesce(ce.patron, '') = '', '', concat(' ', ce.patron))
    ))                                                    AS creator_emp_fio,
    cd.fio_short                                          AS creator_emp_fio_short,
    cd.medical_employee_id                                AS creator_emp_medical_employee_id,
    cd.doctor_id                                          AS creator_emp_doctor_id,
    d.description                                         AS description,
    d.conclusion                                          AS conclusion,
    p.birth_date                                          AS birth_date,
    now()                                                 AS load_datetime

FROM kis.eris e
LEFT JOIN instrumental_agg i  ON e.naz_id           = i.naz_id
LEFT JOIN current_emp se       ON i.sign_emp_id      = se.id
LEFT JOIN current_emp ce       ON i.creator_emp_id   = ce.id
LEFT JOIN descriptions d       ON i.protocol_id      = d.protocol_id
LEFT JOIN snils_to_doctor sd   ON se.snils_cache     = sd.snils_cache
LEFT JOIN snils_to_doctor cd   ON ce.snils_cache     = cd.snils_cache
LEFT JOIN patients_uniq p      ON i.emias_id         = p.emias_id

WHERE
    e.studyuid IS NOT NULL
    AND e.studyuid != ''
    AND i.sign_dt >= '{n_days_ago}'
    AND i.emias_id IS NOT NULL
    AND sd.medical_employee_id IS NOT NULL
    AND e.naz_code IS NOT NULL
    AND d.description IS NOT NULL AND d.description != ''
    AND d.conclusion  IS NOT NULL AND d.conclusion  != ''

ORDER BY i.sign_dt DESC NULLS LAST
"""


def _sanitize_for_sqlite(rows: list) -> list:
    import uuid
    result = []
    for row in rows:
        result.append([str(v) if isinstance(v, uuid.UUID) else v for v in row])
    return result


def _parse_datetime(val) -> datetime_module.datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime_module.datetime):
        return val.replace(tzinfo=None)
    s = str(val).strip()
    for i in range(len(s) - 1, 9, -1):
        if s[i] in ('+', '-'):
            s = s[:i]
            break
        if s[i] == 'Z':
            s = s[:i]
            break
    s = s.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _process_rows(raw_rows: list) -> list:
    result = []
    for row in raw_rows:
        (
            accession_number, study_uid, naz_code, naz_name, device_type, facility_id,
            time_created, emias_id, sign_date, sign_datetime,
            patient_id, pay_type_name, protocol_id,
            sign_emp_id, sign_emp_fio, sign_emp_fio_short,
            sign_emp_medical_employee_id, sign_emp_doctor_id,
            creator_emp_id, creator_emp_fio, creator_emp_fio_short,
            creator_emp_medical_employee_id, creator_emp_doctor_id,
            description, conclusion, birth_date, load_datetime,
        ) = row

        if isinstance(sign_date, datetime_module.date) and not isinstance(sign_date, datetime_module.datetime):
            d = sign_date
        elif sign_date is not None:
            try:
                d = datetime.strptime(str(sign_date)[:10], '%Y-%m-%d').date()
            except Exception:
                d = datetime(1900, 1, 1).date()
        else:
            d = datetime(1900, 1, 1).date()

        tc = _parse_datetime(time_created) or datetime(1900, 1, 1)
        sd_dt = _parse_datetime(sign_datetime) or datetime(1900, 1, 1)
        ld = _parse_datetime(load_datetime) or datetime.now()

        bd_str = str(birth_date).strip() if birth_date is not None else ''
        if bd_str and bd_str.lower() != 'none':
            if isinstance(birth_date, datetime_module.date):
                bd = birth_date.strftime('%Y-%m-%d')
            else:
                try:
                    bd = datetime.strptime(bd_str[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
                except Exception:
                    bd = None
        else:
            bd = None

        def s(v): return v if v is not None else ''
        def n(v): return int(v) if v is not None else None

        result.append([
            s(accession_number), s(study_uid), s(naz_code), s(naz_name),
            s(device_type), s(facility_id),
            tc, s(emias_id), d, sd_dt,
            s(patient_id), s(pay_type_name), s(protocol_id),
            s(sign_emp_id), s(sign_emp_fio), s(sign_emp_fio_short),
            n(sign_emp_medical_employee_id), n(sign_emp_doctor_id),
            s(creator_emp_id), s(creator_emp_fio), s(creator_emp_fio_short),
            n(creator_emp_medical_employee_id), n(creator_emp_doctor_id),
            s(description), s(conclusion), bd, ld,
        ])
    return result


def sync_ari_kis(days: int) -> bool:
    today      = datetime.now().date()
    n_days_ago = (today - timedelta(days=days)).isoformat()

    print(f"[ari_kis] Синхронизация за последние {days} дней (с {n_days_ago}).")
    send_ntfy_alert(
        f"Начинаю синхронизацию ari_kis за {days} дней...",
        title="ari_kis Start", priority="default", tags="inbox",
    )

    setup_sqlite_adapters()

    # Шаг 1: Очистка целевой ClickHouse за период
    client_target_del = None
    try:
        print("Подключаюсь к целевой ClickHouse для очистки...")
        client_target_del = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )
        _ensure_table_exists(client_target_del)
        delete_query = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE sign_date >= '{n_days_ago}'"
        print(f"   DELETE для sign_date >= {n_days_ago}")
        client_target_del.command(delete_query)
        print("Старые данные удалены.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"Ошибка очистки целевой ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="ari_kis Clean Error", priority="urgent", tags="database")
        if client_target_del:
            client_target_del.close()
        return False

    # Шаг 2: Извлечение из source CH через VPN → SQLite буфер
    client_source = None
    try:
        connect_vpn()

        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("Исходная ClickHouse подключена.")

        query    = _build_query(n_days_ago)
        print("Выполняю запрос к исходной ClickHouse...")
        result   = client_source.query(query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None
        print(f"Получено {len(raw_rows)} строк.")

        if not raw_rows:
            print(f"Нет данных за последние {days} дней.")
            send_ntfy_alert(f"Нет данных ari_kis за {days} дней",
                            title="ari_kis Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # Шаг 3: SQLite буфер
        print(f"Создаю SQLite буфер ({_BUFFER_PATH})...")
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS temp_ari_kis;")
        cursor.execute("""
            CREATE TABLE temp_ari_kis (
                accession_number                TEXT,
                study_uid                       TEXT,
                naz_code                        TEXT,
                naz_name                        TEXT,
                device_type                     TEXT,
                facility_id                     TEXT,
                time_created                    TEXT,
                emias_id                        TEXT,
                sign_date                       TEXT,
                sign_datetime                   TEXT,
                patient_id                      TEXT,
                pay_type_name                   TEXT,
                protocol_id                     TEXT,
                sign_emp_id                     TEXT,
                sign_emp_fio                    TEXT,
                sign_emp_fio_short              TEXT,
                sign_emp_medical_employee_id    INTEGER,
                sign_emp_doctor_id              INTEGER,
                creator_emp_id                  TEXT,
                creator_emp_fio                 TEXT,
                creator_emp_fio_short           TEXT,
                creator_emp_medical_employee_id INTEGER,
                creator_emp_doctor_id           INTEGER,
                description                     TEXT,
                conclusion                      TEXT,
                birth_date                      TEXT,
                load_datetime                   TEXT
            );
        """)
        placeholders = ', '.join(['?' for _ in _COLUMNS])
        cursor.executemany(
            f"INSERT INTO temp_ari_kis VALUES ({placeholders});",
            _sanitize_for_sqlite(raw_rows),
        )
        conn.commit()
        conn.close()
        print("SQLite буфер заполнен.")

    except Exception as e:
        msg = f"Ошибка source CH / SQLite: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="ari_kis Error", priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        try: disconnect_vpn()
        except Exception: pass
        return False

    # Шаг 4: Отключение VPN
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"Ошибка отключения VPN: {e}", title="VPN Warning",
                        priority="default", tags="warning")

    # Шаг 5: Вставка в целевую ClickHouse (с retry)
    CHUNK_SIZE = 2000
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"Подключаюсь к целевой ClickHouse (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
                send_receive_timeout=300, connect_timeout=30,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            send_ntfy_alert(f"Ошибка подключения к target CH: {str(e)[:60]}",
                            title="ari_kis Connect Error", priority="urgent", tags="database")
            if attempt < 3:
                time.sleep(5)
            else:
                if os.path.exists(_BUFFER_PATH):
                    try: os.remove(_BUFFER_PATH)
                    except Exception: pass
                return False

    try:
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM temp_ari_kis;")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"Прочитано {len(sqlite_rows)} строк из SQLite.")

        processed_rows = _process_rows(sqlite_rows)
        total = len(processed_rows)

        print(f"Загружаю {total} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME} (батчи по {CHUNK_SIZE})...")
        for i in range(0, total, CHUNK_SIZE):
            chunk = processed_rows[i:i + CHUNK_SIZE]
            client_target.insert(_TABLE_NAME, chunk, column_names=_COLUMNS)
            print(f"   Загружено {min(i + CHUNK_SIZE, total)}/{total} строк.")

        msg = f"[ari_kis] Синхронизировано {total} строк за {days} дней."
        print(msg)
        send_ntfy_alert(msg, title="ari_kis Success", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            os.remove(_BUFFER_PATH)
            print("Временный файл удалён.")
        except Exception as e:
            print(f"Не удалось удалить буфер: {e}")

        return True

    except Exception as e:
        msg = f"Ошибка выгрузки в целевую ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="ari_kis Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def main():
    days = DAYS_TO_SYNC
    print(f"Запуск ари_кис_up (последние {days} дней)")
    send_ntfy_alert(
        f"Запускаю ари_кис_up за {days} дней...",
        title="ari_kis Start", priority="default", tags="robot",
    )

    success = sync_ari_kis(days)

    if success:
        send_ntfy_alert(
            f"Данные АРИ КИС выгружены успешно! ({days} дней)",
            title="ari_kis Done", priority="high", tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "Данные АРИ КИС выгружены с ошибкой! Проверьте консоль.",
            title="ari_kis Failed", priority="urgent", tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
