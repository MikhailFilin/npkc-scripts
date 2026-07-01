# INPUT:  evnt.etl_events (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: консольный вывод + C:\Users\risen\Desktop\etl_events_пики_обновления.xlsx
#
# Для каждой entity считает распределение количества загрузок (loaded) по
# 20-минутным интервалам времени суток (без привязки к дате) и определяет
# слот(ы) с максимальным количеством загрузок — то есть в какое время суток
# витрина чаще всего обновляется.

import sys
import pandas as pd
import clickhouse_connect

sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

BUCKET_MINUTES = 20

QUERY = """
SELECT
    entity,
    toStartOfInterval(loaded, INTERVAL %(bucket)s MINUTE) AS bucket_start,
    formatDateTime(toStartOfInterval(loaded, INTERVAL %(bucket)s MINUTE), '%%H:%%i') AS time_slot,
    count() AS load_count
FROM evnt.etl_events
GROUP BY entity, bucket_start, time_slot
ORDER BY entity, bucket_start
"""


def main():
    client = clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=1800,
    )

    print(f'Тяну все loaded из evnt.etl_events, бакет {BUCKET_MINUTES} мин...')
    df = client.query_df(QUERY, parameters={'bucket': BUCKET_MINUTES})
    print(f'Получено строк: {len(df)}, сущностей: {df["entity"].nunique()}')

    # Схлопываем bucket_start (дата+время) в чистый time_slot времени суток,
    # суммируя загрузки по всем дням в один и тот же 20-минутный слот.
    by_slot = (
        df.groupby(['entity', 'time_slot'])['load_count']
        .sum()
        .reset_index()
        .sort_values(['entity', 'time_slot'])
    )

    # Пиковый слот по каждой entity
    peak = (
        by_slot.loc[by_slot.groupby('entity')['load_count'].idxmax()]
        .rename(columns={'time_slot': 'peak_time_slot', 'load_count': 'peak_load_count'})
        .reset_index(drop=True)
        .sort_values('entity')
    )

    print('\n=== Пиковый слот обновления по каждой entity ===')
    print(peak.to_string(index=False))

    out_path = r'C:\Users\risen\Desktop\etl_events_пики_обновления.xlsx'
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        peak.to_excel(writer, sheet_name='пики по entity', index=False)
        by_slot.to_excel(writer, sheet_name='полное распределение', index=False)

    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
