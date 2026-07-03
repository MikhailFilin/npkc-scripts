# INPUT:  data_views.v_eris_assignment_results (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: C:\Users\risen\Desktop\среднее_время_описания_по_дням.xlsx
#
# Без ограничений по кодам КОДАП — берутся все процедуры за последние
# DAYS_TO_SYNC дней (по дате завершения описания). Итог — одна таблица:
# дата × процедура × модальность × среднее время описания.

import io
import sys
import pandas as pd
import clickhouse_connect

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

MU_ID = '100001912827'
MAX_DURATION_HOURS = 6
DAYS_TO_SYNC = 30

SAMPLE_QUERY = f"""
WITH studies AS (
    SELECT
        accession_number,
        diagnostic_code,
        any(diagnostic_name)                    AS diagnostic_name,
        any(device_type)                        AS modality,
        min(assignment_describe_start_date)      AS describe_start,
        min(assignment_result_doc_created_date)  AS describe_end
    FROM data_views.v_eris_assignment_results
    WHERE assignment_describe_mu_id = %(mu_id)s
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      AND assignment_result_doc_created_date >= now() - INTERVAL {DAYS_TO_SYNC} DAY
      and assignment_result_emp_fio != 'Кивасёв С. А.' and assignment_result_emp_fio != 'Каспарьян Л. Г.'
    GROUP BY accession_number, diagnostic_code
    HAVING describe_end >= describe_start
       AND dateDiff('second', describe_start, describe_end) <= %(max_duration_seconds)s
)
SELECT diagnostic_code, diagnostic_name, modality, describe_start, describe_end
FROM studies
ORDER BY describe_start
"""


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
        connect_timeout=15, send_receive_timeout=1800,
    )

    print(f'Тяну все процедуры за последние {DAYS_TO_SYNC} дн., '
          f'отсечка > {MAX_DURATION_HOURS} ч исключена...')
    df = client.query_df(SAMPLE_QUERY, parameters={
        'mu_id': MU_ID,
        'max_duration_seconds': MAX_DURATION_HOURS * 3600,
    })
    print(f'Получено строк: {len(df)}')

    for col in ('describe_start', 'describe_end'):
        if pd.api.types.is_datetime64tz_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)

    df['duration_seconds'] = (df['describe_end'] - df['describe_start']).dt.total_seconds()
    df['date'] = df['describe_end'].dt.date

    summary = df.groupby(['date', 'diagnostic_name', 'modality']).agg(
        n_studies=('duration_seconds', 'count'),
        avg_seconds=('duration_seconds', 'mean'),
    ).reset_index()
    summary['avg_time'] = summary['avg_seconds'].apply(_format_hms)
    summary = summary.drop(columns=['avg_seconds'])
    summary = summary.sort_values(['date', 'diagnostic_name', 'modality'])
    summary = summary.rename(columns={
        'date': 'дата', 'diagnostic_name': 'процедура', 'modality': 'модальность',
        'n_studies': 'кол-во', 'avg_time': 'среднее время',
    })

    print('\n=== Сводка: дата x процедура x модальность ===')
    print(summary.to_string(index=False))

    out_path = r'C:\Users\risen\Desktop\среднее_время_описания_по_дням.xlsx'
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        summary.to_excel(writer, sheet_name='среднее по дням', index=False)

    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
