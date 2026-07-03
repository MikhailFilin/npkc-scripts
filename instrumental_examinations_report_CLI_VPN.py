"""
instrumental_examinations_report_CLI_VPN.py

### ВНИМАНИЕ: VPN подключается через CLI (trac.exe), а НЕ через GUI ###
### (в отличие от большинства скриптов проекта, где VPN поднимается ###
### через TrGUI.exe + pyautogui-клики по координатам экрана).       ###

Мини-отчёт "свежесть витрин": по каждой витрине из CHECKS считает
  1) когда витрина последний раз обновилась — по логу evnt.etl_events
  2) что реально видно внутри витрины прямо сейчас — точная (не округлённая)
     максимальная дата + кол-во строк за вчера и позавчера

Результат не просто печатается, а копится в ClickHouse (target CH, без VPN):
один запуск скрипта = одна строка на каждую витрину из CHECKS в таблице
_TABLE_NAME. Если добавить сюда вторую витрину — на каждый запуск будет
появляться 2 строки, и так по истории видно "макс. дата обновления и
какие данные были на такой-то момент".

Чтобы добавить новую витрину: дописать словарь в CHECKS ниже — свой
entity, свой SQL с фильтром/полем даты этой витрины (алиасы результата
должны остаться max_source_date / cnt_yesterday / cnt_day_before_yesterday
/ query_time — под них рассчитана таблица).

# INPUT:  evnt.etl_events, data_views.v_instrumental_examinations
#         (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: <CH_DATABASE_TARGET>.vitrina_freshness_log (target CH, Yandex Cloud, без VPN)
"""

import os
import sys
import socket
import subprocess
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg
import clickhouse_connect

_TABLE_NAME = 'vitrina_freshness_log'
_COLUMNS = [
    'run_ts', 'entity', 'filter_desc',
    'etl_last_loaded', 'etl_query_time',
    'max_source_date', 'cnt_yesterday', 'cnt_day_before_yesterday', 'vitrina_query_time',
]

ETL_LOG_QUERY = """
SELECT
    max(loaded) AS last_loaded,
    now64(3, 'Europe/Moscow') AS query_time
FROM evnt.etl_events
WHERE entity = %(entity)s
  AND event_type = 'completed'
"""

# Каждая витрина описывает себя сама: entity (как записано в evnt.etl_events),
# filter_desc — для контекста в логе, и vitrina_query — SQL с алиасами
# max_source_date / cnt_yesterday / cnt_day_before_yesterday / query_time.
CHECKS = [
    {
        'entity': 'data_views.v_instrumental_examinations',
        'filter_desc': 'ae_title IS NOT NULL',
        'vitrina_query': """
            SELECT
                max(vie.conduct_date) AS max_source_date,
                countIf(toDate(vie.conduct_date) = yesterday()) AS cnt_yesterday,
                countIf(toDate(vie.conduct_date) = yesterday() - 1) AS cnt_day_before_yesterday,
                now64(3, 'Europe/Moscow') AS query_time
            FROM data_views.v_instrumental_examinations vie
            WHERE vie.ae_title IS NOT NULL
        """,
    },
    # "Конкл" — описания/заключения рентгенологов (assignment_result_doc_created_date,
    # фильтр как в concl_click_up.py).
    {
        'entity': 'data_views.v_eris_assignment_results',
        'filter_desc': "accession_number != ''",
        'vitrina_query': """
            SELECT
                max(t2.assignment_result_doc_created_date) AS max_source_date,
                countIf(toDate(t2.assignment_result_doc_created_date) = yesterday()) AS cnt_yesterday,
                countIf(toDate(t2.assignment_result_doc_created_date) = yesterday() - 1) AS cnt_day_before_yesterday,
                now64(3, 'Europe/Moscow') AS query_time
            FROM data_views.v_eris_assignment_results t2
            WHERE t2.accession_number != ''
        """,
    },
    # "Травма" — травматологические направления (assignment_signed_date,
    # фильтр как в trauma_exam CTE из instrumental_3w_up.py).
    {
        'entity': 'data_views.v_route_eris_trauma',
        'filter_desc': 'accession_number IS NOT NULL',
        'vitrina_query': """
            SELECT
                max(vret.assignment_signed_date) AS max_source_date,
                countIf(toDate(vret.assignment_signed_date) = yesterday()) AS cnt_yesterday,
                countIf(toDate(vret.assignment_signed_date) = yesterday() - 1) AS cnt_day_before_yesterday,
                now64(3, 'Europe/Moscow') AS query_time
            FROM data_views.v_route_eris_trauma vret
            WHERE vret.accession_number IS NOT NULL
        """,
    },
]


# ---------------------------------------------------------------------------
# VPN: подключение/отключение через CLI trac.exe (Check Point Endpoint Connect)
# ---------------------------------------------------------------------------
def _trac(*args, timeout: int = 60) -> tuple:
    """Запускает trac.exe с аргументами, возвращает (returncode, stdout, stderr)."""
    cmd = [cfg.VPN_TRAC_PATH] + list(args)
    print(f"  > {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.stdout.strip():
            print(f"  stdout: {r.stdout.strip()}")
        if r.stderr.strip():
            print(f"  stderr: {r.stderr.strip()}")
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        print(f"  Таймаут {timeout} сек")
        return -1, '', 'timeout'
    except Exception as e:
        print(f"  Ошибка запуска trac: {e}")
        return -1, '', str(e)


def _vpn_dns_ok() -> bool:
    """Проверяет VPN через DNS: резолвится ли внутренний хост."""
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo('datalake.emias.ru', cfg.CH_PORT)
        return True
    except Exception:
        return False


def connect_vpn_cli() -> bool:
    print("\n[VPN-CLI] Подключаю через trac.exe...")

    _, info_out, _ = _trac('info', timeout=15)
    if 'idle' not in info_out.lower():
        print("  VPN уже активен или в переходном статусе — отключаю перед новым подключением...")
        _trac('disconnect', timeout=20)
        time.sleep(3)

    cmd = [cfg.VPN_TRAC_PATH, 'connect']
    if cfg.VPN_SITE:
        cmd += ['-s', cfg.VPN_SITE]

    # Креды через Popen.communicate(input=...) бинарно: echo в CMD добавляет \r,
    # который ломает пароль в prompt trac.exe. LF без CR — рабочий вариант.
    stdin_bytes = (cfg.VPN_USERNAME + '\n' + cfg.VPN_PASSWORD + '\n').encode('utf-8')
    print(f"  > {' '.join(cmd)}  (креды через stdin)")
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out_bytes, err_bytes = proc.communicate(input=stdin_bytes, timeout=120)
        out_text = out_bytes.decode('utf-8', errors='replace').strip()
        if out_text:
            print(f"  stdout: {out_text}")
        if 'successfully established' in out_text:
            print("  trac сообщил об успешном подключении")
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
        print("  Таймаут 120 сек при подключении")
    except Exception as e:
        print(f"  Ошибка: {e}")

    print("  Жду поднятия VPN...")
    for _ in range(2):
        time.sleep(20)
        if _vpn_dns_ok():
            print("  VPN подключён (datalake.emias.ru резолвится)")
            return True

    print("  VPN НЕ подключён (datalake.emias.ru не резолвится)")
    return False


def disconnect_vpn_cli():
    print("\n[VPN-CLI] Отключаю через trac.exe...")
    _trac('disconnect', timeout=30)
    time.sleep(3)


# ---------------------------------------------------------------------------
# Сбор данных по одной витрине (source CH, через VPN)
# ---------------------------------------------------------------------------
def collect_check(client_source, run_ts, check: dict) -> tuple:
    entity = check['entity']
    print(f"\n--- {entity} ---")

    df_log = client_source.query_df(ETL_LOG_QUERY, parameters={'entity': entity})
    etl_last_loaded = None
    etl_query_time = None
    if not df_log.empty and df_log.iloc[0]['last_loaded'] is not None:
        etl_last_loaded = df_log.iloc[0]['last_loaded']
        etl_query_time = df_log.iloc[0]['query_time']
        print(f"  Витрина обновилась (loaded):  {etl_last_loaded}")
        print(f"  Время выполнения запроса 1:   {etl_query_time}")
    else:
        print(f"  Нет записей event_type='completed' для entity='{entity}'")

    df_vie = client_source.query_df(check['vitrina_query'])
    row = df_vie.iloc[0]
    print(f"  Максимальная дата внутри витрины: {row['max_source_date']}")
    print(f"  Строк за вчера:                   {row['cnt_yesterday']}")
    print(f"  Строк за позавчера:               {row['cnt_day_before_yesterday']}")
    print(f"  Время выполнения запроса 2:       {row['query_time']}")

    return (
        run_ts, entity, check['filter_desc'],
        etl_last_loaded, etl_query_time,
        row['max_source_date'], int(row['cnt_yesterday']), int(row['cnt_day_before_yesterday']), row['query_time'],
    )


# ---------------------------------------------------------------------------
# Таблица-лог в target CH (Yandex Cloud, без VPN)
# ---------------------------------------------------------------------------
def _ensure_table_exists(client) -> None:
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            run_ts                    DateTime64(3),
            entity                    String,
            filter_desc               String,
            etl_last_loaded           Nullable(DateTime64(3)),
            etl_query_time            Nullable(DateTime64(3)),
            max_source_date           Nullable(DateTime64(3)),
            cnt_yesterday             UInt64,
            cnt_day_before_yesterday  UInt64,
            vitrina_query_time        DateTime64(3)
        )
        ENGINE = MergeTree
        ORDER BY (entity, run_ts)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


def save_rows(rows: list) -> None:
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"\nПодключаюсь к target CH (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=cfg.CH_HOST_TARGET, port=cfg.CH_PORT_TARGET,
                username=cfg.CH_USER_TARGET, password=cfg.CH_PASSWORD_TARGET,
                database=cfg.CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                raise

    try:
        _ensure_table_exists(client_target)
        client_target.insert(_TABLE_NAME, rows, column_names=_COLUMNS)
        print(f"Записано {len(rows)} строк в {cfg.CH_DATABASE_TARGET}.{_TABLE_NAME}.")
    finally:
        client_target.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start = datetime.now()
    run_ts = start
    print(f"=== Отчёт по свежести витрин (VPN через CLI trac.exe) === {start}")

    if not connect_vpn_cli():
        print("Прерываю: VPN не поднялся.")
        return

    rows = []
    client_source = None
    try:
        client_source = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT,
            username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            secure=True, verify=False,
            connect_timeout=15, send_receive_timeout=120,
        )
        for check in CHECKS:
            rows.append(collect_check(client_source, run_ts, check))
    except Exception as e:
        print(f"Ошибка запроса к исходной ClickHouse: {e}")
    finally:
        if client_source:
            client_source.close()
        disconnect_vpn_cli()

    if rows:
        save_rows(rows)
    else:
        print("Нет данных для сохранения — пропускаю запись в ClickHouse.")

    print(f"\nГотово за {datetime.now() - start}")


if __name__ == '__main__':
    main()
