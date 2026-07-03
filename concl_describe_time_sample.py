# INPUT:  data_views.v_eris_assignment_results (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: C:\Users\risen\Desktop\время_описания_кодап_выборка15000.xlsx
#
# Для каждого кода КОДАП — выборка до 15000 самых свежих исследований без нижней
# границы по дате: сначала добираются записи за последние периоды, и только если
# по коду не набралось 15000 — добавляются более старые записи (без ограничения
# "снизу" по времени). Это убирает недобор для редких процедур, сохраняя
# максимально свежие данные там, где их достаточно.

import io
import sys
import pandas as pd
import clickhouse_connect

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

MU_ID = '100001912827'
SAMPLE_SIZE = 15000
MAX_DURATION_HOURS = 6

CODES_ALL = (
    '523', '80', '1140', '60', '68', '2', '427', '33', '452', '69', '65', '58',
    '1382', '1', '43', '1144', '51', '70', '32', '31', '209', '210', '143', '144',
    '10', '11', '185', '186', '174', '175', '193', '194', '378', '183', '172',
    '369', '370', '356', '170', '171', '203', '204', '353', '1115', '407', '6',
    '7', '389', '401', '103', '104', '97', '404', '391', '139', '8', '1061',
    '116', '106', '1062',
)

SAMPLE_QUERY = """
WITH studies AS (
    SELECT
        accession_number,
        diagnostic_code,
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
      AND diagnostic_code IN %(codes)s
      AND assignment_status = 'Выполнено'
      AND assignment_describe_start_date     IS NOT NULL
      AND assignment_result_doc_created_date IS NOT NULL
      AND accession_number                   IS NOT NULL
      and assignment_result_emp_fio != 'Кивасёв С. А.' and assignment_result_emp_fio != 'Каспарьян Л. Г.'
    GROUP BY accession_number, diagnostic_code
    HAVING describe_end >= describe_start
       AND dateDiff('second', describe_start, describe_end) <= %(max_duration_seconds)s
),
sampled AS (
    SELECT
        *,
        row_number() OVER (PARTITION BY diagnostic_code ORDER BY describe_start DESC) AS rn
    FROM studies
)
SELECT
    diagnostic_code, diagnostic_name, accession_number, organization, division,
    modality, device, body_part, doctor_fio, describe_start, describe_end
FROM sampled
WHERE rn <= %(sample_size)s
ORDER BY diagnostic_code, describe_start
"""


def _format_hms(seconds: float) -> str:
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}'


def _safe_sheet_name(name: str) -> str:
    name = name.replace(':', '-').replace('\\', '-').replace('/', '-') \
                .replace('?', '').replace('*', '').replace('[', '(').replace(']', ')')
    return name[:31]


def main():
    client = clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=1800,
    )

    print(f'Тяну до {SAMPLE_SIZE} самых свежих исследований на код (без нижней границы по дате), '
          f'отсечка > {MAX_DURATION_HOURS} ч исключена...')
    df = client.query_df(SAMPLE_QUERY, parameters={
        'mu_id': MU_ID, 'codes': CODES_ALL,
        'sample_size': SAMPLE_SIZE,
        'max_duration_seconds': MAX_DURATION_HOURS * 3600,
    })
    print(f'Получено строк: {len(df)}')

    for col in ('describe_start', 'describe_end'):
        if pd.api.types.is_datetime64tz_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)

    df['duration_seconds'] = (df['describe_end'] - df['describe_start']).dt.total_seconds()
    df['duration_minutes'] = (df['duration_seconds'] // 60).astype(int)
    df['duration'] = df['duration_seconds'].apply(_format_hms)

    summary = df.groupby(['diagnostic_code', 'diagnostic_name']).agg(
        n_studies=('accession_number', 'count'),
        avg_seconds=('duration_seconds', 'mean'),
        median_minutes=('duration_minutes', 'median'),
        min_minutes=('duration_minutes', 'min'),
        max_minutes=('duration_minutes', 'max'),
    ).reset_index()
    summary['avg_time']    = summary['avg_seconds'].apply(_format_hms)
    summary['median_time'] = (summary['median_minutes'] * 60).apply(_format_hms)
    summary['min_time']    = (summary['min_minutes'] * 60).apply(_format_hms)
    summary['max_time']    = (summary['max_minutes'] * 60).apply(_format_hms)
    summary = summary.drop(columns=['avg_seconds', 'median_minutes', 'min_minutes', 'max_minutes'])
    summary = summary.sort_values('diagnostic_code', key=lambda s: s.astype(int))

    print('\n=== Сводка по случайной выборке ===')
    print(summary.to_string(index=False))

    out_path = r'C:\Users\risen\Desktop\время_описания_кодап_выборка15000_до6ч.xlsx'
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        summary.to_excel(writer, sheet_name='сводка выборка 15000', index=False)

        print('\nВыгружаю фактуру по каждому коду...')
        for code in CODES_ALL:
            sub = df[df['diagnostic_code'] == code].drop(columns=['duration_seconds', 'duration_minutes'])
            if sub.empty:
                continue
            print(f'  код {code}: {len(sub)} строк')
            sub.to_excel(writer, sheet_name=_safe_sheet_name(f'ф_{code}'), index=False)

    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
