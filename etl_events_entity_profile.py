# INPUT:  evnt.etl_events (source CH datalake.emias.ru, требуется CheckPoint VPN)
# OUTPUT: консоль + C:\Users\risen\Desktop\etl_events_профиль_витрин.xlsx
#
# Для заданного списка entity (витрин из instrumental_3w_up.py) считает:
#   - общее кол-во загрузок (loaded) за весь период
#   - период наблюдения в днях и среднее кол-во обновлений в день
#   - топ-3 самых частых 20-минутных слота времени суток

import sys
import pandas as pd
import clickhouse_connect

sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg

BUCKET_MINUTES = 20
LOOKBACK_DAYS = 30

# Витрины, которые реально читает instrumental_3w_up.py (_build_query / _build_monitoring_query)
ENTITIES = [
    'data_views.v_instrumental_examinations',
    'data_views.v_eris_assignment_results',
    'data_views.v_route_eris_trauma',
    'data_views.v_task_pin',
    'dwh_views.v_undescribed_researches',
    'data_views.v_instrumental_task_lists',
]

QUERY = """
SELECT loaded
FROM evnt.etl_events
WHERE entity = %(entity)s
  AND loaded >= now() - INTERVAL %(lookback_days)s DAY
ORDER BY loaded
"""


def profile_entity(client, entity: str) -> dict:
    df = client.query_df(QUERY, parameters={'entity': entity, 'lookback_days': LOOKBACK_DAYS})
    if df.empty:
        return {'entity': entity, 'total_events': 0}

    loaded = pd.to_datetime(df['loaded']).dt.tz_localize(None)
    days_span = max((loaded.max().normalize() - loaded.min().normalize()).days + 1, 1)
    avg_per_day = len(loaded) / days_span

    slot = loaded.dt.hour.astype(str).str.zfill(2) + ':' + (loaded.dt.minute // BUCKET_MINUTES * BUCKET_MINUTES).astype(str).str.zfill(2)
    top_slots = slot.value_counts().sort_values(ascending=False).head(3)
    top_slots_str = ', '.join(f'{s} ({c})' for s, c in top_slots.items())

    return {
        'entity': entity,
        'total_events': len(loaded),
        'first_loaded': loaded.min(),
        'last_loaded': loaded.max(),
        'days_span': days_span,
        'avg_updates_per_day': round(avg_per_day, 2),
        'top_3_slots': top_slots_str,
    }


def main():
    client = clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=600,
    )

    rows = []
    for entity in ENTITIES:
        print(f'Считаю: {entity}...')
        rows.append(profile_entity(client, entity))

    result = pd.DataFrame(rows)
    print(f'\n=== Профиль обновлений витрин instrumental_3w_up.py (последние {LOOKBACK_DAYS} дней) ===')
    print(result.to_string(index=False))

    out_path = rf'C:\Users\risen\Desktop\etl_events_профиль_витрин_{LOOKBACK_DAYS}d.xlsx'
    result.to_excel(out_path, index=False)
    print(f'\nСохранено: {out_path}')


if __name__ == '__main__':
    main()
