# INPUT:  data_views.v_eris_assignment_results + v_clinical_events + v_events_status_change
#         (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: C:\Users\risen\Desktop\тест_статусы_код_404.xlsx
#
# Тест на ОДНОЙ процедуре (код 404): проверяем, объясняются ли экстремальные
# выбросы времени описания статусом DEFERRED (отложено) из v_events_status_change.
# Логика join скопирована из oko_saurona_up.py (_build_query / event_cte).

import io
import sys
import pandas as pd
import clickhouse_connect

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

MU_ID = '100001912827'
CODE = '404'
PERIOD_FROM = '2025-06-01'

QUERY = """
WITH
studies AS (
    SELECT
        accession_number,
        any(patient_id)                              AS patient_id,
        any(assignment_result_emp_job_execution_id)  AS job_execution_id,
        any(diagnostic_name)                          AS diagnostic_name,
        any(assignment_result_emp_fio)                AS doctor_fio,
        min(assignment_describe_start_date)            AS describe_start,
        min(assignment_result_doc_created_date)        AS describe_end
    FROM data_views.v_eris_assignment_results
    WHERE assignment_describe_mu_id = %(mu_id)s
      AND diagnostic_code = %(code)s
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      AND toDate(assignment_describe_start_date) >= %(period_from)s
    GROUP BY accession_number
    HAVING describe_end >= describe_start
),
event_cte AS (
    SELECT
        b.accession_number,
        toString(ce.event_id) AS event_id,
        if(ce.event_id IS NOT NULL,
            arrayStringConcat(
                arrayMap(x -> x.2, arraySort(x -> x.1, groupArray((esc.change_time, esc.status)))),
                ' -> '
            ), NULL
        ) AS status_chain,
        if(ce.event_id IS NOT NULL, countIf(esc.status = 'DEFERRED'), 0) AS cnt_deferred,
        if(ce.event_id IS NOT NULL, addHours(minIf(esc.change_time, esc.status = 'RUNNING'), 3), NULL) AS first_running,
        if(ce.event_id IS NOT NULL, addHours(maxIf(esc.change_time, esc.status = 'RUNNING'), 3), NULL) AS last_running
    FROM studies AS b
    LEFT JOIN (
        SELECT patient_id, medical_employee_job_info_id, event_id
        FROM data_views.v_clinical_events
        WHERE event_id IN (
            SELECT DISTINCT event_id
            FROM data_views.v_events_status_change
            WHERE change_time >= %(period_from)s
        )
    ) AS ce
        ON  toString(ce.patient_id)                   = toString(b.patient_id)
        AND toString(ce.medical_employee_job_info_id)  = toString(b.job_execution_id)
    LEFT JOIN (
        SELECT event_id, change_time, status
        FROM data_views.v_events_status_change
        WHERE change_time >= %(period_from)s
    ) AS esc ON esc.event_id = ce.event_id
    GROUP BY b.accession_number, ce.event_id
)
SELECT
    b.accession_number,
    b.diagnostic_name,
    b.doctor_fio,
    b.describe_start,
    b.describe_end,
    e.event_id,
    e.cnt_deferred,
    e.first_running,
    e.last_running,
    e.status_chain
FROM studies AS b
LEFT JOIN event_cte AS e ON e.accession_number = b.accession_number
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY b.accession_number
    ORDER BY
        if(e.event_id IS NULL, 1, 0),
        if(e.first_running IS NOT NULL,
           abs(dateDiff('second', e.first_running, b.describe_end)), 0)
) = 1
ORDER BY dateDiff('second', b.describe_start, b.describe_end) DESC
"""


def _format_hms(seconds: float) -> str:
    total = round(seconds)
    sign = '-' if total < 0 else ''
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{sign}{h}:{m:02d}:{s:02d}'


def main():
    client = clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=1800,
    )

    print(f'Тестовый запрос со статусами для кода {CODE}, период с {PERIOD_FROM}...')
    df = client.query_df(QUERY, parameters={'mu_id': MU_ID, 'code': CODE, 'period_from': PERIOD_FROM})
    print(f'Получено строк: {len(df)}')

    for col in ('describe_start', 'describe_end', 'first_running', 'last_running'):
        if pd.api.types.is_datetime64tz_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)

    df['duration_total_seconds'] = (df['describe_end'] - df['describe_start']).dt.total_seconds()
    df['duration_total'] = df['duration_total_seconds'].apply(_format_hms)

    has_running = df['first_running'].notna() & df['last_running'].notna()
    df.loc[has_running, 'duration_running'] = (
        (df.loc[has_running, 'last_running'] - df.loc[has_running, 'first_running']).dt.total_seconds()
    ).apply(_format_hms)

    # corrected_start = last_running, если он есть и не позже describe_end, иначе исходный describe_start
    valid_last_running = df['last_running'].notna() & (df['last_running'] <= df['describe_end'])
    df['corrected_start'] = df['describe_start']
    df.loc[valid_last_running, 'corrected_start'] = df.loc[valid_last_running, 'last_running']
    df['duration_corrected_seconds'] = (df['describe_end'] - df['corrected_start']).dt.total_seconds()
    df['duration_corrected'] = df['duration_corrected_seconds'].apply(_format_hms)
    df['used_last_running'] = valid_last_running

    print('\n=== Топ-20 самых длинных по ИСХОДНОМУ времени (describe_start->describe_end) ===')
    print(df.sort_values('duration_total_seconds', ascending=False).head(20)[[
        'accession_number', 'duration_total', 'cnt_deferred', 'used_last_running',
        'duration_corrected', 'status_chain'
    ]].to_string(index=False))

    print('\n=== Сводное сравнение: исходное vs скорректированное (last_running) время ===')
    print(f"  Среднее исходное:        {_format_hms(df['duration_total_seconds'].mean())}")
    print(f"  Среднее скорректированное: {_format_hms(df['duration_corrected_seconds'].mean())}")
    print(f"  Медиана исходная:        {_format_hms(df['duration_total_seconds'].median())}")
    print(f"  Медиана скорректированная: {_format_hms(df['duration_corrected_seconds'].median())}")
    print(f"  Макс исходный:           {_format_hms(df['duration_total_seconds'].max())}")
    print(f"  Макс скорректированный:    {_format_hms(df['duration_corrected_seconds'].max())}")
    print(f"  Доля строк с last_running: {valid_last_running.mean():.1%}")

    out_path = r'C:\Users\risen\Desktop\тест_статусы_код_404.xlsx'
    df.to_excel(out_path, index=False, engine='xlsxwriter')
    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
