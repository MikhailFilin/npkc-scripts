# INPUT:  data_views.v_eris_assignment_results (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: stdout — таблицы среднего времени описания по кодам КОДАП

import io
import sys
from datetime import timedelta
import pandas as pd
import clickhouse_connect

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

MU_ID = '100001912827'

CODES_ALL = (
    '523', '80', '1140', '60', '68', '2', '427', '33', '452', '69', '65', '58',
    '1382', '1', '43', '1144', '51', '70', '32', '31', '209', '210', '143', '144',
    '10', '11', '185', '186', '174', '175', '193', '194', '378', '183', '172',
    '369', '370', '356', '170', '171', '203', '204', '353', '1115', '407', '6',
    '7', '389', '401', '103', '104', '97', '404', '391', '139', '8', '1061',
    '116', '106', '1062',
)

QUERY_90D = """
WITH studies AS (
    SELECT
        accession_number,
        diagnostic_code,
        any(diagnostic_name)                        AS diagnostic_name,
        min(assignment_describe_start_date)          AS describe_start,
        min(assignment_result_doc_created_date)      AS describe_end
    FROM data_views.v_eris_assignment_results
    WHERE assignment_describe_mu_id = %(mu_id)s
      AND diagnostic_code IN %(codes)s
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      AND toDate(assignment_describe_start_date) >= today() - 90
    GROUP BY accession_number, diagnostic_code
),
filtered AS (
    SELECT
        diagnostic_code,
        diagnostic_name,
        dateDiff('second', describe_start, describe_end)             AS seconds,
        intDiv(dateDiff('second', describe_start, describe_end), 60) AS minutes
    FROM studies
    WHERE describe_end >= describe_start
)
SELECT
    diagnostic_code,
    any(diagnostic_name)   AS diagnostic_name,
    count()                AS n_studies,
    avg(seconds)            AS avg_seconds,
    median(minutes)         AS median_minutes,
    min(minutes)            AS min_minutes,
    max(minutes)            AS max_minutes
FROM filtered
GROUP BY diagnostic_code
ORDER BY diagnostic_code
"""

QUERY_COMPARE = """
WITH studies AS (
    SELECT
        accession_number,
        diagnostic_code,
        any(diagnostic_name)                        AS diagnostic_name,
        min(assignment_describe_start_date)          AS describe_start,
        min(assignment_result_doc_created_date)      AS describe_end
    FROM data_views.v_eris_assignment_results
    WHERE assignment_describe_mu_id = %(mu_id)s
      AND diagnostic_code IN ('427', '33')
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      AND toDate(assignment_describe_start_date) BETWEEN '2026-01-18' AND '2026-03-18'
    GROUP BY accession_number, diagnostic_code
),
labeled AS (
    SELECT
        diagnostic_code,
        diagnostic_name,
        dateDiff('second', describe_start, describe_end)             AS seconds,
        intDiv(dateDiff('second', describe_start, describe_end), 60) AS minutes,
        CASE
            WHEN toDate(describe_start) < '2026-02-17' THEN 'до протокола'
            ELSE 'после протокола'
        END AS period
    FROM studies
    WHERE describe_end >= describe_start
)
SELECT
    diagnostic_code,
    any(diagnostic_name)  AS diagnostic_name,
    period,
    count()               AS n_studies,
    avg(seconds)           AS avg_seconds,
    median(minutes)        AS median_minutes,
    min(minutes)           AS min_minutes,
    max(minutes)           AS max_minutes
FROM labeled
GROUP BY diagnostic_code, period
ORDER BY diagnostic_code, period
"""


FACTURA_QUERY_90D = """
SELECT
    accession_number,
    any(diagnostic_name)                    AS diagnostic_name,
    any(conduct_mo_name)                    AS organization,
    any(conduct_mu_name)                    AS division,
    any(device_type)                        AS modality,
    any(ae_title)                           AS device,
    any(body_part)                          AS body_part,
    any(assignment_result_emp_fio)          AS doctor_fio,
    min(assignment_describe_start_date)      AS describe_start,
    min(assignment_result_doc_created_date)  AS describe_end
FROM data_views.v_eris_assignment_results
WHERE assignment_describe_mu_id = %(mu_id)s
  AND diagnostic_code = %(code)s
  AND assignment_status = 'Выполнено'
  AND assignment_describe_start_date     IS NOT NULL
  AND assignment_result_doc_created_date IS NOT NULL
  AND accession_number                   IS NOT NULL
  AND toDate(assignment_describe_start_date) >= today() - 90
GROUP BY accession_number
HAVING describe_end >= describe_start
ORDER BY describe_start
"""

FACTURA_QUERY_COMPARE = """
SELECT
    accession_number,
    any(diagnostic_name)                    AS diagnostic_name,
    any(conduct_mo_name)                    AS organization,
    any(conduct_mu_name)                    AS division,
    any(device_type)                        AS modality,
    any(ae_title)                           AS device,
    any(body_part)                          AS body_part,
    any(assignment_result_emp_fio)          AS doctor_fio,
    min(assignment_describe_start_date)      AS describe_start,
    min(assignment_result_doc_created_date)  AS describe_end
FROM data_views.v_eris_assignment_results
WHERE assignment_describe_mu_id = %(mu_id)s
  AND diagnostic_code = %(code)s
  AND assignment_status = 'Выполнено'
  AND assignment_describe_start_date     IS NOT NULL
  AND assignment_result_doc_created_date IS NOT NULL
  AND accession_number                   IS NOT NULL
  AND toDate(assignment_describe_start_date) BETWEEN '2026-01-18' AND '2026-03-18'
GROUP BY accession_number
HAVING describe_end >= describe_start
ORDER BY describe_start
"""


def _safe_sheet_name(name: str) -> str:
    name = name.replace(':', '-').replace('\\', '-').replace('/', '-') \
                .replace('?', '').replace('*', '').replace('[', '(').replace(']', ')')
    return name[:31]


def _add_factura_sheet(writer, client, mu_id, code, sheet_name, query, extra_params=None):
    params = {'mu_id': mu_id, 'code': code}
    if extra_params:
        params.update(extra_params)
    df = client.query_df(query, parameters=params)
    if df.empty:
        return
    seconds = (df['describe_end'] - df['describe_start']).dt.total_seconds()
    df['duration'] = seconds.apply(_format_hms)
    for col in ('describe_start', 'describe_end'):
        if pd.api.types.is_datetime64tz_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)
    df.to_excel(writer, sheet_name=_safe_sheet_name(sheet_name), index=False)


def _format_hms(seconds: float) -> str:
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}'


def main():
    client = clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=300,
    )

    print('=== Запрос 1: среднее время описания за 90 дней по кодам КОДАП ===')
    df1 = client.query_df(QUERY_90D, parameters={'mu_id': MU_ID, 'codes': CODES_ALL})
    df1['avg_time']    = df1['avg_seconds'].apply(_format_hms)
    df1['median_time'] = (df1['median_minutes'] * 60).apply(_format_hms)
    df1['min_time']    = (df1['min_minutes'] * 60).apply(_format_hms)
    df1['max_time']    = (df1['max_minutes'] * 60).apply(_format_hms)
    df1 = df1.drop(columns=['avg_seconds', 'median_minutes', 'min_minutes', 'max_minutes'])
    print(df1.to_string(index=False))

    print('\n=== Запрос 2: коды 427/33 — до/после 17.02.2026 ===')
    df2 = client.query_df(QUERY_COMPARE, parameters={'mu_id': MU_ID})
    df2['avg_time']    = df2['avg_seconds'].apply(_format_hms)
    df2['median_time'] = (df2['median_minutes'] * 60).apply(_format_hms)
    df2['min_time']    = (df2['min_minutes'] * 60).apply(_format_hms)
    df2['max_time']    = (df2['max_minutes'] * 60).apply(_format_hms)
    df2 = df2.drop(columns=['avg_seconds', 'median_minutes', 'min_minutes', 'max_minutes'])
    print(df2.to_string(index=False))

    out_path = r'C:\Users\risen\Desktop\время_описания_кодап.xlsx'
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        df1.to_excel(writer, sheet_name='90 дней все коды', index=False)
        df2.to_excel(writer, sheet_name='427_33 до-после', index=False)

        print('\nВыгружаю фактуру по каждому коду (90 дней)...')
        for code in CODES_ALL:
            print(f'  код {code}...')
            _add_factura_sheet(
                writer, client, MU_ID, code, f'ф_{code}', FACTURA_QUERY_90D
            )

        print('Выгружаю фактуру 427/33 до-после протокола...')
        for code in ('427', '33'):
            _add_factura_sheet(
                writer, client, MU_ID, code, f'ф_{code}_до-после', FACTURA_QUERY_COMPARE
            )

    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
